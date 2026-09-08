"""
ИИ-функции раздела «AI-тренер»: промпты (RU/EN) и нормализация ответов модели.

Модуль намеренно «чистый»: не ходит в БД и не импортирует модели/seed —
каталог упражнений приходит ПАРАМЕТРОМ (список dict, dict slug→dict, ORM-объекты
или уже отформатированная строка каталога «slug | name_en | muscle | equipment |
measure | difficulty»). Благодаря этому модуль тестируется независимо, а
параллельные этапы стыкуются только по контракту ТЗ §5.

Общий движок — ai_service._run_text_completion (вызывается через модуль, чтобы
mock.patch.object(ai_service, "_run_text_completion", ...) в тестах подменял и
наши вызовы). При сбое ИИ или пустом результате — AIError (маршрут превращает
в 502). Мусор в ответе модели не роняет: значения приводятся к типам и
обрезаются по диапазонам, незнакомые slug — фаззи-матч по slug/имени, иначе
отбрасываются (список отброшенных возвращается в поле "unmatched").

Публичные функции:
    generate_program(profile, body, catalog, lang="ru", catalog_map=None) -> dict
        Шаблон недели + периодизация + советы (tag trainer_program, 4000 токенов).
    exercise_technique(exercise, limitations=None, lang="ru") -> dict
        Техника упражнения (tag trainer_technique, 700 токенов). Ограничения
        пользователя в промпт НЕ попадают — кэш техники общий на упражнение и язык.
    weekly_review(stats, lang="ru", catalog=None) -> dict
        Недельный разбор с правками (tag trainer_review, 1500 токенов).
    nutrition_day_tip(ctx, lang="ru") -> dict
        Совет дня по питанию (tag trainer_nutrition, 500 токенов).
    normalize_program / normalize_review / normalize_technique / normalize_tip
        Чистые нормализаторы (без ИИ) — используются функциями выше и тестами.

Форматы значений в изменениях недельного разбора (для apply_review_changes):
    weight_pct        — дельта рабочего веса в процентах, [-15, +10];
    sets              — дельта подходов, -1 или +1;
    rest_sec          — НОВОЕ время отдыха в секундах (абсолют), [20, 300];
    swap              — замена exercise_slug → new_slug (та же группа мышц);
    deload_next_week  — value=1, без упражнения.
    id изменения = его индекс в списке changes (0-based).
"""

import difflib
import json
import logging
import re

from backend import ai_service
from backend.ai_service import (
    AIError,
    _coerce_float,
    _coerce_int,
    _normalize_lang,
    _pick_prompt,
)

logger = logging.getLogger("trainer_ai")

# --------------------------------------------------------------------------- #
#  Справочники, лимиты и теги телеметрии
# --------------------------------------------------------------------------- #
MUSCLE_GROUPS = (
    "chest", "back", "shoulders", "biceps", "triceps", "quads", "hamstrings",
    "glutes", "calves", "core", "full_body", "cardio", "mobility",
)
MEASURE_TYPES = ("reps_weight", "reps", "time", "distance")
SESSION_TYPES = ("strength", "cardio", "mixed", "mobility")
SPLIT_TYPES = ("full_body", "upper_lower", "ppl", "custom")
PHASES = ("base", "build", "peak", "deload")
# Подписи фаз (ru, en) — маршрут подставляет label по языку пользователя.
PHASE_LABELS = {
    "base": ("База", "Base"),
    "build": ("Рост", "Build"),
    "peak": ("Пик", "Peak"),
    "deload": ("Разгрузка", "Deload"),
}
REVIEW_CHANGE_TYPES = ("weight_pct", "sets", "swap", "rest_sec", "deload_next_week")

# Slug дефолтной разминки/заминки (обязательны в seed) — инжектируются, если
# модель не вернула блок.
DEFAULT_WARMUP_SLUG = "warmup_general_5min"
DEFAULT_COOLDOWN_SLUG = "stretch_full_body_5min"
DEFAULT_WARMUP_SEC = 300
DEFAULT_COOLDOWN_SEC = 300

# Диапазоны значений в плане (clamp при нормализации).
SETS_RANGE = (1, 6)
REPS_RANGE = (1, 30)
REST_RANGE = (20, 300)
WEIGHT_RANGE = (0.0, 300.0)
TIME_RANGE = (5, 3600)
DURATION_RANGE = (10, 180)
RPE_RANGE = (5, 10)
WEIGHT_PCT_RANGE = (-15, 10)
MAX_MAIN_EXERCISES = 10
MAX_AUX_ITEMS = 4
MAX_REVIEW_CHANGES = 5
MAX_CATALOG_LINES = 110
MAX_STATS_CHARS = 9000

# Теги телеметрии (_log_usage) и лимиты токенов ответа.
TAG_PROGRAM = "trainer_program"
TAG_TECHNIQUE = "trainer_technique"
TAG_REVIEW = "trainer_review"
TAG_NUTRITION = "trainer_nutrition"
MAX_TOKENS_PROGRAM = 4000
MAX_TOKENS_TECHNIQUE = 700
MAX_TOKENS_REVIEW = 1500
MAX_TOKENS_NUTRITION = 500

# Ключи stats, в которых collect_week_stats может передать каталог для swap.
_STATS_CATALOG_KEYS = ("catalog", "allowed_slugs", "swap_catalog", "allowed_swaps", "exercises_catalog")

# --------------------------------------------------------------------------- #
#  Человекочитаемые названия кодов для промптов (ru, en)
# --------------------------------------------------------------------------- #
_GOAL_NAMES = {
    "loss": ("похудеть", "fat loss"),
    "muscle": ("набрать мышцы", "muscle gain"),
    "strength": ("стать сильнее", "strength"),
    "endurance": ("выносливость", "endurance"),
    "tone": ("тонус и здоровье", "tone and general health"),
}
_LEVEL_NAMES = {
    "beginner": ("новичок, меньше 6 месяцев", "beginner, under 6 months"),
    "intermediate": ("средний, 6 мес.–2 года регулярно", "intermediate, 6 months–2 years"),
    "advanced": ("опытный, 2+ года", "advanced, 2+ years"),
}
_EQUIPMENT_NAMES = {
    "gym": ("зал: штанги, гантели, тренажёры", "gym: barbells, dumbbells, machines"),
    "home_dumbbells": ("дома с гантелями/резинками", "home with dumbbells/bands"),
    "bodyweight": ("только вес тела", "bodyweight only"),
}
_EXTRA_NAMES = {
    "pullup_bar": ("турник", "pull-up bar"),
    "bands": ("резинки", "bands"),
    "bench": ("скамья", "bench"),
    "kettlebell": ("гиря", "kettlebell"),
    "barbell": ("штанга", "barbell"),
    "cardio_machine": ("кардиотренажёр", "cardio machine"),
}
_LIMITATION_NAMES = {
    "knee": ("колени", "knees"),
    "lower_back": ("поясница", "lower back"),
    "shoulder": ("плечи", "shoulders"),
    "wrist": ("запястья/локти", "wrists/elbows"),
    "neck": ("шея", "neck"),
    "hip": ("тазобедренные суставы", "hips"),
    "pregnancy": ("беременность/после родов", "pregnancy/postpartum"),
    "heart_bp": ("давление/сердце", "blood pressure/heart"),
    "none": ("нет", "none"),
}
_FOCUS_NAMES = {
    "glutes": ("ягодицы", "glutes"),
    "core": ("кор", "core"),
    "back": ("спина", "back"),
    "chest": ("грудь", "chest"),
    "shoulders": ("плечи", "shoulders"),
    "arms": ("руки", "arms"),
    "legs": ("ноги", "legs"),
    "none": ("нет", "none"),
}
_WEEKDAY_NAMES = (
    ("Пн", "Mon"), ("Вт", "Tue"), ("Ср", "Wed"), ("Чт", "Thu"),
    ("Пт", "Fri"), ("Сб", "Sat"), ("Вс", "Sun"),
)
_GENDER_NAMES = {
    "male": ("мужской", "male"), "m": ("мужской", "male"),
    "female": ("женский", "female"), "f": ("женский", "female"),
}
_DIET_GOAL_NAMES = {
    "loss": ("снижение веса", "weight loss"),
    "maintain": ("поддержание", "maintenance"),
    "gain": ("набор", "weight gain"),
}
_SPLIT_TITLES = {
    "full_body": ("Всё тело", "Full body"),
    "upper_lower": ("Верх/Низ", "Upper/Lower"),
    "ppl": ("Тяни/Толкай/Ноги", "Push/Pull/Legs"),
    "custom": ("Программа", "Program"),
}

# --------------------------------------------------------------------------- #
#  Общие правила безопасности (входят во все system-промпты)
# --------------------------------------------------------------------------- #
SAFETY_RULES_RU = (
    "ПРАВИЛА БЕЗОПАСНОСТИ (обязательны):\n"
    "- Ты тренер, а не врач: НЕ ставишь диагнозы, НЕ назначаешь лекарства и добавки.\n"
    "- При боли — прекратить упражнение и обратиться к врачу.\n"
    "- При беременности, повышенном давлении или проблемах с сердцем — низкая "
    "интенсивность, без задержки дыхания и натуживания, без упражнений лёжа на спине "
    "после 1 триместра; рекомендуй согласовать нагрузку с врачом.\n"
    "- Не запугивай и не давай медицинских советов вне рамок техники и нагрузки."
)
SAFETY_RULES_EN = (
    "SAFETY RULES (mandatory):\n"
    "- You are a coach, not a doctor: you do NOT diagnose and do NOT prescribe "
    "medication or supplements.\n"
    "- If there is pain — stop the exercise and see a doctor.\n"
    "- Pregnancy, high blood pressure or heart issues — low intensity, no breath "
    "holding or straining, no supine exercises after the 1st trimester; recommend "
    "clearing the load with a doctor.\n"
    "- Do not scare the user and give no medical advice beyond technique and loading."
)

# --------------------------------------------------------------------------- #
#  §5.1 Генерация программы — system-промпты
# --------------------------------------------------------------------------- #
PROGRAM_SYSTEM_PROMPT = (
    "Ты — опытный персональный тренер. Составь персональную программу тренировок: "
    "ШАБЛОН ОДНОЙ НЕДЕЛИ и план периодизации на всю программу. Бэкенд сам раскроет "
    "шаблон по неделям — не описывай недели по отдельности.\n\n"
    + SAFETY_RULES_RU + "\n\n"
    "КАТАЛОГ: пользователь передаёт список доступных упражнений в формате "
    "«slug | name_en | muscle | equipment | measure | difficulty». Используй ТОЛЬКО slug "
    "из этого списка — ни одного выдуманного или изменённого slug. Если нужного "
    "упражнения нет — возьми ближайшее по мышце из каталога.\n\n"
    "ПРАВИЛА ПРОГРАММЫ:\n"
    "- Ровно столько дней, сколько тренировок в неделю (day_index с 1). Сплит по числу "
    "дней: не более 3 — full_body, 4 — upper_lower, 5–6 — ppl.\n"
    "- Основных упражнений в день по длительности сессии: 20 мин → 3–4, 30 → 4–5, "
    "45 → 5–6, 60 и больше → 6–8. Базовые (многосуставные) раньше изолирующих.\n"
    "- Баланс тяни/толкай и верх/низ в рамках недели; недельных рабочих сетов на группу "
    "мышц: 8–12 для новичка, 10–20 для остальных.\n"
    "- Диапазоны повторов и отдых по цели: strength — 3–6 повт., отдых 120–180 с; "
    "muscle — 6–12 повт., 60–120 с; loss и tone — 10–15 повт., 45–60 с плюс один "
    "кардио-блок; endurance — 12–20 повт., 30–45 с плюс кардио-интервалы.\n"
    "- Всегда разминка 5–8 мин (warmup_general_5min плюс 1–2 специфичных движения) и "
    "заминка 3–5 мин (растяжка рабочих мышц).\n"
    "- Периодизация по неделям: фазы base / build / peak / deload; каждая 4-я неделя и "
    "последняя — deload (weight_pct 85, sets_delta -1).\n"
    "- start_weight_kg — консервативно по уровню, полу и весу тела; для упражнений с "
    "весом тела и measure reps/time — null. Если известны рабочие веса — отталкивайся "
    "от них. Для measure time указывай time_sec вместо повторов.\n"
    "- Ограничения: колени — без глубоких приседов и прыжковых выпадов; поясница — без "
    "становой тяги с пола и наклонов со штангой; плечи — без жима из-за головы и "
    "глубоких отжиманий на брусьях; запястья — без опоры на кисти под нагрузкой; шея — "
    "без нагрузки на шею и рывков; беременность/давление/сердце — см. правила "
    "безопасности.\n"
    "- Нельзя ставить два тяжёлых упражнения на поясницу (становая, наклоны, "
    "гиперэкстензия с весом) в один день.\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него):\n"
    '{"title": "Верх/Низ — 6 недель", "split_type": "full_body|upper_lower|ppl|custom", '
    '"summary": "почему так, 2-3 предложения", '
    '"week_template": {"days": [{"day_index": 1, "title": "Верх тела", '
    '"session_type": "strength|cardio|mixed|mobility", "focus_muscles": ["chest", "back"], '
    '"duration_min": 45, '
    '"warmup": [{"slug": "warmup_general_5min", "time_sec": 300, "note": "..."}, '
    '{"slug": "arm_circles", "sets": 1, "reps": 15}], '
    '"exercises": [{"slug": "db_bench_press", "sets": 3, "reps_min": 8, "reps_max": 12, '
    '"rest_sec": 90, "start_weight_kg": 14, "rpe": 7, "tempo": "2-0-2", '
    '"note": "локти под 45°"}], '
    '"cooldown": [{"slug": "chest_stretch", "time_sec": 30}]}]}, '
    '"periodization": [{"week": 1, "phase": "base", "weight_pct": 100, "sets_delta": 0}, '
    '{"week": 4, "phase": "deload", "weight_pct": 85, "sets_delta": -1}], '
    '"tips": ["...", "..."]}\n\n'
    "Все тексты (title, summary, note, tips) — по-русски, коротко и конкретно."
)

# Английский аналог PROGRAM_SYSTEM_PROMPT (те же ключи, значения на английском).
PROGRAM_SYSTEM_PROMPT_EN = (
    "You are an experienced personal trainer. Build a personalized training program: "
    "a ONE-WEEK TEMPLATE plus a periodization plan for the whole program. The backend "
    "expands the template across weeks — do not describe weeks one by one.\n\n"
    + SAFETY_RULES_EN + "\n\n"
    "CATALOG: the user passes the list of available exercises in the format "
    "\"slug | name_en | muscle | equipment | measure | difficulty\". Use ONLY slugs from "
    "this list — no invented or altered slugs. If the exact exercise is missing, take "
    "the closest one for the same muscle from the catalog.\n\n"
    "PROGRAM RULES:\n"
    "- Exactly as many days as training sessions per week (day_index starts at 1). "
    "Split by day count: up to 3 — full_body, 4 — upper_lower, 5–6 — ppl.\n"
    "- Main exercises per day by session length: 20 min → 3–4, 30 → 4–5, 45 → 5–6, "
    "60+ → 6–8. Compound movements before isolation.\n"
    "- Balance push/pull and upper/lower across the week; weekly working sets per "
    "muscle group: 8–12 for beginners, 10–20 otherwise.\n"
    "- Rep ranges and rest by goal: strength — 3–6 reps, rest 120–180 s; muscle — "
    "6–12 reps, 60–120 s; loss and tone — 10–15 reps, 45–60 s plus one cardio block; "
    "endurance — 12–20 reps, 30–45 s plus cardio intervals.\n"
    "- Always a 5–8 min warm-up (warmup_general_5min plus 1–2 specific drills) and a "
    "3–5 min cool-down (stretching the muscles trained).\n"
    "- Periodization by week: phases base / build / peak / deload; every 4th week and "
    "the last one — deload (weight_pct 85, sets_delta -1).\n"
    "- start_weight_kg — conservative for the level, sex and body weight; null for "
    "bodyweight exercises and measure reps/time. If working weights are known, start "
    "from them. For measure time give time_sec instead of reps.\n"
    "- Limitations: knees — no deep squats or jumping lunges; lower back — no deadlifts "
    "from the floor or barbell hinges; shoulders — no behind-the-neck presses or deep "
    "dips; wrists — no loaded weight on the hands; neck — no neck loading or jerks; "
    "pregnancy/blood pressure/heart — see the safety rules.\n"
    "- Never put two heavy lower-back exercises (deadlift, hinges, weighted back "
    "extension) on the same day.\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else):\n"
    '{"title": "Upper/Lower — 6 weeks", "split_type": "full_body|upper_lower|ppl|custom", '
    '"summary": "why this plan, 2-3 sentences", '
    '"week_template": {"days": [{"day_index": 1, "title": "Upper body", '
    '"session_type": "strength|cardio|mixed|mobility", "focus_muscles": ["chest", "back"], '
    '"duration_min": 45, '
    '"warmup": [{"slug": "warmup_general_5min", "time_sec": 300, "note": "..."}, '
    '{"slug": "arm_circles", "sets": 1, "reps": 15}], '
    '"exercises": [{"slug": "db_bench_press", "sets": 3, "reps_min": 8, "reps_max": 12, '
    '"rest_sec": 90, "start_weight_kg": 14, "rpe": 7, "tempo": "2-0-2", '
    '"note": "elbows at 45°"}], '
    '"cooldown": [{"slug": "chest_stretch", "time_sec": 30}]}]}, '
    '"periodization": [{"week": 1, "phase": "base", "weight_pct": 100, "sets_delta": 0}, '
    '{"week": 4, "phase": "deload", "weight_pct": 85, "sets_delta": -1}], '
    '"tips": ["...", "..."]}\n\n'
    "All texts (title, summary, note, tips) — in English, short and specific."
)

# --------------------------------------------------------------------------- #
#  §5.2 Техника упражнения — system-промпты
# --------------------------------------------------------------------------- #
TECHNIQUE_SYSTEM_PROMPT = (
    "Ты — опытный тренер. Опиши технику выполнения одного упражнения для обычного "
    "посетителя зала: понятно, без жаргона, коротко.\n\n"
    + SAFETY_RULES_RU + "\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него) с полями:\n"
    '  "steps" — массив из 3-6 строк: пошаговое выполнение (исходное положение → движение → возврат);\n'
    '  "cues" — массив из 3-5 строк: ключевые подсказки-акценты («локти под 45°», «спина прямая»);\n'
    '  "mistakes" — массив из 3-5 строк: типичные ошибки и как их избежать;\n'
    '  "breathing" — строка: дыхание (когда вдох, когда выдох);\n'
    '  "safety" — строка: безопасность и кому быть осторожнее (общие рекомендации);\n'
    '  "muscles_text" — строка: какие мышцы работают, простыми словами.\n\n'
    "Правила:\n"
    "- Пиши по-русски, каждый пункт — законченная короткая фраза (до 140 символов).\n"
    "- Не выдумывай оборудование, которого нет в описании упражнения."
)

# Английский аналог TECHNIQUE_SYSTEM_PROMPT (те же ключи, значения на английском).
TECHNIQUE_SYSTEM_PROMPT_EN = (
    "You are an experienced coach. Describe how to perform one exercise for a regular "
    "gym-goer: clear, no jargon, short.\n\n"
    + SAFETY_RULES_EN + "\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else) with fields:\n"
    '  "steps" — array of 3-6 strings: step by step (start position → movement → return);\n'
    '  "cues" — array of 3-5 strings: key coaching cues ("elbows at 45°", "flat back");\n'
    '  "mistakes" — array of 3-5 strings: common mistakes and how to avoid them;\n'
    '  "breathing" — string: breathing pattern (when to inhale, when to exhale);\n'
    '  "safety" — string: safety notes and who should be careful (general guidance);\n'
    '  "muscles_text" — string: which muscles work, in plain words.\n\n'
    "Rules:\n"
    "- Write in English, each item is a short complete phrase (up to 140 characters).\n"
    "- Do not invent equipment that is not in the exercise description."
)

# --------------------------------------------------------------------------- #
#  §5.3 Недельный разбор — system-промпты
# --------------------------------------------------------------------------- #
REVIEW_SYSTEM_PROMPT = (
    "Ты — персональный тренер. Разбери тренировочную неделю пользователя по данным "
    "(программа и фаза, план vs факт, сессии с отзывами, упражнения план → факт с "
    "результатом success/partial/fail и рекордами, сеты по группам мышц, средние ккал и "
    "белок против нормы, динамика веса, ограничения) и предложи правки на следующую "
    "неделю.\n\n"
    + SAFETY_RULES_RU + "\n\n"
    "ПРАВИЛА ПРАВОК:\n"
    "- Не больше 5 изменений; на одно упражнение — только одна переменная.\n"
    "- Типы: weight_pct (дельта рабочего веса в процентах, от -15 до +10), sets (дельта "
    "подходов, -1 или +1), swap (замена упражнения: new_slug ТОЛЬКО из списка разрешённых "
    "slug и ТОЛЬКО на ту же группу мышц), rest_sec (новое время отдыха в секундах, "
    "30–240), deload_next_week (value 1 — разгрузочная неделя).\n"
    "- exercise_slug — только slug из данных недели или из списка разрешённых.\n"
    "- Если пропущено 50% и больше тренировок — не усложнять, предложить сократить дни "
    "или объём.\n"
    "- Если отзывы «легко» и все упражнения success — прибавляй вес или подходы; после "
    "двух неудач подряд — снижай вес; при отзывах «тяжело» несколько раз подряд — "
    "deload_next_week.\n"
    "- Питание — только с опорой на цифры (дефицит/профицит, белок к норме), без диет, "
    "лекарств и добавок.\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него):\n"
    '{"summary": "2-3 предложения о неделе", "wins": ["что получилось"], '
    '"issues": ["что менять"], "nutrition": ["1-3 замечания по питанию с цифрами"], '
    '"changes": [{"type": "weight_pct", "exercise_slug": "bb_squat", "value": -10, '
    '"reason": "два раза не добил повторы"}, '
    '{"type": "sets", "exercise_slug": "lat_pulldown", "value": 1, "reason": "спина недогружена: 6 сетов"}, '
    '{"type": "swap", "exercise_slug": "bb_deadlift", "new_slug": "rdl_db", "reason": "жалоба на поясницу"}, '
    '{"type": "deload_next_week", "value": 1, "reason": "3 тяжёлых отзыва подряд"}], '
    '"next_week_focus": "одна фраза — фокус следующей недели", '
    '"motivation": "одна тёплая фраза поддержки"}\n\n'
    "Все тексты — по-русски, коротко и по делу, каждый пункт до 160 символов."
)

# Английский аналог REVIEW_SYSTEM_PROMPT (те же ключи, значения на английском).
REVIEW_SYSTEM_PROMPT_EN = (
    "You are a personal trainer. Review the user's training week from the data "
    "(program and phase, plan vs actual, sessions with feedback, exercises planned → "
    "actual with success/partial/fail results and PRs, sets per muscle group, average "
    "calories and protein vs targets, weight trend, limitations) and propose changes "
    "for next week.\n\n"
    + SAFETY_RULES_EN + "\n\n"
    "CHANGE RULES:\n"
    "- At most 5 changes; only one variable per exercise.\n"
    "- Types: weight_pct (working weight delta in percent, -15 to +10), sets (sets "
    "delta, -1 or +1), swap (exercise replacement: new_slug ONLY from the allowed slug "
    "list and ONLY for the same muscle group), rest_sec (new rest time in seconds, "
    "30–240), deload_next_week (value 1 — a deload week).\n"
    "- exercise_slug — only a slug from the week data or from the allowed list.\n"
    "- If 50% or more sessions were skipped — do not make it harder; suggest fewer "
    "days or less volume.\n"
    "- If feedback is \"easy\" and every exercise is success — add weight or sets; "
    "after two failures in a row — reduce weight; several \"hard\" feedbacks in a row "
    "— deload_next_week.\n"
    "- Nutrition — only based on the numbers (deficit/surplus, protein vs target), no "
    "diets, medication or supplements.\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else):\n"
    '{"summary": "2-3 sentences about the week", "wins": ["what went well"], '
    '"issues": ["what to change"], "nutrition": ["1-3 nutrition notes with numbers"], '
    '"changes": [{"type": "weight_pct", "exercise_slug": "bb_squat", "value": -10, '
    '"reason": "missed the reps twice"}, '
    '{"type": "sets", "exercise_slug": "lat_pulldown", "value": 1, "reason": "back under-loaded: 6 sets"}, '
    '{"type": "swap", "exercise_slug": "bb_deadlift", "new_slug": "rdl_db", "reason": "lower back complaint"}, '
    '{"type": "deload_next_week", "value": 1, "reason": "3 hard feedbacks in a row"}], '
    '"next_week_focus": "one phrase — focus for next week", '
    '"motivation": "one warm supportive phrase"}\n\n'
    "All texts — in English, short and practical, each item up to 160 characters."
)

# --------------------------------------------------------------------------- #
#  §5.4 Совет дня по питанию — system-промпты
# --------------------------------------------------------------------------- #
NUTRITION_SYSTEM_PROMPT = (
    "Ты — тренер, который помогает связать тренировки и питание. По контексту дня "
    "(тренировочный или день отдыха, тип и длительность тренировки, цель по питанию, "
    "дневная норма ккал и белка, сколько уже съедено, вес) дай короткий совет на "
    "сегодня.\n\n"
    + SAFETY_RULES_RU + "\n\n"
    "Правила:\n"
    "- НЕ меняй дневную цель по калориям — объясняй, как распределить еду в течение дня.\n"
    "- В тренировочный день: что съесть за 1–2 часа до и после тренировки (pre_workout, "
    "post_workout).\n"
    "- В день отдыха: про белок и восстановление; pre_workout и post_workout — null.\n"
    "- Цифры — только из контекста, ничего не придумывай. Без диет, лекарств и добавок.\n\n"
    "Верни СТРОГО валидный JSON-объект (и НИЧЕГО кроме него):\n"
    '{"headline": "одна фраза-заголовок", "calories_note": "про калории с цифрами", '
    '"protein_note": "про белок с цифрами", "pre_workout": "строка или null", '
    '"post_workout": "строка или null", "hydration": "про воду", '
    '"tips": ["2-3 коротких совета"]}\n\n'
    "Все тексты — по-русски, коротко, каждый до 160 символов."
)

# Английский аналог NUTRITION_SYSTEM_PROMPT (те же ключи, значения на английском).
NUTRITION_SYSTEM_PROMPT_EN = (
    "You are a coach who links training and nutrition. From the day context (training "
    "day or rest day, workout type and duration, diet goal, daily calorie and protein "
    "targets, what has been eaten so far, body weight) give a short tip for today.\n\n"
    + SAFETY_RULES_EN + "\n\n"
    "Rules:\n"
    "- Do NOT change the daily calorie target — explain how to distribute food across "
    "the day.\n"
    "- On a training day: what to eat 1–2 hours before and after the workout "
    "(pre_workout, post_workout).\n"
    "- On a rest day: focus on protein and recovery; pre_workout and post_workout — null.\n"
    "- Numbers only from the context, never invent them. No diets, medication or "
    "supplements.\n\n"
    "Return STRICTLY a valid JSON object (and NOTHING else):\n"
    '{"headline": "one headline phrase", "calories_note": "about calories with numbers", '
    '"protein_note": "about protein with numbers", "pre_workout": "string or null", '
    '"post_workout": "string or null", "hydration": "about water", '
    '"tips": ["2-3 short tips"]}\n\n'
    "All texts — in English, short, each up to 160 characters."
)


# --------------------------------------------------------------------------- #
#  Мелкие хелперы приведения типов
# --------------------------------------------------------------------------- #
def _clamp(value, lo, hi):
    """Ограничить число диапазоном [lo, hi]."""
    return max(lo, min(hi, value))


def _clean_str(value, limit: int = 200) -> str:
    """Строка без крайних пробелов, обрезанная до limit символов; не-строка → "".

    Числа/None/bool в текстовых полях — мусор от модели, отбрасываем (как в
    ai_service.recovery_advice), а не превращаем в «42».
    """
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text[:limit].strip()


def _str_list(value, limit: int, item_limit: int = 200) -> list[str]:
    """Список непустых строк (не более limit). Строка с переносами — режется на пункты."""
    if isinstance(value, str):
        items = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line) for line in value.splitlines()]
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return []
    out: list[str] = []
    for item in items:
        text = _clean_str(item, item_limit)
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _as_list(value) -> list:
    """Список из значения: list/tuple → как есть, JSON-строка → разбор, CSV → split, None → []."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [x for x in value if x is not None]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [x for x in parsed if x is not None]
            except (TypeError, ValueError):
                pass
        return [p.strip() for p in text.split(",") if p.strip()]
    return [value]


def _norm_slug(value) -> str:
    """Slug к каноническому виду: нижний регистр, всё кроме букв/цифр → «_»."""
    text = _clean_str(value, 80).lower()
    text = re.sub(r"[^a-z0-9а-яё]+", "_", text)
    return text.strip("_")


def _int_or_none(value, lo: int, hi: int):
    """Целое в диапазоне или None, если значения нет/оно не число."""
    if value is None or value == "":
        return None
    result = _coerce_int(value, default=None) if value is not None else None
    if result is None:
        return None
    return _clamp(result, lo, hi)


def _plural_weeks_ru(n: int) -> str:
    """«4 недели», «6 недель», «1 неделя» — русское склонение."""
    if n % 10 == 1 and n % 100 != 11:
        word = "неделя"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        word = "недели"
    else:
        word = "недель"
    return f"{n} {word}"


def _label(table: dict, code, lang: str) -> str:
    """Название кода по таблице (ru/en) + сам код в скобках; неизвестный код — как есть."""
    key = _clean_str(code, 40)
    pair = table.get(key)
    if not pair:
        return key
    return f"{pair[1 if lang == 'en' else 0]} ({key})"


def _field(entry, name: str, default=None):
    """Поле записи каталога: dict → ключ, ORM/объект → атрибут."""
    if entry is None:
        return default
    if isinstance(entry, dict):
        value = entry.get(name, default)
    else:
        value = getattr(entry, name, default)
    return default if value is None else value


def _contraindications(entry) -> set:
    """Коды ограничений записи каталога (contraindications или contraindications_json)."""
    raw = _field(entry, "contraindications")
    if raw is None:
        raw = _field(entry, "contraindications_json")
    return {str(x).strip() for x in _as_list(raw) if str(x).strip()}


# --------------------------------------------------------------------------- #
#  Каталог упражнений: индекс + фаззи-поиск slug
# --------------------------------------------------------------------------- #
def _iter_catalog(catalog):
    """Единообразный обход каталога любого поддерживаемого вида → записи (dict/объект)."""
    if catalog is None:
        return []
    if isinstance(catalog, _Catalog):
        return list(catalog.entries.values())
    if isinstance(catalog, str):
        entries = []
        for line in catalog.splitlines():
            if "|" not in line:
                continue
            cols = [c.strip() for c in line.split("|")]
            if not cols[0] or cols[0].lower() == "slug":
                continue
            cols += [""] * (6 - len(cols))
            entries.append({
                "slug": cols[0],
                "name_en": cols[1] or None,
                "muscle_group": cols[2] or None,
                "equipment": cols[3] or None,
                "measure_type": cols[4] or None,
                "difficulty": _coerce_int(cols[5], 1) if cols[5] else 1,
            })
        return entries
    if isinstance(catalog, dict):
        entries = []
        for key, value in catalog.items():
            if isinstance(value, dict):
                if not value.get("slug"):
                    value = dict(value, slug=key)
                entries.append(value)
            elif isinstance(value, str):
                entries.append({"slug": key, "name_en": value})
            elif value is not None:
                entries.append(value)
        return entries
    if isinstance(catalog, (list, tuple, set)):
        out = []
        for item in catalog:
            if isinstance(item, str):
                out.append({"slug": item})
            elif item is not None:
                out.append(item)
        return out
    return []


class _Catalog:
    """Индекс каталога упражнений: точный, фаззи по slug и фаззи по имени поиск."""

    def __init__(self, catalog=None):
        self.entries: dict = {}
        self._by_name: dict[str, str] = {}
        for entry in _iter_catalog(catalog):
            slug = _norm_slug(_field(entry, "slug"))
            if not slug:
                continue
            self.entries[slug] = entry
            for key in ("name_en", "name_ru", "name"):
                name = _field(entry, key)
                if isinstance(name, str) and name.strip():
                    self._by_name.setdefault(name.strip().lower(), slug)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def __contains__(self, slug) -> bool:
        return slug in self.entries

    def __len__(self) -> int:
        return len(self.entries)

    def get(self, slug: str):
        return self.entries.get(slug)

    def field(self, slug: str, name: str, default=None):
        return _field(self.entries.get(slug), name, default)

    def resolve(self, raw_slug, *hints) -> str | None:
        """Найти slug каталога: точно → фаззи по slug → точно/фаззи по имени → None.

        hints — имена из ответа модели (name/name_en/name_ru), помогают, когда
        модель вернула не slug, а название.
        """
        cand = _norm_slug(raw_slug)
        if cand and cand in self.entries:
            return cand
        if not self.entries:
            return None
        slugs = list(self.entries)
        if cand:
            close = difflib.get_close_matches(cand, slugs, n=1, cutoff=0.8)
            if close:
                return close[0]
        queries = []
        if cand:
            queries.append(cand.replace("_", " "))
        for hint in hints:
            text = _clean_str(hint, 80).lower()
            if text:
                queries.append(text)
        if not self._by_name:
            return None
        names = list(self._by_name)
        for query in queries:
            if query in self._by_name:
                return self._by_name[query]
            close = difflib.get_close_matches(query, names, n=1, cutoff=0.72)
            if close:
                return self._by_name[close[0]]
        return None

    def lines(self, limit: int = MAX_CATALOG_LINES) -> str:
        """Строки «slug | name_en | muscle | equipment | measure | difficulty» для промпта."""
        rows = []
        for slug, entry in self.entries.items():
            name = _field(entry, "name_en") or _field(entry, "name_ru") or slug
            rows.append(" | ".join([
                slug,
                _clean_str(name, 60),
                _clean_str(_field(entry, "muscle_group", ""), 20),
                _clean_str(_field(entry, "equipment", ""), 20),
                _clean_str(_field(entry, "measure_type", ""), 12),
                str(_coerce_int(_field(entry, "difficulty", 1), 1)),
            ]))
            if len(rows) >= limit:
                break
        return "\n".join(rows)

    def brief(self, slug: str) -> dict:
        """Краткие поля записи для вывода в нормализованные структуры."""
        return {
            "exercise_id": _field(self.entries.get(slug), "id"),
            "name_ru": _field(self.entries.get(slug), "name_ru"),
            "name_en": _field(self.entries.get(slug), "name_en"),
            "muscle_group": _field(self.entries.get(slug), "muscle_group"),
        }


def _pick_alternative(cat: _Catalog, slug: str, limits: set, used: set) -> str | None:
    """Замена контриндицированного упражнения: та же мышца, без пересечения с
    ограничениями, предпочтительно та же категория и оборудование, полегче."""
    entry = cat.get(slug)
    if entry is None:
        return None
    muscle = _field(entry, "muscle_group")
    category = _field(entry, "category")
    equipment = _field(entry, "equipment")
    candidates = []
    for other_slug, other in cat.entries.items():
        if other_slug == slug or other_slug in used:
            continue
        if _field(other, "muscle_group") != muscle:
            continue
        if _contraindications(other) & limits:
            continue
        candidates.append((
            0 if _field(other, "category") == category else 1,
            0 if _field(other, "equipment") == equipment else 1,
            _coerce_int(_field(other, "difficulty", 1), 1),
            other_slug,
        ))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][3]


# --------------------------------------------------------------------------- #
#  §5.1 Нормализация программы
# --------------------------------------------------------------------------- #
def _normalize_aux_items(items, cat: _Catalog, unmatched: list, day_index: int, block: str) -> list[dict]:
    """Разминка/заминка: [{"slug","exercise_id","sets","reps","time_sec","note"}]."""
    out: list[dict] = []
    used: set = set()
    for raw in items if isinstance(items, list) else []:
        if isinstance(raw, str):
            raw = {"slug": raw}
        if not isinstance(raw, dict):
            continue
        slug = cat.resolve(raw.get("slug"), raw.get("name"), raw.get("name_en"), raw.get("name_ru")) if cat else _norm_slug(raw.get("slug"))
        if not slug:
            unmatched.append({"slug": _clean_str(raw.get("slug"), 80), "day_index": day_index, "block": block, "reason": "unknown"})
            continue
        if slug in used:
            continue
        used.add(slug)
        measure = cat.field(slug, "measure_type") if cat else None
        reps = _int_or_none(raw.get("reps"), 1, 50)
        time_sec = _int_or_none(raw.get("time_sec"), TIME_RANGE[0], TIME_RANGE[1])
        if measure == "time" or (reps is None and time_sec is None):
            reps = None
            time_sec = time_sec or 30
        out.append({
            "slug": slug,
            "exercise_id": cat.field(slug, "id") if cat else None,
            "sets": _clamp(_coerce_int(raw.get("sets"), 1), 1, 3),
            "reps": reps,
            "time_sec": time_sec,
            "note": _clean_str(raw.get("note"), 120) or None,
        })
        if len(out) >= MAX_AUX_ITEMS:
            break
    return out


def _default_aux_item(cat: _Catalog, slug: str, seconds: int) -> dict:
    """Дефолтный пункт разминки/заминки (инжектируется при отсутствии блока)."""
    return {
        "slug": slug,
        "exercise_id": cat.field(slug, "id") if cat else None,
        "sets": 1,
        "reps": None,
        "time_sec": seconds,
        "note": None,
    }


def _normalize_main_items(items, cat: _Catalog, limits: set, unmatched: list, day_index: int) -> list[dict]:
    """Основные упражнения дня в формате exercises_json (см. ТЗ §3)."""
    out: list[dict] = []
    used: set = set()
    for raw in items if isinstance(items, list) else []:
        if isinstance(raw, str):
            raw = {"slug": raw}
        if not isinstance(raw, dict):
            continue
        raw_slug = _clean_str(raw.get("slug"), 80)
        slug = cat.resolve(raw.get("slug"), raw.get("name"), raw.get("name_en"), raw.get("name_ru")) if cat else _norm_slug(raw.get("slug"))
        if not slug:
            unmatched.append({"slug": raw_slug, "day_index": day_index, "block": "main", "reason": "unknown"})
            continue
        if cat and limits and (_contraindications(cat.get(slug)) & limits):
            alt = _pick_alternative(cat, slug, limits, used)
            unmatched.append({"slug": slug, "day_index": day_index, "block": "main", "reason": "contraindicated", "replaced_with": alt})
            if not alt:
                continue
            slug = alt
        if slug in used:
            continue
        used.add(slug)

        measure = (cat.field(slug, "measure_type") if cat else None) or "reps_weight"
        timed = measure in ("time", "distance")
        reps_min = None if timed else _clamp(_coerce_int(raw.get("reps_min"), 8), REPS_RANGE[0], REPS_RANGE[1])
        reps_max = None if timed else _clamp(_coerce_int(raw.get("reps_max"), max(reps_min, 12)), REPS_RANGE[0], REPS_RANGE[1])
        if reps_min is not None and reps_max is not None and reps_min > reps_max:
            reps_min, reps_max = reps_max, reps_min
        time_sec = None
        if timed:
            time_sec = _clamp(_coerce_int(raw.get("time_sec"), 45 if measure == "time" else 600), TIME_RANGE[0], TIME_RANGE[1])
        weight = None
        if measure == "reps_weight" and raw.get("start_weight_kg") is not None:
            weight = _clamp(_coerce_float(raw.get("start_weight_kg"), 0.0), WEIGHT_RANGE[0], WEIGHT_RANGE[1])
            if weight <= 0:
                weight = None
        out.append({
            "slug": slug,
            "exercise_id": cat.field(slug, "id") if cat else None,
            "muscle_group": cat.field(slug, "muscle_group") if cat else None,
            "sets": _clamp(_coerce_int(raw.get("sets"), 3), SETS_RANGE[0], SETS_RANGE[1]),
            "reps_min": reps_min,
            "reps_max": reps_max,
            "time_sec": time_sec,
            "rest_sec": _clamp(_coerce_int(raw.get("rest_sec"), 90), REST_RANGE[0], REST_RANGE[1]),
            "start_weight_kg": weight,
            "rpe": _int_or_none(raw.get("rpe"), RPE_RANGE[0], RPE_RANGE[1]),
            "tempo": _clean_str(raw.get("tempo"), 12) or None,
            "note": _clean_str(raw.get("note"), 160) or None,
            "order": len(out) + 1,
        })
        if len(out) >= MAX_MAIN_EXERCISES:
            break
    return out


def _normalize_day(raw: dict, index: int, cat: _Catalog, limits: set, unmatched: list, lang: str, session_minutes) -> dict:
    """Один день шаблона недели."""
    day_index = _coerce_int(raw.get("day_index"), index + 1)
    if day_index < 1:
        day_index = index + 1
    session_type = _clean_str(raw.get("session_type"), 20).lower()
    if session_type not in SESSION_TYPES:
        session_type = "strength"
    exercises = _normalize_main_items(raw.get("exercises"), cat, limits, unmatched, day_index)
    warmup = _normalize_aux_items(raw.get("warmup"), cat, unmatched, day_index, "warmup")
    cooldown = _normalize_aux_items(raw.get("cooldown"), cat, unmatched, day_index, "cooldown")
    if not warmup:
        warmup = [_default_aux_item(cat, DEFAULT_WARMUP_SLUG, DEFAULT_WARMUP_SEC)]
    if not cooldown:
        cooldown = [_default_aux_item(cat, DEFAULT_COOLDOWN_SLUG, DEFAULT_COOLDOWN_SEC)]

    focus = []
    for muscle in _as_list(raw.get("focus_muscles")):
        code = _clean_str(muscle, 20).lower()
        if code in MUSCLE_GROUPS and code not in focus:
            focus.append(code)
    if not focus:
        for item in exercises:
            code = item.get("muscle_group")
            if code in MUSCLE_GROUPS and code not in focus:
                focus.append(code)
    focus = focus[:5]

    title = _clean_str(raw.get("title"), 60)
    if not title:
        title = f"Day {day_index}" if lang == "en" else f"День {day_index}"
    duration_default = _coerce_int(session_minutes, 45) or 45
    return {
        "day_index": day_index,
        "title": title,
        "session_type": session_type,
        "focus_muscles": focus,
        "duration_min": _clamp(_coerce_int(raw.get("duration_min"), duration_default), DURATION_RANGE[0], DURATION_RANGE[1]),
        "warmup": warmup,
        "exercises": exercises,
        "cooldown": cooldown,
    }


def _normalize_periodization(raw, weeks) -> list[dict]:
    """План периодизации: по неделе {week, phase, weight_pct, sets_delta, label_ru, label_en}.

    Недостающие недели дополняются фазой base; последняя неделя без явной фазы —
    deload (ТЗ §5.1). Значения clamp: weight_pct 50–110, sets_delta −2..+2.
    """
    by_week: dict[int, dict] = {}
    max_week = _coerce_int(weeks, 0) if weeks else 0
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        week = _coerce_int(item.get("week"), 0)
        if week < 1 or (max_week and week > max_week) or week > 52:
            continue
        phase = _clean_str(item.get("phase"), 12).lower()
        if phase not in PHASES:
            phase = "base"
        default_pct = 85 if phase == "deload" else 100
        default_delta = -1 if phase == "deload" else 0
        by_week.setdefault(week, {
            "week": week,
            "phase": phase,
            "weight_pct": _clamp(_coerce_int(item.get("weight_pct"), default_pct), 50, 110),
            "sets_delta": _clamp(_coerce_int(item.get("sets_delta"), default_delta), -2, 2),
        })
    if max_week:
        for week in range(1, max_week + 1):
            if week in by_week:
                continue
            if week == max_week:
                by_week[week] = {"week": week, "phase": "deload", "weight_pct": 85, "sets_delta": -1}
            else:
                by_week[week] = {"week": week, "phase": "base", "weight_pct": 100, "sets_delta": 0}
    out = []
    for week in sorted(by_week):
        item = by_week[week]
        item["label_ru"], item["label_en"] = PHASE_LABELS[item["phase"]]
        out.append(item)
    return out


def _default_split(days_per_week) -> str:
    """Сплит по числу тренировочных дней (≤3 full_body, 4 upper_lower, 5–6 ppl)."""
    days = _coerce_int(days_per_week, 3)
    if days <= 3:
        return "full_body"
    if days == 4:
        return "upper_lower"
    return "ppl"


def normalize_program(
    data,
    catalog_map,
    days_per_week=None,
    weeks=None,
    limitations=None,
    lang: str = "ru",
    session_minutes=None,
) -> dict:
    """Нормализовать ответ модели по программе (ТЗ §5.1). Без ИИ, без БД.

    Параметры:
      * data          — распарсенный JSON ответа модели;
      * catalog_map   — каталог (dict slug→запись, список записей, ORM-объекты или
                        строка каталога); незнакомый slug → фаззи-матч → отброс;
      * days_per_week — ожидаемое число дней: лишние усекаются, недостающие → AIError;
      * weeks         — длина программы (периодизация дополняется до неё);
      * limitations   — коды ограничений пользователя: контриндицированные
                        упражнения заменяются альтернативой из каталога или отбрасываются;
      * session_minutes — дефолт duration_min дня.

    Возвращает {title, split_type, summary, week_template: {days}, periodization,
    tips, unmatched} — unmatched содержит отброшенные/заменённые slug для
    маршрута (например, чтобы создать строку с created_by_ai=True).
    """
    lang = _normalize_lang(lang)
    if not isinstance(data, dict) or not data:
        raise AIError("AI не вернул программу (пустой ответ)")
    cat = catalog_map if isinstance(catalog_map, _Catalog) else _Catalog(catalog_map)
    limits = {str(x).strip() for x in _as_list(limitations) if str(x).strip()} - {"none", ""}
    expected_days = _coerce_int(days_per_week, 0) if days_per_week else 0
    weeks_n = _coerce_int(weeks, 0) if weeks else 0

    template = data.get("week_template")
    raw_days = template.get("days") if isinstance(template, dict) else data.get("days")
    if not isinstance(raw_days, list) or not raw_days:
        raise AIError("AI не вернул дни недели программы")

    unmatched: list[dict] = []
    days: list[dict] = []
    for index, raw in enumerate(raw_days):
        if not isinstance(raw, dict):
            continue
        day = _normalize_day(raw, index, cat, limits, unmatched, lang, session_minutes)
        if day["exercises"]:
            days.append(day)
    if not days:
        raise AIError("AI не вернул ни одного дня с упражнениями")
    if expected_days:
        if len(days) < expected_days:
            raise AIError(f"AI вернул {len(days)} дней вместо {expected_days}")
        days = days[:expected_days]
    for index, day in enumerate(days, start=1):
        day["day_index"] = index

    split_type = _clean_str(data.get("split_type"), 20).lower()
    if split_type not in SPLIT_TYPES:
        split_type = _default_split(expected_days or len(days))

    if not weeks_n:
        raw_period = data.get("periodization")
        if isinstance(raw_period, list):
            weeks_n = max((_coerce_int(p.get("week"), 0) for p in raw_period if isinstance(p, dict)), default=0)
    periodization = _normalize_periodization(data.get("periodization"), weeks_n or None)

    title = _clean_str(data.get("title"), 80)
    if not title:
        split_title = _SPLIT_TITLES[split_type][1 if lang == "en" else 0]
        if weeks_n:
            title = f"{split_title} — {weeks_n} weeks" if lang == "en" else f"{split_title} — {_plural_weeks_ru(weeks_n)}"
        else:
            title = split_title

    return {
        "title": title,
        "split_type": split_type,
        "summary": _clean_str(data.get("summary"), 600),
        "week_template": {"days": days},
        "periodization": periodization,
        "tips": _str_list(data.get("tips"), 6, 200),
        "unmatched": unmatched,
    }


# --------------------------------------------------------------------------- #
#  §5.1 Генерация программы
# --------------------------------------------------------------------------- #
def _render_known_weights(value, lang: str) -> str:
    """«Известные рабочие веса» из профиля: dict slug→кг, список dict или строк."""
    items = []
    if isinstance(value, dict):
        for slug, kg in value.items():
            kg_val = _coerce_float(kg, 0.0)
            if slug and kg_val > 0:
                items.append(f"{_clean_str(slug, 60)} — {kg_val:g} {'kg' if lang == 'en' else 'кг'}")
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                slug = item.get("slug") or item.get("name_en") or item.get("name_ru") or item.get("name")
                kg_val = _coerce_float(item.get("weight_kg", item.get("working_weight_kg", item.get("kg"))), 0.0)
                if slug and kg_val > 0:
                    items.append(f"{_clean_str(slug, 60)} — {kg_val:g} {'kg' if lang == 'en' else 'кг'}")
            else:
                text = _clean_str(item, 80)
                if text:
                    items.append(text)
    return "; ".join(items[:25])


def _render_profile_parts(profile: dict, body: dict, lang: str) -> list[str]:
    """Строки user_prompt с анкетой и телом пользователя."""
    en = lang == "en"
    parts: list[str] = []

    parts.append(("Goal: " if en else "Цель: ") + (_label(_GOAL_NAMES, profile.get("goal"), lang) or ("not set" if en else "не указана")) + ".")
    parts.append(("Level: " if en else "Уровень: ") + (_label(_LEVEL_NAMES, profile.get("level"), lang) or ("not set" if en else "не указан")) + ".")

    equip = _label(_EQUIPMENT_NAMES, profile.get("equipment"), lang) or ("not set" if en else "не указано")
    extra = [_label(_EXTRA_NAMES, x, lang) for x in _as_list(profile.get("equipment_extra")) if _clean_str(x, 40)]
    line = ("Equipment: " if en else "Оборудование: ") + equip
    if extra:
        line += (". Extra: " if en else ". Дополнительно: ") + ", ".join(extra)
    parts.append(line + ".")

    days = _coerce_int(profile.get("days_per_week"), 3)
    weekdays = []
    for w in _as_list(profile.get("preferred_weekdays")):
        idx = _coerce_int(w, -1)
        if 0 <= idx <= 6:
            weekdays.append(_WEEKDAY_NAMES[idx][1 if en else 0])
    line = (f"Sessions per week: {days}" if en else f"Тренировок в неделю: {days}")
    if weekdays:
        line += (" (days: " if en else " (дни: ") + ", ".join(weekdays) + ")"
    parts.append(line + ".")

    minutes = _coerce_int(profile.get("session_minutes"), 45)
    parts.append((f"Session length: {minutes} min." if en else f"Длительность сессии: {minutes} мин."))
    weeks = _coerce_int(profile.get("program_weeks"), 6)
    parts.append((f"Program length: {weeks} weeks." if en else f"Длина программы: {_plural_weeks_ru(weeks)}."))

    limits = [x for x in _as_list(profile.get("limitations")) if _clean_str(x, 40) and _clean_str(x, 40) != "none"]
    if limits:
        line = ("Limitations/injuries: " if en else "Ограничения и травмы: ") + ", ".join(_label(_LIMITATION_NAMES, x, lang) for x in limits)
    else:
        line = "Limitations: none" if en else "Ограничений нет"
    note = _clean_str(profile.get("limitations_text"), 300)
    if note:
        line += (". Details: " if en else ". Комментарий: ") + note
    parts.append(line + ".")

    focus = [x for x in _as_list(profile.get("focus")) if _clean_str(x, 40) and _clean_str(x, 40) != "none"]
    if focus:
        parts.append(("Focus: " if en else "Акцент: ") + ", ".join(_label(_FOCUS_NAMES, x, lang) for x in focus) + ".")

    body_bits = []
    gender = _clean_str(body.get("gender"), 20).lower()
    if gender:
        body_bits.append(("sex " if en else "пол ") + (_GENDER_NAMES.get(gender, (gender, gender))[1 if en else 0]))
    age = _coerce_int(body.get("age"), 0)
    if age > 0:
        body_bits.append((f"age {age}" if en else f"возраст {age}"))
    weight = _coerce_float(body.get("weight"), 0.0)
    if weight > 0:
        body_bits.append((f"weight {weight:g} kg" if en else f"вес {weight:g} кг"))
    height = _coerce_float(body.get("height"), 0.0)
    if height > 0:
        body_bits.append((f"height {height:g} cm" if en else f"рост {height:g} см"))
    diet_goal = _clean_str(body.get("diet_goal"), 20).lower()
    if diet_goal:
        body_bits.append(("diet goal " if en else "цель по питанию ") + _label(_DIET_GOAL_NAMES, diet_goal, lang))
    kcal = _coerce_int(body.get("daily_goal_kcal"), 0)
    if kcal > 0:
        body_bits.append((f"daily target {kcal} kcal" if en else f"норма {kcal} ккал"))
    if body_bits:
        parts.append(("Body: " if en else "Тело: ") + ", ".join(body_bits) + ".")
    else:
        parts.append("Body data not provided — use conservative starting weights." if en
                     else "Данных о теле нет — стартовые веса выбирай консервативно.")

    known = _render_known_weights(profile.get("known_weights") or body.get("known_weights"), lang)
    if known:
        parts.append(("Known working weights: " if en else "Известные рабочие веса: ") + known + ".")
    regen = _clean_str(profile.get("regenerate_note") or body.get("regenerate_note"), 300)
    if regen:
        parts.append(("Request for the rebuild: " if en else "Пожелание при пересборке: ") + regen)
    return parts


def generate_program(profile: dict, body: dict, catalog, lang: str = "ru", catalog_map=None) -> dict:
    """Сгенерировать шаблон недели + периодизацию (ТЗ §5.1). Тег trainer_program.

    Параметры:
      * profile     — анкета тренера: goal, level, equipment, equipment_extra,
                      days_per_week, preferred_weekdays, session_minutes, program_weeks,
                      limitations, limitations_text, focus; опционально known_weights
                      ({slug: кг} из TrainerExerciseState при пересборке) и regenerate_note;
      * body        — данные тела из User: gender, age, weight, height, diet_goal,
                      daily_goal_kcal;
      * catalog     — отфильтрованный каталог (trainer_logic.catalog_for_prompt):
                      строка «slug | name_en | muscle | equipment | measure | difficulty»
                      ЛИБО список/словарь записей (тогда строка строится здесь, ≤110);
      * lang        — "ru" | "en" (язык значений; ключи JSON не меняются);
      * catalog_map — необязательный полный каталог для нормализации (если catalog —
                      строка, карта строится из её строк).

    Возвращает нормализованный словарь (см. normalize_program) плюс "ai_model".
    При сбое ИИ или непригодном ответе — AIError (502 на маршруте).
    """
    lang = _normalize_lang(lang)
    profile = profile if isinstance(profile, dict) else {}
    body = body if isinstance(body, dict) else {}

    cat = _Catalog(catalog_map if catalog_map is not None else catalog)
    catalog_text = catalog.strip() if isinstance(catalog, str) else cat.lines()

    parts = _render_profile_parts(profile, body, lang)
    if lang == "en":
        parts.append(
            "CATALOG (slug | name_en | muscle | equipment | measure | difficulty) — use ONLY these slugs:\n"
            + (catalog_text or "(empty)")
        )
        parts.append("Return the result strictly as JSON per the instructions.")
    else:
        parts.append(
            "КАТАЛОГ (slug | name_en | muscle | equipment | measure | difficulty) — используй ТОЛЬКО эти slug:\n"
            + (catalog_text or "(пусто)")
        )
        parts.append("Верни результат строго в формате JSON по инструкции.")

    system_prompt = _pick_prompt(PROGRAM_SYSTEM_PROMPT, PROGRAM_SYSTEM_PROMPT_EN, lang)
    data, _debug = ai_service._run_text_completion(
        system_prompt, "\n".join(parts), log_tag=TAG_PROGRAM, max_tokens=MAX_TOKENS_PROGRAM
    )
    _debug = _debug if isinstance(_debug, dict) else {}

    try:
        result = normalize_program(
            data,
            cat,
            days_per_week=profile.get("days_per_week"),
            weeks=profile.get("program_weeks"),
            limitations=profile.get("limitations"),
            lang=lang,
            session_minutes=profile.get("session_minutes"),
        )
    except AIError as exc:
        logger.warning("AI[%s]: непригодный ответ: %s", TAG_PROGRAM, exc)
        raise AIError(
            str(exc),
            raw=_debug.get("raw", ""),
            finish_reason=_debug.get("finish_reason"),
            refusal=_debug.get("refusal"),
        )
    if result["unmatched"]:
        logger.info("AI[%s]: отброшено/заменено %d slug: %s", TAG_PROGRAM, len(result["unmatched"]),
                    [u.get("slug") for u in result["unmatched"]][:10])
    result["ai_model"] = ai_service.TEXT_MODEL
    return result


# --------------------------------------------------------------------------- #
#  §5.2 Техника упражнения
# --------------------------------------------------------------------------- #
def normalize_technique(data) -> dict:
    """Нормализовать технику: steps ≤6, cues ≤5, mistakes ≤5, строки breathing/safety/muscles_text."""
    data = data if isinstance(data, dict) else {}
    return {
        "steps": _str_list(data.get("steps"), 6, 200),
        "cues": _str_list(data.get("cues"), 5, 160),
        "mistakes": _str_list(data.get("mistakes"), 5, 160),
        "breathing": _clean_str(data.get("breathing"), 300),
        "safety": _clean_str(data.get("safety"), 300),
        "muscles_text": _clean_str(data.get("muscles_text"), 300),
    }


def exercise_technique(exercise, limitations=None, lang: str = "ru") -> dict:
    """Техника выполнения упражнения (ТЗ §5.2). Тег trainer_technique, 700 токенов.

    exercise — dict или ORM-объект TrainerExercise (slug, name_ru, name_en,
    muscle_group, secondary_muscles(_json), equipment, category, measure_type,
    is_unilateral). limitations принимается для совместимости сигнатуры, но в
    промпт НЕ попадает: кэш техники общий на упражнение и язык, дисклеймер при
    ограничениях добавляет маршрут.

    Возвращает {steps, cues, mistakes, breathing, safety, muscles_text}; без
    шагов — AIError.
    """
    lang = _normalize_lang(lang)
    en = lang == "en"
    name_ru = _clean_str(_field(exercise, "name_ru"), 80)
    name_en = _clean_str(_field(exercise, "name_en"), 80)
    slug = _clean_str(_field(exercise, "slug"), 80)
    primary = name_en if en else (name_ru or name_en)
    secondary = name_ru if en else name_en
    display = primary or secondary or slug or ("exercise" if en else "упражнение")
    if secondary and secondary != display:
        display += f" ({secondary})"
    if slug:
        display += f", slug {slug}"

    muscle = _clean_str(_field(exercise, "muscle_group"), 20)
    extra = [_clean_str(x, 20) for x in _as_list(_field(exercise, "secondary_muscles") or _field(exercise, "secondary_muscles_json"))]
    extra = [x for x in extra if x]
    equipment = _clean_str(_field(exercise, "equipment"), 20)
    category = _clean_str(_field(exercise, "category"), 20)
    measure = _clean_str(_field(exercise, "measure_type"), 20)
    unilateral = bool(_field(exercise, "is_unilateral", False))

    if en:
        parts = [f"Exercise: {display}."]
        if muscle:
            parts.append(f"Primary muscle: {muscle}" + (f"; secondary: {', '.join(extra)}" if extra else "") + ".")
        bits = [b for b in (f"equipment {equipment}" if equipment else "", f"type {category}" if category else "",
                            f"measure {measure}" if measure else "", "unilateral" if unilateral else "") if b]
        if bits:
            parts.append("Details: " + ", ".join(bits) + ".")
        parts.append("Return the result strictly as JSON per the instructions.")
    else:
        parts = [f"Упражнение: {display}."]
        if muscle:
            parts.append(f"Основная мышца: {muscle}" + (f"; вспомогательные: {', '.join(extra)}" if extra else "") + ".")
        bits = [b for b in (f"оборудование {equipment}" if equipment else "", f"тип {category}" if category else "",
                            f"измерение {measure}" if measure else "", "одностороннее" if unilateral else "") if b]
        if bits:
            parts.append("Детали: " + ", ".join(bits) + ".")
        parts.append("Верни результат строго в формате JSON по инструкции.")

    system_prompt = _pick_prompt(TECHNIQUE_SYSTEM_PROMPT, TECHNIQUE_SYSTEM_PROMPT_EN, lang)
    data, _debug = ai_service._run_text_completion(
        system_prompt, "\n".join(parts), log_tag=TAG_TECHNIQUE, max_tokens=MAX_TOKENS_TECHNIQUE
    )
    _debug = _debug if isinstance(_debug, dict) else {}
    result = normalize_technique(data)
    if not result["steps"]:
        raise AIError(
            "AI не вернул технику упражнения",
            raw=_debug.get("raw", ""),
            finish_reason=_debug.get("finish_reason"),
            refusal=_debug.get("refusal"),
        )
    return result


# --------------------------------------------------------------------------- #
#  §5.3 Недельный разбор
# --------------------------------------------------------------------------- #
def normalize_review(data, catalog_map=None) -> dict:
    """Нормализовать разбор недели (ТЗ §5.3): тексты обрезать, изменения проверить.

    catalog_map — каталог разрешённых упражнений (любой поддерживаемый вид). Если
    он задан, exercise_slug/new_slug обязаны в нём находиться (фаззи-матч по
    slug/имени), swap допустим только внутри одной группы мышц. Неизвестные типы
    и slug отбрасываются, значения clamp: weight_pct [−15, +10], sets ±1,
    rest_sec [20, 300]; одна переменная на упражнение; не более 5 изменений.
    """
    data = data if isinstance(data, dict) else {}
    cat = catalog_map if isinstance(catalog_map, _Catalog) else _Catalog(catalog_map)

    changes: list[dict] = []
    seen_slugs: set = set()
    has_deload = False
    for raw in (data.get("changes") if isinstance(data.get("changes"), list) else [])[:20]:
        if not isinstance(raw, dict):
            continue
        change_type = _clean_str(raw.get("type"), 24).lower()
        if change_type not in REVIEW_CHANGE_TYPES:
            continue
        reason = _clean_str(raw.get("reason"), 200)

        if change_type == "deload_next_week":
            if has_deload:
                continue
            has_deload = True
            changes.append({
                "id": len(changes), "type": change_type, "exercise_slug": None, "exercise_id": None,
                "exercise_name_ru": None, "exercise_name_en": None, "value": 1,
                "new_slug": None, "new_exercise_id": None, "new_exercise_name_ru": None,
                "new_exercise_name_en": None, "reason": reason,
            })
            if len(changes) >= MAX_REVIEW_CHANGES:
                break
            continue

        raw_slug = raw.get("exercise_slug") or raw.get("slug")
        if cat:
            slug = cat.resolve(raw_slug, raw.get("exercise_name"), raw.get("exercise_name_en"), raw.get("exercise_name_ru"))
        else:
            slug = _norm_slug(raw_slug)
        if not slug or slug in seen_slugs:
            continue

        value = None
        new_slug = None
        if change_type == "weight_pct":
            value = _clamp(_coerce_int(raw.get("value"), 0), WEIGHT_PCT_RANGE[0], WEIGHT_PCT_RANGE[1])
            if value == 0:
                continue
        elif change_type == "sets":
            delta = _coerce_int(raw.get("value"), 0)
            if delta == 0:
                continue
            value = 1 if delta > 0 else -1
        elif change_type == "rest_sec":
            # Абсолютное время отдыха: без числа (или ≤0) правка бессмысленна — отбрасываем,
            # иначе clamp до 20 с превратил бы «пропущенное значение» в реальную правку.
            seconds = _coerce_int(raw.get("value"), 0)
            if seconds <= 0:
                continue
            value = _clamp(seconds, REST_RANGE[0], REST_RANGE[1])
        elif change_type == "swap":
            raw_new = raw.get("new_slug") or raw.get("new_exercise_slug")
            if cat:
                new_slug = cat.resolve(raw_new, raw.get("new_exercise_name"), raw.get("new_name"))
                if not new_slug or new_slug == slug:
                    continue
                old_muscle = cat.field(slug, "muscle_group")
                new_muscle = cat.field(new_slug, "muscle_group")
                if old_muscle and new_muscle and old_muscle != new_muscle:
                    continue
            else:
                new_slug = _norm_slug(raw_new)
                if not new_slug or new_slug == slug:
                    continue

        seen_slugs.add(slug)
        brief = cat.brief(slug) if cat else {"exercise_id": None, "name_ru": None, "name_en": None}
        new_brief = cat.brief(new_slug) if (cat and new_slug) else {"exercise_id": None, "name_ru": None, "name_en": None}
        changes.append({
            "id": len(changes),
            "type": change_type,
            "exercise_slug": slug,
            "exercise_id": brief["exercise_id"],
            "exercise_name_ru": brief["name_ru"],
            "exercise_name_en": brief["name_en"],
            "value": value,
            "new_slug": new_slug,
            "new_exercise_id": new_brief["exercise_id"],
            "new_exercise_name_ru": new_brief["name_ru"],
            "new_exercise_name_en": new_brief["name_en"],
            "reason": reason,
        })
        if len(changes) >= MAX_REVIEW_CHANGES:
            break

    return {
        "summary": _clean_str(data.get("summary"), 600),
        "wins": _str_list(data.get("wins"), 5, 200),
        "issues": _str_list(data.get("issues"), 5, 200),
        "nutrition": _str_list(data.get("nutrition"), 5, 200),
        "changes": changes,
        "next_week_focus": _clean_str(data.get("next_week_focus"), 300),
        "motivation": _clean_str(data.get("motivation"), 300),
    }


def _stats_json(stats: dict, limit: int = MAX_STATS_CHARS) -> str:
    """Компактный JSON статистики для промпта (не-сериализуемое → str, обрезка)."""
    try:
        text = json.dumps(stats, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = "{}"
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def weekly_review(stats: dict, lang: str = "ru", catalog=None) -> dict:
    """Недельный разбор тренера (ТЗ §5.3). Тег trainer_review, 1500 токенов.

    stats — контекст от trainer_logic.collect_week_stats: программа, план vs факт,
    сессии, упражнения план→факт, сеты по мышцам, ккал/белок, вес, ограничения,
    и каталог разрешённых slug для swap (ключ "catalog" / "allowed_slugs" / ...
    либо параметр catalog). Отдаётся модели как JSON, каталог — строками.

    Возвращает {summary, wins, issues, nutrition, changes, next_week_focus,
    motivation}; при пустом результате — AIError.
    """
    lang = _normalize_lang(lang)
    stats = stats if isinstance(stats, dict) else {}

    catalog_source = catalog
    rest = {}
    for key, value in stats.items():
        if key in _STATS_CATALOG_KEYS:
            if catalog_source is None:
                catalog_source = value
            continue
        rest[key] = value
    cat = _Catalog(catalog_source)

    if lang == "en":
        parts = ["Week data (JSON):\n" + _stats_json(rest)]
        if cat:
            parts.append("Allowed slugs for swap (slug | name_en | muscle | equipment | measure | difficulty):\n" + cat.lines())
        parts.append("Review the week and propose changes. Return the result strictly as JSON per the instructions.")
    else:
        parts = ["Данные недели (JSON):\n" + _stats_json(rest)]
        if cat:
            parts.append("Разрешённые slug для swap (slug | name_en | muscle | equipment | measure | difficulty):\n" + cat.lines())
        parts.append("Разбери неделю и предложи правки. Верни результат строго в формате JSON по инструкции.")

    system_prompt = _pick_prompt(REVIEW_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT_EN, lang)
    data, _debug = ai_service._run_text_completion(
        system_prompt, "\n".join(parts), log_tag=TAG_REVIEW, max_tokens=MAX_TOKENS_REVIEW
    )
    _debug = _debug if isinstance(_debug, dict) else {}
    result = normalize_review(data, cat)
    if not result["summary"] and not result["wins"] and not result["changes"]:
        raise AIError(
            "AI не вернул разбор недели",
            raw=_debug.get("raw", ""),
            finish_reason=_debug.get("finish_reason"),
            refusal=_debug.get("refusal"),
        )
    return result


# --------------------------------------------------------------------------- #
#  §5.4 Совет дня по питанию
# --------------------------------------------------------------------------- #
def normalize_tip(data, kind: str = "training") -> dict:
    """Нормализовать совет дня: строки обрезать, tips ≤3, в день отдыха pre/post → None."""
    data = data if isinstance(data, dict) else {}
    training = kind == "training"
    pre = _clean_str(data.get("pre_workout"), 300) or None
    post = _clean_str(data.get("post_workout"), 300) or None
    return {
        "headline": _clean_str(data.get("headline"), 160),
        "calories_note": _clean_str(data.get("calories_note"), 300),
        "protein_note": _clean_str(data.get("protein_note"), 300),
        "pre_workout": pre if training else None,
        "post_workout": post if training else None,
        "hydration": _clean_str(data.get("hydration"), 200),
        "tips": _str_list(data.get("tips"), 3, 200),
    }


_TIP_KNOWN_KEYS = (
    "kind", "workout_title", "title", "workout_type", "session_type", "duration_min",
    "calories_burned", "status", "when", "time", "diet_goal", "daily_goal_kcal",
    "target_proteins", "eaten_kcal", "eaten_protein", "burned_kcal", "weight",
)


def nutrition_day_tip(ctx: dict, lang: str = "ru") -> dict:
    """Совет дня по питанию (ТЗ §5.4). Тег trainer_nutrition, 500 токенов.

    ctx: kind (training|rest), workout_title/title, workout_type/session_type,
    duration_min, calories_burned, status/when (planned|done), diet_goal,
    daily_goal_kcal, target_proteins, eaten_kcal, eaten_protein, burned_kcal,
    weight. Прочие ключи уходят в промпт как JSON.

    Возвращает {kind, headline, calories_note, protein_note, pre_workout,
    post_workout, hydration, tips}; без заголовка и советов — AIError.
    """
    lang = _normalize_lang(lang)
    en = lang == "en"
    ctx = ctx if isinstance(ctx, dict) else {}
    kind = "training" if _clean_str(ctx.get("kind"), 20).lower() == "training" else "rest"

    parts: list[str] = []
    parts.append(("Day type: training day." if kind == "training" else "Day type: rest day.") if en
                 else ("Тип дня: тренировочный." if kind == "training" else "Тип дня: день отдыха."))
    if kind == "training":
        bits = []
        title = _clean_str(ctx.get("workout_title") or ctx.get("title"), 80)
        wtype = _clean_str(ctx.get("workout_type") or ctx.get("session_type"), 20)
        if title:
            bits.append(title + (f" ({wtype})" if wtype else ""))
        elif wtype:
            bits.append(wtype)
        minutes = _coerce_int(ctx.get("duration_min"), 0)
        if minutes > 0:
            bits.append(f"{minutes} min" if en else f"{minutes} мин")
        burned = _coerce_int(ctx.get("calories_burned"), 0)
        if burned > 0:
            bits.append(f"about {burned} kcal" if en else f"около {burned} ккал")
        status = _clean_str(ctx.get("status") or ctx.get("when"), 20).lower()
        if status:
            done = status in ("done", "completed", "finished")
            bits.append(("already done" if done else "planned") if en else ("уже выполнена" if done else "по плану"))
        when = _clean_str(ctx.get("time"), 20)
        if when:
            bits.append((f"at {when}" if en else f"в {when}"))
        if bits:
            parts.append(("Workout: " if en else "Тренировка: ") + ", ".join(bits) + ".")

    diet_goal = _clean_str(ctx.get("diet_goal"), 20).lower()
    goal_kcal = _coerce_int(ctx.get("daily_goal_kcal"), 0)
    protein_goal = _coerce_int(ctx.get("target_proteins"), 0)
    bits = []
    if diet_goal:
        bits.append(("diet goal " if en else "цель ") + _label(_DIET_GOAL_NAMES, diet_goal, lang))
    if goal_kcal > 0:
        bits.append(f"daily target {goal_kcal} kcal" if en else f"норма {goal_kcal} ккал")
    if protein_goal > 0:
        bits.append(f"protein target {protein_goal} g" if en else f"белок {protein_goal} г")
    if bits:
        parts.append(("Nutrition plan: " if en else "План питания: ") + ", ".join(bits) + ".")

    eaten_kcal = _coerce_int(ctx.get("eaten_kcal"), 0)
    eaten_protein = _coerce_int(ctx.get("eaten_protein"), 0)
    burned_kcal = _coerce_int(ctx.get("burned_kcal"), 0)
    bits = []
    if eaten_kcal > 0 or eaten_protein > 0:
        bits.append((f"{eaten_kcal} kcal, protein {eaten_protein} g" if en else f"{eaten_kcal} ккал, белок {eaten_protein} г"))
    if burned_kcal > 0:
        bits.append((f"burned {burned_kcal} kcal" if en else f"сожжено {burned_kcal} ккал"))
    parts.append((("Eaten today: " if en else "Съедено сегодня: ") + ", ".join(bits) + ".") if bits
                 else ("Nothing logged today yet." if en else "Сегодня пока ничего не записано."))

    weight = _coerce_float(ctx.get("weight"), 0.0)
    if weight > 0:
        parts.append(f"Body weight: {weight:g} kg." if en else f"Вес: {weight:g} кг.")

    other = {k: v for k, v in ctx.items() if k not in _TIP_KNOWN_KEYS and v not in (None, "", [], {})}
    if other:
        parts.append(("Extra (JSON): " if en else "Дополнительно (JSON): ") + _stats_json(other, 1500))
    parts.append("Return the result strictly as JSON per the instructions." if en
                 else "Верни результат строго в формате JSON по инструкции.")

    system_prompt = _pick_prompt(NUTRITION_SYSTEM_PROMPT, NUTRITION_SYSTEM_PROMPT_EN, lang)
    data, _debug = ai_service._run_text_completion(
        system_prompt, "\n".join(parts), log_tag=TAG_NUTRITION, max_tokens=MAX_TOKENS_NUTRITION
    )
    _debug = _debug if isinstance(_debug, dict) else {}
    result = normalize_tip(data, kind)
    if not result["headline"] and not result["tips"]:
        raise AIError(
            "AI не вернул совет по питанию",
            raw=_debug.get("raw", ""),
            finish_reason=_debug.get("finish_reason"),
            refusal=_debug.get("refusal"),
        )
    result["kind"] = kind
    return result
