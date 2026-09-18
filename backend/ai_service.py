"""
Сервис AI-распознавания еды по фотографии и текстовых AI-подсказок.

Использует OpenAI GPT-4o (Vision) для анализа изображения блюда и оценки
его калорийности и БЖУ. Перед отправкой изображение уменьшается с помощью
Pillow для снижения стоимости запроса.

Двуязычность (RU/EN):
  * у всех четырёх публичных функций есть параметр lang ("ru" по умолчанию);
  * при lang=="en" используются английские system/user-промпты, которые
    требуют JSON с ТЕМИ ЖЕ ключами, но текстовые ЗНАЧЕНИЯ (dish_name, note,
    reason, name, dosage) — на английском;
  * при lang=="ru" поведение полностью прежнее (русские промпты без изменений);
  * имена JSON-полей НИКОГДА не меняются — меняется только язык значений.

Надёжность:
  * промпт настроен на «всегда дай оценку» (не отказываться от обычной еды);
  * при пустом/некорректном ответе модели делается повторная попытка;
  * ответ парсится устойчиво (срезаются markdown-ограждения ```);
  * «сырой» ответ модели логируется и возвращается в debug-данных,
    чтобы его можно было посмотреть при отладке.

Публичные функции:
    analyze_food_image(image_bytes, mime="image/jpeg", lang="ru") -> dict
        Распознаёт блюдо по фото (КБЖУ + примерный вес порции + уверенность).
    recommend_meals(remaining_calories, remaining_proteins, remaining_fats,
                    remaining_carbs, diet_goal, time_of_day, lang="ru") -> dict
        Подбирает 2-3 варианта блюд под остаток КБЖУ на день.
    suggest_supplements(diet_goal, lang="ru") -> dict
        Подбирает спортивные добавки под цель пользователя.
    recommend_supplements(improvement_goal, training_count, workout_types,
                          diet_goal, lang="ru") -> dict
        Персональный подбор добавок с учётом цели улучшения, частоты/типа
        тренировок и цели диеты.
    transcribe_audio(audio_bytes, filename="audio.ogg", lang=None) -> str
        Распознаёт речь в тексте через Whisper (whisper-1).
    parse_food_text(text, lang="ru") -> dict
        По текстовому описанию приёма пищи извлекает список блюд с КБЖУ и
        определяет тип приёма пищи (Этап 2 — голосовой ввод еды).
"""

import base64
import io
import json
import logging
import os
import re

# Pillow — для уменьшения изображения (best-effort, не критично для работы).
try:
    from PIL import Image
except Exception:  # pragma: no cover - на случай отсутствия Pillow
    Image = None

# Клиент OpenAI (openai>=1.40). Ключ OPENAI_API_KEY берётся из окружения
# автоматически при создании клиента OpenAI().
from openai import OpenAI

logger = logging.getLogger("ai_service")

# Модель можно переопределить переменной окружения (например, gpt-4o-mini — дешевле).
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
# Модель для VISION (распознавание фото по картинке) — тут качество важно,
# оставляем полноценную gpt-4o.
VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", MODEL)
# Модель для ТЕКСТОВЫХ функций (расчёт КБЖУ, разбор голоса, рекомендации, план
# меню, отчёт) — это структурированный JSON, gpt-4o-mini справляется практически
# так же, но примерно в 16 раз дешевле. Меняется через env при желании.
TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")
# Таймаут запроса к OpenAI (сек). Без него дефолт SDK — 600с: один зависший
# вызов надолго занимал бы поток. Ретраи делаем сами (MAX_ATTEMPTS), поэтому
# у клиента max_retries=0. Транскрипции даём больший таймаут (аудио дольше).
OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "60"))
OPENAI_TRANSCRIBE_TIMEOUT = float(os.getenv("OPENAI_TRANSCRIBE_TIMEOUT", "120"))

# Единые клиенты OpenAI (создаются один раз, переиспользуются). С таймаутом,
# чтобы зависший запрос не держал поток бесконечно.
_shared_client = None
_transcribe_client = None


def _get_client():
    """Единый клиент OpenAI для текстовых/vision-запросов (с таймаутом)."""
    global _shared_client
    if _shared_client is None:
        _shared_client = OpenAI(timeout=OPENAI_TIMEOUT, max_retries=0)
    return _shared_client


# Куда ходить за распознаванием ФОТО. Пусто — тот же OpenAI, что и всё
# остальное. Задав OPENAI_VISION_BASE_URL (например, OpenAI-совместимый
# адрес Gemini) и OPENAI_VISION_API_KEY, фото можно отдать другому провайдеру,
# не трогая Whisper и текстовые функции.
VISION_BASE_URL = os.getenv("OPENAI_VISION_BASE_URL", "").strip()
VISION_API_KEY = os.getenv("OPENAI_VISION_API_KEY", "").strip()
_vision_client = None


def _get_vision_client():
    """Клиент для vision-запросов: отдельный провайдер, если задан в env."""
    global _vision_client
    if not VISION_BASE_URL and not VISION_API_KEY:
        return _get_client()
    if _vision_client is None:
        kwargs = {"timeout": OPENAI_TIMEOUT, "max_retries": 0}
        if VISION_BASE_URL:
            kwargs["base_url"] = VISION_BASE_URL
        if VISION_API_KEY:
            kwargs["api_key"] = VISION_API_KEY
        _vision_client = OpenAI(**kwargs)
    return _vision_client


def _completion_params(model: str, max_tokens: int, temperature: float) -> dict:
    """Параметры chat.completions под семейство модели.

    GPT-5 и o-серия не принимают temperature (кроме значения по умолчанию) и
    ограничивают ответ параметром max_completion_tokens; у остальных (gpt-4o,
    Gemini через совместимый API, Qwen) — классические max_tokens/temperature.
    """
    name = (model or "").lower()
    reasoning = name.startswith(("gpt-5", "o1", "o3", "o4"))
    if reasoning:
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens, "temperature": temperature}


def _get_transcribe_client():
    """Отдельный клиент для распознавания речи (больший таймаут — аудио дольше)."""
    global _transcribe_client
    if _transcribe_client is None:
        _transcribe_client = OpenAI(timeout=OPENAI_TRANSCRIBE_TIMEOUT, max_retries=0)
    return _transcribe_client


def _log_usage(tag: str, response) -> None:
    """Залогировать расход токенов вызова (для контроля стоимости AI)."""
    try:
        u = getattr(response, "usage", None)
        if u is not None:
            logger.info(
                "AI[%s] tokens prompt=%s completion=%s total=%s model=%s",
                tag,
                getattr(u, "prompt_tokens", None),
                getattr(u, "completion_tokens", None),
                getattr(u, "total_tokens", None),
                getattr(response, "model", None),
            )
    except Exception as exc:  # noqa: BLE001 — телеметрия не должна ронять запрос
        logger.debug("Подавлено исключение: %r", exc)
# Максимальный размер большей стороны изображения после уменьшения (в пикселях).
MAX_DIMENSION = 1024
# Качество JPEG при пересжатии.
JPEG_QUALITY = 85
# Лимит токенов ответа (JSON с оценкой короткий — этого с запасом хватает).
MAX_TOKENS = 700
# Сколько всего попыток сделать при пустом/битом ответе модели.
MAX_ATTEMPTS = 2

# Текст-маркер «на фото нет еды» (по языкам).
NO_FOOD_NAME = "На фото не найдено еды"
NO_FOOD_NAME_EN = "No food detected"

# Допустимые значения уровня уверенности модели в оценке.
CONFIDENCE_LEVELS = ("low", "medium", "high")


def _normalize_lang(lang: str | None) -> str:
    """
    Приводит код языка к "ru" или "en".

    По умолчанию (и для любого неизвестного/пустого значения) — "ru",
    что сохраняет полную обратную совместимость со старыми вызовами без lang.
    Только явное "en" (в любом регистре) переключает на английский.
    """
    try:
        if str(lang or "").strip().lower().startswith("en"):
            return "en"
    except (TypeError, ValueError):
        pass
    return "ru"


# --------------------------------------------------------------------------- #
#  Системные промпты для анализа фото еды (RU/EN)
# --------------------------------------------------------------------------- #

# Системный промпт: заставляем модель ВСЕГДА оценивать обычную еду,
# а не отказываться. Отказ допустим только если еды на фото реально нет.
SYSTEM_PROMPT = (
    "Ты — опытный нутрициолог. На фотографии — еда. "
    "Твоя задача — ВСЕГДА определить блюдо и оценить его пищевую ценность, "
    "даже если ты не уверен на 100%: дай наиболее вероятную оценку по тому, что видишь.\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него) с полями:\n"
    '  "dish_name"    — строка, название блюда на русском '
    '(например: "Варёный картофель", "Тефтели с подливой", "Варёная кукуруза");\n'
    '  "weight_grams" — целое число, примерный ВЕС видимой порции в граммах '
    "(оцени по размеру тарелки/приборов на фото);\n"
    '  "calories"     — целое число, ккал для порции, видимой на фото;\n'
    '  "proteins"     — число, белки в граммах;\n'
    '  "fats"         — число, жиры в граммах;\n'
    '  "carbs"        — число, углеводы в граммах;\n'
    '  "confidence"   — строка-уровень уверенности в оценке: '
    '"low", "medium" или "high";\n'
    '  "note"         — строка, короткий комментарий на русском '
    "(состав, степень уверенности или совет).\n\n"
    "Правила:\n"
    "- Оценивай реалистично по размеру видимой порции; для настоящей еды "
    "weight_grams, calories и БЖУ должны быть БОЛЬШЕ нуля.\n"
    "- Если на фото НЕСКОЛЬКО блюд/продуктов — оцени их СУММАРНО "
    "(общий вес и общее КБЖУ) и перечисли все блюда в dish_name через запятую.\n"
    "- confidence ставь \"high\", если блюдо очевидно и порция хорошо видна; "
    "\"medium\" при обычной неопределённости; \"low\", если фото нечёткое "
    "или состав трудно определить.\n"
    "- НЕ отказывайся от оценки обычных блюд (картофель, мясо, каши, супы и т.п.).\n"
    "- Только если на фото СОВСЕМ нет еды (пустая тарелка, не еда), "
    'верни dish_name="' + NO_FOOD_NAME + '", confidence="high" и нули.'
)

# Английский аналог SYSTEM_PROMPT: ТЕ ЖЕ ключи JSON, значения dish_name/note — на английском.
SYSTEM_PROMPT_EN = (
    "You are an experienced nutritionist. The photo shows food. "
    "Your task is to ALWAYS identify the dish and estimate its nutritional value, "
    "even if you are not 100% sure: give the most likely estimate based on what you see.\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else) with the fields:\n"
    '  "dish_name"    — string, the dish name in English '
    '(for example: "Boiled potatoes", "Meatballs in gravy", "Boiled corn");\n'
    '  "weight_grams" — integer, the approximate WEIGHT of the visible portion in grams '
    "(estimate from the size of the plate/cutlery in the photo);\n"
    '  "calories"     — integer, kcal for the portion visible in the photo;\n'
    '  "proteins"     — number, protein in grams;\n'
    '  "fats"         — number, fat in grams;\n'
    '  "carbs"        — number, carbohydrates in grams;\n'
    '  "confidence"   — string confidence level of the estimate: '
    '"low", "medium" or "high";\n'
    '  "note"         — string, a short comment in English '
    "(composition, confidence level or advice).\n\n"
    "Rules:\n"
    "- Estimate realistically by the size of the visible portion; for real food "
    "weight_grams, calories and macros must be GREATER than zero.\n"
    "- If the photo shows SEVERAL dishes/products — estimate them TOGETHER "
    "(total weight and total calories/macros) and list all dishes in dish_name separated by commas.\n"
    "- Set confidence to \"high\" if the dish is obvious and the portion is clearly visible; "
    "\"medium\" for ordinary uncertainty; \"low\" if the photo is blurry "
    "or the composition is hard to determine.\n"
    "- Do NOT refuse to estimate ordinary dishes (potatoes, meat, porridge, soups, etc.).\n"
    "- Only if there is NO food at all in the photo (empty plate, not food), "
    'return dish_name="' + NO_FOOD_NAME_EN + '", confidence="high" and zeros.'
)

# Пользовательский текст к vision-вызову (по языкам).
VISION_USER_PROMPT = (
    "Определи блюдо на этом фото, оцени примерный вес порции, "
    "калорийность и БЖУ. "
    "Верни результат строго в формате JSON по инструкции."
)
VISION_USER_PROMPT_EN = (
    "Identify the dish in this photo, estimate the approximate portion weight, "
    "calories and macros. "
    "Return the result strictly in JSON format following the instructions."
)


class AIError(RuntimeError):
    """Ошибка анализа фото. Несёт «сырой» ответ модели для отладки."""

    def __init__(self, message: str, raw: str = "", finish_reason=None, refusal=None):
        super().__init__(message)
        self.raw = raw or ""
        self.finish_reason = finish_reason
        self.refusal = refusal


def _downscale_image(image_bytes: bytes) -> tuple[bytes, str]:
    """
    Уменьшает изображение до MAX_DIMENSION по большей стороне и пересжимает
    в JPEG (best-effort). Возвращает кортеж (байты, mime).

    Если Pillow недоступен или произошла любая ошибка — возвращает исходные
    байты как есть с mime "image/jpeg".
    """
    if Image is None:
        return image_bytes, "image/jpeg"

    try:
        img = Image.open(io.BytesIO(image_bytes))

        # Приводим к RGB, чтобы корректно сохранить в JPEG.
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        width, height = img.size
        largest = max(width, height)
        if largest > MAX_DIMENSION:
            scale = MAX_DIMENSION / float(largest)
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            img = img.resize(new_size, Image.LANCZOS)

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=JPEG_QUALITY)
        return buffer.getvalue(), "image/jpeg"
    except Exception:
        return image_bytes, "image/jpeg"


def _coerce_int(value, default: int = 0) -> int:
    """Безопасно приводит значение к целому числу (с округлением)."""
    try:
        if value is None:
            return default
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _coerce_float(value, default: float = 0.0) -> float:
    """Безопасно приводит значение к числу с плавающей точкой (округление до 1 знака)."""
    try:
        if value is None:
            return default
        return round(float(value), 1)
    except (TypeError, ValueError):
        return default


def _coerce_confidence(value, default: str = "medium") -> str:
    """
    Нормализует уровень уверенности к одному из CONFIDENCE_LEVELS.

    Принимает строку в любом регистре с возможными пробелами; всё, что
    не входит в допустимый набор, заменяется на default ("medium").
    """
    try:
        if value is None:
            return default
        normalized = str(value).strip().lower()
        return normalized if normalized in CONFIDENCE_LEVELS else default
    except (TypeError, ValueError):
        return default


# Канонические единицы измерения (язык-независимые КЛЮЧИ, хранятся в БД):
#   pcs (штучное), g (весовое), ml (жидкое), serving (порция).
CANONICAL_UNITS = ("pcs", "g", "ml", "serving")

# Карта псевдонимов единиц -> канонический ключ. Всё остальное -> None.
_UNIT_ALIASES = {
    # штучное
    "pcs": "pcs", "pc": "pcs", "piece": "pcs", "pieces": "pcs",
    "шт": "pcs", "штук": "pcs", "штука": "pcs", "штуки": "pcs", "штук.": "pcs",
    # весовое
    "g": "g", "gram": "g", "grams": "g",
    "г": "g", "гр": "g", "грамм": "g", "граммов": "g", "грамма": "g",
    # жидкое
    "ml": "ml", "мл": "ml",
    # порция
    "serving": "serving", "servings": "serving", "portion": "serving", "portions": "serving",
    "порция": "serving", "порции": "serving", "порций": "serving",
}


def _normalize_unit(u) -> str | None:
    """
    Приводит единицу измерения к каноническому ключу (pcs|g|ml|serving) или None.

    Принимает как сами канонические ключи, так и распространённые псевдонимы
    (шт/штук/pc/piece -> pcs; г/гр/грамм/gram -> g; мл/ml -> ml;
    порция/portion/serving -> serving). Регистр и крайние пробелы игнорируются.
    Всё, что не удаётся сопоставить, даёт None.
    """
    try:
        if u is None:
            return None
        key = str(u).strip().lower()
        if not key:
            return None
        if key in CANONICAL_UNITS:
            return key
        return _UNIT_ALIASES.get(key)
    except (TypeError, ValueError):
        return None


def _extract_json(content: str):
    """
    Пытается достать JSON-объект из текста ответа модели.

    Устойчив к markdown-ограждениям (```json ... ```) и к лишнему тексту
    вокруг: берёт подстроку от первой "{" до последней "}". Возвращает dict
    или None, если распарсить не удалось.
    """
    if not content:
        return None

    text = content.strip()

    # Срезаем ограждения ```json ... ``` или ``` ... ```.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    # Прямой разбор.
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (ValueError, TypeError):
        pass

    # Фолбэк: берём фрагмент между первой "{" и последней "}".
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                return data
        except (ValueError, TypeError):
            pass

    return None


def _call_model(client: "OpenAI", data_url: str, lang: str = "ru"):
    """
    Один вызов модели (vision). Возвращает (content, finish_reason, refusal).

    В зависимости от lang выбираются русские или английские system/user-промпты;
    набор и порядок сообщений остаётся прежним.
    """
    # Выбор промптов по языку (по умолчанию русский — обратная совместимость).
    if _normalize_lang(lang) == "en":
        system_prompt = SYSTEM_PROMPT_EN
        user_text = VISION_USER_PROMPT_EN
    else:
        system_prompt = SYSTEM_PROMPT
        user_text = VISION_USER_PROMPT

    response = client.chat.completions.create(
        model=VISION_MODEL,
        response_format={"type": "json_object"},
        **_completion_params(VISION_MODEL, MAX_TOKENS, 0.3),
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": user_text,
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    )
    _log_usage("analyze_food", response)
    choice = response.choices[0]
    content = choice.message.content
    refusal = getattr(choice.message, "refusal", None)
    return content, choice.finish_reason, refusal


def analyze_food_image(
    image_bytes: bytes,
    mime: str = "image/jpeg",
    lang: str = "ru",
) -> dict:
    """
    Анализирует фотографию еды и возвращает оценку калорийности и БЖУ.

    Параметры:
        image_bytes — байты изображения;
        mime        — mime изображения (по факту перекодируется в JPEG);
        lang        — язык ответа модели: "ru" (по умолчанию) или "en".
                      При "en" dish_name/note возвращаются на английском,
                      маркер «нет еды» — "No food detected".

    Возвращает словарь:
        {
            "dish_name": str, "weight_grams": int, "calories": int,
            "proteins": float, "fats": float, "carbs": float,
            "confidence": str, "note": str,
            "_debug": { "raw": str, "finish_reason": str|None,
                        "refusal": str|None, "model": str, "attempts": int },
        }

    При невозможности получить корректный ответ выбрасывает AIError
    (наследник RuntimeError) с «сырым» ответом модели внутри.
    """
    # Нормализуем язык и подбираем правильный маркер «нет еды».
    lang = _normalize_lang(lang)
    no_food_name = NO_FOOD_NAME_EN if lang == "en" else NO_FOOD_NAME

    # 1. Уменьшаем изображение и формируем data URL.
    processed_bytes, processed_mime = _downscale_image(image_bytes)
    b64 = base64.b64encode(processed_bytes).decode("ascii")
    data_url = f"data:{processed_mime};base64,{b64}"

    # 2. Клиент для фото: OpenAI по умолчанию или отдельный провайдер из env.
    client = _get_vision_client()

    last_error = "неизвестная ошибка"
    raw = ""
    finish_reason = None
    refusal = None

    # 3. Несколько попыток: пустой/битый ответ модели — частая транзиентная проблема.
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            content, finish_reason, refusal = _call_model(client, data_url, lang=lang)
            raw = content or ""
            # Всегда логируем сырой ответ — он будет виден в логах Railway/uvicorn.
            logger.info(
                "AI попытка %d/%d (lang=%s): finish=%s refusal=%s raw=%s",
                attempt, MAX_ATTEMPTS, lang, finish_reason, refusal, raw[:600],
            )
        except Exception as exc:  # сеть, ключ, лимиты OpenAI и т.п.
            last_error = f"ошибка обращения к OpenAI: {exc}"
            logger.warning("AI попытка %d/%d не удалась: %s", attempt, MAX_ATTEMPTS, exc)
            continue

        if not raw.strip():
            last_error = (
                "модель вернула пустой ответ "
                f"(finish_reason={finish_reason}, refusal={refusal or 'нет'})"
            )
            continue

        data = _extract_json(raw)
        if data is None:
            last_error = "ответ модели не является корректным JSON"
            continue

        # 4. Успех: нормализуем поля.
        dish_name = data.get("dish_name")
        if not isinstance(dish_name, str) or not dish_name.strip():
            dish_name = no_food_name

        note = data.get("note")
        if not isinstance(note, str):
            note = ""

        return {
            "dish_name": dish_name.strip(),
            # Примерный вес видимой порции (граммы); если модель не указала — 0.
            "weight_grams": _coerce_int(data.get("weight_grams")),
            "calories": _coerce_int(data.get("calories")),
            "proteins": _coerce_float(data.get("proteins")),
            "fats": _coerce_float(data.get("fats")),
            "carbs": _coerce_float(data.get("carbs")),
            # Уровень уверенности модели; по умолчанию "medium".
            "confidence": _coerce_confidence(data.get("confidence")),
            "note": note.strip(),
            "_debug": {
                "raw": raw,
                "finish_reason": finish_reason,
                "refusal": refusal,
                "model": MODEL,
                "attempts": attempt,
            },
        }

    # 5. Все попытки исчерпаны.
    logger.error("AI: все попытки исчерпаны. Последняя ошибка: %s", last_error)
    raise AIError(
        f"Не удалось распознать еду: {last_error}",
        raw=raw,
        finish_reason=finish_reason,
        refusal=refusal,
    )


# --------------------------------------------------------------------------- #
#  Текстовые AI-подсказки: подбор блюд и спортивных добавок
# --------------------------------------------------------------------------- #
#
# Обе функции ниже используют тот же подход, что и analyze_food_image:
#   * единый клиент OpenAI() (ключ из окружения);
#   * response_format=json_object — модель обязана вернуть JSON-объект;
#   * несколько попыток (MAX_ATTEMPTS) на случай пустого/битого ответа;
#   * устойчивый разбор JSON через _extract_json;
#   * при неудаче — AIError с «сырым» ответом для отладки.
#
# Двуязычность: для каждого system-промпта есть английский аналог (..._EN) с
# ТЕМИ ЖЕ ключами JSON, но значениями на английском. Выбор делает _pick_prompt.

# Системный промпт для подбора блюд под остаток КБЖУ.
RECOMMEND_SYSTEM_PROMPT = (
    "Ты — опытный нутрициолог и повар. Пользователь хочет «добрать» дневную "
    "норму КБЖУ и просит 2-3 варианта блюд под оставшийся лимит.\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него) с полем:\n"
    '  "suggestions" — массив из 2-3 объектов, каждый с полями:\n'
    '      "dish_name" — строка, название блюда на русском;\n'
    '      "calories"  — целое число, ккал порции;\n'
    '      "proteins"  — число, белки в граммах;\n'
    '      "fats"      — число, жиры в граммах;\n'
    '      "carbs"     — число, углеводы в граммах;\n'
    '      "reason"    — строка, почему это блюдо подходит '
    "(коротко, на русском).\n\n"
    "Правила:\n"
    "- Подбирай реальные, простые в приготовлении блюда.\n"
    "- Суммарно блюдо должно вписываться в оставшийся лимит калорий "
    "и помогать добрать БЖУ (особенно белок).\n"
    "- Учитывай цель (loss — похудение, maintain — поддержание, "
    "gain — набор массы) и время суток, если они указаны.\n"
    "- Если лимит калорий маленький или отрицательный — предложи лёгкие "
    "низкокалорийные варианты (овощи, нежирный белок).\n"
    "- Числа — реалистичные и положительные."
)

# Английский аналог RECOMMEND_SYSTEM_PROMPT (те же ключи, значения на английском).
RECOMMEND_SYSTEM_PROMPT_EN = (
    "You are an experienced nutritionist and cook. The user wants to «top up» their "
    "daily calorie/macro goal and asks for 2-3 dish options for the remaining limit.\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else) with the field:\n"
    '  "suggestions" — an array of 2-3 objects, each with the fields:\n'
    '      "dish_name" — string, the dish name in English;\n'
    '      "calories"  — integer, kcal of the portion;\n'
    '      "proteins"  — number, protein in grams;\n'
    '      "fats"      — number, fat in grams;\n'
    '      "carbs"     — number, carbohydrates in grams;\n'
    '      "reason"    — string, why this dish is a good fit '
    "(short, in English).\n\n"
    "Rules:\n"
    "- Suggest real, easy-to-cook dishes.\n"
    "- Overall the dish must fit within the remaining calorie limit "
    "and help top up macros (especially protein).\n"
    "- Consider the goal (loss — weight loss, maintain — maintenance, "
    "gain — muscle gain) and the time of day if they are provided.\n"
    "- If the calorie limit is small or negative — suggest light "
    "low-calorie options (vegetables, lean protein).\n"
    "- Numbers must be realistic and positive."
)

# Системный промпт для подбора спортивных добавок.
SUPPLEMENT_SYSTEM_PROMPT = (
    "Ты — консультант по спортивному питанию. Пользователь просит подсказать "
    "спортивные добавки под его цель и тренировки.\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него) с полем:\n"
    '  "suggestions" — массив из 3-5 объектов, каждый с полями:\n'
    '      "name"    — строка, название добавки на русском '
    '(например: "Креатин моногидрат", "Сывороточный протеин", "Омега-3");\n'
    '      "dosage"  — строка, типичная суточная дозировка '
    '(например: "3-5 г в день");\n'
    '      "note"    — строка, кратко зачем нужна и как принимать.\n\n'
    "Правила:\n"
    "- АКТИВНО предлагай реальные СПОРТИВНЫЕ добавки, а не только витамины и "
    "минералы. Уместно и приветствуется рекомендовать (под цель и тренировки): "
    "креатин моногидрат, протеин/сывороточный (whey), BCAA/EAA (аминокислоты), "
    "бета-аланин, цитруллин, L-карнитин, глютамин, кофеин/предтреник (умеренно), "
    "электролиты, а также омега-3, магний, витамин D и подобные.\n"
    "- Учитывай цель (loss — похудение, maintain — поддержание, "
    "gain — набор массы), если она указана, и привязывай выбор к тренировкам.\n"
    "- НЕ предлагай рецептурные препараты, ГОРМОНЫ, анаболические СТЕРОИДЫ и "
    "любые ЗАПРЕЩЁННЫЕ/допинговые вещества.\n"
    "- Формулировки — общие и осторожные, без медицинских обещаний."
)

# Английский аналог SUPPLEMENT_SYSTEM_PROMPT (те же ключи, значения на английском).
SUPPLEMENT_SYSTEM_PROMPT_EN = (
    "You are a sports nutrition consultant. The user asks you to suggest "
    "sports supplements for their goal and training.\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else) with the field:\n"
    '  "suggestions" — an array of 3-5 objects, each with the fields:\n'
    '      "name"    — string, the supplement name in English '
    '(for example: "Creatine monohydrate", "Whey protein", "Omega-3");\n'
    '      "dosage"  — string, the typical daily dosage '
    '(for example: "3-5 g per day");\n'
    '      "note"    — string, briefly what it is for and how to take it.\n\n'
    "Rules:\n"
    "- ACTIVELY suggest real SPORTS supplements, not just vitamins and minerals. "
    "It is appropriate and encouraged to recommend (tied to the goal and "
    "training): creatine monohydrate, protein/whey, BCAA/EAA (amino acids), "
    "beta-alanine, citrulline, L-carnitine, glutamine, caffeine/pre-workout "
    "(in moderation), electrolytes, as well as omega-3, magnesium, vitamin D "
    "and similar.\n"
    "- Consider the goal (loss — weight loss, maintain — maintenance, "
    "gain — muscle gain) if it is provided, and tie the choice to training.\n"
    "- Do NOT suggest prescription drugs, HORMONES, anabolic STEROIDS or any "
    "BANNED/doping substances.\n"
    "- Keep wording general and cautious, with no medical promises."
)

# Системный промпт для ПЕРСОНАЛЬНОГО подбора добавок: учитывает цель улучшения
# (сон/восстановление/сила/энергия/иммунитет или произвольный текст),
# частоту и тип тренировок, а также цель диеты.
SUPPLEMENT_RECOMMEND_SYSTEM_PROMPT = (
    "Ты — опытный эксперт по спортивному питанию. Подбери пользователю "
    "персональные базовые добавки, учитывая его данные.\n\n"
    "Учитывай при подборе:\n"
    "- ЦЕЛЬ УЛУЧШЕНИЯ (improvement_goal): например, сон, восстановление, "
    "сила, энергия, иммунитет — или произвольный текст пользователя;\n"
    "- ЧАСТОТУ и ТИП тренировок (training_count — число тренировок за "
    "последние 2 недели; workout_types — какие именно тренировки);\n"
    "- ЦЕЛЬ ДИЕТЫ (diet_goal: loss — похудение, maintain — поддержание, "
    "gain — набор массы).\n\n"
    "Примеры логики (ориентир, не жёсткое правило):\n"
    "- цель «сон» -> магний, глицин;\n"
    "- цель «восстановление» + частые тренировки -> протеин, BCAA/EAA, "
    "омега-3, глютамин, магний;\n"
    "- цель «сила» + частые силовые тренировки -> креатин моногидрат, протеин, "
    "бета-аланин;\n"
    "- цель «выносливость/пампинг» -> цитруллин, бета-аланин, электролиты;\n"
    "- цель «энергия» -> кофеин/предтреник умеренно, L-карнитин, витамины "
    "группы B;\n"
    "- цель «иммунитет» -> витамин D, витамин C, цинк, омега-3.\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него) с полем:\n"
    '  "suggestions" — массив из 2-4 объектов, каждый с полями:\n'
    '      "name"    — строка, название добавки на русском '
    '(например: "Креатин моногидрат", "Протеин", "Цитруллин");\n'
    '      "dosage"  — строка, типичная суточная дозировка '
    '(например: "3-5 г в день");\n'
    '      "note"    — строка, кратко зачем нужна именно под цель/тренировки '
    "и как принимать.\n\n"
    "Правила:\n"
    "- АКТИВНО предлагай реальные СПОРТИВНЫЕ добавки, а не только витамины и "
    "минералы. Уместно и приветствуется рекомендовать (под цель и тренировки): "
    "креатин моногидрат, протеин/сывороточный (whey), BCAA/EAA (аминокислоты), "
    "бета-аланин, цитруллин, L-карнитин, глютамин, кофеин/предтреник (умеренно), "
    "электролиты, а также омега-3, магний, витамин D и подобные "
    "(2-4 штуки, без воды).\n"
    "- Связывай выбор с целью улучшения и тренировками пользователя.\n"
    "- НЕ предлагай рецептурные препараты, ГОРМОНЫ, анаболические СТЕРОИДЫ и "
    "любые ЗАПРЕЩЁННЫЕ/допинговые вещества.\n"
    "- Формулировки — общие и осторожные, без медицинских обещаний."
)

# Английский аналог SUPPLEMENT_RECOMMEND_SYSTEM_PROMPT (те же ключи, значения на английском).
SUPPLEMENT_RECOMMEND_SYSTEM_PROMPT_EN = (
    "You are an experienced sports nutrition expert. Pick personal basic "
    "supplements for the user, taking their data into account.\n\n"
    "Consider when choosing:\n"
    "- IMPROVEMENT GOAL (improvement_goal): for example, sleep, recovery, "
    "strength, energy, immunity — or the user's free-form text;\n"
    "- FREQUENCY and TYPE of training (training_count — number of workouts in "
    "the last 2 weeks; workout_types — which workouts exactly);\n"
    "- DIET GOAL (diet_goal: loss — weight loss, maintain — maintenance, "
    "gain — muscle gain).\n\n"
    "Examples of the logic (guideline, not a strict rule):\n"
    "- goal «sleep» -> magnesium, glycine;\n"
    "- goal «recovery» + frequent workouts -> protein, BCAA/EAA, omega-3, "
    "glutamine, magnesium;\n"
    "- goal «strength» + frequent strength training -> creatine monohydrate, "
    "protein, beta-alanine;\n"
    "- goal «endurance/pump» -> citrulline, beta-alanine, electrolytes;\n"
    "- goal «energy» -> caffeine/pre-workout in moderation, L-carnitine, "
    "B vitamins;\n"
    "- goal «immunity» -> vitamin D, vitamin C, zinc, omega-3.\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else) with the field:\n"
    '  "suggestions" — an array of 2-4 objects, each with the fields:\n'
    '      "name"    — string, the supplement name in English '
    '(for example: "Creatine monohydrate", "Protein", "Citrulline");\n'
    '      "dosage"  — string, the typical daily dosage '
    '(for example: "3-5 g per day");\n'
    '      "note"    — string, briefly why it fits the goal/training '
    "and how to take it.\n\n"
    "Rules:\n"
    "- ACTIVELY suggest real SPORTS supplements, not just vitamins and minerals. "
    "It is appropriate and encouraged to recommend (tied to the goal and "
    "training): creatine monohydrate, protein/whey, BCAA/EAA (amino acids), "
    "beta-alanine, citrulline, L-carnitine, glutamine, caffeine/pre-workout "
    "(in moderation), electrolytes, as well as omega-3, magnesium, vitamin D "
    "and similar (2-4 items, no padding).\n"
    "- Tie the choice to the user's improvement goal and training.\n"
    "- Do NOT suggest prescription drugs, HORMONES, anabolic STEROIDS or any "
    "BANNED/doping substances.\n"
    "- Keep wording general and cautious, with no medical promises."
)


def _pick_prompt(prompt_ru: str, prompt_en: str, lang: str) -> str:
    """
    Хелпер выбора system-промпта по языку.

    Возвращает английский вариант при lang=="en", иначе русский.
    Используется текстовыми подсказками; имена JSON-полей одинаковы в обоих
    промптах — меняется только язык значений.
    """
    return prompt_en if _normalize_lang(lang) == "en" else prompt_ru


def _call_text_model(client: "OpenAI", system_prompt: str, user_prompt: str, max_tokens: int | None = None, log_tag: str = "text"):
    """
    Один текстовый вызов модели (без изображения) с принудительным JSON-ответом.

    Использует более дешёвую TEXT_MODEL (gpt-4o-mini по умолчанию). Возвращает
    (content, finish_reason, refusal). max_tokens можно переопределить локально.
    """
    response = client.chat.completions.create(
        model=TEXT_MODEL,
        response_format={"type": "json_object"},
        **_completion_params(TEXT_MODEL, max_tokens or MAX_TOKENS, 0.5),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    _log_usage(log_tag, response)
    choice = response.choices[0]
    content = choice.message.content
    refusal = getattr(choice.message, "refusal", None)
    return content, choice.finish_reason, refusal


def _run_text_completion(system_prompt: str, user_prompt: str, log_tag: str, max_tokens: int | None = None) -> tuple[dict, dict]:
    """
    Общий «движок» текстовых AI-подсказок (подбор блюд/добавок).

    Делает MAX_ATTEMPTS попыток вызвать модель, устойчиво разбирает JSON и
    возвращает кортеж (data, debug), где data — распарсенный объект ответа,
    debug — служебная информация о вызове.

    При неудаче всех попыток выбрасывает AIError (как и analyze_food_image).
    """
    client = _get_client()

    last_error = "неизвестная ошибка"
    raw = ""
    finish_reason = None
    refusal = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            content, finish_reason, refusal = _call_text_model(
                client, system_prompt, user_prompt, max_tokens=max_tokens, log_tag=log_tag
            )
            raw = content or ""
            logger.info(
                "AI[%s] попытка %d/%d: finish=%s refusal=%s raw=%s",
                log_tag, attempt, MAX_ATTEMPTS, finish_reason, refusal, raw[:600],
            )
        except Exception as exc:  # сеть, ключ, лимиты OpenAI и т.п.
            last_error = f"ошибка обращения к OpenAI: {exc}"
            logger.warning(
                "AI[%s] попытка %d/%d не удалась: %s", log_tag, attempt, MAX_ATTEMPTS, exc
            )
            continue

        if not raw.strip():
            last_error = (
                "модель вернула пустой ответ "
                f"(finish_reason={finish_reason}, refusal={refusal or 'нет'})"
            )
            continue

        data = _extract_json(raw)
        if data is None:
            last_error = "ответ модели не является корректным JSON"
            continue

        debug = {
            "raw": raw,
            "finish_reason": finish_reason,
            "refusal": refusal,
            "model": MODEL,
            "attempts": attempt,
        }
        return data, debug

    # Все попытки исчерпаны — поведение как в analyze_food_image.
    logger.error("AI[%s]: все попытки исчерпаны. Последняя ошибка: %s", log_tag, last_error)
    raise AIError(
        f"AI не ответил ({log_tag}): {last_error}",
        raw=raw,
        finish_reason=finish_reason,
        refusal=refusal,
    )


# Системный промпт для AI-оценки калорий по свободному описанию активности
# (для тренировок типа "other", когда нет MET-коэффициента в таблице).
WORKOUT_ESTIMATE_SYSTEM_PROMPT = (
    "Ты — эксперт по физиологии нагрузок. По свободному описанию активности, "
    "её длительности (минуты) и, если задан, весу тела (кг) оцени реалистичный "
    "ИТОГОВЫЙ расход калорий и примерный MET-коэффициент активности.\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него) с полями:\n"
    '      "calories" — целое число, суммарно сожжённые калории (ккал);\n'
    '      "met"      — число, примерный MET-коэффициент активности.\n\n'
    "Правила:\n"
    "- Значения реалистичные и положительные.\n"
    "- Если вес не задан — прими средний около 70 кг.\n"
    "- Никакого текста вне JSON."
)

# Английский аналог WORKOUT_ESTIMATE_SYSTEM_PROMPT (те же ключи).
WORKOUT_ESTIMATE_SYSTEM_PROMPT_EN = (
    "You are an exercise physiology expert. From a free-text activity "
    "description, its duration (minutes) and, if given, body weight (kg), "
    "estimate a realistic TOTAL calories burned and an approximate MET value "
    "of the activity.\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else) with the fields:\n"
    '      "calories" — integer, total calories burned (kcal);\n'
    '      "met"      — number, approximate MET of the activity.\n\n'
    "Rules:\n"
    "- Values must be realistic and positive.\n"
    "- If weight is not given, assume an average of about 70 kg.\n"
    "- No text outside the JSON."
)


def estimate_workout_calories(
    description: str,
    duration_min: int,
    weight_kg: float | None = None,
    lang: str = "ru",
) -> dict:
    """
    Оценить сожжённые калории и MET по свободному описанию активности (ИИ).

    Используется для тренировок типа "other", когда нет готового MET-коэффициента.
    Параметр lang ("ru" по умолчанию, либо "en") влияет только на язык промпта.

    Возвращает словарь:
        {"calories": int, "met": float}

    При пустом/непригодном описании или неудаче обращения к ИИ выбрасывает
    AIError (502 на уровне роута).
    """
    lang = _normalize_lang(lang)

    # Без внятного описания оценивать нечего — сразу сигналим ошибкой.
    text = (description or "").strip()
    if not text:
        raise AIError("estimate_workout: пустое описание активности")

    duration = _coerce_int(duration_min)

    # Формируем запрос пользователя на нужном языке.
    if lang == "en":
        parts = [
            "Estimate the calories burned for the following activity.",
            f"Activity: {text}.",
            f"Duration: {duration} minutes.",
        ]
        if weight_kg:
            parts.append(f"Body weight: {_coerce_float(weight_kg)} kg.")
        parts.append("Return the result strictly in JSON format following the instructions.")
    else:
        parts = [
            "Оцени сожжённые калории для следующей активности.",
            f"Активность: {text}.",
            f"Длительность: {duration} минут.",
        ]
        if weight_kg:
            parts.append(f"Вес тела: {_coerce_float(weight_kg)} кг.")
        parts.append("Верни результат строго в формате JSON по инструкции.")
    user_prompt = "\n".join(parts)

    system_prompt = _pick_prompt(
        WORKOUT_ESTIMATE_SYSTEM_PROMPT, WORKOUT_ESTIMATE_SYSTEM_PROMPT_EN, lang
    )
    data, _debug = _run_text_completion(
        system_prompt, user_prompt, log_tag="workout_estimate"
    )

    # Нормализуем числа: калории — целое положительное, MET — положительное число.
    calories = _coerce_int(data.get("calories"))
    met = _coerce_float(data.get("met"))
    if calories <= 0:
        raise AIError("estimate_workout: модель вернула непригодные калории")
    if met <= 0:
        met = 4.0

    return {"calories": max(0, calories), "met": met}


def recommend_meals(
    remaining_calories: int,
    remaining_proteins: float,
    remaining_fats: float,
    remaining_carbs: float,
    diet_goal: str | None = None,
    time_of_day: str | None = None,
    lang: str = "ru",
) -> dict:
    """
    Подбирает 2-3 варианта блюд под оставшийся на день лимит КБЖУ.

    Параметр lang ("ru" по умолчанию, либо "en") управляет языком значений
    dish_name/reason в ответе. Ключи JSON не меняются.

    Возвращает словарь:
        {
            "suggestions": [
                {"dish_name": str, "calories": int, "proteins": float,
                 "fats": float, "carbs": float, "reason": str},
                ...
            ]
        }

    При неудаче обращения к ИИ выбрасывает AIError (502 на уровне роута).
    """
    lang = _normalize_lang(lang)

    # Формируем запрос пользователя с понятными числами остатка — на нужном языке.
    if lang == "en":
        parts = [
            "Suggest 2-3 dishes to top up the remaining daily calorie/macro goal.",
            f"Calories remaining: {remaining_calories} kcal.",
            f"Protein remaining: {remaining_proteins} g.",
            f"Fat remaining: {remaining_fats} g.",
            f"Carbs remaining: {remaining_carbs} g.",
        ]
        if diet_goal:
            parts.append(f"User goal: {diet_goal}.")
        if time_of_day:
            parts.append(f"Time of day / meal: {time_of_day}.")
        parts.append("Return the result strictly in JSON format following the instructions.")
    else:
        parts = [
            "Подбери 2-3 блюда, чтобы добрать оставшуюся за день норму КБЖУ.",
            f"Осталось калорий: {remaining_calories} ккал.",
            f"Осталось белков: {remaining_proteins} г.",
            f"Осталось жиров: {remaining_fats} г.",
            f"Осталось углеводов: {remaining_carbs} г.",
        ]
        if diet_goal:
            parts.append(f"Цель пользователя: {diet_goal}.")
        if time_of_day:
            parts.append(f"Время суток / приём пищи: {time_of_day}.")
        parts.append("Верни результат строго в формате JSON по инструкции.")
    user_prompt = "\n".join(parts)

    system_prompt = _pick_prompt(
        RECOMMEND_SYSTEM_PROMPT, RECOMMEND_SYSTEM_PROMPT_EN, lang
    )
    data, _debug = _run_text_completion(
        system_prompt, user_prompt, log_tag="recommend"
    )

    # Нормализуем массив предложений: чистим типы и пропускаем мусор.
    raw_items = data.get("suggestions")
    if not isinstance(raw_items, list):
        raw_items = []

    suggestions: list[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        dish_name = item.get("dish_name")
        if not isinstance(dish_name, str) or not dish_name.strip():
            continue
        reason = item.get("reason")
        if not isinstance(reason, str):
            reason = ""
        suggestions.append(
            {
                "dish_name": dish_name.strip(),
                "calories": _coerce_int(item.get("calories")),
                "proteins": _coerce_float(item.get("proteins")),
                "fats": _coerce_float(item.get("fats")),
                "carbs": _coerce_float(item.get("carbs")),
                "reason": reason.strip(),
            }
        )

    # Если модель вернула валидный JSON, но без пригодных вариантов —
    # считаем это неудачей разбора (роут отдаст 502).
    if not suggestions:
        raise AIError(
            "AI не вернул ни одного корректного варианта блюда",
            raw=_debug.get("raw", ""),
            finish_reason=_debug.get("finish_reason"),
            refusal=_debug.get("refusal"),
        )

    return {"suggestions": suggestions}


def suggest_supplements(diet_goal: str | None = None, lang: str = "ru") -> dict:
    """
    Подбирает базовые спортивные добавки под цель пользователя.

    Параметр lang ("ru" по умолчанию, либо "en") управляет языком значений
    name/dosage/note в ответе. Ключи JSON не меняются.

    Возвращает словарь:
        {
            "suggestions": [
                {"name": str, "dosage": str, "note": str},
                ...
            ]
        }

    При неудаче обращения к ИИ выбрасывает AIError (502 на уровне роута).
    Дисклеймер добавляется на уровне роута, чтобы держать его в одном месте.
    """
    lang = _normalize_lang(lang)

    # Запрос пользователя; цель добавляем, если она известна — на нужном языке.
    if lang == "en":
        parts = ["Suggest basic sports supplements."]
        if diet_goal:
            parts.append(f"My goal: {diet_goal}.")
        parts.append("Return the result strictly in JSON format following the instructions.")
    else:
        parts = ["Подскажи базовые спортивные добавки."]
        if diet_goal:
            parts.append(f"Моя цель: {diet_goal}.")
        parts.append("Верни результат строго в формате JSON по инструкции.")
    user_prompt = "\n".join(parts)

    system_prompt = _pick_prompt(
        SUPPLEMENT_SYSTEM_PROMPT, SUPPLEMENT_SYSTEM_PROMPT_EN, lang
    )
    data, _debug = _run_text_completion(
        system_prompt, user_prompt, log_tag="supplements"
    )

    # Нормализуем массив рекомендаций.
    raw_items = data.get("suggestions")
    if not isinstance(raw_items, list):
        raw_items = []

    suggestions: list[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        dosage = item.get("dosage")
        if not isinstance(dosage, str):
            dosage = ""
        note = item.get("note")
        if not isinstance(note, str):
            note = ""
        suggestions.append(
            {
                "name": name.strip(),
                "dosage": dosage.strip(),
                "note": note.strip(),
            }
        )

    if not suggestions:
        raise AIError(
            "AI не вернул ни одной корректной добавки",
            raw=_debug.get("raw", ""),
            finish_reason=_debug.get("finish_reason"),
            refusal=_debug.get("refusal"),
        )

    return {"suggestions": suggestions}


def recommend_supplements(
    improvement_goal: str | None = None,
    training_count: int = 0,
    workout_types: list[str] | None = None,
    diet_goal: str | None = None,
    lang: str = "ru",
) -> dict:
    """
    Персональный подбор спортивных добавок (2-4 шт.) с учётом:
      * improvement_goal — цели улучшения (сон/восстановление/сила/энергия/
        иммунитет или произвольный текст пользователя);
      * training_count   — числа тренировок за последние 2 недели;
      * workout_types    — типов этих тренировок;
      * diet_goal        — цели диеты (loss/maintain/gain).

    Параметр lang ("ru" по умолчанию, либо "en") управляет языком значений
    name/dosage/note в ответе. Ключи JSON не меняются.

    Возвращает словарь:
        {
            "suggestions": [
                {"name": str, "dosage": str, "note": str},
                ...
            ]
        }

    Использует тот же паттерн, что suggest_supplements (OpenAI json_object,
    ретрай, устойчивый разбор JSON). При неудаче обращения к ИИ выбрасывает
    AIError (502 на уровне роута). Дисклеймер добавляется на уровне роута.
    """
    lang = _normalize_lang(lang)

    # Формируем запрос пользователя из тех данных, что известны — на нужном языке.
    if lang == "en":
        parts = ["Pick personal sports supplements for me (2-4 items)."]

        if improvement_goal and str(improvement_goal).strip():
            parts.append(f"Improvement goal: {str(improvement_goal).strip()}.")
        else:
            parts.append("Improvement goal not specified — pick a basic set.")

        # Частота тренировок за последние 2 недели.
        parts.append(f"Workouts in the last 2 weeks: {_coerce_int(training_count)}.")

        # Типы тренировок (если есть) — перечисляем их через запятую.
        if workout_types:
            types_clean = [
                str(t).strip() for t in workout_types if t and str(t).strip()
            ]
            if types_clean:
                parts.append("Training types: " + ", ".join(types_clean) + ".")

        if diet_goal and str(diet_goal).strip():
            parts.append(f"Diet goal: {str(diet_goal).strip()}.")

        parts.append("Return the result strictly in JSON format following the instructions.")
    else:
        parts = ["Подбери мне персональные спортивные добавки (2-4 штуки)."]

        if improvement_goal and str(improvement_goal).strip():
            parts.append(f"Цель улучшения: {str(improvement_goal).strip()}.")
        else:
            parts.append("Цель улучшения не указана — подбери базовый набор.")

        # Частота тренировок за последние 2 недели.
        parts.append(f"Тренировок за последние 2 недели: {_coerce_int(training_count)}.")

        # Типы тренировок (если есть) — перечисляем их через запятую.
        if workout_types:
            types_clean = [
                str(t).strip() for t in workout_types if t and str(t).strip()
            ]
            if types_clean:
                parts.append("Типы тренировок: " + ", ".join(types_clean) + ".")

        if diet_goal and str(diet_goal).strip():
            parts.append(f"Цель диеты: {str(diet_goal).strip()}.")

        parts.append("Верни результат строго в формате JSON по инструкции.")
    user_prompt = "\n".join(parts)

    system_prompt = _pick_prompt(
        SUPPLEMENT_RECOMMEND_SYSTEM_PROMPT,
        SUPPLEMENT_RECOMMEND_SYSTEM_PROMPT_EN,
        lang,
    )
    data, _debug = _run_text_completion(
        system_prompt, user_prompt, log_tag="supplement_recommend"
    )

    # Нормализуем массив рекомендаций (та же чистка типов, что в suggest_supplements).
    raw_items = data.get("suggestions")
    if not isinstance(raw_items, list):
        raw_items = []

    suggestions: list[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        dosage = item.get("dosage")
        if not isinstance(dosage, str):
            dosage = ""
        note = item.get("note")
        if not isinstance(note, str):
            note = ""
        suggestions.append(
            {
                "name": name.strip(),
                "dosage": dosage.strip(),
                "note": note.strip(),
            }
        )

    if not suggestions:
        raise AIError(
            "AI не вернул ни одной корректной добавки",
            raw=_debug.get("raw", ""),
            finish_reason=_debug.get("finish_reason"),
            refusal=_debug.get("refusal"),
        )

    return {"suggestions": suggestions}


# --------------------------------------------------------------------------- #
#  Советы по восстановлению после тренировок (ИИ)
#
#  БЕЗОПАСНОСТЬ — главный приоритет: модель НЕ ставит диагнозы, отличает обычную
#  крепатуру от тревожных симптомов и обязана вернуть «красные флаги» — признаки,
#  при которых нужно к врачу. Дисклеймер добавляется на уровне роута.
# --------------------------------------------------------------------------- #

# Допустимые зоны тела (языконезависимые ключи; подписи локализует фронт).
RECOVERY_ZONES = (
    "legs", "back", "shoulders", "arms", "chest", "core", "neck", "knees", "other",
)

# Человекочитаемые названия зон для промпта.
_ZONE_NAMES_RU = {
    "legs": "ноги", "back": "спина", "shoulders": "плечи", "arms": "руки",
    "chest": "грудь", "core": "пресс/корпус", "neck": "шея", "knees": "колени",
    "other": "другое",
}
_ZONE_NAMES_EN = {
    "legs": "legs", "back": "back", "shoulders": "shoulders", "arms": "arms",
    "chest": "chest", "core": "core/abs", "neck": "neck", "knees": "knees",
    "other": "other",
}

RECOVERY_SYSTEM_PROMPT = (
    "Ты — опытный тренер по физической подготовке и специалист по восстановлению. "
    "Пользователь жалуется на дискомфорт в определённой зоне тела после тренировок. "
    "Дай практичный совет по восстановлению.\n\n"
    "КРИТИЧЕСКИ ВАЖНО ПО БЕЗОПАСНОСТИ:\n"
    "- Ты НЕ ставишь диагноз и НЕ назначаешь лечение. Ты предполагаешь наиболее "
    "вероятную причину и даёшь общие рекомендации по восстановлению.\n"
    "- Чётко отличай обычную мышечную боль после нагрузки (крепатура, DOMS: "
    "тупая, симметричная, появляется через 12-48 часов, проходит за 2-4 дня) "
    "от ТРЕВОЖНЫХ признаков.\n"
    "- Если в жалобе есть признаки травмы — ОБЯЗАТЕЛЬНО поставь их в red_flags и "
    "прямо порекомендуй обратиться к врачу, а не «потерпеть».\n"
    "- НЕ рекомендуй обезболивающие, рецептурные препараты и любые лекарства.\n"
    "- Не запугивай: если это похоже на обычную крепатуру — так и скажи спокойно.\n\n"
    "ТРЕВОЖНЫЕ ПРИЗНАКИ (при наличии — обязательно к врачу): резкая/острая боль; "
    "боль во время самого движения, а не после; отёк, покраснение, местное "
    "повышение температуры сустава; онемение, покалывание, «прострелы» в конечность; "
    "щелчок/хруст в момент травмы; невозможность опереться на ногу или поднять "
    "руку; боль, которая не проходит больше 7-10 дней или усиливается; "
    "боль в груди, одышка, головокружение; боль в пояснице, отдающая в ногу.\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него) с полями:\n"
    '  "likely_cause" — строка: наиболее вероятная причина простыми словами '
    "(1-2 предложения);\n"
    '  "is_typical_soreness" — true, если это похоже на обычную крепатуру, '
    "false — если есть признаки, требующие осторожности;\n"
    '  "today" — массив из 2-4 строк: что сделать СЕГОДНЯ (конкретные действия: '
    "лёгкая активность, растяжка, тепло/холод, сон, вода, питание);\n"
    '  "avoid" — массив из 2-3 строк: чего избегать в ближайшие дни;\n'
    '  "training" — строка: когда и как возвращаться к нагрузке на эту зону '
    "(например: «лёгкая нагрузка через 1-2 дня, полная — когда боль пройдёт»);\n"
    '  "red_flags" — массив из 2-4 строк: при каких признаках обратиться к врачу.\n\n'
    "Правила:\n"
    "- Пиши по-русски, коротко и по делу, без воды и без медицинских терминов.\n"
    "- Учитывай недавние тренировки пользователя, если они указаны.\n"
    "- Каждый пункт — законченная короткая фраза (до 120 символов)."
)

RECOVERY_SYSTEM_PROMPT_EN = (
    "You are an experienced strength coach and recovery specialist. The user "
    "reports discomfort in a body area after training. Give practical recovery advice.\n\n"
    "CRITICAL SAFETY RULES:\n"
    "- You do NOT diagnose and do NOT prescribe treatment. You suggest the most "
    "likely cause and give general recovery guidance.\n"
    "- Clearly distinguish normal post-exercise muscle soreness (DOMS: dull, "
    "symmetric, appears 12-48h later, resolves in 2-4 days) from WARNING signs.\n"
    "- If the complaint shows signs of injury, you MUST list them in red_flags and "
    "directly recommend seeing a doctor rather than pushing through.\n"
    "- Do NOT recommend painkillers, prescription drugs or any medication.\n"
    "- Do not scare the user: if it looks like ordinary soreness, say so calmly.\n\n"
    "WARNING SIGNS (must see a doctor): sharp/acute pain; pain during the movement "
    "itself rather than after; swelling, redness, local joint warmth; numbness, "
    "tingling, shooting pain into a limb; a pop/click at the moment of injury; "
    "inability to bear weight or raise the arm; pain lasting over 7-10 days or "
    "worsening; chest pain, shortness of breath, dizziness; low-back pain radiating "
    "into the leg.\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else) with fields:\n"
    '  "likely_cause" — string: the most likely cause in plain words (1-2 sentences);\n'
    '  "is_typical_soreness" — true if this looks like ordinary soreness, false if '
    "there are signs requiring caution;\n"
    '  "today" — array of 2-4 strings: what to do TODAY (concrete actions);\n'
    '  "avoid" — array of 2-3 strings: what to avoid in the coming days;\n'
    '  "training" — string: when and how to return to loading this area;\n'
    '  "red_flags" — array of 2-4 strings: signs that require seeing a doctor.\n\n'
    "Rules:\n"
    "- Write in English, short and practical, no fluff, no medical jargon.\n"
    "- Take the user's recent workouts into account if provided.\n"
    "- Each item is a short complete phrase (up to 120 characters)."
)


def recovery_advice(
    zone: str,
    complaint: str | None = None,
    recent_workouts: list[str] | None = None,
    lang: str = "ru",
) -> dict:
    """Совет по восстановлению для выбранной зоны тела.

    Параметры:
      * zone            — ключ зоны из RECOVERY_ZONES;
      * complaint       — свободное описание жалобы (может отсутствовать);
      * recent_workouts — краткие описания последних тренировок (для контекста);
      * lang            — "ru" | "en" (язык значений; ключи JSON не меняются).

    Возвращает словарь с ключами likely_cause, is_typical_soreness, today,
    avoid, training, red_flags. При сбое ИИ выбрасывает AIError (502 на роуте).
    НЕ является медицинской рекомендацией — дисклеймер добавляет роут.
    """
    lang = _normalize_lang(lang)
    zone_key = zone if zone in RECOVERY_ZONES else "other"

    if lang == "en":
        parts = [f"Body area: {_ZONE_NAMES_EN[zone_key]}."]
        if complaint and str(complaint).strip():
            parts.append(f"The user describes: {str(complaint).strip()}")
        else:
            parts.append("No extra description — assume typical post-workout soreness.")
        if recent_workouts:
            clean = [str(w).strip() for w in recent_workouts if w and str(w).strip()]
            if clean:
                parts.append("Recent workouts: " + "; ".join(clean[:10]) + ".")
        parts.append("Return the result strictly as JSON per the instructions.")
    else:
        parts = [f"Зона тела: {_ZONE_NAMES_RU[zone_key]}."]
        if complaint and str(complaint).strip():
            parts.append(f"Пользователь описывает так: {str(complaint).strip()}")
        else:
            parts.append("Дополнительного описания нет — считай типичной болью после нагрузки.")
        if recent_workouts:
            clean = [str(w).strip() for w in recent_workouts if w and str(w).strip()]
            if clean:
                parts.append("Недавние тренировки: " + "; ".join(clean[:10]) + ".")
        parts.append("Верни результат строго в формате JSON по инструкции.")

    system_prompt = _pick_prompt(
        RECOVERY_SYSTEM_PROMPT, RECOVERY_SYSTEM_PROMPT_EN, lang
    )
    data, _debug = _run_text_completion(
        system_prompt, "\n".join(parts), log_tag="recovery_advice"
    )

    def _str_list(key: str, limit: int) -> list[str]:
        raw = data.get(key)
        if not isinstance(raw, list):
            return []
        out = []
        for x in raw:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
            if len(out) >= limit:
                break
        return out

    likely_cause = data.get("likely_cause")
    if not isinstance(likely_cause, str):
        likely_cause = ""
    training = data.get("training")
    if not isinstance(training, str):
        training = ""

    result = {
        "zone": zone_key,
        "likely_cause": likely_cause.strip(),
        "is_typical_soreness": bool(data.get("is_typical_soreness")),
        "today": _str_list("today", 4),
        "avoid": _str_list("avoid", 3),
        "training": training.strip(),
        "red_flags": _str_list("red_flags", 4),
    }

    # Совет без единого практического пункта бесполезен — считаем это сбоем ИИ.
    if not result["today"] and not result["likely_cause"]:
        raise AIError(
            "AI не вернул совет по восстановлению",
            raw=_debug.get("raw", ""),
            finish_reason=_debug.get("finish_reason"),
            refusal=_debug.get("refusal"),
        )

    return result


# --------------------------------------------------------------------------- #
#  Голосовой ввод еды (Этап 2): распознавание речи (Whisper) + разбор текста
# --------------------------------------------------------------------------- #

# Допустимые приёмы пищи для голосового разбора.
_VOICE_MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")

# Системный промпт разбора текстового описания еды на блюда с КБЖУ (RU).
PARSE_FOOD_SYSTEM_PROMPT = (
    "Ты — внимательный нутрициолог. Пользователь голосом/текстом описал, что съел "
    "(например: «на завтрак два варёных яйца, 100 г колбасы и 100 г хлеба»). "
    "Извлеки КАЖДЫЙ продукт/блюдо с учётом количества и оцени его КБЖУ.\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него) с полями:\n"
    '  "meal_type" — "breakfast" | "lunch" | "dinner" | "snack" — приём пищи из фразы '
    "(завтрак->breakfast, обед->lunch, ужин->dinner, перекус->snack); если не указано — null;\n"
    '  "items" — массив блюд, по одному объекту на продукт: '
    '{"dish_name": строка на русском, "quantity": число (количество/вес), '
    '"unit": "pcs"|"g"|"ml"|"serving", "calories": целое ккал, "proteins": число г, '
    '"fats": число г, "carbs": число г}.\n\n'
    "Правила:\n"
    '- "unit" — СТРОГО один из ключей: "pcs" (штучное: яйца, бананы, котлеты), '
    '"g" (весовое: хлеб, рис, мясо), "ml" (жидкое: молоко, сок), '
    '"serving" (порция — если ни то, ни другое).\n'
    '- "quantity" — количество для единицы: для "pcs" число штук, для "g" вес в граммах, '
    'для "ml" объём в миллилитрах, для "serving" число порций.\n'
    "- calories и БЖУ — это СУММА (ИТОГО) для указанного quantity, а НЕ на единицу.\n"
    "- ОБЪЕДИНЯЙ повторяющиеся одинаковые продукты в ОДИН элемент с суммарным quantity "
    '(например, «два варёных яйца» -> ОДИН объект {dish_name:"яйцо варёное", quantity:2, '
    'unit:"pcs", ...итого за 2 яйца}; «100 грамм хлеба» -> {dish_name:"хлеб", quantity:100, '
    'unit:"g", ...итого за 100 г}).\n'
    "- Учитывай количество/вес (штуки, граммы) при оценке.\n"
    "- Оценивай реалистично; для настоящей еды калории и БЖУ больше нуля.\n"
    "- Не добавляй того, чего нет в описании; если еды в тексте нет — items пустой.\n"
    "- dish_name — на русском языке."
)

# Английский аналог: те же ключи JSON, dish_name — на английском.
PARSE_FOOD_SYSTEM_PROMPT_EN = (
    "You are an attentive nutritionist. The user described by voice/text what they ate "
    '(e.g. "for breakfast two boiled eggs, 100 g of sausage and 100 g of bread"). '
    "Extract EACH product/dish taking quantity into account and estimate its calories and macros.\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else) with the fields:\n"
    '  "meal_type" — "breakfast" | "lunch" | "dinner" | "snack" — the meal detected from the '
    "phrase; if not stated — null;\n"
    '  "items" — array of dishes, one object per product: '
    '{"dish_name": string in English, "quantity": number (count/weight), '
    '"unit": "pcs"|"g"|"ml"|"serving", "calories": integer kcal, "proteins": number g, '
    '"fats": number g, "carbs": number g}.\n\n'
    "Rules:\n"
    '- "unit" must be STRICTLY one of the keys: "pcs" (countable: eggs, bananas, cutlets), '
    '"g" (by weight: bread, rice, meat), "ml" (liquid: milk, juice), '
    '"serving" (a portion — if neither of the above).\n'
    '- "quantity" is the amount for the unit: for "pcs" the number of pieces, for "g" the '
    'weight in grams, for "ml" the volume in millilitres, for "serving" the number of portions.\n'
    "- calories and macros are the TOTAL for the given quantity, NOT per unit.\n"
    "- COMBINE repeated identical foods into a SINGLE item with the summed quantity "
    '(e.g. "two boiled eggs" -> ONE object {dish_name:"boiled egg", quantity:2, unit:"pcs", '
    '...totals for 2 eggs}; "100 grams of bread" -> {dish_name:"bread", quantity:100, '
    'unit:"g", ...totals for 100 g}).\n'
    "- Take the stated quantity/weight (pieces, grams) into account.\n"
    "- Estimate realistically; for real food calories and macros are greater than zero.\n"
    "- Do not add anything not in the description; if there is no food — items is empty.\n"
    "- dish_name must be in English."
)


# Модель распознавания речи. По умолчанию gpt-4o-transcribe — заметно точнее
# whisper-1 (и на русском, и на английском). Можно переопределить через env.
# При сбое основной модели делаем фолбэк на whisper-1, чтобы голос всегда работал.
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "gpt-4o-transcribe")

# Контекст-подсказка (prompt) распознавания: смещает модель к «пищевой» лексике —
# так она гораздо точнее слышит названия продуктов, блюда, количество и вес.
_TRANSCRIBE_PROMPT_RU = (
    "Пользователь диктует, что он съел: перечисляет продукты, блюда, их количество "
    "и вес. Например: два варёных яйца, сто грамм хлеба, тарелка гречки с курицей, "
    "банан, стакан молока, ложка мёда, порция творога, овсянка на молоке."
)
_TRANSCRIBE_PROMPT_EN = (
    "The user dictates what they ate: foods, dishes, their quantity and weight. "
    "For example: two boiled eggs, one hundred grams of bread, a bowl of buckwheat "
    "with chicken, a banana, a glass of milk, a spoon of honey, a portion of cottage cheese."
)


def _transcribe_once(client, model: str, audio_bytes: bytes, filename: str, norm: str | None) -> str:
    """Одна попытка распознавания заданной моделью (с языком и пищевым prompt-контекстом)."""
    kwargs = {
        "model": model,
        "file": (filename or "audio.ogg", audio_bytes),
        # Пищевой контекст резко повышает точность на названиях еды.
        "prompt": _TRANSCRIBE_PROMPT_EN if norm == "en" else _TRANSCRIBE_PROMPT_RU,
    }
    if norm:
        kwargs["language"] = norm
    resp = client.audio.transcriptions.create(**kwargs)
    return (getattr(resp, "text", "") or "").strip()


def transcribe_audio(audio_bytes: bytes, filename: str = "audio.ogg", lang: str | None = None) -> str:
    """
    Распознаёт речь в тексте через OpenAI (по умолчанию gpt-4o-transcribe,
    фолбэк — whisper-1).

    audio_bytes — байты аудио (ogg/webm/mp3/m4a/wav и т.п.);
    filename    — имя файла с расширением (важно для определения формата);
    lang        — подсказка языка ("ru"/"en"); если не задан — модель определит сама.

    Возвращает распознанный текст (без крайних пробелов). При пустом аудио,
    пустом результате или ошибке распознавания выбрасывает AIError.
    """
    if not audio_bytes:
        raise AIError("Пустое аудио — нечего распознавать")

    # Подсказка языка только как двухбуквенный ISO-639-1, иначе не передаём.
    norm = None
    code = str(lang or "").strip().lower()
    if code.startswith("ru"):
        norm = "ru"
    elif code.startswith("en"):
        norm = "en"

    client = _get_transcribe_client()

    # 1) Основная (более точная) модель. 2) Фолбэк на whisper-1 при любом сбое
    #    (например, если у аккаунта нет доступа к новой модели).
    text = ""
    used_model = TRANSCRIBE_MODEL
    try:
        text = _transcribe_once(client, TRANSCRIBE_MODEL, audio_bytes, filename, norm)
    except Exception as exc:  # сеть, ключ, нет доступа к модели, формат и т.п.
        logger.warning(
            "transcribe_audio: модель %s не сработала (%s) — фолбэк на whisper-1",
            TRANSCRIBE_MODEL, exc,
        )
        used_model = "whisper-1"
        try:
            text = _transcribe_once(client, "whisper-1", audio_bytes, filename, norm)
        except Exception as exc2:
            logger.warning("transcribe_audio: ошибка распознавания: %s", exc2)
            raise AIError(f"Не удалось распознать речь: {exc2}")

    if not text:
        raise AIError("Распознавание вернуло пустой текст")

    logger.info(
        "transcribe_audio: распознано символов=%d, model=%s, lang=%s",
        len(text), used_model, norm or "auto",
    )
    return text


def parse_food_text(text: str, lang: str = "ru") -> dict:
    """
    По текстовому описанию приёма пищи извлекает список блюд с КБЖУ и определяет
    тип приёма пищи.

    Возвращает {"meal_type": "breakfast"|"lunch"|"dinner"|"snack"|None,
                "items": [{"dish_name","quantity","unit","calories","proteins",
                           "fats","carbs"}, ...]}.
    quantity — число (или None), unit — канонический ключ pcs|g|ml|serving (или None);
    калории и БЖУ — это ИТОГО за указанное количество. Одинаковые продукты модель
    объединяет в один элемент с суммарным quantity.

    Параметр lang ("ru"/"en") управляет языком значений dish_name. При неудаче
    обращения к ИИ или отсутствии блюд выбрасывает AIError.
    """
    lang = _normalize_lang(lang)
    if not text or not str(text).strip():
        raise AIError("Пустой текст для разбора")

    body = str(text).strip()
    if lang == "en":
        user_prompt = (
            "Meal description:\n" + body
            + "\n\nReturn the result strictly in JSON format following the instructions."
        )
    else:
        user_prompt = (
            "Описание приёма пищи:\n" + body
            + "\n\nВерни результат строго в формате JSON по инструкции."
        )

    system_prompt = _pick_prompt(
        PARSE_FOOD_SYSTEM_PROMPT, PARSE_FOOD_SYSTEM_PROMPT_EN, lang
    )
    data, _debug = _run_text_completion(system_prompt, user_prompt, log_tag="parse_food")

    # Приём пищи: валидируем по набору, иначе None.
    meal_type = data.get("meal_type")
    if isinstance(meal_type, str) and meal_type.strip().lower() in _VOICE_MEAL_TYPES:
        meal_type = meal_type.strip().lower()
    else:
        meal_type = None

    # Блюда: чистим типы, пропускаем мусор.
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raw_items = []

    items: list[dict] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        dish_name = it.get("dish_name")
        if not isinstance(dish_name, str) or not dish_name.strip():
            continue
        # Количество и единица измерения (канонический ключ pcs|g|ml|serving или None).
        quantity = it.get("quantity")
        quantity = _coerce_float(quantity) if quantity is not None else None
        unit = _normalize_unit(it.get("unit"))
        items.append(
            {
                "dish_name": dish_name.strip(),
                "quantity": quantity,
                "unit": unit,
                "calories": _coerce_int(it.get("calories")),
                "proteins": _coerce_float(it.get("proteins")),
                "fats": _coerce_float(it.get("fats")),
                "carbs": _coerce_float(it.get("carbs")),
            }
        )

    if not items:
        raise AIError(
            "Не удалось распознать блюда в описании",
            raw=_debug.get("raw", ""),
            finish_reason=_debug.get("finish_reason"),
            refusal=_debug.get("refusal"),
        )

    return {"meal_type": meal_type, "items": items}


# --------------------------------------------------------------------------- #
#  AI-функции (Этап 5): недельный отчёт, планировщик меню, умные предложения
# --------------------------------------------------------------------------- #
#
# Все функции ниже используют тот же «движок» _run_text_completion, что и
# предыдущие текстовые подсказки:
#   * единый клиент OpenAI() (ключ из окружения);
#   * response_format=json_object — модель обязана вернуть JSON-объект;
#   * несколько попыток (MAX_ATTEMPTS) на случай пустого/битого ответа;
#   * устойчивый разбор JSON через _extract_json;
#   * нормализация чисел (_coerce_int/_coerce_float), пропуск мусора;
#   * при неудаче (или пустом результате) — AIError (502 на уровне роута).
#
# Двуязычность: для каждого system-промпта есть английский аналог (..._EN) с
# ТЕМИ ЖЕ ключами JSON, но значениями на английском. Выбор делает _pick_prompt.

# Допустимые приёмы пищи для планировщика меню и предложений еды.
_PLAN_MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")


# --------------------------------------------------------------------------- #
#  1) Недельный AI-отчёт (инсайты, а не просто цифры)
# --------------------------------------------------------------------------- #

# Системный промпт недельного отчёта (RU).
WEEKLY_REPORT_SYSTEM_PROMPT = (
    "Ты — опытный тренер-нутрициолог. Тебе дают НЕДЕЛЬНУЮ статистику питания и "
    "активности пользователя. Твоя задача — дать осмысленные ИНСАЙТЫ, а не просто "
    "повторить цифры.\n\n"
    "Анализируй и связывай между собой:\n"
    "- тренд калорий (calories_trend: первая половина недели против второй);\n"
    "- баланс БЖУ (средние белки/жиры/углеводы и хватает ли белка);\n"
    "- связь калорий с тренировками (workouts_count, total_burned) и весом "
    "(weight_change_kg);\n"
    "- средний дефицит/профицит (avg_deficit = цель минус средние калории);\n"
    "- регулярность ведения дневника (days_logged из 7).\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него) с полями:\n"
    '  "summary"  — строка, 1-2 предложения общего вывода о неделе (на русском);\n'
    '  "insights" — массив из 3-5 строк-инсайтов (на русском), каждый про связь '
    "одного показателя с результатом (тренды, дефицит, белок, тренировки, вес);\n"
    '  "focus"    — строка, ОДИН конкретный фокус-совет на следующую неделю '
    "(на русском).\n\n"
    "Правила:\n"
    "- Опирайся на цифры, но объясняй, что они ЗНАЧАТ, а не просто перечисляй их.\n"
    "- Если данных мало (days_logged маленький) — мягко отметь это и дай общий совет.\n"
    "- Тон — поддерживающий, без медицинских обещаний и запугивания.\n"
    "- insights — это массив строк (а не объектов)."
)

# Английский аналог WEEKLY_REPORT_SYSTEM_PROMPT (те же ключи, значения на английском).
WEEKLY_REPORT_SYSTEM_PROMPT_EN = (
    "You are an experienced coach and nutritionist. You are given the user's WEEKLY "
    "nutrition and activity statistics. Your task is to give meaningful INSIGHTS, not "
    "just repeat the numbers.\n\n"
    "Analyze and connect together:\n"
    "- the calorie trend (calories_trend: first half of the week vs the second);\n"
    "- the macro balance (average protein/fat/carbs and whether protein is enough);\n"
    "- the link between calories and workouts (workouts_count, total_burned) and weight "
    "(weight_change_kg);\n"
    "- the average deficit/surplus (avg_deficit = goal minus average calories);\n"
    "- the consistency of logging (days_logged out of 7).\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else) with the fields:\n"
    '  "summary"  — string, 1-2 sentences with the overall conclusion about the week '
    "(in English);\n"
    '  "insights" — an array of 3-5 insight strings (in English), each about how one '
    "metric relates to the result (trends, deficit, protein, workouts, weight);\n"
    '  "focus"    — string, ONE concrete focus tip for the next week (in English).\n\n'
    "Rules:\n"
    "- Rely on the numbers, but explain what they MEAN, do not just list them.\n"
    "- If there is little data (low days_logged) — gently note it and give a general tip.\n"
    "- Tone — supportive, no medical promises and no scaring.\n"
    "- insights is an array of strings (not objects)."
)


def generate_weekly_report(stats: dict, lang: str = "ru") -> dict:
    """
    Формирует недельный AI-отчёт (инсайты) по статистике пользователя.

    stats — словарь со сводкой за 7 дней, который собирает роут:
        avg_calories, goal, calories_trend, avg_proteins, avg_fats, avg_carbs,
        days_logged, workouts_count, total_burned, weight_change_kg, avg_deficit.

    Параметр lang ("ru" по умолчанию, либо "en") управляет языком текстов
    summary/insights/focus. Ключи JSON не меняются.

    Возвращает словарь:
        {"summary": str, "insights": [str, ...], "focus": str}

    При неудаче обращения к ИИ (или пустом результате) выбрасывает AIError
    (502 на уровне роута).
    """
    lang = _normalize_lang(lang)

    # На вход в промпт отдаём аккуратный JSON статистики — модели проще читать.
    stats = stats if isinstance(stats, dict) else {}
    try:
        stats_json = json.dumps(stats, ensure_ascii=False)
    except (TypeError, ValueError):
        stats_json = "{}"

    if lang == "en":
        user_prompt = (
            "Weekly statistics (JSON):\n" + stats_json
            + "\n\nGive insights and one focus tip. "
            "Return the result strictly in JSON format following the instructions."
        )
    else:
        user_prompt = (
            "Недельная статистика (JSON):\n" + stats_json
            + "\n\nДай инсайты и один фокус-совет. "
            "Верни результат строго в формате JSON по инструкции."
        )

    system_prompt = _pick_prompt(
        WEEKLY_REPORT_SYSTEM_PROMPT, WEEKLY_REPORT_SYSTEM_PROMPT_EN, lang
    )
    data, _debug = _run_text_completion(
        system_prompt, user_prompt, log_tag="weekly_report"
    )

    # summary — строка вывода (обязательна, чтобы отчёт имел смысл).
    summary = data.get("summary")
    if not isinstance(summary, str):
        summary = ""
    summary = summary.strip()

    # insights — массив строк; пропускаем пустые и не-строки.
    raw_insights = data.get("insights")
    if not isinstance(raw_insights, list):
        raw_insights = []
    insights: list[str] = []
    for it in raw_insights:
        if isinstance(it, str) and it.strip():
            insights.append(it.strip())

    # focus — один совет (может отсутствовать).
    focus = data.get("focus")
    if not isinstance(focus, str):
        focus = ""
    focus = focus.strip()

    # Считаем результат непригодным, только если нет ни summary, ни инсайтов.
    if not summary and not insights:
        raise AIError(
            "AI не вернул осмысленный недельный отчёт",
            raw=_debug.get("raw", ""),
            finish_reason=_debug.get("finish_reason"),
            refusal=_debug.get("refusal"),
        )

    return {"summary": summary, "insights": insights, "focus": focus}


# --------------------------------------------------------------------------- #
#  2) AI-планировщик меню (день/неделя) + список покупок
# --------------------------------------------------------------------------- #

# Системный промпт планировщика меню (RU).
MEAL_PLAN_SYSTEM_PROMPT = (
    "Ты — опытный нутрициолог и шеф-повар. Составь пользователю план питания под "
    "его дневную цель по калориям и БЖУ.\n\n"
    "Учитывай:\n"
    "- дневную цель калорий (daily_goal_kcal) и целевые БЖУ (target_proteins/"
    "target_fats/target_carbs), если они заданы;\n"
    "- цель диеты (diet_goal: loss — похудение, maintain — поддержание, "
    "gain — набор массы);\n"
    "- пожелания пользователя (preferences) и бюджет (budget), если указаны.\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него) с полями:\n"
    '  "days" — массив дней; для плана на 1 день — один элемент, для недели — 7. '
    "Каждый день:\n"
    '      {"label": строка-название дня (на русском, например "День 1" или '
    '"Понедельник"),\n'
    '       "meals": {"breakfast": [...], "lunch": [...], "dinner": [...], '
    '"snack": [...]}};\n'
    "      каждый приём пищи — массив блюд, блюдо: "
    '{"dish_name": строка на русском, "calories": целое ккал, "proteins": число г, '
    '"fats": число г, "carbs": число г};\n'
    '  "shopping_list" — массив строк, список покупок на весь план (на русском).\n\n'
    "Правила:\n"
    "- Суммарные калории и БЖУ дня должны примерно соответствовать цели "
    "(укладывайся в дневную норму, не превышай сильно).\n"
    "- Блюда — реальные, разнообразные и простые в приготовлении.\n"
    "- Учитывай пожелания и бюджет, если они заданы.\n"
    "- Числа — реалистичные и положительные.\n"
    "- Все текстовые значения — на русском языке."
)

# Английский аналог MEAL_PLAN_SYSTEM_PROMPT (те же ключи, значения на английском).
MEAL_PLAN_SYSTEM_PROMPT_EN = (
    "You are an experienced nutritionist and chef. Build a meal plan for the user "
    "under their daily calorie and macro goal.\n\n"
    "Consider:\n"
    "- the daily calorie goal (daily_goal_kcal) and target macros (target_proteins/"
    "target_fats/target_carbs) if provided;\n"
    "- the diet goal (diet_goal: loss — weight loss, maintain — maintenance, "
    "gain — muscle gain);\n"
    "- the user's preferences and budget if provided.\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else) with the fields:\n"
    '  "days" — an array of days; for a 1-day plan — one element, for a week — 7. '
    "Each day:\n"
    '      {"label": string day name (in English, e.g. "Day 1" or "Monday"),\n'
    '       "meals": {"breakfast": [...], "lunch": [...], "dinner": [...], '
    '"snack": [...]}};\n'
    "      each meal is an array of dishes, a dish is: "
    '{"dish_name": string in English, "calories": integer kcal, "proteins": number g, '
    '"fats": number g, "carbs": number g};\n'
    '  "shopping_list" — an array of strings, a shopping list for the whole plan '
    "(in English).\n\n"
    "Rules:\n"
    "- The day's total calories and macros must roughly match the goal "
    "(stay within the daily norm, do not overshoot much).\n"
    "- Dishes must be real, varied and easy to cook.\n"
    "- Consider preferences and budget if provided.\n"
    "- Numbers must be realistic and positive.\n"
    "- All text values must be in English."
)


def _normalize_plan_dish(item) -> dict | None:
    """
    Нормализует одно блюдо плана меню: чистит типы, отсекает мусор.

    Возвращает словарь {dish_name, calories, proteins, fats, carbs} или None,
    если у блюда нет валидного названия.
    """
    if not isinstance(item, dict):
        return None
    dish_name = item.get("dish_name")
    if not isinstance(dish_name, str) or not dish_name.strip():
        return None
    return {
        "dish_name": dish_name.strip(),
        "calories": _coerce_int(item.get("calories")),
        "proteins": _coerce_float(item.get("proteins")),
        "fats": _coerce_float(item.get("fats")),
        "carbs": _coerce_float(item.get("carbs")),
    }


def generate_meal_plan(
    scope: str,
    daily_goal_kcal: int,
    target_proteins: float | None = None,
    target_fats: float | None = None,
    target_carbs: float | None = None,
    diet_goal: str | None = None,
    preferences: str | None = None,
    budget: str | None = None,
    lang: str = "ru",
) -> dict:
    """
    Составляет план питания на день или на неделю под цель КБЖУ.

    Параметры:
        scope            — "day" (1 день) или "week" (7 дней);
        daily_goal_kcal  — дневная цель по калориям;
        target_*         — целевые БЖУ (необязательно);
        diet_goal        — цель диеты (loss/maintain/gain);
        preferences      — пожелания по еде (необязательно);
        budget           — ограничение по бюджету (необязательно);
        lang             — язык значений ("ru"/"en").

    Возвращает словарь:
        {
            "days": [
                {"label": str,
                 "meals": {"breakfast": [dish, ...], "lunch": [...],
                           "dinner": [...], "snack": [...]}},
                ...
            ],
            "shopping_list": [str, ...]
        }
    где dish = {dish_name, calories, proteins, fats, carbs}.

    При неудаче обращения к ИИ (или пустом результате) выбрасывает AIError.
    """
    lang = _normalize_lang(lang)

    # Нормализуем scope: всё, что не "week", считаем планом на один день.
    scope_norm = "week" if str(scope or "").strip().lower() == "week" else "day"
    goal = _coerce_int(daily_goal_kcal, 2000)
    if goal <= 0:
        goal = 2000

    # Собираем человекочитаемый запрос — на нужном языке.
    if lang == "en":
        parts = []
        if scope_norm == "week":
            parts.append("Make a meal plan for 7 days (a full week).")
        else:
            parts.append("Make a meal plan for 1 day.")
        parts.append(f"Daily calorie goal: {goal} kcal.")
        if target_proteins is not None:
            parts.append(f"Target protein: {_coerce_float(target_proteins)} g/day.")
        if target_fats is not None:
            parts.append(f"Target fat: {_coerce_float(target_fats)} g/day.")
        if target_carbs is not None:
            parts.append(f"Target carbs: {_coerce_float(target_carbs)} g/day.")
        if diet_goal and str(diet_goal).strip():
            parts.append(f"Diet goal: {str(diet_goal).strip()}.")
        if preferences and str(preferences).strip():
            parts.append(f"Preferences: {str(preferences).strip()}.")
        if budget and str(budget).strip():
            parts.append(f"Budget: {str(budget).strip()}.")
        parts.append("Return the result strictly in JSON format following the instructions.")
    else:
        parts = []
        if scope_norm == "week":
            parts.append("Составь план питания на 7 дней (полную неделю).")
        else:
            parts.append("Составь план питания на 1 день.")
        parts.append(f"Дневная цель калорий: {goal} ккал.")
        if target_proteins is not None:
            parts.append(f"Целевой белок: {_coerce_float(target_proteins)} г/день.")
        if target_fats is not None:
            parts.append(f"Целевой жир: {_coerce_float(target_fats)} г/день.")
        if target_carbs is not None:
            parts.append(f"Целевые углеводы: {_coerce_float(target_carbs)} г/день.")
        if diet_goal and str(diet_goal).strip():
            parts.append(f"Цель диеты: {str(diet_goal).strip()}.")
        if preferences and str(preferences).strip():
            parts.append(f"Пожелания: {str(preferences).strip()}.")
        if budget and str(budget).strip():
            parts.append(f"Бюджет: {str(budget).strip()}.")
        parts.append("Верни результат строго в формате JSON по инструкции.")
    user_prompt = "\n".join(parts)

    system_prompt = _pick_prompt(
        MEAL_PLAN_SYSTEM_PROMPT, MEAL_PLAN_SYSTEM_PROMPT_EN, lang
    )

    # План на неделю длиннее обычного ответа — расширяем лимит токенов ЛОКАЛЬНО
    # (параметром), не трогая глобальный MAX_TOKENS других функций (потокобезопасно).
    plan_max_tokens = 3500 if scope_norm == "week" else 1500
    data, _debug = _run_text_completion(
        system_prompt, user_prompt, log_tag="meal_plan", max_tokens=plan_max_tokens
    )

    # Разбираем дни плана.
    raw_days = data.get("days")
    if not isinstance(raw_days, list):
        raw_days = []

    days: list[dict] = []
    for idx, raw_day in enumerate(raw_days, start=1):
        if not isinstance(raw_day, dict):
            continue

        label = raw_day.get("label")
        if not isinstance(label, str) or not label.strip():
            # Дефолтная метка дня, если модель её не дала.
            label = (f"Day {idx}" if lang == "en" else f"День {idx}")
        else:
            label = label.strip()

        raw_meals = raw_day.get("meals")
        if not isinstance(raw_meals, dict):
            raw_meals = {}

        meals: dict[str, list] = {}
        for meal_type in _PLAN_MEAL_TYPES:
            raw_list = raw_meals.get(meal_type)
            if not isinstance(raw_list, list):
                raw_list = []
            dishes: list[dict] = []
            for it in raw_list:
                dish = _normalize_plan_dish(it)
                if dish is not None:
                    dishes.append(dish)
            meals[meal_type] = dishes

        # День добавляем, только если в нём есть хотя бы одно блюдо.
        if any(meals[mt] for mt in _PLAN_MEAL_TYPES):
            days.append({"label": label, "meals": meals})

    # Список покупок — массив строк (необязателен).
    raw_shopping = data.get("shopping_list")
    if not isinstance(raw_shopping, list):
        raw_shopping = []
    shopping_list: list[str] = []
    for it in raw_shopping:
        if isinstance(it, str) and it.strip():
            shopping_list.append(it.strip())
        elif isinstance(it, dict):
            # На случай, если модель вернёт объект — берём поле name/item.
            name = it.get("name") or it.get("item") or it.get("dish_name")
            if isinstance(name, str) and name.strip():
                shopping_list.append(name.strip())

    if not days:
        raise AIError(
            "AI не вернул ни одного дня плана питания",
            raw=_debug.get("raw", ""),
            finish_reason=_debug.get("finish_reason"),
            refusal=_debug.get("refusal"),
        )

    return {"days": days, "shopping_list": shopping_list}


# --------------------------------------------------------------------------- #
#  3) Замена одного блюда в плане (regenerate item)
# --------------------------------------------------------------------------- #

# Системный промпт замены одного блюда (RU).
REGENERATE_ITEM_SYSTEM_PROMPT = (
    "Ты — нутрициолог и повар. Пользователь хочет ЗАМЕНИТЬ одно блюдо в плане "
    "питания на альтернативу для указанного приёма пищи.\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него) — ОДНО блюдо с полями:\n"
    '  "dish_name" — строка, название блюда на русском;\n'
    '  "calories"  — целое число, ккал порции;\n'
    '  "proteins"  — число, белки в граммах;\n'
    '  "fats"      — число, жиры в граммах;\n'
    '  "carbs"     — число, углеводы в граммах.\n\n'
    "Правила:\n"
    "- Блюдо должно подходить под указанный приём пищи.\n"
    "- Если задана примерная калорийность — держись близко к ней.\n"
    "- Учитывай цель диеты и пожелания, если они указаны.\n"
    "- Предложи реальное, простое в приготовлении блюдо; числа положительные."
)

# Английский аналог REGENERATE_ITEM_SYSTEM_PROMPT (те же ключи, значения на английском).
REGENERATE_ITEM_SYSTEM_PROMPT_EN = (
    "You are a nutritionist and cook. The user wants to REPLACE one dish in their meal "
    "plan with an alternative for the specified meal.\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else) — ONE dish with the fields:\n"
    '  "dish_name" — string, the dish name in English;\n'
    '  "calories"  — integer, kcal of the portion;\n'
    '  "proteins"  — number, protein in grams;\n'
    '  "fats"      — number, fat in grams;\n'
    '  "carbs"     — number, carbohydrates in grams.\n\n'
    "Rules:\n"
    "- The dish must fit the specified meal.\n"
    "- If an approximate calorie value is given — stay close to it.\n"
    "- Consider the diet goal and preferences if provided.\n"
    "- Suggest a real, easy-to-cook dish; numbers must be positive."
)


def regenerate_meal_item(
    meal_type: str,
    around_calories: int | None = None,
    diet_goal: str | None = None,
    preferences: str | None = None,
    lang: str = "ru",
) -> dict:
    """
    Подбирает ОДНО альтернативное блюдо под приём пищи и примерную калорийность.

    Параметры:
        meal_type       — приём пищи (breakfast/lunch/dinner/snack);
        around_calories — желаемая примерная калорийность (необязательно);
        diet_goal       — цель диеты (необязательно);
        preferences     — пожелания (необязательно);
        lang            — язык значений ("ru"/"en").

    Возвращает словарь {dish_name, calories, proteins, fats, carbs}.

    При неудаче обращения к ИИ (или невалидном блюде) выбрасывает AIError.
    """
    lang = _normalize_lang(lang)

    # Нормализуем приём пищи; если неизвестен — не настаиваем на нём в промпте.
    mt = str(meal_type or "").strip().lower()
    mt = mt if mt in _PLAN_MEAL_TYPES else ""

    if lang == "en":
        parts = ["Suggest one alternative dish to replace a dish in the meal plan."]
        if mt:
            parts.append(f"Meal: {mt}.")
        if around_calories is not None:
            parts.append(f"Approximate calories: {_coerce_int(around_calories)} kcal.")
        if diet_goal and str(diet_goal).strip():
            parts.append(f"Diet goal: {str(diet_goal).strip()}.")
        if preferences and str(preferences).strip():
            parts.append(f"Preferences: {str(preferences).strip()}.")
        parts.append("Return the result strictly in JSON format following the instructions.")
    else:
        parts = ["Предложи одно альтернативное блюдо для замены в плане питания."]
        if mt:
            parts.append(f"Приём пищи: {mt}.")
        if around_calories is not None:
            parts.append(f"Примерная калорийность: {_coerce_int(around_calories)} ккал.")
        if diet_goal and str(diet_goal).strip():
            parts.append(f"Цель диеты: {str(diet_goal).strip()}.")
        if preferences and str(preferences).strip():
            parts.append(f"Пожелания: {str(preferences).strip()}.")
        parts.append("Верни результат строго в формате JSON по инструкции.")
    user_prompt = "\n".join(parts)

    system_prompt = _pick_prompt(
        REGENERATE_ITEM_SYSTEM_PROMPT, REGENERATE_ITEM_SYSTEM_PROMPT_EN, lang
    )
    data, _debug = _run_text_completion(
        system_prompt, user_prompt, log_tag="regenerate_item"
    )

    # Модель может вернуть блюдо как корень объекта или внутри ключа "dish".
    candidate = data
    if not (isinstance(data.get("dish_name"), str) and data.get("dish_name").strip()):
        nested = data.get("dish")
        if isinstance(nested, dict):
            candidate = nested

    dish = _normalize_plan_dish(candidate)
    if dish is None:
        raise AIError(
            "AI не вернул корректное блюдо для замены",
            raw=_debug.get("raw", ""),
            finish_reason=_debug.get("finish_reason"),
            refusal=_debug.get("refusal"),
        )

    return dish


# --------------------------------------------------------------------------- #
#  4) Умные предложения еды под остаток КБЖУ (с приёмом пищи / пожеланием)
# --------------------------------------------------------------------------- #

# Системный промпт умных предложений еды (RU).
SUGGEST_FOOD_SYSTEM_PROMPT = (
    "Ты — внимательный нутрициолог. Пользователь хочет добрать дневную норму и "
    "просит 2-3 умных варианта еды под ОСТАТОК калорий и БЖУ.\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него) с полем:\n"
    '  "suggestions" — массив из 2-3 объектов, каждый с полями:\n'
    '      "dish_name" — строка, название блюда на русском;\n'
    '      "calories"  — целое число, ккал порции;\n'
    '      "proteins"  — число, белки в граммах;\n'
    '      "fats"      — число, жиры в граммах;\n'
    '      "carbs"     — число, углеводы в граммах;\n'
    '      "reason"    — строка, почему это блюдо подходит '
    "(коротко, на русском).\n\n"
    "Правила:\n"
    "- Блюдо должно ВПИСЫВАТЬСЯ в остаток калорий и помогать добрать БЖУ "
    "(особенно белок).\n"
    "- Если задан приём пищи (meal_type) — предлагай еду именно под него.\n"
    "- Если есть пожелание пользователя (free_text) — учти его.\n"
    "- Учитывай цель диеты (loss/maintain/gain), если она указана.\n"
    "- Если остаток калорий маленький или отрицательный — предложи лёгкие "
    "низкокалорийные варианты.\n"
    "- Если сегодня была или будет тренировка (контекст тренировок) — предпочитай "
    "белковые варианты и учитывай сожжённые калории.\n"
    "- Числа — реалистичные и положительные."
)

# Английский аналог SUGGEST_FOOD_SYSTEM_PROMPT (те же ключи, значения на английском).
SUGGEST_FOOD_SYSTEM_PROMPT_EN = (
    "You are an attentive nutritionist. The user wants to top up their daily goal and "
    "asks for 2-3 smart food options for the REMAINING calories and macros.\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else) with the field:\n"
    '  "suggestions" — an array of 2-3 objects, each with the fields:\n'
    '      "dish_name" — string, the dish name in English;\n'
    '      "calories"  — integer, kcal of the portion;\n'
    '      "proteins"  — number, protein in grams;\n'
    '      "fats"      — number, fat in grams;\n'
    '      "carbs"     — number, carbohydrates in grams;\n'
    '      "reason"    — string, why this dish is a good fit '
    "(short, in English).\n\n"
    "Rules:\n"
    "- The dish must FIT within the remaining calories and help top up macros "
    "(especially protein).\n"
    "- If a meal is specified (meal_type) — suggest food exactly for it.\n"
    "- If there is a user wish (free_text) — take it into account.\n"
    "- Consider the diet goal (loss/maintain/gain) if provided.\n"
    "- If the remaining calories are small or negative — suggest light "
    "low-calorie options.\n"
    "- If there was or will be a workout today (training context) — prefer "
    "protein-rich options and account for the calories burned.\n"
    "- Numbers must be realistic and positive."
)


def _normalize_recommend_items(raw_items, debug: dict, empty_error: str) -> list[dict]:
    """
    Нормализует массив предложений еды в форму RecommendItem.

    Каждый элемент -> {dish_name, calories, proteins, fats, carbs, reason}.
    Пропускает мусор и элементы без названия. Если ни одного валидного варианта —
    выбрасывает AIError с переданным сообщением (502 на уровне роута).
    """
    if not isinstance(raw_items, list):
        raw_items = []

    suggestions: list[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        dish_name = item.get("dish_name")
        if not isinstance(dish_name, str) or not dish_name.strip():
            continue
        reason = item.get("reason")
        if not isinstance(reason, str):
            reason = ""
        suggestions.append(
            {
                "dish_name": dish_name.strip(),
                "calories": _coerce_int(item.get("calories")),
                "proteins": _coerce_float(item.get("proteins")),
                "fats": _coerce_float(item.get("fats")),
                "carbs": _coerce_float(item.get("carbs")),
                "reason": reason.strip(),
            }
        )

    if not suggestions:
        raise AIError(
            empty_error,
            raw=debug.get("raw", ""),
            finish_reason=debug.get("finish_reason"),
            refusal=debug.get("refusal"),
        )

    return suggestions


def suggest_food(
    meal_type: str | None = None,
    free_text: str | None = None,
    remaining_calories: int = 0,
    remaining_proteins: float = 0.0,
    remaining_fats: float = 0.0,
    remaining_carbs: float = 0.0,
    diet_goal: str | None = None,
    lang: str = "ru",
    training_context: str | None = None,
) -> dict:
    """
    Умные предложения еды (2-3 варианта) под остаток КБЖУ на день.

    Параметры:
        meal_type          — приём пищи (если задан, еда подбирается под него);
        free_text          — свободное пожелание пользователя (необязательно);
        remaining_calories — остаток калорий, ккал;
        remaining_proteins/fats/carbs — остаток БЖУ, г;
        diet_goal          — цель диеты (необязательно);
        lang               — язык значений ("ru"/"en");
        training_context   — строка о тренировке дня от AI-тренера (необязательно).

    Возвращает словарь:
        {"suggestions": [
            {dish_name, calories, proteins, fats, carbs, reason}, ...]}

    При неудаче обращения к ИИ (или пустом результате) выбрасывает AIError.
    """
    lang = _normalize_lang(lang)

    mt = str(meal_type or "").strip().lower()
    mt = mt if mt in _PLAN_MEAL_TYPES else ""

    if lang == "en":
        parts = ["Suggest 2-3 smart food options for the remaining daily calories/macros."]
        parts.append(f"Calories remaining: {_coerce_int(remaining_calories)} kcal.")
        parts.append(f"Protein remaining: {_coerce_float(remaining_proteins)} g.")
        parts.append(f"Fat remaining: {_coerce_float(remaining_fats)} g.")
        parts.append(f"Carbs remaining: {_coerce_float(remaining_carbs)} g.")
        if mt:
            parts.append(f"Meal: {mt}.")
        if free_text and str(free_text).strip():
            parts.append(f"User wish: {str(free_text).strip()}.")
        if diet_goal and str(diet_goal).strip():
            parts.append(f"Diet goal: {str(diet_goal).strip()}.")
        if training_context and str(training_context).strip():
            parts.append(f"Training context: {str(training_context).strip()}")
        parts.append("Return the result strictly in JSON format following the instructions.")
    else:
        parts = ["Подбери 2-3 умных варианта еды под оставшиеся калории и БЖУ."]
        parts.append(f"Осталось калорий: {_coerce_int(remaining_calories)} ккал.")
        parts.append(f"Осталось белков: {_coerce_float(remaining_proteins)} г.")
        parts.append(f"Осталось жиров: {_coerce_float(remaining_fats)} г.")
        parts.append(f"Осталось углеводов: {_coerce_float(remaining_carbs)} г.")
        if mt:
            parts.append(f"Приём пищи: {mt}.")
        if free_text and str(free_text).strip():
            parts.append(f"Пожелание пользователя: {str(free_text).strip()}.")
        if diet_goal and str(diet_goal).strip():
            parts.append(f"Цель диеты: {str(diet_goal).strip()}.")
        if training_context and str(training_context).strip():
            parts.append(f"Контекст тренировок: {str(training_context).strip()}")
        parts.append("Верни результат строго в формате JSON по инструкции.")
    user_prompt = "\n".join(parts)

    system_prompt = _pick_prompt(
        SUGGEST_FOOD_SYSTEM_PROMPT, SUGGEST_FOOD_SYSTEM_PROMPT_EN, lang
    )
    data, _debug = _run_text_completion(
        system_prompt, user_prompt, log_tag="suggest_food"
    )

    suggestions = _normalize_recommend_items(
        data.get("suggestions"),
        _debug,
        "AI не вернул ни одного корректного варианта еды",
    )
    return {"suggestions": suggestions}


# --------------------------------------------------------------------------- #
#  5) Здоровые перекусы-«вкусняшки» под остаток калорий
# --------------------------------------------------------------------------- #

# Системный промпт здоровых перекусов (RU).
HEALTHY_SNACKS_SYSTEM_PROMPT = (
    "Ты — нутрициолог. Пользователь хочет «вкусняшку», которую можно съесть и не "
    "поправиться. Подбери 3-4 НИЗКОКАЛОРИЙНЫХ перекуса, влезающих в остаток калорий "
    "на сегодня.\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него) с полем:\n"
    '  "suggestions" — массив из 3-4 объектов, каждый с полями:\n'
    '      "dish_name" — строка, название перекуса на русском;\n'
    '      "calories"  — целое число, ккал порции (НИЗКОЕ);\n'
    '      "proteins"  — число, белки в граммах;\n'
    '      "fats"      — число, жиры в граммах;\n'
    '      "carbs"     — число, углеводы в граммах;\n'
    '      "reason"    — строка, почему это вкусно и не навредит фигуре '
    "(коротко, на русском).\n\n"
    "Правила:\n"
    "- Это должны быть именно вкусные перекусы-«вкусняшки», а не полноценные блюда.\n"
    "- Калорийность каждого — НИЗКАЯ и вписывается в остаток калорий.\n"
    "- Отдавай предпочтение белку/клетчатке и сытным низкокалорийным вариантам.\n"
    "- Числа — реалистичные и положительные."
)

# Английский аналог HEALTHY_SNACKS_SYSTEM_PROMPT (те же ключи, значения на английском).
HEALTHY_SNACKS_SYSTEM_PROMPT_EN = (
    "You are a nutritionist. The user wants a «treat» they can eat without gaining "
    "weight. Suggest 3-4 LOW-CALORIE snacks that fit within today's remaining "
    "calories.\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else) with the field:\n"
    '  "suggestions" — an array of 3-4 objects, each with the fields:\n'
    '      "dish_name" — string, the snack name in English;\n'
    '      "calories"  — integer, kcal of the portion (LOW);\n'
    '      "proteins"  — number, protein in grams;\n'
    '      "fats"      — number, fat in grams;\n'
    '      "carbs"     — number, carbohydrates in grams;\n'
    '      "reason"    — string, why it is tasty and won\'t hurt the figure '
    "(short, in English).\n\n"
    "Rules:\n"
    "- These must be tasty «treat»-style snacks, not full meals.\n"
    "- Each one's calories must be LOW and fit within the remaining calories.\n"
    "- Prefer protein/fiber and filling low-calorie options.\n"
    "- Numbers must be realistic and positive."
)


def healthy_snacks(remaining_calories: int, lang: str = "ru") -> dict:
    """
    Подбирает 3-4 низкокалорийных перекуса-«вкусняшки» под остаток калорий.

    Параметры:
        remaining_calories — остаток калорий на сегодня, ккал;
        lang               — язык значений ("ru"/"en").

    Возвращает словарь:
        {"suggestions": [
            {dish_name, calories, proteins, fats, carbs, reason}, ...]}

    При неудаче обращения к ИИ (или пустом результате) выбрасывает AIError.
    """
    lang = _normalize_lang(lang)
    remaining = _coerce_int(remaining_calories)

    if lang == "en":
        user_prompt = (
            "Suggest 3-4 low-calorie tasty snacks.\n"
            f"Calories remaining today: {remaining} kcal.\n"
            "Return the result strictly in JSON format following the instructions."
        )
    else:
        user_prompt = (
            "Подбери 3-4 низкокалорийных вкусных перекуса.\n"
            f"Осталось калорий на сегодня: {remaining} ккал.\n"
            "Верни результат строго в формате JSON по инструкции."
        )

    system_prompt = _pick_prompt(
        HEALTHY_SNACKS_SYSTEM_PROMPT, HEALTHY_SNACKS_SYSTEM_PROMPT_EN, lang
    )
    data, _debug = _run_text_completion(
        system_prompt, user_prompt, log_tag="healthy_snacks"
    )

    suggestions = _normalize_recommend_items(
        data.get("suggestions"),
        _debug,
        "AI не вернул ни одного корректного перекуса",
    )
    return {"suggestions": suggestions}


# --------------------------------------------------------------------------- #
#  Быстрый расчёт КБЖУ одного продукта по названию + количеству + единице
# --------------------------------------------------------------------------- #
#
# Используется свободным ручным вводом в дневнике (POST /food/calculate):
# пользователь пишет название продукта, (опционально) количество и единицу,
# а модель считает ИТОГОВЫЕ калории и БЖУ на всё это количество.

# Системный промпт расчёта КБЖУ одного продукта (RU).
CALCULATE_FOOD_SYSTEM_PROMPT = (
    "Ты — внимательный нутрициолог. Пользователь называет ОДИН продукт/блюдо и "
    "(возможно) его количество и единицу измерения. Оцени ИТОГОВЫЕ калории и БЖУ "
    "на ВСЁ это количество (а НЕ на единицу).\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него) с полями:\n"
    '  "dish_name" — строка, очищенное название продукта на русском;\n'
    '  "quantity"  — число, количество/вес, для которого посчитаны калории;\n'
    '  "unit"      — "pcs"|"g"|"ml"|"serving" — единица измерения;\n'
    '  "calories"  — целое число, ИТОГО ккал за это количество;\n'
    '  "proteins"  — число, белки в граммах (итого);\n'
    '  "fats"      — число, жиры в граммах (итого);\n'
    '  "carbs"     — число, углеводы в граммах (итого).\n\n'
    "Правила:\n"
    '- "unit" — СТРОГО один из ключей: "pcs" (штучное: яйца, бананы, котлеты), '
    '"g" (весовое: хлеб, рис, мясо), "ml" (жидкое: молоко, сок), '
    '"serving" (порция — если ни то, ни другое).\n'
    "- calories и БЖУ — это СУММА (ИТОГО) для указанного quantity, а НЕ на единицу.\n"
    "- Если количество и/или единица НЕ заданы — предположи разумную порцию по умолчанию "
    "(1 шт/порция для штучного, либо типичные ~100 г для весового) и ВЕРНИ выбранные "
    "quantity и unit.\n"
    "- Оценивай реалистично; для настоящей еды калории и БЖУ должны быть больше нуля.\n"
    "- dish_name — на русском языке."
)

# Английский аналог CALCULATE_FOOD_SYSTEM_PROMPT (те же ключи, значения на английском).
CALCULATE_FOOD_SYSTEM_PROMPT_EN = (
    "You are an attentive nutritionist. The user names ONE product/dish and "
    "(optionally) its quantity and unit of measure. Estimate the TOTAL calories and "
    "macros for the WHOLE quantity (NOT per unit).\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else) with the fields:\n"
    '  "dish_name" — string, the cleaned product name in English;\n'
    '  "quantity"  — number, the quantity/weight the calories are computed for;\n'
    '  "unit"      — "pcs"|"g"|"ml"|"serving" — the unit of measure;\n'
    '  "calories"  — integer, the TOTAL kcal for this quantity;\n'
    '  "proteins"  — number, protein in grams (total);\n'
    '  "fats"      — number, fat in grams (total);\n'
    '  "carbs"     — number, carbohydrates in grams (total).\n\n'
    "Rules:\n"
    '- "unit" must be STRICTLY one of the keys: "pcs" (countable: eggs, bananas, cutlets), '
    '"g" (by weight: bread, rice, meat), "ml" (liquid: milk, juice), '
    '"serving" (a portion — if neither of the above).\n'
    "- calories and macros are the TOTAL for the given quantity, NOT per unit.\n"
    "- If the quantity and/or unit are NOT given — assume a sensible default portion "
    "(1 pcs/serving for countable items, or a typical ~100 g by weight) and RETURN the "
    "chosen quantity and unit.\n"
    "- Estimate realistically; for real food calories and macros must be greater than zero.\n"
    "- dish_name must be in English."
)


def calculate_food(
    name: str,
    quantity: float | None = None,
    unit: str | None = None,
    lang: str = "ru",
) -> dict:
    """
    Считает ИТОГОВЫЕ калории и БЖУ одного продукта по названию + количеству + единице.

    Параметры:
        name     — название продукта/блюда (обязательно);
        quantity — количество/вес (число) или None (модель предположит сама);
        unit     — канонический ключ единицы (pcs|g|ml|serving) или None;
        lang     — язык значения dish_name ("ru"/"en").

    Возвращает словарь:
        {"dish_name": str, "quantity": float|None, "unit": str|None,
         "calories": int, "proteins": float, "fats": float, "carbs": float}
    где калории и БЖУ — это ИТОГО за указанное (или предположённое) количество.

    Если quantity/unit не заданы — модель выбирает разумную порцию по умолчанию и
    возвращает её. При неудаче обращения к ИИ (или бесполезном ответе) выбрасывает
    AIError (502 на уровне роута), как recommend_meals / parse_food_text.
    """
    lang = _normalize_lang(lang)
    if not name or not str(name).strip():
        raise AIError("Пустое название продукта для расчёта")

    # Нормализуем входные количество/единицу (единицу — к каноническому ключу).
    in_name = str(name).strip()
    in_quantity = _coerce_float(quantity) if quantity is not None else None
    in_unit = _normalize_unit(unit)

    # Формируем запрос пользователя из известных данных — на нужном языке.
    if lang == "en":
        parts = [f"Product: {in_name}."]
        if in_quantity is not None:
            parts.append(f"Quantity: {in_quantity}.")
        if in_unit:
            parts.append(f"Unit: {in_unit}.")
        if in_quantity is None and not in_unit:
            parts.append("Quantity and unit are not specified — assume a sensible default portion.")
        parts.append("Return the result strictly in JSON format following the instructions.")
    else:
        parts = [f"Продукт: {in_name}."]
        if in_quantity is not None:
            parts.append(f"Количество: {in_quantity}.")
        if in_unit:
            parts.append(f"Единица: {in_unit}.")
        if in_quantity is None and not in_unit:
            parts.append("Количество и единица не указаны — предположи разумную порцию по умолчанию.")
        parts.append("Верни результат строго в формате JSON по инструкции.")
    user_prompt = "\n".join(parts)

    system_prompt = _pick_prompt(
        CALCULATE_FOOD_SYSTEM_PROMPT, CALCULATE_FOOD_SYSTEM_PROMPT_EN, lang
    )
    data, _debug = _run_text_completion(
        system_prompt, user_prompt, log_tag="calculate_food"
    )

    # dish_name: очищенная строка, при отсутствии — исходное название.
    dish_name = data.get("dish_name")
    if not isinstance(dish_name, str) or not dish_name.strip():
        dish_name = in_name
    else:
        dish_name = dish_name.strip()

    # Количество: из ответа, иначе исходное; единица: канонический ключ, иначе исходная.
    out_quantity = data.get("quantity")
    out_quantity = _coerce_float(out_quantity) if out_quantity is not None else in_quantity
    out_unit = _normalize_unit(data.get("unit")) or in_unit

    calories = _coerce_int(data.get("calories"))

    # Если модель вернула валидный JSON, но по сути пустой (нет названия и 0 ккал) —
    # считаем это неудачей разбора (роут отдаст 502), как в других функциях.
    if (not dish_name) and calories <= 0:
        raise AIError(
            "AI не вернул пригодной оценки продукта",
            raw=_debug.get("raw", ""),
            finish_reason=_debug.get("finish_reason"),
            refusal=_debug.get("refusal"),
        )

    return {
        "dish_name": dish_name,
        "quantity": out_quantity,
        "unit": out_unit,
        "calories": calories,
        "proteins": _coerce_float(data.get("proteins")),
        "fats": _coerce_float(data.get("fats")),
        "carbs": _coerce_float(data.get("carbs")),
    }
