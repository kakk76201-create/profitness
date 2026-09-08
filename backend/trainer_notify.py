"""Обогащение уведомлений AI-тренером (ТЗ §4.6, §1 «Напоминание о тренировке»).

Модуль намеренно отделён от `notifications.py`: там остаётся только вставка в
восемь строк с `try/except`, а вся логика тренера (активная программа, день
плана, его название и длительность) живёт здесь. Свой словарь текстов
`_TEXTS` — чтобы не править `_TEXTS` в `notifications.py`.

Главное правило модуля: он НИКОГДА не мешает отправить обычное напоминание.
Любая неожиданность (нет программы, битая строка, ошибка БД) → возвращаем
исходный текст без изменений.
"""

from __future__ import annotations

import json
import logging
from datetime import date as date_cls

logger = logging.getLogger("trainer_notify")

# --------------------------------------------------------------------------- #
#  Тексты (RU, EN) — собственный словарь модуля
# --------------------------------------------------------------------------- #
_TEXTS: dict[str, tuple[str, str]] = {
    "plan": ("Сегодня по плану: {title}", "Today's plan: {title}"),
    "day": ("День {n} — {title}", "Day {n} — {title}"),
    "minutes": ("{n} мин", "{n} min"),
    "exercises": ("{n} упр.", "{n} exercises"),
    "in_progress": (
        "Тренировка уже начата — можно продолжить: {title}",
        "A workout is already in progress — you can continue: {title}",
    ),
    "done": (
        "Сегодня вы уже потренировались: {title}. Отличная работа!",
        "You have already trained today: {title}. Great job!",
    ),
}


def _t(key: str, lang, **kwargs) -> str:
    """Текст по ключу на языке пользователя (по умолчанию русский)."""
    ru, en = _TEXTS[key]
    template = en if str(lang or "ru").lower().startswith("en") else ru
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template


def _count_items(value) -> int:
    """Сколько пунктов в JSON-поле плана (мусор → 0)."""
    if isinstance(value, list):
        return len(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return 0
        if isinstance(parsed, list):
            return len(parsed)
    return 0


def _day_title(day, lang) -> str:
    """Название дня для пуша: «День 2 — Верх тела» (или просто название)."""
    title = (getattr(day, "title", None) or "").strip()
    index = getattr(day, "day_index", None)
    if not title:
        return _t("day", lang, n=index or 1, title="")
    lowered = title.lower()
    if index and not (lowered.startswith("день") or lowered.startswith("day")):
        return _t("day", lang, n=index, title=title)
    return title


def training_day_line(db, tid: int, day_iso: str, lang: str = "ru") -> str | None:
    """Строка о сегодняшней тренировке из плана тренера или None.

    Возвращает «Сегодня по плану: День 2 — Верх тела, 45 мин, 6 упр.»; если
    сессия за сегодня уже завершена или идёт — соответствующую строку. Если
    программы нет или на сегодня в ней нет плановой тренировки — None.
    """
    from backend import models as M

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
    if session is not None:
        key = "done" if session.status == "completed" else "in_progress"
        return _t(key, lang, title=(session.title or "").strip() or _t("day", lang, n=1, title=""))

    program = (
        db.query(M.TrainerProgram)
        .filter(M.TrainerProgram.telegram_id == tid, M.TrainerProgram.status == "active")
        .order_by(M.TrainerProgram.id.desc())
        .first()
    )
    if program is None:
        return None

    day = (
        db.query(M.TrainerProgramDay)
        .filter(
            M.TrainerProgramDay.program_id == program.id,
            M.TrainerProgramDay.scheduled_date == day_iso,
            M.TrainerProgramDay.status == "planned",
        )
        .order_by(M.TrainerProgramDay.id.asc())
        .first()
    )
    if day is None:
        return None

    parts = [_day_title(day, lang)]
    minutes = getattr(day, "duration_min", None)
    if minutes:
        parts.append(_t("minutes", lang, n=int(minutes)))
    count = _count_items(getattr(day, "exercises_json", None))
    if count:
        parts.append(_t("exercises", lang, n=count))
    return _t("plan", lang, title=", ".join(p for p in parts if p))


def decorate_training_reminder(db, tid: int, today, lang: str, text: str) -> str:
    """Дополнить текст напоминания о тренировке планом дня (ТЗ §4.6).

    Вызывается из `notifications._process_training_reminder` внутри try/except.
    Любая проблема → возвращаем исходный текст: обычное напоминание важнее
    строки тренера.
    """
    base = text or ""
    try:
        day_iso = str(today or "").strip() or date_cls.today().isoformat()
        line = training_day_line(db, tid, day_iso, lang)
    except Exception:
        logger.exception("decorate_training_reminder: tid=%s", tid)
        return base
    if not line:
        return base
    return (base + "\n\n" + line) if base else line
