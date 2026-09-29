"""Поиск продуктов с КБЖУ во внешней базе Open Food Facts.

ЗАЧЕМ: пользователь вводит название блюда и сразу видит варианты с готовыми
КБЖУ — не нужно вбивать цифры руками или тратить AI-расчёт.

ПОЧЕМУ ИМЕННО ЭТОТ ЭНДПОИНТ: у Open Food Facts обычный /api/v2/search НЕ умеет
полнотекстовый поиск по названию (параметр product_name молча игнорируется и
возвращается вся база). Полнотекст живёт в отдельном сервисе Search-a-licious:
    https://search.openfoodfacts.org/search?q=...

ЖЁСТКИЕ ОГРАНИЧЕНИЯ, вокруг которых построен модуль:
  * лимит 10 запросов в минуту НА IP — то есть на ВЕСЬ наш сервер, а не на
    пользователя. Поэтому здесь есть (1) кэш ответов и (2) глобальный
    ограничитель исходящих запросов. Без них при нескольких активных
    пользователях внешний сервис забанит нас за минуту;
  * обязателен собственный User-Agent вида "Имя/Версия (контакт)";
  * энергия часто приходит ТОЛЬКО в килоджоулях — нужен пересчёт в ккал;
  * данные пользовательские: много записей без названия и без нутриентов —
    их отбрасываем.

Модуль полностью fail-safe: любая ошибка сети/лимита возвращает пустой список,
а не исключение. Не нашли — пользователь просто вводит блюдо вручную.
"""

from __future__ import annotations

import logging
import os
import threading
import time

try:
    import httpx
except Exception:  # pragma: no cover — приложение не должно падать без httpx
    httpx = None

logger = logging.getLogger("food_search")

# Адрес полнотекстового поиска (Search-a-licious).
SEARCH_URL = os.getenv("OFF_SEARCH_URL", "https://search.openfoodfacts.org/search")

# User-Agent обязателен по правилам Open Food Facts.
USER_AGENT = os.getenv(
    "OFF_USER_AGENT", "FitnessUp/1.0 (https://t.me/fitness_up_bot)"
)

# Таймаут внешнего запроса: лучше быстро отдать пустой список, чем держать юзера.
TIMEOUT_SEC = float(os.getenv("OFF_TIMEOUT", "6"))

# Сколько запросов в минуту нам разрешено суммарно (официальный лимит — 10).
# Держим запас, чтобы случайные всплески не приводили к бану.
MAX_REQUESTS_PER_MIN = int(os.getenv("OFF_MAX_RPM", "8"))

# Сколько держим ответ в кэше (секунды). КБЖУ продуктов меняются крайне редко,
# поэтому кэш можно держать долго — он и снимает основную нагрузку с лимита.
CACHE_TTL_SEC = int(os.getenv("OFF_CACHE_TTL", str(24 * 3600)))

# Максимальный размер кэша (чтобы память не росла бесконечно).
CACHE_MAX_ITEMS = int(os.getenv("OFF_CACHE_MAX", "500"))

# Поля запрашиваем явно: иначе в ответе прилетают сотни килобайт лишних данных.
_FIELDS = ",".join([
    "code",
    "product_name",
    "brands",
    "nutriments",
    "countries_tags",
    "completeness",
])

# Коэффициент перевода килоджоулей в килокалории.
_KJ_TO_KCAL = 4.184

# --- Кэш и ограничитель (общие на процесс, защищены одним замком) ----------- #
_lock = threading.Lock()
_cache: dict[str, tuple[float, list]] = {}   # ключ -> (время_истечения, результат)
_request_times: list[float] = []             # отметки времени исходящих запросов


def _cache_get(key: str):
    """Достать результат из кэша, если он ещё не протух."""
    with _lock:
        item = _cache.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time.time():
            _cache.pop(key, None)
            return None
        return value


def _cache_put(key: str, value: list) -> None:
    """Положить результат в кэш, вытеснив самые старые записи при переполнении."""
    with _lock:
        if len(_cache) >= CACHE_MAX_ITEMS:
            # Простое вытеснение: убираем записи с ближайшим сроком истечения.
            for old_key in sorted(_cache, key=lambda k: _cache[k][0])[: CACHE_MAX_ITEMS // 4]:
                _cache.pop(old_key, None)
        _cache[key] = (time.time() + CACHE_TTL_SEC, value)


def _allow_request() -> bool:
    """Разрешён ли ещё один исходящий запрос в текущем минутном окне."""
    now = time.time()
    with _lock:
        # Оставляем только отметки за последние 60 секунд.
        while _request_times and now - _request_times[0] > 60:
            _request_times.pop(0)
        if len(_request_times) >= MAX_REQUESTS_PER_MIN:
            return False
        _request_times.append(now)
        return True


def _kcal_from(nutriments: dict) -> float | None:
    """Извлечь калорийность на 100 г/мл.

    У Open Food Facts энергия часто есть ТОЛЬКО в килоджоулях: поля energy_100g
    и energy-kj_100g всегда в кДж (это прямо указано в описании полей базы).
    Поэтому порядок: готовые ккал -> кДж/4.184.
    """
    def num(key):
        value = nutriments.get(key)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    kcal = num("energy-kcal_100g")
    if kcal is not None:
        return kcal
    for kj_key in ("energy-kj_100g", "energy_100g"):
        kj = num(kj_key)
        if kj is not None:
            return kj / _KJ_TO_KCAL
    return None


def _macro(nutriments: dict, key: str) -> float:
    """Значение макроса на 100 г (0.0, если отсутствует или мусор)."""
    try:
        value = float(nutriments.get(key))
    except (TypeError, ValueError):
        return 0.0
    return round(value, 1) if value >= 0 else 0.0


def _normalize_hit(hit: dict) -> dict | None:
    """Превратить запись Open Food Facts в наш компактный формат.

    Возвращает None, если запись бесполезна (нет названия или нет калорий) —
    в этой базе таких много, показывать их пользователю нельзя.
    """
    if not isinstance(hit, dict):
        return None

    name = hit.get("product_name")
    if not isinstance(name, str) or not name.strip():
        return None
    name = " ".join(name.split())[:120]

    nutriments = hit.get("nutriments")
    if not isinstance(nutriments, dict):
        return None

    kcal = _kcal_from(nutriments)
    if kcal is None or kcal <= 0 or kcal > 1000:
        # Больше 1000 ккал/100 г физически невозможно — значит данные битые.
        return None

    # Бренд приходит то строкой, то массивом — приводим к строке.
    brands = hit.get("brands")
    if isinstance(brands, list):
        brand = ", ".join(str(b) for b in brands if b)[:60]
    elif isinstance(brands, str):
        brand = brands.strip()[:60]
    else:
        brand = ""

    return {
        "code": str(hit.get("code") or "")[:32],
        "name": name,
        "brand": brand,
        # КБЖУ — на 100 г / 100 мл продукта.
        "calories": int(round(kcal)),
        "proteins": _macro(nutriments, "proteins_100g"),
        "fats": _macro(nutriments, "fat_100g"),
        "carbs": _macro(nutriments, "carbohydrates_100g"),
    }


def search_products(query: str, lang: str = "ru", limit: int = 12) -> list:
    """Найти продукты по названию. Возвращает список словарей (может быть пустым).

    Никогда не бросает исключений: при любой проблеме (нет сети, превышен лимит,
    некорректный ответ) отдаёт пустой список — фронт покажет ручной ввод.
    """
    q = " ".join((query or "").split())
    if len(q) < 2:
        return []

    lang = "en" if str(lang).lower().startswith("en") else "ru"
    limit = max(1, min(int(limit or 12), 25))
    cache_key = f"{lang}:{limit}:{q.lower()}"

    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    if httpx is None:
        logger.warning("food_search: httpx недоступен")
        return []

    # Глобальный лимит: внешний сервис считает запросы по IP всего сервера.
    if not _allow_request():
        logger.info("food_search: превышен лимит исходящих запросов, пропускаем %r", q)
        return []

    params = {
        "q": q,
        # Язык влияет на релевантность; английский оставляем как запасной.
        "langs": f"{lang},en" if lang != "en" else "en",
        "fields": _FIELDS,
        # Берём с запасом: часть записей отсеется как непригодная.
        "page_size": str(min(limit * 3, 50)),
    }

    try:
        response = httpx.get(
            SEARCH_URL,
            params=params,
            timeout=TIMEOUT_SEC,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        if response.status_code != 200:
            logger.warning("food_search: HTTP %s для %r", response.status_code, q)
            return []
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 — поиск не должен ломать приложение
        logger.warning("food_search: сбой запроса (%s) для %r", exc, q)
        return []

    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, list):
        return []

    items: list = []
    seen: set = set()
    for hit in hits:
        item = _normalize_hit(hit)
        if item is None:
            continue
        # Дедупликация по названию+бренду: в базе много почти одинаковых записей.
        key = (item["name"].lower(), item["brand"].lower())
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
        if len(items) >= limit:
            break

    _cache_put(cache_key, items)
    return items
