"""
Чистая логика AI-тренера (docs/TRAINER_SPEC.md §4, §5.5): прогрессия, рекорды,
стрик, раскрытие программы по неделям, каталог для промпта, подбор альтернатив,
контекст для питания и недельного разбора.

Правило модуля: функции без побочных эффектов и без запросов к БД везде, где
ТЗ этого не требует. Несколько функций принимают `db: Session` параметром
(`collect_week_stats`, `today_training_context`, `apply_review_changes`,
обёртка `alternatives_for_db`) — они читают/правят только строки тренера и
ничего не знают о FastAPI. Модуль не импортирует main.py: копии хелперов
`_weekdays_to_csv / _csv_to_weekdays / _normalize_time` живут здесь
(`_normalize_time` бросает ValueError вместо HTTPException — маршрут сам
превращает её в 400/422).

Соглашения:
  * «запись» (профиль, упражнение, состояние, сет, пункт плана) — dict ИЛИ
    ORM-объект: поля читаются через `_get`, JSON-колонки (`*_json`) разбираются
    на лету через `_list_field`;
  * все тексты для пользователя — только через `EXPLAIN_TEXTS` и
    `explain_changes(changes, lang)` (RU/EN по `lang`);
  * веса округляются к шагу оборудования (`weight_step` / `round_to_step`).

Схема изменения (элемент списка `changes`, совпадает с TrainerChangeOut §4.3):
  {exercise_id, slug, name_ru, name_en, kind: weight|reps|sets|keep|deload,
   old_value, new_value, reason}
`reason` — ключ шаблона в EXPLAIN_TEXTS, по нему строится строка объяснения.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Коды и константы (ТЗ §2–§3, §5.5)
# --------------------------------------------------------------------------- #
MUSCLE_GROUPS = (
    "chest", "back", "shoulders", "biceps", "triceps", "quads", "hamstrings",
    "glutes", "calves", "core", "full_body", "cardio", "mobility",
)
# Группы, по которым считаем рабочие сеты/объём (без кардио и мобильности).
STRENGTH_GROUPS = (
    "chest", "back", "shoulders", "biceps", "triceps", "quads", "hamstrings",
    "glutes", "calves", "core",
)
LOWER_BODY_GROUPS = frozenset({"quads", "hamstrings", "glutes", "calves"})
EQUIPMENT_CODES = (
    "barbell", "dumbbell", "machine", "cable", "bodyweight", "band",
    "kettlebell", "pullup_bar", "bench", "cardio_machine", "none",
)
EQUIPMENT_PROFILES = ("gym", "home_dumbbells", "bodyweight")
EQUIPMENT_EXTRA_CODES = ("pullup_bar", "bands", "bench", "kettlebell", "barbell", "cardio_machine")
GOALS = ("loss", "muscle", "strength", "endurance", "tone")
LEVELS = ("beginner", "intermediate", "advanced")
LIMITATION_CODES = ("knee", "lower_back", "shoulder", "wrist", "neck", "hip", "pregnancy", "heart_bp", "none")
FOCUS_CODES = ("glutes", "core", "back", "chest", "shoulders", "arms", "legs", "none")
SESSION_MINUTES = (20, 30, 45, 60, 75, 90)
PROGRAM_WEEKS = (4, 6, 8)
DAYS_PER_WEEK = (2, 3, 4, 5, 6)
FEEDBACKS = ("easy", "ok", "hard")
RESULTS = ("success", "partial", "fail")
REPLACE_REASONS = ("busy", "no_equipment", "pain", "other")
PHASES = ("base", "build", "peak", "deload")
PHASE_LABELS = {
    "base": ("База", "Base"),
    "build": ("Рост", "Build"),
    "peak": ("Пик", "Peak"),
    "deload": ("Разгрузка", "Deload"),
}
RECORD_TYPES = ("max_weight", "est_1rm", "max_reps", "set_volume", "max_time")
SESSION_TYPES = ("strength", "cardio", "mixed", "mobility")
CATEGORIES = ("compound", "isolation", "cardio", "mobility", "stretch")
MEASURE_TYPES = ("reps_weight", "reps", "time", "distance")

# Обязательные записи библиотеки: подставляются при отсутствии разминки/заминки.
REQUIRED_WARMUP_SLUG = "warmup_general_5min"
REQUIRED_COOLDOWN_SLUG = "stretch_full_body_5min"
DEFAULT_AUX_SEC = 300

# Разгрузочная неделя: −15% веса, −1 сет (ТЗ §4.3, §5.1).
DELOAD_WEIGHT_PCT = 85
DELOAD_SETS_DELTA = -1

SETS_RANGE = (1, 6)
REPS_RANGE = (1, 30)
REST_RANGE = (20, 300)
WEIGHT_RANGE = (0.0, 300.0)
WEIGHT_PCT_RANGE = (-15, 10)
FEEDBACK_SETS_CAP = 5            # «easy → компаунды +1 сет (max 5)»
BODYWEIGHT_REPS_CAP = 25         # дальше — «пора усложнять»
TIME_CAP_SEC = 300
MUSCLE_SETS_TARGET = (10, 20)    # рекомендуемая зона рабочих сетов в неделю
MAX_CATALOG_LINES = 110
MAX_ALTERNATIVES = 5

# session_type → Workout.type (ТЗ §3, «Связи с существующими сущностями»).
WORKOUT_TYPE_BY_SESSION = {
    "strength": "strength",
    "mixed": "strength",
    "cardio": "cardio",
    "mobility": "yoga",
}

# Базовый набор оборудования по варианту «Где и с чем» (§2.2 шаг 3).
EQUIPMENT_BY_PROFILE = {
    "gym": frozenset(EQUIPMENT_CODES),
    "home_dumbbells": frozenset({"dumbbell", "bodyweight", "band", "none"}),
    "bodyweight": frozenset({"bodyweight", "none"}),
}
# Доп. чипы онбординга → код оборудования библиотеки (chip «bands» = equipment «band»).
EXTRA_TO_EQUIPMENT = {
    "pullup_bar": "pullup_bar",
    "bands": "band",
    "band": "band",
    "bench": "bench",
    "kettlebell": "kettlebell",
    "barbell": "barbell",
    "cardio_machine": "cardio_machine",
    "dumbbells": "dumbbell",
    "dumbbell": "dumbbell",
}

# Колонки TrainerProgramDay, которые заполняет expand_program (кроме id/telegram_id/
# program_id/session_id/created_at — их ставит маршрут).
DAY_COLUMNS = (
    "week", "day_index", "weekday", "scheduled_date", "title", "session_type",
    "duration_min", "focus_muscles_json", "warmup_json", "exercises_json",
    "cooldown_json", "adjustments_json", "status",
)


# --------------------------------------------------------------------------- #
#  Тексты объяснений RU/EN (ТЗ §5.5 explain_changes)
#  Плейсхолдеры: {name} {old} {new} {reps} {pct} {weight}
# --------------------------------------------------------------------------- #
EXPLAIN_TEXTS: dict[str, tuple[str, str]] = {
    "weight_up": (
        "{name}: {old} → {new} кг — цель по повторам выполнена, добавляем вес",
        "{name}: {old} → {new} kg — rep target hit, adding weight",
    ),
    "weight_up_easy": (
        "{name}: {old} → {new} кг — было легко",
        "{name}: {old} → {new} kg — it felt easy",
    ),
    "weight_up_double": (
        "{name}: {old} → {new} кг — было легко, +2 шага",
        "{name}: {old} → {new} kg — it felt easy, +2 steps",
    ),
    "keep_hard": (
        "{name}: оставляем {old} кг — было тяжело",
        "{name}: keeping {old} kg — it felt hard",
    ),
    "keep_fail": (
        "{name}: оставляем {old} кг, цель {reps} повторов",
        "{name}: keeping {old} kg, target {reps} reps",
    ),
    "reps_up": (
        "{name}: вес {weight} кг, цель +1 повтор в каждом подходе ({new})",
        "{name}: weight {weight} kg, aim for +1 rep per set ({new})",
    ),
    "deload_two_fails": (
        "{name}: {old} → {new} кг (−10% после двух неудач)",
        "{name}: {old} → {new} kg (−10% after two misses)",
    ),
    "deload_hard": (
        "{name}: {old} → {new} кг — было тяжело, снижаем",
        "{name}: {old} → {new} kg — it felt hard, backing off",
    ),
    "bw_reps_up": (
        "{name}: цель {old} → {new} повторов",
        "{name}: target {old} → {new} reps",
    ),
    "bw_reps_cap": (
        "{name}: {old} повторов — пора усложнять упражнение",
        "{name}: {old} reps — time for a harder variation",
    ),
    "bw_reps_down": (
        "{name}: цель {old} → {new} повторов — упрощаем",
        "{name}: target {old} → {new} reps — easing off",
    ),
    "bw_keep": (
        "{name}: оставляем цель {old} повторов",
        "{name}: keeping the target of {old} reps",
    ),
    "time_up": (
        "{name}: {old} → {new} с",
        "{name}: {old} → {new} s",
    ),
    "time_keep": (
        "{name}: оставляем {old} с",
        "{name}: keeping {old} s",
    ),
    "time_down": (
        "{name}: {old} → {new} с — снижаем",
        "{name}: {old} → {new} s — backing off",
    ),
    "sets_up": (
        "{name}: {old} → {new} подходов",
        "{name}: {old} → {new} sets",
    ),
    "sets_down": (
        "{name}: {old} → {new} подходов",
        "{name}: {old} → {new} sets",
    ),
    "weight_pct": (
        "{name}: вес {old} → {new} кг ({pct}%)",
        "{name}: weight {old} → {new} kg ({pct}%)",
    ),
    "weight_pct_only": (
        "{name}: вес {pct}% на следующей неделе",
        "{name}: weight {pct}% next week",
    ),
    "swap": (
        "{name} → {new}",
        "{name} → {new}",
    ),
    "rest_sec": (
        "{name}: отдых {old} → {new} с",
        "{name}: rest {old} → {new} s",
    ),
    "deload_next_week": (
        "Следующая неделя — разгрузочная: −15% веса, −1 подход",
        "Next week is a deload: −15% weight, −1 set",
    ),
    "keep": (
        "{name}: без изменений",
        "{name}: no change",
    ),
}


# --------------------------------------------------------------------------- #
#  Мелкие хелперы доступа к записям (dict / ORM) и разбора значений
# --------------------------------------------------------------------------- #
def _get(obj, name: str, default=None):
    """Поле записи: dict → ключ, ORM/объект → атрибут; None → default."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        value = obj.get(name, default)
    else:
        value = getattr(obj, name, default)
    return default if value is None else value


def _set(obj, name: str, value) -> None:
    """Записать поле в dict или ORM-объект."""
    if isinstance(obj, dict):
        obj[name] = value
    else:
        setattr(obj, name, value)


def _as_list(value) -> list:
    """Список из чего угодно: list/tuple/set, JSON-строка, CSV-строка, одиночное значение."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text[0] in "[{":
            try:
                parsed = json.loads(text)
            except ValueError:
                return []
            if isinstance(parsed, list):
                return parsed
            return [parsed] if isinstance(parsed, dict) else []
        return [part.strip() for part in text.split(",") if part.strip()]
    return [value]


def _list_field(obj, name: str) -> list:
    """Поле-список записи: `name` (list) либо `name_json` (JSON в Text)."""
    value = _get(obj, name)
    if value is None:
        value = _get(obj, name + "_json")
    return _as_list(value)


def _json_dict(value) -> dict:
    """dict из JSON-строки/dict; мусор → {}."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _to_float(value, default=None):
    if value is None or isinstance(value, bool):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result != result:  # NaN
        return default
    return result


def _to_int(value, default=None):
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _parse_date(value):
    """date из date/datetime/ISO-строки ("2026-09-08" или с временем); мусор → None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _fmt_num(value) -> str:
    """Число для текста: 12.0 → «12», 12.5 → «12.5», None → «?»."""
    number = _to_float(value)
    if number is None:
        return "?" if value is None else str(value)
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.1f}".rstrip("0").rstrip(".")


def _is_en(lang) -> bool:
    return str(lang or "ru").strip().lower().startswith("en")


class _SafeDict(dict):
    """format_map без KeyError: неизвестный плейсхолдер остаётся пустым."""

    def __missing__(self, key):
        return ""


def _same_exercise(row, exercise_id, slug) -> bool:
    """Совпадает ли пункт плана/правки с упражнением (по id, иначе по slug)."""
    row_id = _to_int(_get(row, "exercise_id"))
    if row_id is not None and exercise_id is not None:
        return row_id == _to_int(exercise_id)
    row_slug = _get(row, "slug") or _get(row, "exercise_slug")
    return bool(slug) and row_slug == slug


# --------------------------------------------------------------------------- #
#  Копии хелперов main.py (дни недели <-> CSV, время) — без импорта main
# --------------------------------------------------------------------------- #
def _weekdays_to_csv(weekdays) -> str:
    """Список дней недели (Пн=0..Вс=6) → CSV "0,2,4": только 0..6, без дублей, по порядку."""
    cleaned = set()
    for item in _as_list(weekdays):
        value = _to_int(item)
        if value is not None and 0 <= value <= 6:
            cleaned.add(value)
    return ",".join(str(d) for d in sorted(cleaned))


def _csv_to_weekdays(csv) -> list[int]:
    """CSV "0,2,4" → [0, 2, 4]; пустые/битые элементы молча пропускаем."""
    if not csv:
        return []
    result: list[int] = []
    for part in str(csv).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 6:
            result.append(value)
    return result


def _normalize_time(value) -> str:
    """Мягко привести время к "HH:MM" ("9:5" → "09:05"). Ошибка формата → ValueError."""
    if value is None:
        raise ValueError("Не указано время (HH:MM)")
    parts = str(value).strip().split(":")
    if len(parts) != 2:
        raise ValueError("Некорректное время, нужен формат HH:MM")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except (TypeError, ValueError):
        raise ValueError("Некорректное время, нужен формат HH:MM")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Некорректное время, нужен формат HH:MM")
    return f"{hour:02d}:{minute:02d}"


def default_weekdays(days_per_week) -> list[int]:
    """Дни недели по умолчанию для N тренировок (§2.2 шаг 4)."""
    table = {
        1: [0],
        2: [0, 3],
        3: [0, 2, 4],
        4: [0, 1, 3, 4],
        5: [0, 1, 2, 3, 4],
        6: [0, 1, 2, 3, 4, 5],
        7: [0, 1, 2, 3, 4, 5, 6],
    }
    return list(table.get(_clamp(_to_int(days_per_week, 3), 1, 7)))


def week_bounds(value=None) -> tuple[str, str]:
    """(понедельник, воскресенье) недели, содержащей дату, в ISO."""
    day = _parse_date(value) or date.today()
    monday = day - timedelta(days=day.weekday())
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def map_workout_type(session_type) -> str:
    """Тип сессии тренера → Workout.type: strength/mixed → strength, cardio → cardio, mobility → yoga."""
    return WORKOUT_TYPE_BY_SESSION.get(str(session_type or "").lower(), "strength")


def session_duration_min(elapsed_min, planned_min=None, requested=None) -> int:
    """Длительность сессии при finish: `requested` или clamp(elapsed, 10, max(planned×1.5, 30))."""
    wanted = _to_int(requested)
    if wanted is not None and wanted > 0:
        return _clamp(wanted, 1, 600)
    planned = _to_int(planned_min, 0) or 0
    upper = max(int(round(planned * 1.5)), 30)
    return _clamp(_to_int(elapsed_min, 0) or 0, 10, upper)


# --------------------------------------------------------------------------- #
#  §5.5 1RM, шаг веса, оценка упражнения
# --------------------------------------------------------------------------- #
def est_1rm(weight, reps):
    """Расчётный 1RM по Эпли: w × (1 + reps/30) при 1 ≤ reps ≤ 12, иначе None."""
    w = _to_float(weight)
    r = _to_int(reps)
    if w is None or r is None or w <= 0 or not (1 <= r <= 12):
        return None
    return round(w * (1 + r / 30.0), 1)


def weight_step(equipment, muscle_group=None) -> float:
    """Минимальный шаг веса по оборудованию (низ тела — крупнее шаг)."""
    lower = str(muscle_group or "") in LOWER_BODY_GROUPS
    code = str(equipment or "").lower()
    if code == "barbell":
        return 5.0 if lower else 2.5
    if code == "dumbbell":
        return 2.0
    if code in ("machine", "cable"):
        return 5.0 if lower else 2.5
    if code == "kettlebell":
        return 4.0
    return 2.5


def round_to_step(value, step=2.5):
    """Округлить вес к ближайшему кратному шага (половина — вверх, без хвостов float).

    Именно «половина вверх», а не банковское round(): 41 кг при шаге 2 → 42,
    а не 40 — так ожидает человек у стойки с блинами.
    """
    number = _to_float(value)
    if number is None:
        return None
    size = _to_float(step) or 2.5
    if size <= 0:
        return round(number, 2)
    return round(math.floor(number / size + 0.5) * size, 2)


def _done_work_sets(sets) -> list:
    """Только рабочие выполненные сеты (warmup/drop/failure и не отмеченные — мимо)."""
    out = []
    for item in _as_list(sets):
        if (_get(item, "set_type") or "work") != "work":
            continue
        done = _get(item, "is_done")
        if done is False:
            continue
        out.append(item)
    return out


def evaluate_exercise(planned, sets_done) -> str:
    """Итог упражнения по плану и выполненным сетам: success | partial | fail.

    success — все запланированные рабочие сеты выполнены и в каждом reps ≥ reps_max
    (для time: time_sec ≥ цели); partial — в каждом выполненном сете reps ≥ reps_min
    (time ≥ 70% цели); иначе fail. Пропущенные сеты не делают результат fail, если
    выполненные дотянули до reps_min: снижение веса после двух fail — жёсткая
    мера, и одного недоделанного подхода для неё мало.
    """
    done = _done_work_sets(sets_done)
    if not done:
        return "fail"
    measure = str(_get(planned, "measure_type") or "reps_weight")
    planned_sets = _to_int(_get(planned, "sets") or _get(planned, "planned_sets"), 0) or 0
    if measure in ("time", "distance"):
        target = _to_int(_get(planned, "time_sec") or _get(planned, "planned_time_sec"), 0) or 0
        values = [_to_int(_get(s, "time_sec"), 0) or 0 for s in done]
        if target <= 0:
            return "success" if len(done) >= planned_sets else "partial"
        if len(done) >= planned_sets and all(v >= target for v in values):
            return "success"
        if all(v >= target * 0.7 for v in values):
            return "partial"
        return "fail"
    lo = _to_int(_get(planned, "reps_min") or _get(planned, "planned_reps_min"), 0) or 0
    hi = _to_int(_get(planned, "reps_max") or _get(planned, "planned_reps_max"), 0) or 0
    if hi < lo:
        hi = lo
    reps = [_to_int(_get(s, "reps"), 0) or 0 for s in done]
    if lo <= 0 and hi <= 0:
        return "success" if len(done) >= planned_sets else "partial"
    if hi > 0 and len(done) >= planned_sets and all(r >= hi for r in reps):
        return "success"
    if lo > 0 and all(r >= lo for r in reps):
        return "partial"
    if lo <= 0 and all(r >= hi for r in reps):
        return "partial"
    return "fail"


def median_weight(sets) -> float | None:
    """Медиана веса по выполненным рабочим сетам (для TrainerExerciseState.working_weight_kg)."""
    weights = []
    for item in _done_work_sets(sets):
        w = _to_float(_get(item, "weight_kg"))
        if w is not None and w > 0:
            weights.append(w)
    if not weights:
        return None
    return round(statistics.median(weights), 2)


# --------------------------------------------------------------------------- #
#  §5.5 Прогрессия: следующие цели, объяснения, модификаторы от отзыва
# --------------------------------------------------------------------------- #
def next_targets(state, exercise, result, feedback=None, level=None, sets_done=None) -> dict:
    """Следующие цели по упражнению по правилам ТЗ §5.5 (без ИИ).

    Параметры:
      * state    — TrainerExerciseState (dict/ORM) или None для первого раза;
      * exercise — запись упражнения, дополненная планом дня: equipment, muscle_group,
                   measure_type, id, slug, name_ru/name_en, а также reps_min/reps_max
                   (диапазон программы), start_weight_kg/planned_weight_kg, time_sec;
      * result   — success | partial | fail (evaluate_exercise);
      * feedback — easy | ok | hard | None (None считаем «ok»);
      * level    — уровень пользователя (beginner → easy на низ тела даёт +2 шага);
      * sets_done — выполненные сеты (пока только для контекста строк).

    Возвращает {working_weight_kg, target_reps_min, target_reps_max, target_time_sec,
    success_streak, fail_streak, last_result, changes: [...]} — значения для upsert
    состояния и список изменений для explain_changes.
    """
    result = result if result in RESULTS else "partial"
    feedback = feedback if feedback in FEEDBACKS else "ok"
    measure = str(_get(exercise, "measure_type") or "reps_weight")
    equipment = _get(exercise, "equipment") or "none"
    muscle = _get(exercise, "muscle_group") or ""
    lower = muscle in LOWER_BODY_GROUPS

    range_min = _to_int(_get(exercise, "reps_min") or _get(exercise, "planned_reps_min"))
    range_max = _to_int(_get(exercise, "reps_max") or _get(exercise, "planned_reps_max"))
    weight = _to_float(_get(state, "working_weight_kg"))
    if weight is None:
        weight = _to_float(
            _get(exercise, "start_weight_kg") or _get(exercise, "planned_weight_kg") or _get(exercise, "weight_kg")
        )
    if weight is not None and weight <= 0:
        weight = None
    reps_min = _to_int(_get(state, "target_reps_min")) or range_min
    reps_max = _to_int(_get(state, "target_reps_max")) or range_max or reps_min
    if reps_min and reps_max and reps_max < reps_min:
        reps_max = reps_min
    time_target = _to_int(_get(state, "target_time_sec")) or _to_int(
        _get(exercise, "time_sec") or _get(exercise, "planned_time_sec")
    )
    succ = _to_int(_get(state, "success_streak"), 0) or 0
    fails = _to_int(_get(state, "fail_streak"), 0) or 0

    meta = {
        "exercise_id": _get(exercise, "id") or _get(exercise, "exercise_id"),
        "slug": _get(exercise, "slug"),
        "name_ru": _get(exercise, "name_ru"),
        "name_en": _get(exercise, "name_en"),
    }
    changes: list[dict] = []

    def change(kind: str, reason: str, old, new, **extra) -> None:
        row = dict(meta)
        row.update({"kind": kind, "reason": reason, "old_value": old, "new_value": new})
        row.update(extra)
        changes.append(row)

    if measure in ("time", "distance"):
        # Упражнения на время: +10–15 с при успехе, ×0.9 после двух неудач.
        target = time_target or 30
        if result == "success":
            succ += 1
            fails = 0
            if feedback == "hard":
                change("keep", "time_keep", target, target)
            else:
                inc = 15 if (feedback == "easy" or target > 30) else 10
                new = min(TIME_CAP_SEC, target + inc)
                if new > target:
                    change("reps", "time_up", target, new)
                else:
                    change("keep", "time_keep", target, target)
                target = new
        elif result == "partial":
            succ = 0
            fails = 0
            change("keep", "time_keep", target, target)
        else:
            succ = 0
            fails += 1
            if feedback == "hard" or fails >= 2:
                new = max(10, int(round(target * 0.9 / 5.0)) * 5)
                if new >= target:
                    new = max(10, target - 5)
                change("deload", "time_down", target, new)
                target = new
                fails = 0
            else:
                change("keep", "time_keep", target, target)
        time_target = target

    elif measure == "reps" or weight is None:
        # Вес тела: прогрессия только повторами (+2 при успехе, cap 25).
        target = reps_min or 10
        top = max(reps_max or target, target)
        if result == "success":
            succ += 1
            fails = 0
            if feedback == "hard":
                change("keep", "bw_keep", target, target)
            elif target >= BODYWEIGHT_REPS_CAP:
                change("keep", "bw_reps_cap", target, target)
            else:
                new = min(BODYWEIGHT_REPS_CAP, target + 2)
                change("reps", "bw_reps_up", target, new)
                top = min(REPS_RANGE[1], top + (new - target))
                target = new
        elif result == "partial":
            succ = 0
            fails = 0
            if target >= BODYWEIGHT_REPS_CAP:
                change("keep", "bw_reps_cap", target, target)
            else:
                new = min(BODYWEIGHT_REPS_CAP, target + (2 if feedback == "easy" else 1))
                change("reps", "bw_reps_up", target, new)
                target = new
        else:
            succ = 0
            fails += 1
            if feedback == "hard" or fails >= 2:
                new = max(1, int(round(target * 0.9)))
                if new >= target:
                    new = max(1, target - 1)
                change("deload", "bw_reps_down", target, new)
                target = new
                fails = 0
            else:
                change("keep", "bw_keep", target, target)
        reps_min = target
        reps_max = max(top, target)

    else:
        # Вес + повторы: «повторы сначала, потом вес», откат −10% после двух неудач.
        step = weight_step(equipment, muscle)
        if reps_min is None:
            reps_min = 8
        if reps_max is None or reps_max < reps_min:
            reps_max = max(reps_min, 12)
        if result == "success":
            succ += 1
            fails = 0
            if feedback == "hard":
                change("keep", "keep_hard", weight, weight)
            else:
                double = feedback == "easy" and lower and str(level or "") == "beginner"
                new = round_to_step(weight + step * (2 if double else 1), step)
                if new <= weight:
                    new = round_to_step(weight + step, step)
                reason = "weight_up_double" if double else ("weight_up_easy" if feedback == "easy" else "weight_up")
                change("weight", reason, weight, new, reps=range_min or reps_min)
                weight = new
                if range_min:
                    reps_min = range_min
                if range_max:
                    reps_max = max(range_max, reps_min)
        elif result == "partial":
            succ = 0
            fails = 0
            if feedback == "easy":
                new = round_to_step(weight + step, step)
                change("weight", "weight_up_easy", weight, new, reps=range_min or reps_min)
                weight = new
                if range_min:
                    reps_min = range_min
            else:
                new_reps = reps_min + 1
                if new_reps > reps_max:
                    reps_max = new_reps
                change("reps", "reps_up", reps_min, new_reps, weight=weight)
                reps_min = new_reps
        else:
            succ = 0
            fails += 1
            if feedback == "hard" or fails >= 2:
                new = round_to_step(weight * 0.9, step)
                if new is None or new >= weight:
                    new = round_to_step(weight - step, step)
                new = max(0.0, new or 0.0)
                change("deload", "deload_hard" if feedback == "hard" else "deload_two_fails", weight, new)
                weight = new
                fails = 0
            else:
                change("keep", "keep_fail", weight, weight, reps=reps_min)

    return {
        "working_weight_kg": weight,
        "target_reps_min": reps_min,
        "target_reps_max": reps_max,
        "target_time_sec": time_target,
        "success_streak": succ,
        "fail_streak": fails,
        "last_result": result,
        "changes": changes,
    }


def explain_changes(changes, lang="ru") -> list[str]:
    """Строки объяснений для списка изменений по шаблонам EXPLAIN_TEXTS (RU/EN)."""
    en = _is_en(lang)
    lines: list[str] = []
    for ch in _as_list(changes):
        if not isinstance(ch, dict) and not hasattr(ch, "__dict__"):
            continue
        reason = _get(ch, "reason") or _get(ch, "kind") or "keep"
        template = EXPLAIN_TEXTS.get(reason) or EXPLAIN_TEXTS["keep"]
        name = (_get(ch, "name_en") if en else _get(ch, "name_ru")) or _get(ch, "name_ru") \
            or _get(ch, "name_en") or _get(ch, "slug") or ("Exercise" if en else "Упражнение")
        new_name = (_get(ch, "new_name_en") if en else _get(ch, "new_name_ru")) or _get(ch, "new_name_ru") \
            or _get(ch, "new_name_en") or _get(ch, "new_slug")
        new_value = _get(ch, "new_value")
        ctx = _SafeDict(
            name=name,
            old=_fmt_num(_get(ch, "old_value")),
            new=new_name if (reason == "swap" and new_name) else _fmt_num(new_value),
            reps=_fmt_num(_get(ch, "reps")),
            weight=_fmt_num(_get(ch, "weight")),
            pct=(f"{_to_int(_get(ch, 'pct'), 0):+d}" if _get(ch, "pct") is not None else ""),
        )
        try:
            lines.append(template[1 if en else 0].format_map(ctx))
        except (ValueError, IndexError):
            lines.append(str(name))
    return lines


def feedback_adjustments(feedback, exercise_results) -> dict | None:
    """Модификатор от отзыва для того же day_index на следующей неделе (ТЗ §5.5).

    exercise_results — [{exercise_id, slug, category, result}]. hard → изоляционные
    −1 сет, компаунды −5% (если упражнение не было success); easy → компаунды +1 сет
    (max 5, ограничение применяет apply_targets). Возвращает dict для
    adjustments_json дня: {"source": "feedback", "feedback", "items": [...]} или None.
    """
    feedback = feedback if feedback in FEEDBACKS else "ok"
    items: list[dict] = []
    for row in _as_list(exercise_results):
        category = str(_get(row, "category") or "compound")
        result = _get(row, "result")
        ref = {"exercise_id": _get(row, "exercise_id") or _get(row, "id"), "slug": _get(row, "slug")}
        if feedback == "hard":
            if category == "isolation":
                items.append(dict(ref, sets_delta=-1))
            elif category == "compound" and result != "success":
                items.append(dict(ref, weight_pct=-5))
        elif feedback == "easy" and category == "compound":
            items.append(dict(ref, sets_delta=1))
    if not items:
        return None
    return {"source": "feedback", "feedback": feedback, "items": items}


def merge_adjustments(existing, extra) -> dict:
    """Слить новые правки в adjustments_json дня (items дописываются, флаги перезаписываются)."""
    base = _json_dict(existing)
    if not extra:
        return base
    extra = _json_dict(extra)
    items = list(_as_list(base.get("items"))) + list(_as_list(extra.get("items")))
    for key, value in extra.items():
        if key != "items":
            base[key] = value
    if items:
        base["items"] = items
    return base


def week_modifier(periodization, week, weeks=None) -> dict:
    """Модификатор недели из периодизации: {week, phase, weight_pct, sets_delta, label_ru, label_en}.

    Отсутствующая неделя → base (100%, 0), последняя без явной фазы → deload (85%, −1).
    """
    week = _to_int(week, 1) or 1
    found = None
    for item in _as_list(periodization):
        if isinstance(item, dict) and _to_int(item.get("week")) == week:
            found = item
            break
    if found is not None:
        phase = found.get("phase") if found.get("phase") in PHASES else "base"
        pct = _to_int(found.get("weight_pct"), DELOAD_WEIGHT_PCT if phase == "deload" else 100)
        delta = _to_int(found.get("sets_delta"), DELOAD_SETS_DELTA if phase == "deload" else 0)
    elif weeks and week == _to_int(weeks):
        phase, pct, delta = "deload", DELOAD_WEIGHT_PCT, DELOAD_SETS_DELTA
    else:
        phase, pct, delta = "base", 100, 0
    label_ru, label_en = PHASE_LABELS[phase]
    return {
        "week": week,
        "phase": phase,
        "weight_pct": _clamp(pct, 50, 110),
        "sets_delta": _clamp(delta, -2, 2),
        "label_ru": label_ru,
        "label_en": label_en,
    }


def apply_targets(item, exercise, state=None, week_mod=None, adjustments=None) -> dict:
    """Цели упражнения на сессию: план дня + состояние + модификатор недели + правки дня.

    Порядок наложения (ТЗ §4.3 start): exercises_json → TrainerExerciseState
    (working_weight, target reps/time, rest) → periodization[week] (deload:
    вес×0.85, сеты−1) → adjustments_json дня (deload-флаг, items с sets_delta /
    weight_pct / rest_sec по упражнению). Возвращает planned_* поля
    TrainerSessionExercise плюс weight_pct/sets_delta для отладки.
    """
    measure = str(_get(exercise, "measure_type") or "reps_weight")
    equipment = _get(exercise, "equipment") or "none"
    muscle = _get(exercise, "muscle_group") or ""
    ex_id = _get(exercise, "id") or _get(item, "exercise_id")
    slug = _get(exercise, "slug") or _get(item, "slug")

    sets = _to_int(_get(item, "sets") or _get(item, "planned_sets"), 3) or 3
    reps_min = _to_int(_get(item, "reps_min") or _get(item, "planned_reps_min"))
    reps_max = _to_int(_get(item, "reps_max") or _get(item, "planned_reps_max"))
    time_sec = _to_int(_get(item, "time_sec") or _get(item, "planned_time_sec"))
    rest = _to_int(_get(item, "rest_sec") or _get(item, "planned_rest_sec"), 90) or 90
    weight = _to_float(_get(item, "start_weight_kg") or _get(item, "planned_weight_kg")) if measure == "reps_weight" else None
    rpe = _to_int(_get(item, "rpe") or _get(item, "planned_rpe"))
    note = _get(item, "note")

    if state is not None:
        w = _to_float(_get(state, "working_weight_kg"))
        if w is not None and w > 0 and measure == "reps_weight":
            weight = w
        r_min = _to_int(_get(state, "target_reps_min"))
        r_max = _to_int(_get(state, "target_reps_max"))
        if r_min:
            reps_min = r_min
        if r_max:
            reps_max = max(r_max, reps_min or r_max)
        t = _to_int(_get(state, "target_time_sec"))
        if t and measure in ("time", "distance"):
            time_sec = t
        r = _to_int(_get(state, "rest_sec"))
        if r:
            rest = r

    pct = 100
    sets_delta = 0
    if week_mod:
        pct = _to_int(_get(week_mod, "weight_pct"), 100) or 100
        sets_delta = _to_int(_get(week_mod, "sets_delta"), 0) or 0

    adj = _json_dict(adjustments)
    if adj.get("deload"):
        pct = min(pct, _to_int(adj.get("weight_pct"), DELOAD_WEIGHT_PCT) or DELOAD_WEIGHT_PCT)
        sets_delta += _to_int(adj.get("sets_delta"), DELOAD_SETS_DELTA) or 0
    extra_pct = 0
    extra_sets = 0
    for row in _as_list(adj.get("items")):
        if not isinstance(row, dict) or not _same_exercise(row, ex_id, slug):
            continue
        extra_pct += _to_int(row.get("weight_pct"), 0) or 0
        extra_sets += _to_int(row.get("sets_delta"), 0) or 0
        new_rest = _to_int(row.get("rest_sec"))
        if new_rest:
            rest = new_rest

    total_pct = pct + extra_pct
    step = weight_step(equipment, muscle)
    if weight is not None and total_pct != 100:
        weight = round_to_step(weight * total_pct / 100.0, step)
    new_sets = sets + sets_delta + extra_sets
    if extra_sets > 0:
        new_sets = min(new_sets, FEEDBACK_SETS_CAP)
    sets = _clamp(new_sets, SETS_RANGE[0], SETS_RANGE[1])
    rest = _clamp(rest, REST_RANGE[0], REST_RANGE[1])
    if reps_min and reps_max and reps_max < reps_min:
        reps_max = reps_min

    return {
        "planned_sets": sets,
        "planned_reps_min": reps_min,
        "planned_reps_max": reps_max,
        "planned_weight_kg": weight,
        "planned_time_sec": time_sec,
        "planned_rest_sec": rest,
        "planned_rpe": rpe,
        "note": note,
        "weight_pct": total_pct,
        "sets_delta": sets_delta + extra_sets,
    }


# --------------------------------------------------------------------------- #
#  §5.5 Рекорды, итоги сессии, стрик, объём по мышцам
# --------------------------------------------------------------------------- #
def detect_prs(records_by_type, set_log, exercise) -> list[dict]:
    """Кандидаты в рекорды по одному сету: [{type, value, prev_value, is_pr, weight_kg, reps}].

    records_by_type — {record_type: value | запись с .value} (текущие лучшие).
    Первая запись — не PR (is_pr=False, создать молча); is_pr только при улучшении.
    Только work-сеты с is_done; warmup/drop/failure игнорируются.
    """
    if (_get(set_log, "set_type") or "work") != "work" or _get(set_log, "is_done") is False:
        return []
    measure = str(_get(exercise, "measure_type") or "reps_weight")
    weight = _to_float(_get(set_log, "weight_kg"))
    reps = _to_int(_get(set_log, "reps"))
    time_sec = _to_int(_get(set_log, "time_sec"))

    candidates: list[tuple[str, float]] = []
    if measure == "reps_weight":
        if weight is not None and weight > 0 and reps and reps >= 1:
            candidates.append(("max_weight", weight))
            one_rm = est_1rm(weight, reps)
            if one_rm:
                candidates.append(("est_1rm", one_rm))
            candidates.append(("set_volume", round(weight * reps, 1)))
            candidates.append(("max_reps", float(reps)))
        elif reps and reps >= 1:
            candidates.append(("max_reps", float(reps)))
    elif measure == "reps":
        if reps and reps >= 1:
            candidates.append(("max_reps", float(reps)))
    elif measure in ("time", "distance"):
        if time_sec and time_sec > 0:
            candidates.append(("max_time", float(time_sec)))

    records = records_by_type if isinstance(records_by_type, dict) else {}
    out: list[dict] = []
    for record_type, value in candidates:
        prev = records.get(record_type)
        prev_value = _to_float(prev) if isinstance(prev, (int, float)) else _to_float(_get(prev, "value"))
        row = {
            "type": record_type,
            "value": value,
            "prev_value": prev_value,
            "is_pr": False,
            "weight_kg": weight,
            "reps": reps,
        }
        if prev_value is None:
            out.append(row)
        elif value > prev_value + 1e-9:
            row["is_pr"] = True
            out.append(row)
    return out


def session_totals(set_logs, session_exercises=None) -> dict:
    """Итоги сессии по выполненным рабочим сетам (разминка не в объём)."""
    total_sets = 0
    total_reps = 0
    volume = 0.0
    for item in _done_work_sets(set_logs):
        total_sets += 1
        reps = _to_int(_get(item, "reps"), 0) or 0
        total_reps += reps
        volume += (_to_float(_get(item, "weight_kg"), 0.0) or 0.0) * reps
    done = skipped = 0
    for row in _as_list(session_exercises):
        if (_get(row, "block") or "main") != "main":
            continue
        status = _get(row, "status")
        if status == "done":
            done += 1
        elif status == "skipped":
            skipped += 1
    return {
        "total_sets": total_sets,
        "total_reps": total_reps,
        "total_volume_kg": round(volume, 1),
        "exercises_done": done,
        "exercises_skipped": skipped,
    }


def weekly_streak(session_dates, today=None) -> int:
    """Недели Пн–Вс подряд с ≥1 завершённой сессией; текущая неделя засчитывается,
    если в ней уже есть сессия, а пустая текущая неделя стрик не обнуляет."""
    weeks: set[date] = set()
    for value in _as_list(session_dates):
        day = _parse_date(value)
        if day:
            weeks.add(day - timedelta(days=day.weekday()))
    now = _parse_date(today) or date.today()
    current = now - timedelta(days=now.weekday())
    streak = 0
    cursor = current
    if current in weeks:
        streak = 1
    cursor = current - timedelta(days=7)
    while cursor in weeks:
        streak += 1
        cursor -= timedelta(days=7)
    return streak


def pick_review_week_start(session_dates, today=None) -> str:
    """Понедельник недели для разбора: текущая, но если сегодня Пн/Вт и в текущей
    неделе 0 сессий — прошлая (ТЗ §4.5)."""
    now = _parse_date(today) or date.today()
    monday = now - timedelta(days=now.weekday())
    if now.weekday() in (0, 1):
        has_current = any(
            (d := _parse_date(v)) is not None and monday <= d <= monday + timedelta(days=6)
            for v in _as_list(session_dates)
        )
        if not has_current:
            monday -= timedelta(days=7)
    return monday.isoformat()


def muscle_volume_summary(set_logs, muscle_by_exercise_id) -> list[dict]:
    """Рабочие сеты и объём по группам мышц (work + is_done) с рекомендуемой зоной 10–20."""
    sets_by: dict[str, int] = {g: 0 for g in STRENGTH_GROUPS}
    volume_by: dict[str, float] = {g: 0.0 for g in STRENGTH_GROUPS}
    lookup = muscle_by_exercise_id if isinstance(muscle_by_exercise_id, dict) else {}
    for item in _done_work_sets(set_logs):
        ex_id = _to_int(_get(item, "exercise_id"))
        group = lookup.get(ex_id)
        if not isinstance(group, str):
            group = _get(group, "muscle_group")
        if group not in sets_by:
            continue
        sets_by[group] += 1
        reps = _to_int(_get(item, "reps"), 0) or 0
        volume_by[group] += (_to_float(_get(item, "weight_kg"), 0.0) or 0.0) * reps
    return [
        {
            "muscle_group": group,
            "sets": sets_by[group],
            "volume_kg": round(volume_by[group], 1),
            "target_min": MUSCLE_SETS_TARGET[0],
            "target_max": MUSCLE_SETS_TARGET[1],
        }
        for group in STRENGTH_GROUPS
    ]


# --------------------------------------------------------------------------- #
#  §5.1 Раскрытие программы по неделям
# --------------------------------------------------------------------------- #
def _default_aux_item(slug: str, seconds: int = DEFAULT_AUX_SEC) -> dict:
    return {"slug": slug, "exercise_id": None, "sets": 1, "reps": None, "time_sec": seconds, "note": None}


def expand_program(template, periodization, profile, start_date=None) -> list[dict]:
    """Раскрыть шаблон недели в weeks × days_per_week дней детерминированно.

    Даты — по preferred_weekdays профиля начиная с ближайшего дня ≥ start_date,
    дальше подряд по выбранным дням недели (день k → k-й подходящий день
    календаря); week = k // days_per_week + 1, day_index = k % days_per_week + 1,
    так что day_index всегда попадает на один и тот же день недели.
    Значения exercises_json остаются шаблонными — модификатор недели
    (phase/weight_pct/sets_delta) накладывается при старте сессии
    (`apply_targets`), а здесь только помечает день.

    Каждый элемент — колонки TrainerProgramDay (см. DAY_COLUMNS, JSON-поля уже
    сериализованы) плюс `phase`, `weight_pct`, `sets_delta`; `day_to_row`
    оставляет только колонки.
    """
    days_tpl = template.get("days") if isinstance(template, dict) else template
    days_tpl = [d for d in _as_list(days_tpl) if isinstance(d, dict)]
    if not days_tpl:
        return []
    dpw = _to_int(_get(profile, "days_per_week")) or len(days_tpl)
    dpw = _clamp(dpw, 1, 7)
    weeks = _to_int(_get(profile, "program_weeks")) or _to_int(_get(profile, "weeks"))
    if not weeks:
        weeks = max((_to_int(p.get("week"), 0) for p in _as_list(periodization) if isinstance(p, dict)), default=0) or 6
    weekdays = sorted({
        v for v in (_to_int(x) for x in _as_list(_get(profile, "preferred_weekdays"))) if v is not None and 0 <= v <= 6
    })
    if len(weekdays) != dpw:
        weekdays = default_weekdays(dpw)
    start = _parse_date(start_date) or date.today()
    minutes_default = _to_int(_get(profile, "session_minutes")) or 45

    dates: list[date] = []
    cursor = start
    total = weeks * dpw
    while len(dates) < total:
        if cursor.weekday() in weekdays:
            dates.append(cursor)
        cursor += timedelta(days=1)

    out: list[dict] = []
    for k, day in enumerate(dates):
        week = k // dpw + 1
        day_index = k % dpw + 1
        tpl = days_tpl[(day_index - 1) % len(days_tpl)]
        mod = week_modifier(periodization, week, weeks)

        exercises = []
        for order, item in enumerate(_as_list(tpl.get("exercises")), start=1):
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row["order"] = _to_int(row.get("order")) or order
            exercises.append(row)
        warmup = [dict(x) for x in _as_list(tpl.get("warmup")) if isinstance(x, dict)]
        cooldown = [dict(x) for x in _as_list(tpl.get("cooldown")) if isinstance(x, dict)]
        if not warmup:
            warmup = [_default_aux_item(REQUIRED_WARMUP_SLUG)]
        if not cooldown:
            cooldown = [_default_aux_item(REQUIRED_COOLDOWN_SLUG)]
        focus = [m for m in _as_list(tpl.get("focus_muscles")) if m in MUSCLE_GROUPS]
        session_type = tpl.get("session_type") if tpl.get("session_type") in SESSION_TYPES else "strength"

        out.append({
            "week": week,
            "day_index": day_index,
            "weekday": day.weekday(),
            "scheduled_date": day.isoformat(),
            "title": str(tpl.get("title") or f"День {day_index}"),
            "session_type": session_type,
            "duration_min": _to_int(tpl.get("duration_min")) or minutes_default,
            "focus_muscles_json": _dumps(focus),
            "warmup_json": _dumps(warmup),
            "exercises_json": _dumps(exercises),
            "cooldown_json": _dumps(cooldown),
            "adjustments_json": None,
            "status": "planned",
            "phase": mod["phase"],
            "weight_pct": mod["weight_pct"],
            "sets_delta": mod["sets_delta"],
        })
    return out


def day_to_row(day: dict) -> dict:
    """Оставить в раскрытом дне только колонки TrainerProgramDay."""
    return {key: day.get(key) for key in DAY_COLUMNS}


def program_dates(days) -> tuple[str | None, str | None]:
    """(start_date, end_date) программы по раскрытым дням."""
    dates = sorted(d for d in (_get(x, "scheduled_date") for x in _as_list(days)) if d)
    if not dates:
        return None, None
    return dates[0], dates[-1]


def next_planned_day(days):
    """Следующий день плана: первый status=planned по (week, day_index)."""
    planned = [d for d in _as_list(days) if (_get(d, "status") or "planned") == "planned"]
    if not planned:
        return None
    planned.sort(key=lambda d: (_to_int(_get(d, "week"), 0) or 0, _to_int(_get(d, "day_index"), 0) or 0))
    return planned[0]


def current_week(days, today=None, weeks=None) -> int:
    """Текущая неделя программы: неделя дня с датой = сегодня, иначе следующего
    planned дня, иначе последняя."""
    now = (_parse_date(today) or date.today()).isoformat()
    for d in _as_list(days):
        if _get(d, "scheduled_date") == now:
            return _to_int(_get(d, "week"), 1) or 1
    upcoming = next_planned_day(days)
    if upcoming is not None:
        return _to_int(_get(upcoming, "week"), 1) or 1
    last = max((_to_int(_get(d, "week"), 0) or 0 for d in _as_list(days)), default=0)
    return last or _to_int(weeks, 1) or 1


# --------------------------------------------------------------------------- #
#  Каталог: доступное оборудование, фильтр, строки для промпта
# --------------------------------------------------------------------------- #
def available_equipment(profile) -> set[str]:
    """Оборудование, доступное пользователю: базовый набор варианта + доп. чипы."""
    base = str(_get(profile, "equipment") or "bodyweight")
    allowed = set(EQUIPMENT_BY_PROFILE.get(base, EQUIPMENT_BY_PROFILE["bodyweight"]))
    for code in _list_field(profile, "equipment_extra"):
        mapped = EXTRA_TO_EQUIPMENT.get(str(code).strip().lower())
        if mapped:
            allowed.add(mapped)
    return allowed


def profile_limitations(profile) -> set[str]:
    """Коды ограничений профиля без «none»."""
    return {str(x).strip().lower() for x in _list_field(profile, "limitations") if str(x).strip()} - {"none", ""}


def _entry_contra(entry) -> set[str]:
    return {str(x).strip().lower() for x in _list_field(entry, "contraindications") if str(x).strip()}


def _entry_alternatives(entry) -> list[str]:
    return [str(x).strip() for x in _list_field(entry, "alternatives") if str(x).strip()]


def _max_difficulty(profile) -> int:
    return 2 if str(_get(profile, "level") or "") == "beginner" else 3


def filter_catalog(exercises, profile, excluded_ids=None) -> list:
    """Каталог под пользователя: активные, по оборудованию, без контриндицированных,
    без excluded, по сложности уровня (новичок ≤ 2). Обязательные warmup/stretch
    остаются всегда."""
    allowed = available_equipment(profile)
    limits = profile_limitations(profile)
    max_diff = _max_difficulty(profile)
    excluded = {v for v in (_to_int(x) for x in _as_list(excluded_ids)) if v is not None}
    out = []
    for entry in _as_list(exercises):
        if _get(entry, "is_active", True) is False:
            continue
        ex_id = _to_int(_get(entry, "id"))
        if ex_id is not None and ex_id in excluded:
            continue
        slug = _get(entry, "slug")
        if slug in (REQUIRED_WARMUP_SLUG, REQUIRED_COOLDOWN_SLUG):
            out.append(entry)
            continue
        if _get(entry, "equipment") not in allowed:
            continue
        if _entry_contra(entry) & limits:
            continue
        if (_to_int(_get(entry, "difficulty"), 1) or 1) > max_diff:
            continue
        out.append(entry)
    return out


def catalog_line(entry) -> str:
    """«slug | name_en | muscle | equipment | measure | difficulty» — формат промпта §5.1."""
    return " | ".join([
        str(_get(entry, "slug") or ""),
        str(_get(entry, "name_en") or _get(entry, "name_ru") or _get(entry, "slug") or ""),
        str(_get(entry, "muscle_group") or ""),
        str(_get(entry, "equipment") or ""),
        str(_get(entry, "measure_type") or ""),
        str(_to_int(_get(entry, "difficulty"), 1) or 1),
    ])


def _bucket(entry) -> str:
    category = str(_get(entry, "category") or "")
    group = str(_get(entry, "muscle_group") or "")
    if category == "stretch":
        return "stretch"
    if category == "mobility" or group == "mobility":
        return "mobility"
    if category == "cardio" or group in ("cardio", "full_body"):
        return "cardio"
    return "strength"


def select_for_prompt(entries, limit: int = MAX_CATALOG_LINES) -> list:
    """Усечь каталог до limit строк, сохранив покрытие: обязательные записи, квоты
    на мобильность/растяжку/кардио и round-robin по группам мышц (компаунды раньше)."""
    entries = list(_as_list(entries))
    if len(entries) <= limit:
        return entries
    required = [e for e in entries if _get(e, "slug") in (REQUIRED_WARMUP_SLUG, REQUIRED_COOLDOWN_SLUG)]
    buckets: dict[str, list] = {"strength": [], "cardio": [], "mobility": [], "stretch": []}
    for entry in entries:
        if entry in required:
            continue
        buckets[_bucket(entry)].append(entry)
    picked = list(required)
    quotas = {"mobility": 8, "stretch": 8, "cardio": 10}
    for name, quota in quotas.items():
        picked.extend(buckets[name][:quota])
        buckets[name] = buckets[name][quota:]

    by_group: dict[str, list] = {}
    for entry in buckets["strength"]:
        by_group.setdefault(str(_get(entry, "muscle_group") or ""), []).append(entry)
    for group, rows in by_group.items():
        rows.sort(key=lambda e: (0 if _get(e, "category") == "compound" else 1, _to_int(_get(e, "difficulty"), 1) or 1))
    order = [g for g in STRENGTH_GROUPS if g in by_group] + [g for g in by_group if g not in STRENGTH_GROUPS]
    while len(picked) < limit and any(by_group[g] for g in order):
        for group in order:
            if by_group[group] and len(picked) < limit:
                picked.append(by_group[group].pop(0))
    for name in ("cardio", "mobility", "stretch"):
        while buckets[name] and len(picked) < limit:
            picked.append(buckets[name].pop(0))

    def sort_key(entry):
        group = str(_get(entry, "muscle_group") or "")
        category = str(_get(entry, "category") or "")
        return (
            MUSCLE_GROUPS.index(group) if group in MUSCLE_GROUPS else len(MUSCLE_GROUPS),
            CATEGORIES.index(category) if category in CATEGORIES else len(CATEGORIES),
            _to_int(_get(entry, "difficulty"), 1) or 1,
            str(_get(entry, "slug") or ""),
        )

    picked = picked[:limit]
    picked.sort(key=sort_key)
    return picked


def catalog_lines(entries, limit: int = MAX_CATALOG_LINES) -> str:
    """Строки каталога для промпта (≤ limit)."""
    return "\n".join(catalog_line(e) for e in select_for_prompt(entries, limit))


def catalog_for_prompt(exercises, profile, excluded_ids=None, limit: int = MAX_CATALOG_LINES) -> str:
    """Отфильтрованный каталог для generate_program: по оборудованию, без
    контриндицированных, без excluded; ≤ limit строк «slug | name_en | muscle |
    equipment | measure | difficulty»."""
    return catalog_lines(filter_catalog(exercises, profile, excluded_ids), limit)


# --------------------------------------------------------------------------- #
#  §4.3 Альтернативы упражнения
# --------------------------------------------------------------------------- #
def alternatives_for(exercise, catalog, profile, reason="other", excluded_ids=None, limit: int = MAX_ALTERNATIVES) -> list:
    """3–5 альтернатив на ту же группу мышц правилами из библиотеки.

    Фильтры: та же muscle_group; оборудование ∈ доступного пользователю
    (no_equipment — ещё и без оборудования исходного); без пересечения с
    ограничениями профиля; для pain — дополнительно без пересечения с
    контриндикациями исходного и не сложнее исходного (если так пусто —
    смягчаем до ограничений профиля); excluded и само упражнение — мимо.
    Порядок: ручные alternatives_json → та же category → остальное; ≤ limit.
    """
    reason = reason if reason in REPLACE_REASONS else "other"
    ex_id = _to_int(_get(exercise, "id"))
    ex_slug = _get(exercise, "slug")
    muscle = _get(exercise, "muscle_group")
    equipment = _get(exercise, "equipment")
    category = _get(exercise, "category")
    difficulty = _to_int(_get(exercise, "difficulty"), 1) or 1

    allowed = available_equipment(profile) if profile is not None else set(EQUIPMENT_CODES)
    if reason == "no_equipment" and equipment:
        allowed = allowed - {equipment}
    limits = profile_limitations(profile) if profile is not None else set()
    excluded = {v for v in (_to_int(x) for x in _as_list(excluded_ids)) if v is not None}
    manual = _entry_alternatives(exercise)

    def collect(banned: set[str], max_diff: int | None) -> list:
        rows = []
        for entry in _as_list(catalog):
            if entry is exercise:
                continue
            entry_id = _to_int(_get(entry, "id"))
            if ex_id is not None and entry_id == ex_id:
                continue
            if ex_slug and _get(entry, "slug") == ex_slug:
                continue
            if _get(entry, "is_active", True) is False:
                continue
            if _get(entry, "muscle_group") != muscle:
                continue
            if entry_id is not None and entry_id in excluded:
                continue
            if _get(entry, "equipment") not in allowed:
                continue
            if _entry_contra(entry) & banned:
                continue
            if max_diff is not None and (_to_int(_get(entry, "difficulty"), 1) or 1) > max_diff:
                continue
            rows.append(entry)
        return rows

    if reason == "pain":
        candidates = collect(limits | _entry_contra(exercise), difficulty)
        if not candidates:
            candidates = collect(limits, difficulty)
        if not candidates:
            candidates = collect(limits, None)
    else:
        candidates = collect(limits, None)

    def tier(entry) -> tuple:
        slug = _get(entry, "slug")
        if slug in manual:
            return (0, manual.index(slug))
        if category and _get(entry, "category") == category:
            return (1, 0)
        return (2, 0)

    if reason == "pain":
        candidates.sort(key=lambda e: (tier(e), _to_int(_get(e, "difficulty"), 1) or 1))
    else:
        candidates.sort(key=tier)
    return candidates[:limit]


def alternatives_for_db(db, tid: int, exercise, reason="other", limit: int = MAX_ALTERNATIVES) -> list:
    """Обёртка с БД: профиль, активный каталог и excluded-состояния пользователя."""
    from backend import models as M

    profile = db.query(M.TrainerProfile).filter(M.TrainerProfile.telegram_id == tid).first()
    catalog = db.query(M.TrainerExercise).filter(M.TrainerExercise.is_active.is_(True)).order_by(M.TrainerExercise.id).all()
    excluded = [
        row.exercise_id
        for row in db.query(M.TrainerExerciseState)
        .filter(M.TrainerExerciseState.telegram_id == tid, M.TrainerExerciseState.excluded.is_(True))
        .all()
    ]
    return alternatives_for(exercise, catalog, profile, reason, excluded, limit)


# --------------------------------------------------------------------------- #
#  §5.3 Контекст недельного разбора (с БД)
# --------------------------------------------------------------------------- #
def _active_program(db, tid: int):
    from backend import models as M

    return (
        db.query(M.TrainerProgram)
        .filter(M.TrainerProgram.telegram_id == tid, M.TrainerProgram.status == "active")
        .order_by(M.TrainerProgram.id.desc())
        .first()
    )


def collect_week_stats(db, tid: int, week_start=None, today=None) -> dict:
    """Собрать контекст недели для weekly_review (ТЗ §5.3): программа, план vs факт,
    сессии, упражнения план→факт, сеты по мышцам, ккал/белок из дневника, вес,
    ограничения и каталог разрешённых slug для swap (ключ "catalog")."""
    from backend import models as M

    now = _parse_date(today) or date.today()
    start_iso, end_iso = week_bounds(week_start or now)
    start_day = _parse_date(start_iso)
    end_day = _parse_date(end_iso)

    user = db.query(M.User).filter(M.User.telegram_id == tid).first()
    profile = db.query(M.TrainerProfile).filter(M.TrainerProfile.telegram_id == tid).first()
    program = _active_program(db, tid)

    # Программа и план недели.
    program_info = None
    plan = {"planned": 0, "done": 0, "skipped": 0, "missed": 0}
    if program is not None:
        days = (
            db.query(M.TrainerProgramDay)
            .filter(M.TrainerProgramDay.program_id == program.id)
            .order_by(M.TrainerProgramDay.week, M.TrainerProgramDay.day_index)
            .all()
        )
        in_week = [d for d in days if d.scheduled_date and start_iso <= d.scheduled_date <= end_iso]
        for d in in_week:
            plan["planned"] += 1
            if d.status == "done":
                plan["done"] += 1
            elif d.status == "skipped":
                plan["skipped"] += 1
            elif d.scheduled_date < now.isoformat():
                plan["missed"] += 1
        week_no = None
        if in_week:
            counts: dict[int, int] = {}
            for d in in_week:
                counts[d.week] = counts.get(d.week, 0) + 1
            week_no = max(counts, key=lambda w: (counts[w], -w))
        if week_no is None:
            week_no = current_week(days, now, program.weeks)
        mod = week_modifier(program.periodization_json, week_no, program.weeks)
        program_info = {
            "id": program.id,
            "title": program.title,
            "goal": program.goal,
            "level": program.level,
            "equipment": program.equipment,
            "split_type": program.split_type,
            "week": week_no,
            "weeks": program.weeks,
            "days_per_week": program.days_per_week,
            "phase": mod["phase"],
        }

    # Сессии недели.
    sessions = (
        db.query(M.TrainerSession)
        .filter(
            M.TrainerSession.telegram_id == tid,
            M.TrainerSession.date >= start_iso,
            M.TrainerSession.date <= end_iso,
            M.TrainerSession.status.in_(["completed", "abandoned"]),
        )
        .order_by(M.TrainerSession.date, M.TrainerSession.id)
        .all()
    )
    completed = [s for s in sessions if s.status == "completed"]
    session_rows = []
    feedback_counts = {"easy": 0, "ok": 0, "hard": 0}
    for s in completed:
        if s.feedback in feedback_counts:
            feedback_counts[s.feedback] += 1
        session_rows.append({
            "id": s.id,
            "date": s.date,
            "title": s.title,
            "session_type": s.session_type,
            "week": s.week,
            "day_index": s.day_index,
            "duration_min": s.duration_min,
            "total_sets": s.total_sets,
            "total_reps": s.total_reps,
            "total_volume_kg": s.total_volume_kg,
            "calories_burned": s.calories_burned,
            "feedback": s.feedback,
            "note": (s.feedback_note or "")[:200] or None,
        })

    # Упражнения: план → факт, результат, PR; сеты по мышцам.
    exercise_rows: list[dict] = []
    muscle_sets: dict[str, int] = {}
    session_ids = [s.id for s in completed]
    if session_ids:
        sess_exercises = (
            db.query(M.TrainerSessionExercise)
            .filter(M.TrainerSessionExercise.session_id.in_(session_ids))
            .order_by(M.TrainerSessionExercise.session_id, M.TrainerSessionExercise.order_index)
            .all()
        )
        logs = (
            db.query(M.TrainerSetLog)
            .filter(M.TrainerSetLog.session_id.in_(session_ids))
            .order_by(M.TrainerSetLog.set_index)
            .all()
        )
        logs_by_sex: dict[int, list] = {}
        for log in logs:
            logs_by_sex.setdefault(log.session_exercise_id, []).append(log)
        ex_ids = {se.exercise_id for se in sess_exercises if se.exercise_id}
        exercises = {
            e.id: e for e in db.query(M.TrainerExercise).filter(M.TrainerExercise.id.in_(list(ex_ids))).all()
        } if ex_ids else {}
        session_date = {s.id: s.date for s in completed}
        for se in sess_exercises:
            if se.block != "main" or se.status == "replaced":
                continue
            ex = exercises.get(se.exercise_id)
            done = _done_work_sets(logs_by_sex.get(se.id, []))
            if not done and se.status != "skipped":
                continue
            planned = {
                "sets": se.planned_sets,
                "reps_min": se.planned_reps_min,
                "reps_max": se.planned_reps_max,
                "time_sec": se.planned_time_sec,
                "measure_type": ex.measure_type if ex else "reps_weight",
            }
            group = ex.muscle_group if ex else None
            if group:
                muscle_sets[group] = muscle_sets.get(group, 0) + len(done)
            pr_types: list[str] = []
            for log in done:
                if log.is_pr:
                    for t in _as_list(log.pr_types_json):
                        if t not in pr_types:
                            pr_types.append(str(t))
            exercise_rows.append({
                "date": session_date.get(se.session_id),
                "exercise_id": se.exercise_id,
                "slug": ex.slug if ex else None,
                "name_ru": ex.name_ru if ex else None,
                "name_en": ex.name_en if ex else None,
                "muscle_group": group,
                "status": se.status,
                "plan": {
                    "sets": se.planned_sets,
                    "reps_min": se.planned_reps_min,
                    "reps_max": se.planned_reps_max,
                    "weight_kg": se.planned_weight_kg,
                    "time_sec": se.planned_time_sec,
                },
                "fact": {
                    "sets": len(done),
                    "reps": [log.reps for log in done],
                    "weights": [log.weight_kg for log in done],
                    "time_sec": [log.time_sec for log in done],
                },
                "result": "skipped" if (se.status == "skipped" and not done) else evaluate_exercise(planned, done),
                "prs": pr_types,
            })

    # Питание: средние ккал/белок за дни с записями.
    entries = (
        db.query(M.DiaryEntry)
        .filter(M.DiaryEntry.telegram_id == tid, M.DiaryEntry.date >= start_iso, M.DiaryEntry.date <= end_iso)
        .all()
    )
    per_day: dict[str, list[float]] = {}
    for e in entries:
        row = per_day.setdefault(e.date, [0.0, 0.0])
        row[0] += _to_float(e.calories, 0.0) or 0.0
        row[1] += _to_float(e.proteins, 0.0) or 0.0
    nutrition = {
        "days_logged": len(per_day),
        "avg_kcal": round(statistics.mean(v[0] for v in per_day.values())) if per_day else None,
        "avg_protein": round(statistics.mean(v[1] for v in per_day.values())) if per_day else None,
        "goal_kcal": user.daily_goal_kcal if user else None,
        "goal_protein": _to_int(user.target_proteins) if user and user.target_proteins else None,
    }

    # Вес: первый/последний лог за 14 дней до конца недели.
    since = (end_day - timedelta(days=13)).isoformat()
    weight_logs = (
        db.query(M.WeightLog)
        .filter(M.WeightLog.telegram_id == tid, M.WeightLog.date >= since, M.WeightLog.date <= end_iso)
        .order_by(M.WeightLog.date, M.WeightLog.id)
        .all()
    )
    weight = {"first": None, "last": None, "delta": None, "current": user.weight if user else None}
    if weight_logs:
        weight["first"] = weight_logs[0].weight
        weight["last"] = weight_logs[-1].weight
        if weight_logs[0].weight is not None and weight_logs[-1].weight is not None:
            weight["delta"] = round(weight_logs[-1].weight - weight_logs[0].weight, 1)

    # Каталог для swap: тот же фильтр, что для генерации.
    catalog_entries: list[dict] = []
    if profile is not None:
        library = db.query(M.TrainerExercise).filter(M.TrainerExercise.is_active.is_(True)).order_by(M.TrainerExercise.id).all()
        excluded = [
            r.exercise_id
            for r in db.query(M.TrainerExerciseState)
            .filter(M.TrainerExerciseState.telegram_id == tid, M.TrainerExerciseState.excluded.is_(True))
            .all()
        ]
        for e in select_for_prompt(filter_catalog(library, profile, excluded)):
            catalog_entries.append({
                "id": e.id,
                "slug": e.slug,
                "name_ru": e.name_ru,
                "name_en": e.name_en,
                "muscle_group": e.muscle_group,
                "equipment": e.equipment,
                "measure_type": e.measure_type,
                "difficulty": e.difficulty,
            })

    return {
        "week_start": start_iso,
        "week_end": end_iso,
        "today": now.isoformat(),
        "program": program_info,
        "plan": plan,
        "sessions": session_rows,
        "abandoned": len(sessions) - len(completed),
        "feedback_counts": feedback_counts,
        "exercises": exercise_rows,
        "muscle_sets": muscle_sets,
        "nutrition": nutrition,
        "weight": weight,
        "diet_goal": user.diet_goal if user else None,
        "goal": profile.goal if profile else None,
        "level": profile.level if profile else None,
        "limitations": sorted(profile_limitations(profile)) if profile else [],
        "limitations_text": (profile.limitations_text or None) if profile else None,
        "catalog": catalog_entries,
    }


# --------------------------------------------------------------------------- #
#  §5.5 Контекст тренировки для «Что съесть?» (с БД)
# --------------------------------------------------------------------------- #
def today_training_context(db, tid: int, day=None, lang="ru") -> str | None:
    """Строка о сегодняшней тренировке для suggest_food или None, если её нет.

    Завершённая сессия → «была тренировка, N мин, ≈K ккал»; идущая → «идёт»;
    planned день с scheduled_date = сегодня → «по плану». Плюс цель по белку."""
    from backend import models as M

    en = _is_en(lang)
    day_iso = (_parse_date(day) or date.today()).isoformat()
    user = db.query(M.User).filter(M.User.telegram_id == tid).first()
    sessions = (
        db.query(M.TrainerSession)
        .filter(
            M.TrainerSession.telegram_id == tid,
            M.TrainerSession.date == day_iso,
            M.TrainerSession.status.in_(["completed", "in_progress"]),
        )
        .order_by(M.TrainerSession.id.desc())
        .all()
    )
    done = [s for s in sessions if s.status == "completed"]
    running = [s for s in sessions if s.status == "in_progress"]

    parts: list[str] = []
    if done:
        s = done[0]
        title = s.title or ("workout" if en else "тренировка")
        if en:
            text = f"Workout done today: “{title}”"
            if s.duration_min:
                text += f", {s.duration_min} min"
            if s.calories_burned:
                text += f", ≈{s.calories_burned} kcal burned"
        else:
            text = f"Сегодня уже была тренировка «{title}»"
            if s.duration_min:
                text += f", {s.duration_min} мин"
            if s.calories_burned:
                text += f", сожжено ≈{s.calories_burned} ккал"
        parts.append(text)
    elif running:
        title = running[0].title or ("workout" if en else "тренировка")
        parts.append(f"A workout “{title}” is in progress right now" if en else f"Сейчас идёт тренировка «{title}»")
    else:
        program = _active_program(db, tid)
        planned = None
        if program is not None:
            planned = (
                db.query(M.TrainerProgramDay)
                .filter(
                    M.TrainerProgramDay.program_id == program.id,
                    M.TrainerProgramDay.scheduled_date == day_iso,
                    M.TrainerProgramDay.status == "planned",
                )
                .order_by(M.TrainerProgramDay.id)
                .first()
            )
        if planned is None:
            return None
        title = planned.title or ("workout" if en else "тренировка")
        minutes = planned.duration_min
        if en:
            parts.append(f"A workout is planned today: “{title}”" + (f" ({minutes} min)" if minutes else ""))
        else:
            parts.append(f"Сегодня по плану тренировка «{title}»" + (f" ({minutes} мин)" if minutes else ""))

    protein = _to_int(user.target_proteins) if user and user.target_proteins else None
    if protein:
        parts.append(f"daily protein target {protein} g" if en else f"цель по белку {protein} г в день")
    return "; ".join(parts) + "."


# --------------------------------------------------------------------------- #
#  §4.5 Применение правок недельного разбора (с БД)
# --------------------------------------------------------------------------- #
def apply_review_changes(db, tid: int, review, change_ids, lang="ru") -> dict:
    """Применить выбранные изменения разбора к следующей неделе активной программы.

    review — TrainerWeeklyReview (ORM или dict с review_json/week/program_id);
    change_ids — индексы изменений (`id` в review.changes). Правит exercises_json /
    adjustments_json дней недели (review.week + 1) и TrainerExerciseState;
    записывает применённые id в review_json["applied_change_ids"] и ставит
    applied=True. Повторный вызов с теми же id ничего не меняет (идемпотентно).
    Возвращает {"applied": [id...], "lines": [строки на языке пользователя]}.
    """
    from backend import models as M

    data = _json_dict(_get(review, "review_json"))
    changes = [c for c in _as_list(data.get("changes")) if isinstance(c, dict)]
    by_id: dict[int, dict] = {}
    for index, ch in enumerate(changes):
        by_id[_to_int(ch.get("id"), index)] = ch
    already = {v for v in (_to_int(x) for x in _as_list(data.get("applied_change_ids"))) if v is not None}
    wanted: list[int] = []
    for raw in _as_list(change_ids):
        cid = _to_int(raw)
        if cid is not None and cid in by_id and cid not in wanted:
            wanted.append(cid)

    program = _active_program(db, tid)
    review_program_id = _to_int(_get(review, "program_id"))
    if program is None and review_program_id:
        program = db.query(M.TrainerProgram).filter(
            M.TrainerProgram.id == review_program_id, M.TrainerProgram.telegram_id == tid
        ).first()

    days: list = []
    next_week = None
    if program is not None:
        all_days = (
            db.query(M.TrainerProgramDay)
            .filter(M.TrainerProgramDay.program_id == program.id)
            .order_by(M.TrainerProgramDay.week, M.TrainerProgramDay.day_index)
            .all()
        )
        base_week = _to_int(_get(review, "week"))
        if base_week is None:
            base_week = current_week(all_days, None, program.weeks)
        next_week = base_week + 1
        days = [d for d in all_days if d.week == next_week]

    parsed_items: dict[int, list] = {d.id: _as_list(d.exercises_json) for d in days}
    parsed_adj: dict[int, dict] = {d.id: _json_dict(d.adjustments_json) for d in days}
    touched: set[int] = set()

    def find_exercise(ex_id, slug):
        query = db.query(M.TrainerExercise)
        if _to_int(ex_id) is not None:
            row = query.filter(M.TrainerExercise.id == _to_int(ex_id)).first()
            if row is not None:
                return row
        if slug:
            return query.filter(M.TrainerExercise.slug == slug).first()
        return None

    def get_state(ex_id, create=False):
        if ex_id is None:
            return None
        row = db.query(M.TrainerExerciseState).filter(
            M.TrainerExerciseState.telegram_id == tid, M.TrainerExerciseState.exercise_id == ex_id
        ).first()
        if row is None and create:
            row = M.TrainerExerciseState(telegram_id=tid, exercise_id=ex_id)
            db.add(row)
            db.flush()
        return row

    applied: list[int] = []
    explain: list[dict] = []
    for cid in wanted:
        if cid in already:
            applied.append(cid)
            continue
        ch = by_id[cid]
        ctype = str(ch.get("type") or "")
        ex = find_exercise(ch.get("exercise_id"), ch.get("exercise_slug"))
        ex_id = ex.id if ex is not None else _to_int(ch.get("exercise_id"))
        slug = ex.slug if ex is not None else ch.get("exercise_slug")
        meta = {
            "exercise_id": ex_id,
            "slug": slug,
            "name_ru": ex.name_ru if ex is not None else ch.get("exercise_name_ru"),
            "name_en": ex.name_en if ex is not None else ch.get("exercise_name_en"),
        }

        if ctype == "deload_next_week":
            for d in days:
                adj = parsed_adj[d.id]
                adj["deload"] = True
                adj["weight_pct"] = DELOAD_WEIGHT_PCT
                adj["sets_delta"] = DELOAD_SETS_DELTA
                touched.add(d.id)
            explain.append({"kind": "deload", "reason": "deload_next_week", "old_value": None, "new_value": None})
            applied.append(cid)
            continue

        if ctype not in ("weight_pct", "sets", "swap", "rest_sec") or (ex_id is None and not slug):
            continue

        if ctype == "weight_pct":
            pct = _clamp(_to_int(ch.get("value"), 0) or 0, WEIGHT_PCT_RANGE[0], WEIGHT_PCT_RANGE[1])
            if pct == 0:
                continue
            step = weight_step(ex.equipment if ex else None, ex.muscle_group if ex else None)
            old = new = None
            for d in days:
                for item in parsed_items[d.id]:
                    if isinstance(item, dict) and _same_exercise(item, ex_id, slug):
                        w = _to_float(item.get("start_weight_kg"))
                        if w:
                            item["start_weight_kg"] = round_to_step(w * (100 + pct) / 100.0, step)
                            old, new = (old if old is not None else w), (new if new is not None else item["start_weight_kg"])
                            touched.add(d.id)
            state = get_state(ex_id)
            if state is not None and state.working_weight_kg:
                old = state.working_weight_kg if old is None else old
                state.working_weight_kg = round_to_step(state.working_weight_kg * (100 + pct) / 100.0, step)
                new = state.working_weight_kg if new is None else new
            explain.append(dict(meta, kind="weight", reason="weight_pct" if old is not None else "weight_pct_only",
                                old_value=old, new_value=new, pct=pct))
            applied.append(cid)

        elif ctype == "sets":
            delta = 1 if (_to_int(ch.get("value"), 0) or 0) > 0 else -1
            old = new = None
            for d in days:
                for item in parsed_items[d.id]:
                    if isinstance(item, dict) and _same_exercise(item, ex_id, slug):
                        sets = _to_int(item.get("sets"), 3) or 3
                        item["sets"] = _clamp(sets + delta, SETS_RANGE[0], SETS_RANGE[1])
                        old = sets if old is None else old
                        new = item["sets"] if new is None else new
                        touched.add(d.id)
            explain.append(dict(meta, kind="sets", reason="sets_up" if delta > 0 else "sets_down",
                                old_value=old, new_value=new))
            applied.append(cid)

        elif ctype == "rest_sec":
            seconds = _clamp(_to_int(ch.get("value"), 0) or 0, REST_RANGE[0], REST_RANGE[1])
            old = None
            for d in days:
                for item in parsed_items[d.id]:
                    if isinstance(item, dict) and _same_exercise(item, ex_id, slug):
                        old = item.get("rest_sec") if old is None else old
                        item["rest_sec"] = seconds
                        touched.add(d.id)
            explain.append(dict(meta, kind="keep", reason="rest_sec", old_value=old, new_value=seconds))
            applied.append(cid)

        elif ctype == "swap":
            new_ex = find_exercise(ch.get("new_exercise_id"), ch.get("new_slug"))
            if new_ex is None or new_ex.id == ex_id:
                continue
            new_state = get_state(new_ex.id)
            new_weight = new_state.working_weight_kg if new_state is not None else None
            for d in days:
                for item in parsed_items[d.id]:
                    if isinstance(item, dict) and _same_exercise(item, ex_id, slug):
                        item["slug"] = new_ex.slug
                        item["exercise_id"] = new_ex.id
                        item["muscle_group"] = new_ex.muscle_group
                        item["start_weight_kg"] = new_weight if new_ex.measure_type == "reps_weight" else None
                        if new_ex.measure_type in ("time", "distance"):
                            item["time_sec"] = item.get("time_sec") or 45
                            item["reps_min"] = None
                            item["reps_max"] = None
                        touched.add(d.id)
            if ex_id is not None:
                state = get_state(ex_id, create=True)
                state.preferred_alternative_id = new_ex.id
            explain.append(dict(meta, kind="keep", reason="swap", old_value=None, new_value=new_ex.slug,
                                new_slug=new_ex.slug, new_name_ru=new_ex.name_ru, new_name_en=new_ex.name_en))
            applied.append(cid)

    for d in days:
        if d.id in touched:
            d.exercises_json = _dumps(parsed_items[d.id])
            d.adjustments_json = _dumps(parsed_adj[d.id]) if parsed_adj[d.id] else d.adjustments_json

    data["applied_change_ids"] = sorted(already | set(applied))
    _set(review, "review_json", _dumps(data))
    if applied:
        _set(review, "applied", True)
    db.commit()
    return {"applied": applied, "lines": explain_changes(explain, lang), "next_week": next_week}
