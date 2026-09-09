"""Маршруты AI-тренера (ТЗ §4): профиль, программа, «Сегодня», библиотека.

Отдельный роутер `/trainer`, чтобы не толкаться в `main.py`: в приложении
делается одна вставка `app.include_router(trainer.router)` выше `app.mount`.
Зависимости берём из существующих модулей (`backend.auth.get_current_user`
через `subscription.require_premium`, `backend.database.get_db`) — циклического
импорта нет.

Правила раздела:
  * ВСЕ маршруты — под `Depends(subscription.require_premium)` (402 без подписки);
  * ИИ-вызовы — через `ratelimit.enforce_ai` / `enforce_heavy`; `AIError` и
    `RuntimeError` → 502 с русским текстом (сырой ответ — только при DEBUG_AI);
  * бизнес-логика без побочных эффектов живёт в `trainer_logic`, промпты — в
    `trainer_ai`; здесь только БД, валидация и сериализация.

Структура файла: сначала общие хелперы и сериализаторы (их переиспользуют
следующие этапы — сессия, прогресс, разбор), затем маршруты. Новые маршруты
дописываются В КОНЕЦ файла; порядок объявления важен только там, где статический
путь конкурирует с параметром (`/session/active` должен идти ДО
`/session/{session_id}`).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date as date_cls, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import ai_service, fitness, models as M, ratelimit, subscription, trainer_ai, trainer_logic
from backend.ai_service import AIError
from backend.database import get_db
from backend.models import User
from backend.trainer_schemas import (
    TrainerAdaptationOut,
    TrainerAddExerciseIn,
    TrainerAlternativesOut,
    TrainerChartOut,
    TrainerChartPointOut,
    TrainerChangeOut,
    TrainerExerciseHistoryOut,
    TrainerExerciseListItemOut,
    TrainerExerciseOut,
    TrainerExercisesOut,
    TrainerExcludeIn,
    TrainerExerciseBriefOut,
    TrainerFeedbackIn,
    TrainerFinishIn,
    TrainerFinishOut,
    TrainerFinishSummaryOut,
    TrainerGenerateIn,
    TrainerHistorySessionOut,
    TrainerMuscleVolumeOut,
    TrainerNutritionNumbersOut,
    TrainerNutritionTipOut,
    TrainerOkOut,
    TrainerOverviewOut,
    TrainerPlanItemOut,
    TrainerPrOut,
    TrainerPrevSetOut,
    TrainerProfileIn,
    TrainerProgressOut,
    TrainerProfileOut,
    TrainerProgramBriefOut,
    TrainerProgramDayOut,
    TrainerProgramOut,
    TrainerRecordOut,
    TrainerReplaceIn,
    TrainerReviewApplyIn,
    TrainerReviewApplyOut,
    TrainerReviewBodyOut,
    TrainerReviewChangeOut,
    TrainerReviewIn,
    TrainerSessionBriefOut,
    TrainerSessionExerciseOut,
    TrainerSessionOut,
    TrainerSessionStartIn,
    TrainerSessionsOut,
    TrainerSetIn,
    TrainerSetOut,
    TrainerSetSaveOut,
    TrainerStreakOut,
    TrainerTechniqueOut,
    TrainerTodayOut,
    TrainerTotalsOut,
    TrainerWeekCompareOut,
    TrainerWeekDayOut,
    TrainerWeekPhaseOut,
    TrainerWeeklyReviewOut,
)

logger = logging.getLogger("trainer")

# Режим отладки ИИ: при включении в 502 попадает сырой ответ модели.
# Читаем переменную здесь же, чтобы не импортировать main (циклический импорт).
DEBUG_AI = os.getenv("DEBUG_AI") == "1"

router = APIRouter(prefix="/trainer", tags=["trainer"])

# Дисклеймер для экранов с медицинским контекстом (техника при ограничениях).
DISCLAIMER_RU = (
    "Тренер не врач. При травмах и хронических состояниях проконсультируйтесь со специалистом."
)
DISCLAIMER_EN = (
    "The coach is not a doctor. With injuries or chronic conditions consult a specialist."
)


# --------------------------------------------------------------------------- #
#  Общие хелперы: язык, JSON-поля, ошибки ИИ
# --------------------------------------------------------------------------- #
def user_lang(user: User) -> str:
    """Язык пользователя для текстов и промптов ("ru" по умолчанию)."""
    return (getattr(user, "language", None) or "ru").strip().lower() or "ru"


def is_en(lang: str) -> bool:
    return str(lang or "ru").lower().startswith("en")


def json_list(value) -> list:
    """Список из JSON-поля Text; мусор → []."""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def json_obj(value) -> dict:
    """Словарь из JSON-поля Text; мусор → {}."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def dumps(value) -> str:
    """JSON для хранения в Text-колонке (с кириллицей как есть)."""
    return json.dumps(value, ensure_ascii=False)


def today_str(value: Optional[str] = None) -> str:
    """Дата запроса: значение клиента (ISO) либо сегодняшняя дата сервера."""
    parsed = trainer_logic._parse_date(value)
    return (parsed or date_cls.today()).isoformat()


def ai_failed(exc: Exception, message: str, tag: str) -> HTTPException:
    """AIError/RuntimeError → HTTPException 502 с русским текстом (raw — при DEBUG_AI)."""
    if isinstance(exc, AIError):
        logger.warning(
            "trainer/%s: %s | finish=%s raw=%s",
            tag, exc, exc.finish_reason, (exc.raw or "")[:600],
        )
        detail = message
        if DEBUG_AI:
            detail = f"{exc} | finish={exc.finish_reason} | raw={(exc.raw or 'пусто')[:1500]}"
    else:
        logger.warning("trainer/%s: %s", tag, exc)
        detail = message
        if DEBUG_AI:
            detail = str(exc)
    return HTTPException(status_code=502, detail=detail)


# --------------------------------------------------------------------------- #
#  Общие хелперы: выборки из БД
# --------------------------------------------------------------------------- #
def get_profile(db: Session, tid: int):
    """Анкета тренера пользователя или None."""
    return (
        db.query(M.TrainerProfile)
        .filter(M.TrainerProfile.telegram_id == tid)
        .first()
    )


def get_profile_or_404(db: Session, tid: int):
    """Анкета тренера; её отсутствие — 404 (фронт уводит в онбординг)."""
    profile = get_profile(db, tid)
    if profile is None:
        raise HTTPException(status_code=404, detail="Профиль тренера не найден")
    return profile


def get_active_program(db: Session, tid: int):
    """Текущая активная программа пользователя или None."""
    return (
        db.query(M.TrainerProgram)
        .filter(M.TrainerProgram.telegram_id == tid, M.TrainerProgram.status == "active")
        .order_by(M.TrainerProgram.id.desc())
        .first()
    )


def get_program_days(db: Session, program) -> list:
    """Раскрытые дни программы по (неделя, номер дня)."""
    if program is None:
        return []
    return (
        db.query(M.TrainerProgramDay)
        .filter(M.TrainerProgramDay.program_id == program.id)
        .order_by(M.TrainerProgramDay.week.asc(), M.TrainerProgramDay.day_index.asc())
        .all()
    )


def get_active_session(db: Session, tid: int):
    """Незавершённая сессия пользователя или None."""
    return (
        db.query(M.TrainerSession)
        .filter(M.TrainerSession.telegram_id == tid, M.TrainerSession.status == "in_progress")
        .order_by(M.TrainerSession.id.desc())
        .first()
    )


def get_exercise_or_404(db: Session, exercise_id: int):
    """Упражнение библиотеки; отсутствие — 404."""
    exercise = (
        db.query(M.TrainerExercise)
        .filter(M.TrainerExercise.id == exercise_id)
        .first()
    )
    if exercise is None:
        raise HTTPException(status_code=404, detail="Упражнение не найдено")
    return exercise


def exercises_by_id(db: Session, ids) -> dict:
    """{id: TrainerExercise} для набора идентификаторов (один запрос)."""
    ids = [i for i in {int(x) for x in ids if x is not None}]
    if not ids:
        return {}
    rows = db.query(M.TrainerExercise).filter(M.TrainerExercise.id.in_(ids)).all()
    return {row.id: row for row in rows}


def exercises_by_slug(db: Session, slugs) -> dict:
    """{slug: TrainerExercise} для набора slug (один запрос)."""
    slugs = [s for s in {str(x) for x in slugs if x}]
    if not slugs:
        return {}
    rows = db.query(M.TrainerExercise).filter(M.TrainerExercise.slug.in_(slugs)).all()
    return {row.slug: row for row in rows}


def excluded_exercise_ids(db: Session, tid: int) -> list:
    """Идентификаторы упражнений, исключённых пользователем «навсегда»."""
    return [
        row.exercise_id
        for row in db.query(M.TrainerExerciseState)
        .filter(
            M.TrainerExerciseState.telegram_id == tid,
            M.TrainerExerciseState.excluded.is_(True),
        )
        .all()
        if row.exercise_id is not None
    ]


def get_or_create_state(db: Session, tid: int, exercise_id: int):
    """Состояние прогрессии по упражнению (создаётся при первом обращении)."""
    state = (
        db.query(M.TrainerExerciseState)
        .filter(
            M.TrainerExerciseState.telegram_id == tid,
            M.TrainerExerciseState.exercise_id == exercise_id,
        )
        .first()
    )
    if state is None:
        state = M.TrainerExerciseState(telegram_id=tid, exercise_id=exercise_id)
        db.add(state)
        db.flush()
    return state


def active_catalog(db: Session) -> list:
    """Активная библиотека упражнений (для промпта и альтернатив)."""
    return (
        db.query(M.TrainerExercise)
        .filter(M.TrainerExercise.is_active.is_(True))
        .order_by(M.TrainerExercise.id.asc())
        .all()
    )


# --------------------------------------------------------------------------- #
#  Сериализаторы (используются и следующими этапами: сессия, прогресс, разбор)
# --------------------------------------------------------------------------- #
def profile_out(profile) -> TrainerProfileOut:
    """ORM TrainerProfile → схема (JSON-поля разворачиваются в списки)."""
    return TrainerProfileOut(
        id=profile.id,
        goal=profile.goal,
        level=profile.level,
        equipment=profile.equipment,
        equipment_extra=[str(x) for x in json_list(profile.equipment_extra_json)],
        days_per_week=profile.days_per_week,
        preferred_weekdays=trainer_logic._csv_to_weekdays(profile.preferred_weekdays),
        session_minutes=profile.session_minutes,
        program_weeks=profile.program_weeks or 6,
        limitations=[str(x) for x in json_list(profile.limitations_json)],
        limitations_text=profile.limitations_text,
        focus=[str(x) for x in json_list(profile.focus_json)],
        rest_default_sec=profile.rest_default_sec or 90,
        reminder_enabled=bool(profile.reminder_enabled),
        reminder_time=profile.reminder_time,
        reminder_id=profile.reminder_id,
        onboarding_completed=bool(profile.onboarding_completed),
    )


def exercise_brief_out(exercise) -> Optional[TrainerExerciseBriefOut]:
    """ORM TrainerExercise → краткая схема."""
    if exercise is None:
        return None
    return TrainerExerciseBriefOut(
        id=exercise.id,
        slug=exercise.slug,
        name_ru=exercise.name_ru,
        name_en=exercise.name_en,
        muscle_group=exercise.muscle_group,
        equipment=exercise.equipment,
        measure_type=exercise.measure_type,
    )


def exercise_item_out(exercise, excluded: bool = False) -> TrainerExerciseListItemOut:
    """ORM TrainerExercise → строка списка библиотеки."""
    return TrainerExerciseListItemOut(
        id=exercise.id,
        slug=exercise.slug,
        name_ru=exercise.name_ru,
        name_en=exercise.name_en,
        muscle_group=exercise.muscle_group,
        equipment=exercise.equipment,
        measure_type=exercise.measure_type,
        difficulty=exercise.difficulty or 1,
        category=exercise.category,
        technique_status=exercise.technique_status or "none",
        excluded=bool(excluded),
    )


def plan_item_out(item: dict, exercise=None) -> TrainerPlanItemOut:
    """Пункт warmup_json/exercises_json/cooldown_json → схема плана.

    Имена и характеристики упражнения подставляем из библиотеки, чтобы фронт не
    ходил за ними отдельно.
    """
    item = item if isinstance(item, dict) else {}
    return TrainerPlanItemOut(
        exercise_id=(exercise.id if exercise is not None else item.get("exercise_id")),
        slug=(exercise.slug if exercise is not None else item.get("slug")),
        name_ru=(exercise.name_ru if exercise is not None else None),
        name_en=(exercise.name_en if exercise is not None else None),
        muscle_group=(exercise.muscle_group if exercise is not None else item.get("muscle_group")),
        equipment=(exercise.equipment if exercise is not None else None),
        measure_type=(exercise.measure_type if exercise is not None else None),
        sets=item.get("sets"),
        reps_min=item.get("reps_min", item.get("reps")),
        reps_max=item.get("reps_max"),
        time_sec=item.get("time_sec"),
        rest_sec=item.get("rest_sec"),
        target_weight_kg=item.get("start_weight_kg", item.get("target_weight_kg")),
        rpe=item.get("rpe"),
        tempo=item.get("tempo"),
        note=item.get("note"),
    )


def _plan_block(items, ex_by_id: dict, ex_by_slug: dict) -> list:
    """Список пунктов блока плана с подставленными упражнениями."""
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        exercise = ex_by_id.get(item.get("exercise_id")) or ex_by_slug.get(item.get("slug"))
        out.append(plan_item_out(item, exercise))
    return out


def program_day_out(day, ex_by_id: dict, ex_by_slug: dict, phase: Optional[str] = None) -> TrainerProgramDayOut:
    """ORM TrainerProgramDay → схема дня программы."""
    return TrainerProgramDayOut(
        id=day.id,
        week=day.week or 1,
        day_index=day.day_index or 1,
        weekday=day.weekday,
        scheduled_date=day.scheduled_date,
        title=day.title or "",
        session_type=day.session_type or "strength",
        duration_min=day.duration_min,
        focus_muscles=[str(x) for x in json_list(day.focus_muscles_json)],
        warmup=_plan_block(json_list(day.warmup_json), ex_by_id, ex_by_slug),
        exercises=_plan_block(json_list(day.exercises_json), ex_by_id, ex_by_slug),
        cooldown=_plan_block(json_list(day.cooldown_json), ex_by_id, ex_by_slug),
        status=day.status or "planned",
        session_id=day.session_id,
        phase=phase,
    )


def day_catalog(db: Session, days) -> tuple:
    """Пары словарей (по id, по slug) для всех упражнений, упомянутых в днях."""
    ids: set = set()
    slugs: set = set()
    for day in days:
        for field in ("warmup_json", "exercises_json", "cooldown_json"):
            for item in json_list(getattr(day, field, None)):
                if not isinstance(item, dict):
                    continue
                if item.get("exercise_id") is not None:
                    ids.add(item.get("exercise_id"))
                if item.get("slug"):
                    slugs.add(item.get("slug"))
    return exercises_by_id(db, ids), exercises_by_slug(db, slugs)


def periodization_out(program, lang: str) -> list:
    """periodization_json → список фаз с подписью на языке пользователя."""
    raw = json_list(program.periodization_json)
    weeks = program.weeks or len(raw)
    out = []
    for week in range(1, (weeks or 0) + 1):
        mod = trainer_logic.week_modifier(raw, week, weeks)
        out.append(
            TrainerWeekPhaseOut(
                week=week,
                phase=mod["phase"],
                weight_pct=mod["weight_pct"],
                sets_delta=mod["sets_delta"],
                label=mod["label_en"] if is_en(lang) else mod["label_ru"],
            )
        )
    return out


def program_out(db: Session, program, lang: str, today: Optional[str] = None) -> TrainerProgramOut:
    """Программа целиком: шапка, периодизация, раскрытые дни."""
    days = get_program_days(db, program)
    ex_by_id, ex_by_slug = day_catalog(db, days)
    periodization = periodization_out(program, lang)
    phases = {p.week: p.phase for p in periodization}
    return TrainerProgramOut(
        id=program.id,
        title=program.title or "",
        status=program.status or "active",
        split_type=program.split_type,
        weeks=program.weeks or 0,
        days_per_week=program.days_per_week or 0,
        start_date=program.start_date,
        end_date=program.end_date,
        current_week=trainer_logic.current_week(days, today, program.weeks),
        summary=program.summary,
        tips=[str(x) for x in json_list(program.tips_json)],
        periodization=periodization,
        days=[
            program_day_out(day, ex_by_id, ex_by_slug, phases.get(day.week or 1))
            for day in days
        ],
    )


def program_brief_out(program, days: list, lang: str, today: Optional[str] = None) -> TrainerProgramBriefOut:
    """Краткая карточка программы для «Сегодня»."""
    week = trainer_logic.current_week(days, today, program.weeks)
    mod = trainer_logic.week_modifier(json_list(program.periodization_json), week, program.weeks)
    return TrainerProgramBriefOut(
        id=program.id,
        title=program.title or "",
        status=program.status or "active",
        split_type=program.split_type,
        weeks=program.weeks or 0,
        days_per_week=program.days_per_week or 0,
        start_date=program.start_date,
        end_date=program.end_date,
        current_week=week,
        phase=mod["phase"],
        phase_label=mod["label_en"] if is_en(lang) else mod["label_ru"],
        days_total=len(days),
        days_done=sum(1 for d in days if (d.status or "planned") == "done"),
        summary=program.summary,
    )


def set_out(row) -> TrainerSetOut:
    """ORM TrainerSetLog → схема подхода."""
    return TrainerSetOut(
        id=row.id,
        set_index=row.set_index or 1,
        set_type=row.set_type or "work",
        weight_kg=row.weight_kg,
        reps=row.reps,
        time_sec=row.time_sec,
        rpe=row.rpe,
        is_done=bool(row.is_done),
        is_pr=bool(row.is_pr),
        pr_types=[str(x) for x in json_list(row.pr_types_json)],
    )


def previous_sets(db: Session, tid: int, exercise_ids, exclude_session_id: Optional[int] = None) -> dict:
    """{exercise_id: [TrainerPrevSetOut]} — сеты последней завершённой сессии.

    Берём только выполненные рабочие подходы (разминочные в «прошлый раз» не
    показываем) и только из одной — самой свежей — сессии по каждому упражнению.
    """
    ids = [int(x) for x in {i for i in exercise_ids if i is not None}]
    if not ids:
        return {}
    query = db.query(M.TrainerSetLog).filter(
        M.TrainerSetLog.telegram_id == tid,
        M.TrainerSetLog.exercise_id.in_(ids),
        M.TrainerSetLog.is_done.is_(True),
        # Разминочные подходы в «прошлый раз» не показываем (set_type может быть NULL).
        (M.TrainerSetLog.set_type.is_(None)) | (M.TrainerSetLog.set_type != "warmup"),
    )
    if exclude_session_id is not None:
        query = query.filter(M.TrainerSetLog.session_id != exclude_session_id)
    rows = query.order_by(
        M.TrainerSetLog.date.desc(),
        M.TrainerSetLog.session_id.desc(),
        M.TrainerSetLog.set_index.asc(),
    ).all()

    out: dict = {}
    source_session: dict = {}
    for row in rows:
        ex_id = row.exercise_id
        source_session.setdefault(ex_id, row.session_id)
        if row.session_id != source_session[ex_id]:
            continue
        out.setdefault(ex_id, []).append(
            TrainerPrevSetOut(
                set_index=row.set_index or 1,
                weight_kg=row.weight_kg,
                reps=row.reps,
                time_sec=row.time_sec,
            )
        )
    return out


def pr_out(raw: dict, ex_by_id: dict) -> TrainerPrOut:
    """Элемент prs_json → схема рекорда (имена упражнения — из библиотеки)."""
    raw = raw if isinstance(raw, dict) else {}
    exercise = ex_by_id.get(raw.get("exercise_id"))
    return TrainerPrOut(
        type=str(raw.get("type") or ""),
        value=float(raw.get("value") or 0.0),
        prev_value=raw.get("prev_value", raw.get("prev")),
        exercise_id=raw.get("exercise_id"),
        exercise_name_ru=(exercise.name_ru if exercise is not None else raw.get("exercise_name_ru")),
        exercise_name_en=(exercise.name_en if exercise is not None else raw.get("exercise_name_en")),
    )


def adaptation_out(raw) -> Optional[TrainerAdaptationOut]:
    """adaptation_json → схема адаптации («Учёл на следующий раз»)."""
    data = json_obj(raw)
    if not data:
        return None
    changes = []
    for item in data.get("changes") or []:
        if not isinstance(item, dict):
            continue
        changes.append(
            TrainerChangeOut(
                exercise_id=item.get("exercise_id"),
                name_ru=item.get("name_ru"),
                name_en=item.get("name_en"),
                kind=str(item.get("kind") or "keep"),
                old_value=item.get("old_value"),
                new_value=item.get("new_value"),
            )
        )
    return TrainerAdaptationOut(
        changes=changes,
        lines=[str(x) for x in (data.get("lines") or []) if isinstance(x, str)],
        message=str(data.get("message") or ""),
    )


def session_out(db: Session, session) -> Optional[TrainerSessionOut]:
    """ORM TrainerSession → полная схема сессии (упражнения, сеты, «прошлый раз»)."""
    if session is None:
        return None
    rows = (
        db.query(M.TrainerSessionExercise)
        .filter(M.TrainerSessionExercise.session_id == session.id)
        .order_by(M.TrainerSessionExercise.order_index.asc(), M.TrainerSessionExercise.id.asc())
        .all()
    )
    ex_by_id = exercises_by_id(db, [r.exercise_id for r in rows])
    sets = (
        db.query(M.TrainerSetLog)
        .filter(M.TrainerSetLog.session_id == session.id)
        .order_by(M.TrainerSetLog.set_index.asc(), M.TrainerSetLog.id.asc())
        .all()
    )
    sets_by_sex: dict = {}
    for row in sets:
        sets_by_sex.setdefault(row.session_exercise_id, []).append(set_out(row))
    prev = previous_sets(db, session.telegram_id, [r.exercise_id for r in rows], session.id)

    exercises = [
        TrainerSessionExerciseOut(
            id=row.id,
            exercise=exercise_brief_out(ex_by_id.get(row.exercise_id)),
            block=row.block or "main",
            order_index=row.order_index or 0,
            planned_sets=row.planned_sets,
            planned_reps_min=row.planned_reps_min,
            planned_reps_max=row.planned_reps_max,
            planned_weight_kg=row.planned_weight_kg,
            planned_time_sec=row.planned_time_sec,
            planned_rest_sec=row.planned_rest_sec,
            planned_rpe=row.planned_rpe,
            status=row.status or "pending",
            note=row.note,
            previous=prev.get(row.exercise_id, []),
            sets=sets_by_sex.get(row.id, []),
        )
        for row in rows
    ]
    prs_raw = json_list(session.prs_json)
    pr_ex = exercises_by_id(db, [p.get("exercise_id") for p in prs_raw if isinstance(p, dict)])
    return TrainerSessionOut(
        id=session.id,
        date=session.date,
        status=session.status or "in_progress",
        title=session.title or "",
        session_type=session.session_type or "strength",
        week=session.week,
        day_index=session.day_index,
        program_day_id=session.program_day_id,
        started_at=session.started_at.isoformat() if session.started_at else None,
        finished_at=session.finished_at.isoformat() if session.finished_at else None,
        duration_min=session.duration_min,
        total_sets=session.total_sets or 0,
        total_reps=session.total_reps or 0,
        total_volume_kg=session.total_volume_kg or 0.0,
        calories_burned=session.calories_burned,
        workout_id=session.workout_id,
        feedback=session.feedback,
        feedback_note=session.feedback_note,
        adaptation=adaptation_out(session.adaptation_json),
        prs=[pr_out(p, pr_ex) for p in prs_raw if isinstance(p, dict)],
        exercises=exercises,
    )


# --------------------------------------------------------------------------- #
#  Хелперы «Сегодня»: план дня, лента недели, стрик, готовность разбора
# --------------------------------------------------------------------------- #
def today_payload(db: Session, tid: int, program, days: list, date_value: str) -> TrainerTodayOut:
    """План на дату (ТЗ §4.3): planned / done / rest / week_done / no_program."""
    active = get_active_session(db, tid)
    session = session_out(db, active) if active is not None else None
    if program is None:
        return TrainerTodayOut(date=date_value, kind="no_program", active_session=session)

    ex_by_id, ex_by_slug = day_catalog(db, days)
    # Здесь важна только фаза (код), не её подпись — язык роли не играет.
    phases = {p.week: p.phase for p in periodization_out(program, "ru")}

    def serialize(day):
        return program_day_out(day, ex_by_id, ex_by_slug, phases.get(day.week or 1))

    planned = [d for d in days if (d.status or "planned") == "planned"]
    today_day = next((d for d in planned if d.scheduled_date == date_value), None)
    if today_day is not None:
        return TrainerTodayOut(
            date=date_value,
            is_training_day=True,
            kind="planned",
            day=serialize(today_day),
            next_date=today_day.scheduled_date,
            active_session=session,
        )

    # Ближайшая будущая тренировка; если план весь в прошлом — первый planned
    # по (неделя, номер дня), как описано в ТЗ §4.3.
    future = sorted(
        (d for d in planned if (d.scheduled_date or "") >= date_value),
        key=lambda d: (d.scheduled_date or "", d.week or 0, d.day_index or 0),
    )
    upcoming = future[0] if future else trainer_logic.next_planned_day(planned)

    # Тренировка на эту дату уже закрыта (сделана или пропущена) — показываем
    # итог дня и ближайшую следующую, а не «день отдыха».
    done_today = next(
        (d for d in days if d.scheduled_date == date_value and (d.status or "") in ("done", "skipped")),
        None,
    )
    if done_today is not None:
        return TrainerTodayOut(
            date=date_value,
            is_training_day=True,
            kind="done",
            day=serialize(done_today),
            next_date=upcoming.scheduled_date if upcoming is not None else None,
            next_title=upcoming.title if upcoming is not None else None,
            active_session=session,
        )

    if upcoming is None:
        # Плановых дней не осталось — программа пройдена целиком.
        return TrainerTodayOut(date=date_value, kind="week_done", active_session=session)

    week_end = trainer_logic.week_bounds(date_value)[1]
    next_date = upcoming.scheduled_date
    # Ближайшая тренировка уже за пределами текущей недели → неделя закрыта.
    kind = "week_done" if (next_date or "") > week_end else "rest"
    return TrainerTodayOut(
        date=date_value,
        is_training_day=False,
        kind=kind,
        day=serialize(upcoming),
        next_date=next_date,
        active_session=session,
    )


def week_strip(db: Session, tid: int, days: list, date_value: str) -> list:
    """Лента Пн–Вс: статус каждого дня недели, содержащей дату."""
    monday_str, sunday_str = trainer_logic.week_bounds(date_value)
    monday = date_cls.fromisoformat(monday_str)
    day_by_date = {d.scheduled_date: d for d in days if d.scheduled_date}
    sessions = (
        db.query(M.TrainerSession)
        .filter(
            M.TrainerSession.telegram_id == tid,
            M.TrainerSession.status == "completed",
            M.TrainerSession.date >= monday_str,
            M.TrainerSession.date <= sunday_str,
        )
        .order_by(M.TrainerSession.id.asc())
        .all()
    )
    session_by_date = {s.date: s for s in sessions}

    out = []
    for offset in range(7):
        current = (monday + timedelta(days=offset)).isoformat()
        day = day_by_date.get(current)
        session = session_by_date.get(current)
        day_status = (day.status or "planned") if day is not None else None
        if session is not None or day_status == "done":
            status = "done"
        elif day_status == "skipped":
            status = "skipped"
        elif current == date_value:
            status = "today" if day is not None else "rest"
        elif day is not None:
            status = "planned"
        else:
            status = "rest"
        out.append(
            TrainerWeekDayOut(
                date=current,
                weekday=offset,
                status=status,
                is_today=(current == date_value),
                title=(day.title if day is not None else (session.title if session is not None else None)),
                program_day_id=(day.id if day is not None else None),
                session_id=(session.id if session is not None else (day.session_id if day is not None else None)),
            )
        )
    return out


def completed_session_dates(db: Session, tid: int) -> list:
    """Даты всех завершённых сессий пользователя (для стрика и разбора)."""
    return [
        row.date
        for row in db.query(M.TrainerSession.date)
        .filter(M.TrainerSession.telegram_id == tid, M.TrainerSession.status == "completed")
        .all()
        if row.date
    ]


def streak_out(dates: list, days: list, profile, date_value: str) -> TrainerStreakOut:
    """Стрик по неделям + прогресс текущей недели (сделано из запланированного)."""
    monday_str, sunday_str = trainer_logic.week_bounds(date_value)
    done = sum(1 for d in dates if monday_str <= d <= sunday_str)
    goal = sum(1 for d in days if d.scheduled_date and monday_str <= d.scheduled_date <= sunday_str)
    if not goal:
        goal = (profile.days_per_week or 0) if profile is not None else 0
    return TrainerStreakOut(
        weeks=trainer_logic.weekly_streak(dates, date_value),
        this_week_done=done,
        this_week_goal=goal,
    )


def pending_review(db: Session, tid: int, dates: list, date_value: str) -> bool:
    """Есть ли неделя с тренировками, по которой разбор ещё не сделан."""
    if not dates:
        return False
    week_start = trainer_logic.pick_review_week_start(dates, date_value)
    week_end = (date_cls.fromisoformat(week_start) + timedelta(days=6)).isoformat()
    if not any(week_start <= d <= week_end for d in dates):
        return False
    exists = (
        db.query(M.TrainerWeeklyReview)
        .filter(
            M.TrainerWeeklyReview.telegram_id == tid,
            M.TrainerWeeklyReview.week_start == week_start,
        )
        .first()
    )
    return exists is None


# --------------------------------------------------------------------------- #
#  §4.1 Обзор и профиль
# --------------------------------------------------------------------------- #
@router.get("/overview", response_model=TrainerOverviewOut)
def trainer_overview(
    date: Optional[str] = Query(None, description="Локальная дата клиента, YYYY-MM-DD"),
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerOverviewOut:
    """Всё для страницы «Сегодня» одним вызовом (ТЗ §4.1).

    Профиль, активная программа, план на дату, стрик, лента недели,
    идентификатор незавершённой сессии и флаг готовности недельного разбора.
    """
    tid = user.telegram_id
    lang = user_lang(user)
    day_value = today_str(date)

    profile = get_profile(db, tid)
    program = get_active_program(db, tid)
    days = get_program_days(db, program)
    dates = completed_session_dates(db, tid)
    active = get_active_session(db, tid)

    return TrainerOverviewOut(
        profile=profile_out(profile) if profile is not None else None,
        program=program_brief_out(program, days, lang, day_value) if program is not None else None,
        today=today_payload(db, tid, program, days, day_value),
        streak=streak_out(dates, days, profile, day_value),
        week=week_strip(db, tid, days, day_value),
        active_session_id=(active.id if active is not None else None),
        pending_review=pending_review(db, tid, dates, day_value),
    )


@router.get("/profile", response_model=TrainerProfileOut)
def trainer_profile_get(
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerProfileOut:
    """Анкета тренера текущего пользователя (404 — онбординг не пройден)."""
    return profile_out(get_profile_or_404(db, user.telegram_id))


@router.post("/profile", response_model=TrainerProfileOut)
def trainer_profile_save(
    data: TrainerProfileIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerProfileOut:
    """Сохранить анкету онбординга (создать или обновить) — ТЗ §4.1.

    Побочный эффект: создаём/обновляем строку TrainingReminder (дни = дни
    тренировок, время из анкеты). При выключенном напоминании строку НЕ удаляем,
    а гасим флагом `enabled=False`, чтобы не терять историю.
    """
    tid = user.telegram_id
    profile = get_profile(db, tid)
    if profile is None:
        profile = M.TrainerProfile(telegram_id=tid)
        db.add(profile)

    profile.goal = data.goal
    profile.level = data.level
    profile.equipment = data.equipment
    profile.equipment_extra_json = dumps(data.equipment_extra)
    profile.days_per_week = data.days_per_week
    profile.preferred_weekdays = trainer_logic._weekdays_to_csv(data.preferred_weekdays)
    profile.session_minutes = data.session_minutes
    profile.program_weeks = data.program_weeks
    profile.limitations_json = dumps(data.limitations)
    profile.limitations_text = data.limitations_text
    profile.focus_json = dumps(data.focus)
    profile.reminder_enabled = bool(data.reminder_enabled)
    profile.reminder_time = data.reminder_time or profile.reminder_time or "18:00"
    profile.onboarding_completed = True
    if profile.rest_default_sec is None:
        profile.rest_default_sec = 90

    # Напоминание о тренировке: одна строка на профиль тренера (по reminder_id).
    reminder = None
    if profile.reminder_id:
        reminder = (
            db.query(M.TrainingReminder)
            .filter(
                M.TrainingReminder.id == profile.reminder_id,
                M.TrainingReminder.telegram_id == tid,
            )
            .first()
        )
    try:
        time_str = trainer_logic._normalize_time(profile.reminder_time)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    weekdays_csv = trainer_logic._weekdays_to_csv(data.preferred_weekdays)

    if reminder is None and data.reminder_enabled:
        reminder = M.TrainingReminder(
            telegram_id=tid,
            weekdays=weekdays_csv,
            time=time_str,
            enabled=True,
        )
        db.add(reminder)
        db.flush()
    elif reminder is not None:
        reminder.weekdays = weekdays_csv
        reminder.time = time_str
        reminder.enabled = bool(data.reminder_enabled)
    if reminder is not None:
        profile.reminder_id = reminder.id

    db.commit()
    db.refresh(profile)
    return profile_out(profile)


# --------------------------------------------------------------------------- #
#  §4.2 Программа: генерация, чтение, архивация
# --------------------------------------------------------------------------- #
def _ai_profile_dict(db: Session, tid: int, profile, regenerate_note: Optional[str]) -> dict:
    """Анкета в виде словаря для промпта (плюс известные рабочие веса)."""
    known: dict = {}
    states = (
        db.query(M.TrainerExerciseState)
        .filter(
            M.TrainerExerciseState.telegram_id == tid,
            M.TrainerExerciseState.working_weight_kg.isnot(None),
        )
        .all()
    )
    if states:
        ex_by_id = exercises_by_id(db, [s.exercise_id for s in states])
        for state in states:
            exercise = ex_by_id.get(state.exercise_id)
            if exercise is None or not state.working_weight_kg:
                continue
            known[exercise.slug] = state.working_weight_kg
    return {
        "goal": profile.goal,
        "level": profile.level,
        "equipment": profile.equipment,
        "equipment_extra": [str(x) for x in json_list(profile.equipment_extra_json)],
        "days_per_week": profile.days_per_week,
        "preferred_weekdays": trainer_logic._csv_to_weekdays(profile.preferred_weekdays),
        "session_minutes": profile.session_minutes,
        "program_weeks": profile.program_weeks or 6,
        "limitations": [str(x) for x in json_list(profile.limitations_json)],
        "limitations_text": profile.limitations_text,
        "focus": [str(x) for x in json_list(profile.focus_json)],
        "known_weights": known,
        "regenerate_note": regenerate_note,
    }


def _ai_body_dict(user: User) -> dict:
    """Данные тела из профиля пользователя (не дублируем их у тренера)."""
    return {
        "gender": user.gender,
        "age": user.age,
        "weight": user.weight,
        "height": user.height,
        "diet_goal": user.diet_goal,
        "daily_goal_kcal": user.daily_goal_kcal,
    }


@router.post("/program/generate", response_model=TrainerProgramOut)
def trainer_program_generate(
    data: TrainerGenerateIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerProgramOut:
    """Собрать программу тренировок ИИ и раскрыть её по неделям (ТЗ §4.2).

    ИИ возвращает шаблон недели и план периодизации, бэкенд детерминированно
    раскрывает его в `weeks × days_per_week` дней (`trainer_logic.expand_program`).
    Предыдущая активная программа архивируется ТОЛЬКО после успешной генерации.
    """
    tid = user.telegram_id
    lang = user_lang(user)
    profile = get_profile(db, tid)
    if profile is None or not profile.onboarding_completed:
        raise HTTPException(status_code=409, detail="Сначала заполните анкету тренера")

    # Тяжёлая генерация: собственный лимит + общий лимит ИИ.
    ratelimit.enforce_heavy(tid)
    ratelimit.enforce_ai(tid)

    catalog_rows = active_catalog(db)
    excluded = excluded_exercise_ids(db, tid)
    catalog_text = trainer_logic.catalog_for_prompt(catalog_rows, profile, excluded)

    try:
        result = trainer_ai.generate_program(
            profile=_ai_profile_dict(db, tid, profile, data.regenerate_note),
            body=_ai_body_dict(user),
            catalog=catalog_text,
            lang=lang,
            catalog_map=catalog_rows,
        )
    except (AIError, RuntimeError) as exc:
        raise ai_failed(exc, "Не удалось собрать программу. Попробуйте ещё раз.", "program/generate")

    if result.get("unmatched"):
        # Незнакомые/контриндицированные slug модель уже потеряла при нормализации —
        # в библиотеку мусор не добавляем, только фиксируем в логе.
        logger.info(
            "trainer/program/generate: отброшено %d пунктов ответа ИИ", len(result["unmatched"])
        )

    start_date = today_str(data.start_date)
    expanded = trainer_logic.expand_program(
        result.get("week_template") or {},
        result.get("periodization") or [],
        profile,
        start_date,
    )
    if not expanded:
        raise HTTPException(status_code=502, detail="Не удалось раскрыть программу по неделям")

    # Предыдущие активные программы уходят в архив (одна активная на пользователя).
    (
        db.query(M.TrainerProgram)
        .filter(M.TrainerProgram.telegram_id == tid, M.TrainerProgram.status == "active")
        .update({"status": "archived"}, synchronize_session=False)
    )

    first_date, last_date = trainer_logic.program_dates(expanded)
    program = M.TrainerProgram(
        telegram_id=tid,
        status="active",
        title=result.get("title") or "",
        split_type=result.get("split_type"),
        goal=profile.goal,
        level=profile.level,
        equipment=profile.equipment,
        weeks=profile.program_weeks or 6,
        days_per_week=profile.days_per_week,
        start_date=first_date or start_date,
        end_date=last_date,
        summary=result.get("summary"),
        periodization_json=dumps(result.get("periodization") or []),
        tips_json=dumps(result.get("tips") or []),
        ai_model=result.get("ai_model") or ai_service.TEXT_MODEL,
    )
    db.add(program)
    db.flush()

    for day in expanded:
        row = trainer_logic.day_to_row(day)
        db.add(M.TrainerProgramDay(telegram_id=tid, program_id=program.id, **row))
    db.commit()
    db.refresh(program)

    logger.info(
        "trainer/program/generate: tid=%s программа #%s, %d дней (%s..%s)",
        tid, program.id, len(expanded), first_date, last_date,
    )
    return program_out(db, program, lang, start_date)


@router.get("/program", response_model=TrainerProgramOut)
def trainer_program_get(
    program_id: Optional[int] = Query(None),
    date: Optional[str] = Query(None, description="Локальная дата клиента, YYYY-MM-DD"),
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerProgramOut:
    """Активная (или указанная) программа со всеми днями. 404 — программы нет."""
    tid = user.telegram_id
    if program_id is not None:
        program = (
            db.query(M.TrainerProgram)
            .filter(M.TrainerProgram.id == program_id, M.TrainerProgram.telegram_id == tid)
            .first()
        )
    else:
        program = get_active_program(db, tid)
    if program is None:
        raise HTTPException(status_code=404, detail="Программа не найдена")
    return program_out(db, program, user_lang(user), today_str(date))


@router.post("/program/{program_id}/archive", response_model=TrainerOkOut)
def trainer_program_archive(
    program_id: int,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerOkOut:
    """Архивировать программу (дни и история сессий сохраняются)."""
    program = (
        db.query(M.TrainerProgram)
        .filter(M.TrainerProgram.id == program_id, M.TrainerProgram.telegram_id == user.telegram_id)
        .first()
    )
    if program is None:
        raise HTTPException(status_code=404, detail="Программа не найдена")
    program.status = "archived"
    program.updated_at = datetime.utcnow()
    db.commit()
    return TrainerOkOut(ok=True, status="archived")


# --------------------------------------------------------------------------- #
#  §4.3 «Сегодня» (маршруты сессии добавляет следующий этап — ниже по файлу)
# --------------------------------------------------------------------------- #
@router.get("/today", response_model=TrainerTodayOut)
def trainer_today(
    date: Optional[str] = Query(None, description="Локальная дата клиента, YYYY-MM-DD"),
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerTodayOut:
    """План на дату: тренировка, отдых, закрытая неделя или «нет программы»."""
    tid = user.telegram_id
    program = get_active_program(db, tid)
    days = get_program_days(db, program)
    return today_payload(db, tid, program, days, today_str(date))


# --------------------------------------------------------------------------- #
#  §4.4 Библиотека упражнений
# --------------------------------------------------------------------------- #
@router.get("/exercises", response_model=TrainerExercisesOut)
def trainer_exercises_list(
    muscle: Optional[str] = Query(None),
    equipment: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=300),
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerExercisesOut:
    """Список библиотеки с фильтрами по мышце, оборудованию и поиском по имени."""
    tid = user.telegram_id
    query = db.query(M.TrainerExercise).filter(M.TrainerExercise.is_active.is_(True))
    if muscle:
        query = query.filter(M.TrainerExercise.muscle_group == muscle.strip().lower())
    if equipment:
        query = query.filter(M.TrainerExercise.equipment == equipment.strip().lower())
    text = (q or "").strip()
    if text:
        pattern = f"%{text.lower()}%"
        query = query.filter(
            M.TrainerExercise.name_ru.ilike(pattern)
            | M.TrainerExercise.name_en.ilike(pattern)
            | M.TrainerExercise.slug.ilike(pattern)
        )
    total = query.count()
    rows = (
        query.order_by(M.TrainerExercise.muscle_group.asc(), M.TrainerExercise.id.asc())
        .limit(limit)
        .all()
    )
    excluded = set(excluded_exercise_ids(db, tid))
    return TrainerExercisesOut(
        items=[exercise_item_out(row, row.id in excluded) for row in rows],
        total=total,
    )


@router.get("/exercises/{exercise_id}", response_model=TrainerExerciseOut)
def trainer_exercise_get(
    exercise_id: int,
    technique: int = Query(0, description="1 — сгенерировать/вернуть технику от ИИ"),
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerExerciseOut:
    """Карточка упражнения; при `technique=1` — техника от ИИ с кэшем на язык.

    Кэш общий для всех пользователей (`technique_json[lang]`), поэтому ИИ
    вызывается один раз на упражнение и язык. Сбой ИИ НЕ ломает экран: отдаём
    200 с `technique=None` и `technique_status="failed"`.
    """
    tid = user.telegram_id
    lang = "en" if is_en(user_lang(user)) else "ru"
    exercise = get_exercise_or_404(db, exercise_id)
    profile = get_profile(db, tid)
    limitations = [str(x) for x in json_list(profile.limitations_json)] if profile is not None else []
    limitations = [x for x in limitations if x and x != "none"]

    cache = json_obj(exercise.technique_json)
    cached = cache.get(lang) if isinstance(cache.get(lang), dict) else None
    status = exercise.technique_status or "none"

    if technique and cached is None:
        ratelimit.enforce_ai(tid)
        try:
            cached = trainer_ai.exercise_technique(exercise, limitations, lang)
        except (AIError, RuntimeError) as exc:
            # Экран должен открыться даже без техники — 502 здесь не отдаём.
            logger.warning("trainer/exercises/%s technique: %s", exercise_id, exc)
            cached = None
            status = "failed"
            if (exercise.technique_status or "none") != "ready":
                exercise.technique_status = "failed"
                db.commit()
        else:
            cache[lang] = cached
            exercise.technique_json = dumps(cache)
            exercise.technique_status = "ready"
            exercise.updated_at = datetime.utcnow()
            db.commit()
            status = "ready"
    elif cached is not None:
        status = "ready"

    excluded = exercise.id in set(excluded_exercise_ids(db, tid))
    return TrainerExerciseOut(
        id=exercise.id,
        slug=exercise.slug,
        name_ru=exercise.name_ru,
        name_en=exercise.name_en,
        muscle_group=exercise.muscle_group,
        equipment=exercise.equipment,
        measure_type=exercise.measure_type,
        difficulty=exercise.difficulty or 1,
        category=exercise.category,
        is_unilateral=bool(exercise.is_unilateral),
        secondary_muscles=[str(x) for x in json_list(exercise.secondary_muscles_json)],
        contraindications=[str(x) for x in json_list(exercise.contraindications_json)],
        technique=TrainerTechniqueOut(**cached) if isinstance(cached, dict) else None,
        technique_status=status,
        excluded=excluded,
        disclaimer=((DISCLAIMER_EN if lang == "en" else DISCLAIMER_RU) if limitations else None),
    )


@router.get("/exercises/{exercise_id}/alternatives", response_model=TrainerAlternativesOut)
def trainer_exercise_alternatives(
    exercise_id: int,
    reason: str = Query("other", description="busy|no_equipment|pain|other"),
    session_id: Optional[int] = Query(None),
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerAlternativesOut:
    """3–5 альтернатив на ту же группу мышц правилами библиотеки (ТЗ §4.3).

    Без ИИ: фильтры по оборудованию пользователя, ограничениям и причине замены.
    Если передан `session_id`, упражнения, уже стоящие в этой сессии, не предлагаем.
    """
    tid = user.telegram_id
    if reason not in trainer_logic.REPLACE_REASONS:
        reason = "other"
    exercise = get_exercise_or_404(db, exercise_id)
    profile = get_profile(db, tid)
    excluded = set(excluded_exercise_ids(db, tid))
    if session_id is not None:
        session = (
            db.query(M.TrainerSession)
            .filter(M.TrainerSession.id == session_id, M.TrainerSession.telegram_id == tid)
            .first()
        )
        if session is not None:
            excluded.update(
                row.exercise_id
                for row in db.query(M.TrainerSessionExercise)
                .filter(
                    M.TrainerSessionExercise.session_id == session.id,
                    M.TrainerSessionExercise.status != "replaced",
                )
                .all()
                if row.exercise_id is not None
            )
    items = trainer_logic.alternatives_for(
        exercise, active_catalog(db), profile, reason, sorted(excluded)
    )
    return TrainerAlternativesOut(
        items=[exercise_item_out(row, False) for row in items],
        reason=reason,
    )


@router.post("/exercises/{exercise_id}/exclude", response_model=TrainerOkOut)
def trainer_exercise_exclude(
    exercise_id: int,
    data: TrainerExcludeIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerOkOut:
    """«Никогда не предлагать» это упражнение (или вернуть его в программы)."""
    tid = user.telegram_id
    exercise = get_exercise_or_404(db, exercise_id)
    state = get_or_create_state(db, tid, exercise.id)
    state.excluded = bool(data.excluded)
    state.updated_at = datetime.utcnow()
    db.commit()
    return TrainerOkOut(ok=True, excluded=bool(data.excluded))


# --------------------------------------------------------------------------- #
#  §4.3 Сессия: хелперы выборки и сериализации
# --------------------------------------------------------------------------- #
def get_session_or_404(db: Session, tid: int, session_id: int):
    """Сессия текущего пользователя; чужая или несуществующая — 404."""
    session = (
        db.query(M.TrainerSession)
        .filter(M.TrainerSession.id == session_id, M.TrainerSession.telegram_id == tid)
        .first()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Тренировка не найдена")
    return session


def require_in_progress(session) -> None:
    """Менять можно только незавершённую тренировку (иначе 409)."""
    if (session.status or "") != "in_progress":
        raise HTTPException(status_code=409, detail="Тренировка уже завершена")


def get_session_exercise_or_404(db: Session, session, sex_id: int):
    """Строка упражнения внутри сессии; чужая или из другой сессии — 404."""
    row = (
        db.query(M.TrainerSessionExercise)
        .filter(
            M.TrainerSessionExercise.id == sex_id,
            M.TrainerSessionExercise.session_id == session.id,
            M.TrainerSessionExercise.telegram_id == session.telegram_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Упражнение тренировки не найдено")
    return row


def session_exercise_rows(db: Session, session_id: int) -> list:
    """Упражнения сессии по порядку показа."""
    return (
        db.query(M.TrainerSessionExercise)
        .filter(M.TrainerSessionExercise.session_id == session_id)
        .order_by(M.TrainerSessionExercise.order_index.asc(), M.TrainerSessionExercise.id.asc())
        .all()
    )


def session_set_rows(db: Session, session_id: int, session_exercise_id: Optional[int] = None) -> list:
    """Сеты сессии (или одного упражнения сессии) по номеру подхода."""
    query = db.query(M.TrainerSetLog).filter(M.TrainerSetLog.session_id == session_id)
    if session_exercise_id is not None:
        query = query.filter(M.TrainerSetLog.session_exercise_id == session_exercise_id)
    return query.order_by(M.TrainerSetLog.set_index.asc(), M.TrainerSetLog.id.asc()).all()


def session_exercise_out(db: Session, session, row) -> TrainerSessionExerciseOut:
    """Одно упражнение сессии целиком — ответ replace / add (ТЗ §4.3)."""
    exercise = exercises_by_id(db, [row.exercise_id]).get(row.exercise_id)
    prev = previous_sets(db, session.telegram_id, [row.exercise_id], session.id)
    return TrainerSessionExerciseOut(
        id=row.id,
        exercise=exercise_brief_out(exercise),
        block=row.block or "main",
        order_index=row.order_index or 0,
        planned_sets=row.planned_sets,
        planned_reps_min=row.planned_reps_min,
        planned_reps_max=row.planned_reps_max,
        planned_weight_kg=row.planned_weight_kg,
        planned_time_sec=row.planned_time_sec,
        planned_rest_sec=row.planned_rest_sec,
        planned_rpe=row.planned_rpe,
        status=row.status or "pending",
        note=row.note,
        previous=prev.get(row.exercise_id, []),
        sets=[set_out(s) for s in session_set_rows(db, session.id, row.id)],
    )


def rest_for(sex, profile) -> int:
    """Отдых после подхода: цель упражнения → значение анкеты → 90 с."""
    rest = sex.planned_rest_sec or (profile.rest_default_sec if profile is not None else None) or 90
    return int(max(trainer_logic.REST_RANGE[0], min(trainer_logic.REST_RANGE[1], int(rest))))


def done_sets_of(sex, sets: list) -> list:
    """Выполненные подходы упражнения, которые считаются «сделанными».

    Для основного блока это только рабочие сеты (разминочные не в объём и не в
    PR — ТЗ §1), для блоков разминки/заминки — любые отмеченные пункты чек-листа.
    """
    if (sex.block or "main") == "main":
        return [s for s in sets if s.is_done and (s.set_type or "work") == "work"]
    return [s for s in sets if s.is_done]


def exercise_status_after(sex, sets: list) -> str:
    """Статус упражнения после записи подхода: done, когда план закрыт."""
    if (sex.status or "pending") in ("skipped", "replaced"):
        return sex.status or "pending"
    done = done_sets_of(sex, sets)
    planned = sex.planned_sets or 1
    return "done" if done and len(done) >= planned else "pending"


def refresh_session_totals(db: Session, session) -> dict:
    """Пересчитать итоги сессии по журналу подходов (разминка не в объём)."""
    rows = session_set_rows(db, session.id)
    exercises = session_exercise_rows(db, session.id)
    totals = trainer_logic.session_totals(rows, exercises)
    session.total_sets = totals["total_sets"]
    session.total_reps = totals["total_reps"]
    session.total_volume_kg = totals["total_volume_kg"]
    session.updated_at = datetime.utcnow()
    return totals


def plan_context(exercise, sex) -> dict:
    """Упражнение + его цели в сессии — вход для evaluate_exercise / next_targets."""
    return {
        "id": exercise.id,
        "slug": exercise.slug,
        "name_ru": exercise.name_ru,
        "name_en": exercise.name_en,
        "equipment": exercise.equipment,
        "muscle_group": exercise.muscle_group,
        "measure_type": exercise.measure_type,
        "category": exercise.category,
        "sets": sex.planned_sets,
        "reps_min": sex.planned_reps_min,
        "reps_max": sex.planned_reps_max,
        "time_sec": sex.planned_time_sec,
        "planned_weight_kg": sex.planned_weight_kg,
    }


def state_snapshot(state) -> dict:
    """Снимок состояния прогрессии «до» отзыва (для идемпотентного пересчёта)."""
    return {
        "working_weight_kg": state.working_weight_kg,
        "target_reps_min": state.target_reps_min,
        "target_reps_max": state.target_reps_max,
        "target_time_sec": state.target_time_sec,
        "success_streak": state.success_streak or 0,
        "fail_streak": state.fail_streak or 0,
        "last_result": state.last_result,
    }


def get_program_day(db: Session, tid: int, day_id: Optional[int]):
    """День программы пользователя по идентификатору (или None)."""
    if not day_id:
        return None
    return (
        db.query(M.TrainerProgramDay)
        .filter(M.TrainerProgramDay.id == day_id, M.TrainerProgramDay.telegram_id == tid)
        .first()
    )


def user_states(db: Session, tid: int) -> dict:
    """{exercise_id: TrainerExerciseState} — все состояния прогрессии пользователя."""
    return {
        row.exercise_id: row
        for row in db.query(M.TrainerExerciseState)
        .filter(M.TrainerExerciseState.telegram_id == tid)
        .all()
        if row.exercise_id is not None
    }


def record_prs(db: Session, tid: int, session, row, exercise) -> list:
    """Обновить TrainerRecord по сохранённому подходу и вернуть новые рекорды.

    Первая запись рекордом НЕ считается (иначе каждая первая тренировка —
    «рекорд»): строка создаётся молча, `is_pr` только при улучшении (§5.5).
    Рекорды, поставленные ЭТИМ же сетом, из базы сравнения исключаются — иначе
    правка собственного подхода сравнивалась бы сама с собой.
    """
    records = {
        r.record_type: r
        for r in db.query(M.TrainerRecord)
        .filter(M.TrainerRecord.telegram_id == tid, M.TrainerRecord.exercise_id == exercise.id)
        .all()
    }
    baseline = {t: r.value for t, r in records.items() if r.set_log_id != row.id}
    found = trainer_logic.detect_prs(baseline, row, exercise)

    achieved = []
    for cand in found:
        record = records.get(cand["type"])
        if record is None:
            record = M.TrainerRecord(telegram_id=tid, exercise_id=exercise.id, record_type=cand["type"])
            db.add(record)
            records[cand["type"]] = record
        elif not cand["is_pr"] and record.set_log_id != row.id:
            continue
        record.value = cand["value"]
        record.weight_kg = cand.get("weight_kg")
        record.reps = cand.get("reps")
        record.set_log_id = row.id
        record.session_id = session.id
        record.date = session.date
        record.updated_at = datetime.utcnow()
        if cand["is_pr"]:
            achieved.append(cand)

    row.is_pr = bool(achieved)
    row.pr_types_json = dumps([c["type"] for c in achieved]) if achieved else None

    if achieved:
        # Рекорды сессии: одна строка на (упражнение, тип), последняя выигрывает.
        merged: dict = {}
        for item in json_list(session.prs_json):
            if isinstance(item, dict):
                merged[(item.get("exercise_id"), item.get("type"))] = item
        for cand in achieved:
            merged[(exercise.id, cand["type"])] = {
                "exercise_id": exercise.id,
                "type": cand["type"],
                "value": cand["value"],
                "prev": cand.get("prev_value"),
                "prev_value": cand.get("prev_value"),
                "set_log_id": row.id,
            }
        session.prs_json = dumps(list(merged.values()))
    return achieved


# --------------------------------------------------------------------------- #
#  §4.3 Сессия: старт
# --------------------------------------------------------------------------- #
def _resolve_start_day(db: Session, tid: int, program, program_day_id: Optional[int], date_value: str):
    """День программы для старта: явно указанный, сегодняшний или ближайший плановый."""
    if program_day_id is not None:
        day = get_program_day(db, tid, program_day_id)
        if day is None:
            raise HTTPException(status_code=404, detail="День программы не найден")
        return day
    days = get_program_days(db, program)
    planned = [d for d in days if (d.status or "planned") == "planned"]
    day = next((d for d in planned if d.scheduled_date == date_value), None)
    if day is not None:
        return day
    future = sorted(
        (d for d in planned if (d.scheduled_date or "") >= date_value),
        key=lambda d: (d.scheduled_date or "", d.week or 0, d.day_index or 0),
    )
    day = future[0] if future else trainer_logic.next_planned_day(planned)
    if day is None:
        raise HTTPException(status_code=409, detail="Нет активной программы — сначала соберите её")
    return day


def _substitute_exercise(db: Session, tid: int, exercise, state, profile, states: dict, excluded: list):
    """Замена упражнения на старте: запомненная альтернатива или замена исключённого.

    Возвращает (упражнение, состояние, признак замены).
    """
    if state is None:
        return exercise, None, False
    if state.preferred_alternative_id and state.preferred_alternative_id != exercise.id:
        alt = (
            db.query(M.TrainerExercise)
            .filter(
                M.TrainerExercise.id == state.preferred_alternative_id,
                M.TrainerExercise.is_active.is_(True),
            )
            .first()
        )
        if alt is not None:
            return alt, states.get(alt.id), True
    if state.excluded:
        alts = trainer_logic.alternatives_for(exercise, active_catalog(db), profile, "other", excluded)
        if alts:
            return alts[0], states.get(alts[0].id), True
    return exercise, state, False


@router.post("/session/start", response_model=TrainerSessionOut)
def trainer_session_start(
    data: TrainerSessionStartIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerSessionOut:
    """Начать тренировку по дню программы (ТЗ §4.3).

    Цели каждого упражнения = план дня → состояние прогрессии пользователя →
    модификатор недели (deload: вес×0.85, сеты−1) → правки дня из отзыва/разбора
    (`trainer_logic.apply_targets`). Исключённые упражнения и запомненные замены
    подставляются автоматически. 409, если тренировка уже идёт.
    """
    tid = user.telegram_id
    active = get_active_session(db, tid)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "Тренировка уже начата — продолжите её", "session_id": active.id},
        )

    date_value = today_str(data.date)
    program = get_active_program(db, tid)
    day = _resolve_start_day(db, tid, program, data.program_day_id, date_value)
    if program is None or day.program_id != program.id:
        program = (
            db.query(M.TrainerProgram)
            .filter(M.TrainerProgram.id == day.program_id, M.TrainerProgram.telegram_id == tid)
            .first()
        )

    profile = get_profile(db, tid)
    states = user_states(db, tid)
    excluded = excluded_exercise_ids(db, tid)
    week_mod = trainer_logic.week_modifier(
        json_list(program.periodization_json) if program is not None else [],
        day.week,
        program.weeks if program is not None else None,
    )
    adjustments = json_obj(day.adjustments_json)
    ex_by_id, ex_by_slug = day_catalog(db, [day])

    session = M.TrainerSession(
        telegram_id=tid,
        program_id=(program.id if program is not None else None),
        program_day_id=day.id,
        date=date_value,
        status="in_progress",
        title=day.title or "",
        session_type=day.session_type or "strength",
        week=day.week,
        day_index=day.day_index,
        started_at=datetime.utcnow(),
    )
    db.add(session)
    db.flush()

    order = 0
    for block, field in (("warmup", "warmup_json"), ("main", "exercises_json"), ("cooldown", "cooldown_json")):
        for item in json_list(getattr(day, field, None)):
            if not isinstance(item, dict):
                continue
            exercise = ex_by_id.get(item.get("exercise_id")) or ex_by_slug.get(item.get("slug"))
            if exercise is None:
                logger.info("trainer/session/start: пункт плана без упражнения (%s)", item.get("slug"))
                continue
            state = states.get(exercise.id)
            plan_item = dict(item)
            if block == "main":
                exercise, state, replaced = _substitute_exercise(
                    db, tid, exercise, state, profile, states, excluded
                )
                if replaced:
                    # Вес исходного упражнения новому не подходит — только из его состояния.
                    plan_item.pop("start_weight_kg", None)
                    plan_item.pop("planned_weight_kg", None)
                targets = trainer_logic.apply_targets(plan_item, exercise, state, week_mod, adjustments)
            else:
                targets = trainer_logic.apply_targets(plan_item, exercise, state, None, None)
            order += 1
            db.add(
                M.TrainerSessionExercise(
                    telegram_id=tid,
                    session_id=session.id,
                    exercise_id=exercise.id,
                    block=block,
                    order_index=order,
                    planned_sets=targets["planned_sets"],
                    planned_reps_min=targets["planned_reps_min"],
                    planned_reps_max=targets["planned_reps_max"],
                    planned_weight_kg=targets["planned_weight_kg"],
                    planned_time_sec=targets["planned_time_sec"],
                    planned_rest_sec=targets["planned_rest_sec"],
                    planned_rpe=targets["planned_rpe"],
                    status="pending",
                    note=targets["note"],
                )
            )

    db.commit()
    db.refresh(session)
    logger.info(
        "trainer/session/start: tid=%s сессия #%s, день #%s (неделя %s, фаза %s)",
        tid, session.id, day.id, day.week, week_mod["phase"],
    )
    return session_out(db, session)


# --------------------------------------------------------------------------- #
#  §4.3 Сессия: чтение (ВАЖНО: /session/active объявлен ДО /session/{id})
# --------------------------------------------------------------------------- #
@router.get("/session/active", response_model=Optional[TrainerSessionOut])
def trainer_session_active(
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> Optional[TrainerSessionOut]:
    """Незавершённая тренировка пользователя (или null) — «Продолжить»."""
    return session_out(db, get_active_session(db, user.telegram_id))


@router.get("/sessions", response_model=TrainerSessionsOut)
def trainer_sessions_list(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerSessionsOut:
    """История завершённых тренировок (страницами) — ТЗ §4.3."""
    tid = user.telegram_id
    query = (
        db.query(M.TrainerSession)
        .filter(M.TrainerSession.telegram_id == tid, M.TrainerSession.status == "completed")
    )
    total = query.count()
    rows = (
        query.order_by(M.TrainerSession.date.desc(), M.TrainerSession.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return TrainerSessionsOut(
        items=[
            TrainerSessionBriefOut(
                id=row.id,
                date=row.date,
                title=row.title or "",
                session_type=row.session_type or "strength",
                duration_min=row.duration_min,
                total_volume_kg=row.total_volume_kg or 0.0,
                total_sets=row.total_sets or 0,
                calories_burned=row.calories_burned,
                prs_count=len(json_list(row.prs_json)),
                feedback=row.feedback,
            )
            for row in rows
        ],
        total=total,
    )


@router.get("/session/{session_id}", response_model=TrainerSessionOut)
def trainer_session_get(
    session_id: int,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerSessionOut:
    """Тренировка целиком: упражнения, цели, сеты, «прошлый раз», рекорды."""
    return session_out(db, get_session_or_404(db, user.telegram_id, session_id))


# --------------------------------------------------------------------------- #
#  §4.3 Сессия: подходы
# --------------------------------------------------------------------------- #
@router.post("/session/{session_id}/set", response_model=TrainerSetSaveOut)
def trainer_session_set_save(
    session_id: int,
    data: TrainerSetIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerSetSaveOut:
    """Сохранить подход (upsert по session_exercise_id + set_index) — ТЗ §4.3.

    Считает объём и расчётный 1RM, ищет рекорды (`trainer_logic.detect_prs` —
    только рабочие выполненные сеты) и обновляет `TrainerRecord`. Снятая отметка
    ✓ (`is_done=false`) рекорды не пересчитывает: они «истинны» по последнему
    сохранению (MVP, см. §4.3).
    """
    tid = user.telegram_id
    session = get_session_or_404(db, tid, session_id)
    require_in_progress(session)
    sex = get_session_exercise_or_404(db, session, data.session_exercise_id)
    profile = get_profile(db, tid)
    exercise = exercises_by_id(db, [sex.exercise_id]).get(sex.exercise_id)

    row = (
        db.query(M.TrainerSetLog)
        .filter(
            M.TrainerSetLog.session_exercise_id == sex.id,
            M.TrainerSetLog.set_index == data.set_index,
        )
        .first()
    )
    unchanged = row is not None and (
        (row.set_type or "work") == data.set_type
        and row.weight_kg == data.weight_kg
        and row.reps == data.reps
        and row.time_sec == data.time_sec
        and bool(row.is_done) == bool(data.is_done)
    )
    if row is None:
        row = M.TrainerSetLog(
            telegram_id=tid,
            session_id=session.id,
            session_exercise_id=sex.id,
            exercise_id=sex.exercise_id,
            date=session.date,
            set_index=data.set_index,
        )
        db.add(row)

    row.set_type = data.set_type
    row.weight_kg = data.weight_kg
    row.reps = data.reps
    row.time_sec = data.time_sec
    row.rpe = data.rpe
    row.is_done = bool(data.is_done)
    # Объём и 1RM считаем только по рабочим подходам: разминка не в объём (§1).
    if data.set_type == "work":
        row.volume_kg = round((data.weight_kg or 0.0) * (data.reps or 0), 1)
        row.est_1rm = trainer_logic.est_1rm(data.weight_kg, data.reps)
    else:
        row.volume_kg = 0.0
        row.est_1rm = None
    row.updated_at = datetime.utcnow()
    db.flush()

    achieved = []
    if not bool(data.is_done):
        row.is_pr = False
        row.pr_types_json = None
    elif exercise is not None and not unchanged:
        achieved = record_prs(db, tid, session, row, exercise)

    sets = session_set_rows(db, session.id, sex.id)
    sex.status = exercise_status_after(sex, sets)
    refresh_session_totals(db, session)
    db.commit()
    db.refresh(row)

    ex_map = {exercise.id: exercise} if exercise is not None else {}
    return TrainerSetSaveOut(
        set=set_out(row),
        prs=[
            pr_out(
                {
                    "exercise_id": (exercise.id if exercise is not None else None),
                    "type": cand["type"],
                    "value": cand["value"],
                    "prev_value": cand.get("prev_value"),
                },
                ex_map,
            )
            for cand in achieved
        ],
        rest_sec=rest_for(sex, profile),
        exercise_status=sex.status or "pending",
    )


@router.delete("/session/{session_id}/set/{set_id}", response_model=TrainerOkOut)
def trainer_session_set_delete(
    session_id: int,
    set_id: int,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerOkOut:
    """Удалить подход (рекорды в MVP не пересчитываем — §4.3)."""
    tid = user.telegram_id
    session = get_session_or_404(db, tid, session_id)
    require_in_progress(session)
    row = (
        db.query(M.TrainerSetLog)
        .filter(
            M.TrainerSetLog.id == set_id,
            M.TrainerSetLog.session_id == session.id,
            M.TrainerSetLog.telegram_id == tid,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Подход не найден")
    sex = (
        db.query(M.TrainerSessionExercise)
        .filter(M.TrainerSessionExercise.id == row.session_exercise_id)
        .first()
    )
    db.delete(row)
    db.flush()
    if sex is not None:
        sex.status = exercise_status_after(sex, session_set_rows(db, session.id, sex.id))
    refresh_session_totals(db, session)
    db.commit()
    return TrainerOkOut(ok=True)


# --------------------------------------------------------------------------- #
#  §4.3 Сессия: замена, пропуск, добавление упражнения
# --------------------------------------------------------------------------- #
@router.post("/session/{session_id}/exercise/add", response_model=TrainerSessionExerciseOut)
def trainer_session_exercise_add(
    session_id: int,
    data: TrainerAddExerciseIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerSessionExerciseOut:
    """Добавить упражнение в текущую тренировку вручную (в конец основного блока)."""
    tid = user.telegram_id
    session = get_session_or_404(db, tid, session_id)
    require_in_progress(session)
    exercise = get_exercise_or_404(db, data.exercise_id)
    profile = get_profile(db, tid)
    state = user_states(db, tid).get(exercise.id)

    rows = session_exercise_rows(db, session.id)
    order = max((r.order_index or 0) for r in rows) + 1 if rows else 1
    item = {
        "sets": data.sets,
        "reps_min": data.reps_min,
        "reps_max": data.reps_max,
        "rest_sec": (profile.rest_default_sec if profile is not None else None) or 90,
    }
    targets = trainer_logic.apply_targets(item, exercise, state, None, None)
    row = M.TrainerSessionExercise(
        telegram_id=tid,
        session_id=session.id,
        exercise_id=exercise.id,
        block="main",
        order_index=order,
        planned_sets=targets["planned_sets"],
        planned_reps_min=targets["planned_reps_min"],
        planned_reps_max=targets["planned_reps_max"],
        planned_weight_kg=targets["planned_weight_kg"],
        planned_time_sec=targets["planned_time_sec"],
        planned_rest_sec=targets["planned_rest_sec"],
        planned_rpe=targets["planned_rpe"],
        status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return session_exercise_out(db, session, row)


@router.post("/session/{session_id}/exercise/{sex_id}/replace", response_model=TrainerSessionExerciseOut)
def trainer_session_exercise_replace(
    session_id: int,
    sex_id: int,
    data: TrainerReplaceIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerSessionExerciseOut:
    """Заменить упражнение в сессии (занят тренажёр / нет оборудования / болит).

    Старая строка помечается `replaced`, новая встаёт на её место (тот же
    `order_index` и цели, вес — из состояния нового упражнения). При `remember`
    замена запоминается в состоянии исходного упражнения; причина `pain`
    дополнительно исключает исходное из программ (ТЗ §4.3).
    """
    tid = user.telegram_id
    session = get_session_or_404(db, tid, session_id)
    require_in_progress(session)
    old = get_session_exercise_or_404(db, session, sex_id)
    new_exercise = get_exercise_or_404(db, data.new_exercise_id)
    if new_exercise.id == old.exercise_id:
        raise HTTPException(status_code=409, detail="Это то же самое упражнение")

    state_new = user_states(db, tid).get(new_exercise.id)
    item = {
        "sets": old.planned_sets,
        "reps_min": old.planned_reps_min,
        "reps_max": old.planned_reps_max,
        "time_sec": old.planned_time_sec,
        "rest_sec": old.planned_rest_sec,
        "rpe": old.planned_rpe,
    }
    targets = trainer_logic.apply_targets(item, new_exercise, state_new, None, None)

    old.status = "replaced"
    row = M.TrainerSessionExercise(
        telegram_id=tid,
        session_id=session.id,
        exercise_id=new_exercise.id,
        block=old.block or "main",
        order_index=old.order_index or 0,
        planned_sets=targets["planned_sets"],
        planned_reps_min=targets["planned_reps_min"],
        planned_reps_max=targets["planned_reps_max"],
        planned_weight_kg=targets["planned_weight_kg"],
        planned_time_sec=targets["planned_time_sec"],
        planned_rest_sec=targets["planned_rest_sec"],
        planned_rpe=targets["planned_rpe"],
        status="pending",
        replaced_from_exercise_id=old.exercise_id,
        replace_reason=data.reason,
    )
    db.add(row)

    if data.remember and old.exercise_id:
        state_old = get_or_create_state(db, tid, old.exercise_id)
        state_old.preferred_alternative_id = new_exercise.id
        if data.reason == "pain":
            # «Болит» — до конца программы это упражнение не предлагаем (§4.3).
            state_old.excluded = True
        state_old.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(row)
    return session_exercise_out(db, session, row)


@router.post("/session/{session_id}/exercise/{sex_id}/skip", response_model=TrainerOkOut)
def trainer_session_exercise_skip(
    session_id: int,
    sex_id: int,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerOkOut:
    """Пропустить упражнение в текущей тренировке."""
    tid = user.telegram_id
    session = get_session_or_404(db, tid, session_id)
    require_in_progress(session)
    row = get_session_exercise_or_404(db, session, sex_id)
    row.status = "skipped"
    db.commit()
    return TrainerOkOut(ok=True, status="skipped")


# --------------------------------------------------------------------------- #
#  §4.3 Сессия: завершение, отзыв, отмена
# --------------------------------------------------------------------------- #
def _update_states_after_finish(db: Session, tid: int, session, exercises: list, sets_by_sex: dict, ex_by_id: dict) -> None:
    """Состояние прогрессии после тренировки: рабочий вес (медиана) и результат.

    Сами цели на следующий раз считает отзыв (`/feedback` → `next_targets`):
    здесь фиксируем только факт — что было сделано и с каким весом.
    """
    for sex in exercises:
        if (sex.block or "main") != "main" or (sex.status or "") == "replaced":
            continue
        exercise = ex_by_id.get(sex.exercise_id)
        if exercise is None:
            continue
        sets = sets_by_sex.get(sex.id, [])
        if not done_sets_of(sex, sets):
            continue
        state = get_or_create_state(db, tid, exercise.id)
        median = trainer_logic.median_weight(sets)
        if median is not None:
            state.working_weight_kg = median
        if state.target_reps_min is None:
            state.target_reps_min = sex.planned_reps_min
        if state.target_reps_max is None:
            state.target_reps_max = sex.planned_reps_max
        if state.target_time_sec is None and sex.planned_time_sec:
            state.target_time_sec = sex.planned_time_sec
        if state.rest_sec is None:
            state.rest_sec = sex.planned_rest_sec
        state.last_result = trainer_logic.evaluate_exercise(plan_context(exercise, sex), sets)
        state.last_session_id = session.id
        state.last_date = session.date
        state.updated_at = datetime.utcnow()


@router.post("/session/{session_id}/finish", response_model=TrainerFinishOut)
def trainer_session_finish(
    session_id: int,
    data: TrainerFinishIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerFinishOut:
    """Завершить тренировку: итоги, калории и запись в дневник (ТЗ §4.3, §3).

    Создаёт строку `Workout` (тип по типу сессии, калории по MET через
    `fitness.estimate_calories_burned`, описание «Тренер: <день>») — дневник
    учитывает сожжённые калории, ничего не зная о тренере. День программы
    помечается выполненным, состояние прогрессии обновляется.
    """
    tid = user.telegram_id
    session = get_session_or_404(db, tid, session_id)
    require_in_progress(session)

    rows = session_set_rows(db, session.id)
    if not [r for r in rows if r.is_done and (r.set_type or "work") == "work"]:
        raise HTTPException(status_code=400, detail="Отметьте хотя бы один рабочий подход")

    exercises = session_exercise_rows(db, session.id)
    sets_by_sex: dict = {}
    for row in rows:
        sets_by_sex.setdefault(row.session_exercise_id, []).append(row)
    ex_by_id = exercises_by_id(db, [e.exercise_id for e in exercises])

    # Статусы упражнений — до подсчёта итогов: они входят в summary.
    for sex in exercises:
        sex.status = exercise_status_after(sex, sets_by_sex.get(sex.id, []))
    totals = trainer_logic.session_totals(rows, exercises)

    day = get_program_day(db, tid, session.program_day_id)
    started = session.started_at or datetime.utcnow()
    elapsed = int(max(0.0, (datetime.utcnow() - started).total_seconds()) // 60)
    duration = trainer_logic.session_duration_min(
        elapsed, day.duration_min if day is not None else None, data.duration_min
    )
    workout_type = trainer_logic.map_workout_type(session.session_type)
    calories, _met = fitness.estimate_calories_burned(workout_type, duration, user.weight)

    title = (session.title or "").strip()
    workout = M.Workout(
        telegram_id=tid,
        date=session.date,
        type=workout_type,
        duration_min=duration,
        calories_burned=calories,
        description="Тренер: " + (title or ("тренировка" if not is_en(user_lang(user)) else "workout")),
    )
    db.add(workout)
    db.flush()

    session.status = "completed"
    session.finished_at = datetime.utcnow()
    session.duration_min = duration
    session.total_sets = totals["total_sets"]
    session.total_reps = totals["total_reps"]
    session.total_volume_kg = totals["total_volume_kg"]
    session.calories_burned = calories
    session.workout_id = workout.id
    session.updated_at = datetime.utcnow()
    if data.note:
        session.feedback_note = data.note

    _update_states_after_finish(db, tid, session, exercises, sets_by_sex, ex_by_id)

    if day is not None:
        day.status = "done"
        day.session_id = session.id

    db.commit()
    db.refresh(session)

    logger.info(
        "trainer/session/finish: tid=%s сессия #%s, %d мин, %s кг, %d ккал, workout #%s",
        tid, session.id, duration, totals["total_volume_kg"], calories, workout.id,
    )
    pr_rows = json_list(session.prs_json)
    pr_ex = exercises_by_id(db, [p.get("exercise_id") for p in pr_rows if isinstance(p, dict)])
    return TrainerFinishOut(
        session=session_out(db, session),
        summary=TrainerFinishSummaryOut(
            duration_min=duration,
            total_sets=totals["total_sets"],
            total_reps=totals["total_reps"],
            total_volume_kg=totals["total_volume_kg"],
            calories_burned=calories,
            exercises_done=totals["exercises_done"],
            exercises_skipped=totals["exercises_skipped"],
        ),
        prs=[pr_out(p, pr_ex) for p in pr_rows if isinstance(p, dict)],
        workout_id=workout.id,
    )


def _apply_feedback_adjustments(db: Session, session, feedback: str, results: list) -> None:
    """Правки от отзыва — в день того же `day_index` следующей недели (ТЗ §5.5).

    Правки этой же сессии перезаписываются (повторный отзыв идемпотентен), чужие
    правки (например, применённый недельный разбор) сохраняются.
    """
    if not session.program_id or not session.week or not session.day_index:
        return
    day = (
        db.query(M.TrainerProgramDay)
        .filter(
            M.TrainerProgramDay.program_id == session.program_id,
            M.TrainerProgramDay.telegram_id == session.telegram_id,
            M.TrainerProgramDay.week == (session.week or 0) + 1,
            M.TrainerProgramDay.day_index == session.day_index,
        )
        .first()
    )
    if day is None:
        return
    base = json_obj(day.adjustments_json)
    items = [
        item
        for item in (base.get("items") or [])
        if isinstance(item, dict) and item.get("from_session") != session.id
    ]
    extra = trainer_logic.feedback_adjustments(feedback, results)
    for item in (extra or {}).get("items") or []:
        item["from_session"] = session.id
        items.append(item)
    if items:
        base["items"] = items
    else:
        base.pop("items", None)
    base["feedback"] = feedback
    day.adjustments_json = dumps(base) if base.get("items") else None


@router.post("/session/{session_id}/feedback", response_model=TrainerAdaptationOut)
def trainer_session_feedback(
    session_id: int,
    data: TrainerFeedbackIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerAdaptationOut:
    """Отзыв «легко / норм / тяжело» → адаптация правилами (ТЗ §4.3, §5.5).

    Каждое изменение объясняется строкой на языке пользователя. Повторный вызов
    перезаписывает адаптацию: пересчёт идёт от снимка состояния «до», который
    хранится в `adaptation_json.before`, поэтому вес не «съезжает» на каждом
    нажатии.
    """
    tid = user.telegram_id
    lang = user_lang(user)
    session = get_session_or_404(db, tid, session_id)
    if (session.status or "") != "completed":
        raise HTTPException(status_code=409, detail="Отзыв доступен после завершения тренировки")

    profile = get_profile(db, tid)
    stored = json_obj(session.adaptation_json)
    before = stored.get("before") if isinstance(stored.get("before"), dict) else {}

    exercises = session_exercise_rows(db, session.id)
    sets_by_sex: dict = {}
    for row in session_set_rows(db, session.id):
        sets_by_sex.setdefault(row.session_exercise_id, []).append(row)
    ex_by_id = exercises_by_id(db, [e.exercise_id for e in exercises])

    changes: list = []
    results: list = []
    snapshot_all: dict = {}
    for sex in exercises:
        if (sex.block or "main") != "main" or (sex.status or "") == "replaced":
            continue
        exercise = ex_by_id.get(sex.exercise_id)
        if exercise is None:
            continue
        sets = sets_by_sex.get(sex.id, [])
        if not done_sets_of(sex, sets):
            # Пропущенное упражнение не адаптируем: план не менялся, факта нет.
            continue
        plan = plan_context(exercise, sex)
        result = trainer_logic.evaluate_exercise(plan, sets)
        results.append(
            {
                "exercise_id": exercise.id,
                "slug": exercise.slug,
                "category": exercise.category,
                "result": result,
            }
        )
        state = get_or_create_state(db, tid, exercise.id)
        key = str(exercise.id)
        snapshot = before.get(key) if isinstance(before.get(key), dict) else state_snapshot(state)
        snapshot_all[key] = snapshot
        nxt = trainer_logic.next_targets(
            snapshot,
            plan,
            result,
            data.feedback,
            level=(profile.level if profile is not None else None),
            sets_done=sets,
        )
        state.working_weight_kg = nxt["working_weight_kg"]
        state.target_reps_min = nxt["target_reps_min"]
        state.target_reps_max = nxt["target_reps_max"]
        state.target_time_sec = nxt["target_time_sec"]
        state.success_streak = nxt["success_streak"]
        state.fail_streak = nxt["fail_streak"]
        state.last_result = result
        state.updated_at = datetime.utcnow()
        changes.extend(nxt["changes"])

    lines = trainer_logic.explain_changes(changes, lang)
    message = "Учёл на следующий раз" if not is_en(lang) else "Noted for next time"
    out = TrainerAdaptationOut(
        changes=[
            TrainerChangeOut(
                exercise_id=ch.get("exercise_id"),
                name_ru=ch.get("name_ru"),
                name_en=ch.get("name_en"),
                kind=str(ch.get("kind") or "keep"),
                old_value=trainer_logic._to_float(ch.get("old_value")),
                new_value=trainer_logic._to_float(ch.get("new_value")),
            )
            for ch in changes
        ],
        lines=lines,
        message=message,
    )

    session.feedback = data.feedback
    note = (data.note or "").strip()
    session.feedback_note = note[:500] or None
    session.adaptation_json = dumps(
        {
            "changes": changes,
            "lines": lines,
            "message": message,
            "before": snapshot_all,
            "results": results,
            "feedback": data.feedback,
            "rpe": data.rpe,
        }
    )
    session.updated_at = datetime.utcnow()
    _apply_feedback_adjustments(db, session, data.feedback, results)
    db.commit()
    return out


@router.post("/session/{session_id}/abandon", response_model=TrainerOkOut)
def trainer_session_abandon(
    session_id: int,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerOkOut:
    """Отменить тренировку: день остаётся плановым, запись в дневник НЕ создаём."""
    tid = user.telegram_id
    session = get_session_or_404(db, tid, session_id)
    if (session.status or "") == "completed":
        raise HTTPException(status_code=409, detail="Тренировка уже завершена")
    if (session.status or "") == "in_progress":
        session.status = "abandoned"
        session.finished_at = datetime.utcnow()
        session.updated_at = datetime.utcnow()
        db.commit()
    return TrainerOkOut(ok=True, status="abandoned")


# --------------------------------------------------------------------------- #
#  Ниже дописываются маршруты следующих этапов:
#    §4.4 прогресс и история упражнения; §4.5 недельный разбор и питание.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
#  §4.4 Прогресс: хелперы выборок и агрегации
# --------------------------------------------------------------------------- #
def period_start(period: Optional[str], date_value: str) -> Optional[str]:
    """Начало периода графика: 4w → −27 дней, 3m → −89 дней, all → без границы."""
    code = (period or "4w").strip().lower()
    day = date_cls.fromisoformat(date_value)
    if code == "all":
        return None
    if code == "3m":
        return (day - timedelta(days=89)).isoformat()
    return (day - timedelta(days=27)).isoformat()


def done_work_logs(
    db: Session,
    tid: int,
    since: Optional[str] = None,
    until: Optional[str] = None,
    exercise_id: Optional[int] = None,
) -> list:
    """Выполненные РАБОЧИЕ подходы пользователя за период (разминочные — мимо).

    `set_type` может быть NULL у старых строк — трактуем такие как рабочие.
    """
    query = db.query(M.TrainerSetLog).filter(
        M.TrainerSetLog.telegram_id == tid,
        M.TrainerSetLog.is_done.is_(True),
        (M.TrainerSetLog.set_type.is_(None)) | (M.TrainerSetLog.set_type == "work"),
    )
    if since:
        query = query.filter(M.TrainerSetLog.date >= since)
    if until:
        query = query.filter(M.TrainerSetLog.date <= until)
    if exercise_id is not None:
        query = query.filter(M.TrainerSetLog.exercise_id == exercise_id)
    return query.order_by(
        M.TrainerSetLog.date.asc(),
        M.TrainerSetLog.session_id.asc(),
        M.TrainerSetLog.set_index.asc(),
    ).all()


def totals_between(db: Session, tid: int, since: str, until: str) -> TrainerTotalsOut:
    """Итоги завершённых сессий за отрезок дат: тренировки, объём, подходы, минуты."""
    rows = (
        db.query(M.TrainerSession)
        .filter(
            M.TrainerSession.telegram_id == tid,
            M.TrainerSession.status == "completed",
            M.TrainerSession.date >= since,
            M.TrainerSession.date <= until,
        )
        .all()
    )
    return TrainerTotalsOut(
        sessions=len(rows),
        volume_kg=round(sum(float(r.total_volume_kg or 0.0) for r in rows), 1),
        sets=sum(int(r.total_sets or 0) for r in rows),
        minutes=sum(int(r.duration_min or 0) for r in rows),
    )


def chart_points(logs: list) -> list:
    """Подходы → точки графика по дням: лучший 1RM, лучший вес, объём за день."""
    by_date: dict = {}
    for row in logs:
        if not row.date:
            continue
        point = by_date.setdefault(row.date, {"est_1rm": None, "max_weight": None, "volume": 0.0})
        reps = int(row.reps or 0)
        weight = row.weight_kg
        volume = row.volume_kg if row.volume_kg else (float(weight or 0.0) * reps)
        point["volume"] += float(volume or 0.0)
        if weight is not None and (point["max_weight"] is None or weight > point["max_weight"]):
            point["max_weight"] = float(weight)
        est = row.est_1rm if row.est_1rm else trainer_logic.est_1rm(weight, reps)
        if est is not None and (point["est_1rm"] is None or est > point["est_1rm"]):
            point["est_1rm"] = float(est)
    return [
        TrainerChartPointOut(
            date=day,
            est_1rm=(round(value["est_1rm"], 1) if value["est_1rm"] is not None else None),
            max_weight=(round(value["max_weight"], 1) if value["max_weight"] is not None else None),
            volume=round(value["volume"], 1),
        )
        for day, value in sorted(by_date.items())
    ]


def records_out(db: Session, tid: int, exercise_id: Optional[int] = None, limit: int = 60) -> list:
    """Текущие рекорды пользователя (одна строка на упражнение и тип рекорда)."""
    query = db.query(M.TrainerRecord).filter(M.TrainerRecord.telegram_id == tid)
    if exercise_id is not None:
        query = query.filter(M.TrainerRecord.exercise_id == exercise_id)
    rows = (
        query.order_by(M.TrainerRecord.date.desc(), M.TrainerRecord.id.desc())
        .limit(limit)
        .all()
    )
    ex_by_id = exercises_by_id(db, [r.exercise_id for r in rows])
    return [
        TrainerRecordOut(
            exercise=exercise_brief_out(ex_by_id.get(row.exercise_id)),
            record_type=row.record_type or "",
            value=float(row.value or 0.0),
            weight_kg=row.weight_kg,
            reps=row.reps,
            date=row.date,
        )
        for row in rows
    ]


def top_exercise_ids(db: Session, tid: int, limit: int = 5) -> list:
    """Топ упражнений по числу выполненных рабочих подходов (выбор графика)."""
    rows = (
        db.query(M.TrainerSetLog.exercise_id, func.count(M.TrainerSetLog.id).label("n"))
        .filter(
            M.TrainerSetLog.telegram_id == tid,
            M.TrainerSetLog.is_done.is_(True),
            (M.TrainerSetLog.set_type.is_(None)) | (M.TrainerSetLog.set_type == "work"),
            M.TrainerSetLog.exercise_id.isnot(None),
        )
        .group_by(M.TrainerSetLog.exercise_id)
        .order_by(func.count(M.TrainerSetLog.id).desc(), M.TrainerSetLog.exercise_id.asc())
        .limit(limit)
        .all()
    )
    return [row[0] for row in rows if row[0] is not None]


# --------------------------------------------------------------------------- #
#  §4.4 Прогресс и история упражнения
# --------------------------------------------------------------------------- #
@router.get("/progress", response_model=TrainerProgressOut)
def trainer_progress(
    exercise_id: Optional[int] = Query(None, description="Упражнение для графика"),
    period: str = Query("4w", description="4w|3m|all — период графика"),
    date: Optional[str] = Query(None, description="Локальная дата клиента, YYYY-MM-DD"),
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerProgressOut:
    """Экран «Прогресс» одним вызовом (ТЗ §4.4): стрик, итоги, мышцы, рекорды, график.

    Без ИИ. Объём по группам мышц считается только по выполненным рабочим
    подходам за 7 дней (разминочные и неотмеченные не учитываются).
    """
    tid = user.telegram_id
    day_iso = today_str(date)
    day = date_cls.fromisoformat(day_iso)
    profile = get_profile(db, tid)
    program = get_active_program(db, tid)
    days = get_program_days(db, program)
    dates = completed_session_dates(db, tid)

    # Итоги за 4 недели и сравнение текущей недели с прошлой.
    totals_4w = totals_between(db, tid, (day - timedelta(days=27)).isoformat(), day_iso)
    monday_str, sunday_str = trainer_logic.week_bounds(day_iso)
    prev_monday = date_cls.fromisoformat(monday_str) - timedelta(days=7)
    week_compare = TrainerWeekCompareOut(
        this=totals_between(db, tid, monday_str, sunday_str),
        prev=totals_between(
            db, tid, prev_monday.isoformat(), (prev_monday + timedelta(days=6)).isoformat()
        ),
    )

    # Объём по группам мышц за последние 7 дней.
    week_logs = done_work_logs(db, tid, since=(day - timedelta(days=6)).isoformat(), until=day_iso)
    muscle_by_id = {
        ex_id: row.muscle_group
        for ex_id, row in exercises_by_id(db, [log.exercise_id for log in week_logs]).items()
    }
    muscle_volume = [
        TrainerMuscleVolumeOut(**item)
        for item in trainer_logic.muscle_volume_summary(week_logs, muscle_by_id)
    ]

    # Топ-5 упражнений по частоте и график по выбранному (или самому частому).
    top_ids = top_exercise_ids(db, tid, 5)
    top_map = exercises_by_id(db, top_ids)
    top_exercises = [exercise_brief_out(top_map[i]) for i in top_ids if i in top_map]

    chart = None
    chart_id = exercise_id if exercise_id is not None else (top_ids[0] if top_ids else None)
    if chart_id is not None:
        chart_exercise = exercises_by_id(db, [chart_id]).get(chart_id)
        logs = done_work_logs(
            db, tid, since=period_start(period, day_iso), until=day_iso, exercise_id=chart_id
        )
        chart = TrainerChartOut(
            exercise_id=chart_id,
            exercise=exercise_brief_out(chart_exercise),
            points=chart_points(logs),
        )

    return TrainerProgressOut(
        streak=streak_out(dates, days, profile, day_iso),
        totals_4w=totals_4w,
        week_compare=week_compare,
        muscle_volume_7d=muscle_volume,
        records=records_out(db, tid),
        top_exercises=top_exercises,
        chart=chart,
        period=(period or "4w").strip().lower(),
    )


@router.get("/exercises/{exercise_id}/history", response_model=TrainerExerciseHistoryOut)
def trainer_exercise_history(
    exercise_id: int,
    limit: int = Query(20, ge=1, le=100, description="Сколько последних сессий вернуть"),
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerExerciseHistoryOut:
    """История упражнения: рекорды, последние сессии с подходами и точки графика."""
    tid = user.telegram_id
    exercise = get_exercise_or_404(db, exercise_id)

    rows = (
        db.query(M.TrainerSetLog)
        .filter(M.TrainerSetLog.telegram_id == tid, M.TrainerSetLog.exercise_id == exercise.id)
        .order_by(
            M.TrainerSetLog.date.desc(),
            M.TrainerSetLog.session_id.desc(),
            M.TrainerSetLog.set_index.asc(),
        )
        .all()
    )
    grouped: dict = {}
    for row in rows:
        if row.session_id is None:
            continue
        grouped.setdefault(row.session_id, []).append(row)
    session_ids = list(grouped.keys())[:limit]
    sessions_by_id = {
        s.id: s
        for s in db.query(M.TrainerSession).filter(M.TrainerSession.id.in_(session_ids)).all()
    } if session_ids else {}
    sessions = []
    for sid in session_ids:
        source = sessions_by_id.get(sid)
        sessions.append(
            TrainerHistorySessionOut(
                session_id=sid,
                date=(source.date if source is not None else grouped[sid][0].date),
                title=(source.title if source is not None else None),
                sets=[set_out(row) for row in sorted(grouped[sid], key=lambda r: r.set_index or 0)],
            )
        )

    work_rows = [row for row in rows if bool(row.is_done) and (row.set_type or "work") == "work"]
    return TrainerExerciseHistoryOut(
        exercise=exercise_brief_out(exercise),
        records=records_out(db, tid, exercise.id),
        sessions=sessions,
        points=chart_points(work_rows),
    )


# --------------------------------------------------------------------------- #
#  §4.5 Недельный разбор: сериализация
# --------------------------------------------------------------------------- #
def review_change_out(raw: dict, index: int, applied_ids: set) -> TrainerReviewChangeOut:
    """Элемент review_json["changes"] → схема правки (с флагом «уже применена»)."""
    raw = raw if isinstance(raw, dict) else {}
    try:
        cid = int(raw.get("id"))
    except (TypeError, ValueError):
        cid = index
    try:
        value = int(raw.get("value")) if raw.get("value") is not None else None
    except (TypeError, ValueError):
        value = None
    return TrainerReviewChangeOut(
        id=cid,
        type=str(raw.get("type") or ""),
        exercise_id=raw.get("exercise_id"),
        exercise_slug=raw.get("exercise_slug"),
        exercise_name_ru=raw.get("exercise_name_ru"),
        exercise_name_en=raw.get("exercise_name_en"),
        value=value,
        new_exercise_id=raw.get("new_exercise_id"),
        new_slug=raw.get("new_slug"),
        new_exercise_name_ru=raw.get("new_exercise_name_ru"),
        new_exercise_name_en=raw.get("new_exercise_name_en"),
        reason=str(raw.get("reason") or ""),
        applied=cid in applied_ids,
    )


def review_body_out(data: dict) -> TrainerReviewBodyOut:
    """review_json → тело разбора для фронта."""
    data = data if isinstance(data, dict) else {}
    applied_ids = set()
    for raw in json_list(data.get("applied_change_ids")):
        try:
            applied_ids.add(int(raw))
        except (TypeError, ValueError):
            continue
    changes = [
        review_change_out(raw, index, applied_ids)
        for index, raw in enumerate(json_list(data.get("changes")))
        if isinstance(raw, dict)
    ]
    return TrainerReviewBodyOut(
        summary=str(data.get("summary") or ""),
        wins=[str(x) for x in json_list(data.get("wins"))],
        issues=[str(x) for x in json_list(data.get("issues"))],
        nutrition=[str(x) for x in json_list(data.get("nutrition"))],
        changes=changes,
        next_week_focus=str(data.get("next_week_focus") or ""),
        motivation=str(data.get("motivation") or ""),
    )


def review_out(review, lang: str) -> TrainerWeeklyReviewOut:
    """ORM TrainerWeeklyReview → схема ответа (с медицинским дисклеймером)."""
    return TrainerWeeklyReviewOut(
        id=review.id,
        week_start=review.week_start,
        week_end=review.week_end,
        week=review.week,
        program_id=review.program_id,
        stats=json_obj(review.stats_json),
        review=review_body_out(json_obj(review.review_json)),
        applied=bool(review.applied),
        created_at=(review.created_at.isoformat() if review.created_at else None),
        disclaimer=(DISCLAIMER_EN if is_en(lang) else DISCLAIMER_RU),
    )


# --------------------------------------------------------------------------- #
#  §4.5 Недельный разбор: генерация, чтение, применение
# --------------------------------------------------------------------------- #
@router.post("/review/weekly", response_model=TrainerWeeklyReviewOut)
def trainer_review_weekly(
    data: TrainerReviewIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerWeeklyReviewOut:
    """Разобрать неделю тренером (ТЗ §4.5): статистика + правки на следующую неделю.

    Неделя по умолчанию — текущая; если сегодня Пн/Вт и в текущей неделе нет
    сессий, разбираем прошлую (`trainer_logic.pick_review_week_start`). 409 —
    если за неделю нет ни одной завершённой тренировки (разбирать нечего).
    Повторный вызов на ту же неделю перезаписывает разбор (регенерация).
    """
    tid = user.telegram_id
    lang = user_lang(user)
    day_iso = today_str(data.date)
    dates = completed_session_dates(db, tid)
    week_start = data.week_start or trainer_logic.pick_review_week_start(dates, day_iso)
    week_start, week_end = trainer_logic.week_bounds(week_start)

    stats = trainer_logic.collect_week_stats(db, tid, week_start, day_iso)
    if not stats.get("sessions"):
        raise HTTPException(status_code=409, detail="За эту неделю нет завершённых тренировок")

    # Лимиты — только когда разбор действительно пойдёт в модель.
    ratelimit.enforce_heavy(tid)
    ratelimit.enforce_ai(tid)
    try:
        result = trainer_ai.weekly_review(stats, lang)
    except (AIError, RuntimeError) as exc:
        raise ai_failed(exc, "Не удалось разобрать неделю. Попробуйте позже.", "review/weekly")

    program_info = stats.get("program") if isinstance(stats.get("program"), dict) else {}
    # Каталог нужен только промпту — в БД и на фронт его не тащим (сотня строк).
    stats_public = {k: v for k, v in stats.items() if k != "catalog"}

    review = (
        db.query(M.TrainerWeeklyReview)
        .filter(
            M.TrainerWeeklyReview.telegram_id == tid,
            M.TrainerWeeklyReview.week_start == week_start,
        )
        .order_by(M.TrainerWeeklyReview.id.desc())
        .first()
    )
    if review is None:
        review = M.TrainerWeeklyReview(telegram_id=tid, week_start=week_start)
        db.add(review)
    review.week_end = week_end
    review.program_id = program_info.get("id")
    review.week = program_info.get("week")
    review.stats_json = dumps(stats_public)
    review.review_json = dumps(result)
    review.applied = False
    review.created_at = datetime.utcnow()
    db.commit()
    db.refresh(review)

    logger.info(
        "trainer/review/weekly: tid=%s неделя %s, правок %d",
        tid, week_start, len(result.get("changes") or []),
    )
    return review_out(review, lang)


@router.get("/review/latest", response_model=Optional[TrainerWeeklyReviewOut])
def trainer_review_latest(
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> Optional[TrainerWeeklyReviewOut]:
    """Последний сохранённый разбор пользователя или null."""
    review = (
        db.query(M.TrainerWeeklyReview)
        .filter(M.TrainerWeeklyReview.telegram_id == user.telegram_id)
        .order_by(M.TrainerWeeklyReview.week_start.desc(), M.TrainerWeeklyReview.id.desc())
        .first()
    )
    if review is None:
        return None
    return review_out(review, user_lang(user))


@router.post("/review/{review_id}/apply", response_model=TrainerReviewApplyOut)
def trainer_review_apply(
    review_id: int,
    data: TrainerReviewApplyIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerReviewApplyOut:
    """Применить отмеченные правки разбора к следующей неделе программы (ТЗ §4.5).

    Без ИИ: `trainer_logic.apply_review_changes` правит `exercises_json` /
    `adjustments_json` дней недели+1 и состояния упражнений. Повторный вызов с
    теми же id ничего не меняет (правки идемпотентны).
    """
    tid = user.telegram_id
    review = (
        db.query(M.TrainerWeeklyReview)
        .filter(M.TrainerWeeklyReview.id == review_id, M.TrainerWeeklyReview.telegram_id == tid)
        .first()
    )
    if review is None:
        raise HTTPException(status_code=404, detail="Разбор не найден")
    result = trainer_logic.apply_review_changes(db, tid, review, data.change_ids, user_lang(user))
    return TrainerReviewApplyOut(
        applied=[int(x) for x in result.get("applied") or []],
        lines=[str(x) for x in result.get("lines") or []],
        next_week=result.get("next_week"),
        ok=True,
    )


# --------------------------------------------------------------------------- #
#  §4.5 Питание в день тренировки (совет дня с кэшем)
# --------------------------------------------------------------------------- #
def day_nutrition_numbers(db: Session, user: User, day_iso: str) -> TrainerNutritionNumbersOut:
    """Цифры дня из дневника: съедено, белок, сожжено (Workout), цели пользователя."""
    tid = user.telegram_id
    eaten_kcal = 0.0
    eaten_protein = 0.0
    for entry in (
        db.query(M.DiaryEntry)
        .filter(M.DiaryEntry.telegram_id == tid, M.DiaryEntry.date == day_iso)
        .all()
    ):
        eaten_kcal += float(entry.calories or 0)
        eaten_protein += float(entry.proteins or 0.0)
    burned = sum(
        int(w.calories_burned or 0)
        for w in db.query(M.Workout)
        .filter(M.Workout.telegram_id == tid, M.Workout.date == day_iso)
        .all()
    )
    try:
        protein_goal = int(float(user.target_proteins)) if user.target_proteins else None
    except (TypeError, ValueError):
        protein_goal = None
    return TrainerNutritionNumbersOut(
        goal_kcal=user.daily_goal_kcal,
        eaten_kcal=int(round(eaten_kcal)),
        burned_kcal=int(burned),
        protein_goal=protein_goal,
        protein_eaten=int(round(eaten_protein)),
    )


@router.get("/nutrition/today", response_model=TrainerNutritionTipOut)
def trainer_nutrition_today(
    date: Optional[str] = Query(None, description="Локальная дата клиента, YYYY-MM-DD"),
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainerNutritionTipOut:
    """Совет дня по питанию (ТЗ §4.5): тренировочный день или день отдыха.

    Кэш `TrainerDailyTip` по (пользователь, дата, вид дня, язык) — ИИ дёргается
    только при промахе. Цифры дня считаются заново при каждом запросе (они
    меняются в течение дня), кэшируется только текст совета.
    """
    tid = user.telegram_id
    lang = "en" if is_en(user_lang(user)) else "ru"
    day_iso = today_str(date)

    session = (
        db.query(M.TrainerSession)
        .filter(
            M.TrainerSession.telegram_id == tid,
            M.TrainerSession.date == day_iso,
            M.TrainerSession.status.in_(["completed", "in_progress"]),
        )
        .order_by(M.TrainerSession.id.desc())
        .first()
    )
    program = get_active_program(db, tid)
    planned_day = None
    if session is None and program is not None:
        planned_day = (
            db.query(M.TrainerProgramDay)
            .filter(
                M.TrainerProgramDay.program_id == program.id,
                M.TrainerProgramDay.scheduled_date == day_iso,
                M.TrainerProgramDay.status == "planned",
            )
            .order_by(M.TrainerProgramDay.id.asc())
            .first()
        )
    kind = "training" if (session is not None or planned_day is not None) else "rest"
    numbers = day_nutrition_numbers(db, user, day_iso)

    cached = (
        db.query(M.TrainerDailyTip)
        .filter(
            M.TrainerDailyTip.telegram_id == tid,
            M.TrainerDailyTip.date == day_iso,
            M.TrainerDailyTip.kind == kind,
            M.TrainerDailyTip.lang == lang,
        )
        .order_by(M.TrainerDailyTip.id.desc())
        .first()
    )
    tip = json_obj(cached.tip_json) if cached is not None else {}

    if not tip:
        ctx = {
            "kind": kind,
            "diet_goal": getattr(user, "diet_goal", None),
            "daily_goal_kcal": numbers.goal_kcal,
            "target_proteins": numbers.protein_goal,
            "eaten_kcal": numbers.eaten_kcal,
            "eaten_protein": numbers.protein_eaten,
            "burned_kcal": numbers.burned_kcal,
            "weight": getattr(user, "weight", None),
        }
        if session is not None:
            ctx.update({
                "workout_title": session.title,
                "session_type": session.session_type,
                "duration_min": session.duration_min,
                "calories_burned": session.calories_burned,
                "status": "done" if session.status == "completed" else "in_progress",
            })
        elif planned_day is not None:
            ctx.update({
                "workout_title": planned_day.title,
                "session_type": planned_day.session_type,
                "duration_min": planned_day.duration_min,
                "status": "planned",
            })

        ratelimit.enforce_ai(tid)
        try:
            tip = trainer_ai.nutrition_day_tip(ctx, lang)
        except (AIError, RuntimeError) as exc:
            raise ai_failed(
                exc, "Не удалось подготовить совет по питанию. Попробуйте позже.", "nutrition/today"
            )

        if cached is None:
            cached = M.TrainerDailyTip(telegram_id=tid, date=day_iso, kind=kind, lang=lang)
            db.add(cached)
        cached.tip_json = dumps(tip)
        db.commit()

    return TrainerNutritionTipOut(
        date=day_iso,
        kind=kind,
        headline=str(tip.get("headline") or ""),
        calories_note=str(tip.get("calories_note") or ""),
        protein_note=str(tip.get("protein_note") or ""),
        pre_workout=((tip.get("pre_workout") or None) if kind == "training" else None),
        post_workout=((tip.get("post_workout") or None) if kind == "training" else None),
        hydration=str(tip.get("hydration") or ""),
        tips=[str(x) for x in json_list(tip.get("tips"))],
        numbers=numbers,
    )
