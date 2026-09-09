"""Pydantic-схемы (v2) AI-тренера — тела запросов и ответов `/trainer/*` (ТЗ §4).

Вынесены из `schemas.py` отдельным модулем, чтобы параллельные этапы фичи не
толкались в одном файле. Имена схем и полей — контракт с фронтендом (§4.1–§4.5)
и менять их нельзя.

Валидация кодов (цель, уровень, оборудование, ограничения, фокус) идёт по
спискам из `trainer_logic` — один источник правды для бэкенда и промптов.
Нарушение → ValueError внутри `field_validator` → FastAPI отвечает 422.

Схемы сессии (§4.3) описаны здесь целиком, хотя сами маршруты сессии добавляет
следующий этап: они нужны уже сейчас для `GET /trainer/today` и `overview`
(поле `active_session`).
"""

from __future__ import annotations

from datetime import date as _date_cls
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from backend import trainer_logic


# --------------------------------------------------------------------------- #
#  Валидаторы кодов (общие для входных схем)
# --------------------------------------------------------------------------- #
def _one_code(value: Any, allowed: tuple, field: str) -> str:
    """Один код из списка допустимых; иначе ValueError (→ 422)."""
    code = str(value or "").strip().lower()
    if code not in allowed:
        raise ValueError(f"{field}: недопустимое значение {value!r}, ожидается одно из {list(allowed)}")
    return code


def _code_list(values: Any, allowed: tuple, field: str, limit: int = 12) -> List[str]:
    """Список кодов без дублей и пустых строк; неизвестный код → ValueError (→ 422)."""
    if values is None:
        return []
    if not isinstance(values, (list, tuple, set)):
        raise ValueError(f"{field}: ожидается список кодов")
    out: List[str] = []
    for raw in values:
        code = str(raw or "").strip().lower()
        if not code:
            continue
        if code not in allowed:
            raise ValueError(f"{field}: недопустимое значение {raw!r}, ожидается одно из {list(allowed)}")
        if code not in out:
            out.append(code)
    return out[:limit]


# --------------------------------------------------------------------------- #
#  §4.1 Профиль тренера
# --------------------------------------------------------------------------- #
class TrainerProfileIn(BaseModel):
    """Анкета онбординга (9 шагов) — приходит одним запросом на последнем шаге."""

    goal: str                                   # loss|muscle|strength|endurance|tone
    level: str                                  # beginner|intermediate|advanced
    equipment: str                              # gym|home_dumbbells|bodyweight
    equipment_extra: List[str] = []             # pullup_bar|bands|bench|kettlebell|barbell|cardio_machine
    days_per_week: int                          # 2..6
    preferred_weekdays: List[int]               # Пн=0 .. Вс=6, длина == days_per_week
    session_minutes: int                        # 20|30|45|60|75|90
    program_weeks: int = 6                      # 4|6|8
    limitations: List[str] = []                 # knee|lower_back|... |none
    limitations_text: Optional[str] = None      # свободный текст, ≤300 символов
    focus: List[str] = []                       # glutes|core|back|... |none
    reminder_enabled: bool = False
    reminder_time: Optional[str] = None         # "HH:MM"

    @field_validator("goal")
    @classmethod
    def _v_goal(cls, v):
        return _one_code(v, trainer_logic.GOALS, "goal")

    @field_validator("level")
    @classmethod
    def _v_level(cls, v):
        return _one_code(v, trainer_logic.LEVELS, "level")

    @field_validator("equipment")
    @classmethod
    def _v_equipment(cls, v):
        return _one_code(v, trainer_logic.EQUIPMENT_PROFILES, "equipment")

    @field_validator("equipment_extra")
    @classmethod
    def _v_equipment_extra(cls, v):
        return _code_list(v, trainer_logic.EQUIPMENT_EXTRA_CODES, "equipment_extra")

    @field_validator("limitations")
    @classmethod
    def _v_limitations(cls, v):
        return _code_list(v, trainer_logic.LIMITATION_CODES, "limitations")

    @field_validator("focus")
    @classmethod
    def _v_focus(cls, v):
        return _code_list(v, trainer_logic.FOCUS_CODES, "focus")

    @field_validator("days_per_week")
    @classmethod
    def _v_days(cls, v):
        if v not in trainer_logic.DAYS_PER_WEEK:
            raise ValueError(f"days_per_week: допустимо {list(trainer_logic.DAYS_PER_WEEK)}")
        return v

    @field_validator("session_minutes")
    @classmethod
    def _v_minutes(cls, v):
        if v not in trainer_logic.SESSION_MINUTES:
            raise ValueError(f"session_minutes: допустимо {list(trainer_logic.SESSION_MINUTES)}")
        return v

    @field_validator("program_weeks")
    @classmethod
    def _v_weeks(cls, v):
        if v not in trainer_logic.PROGRAM_WEEKS:
            raise ValueError(f"program_weeks: допустимо {list(trainer_logic.PROGRAM_WEEKS)}")
        return v

    @field_validator("preferred_weekdays")
    @classmethod
    def _v_weekdays(cls, v):
        days: List[int] = []
        for raw in v or []:
            try:
                day = int(raw)
            except (TypeError, ValueError):
                raise ValueError("preferred_weekdays: дни недели — целые числа 0..6 (Пн=0)")
            if not (0 <= day <= 6):
                raise ValueError("preferred_weekdays: дни недели — целые числа 0..6 (Пн=0)")
            if day not in days:
                days.append(day)
        return sorted(days)

    @field_validator("limitations_text")
    @classmethod
    def _v_limitations_text(cls, v):
        # Длинный текст молча обрезаем: фронт и так ограничивает поле 300 символами.
        text = str(v or "").strip()
        return text[:300] or None

    @field_validator("reminder_time")
    @classmethod
    def _v_reminder_time(cls, v):
        if v is None or not str(v).strip():
            return None
        try:
            return trainer_logic._normalize_time(v)
        except ValueError as exc:
            raise ValueError(f"reminder_time: {exc}")

    @model_validator(mode="after")
    def _v_weekdays_count(self):
        """Дней недели должно быть ровно столько, сколько тренировок (иначе 422)."""
        if len(self.preferred_weekdays) != self.days_per_week:
            raise ValueError(
                "preferred_weekdays: выбрано %d дней, а тренировок в неделю %d"
                % (len(self.preferred_weekdays), self.days_per_week)
            )
        return self


class TrainerProfileOut(BaseModel):
    """Анкета тренера для фронта (все поля анкеты + служебные идентификаторы)."""

    id: int
    goal: Optional[str] = None
    level: Optional[str] = None
    equipment: Optional[str] = None
    equipment_extra: List[str] = []
    days_per_week: Optional[int] = None
    preferred_weekdays: List[int] = []
    session_minutes: Optional[int] = None
    program_weeks: int = 6
    limitations: List[str] = []
    limitations_text: Optional[str] = None
    focus: List[str] = []
    rest_default_sec: int = 90
    reminder_enabled: bool = False
    reminder_time: Optional[str] = None
    reminder_id: Optional[int] = None
    onboarding_completed: bool = False


# --------------------------------------------------------------------------- #
#  §4.2 Программа
# --------------------------------------------------------------------------- #
class TrainerGenerateIn(BaseModel):
    """Запрос генерации программы. `start_date` — локальная дата клиента."""

    regenerate_note: Optional[str] = None       # пожелание при пересборке
    start_date: Optional[str] = None            # ISO; по умолчанию — сегодня на сервере

    @field_validator("regenerate_note")
    @classmethod
    def _v_note(cls, v):
        text = str(v or "").strip()
        return text[:300] or None


class TrainerWeekPhaseOut(BaseModel):
    """Одна неделя периодизации: фаза и её модификаторы."""

    week: int
    phase: str                                  # base|build|peak|deload
    weight_pct: int = 100
    sets_delta: int = 0
    label: str = ""                             # подпись фазы на языке пользователя


class TrainerPlanItemOut(BaseModel):
    """Пункт плана дня (разминка / основное упражнение / заминка)."""

    exercise_id: Optional[int] = None
    slug: Optional[str] = None
    name_ru: Optional[str] = None
    name_en: Optional[str] = None
    muscle_group: Optional[str] = None
    equipment: Optional[str] = None
    measure_type: Optional[str] = None
    sets: Optional[int] = None
    reps_min: Optional[int] = None
    reps_max: Optional[int] = None
    time_sec: Optional[int] = None
    rest_sec: Optional[int] = None
    target_weight_kg: Optional[float] = None
    rpe: Optional[int] = None
    tempo: Optional[str] = None
    note: Optional[str] = None


class TrainerProgramDayOut(BaseModel):
    """День программы: план целиком (разминка, основное, заминка)."""

    id: Optional[int] = None
    week: int = 1
    day_index: int = 1
    weekday: Optional[int] = None
    scheduled_date: Optional[str] = None
    title: str = ""
    session_type: str = "strength"
    duration_min: Optional[int] = None
    focus_muscles: List[str] = []
    warmup: List[TrainerPlanItemOut] = []
    exercises: List[TrainerPlanItemOut] = []
    cooldown: List[TrainerPlanItemOut] = []
    status: str = "planned"                     # planned|done|skipped
    session_id: Optional[int] = None
    phase: Optional[str] = None                 # фаза недели (для чипа в UI)


class TrainerProgramOut(BaseModel):
    """Программа целиком: шапка, периодизация и раскрытые дни."""

    id: int
    title: str = ""
    status: str = "active"
    split_type: Optional[str] = None
    weeks: int = 0
    days_per_week: int = 0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    current_week: int = 1
    summary: Optional[str] = None
    tips: List[str] = []
    periodization: List[TrainerWeekPhaseOut] = []
    days: List[TrainerProgramDayOut] = []


class TrainerProgramBriefOut(BaseModel):
    """Краткая карточка программы для «Сегодня» (без списка дней)."""

    id: int
    title: str = ""
    status: str = "active"
    split_type: Optional[str] = None
    weeks: int = 0
    days_per_week: int = 0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    current_week: int = 1
    phase: Optional[str] = None
    phase_label: Optional[str] = None
    days_total: int = 0
    days_done: int = 0
    summary: Optional[str] = None


# --------------------------------------------------------------------------- #
#  §4.3 Сессия (схемы; маршруты сессии добавляет следующий этап)
# --------------------------------------------------------------------------- #
class TrainerExerciseBriefOut(BaseModel):
    """Упражнение библиотеки в сокращённом виде."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: Optional[str] = None
    name_ru: Optional[str] = None
    name_en: Optional[str] = None
    muscle_group: Optional[str] = None
    equipment: Optional[str] = None
    measure_type: Optional[str] = None


class TrainerPrevSetOut(BaseModel):
    """«Прошлый раз» — сет из последней завершённой сессии с этим упражнением."""

    set_index: int
    weight_kg: Optional[float] = None
    reps: Optional[int] = None
    time_sec: Optional[int] = None


class TrainerSetOut(BaseModel):
    """Один подход в текущей сессии."""

    id: int
    set_index: int
    set_type: str = "work"                      # warmup|work|drop|failure
    weight_kg: Optional[float] = None
    reps: Optional[int] = None
    time_sec: Optional[int] = None
    rpe: Optional[float] = None
    is_done: bool = False
    is_pr: bool = False
    pr_types: List[str] = []


class TrainerSessionExerciseOut(BaseModel):
    """Упражнение внутри сессии: план, статус, «прошлый раз» и записанные сеты."""

    id: int
    exercise: Optional[TrainerExerciseBriefOut] = None
    block: str = "main"                         # warmup|main|cooldown
    order_index: int = 0
    planned_sets: Optional[int] = None
    planned_reps_min: Optional[int] = None
    planned_reps_max: Optional[int] = None
    planned_weight_kg: Optional[float] = None
    planned_time_sec: Optional[int] = None
    planned_rest_sec: Optional[int] = None
    planned_rpe: Optional[int] = None
    status: str = "pending"                     # pending|done|skipped|replaced
    note: Optional[str] = None
    previous: List[TrainerPrevSetOut] = []
    sets: List[TrainerSetOut] = []


class TrainerPrOut(BaseModel):
    """Личный рекорд, установленный сетом."""

    type: str                                   # max_weight|est_1rm|max_reps|set_volume|max_time
    value: float
    prev_value: Optional[float] = None
    exercise_id: Optional[int] = None
    exercise_name_ru: Optional[str] = None
    exercise_name_en: Optional[str] = None


class TrainerChangeOut(BaseModel):
    """Одно изменение плана после отзыва («Учёл на следующий раз»)."""

    exercise_id: Optional[int] = None
    name_ru: Optional[str] = None
    name_en: Optional[str] = None
    kind: str = "keep"                          # weight|reps|sets|keep|deload
    old_value: Optional[float] = None
    new_value: Optional[float] = None


class TrainerAdaptationOut(BaseModel):
    """Результат адаптации после отзыва: изменения + объяснения на языке пользователя."""

    changes: List[TrainerChangeOut] = []
    lines: List[str] = []
    message: str = ""


class TrainerSessionOut(BaseModel):
    """Сессия целиком (для экрана выполнения и восстановления после сворачивания)."""

    id: int
    date: Optional[str] = None
    status: str = "in_progress"                 # in_progress|completed|abandoned
    title: str = ""
    session_type: str = "strength"
    week: Optional[int] = None
    day_index: Optional[int] = None
    program_day_id: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_min: Optional[int] = None
    total_sets: int = 0
    total_reps: int = 0
    total_volume_kg: float = 0.0
    calories_burned: Optional[int] = None
    workout_id: Optional[int] = None
    feedback: Optional[str] = None
    feedback_note: Optional[str] = None
    adaptation: Optional[TrainerAdaptationOut] = None
    prs: List[TrainerPrOut] = []
    exercises: List[TrainerSessionExerciseOut] = []


class TrainerSessionStartIn(BaseModel):
    """Старт сессии: день программы (или None для внеплановой) и локальная дата клиента."""

    program_day_id: Optional[int] = None
    date: str


class TrainerSetIn(BaseModel):
    """Сохранение одного подхода (upsert по session_exercise_id + set_index)."""

    session_exercise_id: int
    set_index: int
    set_type: str = "work"
    weight_kg: Optional[float] = None
    reps: Optional[int] = None
    time_sec: Optional[int] = None
    rpe: Optional[float] = None
    is_done: bool = True

    @field_validator("set_index")
    @classmethod
    def _v_index(cls, v):
        if not (1 <= v <= 12):
            raise ValueError("set_index: допустимо 1..12")
        return v

    @field_validator("set_type")
    @classmethod
    def _v_type(cls, v):
        return _one_code(v, ("warmup", "work", "drop", "failure"), "set_type")

    @field_validator("weight_kg")
    @classmethod
    def _v_weight(cls, v):
        if v is None:
            return None
        if not (0 <= v <= 500):
            raise ValueError("weight_kg: допустимо 0..500")
        return round(float(v), 2)

    @field_validator("reps")
    @classmethod
    def _v_reps(cls, v):
        if v is None:
            return None
        if not (0 <= v <= 100):
            raise ValueError("reps: допустимо 0..100")
        return v

    @field_validator("time_sec")
    @classmethod
    def _v_time(cls, v):
        if v is None:
            return None
        if not (0 <= v <= 7200):
            raise ValueError("time_sec: допустимо 0..7200")
        return v


class TrainerSetSaveOut(BaseModel):
    """Ответ на сохранение подхода: сам сет, рекорды, время отдыха, статус упражнения."""

    set: TrainerSetOut
    prs: List[TrainerPrOut] = []
    rest_sec: int = 90
    exercise_status: str = "pending"


class TrainerReplaceIn(BaseModel):
    """Замена упражнения в сессии."""

    new_exercise_id: int
    reason: str = "other"                       # busy|no_equipment|pain|other
    remember: bool = True

    @field_validator("reason")
    @classmethod
    def _v_reason(cls, v):
        return _one_code(v, trainer_logic.REPLACE_REASONS, "reason")


class TrainerAddExerciseIn(BaseModel):
    """Добавление упражнения в текущую сессию вручную."""

    exercise_id: int
    sets: int = 3
    reps_min: Optional[int] = None
    reps_max: Optional[int] = None

    @field_validator("sets")
    @classmethod
    def _v_sets(cls, v):
        lo, hi = trainer_logic.SETS_RANGE
        if not (lo <= v <= hi):
            raise ValueError(f"sets: допустимо {lo}..{hi}")
        return v

    @field_validator("reps_min", "reps_max")
    @classmethod
    def _v_reps(cls, v):
        if v is None:
            return None
        lo, hi = trainer_logic.REPS_RANGE
        if not (lo <= v <= hi):
            raise ValueError(f"reps: допустимо {lo}..{hi}")
        return v


class TrainerFinishIn(BaseModel):
    """Завершение сессии: фактическая длительность и заметка."""

    duration_min: Optional[int] = None
    note: Optional[str] = None

    @field_validator("duration_min")
    @classmethod
    def _v_duration(cls, v):
        if v is None:
            return None
        if not (1 <= v <= 600):
            raise ValueError("duration_min: допустимо 1..600")
        return v

    @field_validator("note")
    @classmethod
    def _v_note(cls, v):
        text = str(v or "").strip()
        return text[:500] or None


class TrainerFinishSummaryOut(BaseModel):
    """Итоговые цифры завершённой тренировки."""

    duration_min: int = 0
    total_sets: int = 0
    total_reps: int = 0
    total_volume_kg: float = 0.0
    calories_burned: int = 0
    exercises_done: int = 0
    exercises_skipped: int = 0


class TrainerFinishOut(BaseModel):
    """Экран «Готово!»: сессия, итоги, рекорды и связанная запись в дневнике."""

    session: TrainerSessionOut
    summary: TrainerFinishSummaryOut
    prs: List[TrainerPrOut] = []
    workout_id: Optional[int] = None


class TrainerFeedbackIn(BaseModel):
    """Отзыв после тренировки (легко / норм / тяжело)."""

    feedback: str
    note: Optional[str] = None
    rpe: Optional[int] = None

    @field_validator("feedback")
    @classmethod
    def _v_feedback(cls, v):
        return _one_code(v, trainer_logic.FEEDBACKS, "feedback")

    @field_validator("note")
    @classmethod
    def _v_note(cls, v):
        text = str(v or "").strip()
        return text[:500] or None

    @field_validator("rpe")
    @classmethod
    def _v_rpe(cls, v):
        if v is None:
            return None
        if not (1 <= v <= 10):
            raise ValueError("rpe: допустимо 1..10")
        return v


class TrainerSessionBriefOut(BaseModel):
    """Карточка сессии в истории."""

    id: int
    date: Optional[str] = None
    title: str = ""
    session_type: str = "strength"
    duration_min: Optional[int] = None
    total_volume_kg: float = 0.0
    total_sets: int = 0
    calories_burned: Optional[int] = None
    prs_count: int = 0
    feedback: Optional[str] = None


class TrainerSessionsOut(BaseModel):
    """Постраничная история сессий."""

    items: List[TrainerSessionBriefOut] = []
    total: int = 0


# --------------------------------------------------------------------------- #
#  §4.3 «Сегодня» и §4.1 обзор
# --------------------------------------------------------------------------- #
class TrainerTodayOut(BaseModel):
    """План на дату: тренировочный день, отдых, закрытая неделя или нет программы."""

    date: str
    is_training_day: bool = False
    kind: str = "no_program"                    # planned|done|rest|week_done|no_program
    day: Optional[TrainerProgramDayOut] = None  # planned/done — день на дату; rest — ближайший
    next_date: Optional[str] = None
    next_title: Optional[str] = None            # done — название ближайшей тренировки
    active_session: Optional[TrainerSessionOut] = None


class TrainerStreakOut(BaseModel):
    """Стрик по неделям и прогресс текущей недели."""

    weeks: int = 0
    this_week_done: int = 0
    this_week_goal: int = 0


class TrainerWeekDayOut(BaseModel):
    """Точка ленты недели (Пн–Вс)."""

    date: str
    weekday: int                                # 0..6, Пн=0
    status: str = "rest"                        # done|today|planned|skipped|rest
    is_today: bool = False
    title: Optional[str] = None
    program_day_id: Optional[int] = None
    session_id: Optional[int] = None


class TrainerOverviewOut(BaseModel):
    """Один вызов для страницы «Сегодня»."""

    profile: Optional[TrainerProfileOut] = None
    program: Optional[TrainerProgramBriefOut] = None
    today: Optional[TrainerTodayOut] = None
    streak: TrainerStreakOut = TrainerStreakOut()
    week: List[TrainerWeekDayOut] = []
    active_session_id: Optional[int] = None
    pending_review: bool = False


# --------------------------------------------------------------------------- #
#  §4.4 Библиотека упражнений
# --------------------------------------------------------------------------- #
class TrainerExerciseListItemOut(TrainerExerciseBriefOut):
    """Строка списка библиотеки: бриф + сложность, категория, статус техники."""

    difficulty: int = 1
    category: Optional[str] = None
    technique_status: str = "none"              # none|ready|failed
    excluded: bool = False


class TrainerExercisesOut(BaseModel):
    """Список упражнений библиотеки с учётом фильтров."""

    items: List[TrainerExerciseListItemOut] = []
    total: int = 0


class TrainerTechniqueOut(BaseModel):
    """Техника упражнения от ИИ (кэшируется на упражнение и язык)."""

    steps: List[str] = []
    cues: List[str] = []
    mistakes: List[str] = []
    breathing: str = ""
    safety: str = ""
    muscles_text: str = ""


class TrainerExerciseOut(TrainerExerciseBriefOut):
    """Карточка упражнения: бриф + мышцы, противопоказания и техника."""

    difficulty: int = 1
    category: Optional[str] = None
    is_unilateral: bool = False
    secondary_muscles: List[str] = []
    contraindications: List[str] = []
    technique: Optional[TrainerTechniqueOut] = None
    technique_status: str = "none"
    excluded: bool = False
    disclaimer: Optional[str] = None            # при ограничениях пользователя


class TrainerAlternativesOut(BaseModel):
    """3–5 альтернатив на ту же группу мышц."""

    items: List[TrainerExerciseListItemOut] = []
    reason: str = "other"


class TrainerExcludeIn(BaseModel):
    """«Исключить из программ» / «Вернуть»."""

    excluded: bool = True


class TrainerOkOut(BaseModel):
    """Простой ответ вида {"ok": true} с необязательными полями состояния."""

    ok: bool = True
    excluded: Optional[bool] = None
    status: Optional[str] = None


# --------------------------------------------------------------------------- #
#  §4.4 Прогресс и история упражнения
# --------------------------------------------------------------------------- #
def _iso_date_or_none(value: Any, field: str) -> Optional[str]:
    """ISO-дата "YYYY-MM-DD" или None; мусор → ValueError (→ 422)."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _date_cls.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError(f"{field}: ожидается дата в формате YYYY-MM-DD, получено {value!r}")


class TrainerTotalsOut(BaseModel):
    """Итоги периода: тренировки, объём, подходы, минуты."""

    sessions: int = 0
    volume_kg: float = 0.0
    sets: int = 0
    minutes: int = 0


class TrainerWeekCompareOut(BaseModel):
    """Текущая неделя против прошлой (стрелка «вверх/вниз» на экране прогресса)."""

    this: TrainerTotalsOut = TrainerTotalsOut()
    prev: TrainerTotalsOut = TrainerTotalsOut()


class TrainerMuscleVolumeOut(BaseModel):
    """Рабочие подходы и объём по группе мышц за 7 дней с рекомендуемой зоной."""

    muscle_group: str
    sets: int = 0
    volume_kg: float = 0.0
    target_min: int = 10
    target_max: int = 20


class TrainerRecordOut(BaseModel):
    """Текущий рекорд по упражнению (одна строка на тип рекорда)."""

    exercise: Optional[TrainerExerciseBriefOut] = None
    record_type: str                            # max_weight|est_1rm|max_reps|set_volume|max_time
    value: float = 0.0
    weight_kg: Optional[float] = None
    reps: Optional[int] = None
    date: Optional[str] = None


class TrainerChartPointOut(BaseModel):
    """Точка графика по упражнению за один день."""

    date: str
    est_1rm: Optional[float] = None
    max_weight: Optional[float] = None
    volume: float = 0.0


class TrainerChartOut(BaseModel):
    """График по одному упражнению (1RM / вес / объём по дням)."""

    exercise_id: int
    exercise: Optional[TrainerExerciseBriefOut] = None
    points: List[TrainerChartPointOut] = []


class TrainerProgressOut(BaseModel):
    """Экран «Прогресс» одним вызовом (ТЗ §4.4)."""

    streak: TrainerStreakOut = TrainerStreakOut()
    totals_4w: TrainerTotalsOut = TrainerTotalsOut()
    week_compare: TrainerWeekCompareOut = TrainerWeekCompareOut()
    muscle_volume_7d: List[TrainerMuscleVolumeOut] = []
    records: List[TrainerRecordOut] = []
    top_exercises: List[TrainerExerciseBriefOut] = []
    chart: Optional[TrainerChartOut] = None
    period: str = "4w"                          # 4w|3m|all — период графика


class TrainerHistorySessionOut(BaseModel):
    """Сессия в истории упражнения: дата и записанные подходы."""

    session_id: int
    date: Optional[str] = None
    title: Optional[str] = None
    sets: List[TrainerSetOut] = []


class TrainerExerciseHistoryOut(BaseModel):
    """Вкладка «История» карточки упражнения (ТЗ §4.4)."""

    exercise: Optional[TrainerExerciseBriefOut] = None
    records: List[TrainerRecordOut] = []
    sessions: List[TrainerHistorySessionOut] = []
    points: List[TrainerChartPointOut] = []


# --------------------------------------------------------------------------- #
#  §4.5 Недельный разбор
# --------------------------------------------------------------------------- #
class TrainerReviewIn(BaseModel):
    """Запрос разбора недели. `week_start` — понедельник разбираемой недели."""

    week_start: Optional[str] = None            # ISO; по умолчанию — по правилу §4.5
    date: Optional[str] = None                  # локальная дата клиента («сегодня»)

    @field_validator("week_start")
    @classmethod
    def _v_week_start(cls, v):
        return _iso_date_or_none(v, "week_start")

    @field_validator("date")
    @classmethod
    def _v_date(cls, v):
        return _iso_date_or_none(v, "date")


class TrainerReviewChangeOut(BaseModel):
    """Одна предложенная правка на следующую неделю (чекбокс в списке изменений)."""

    id: int                                     # индекс правки в разборе
    type: str                                   # weight_pct|sets|swap|rest_sec|deload_next_week
    exercise_id: Optional[int] = None
    exercise_slug: Optional[str] = None
    exercise_name_ru: Optional[str] = None
    exercise_name_en: Optional[str] = None
    value: Optional[int] = None
    new_exercise_id: Optional[int] = None
    new_slug: Optional[str] = None
    new_exercise_name_ru: Optional[str] = None
    new_exercise_name_en: Optional[str] = None
    reason: str = ""
    applied: bool = False                       # правка уже применена к программе


class TrainerReviewBodyOut(BaseModel):
    """Тело разбора от ИИ (нормализованное)."""

    summary: str = ""
    wins: List[str] = []
    issues: List[str] = []
    nutrition: List[str] = []
    changes: List[TrainerReviewChangeOut] = []
    next_week_focus: str = ""
    motivation: str = ""


class TrainerWeeklyReviewOut(BaseModel):
    """Сохранённый недельный разбор с контекстом и статусом применения."""

    id: int
    week_start: Optional[str] = None
    week_end: Optional[str] = None
    week: Optional[int] = None
    program_id: Optional[int] = None
    stats: Dict[str, Any] = {}
    review: TrainerReviewBodyOut = TrainerReviewBodyOut()
    applied: bool = False
    created_at: Optional[str] = None
    disclaimer: Optional[str] = None


class TrainerReviewApplyIn(BaseModel):
    """Выбранные пользователем правки разбора («Применить к следующей неделе»)."""

    change_ids: List[int] = []

    @field_validator("change_ids")
    @classmethod
    def _v_ids(cls, v):
        if v is None:
            return []
        if not isinstance(v, (list, tuple, set)):
            raise ValueError("change_ids: ожидается список индексов правок")
        out: List[int] = []
        for raw in v:
            try:
                cid = int(raw)
            except (TypeError, ValueError):
                raise ValueError(f"change_ids: недопустимое значение {raw!r}")
            if cid < 0:
                raise ValueError("change_ids: индекс правки не может быть отрицательным")
            if cid not in out:
                out.append(cid)
        return out[:20]


class TrainerReviewApplyOut(BaseModel):
    """Результат применения правок: что применено и человеческие объяснения."""

    applied: List[int] = []
    lines: List[str] = []
    next_week: Optional[int] = None
    ok: bool = True


# --------------------------------------------------------------------------- #
#  §4.5 Совет дня по питанию
# --------------------------------------------------------------------------- #
class TrainerNutritionNumbersOut(BaseModel):
    """Цифры дня, на которые опирается совет (из дневника и профиля)."""

    goal_kcal: Optional[int] = None
    eaten_kcal: int = 0
    burned_kcal: int = 0
    protein_goal: Optional[int] = None
    protein_eaten: int = 0


class TrainerNutritionTipOut(BaseModel):
    """Карточка «Питание сегодня» (кэш TrainerDailyTip на дату, вид дня и язык)."""

    date: str
    kind: str = "rest"                          # training|rest
    headline: str = ""
    calories_note: str = ""
    protein_note: str = ""
    pre_workout: Optional[str] = None
    post_workout: Optional[str] = None
    hydration: str = ""
    tips: List[str] = []
    numbers: TrainerNutritionNumbersOut = TrainerNutritionNumbersOut()
