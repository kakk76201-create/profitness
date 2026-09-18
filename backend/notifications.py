"""
Планировщик и отправка push-уведомлений в Telegram.

Модуль работает поверх существующей архитектуры (см. database.py / models.py)
и НИЧЕГО в ней не меняет. Раз в минуту фоновый планировщик (APScheduler)
проверяет настройки уведомлений пользователей и отправляет напоминания через
Telegram Bot API (метод sendMessage по httpx).

Виды уведомлений:
  * приёмы пищи (breakfast / lunch / dinner) — берутся из NotificationSettings;
    шлются, только если за сегодня нет записи дневника соответствующего типа;
  * тренировка (trainrem:{id}) — берётся из таблицы TrainingReminder: по дням
    недели (CSV: Пн=0..Вс=6) и времени "HH:MM";
  * приём спортпита (supprem:{id}) — берётся из таблицы SupplementReminder
    (+ SupplementReminderItem -> Supplement.name): по времени "HH:MM", с
    перечислением названий добавок;
  * вечерняя сводка дня (summary) — берётся из NotificationSettings:
    съедено / цель / осталось.

Язык уведомлений:
  Тексты выдаются на языке пользователя (User.language = "ru"|"en", по умолчанию
  "ru"). Для приёмов пищи и сводки язык берётся напрямую из User (он уже
  выбирается в check_notifications). Для тренировок и спортпита язык подтягивается
  по telegram_id отдельным запросом (с фолбэком "ru" при любой ошибке/отсутствии).
  Маленький хелпер _msg() выбирает русский/английский текст по виду уведомления.

Важно: NotificationSettings теперь используется ТОЛЬКО для приёмов пищи (meal_*)
и вечерней сводки (daily_summary_*). Напоминания о тренировках и спортпите
переехали в отдельные таблицы TrainingReminder / SupplementReminder, поэтому
старые поля NotificationSettings (training_*, supplement_reminder_enabled) больше
не читаются (но и не удаляются — чтобы не терять данные).

Чтобы не слать одно и то же несколько раз за день, факт отправки фиксируется
в таблице NotificationLog (дедупликация по паре «вид + дата»).

Надёжность — главный приоритет: ВЕСЬ код обёрнут в try/except. Ни падение
планировщика, ни ошибка отправки (например, бот заблокирован пользователем —
HTTP 403) не должны валить приложение. Если планировщик отключён
(ENABLE_SCHEDULER="0") или не задан BOT_TOKEN — модуль просто бездействует.

Публичные функции:
    start_scheduler() -> scheduler | None
    stop_scheduler(sched) -> None
    check_notifications() -> None
    send_telegram(chat_id, text) -> bool
"""

import json
import logging
import os
from datetime import datetime

# httpx — для обращения к Telegram Bot API. Импортируем мягко, чтобы отсутствие
# зависимости не ломало импорт всего приложения.
try:
    import httpx
except Exception:  # pragma: no cover - на случай отсутствия httpx
    httpx = None

# APScheduler — фоновый планировщик. Тоже импортируем мягко.
try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:  # pragma: no cover - на случай отсутствия APScheduler
    BackgroundScheduler = None

# Часовой пояс приложения. По умолчанию — московское время; при недоступности
# zoneinfo/нужной зоны откатываемся на UTC, чтобы модуль продолжал работать.
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - очень старый Python
    ZoneInfo = None

from backend import adaptive
from backend import subscription
from backend.database import SessionLocal
from backend.models import (
    DiaryEntry,
    NotificationLog,
    NotificationSettings,
    Supplement,
    SupplementReminder,
    SupplementReminderItem,
    TrainingReminder,
    User,
    WeightLog,
    Workout,
)

logger = logging.getLogger("notifications")

# Токен Telegram-бота (без него отправка невозможна).
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Имя часового пояса приложения (например, "Europe/Moscow").
APP_TZ_NAME = os.getenv("APP_TZ", "Europe/Moscow")


def _resolve_tz():
    """Вернуть объект часового пояса приложения с откатом на UTC."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(APP_TZ_NAME)
        except Exception as exc:  # неизвестная зона / нет tzdata
            logger.warning("APP_TZ=%s недоступен (%s), используем UTC", APP_TZ_NAME, exc)
    # Фолбэк — наивный UTC через стандартную библиотеку.
    try:
        from datetime import timezone
        return timezone.utc
    except Exception:  # pragma: no cover
        return None


# Часовой пояс приложения, вычисляется один раз при импорте.
APP_TZ = _resolve_tz()


# --------------------------------------------------------------------------- #
#  Локализация текстов уведомлений (RU / EN)
# --------------------------------------------------------------------------- #
def _safe_rollback(db) -> None:
    """Откатить сессию после сбоя запроса, не поднимая новых исключений.

    КРИТИЧНО для PostgreSQL: там любая ошибка внутри транзакции переводит её в
    состояние aborted, и ВСЕ последующие запросы падают с InFailedSqlTransaction
    до отката. Без этого сбой безобидного чтения ломал последующий _mark_sent —
    факт отправки не записывался, и уведомление уходило заново каждую минуту.
    На SQLite вреда нет.
    """
    try:
        db.rollback()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Подавлено исключение: %r", exc)


def _norm_lang(lang) -> str:
    """Нормализовать язык пользователя к "ru" или "en" (по умолчанию "ru").

    Любое значение, начинающееся на "en" (без учёта регистра), трактуем как
    английский; всё остальное (включая None/пусто/"ru") — как русский. Это
    устойчиво к мусорным/устаревшим значениям в БД.
    """
    try:
        if str(lang or "").strip().lower().startswith("en"):
            return "en"
    except Exception as exc:  # noqa: BLE001
        logger.debug("Подавлено исключение: %r", exc)
    return "ru"


def _user_language(db, tid: int) -> str:
    """Подтянуть язык пользователя по telegram_id (фолбэк "ru").

    Используется там, где у нас на руках нет объекта User (напоминания о
    тренировках/спортпите идут по своим таблицам). При любой ошибке/отсутствии
    пользователя возвращаем "ru", чтобы не ломать отправку.
    """
    try:
        user = db.query(User).filter(User.telegram_id == tid).first()
        if user is not None:
            return _norm_lang(getattr(user, "language", None))
    except Exception as exc:
        logger.warning("_user_language: ошибка получения языка tid=%s: %s", tid, exc)
        _safe_rollback(db)
    return "ru"


# Словарь шаблонов текстов уведомлений: ключ -> {"ru": ..., "en": ...}.
# Для приёмов пищи метки приёма (label) тоже хранятся локализованными.
_TEXTS = {
    # Метки приёмов пищи (используются внутри текста напоминания о приёме).
    "meal_label_breakfast": {"ru": "завтрак", "en": "breakfast"},
    "meal_label_lunch": {"ru": "обед", "en": "lunch"},
    "meal_label_dinner": {"ru": "ужин", "en": "dinner"},
    # Обобщённая метка приёма пищи для произвольных времён (meal_times).
    "meal_label_generic": {"ru": "Приём пищи", "en": "Meal"},
    # Заголовок напоминания о приёме пищи (подставляется emoji и метка).
    # {emoji} — иконка приёма, {label} — локализованная метка приёма пищи.
    "meal_reminder": {
        "ru": (
            "{emoji} <b>Напоминание: {label}</b>\n"
            "Не забудьте поесть и записать приём пищи в дневник 🍽️"
        ),
        "en": (
            "{emoji} <b>Reminder: {label}</b>\n"
            "Don't forget to eat and log your meal in the diary 🍽️"
        ),
    },
    # Напоминание о тренировке.
    "training_reminder": {
        "ru": (
            "💪 <b>Напоминание о тренировке!</b>\n"
            "Пора размяться. После — не забудьте записать тренировку 🏋️"
        ),
        "en": (
            "💪 <b>Workout reminder!</b>\n"
            "Time to move. Afterwards — don't forget to log your workout 🏋️"
        ),
    },
    # Метка по умолчанию для напоминания о спортпите (если у строки нет своей).
    "supplement_default_label": {"ru": "Приём добавок", "en": "Supplements"},
    # Напоминание о приёме спортпита с перечислением названий.
    # {label} — метка напоминания, {names} — список названий добавок.
    "supplement_reminder_named": {
        "ru": (
            "💊 <b>Приём добавок</b>\n"
            "{label}: <b>{names}</b>"
        ),
        "en": (
            "💊 <b>Supplements</b>\n"
            "{label}: <b>{names}</b>"
        ),
    },
    # Напоминание о приёме спортпита без списка (только метка).
    "supplement_reminder_plain": {
        "ru": (
            "💊 <b>Приём добавок</b>\n"
            "{label}"
        ),
        "en": (
            "💊 <b>Supplements</b>\n"
            "{label}"
        ),
    },
    # Хвост вечерней сводки: осталось калорий / превышение.
    # {value} — число калорий.
    "summary_remaining": {
        "ru": "Осталось: <b>{value}</b> ккал ✅",
        "en": "Remaining: <b>{value}</b> kcal ✅",
    },
    "summary_exceeded": {
        "ru": "Превышение: <b>{value}</b> ккал ⚠️",
        "en": "Exceeded by: <b>{value}</b> kcal ⚠️",
    },
    # Вечерняя сводка дня, когда цель задана. {eaten}/{goal}/{tail}.
    "summary_with_goal": {
        "ru": (
            "📊 <b>Итоги дня</b>\n"
            "Съедено: <b>{eaten}</b> ккал\n"
            "Цель: <b>{goal}</b> ккал\n"
            "{tail}"
        ),
        "en": (
            "📊 <b>Daily summary</b>\n"
            "Eaten: <b>{eaten}</b> kcal\n"
            "Goal: <b>{goal}</b> kcal\n"
            "{tail}"
        ),
    },
    # Дополнительная строка вечерней сводки: серия дней подряд («стрик»).
    # Добавляется к сводке, если серия ≥ 2 дней. {n} — число дней.
    "summary_streak": {
        "ru": "🔥 Серия: <b>{n}</b> дн. подряд — так держать!",
        "en": "🔥 Streak: <b>{n}</b> days in a row — keep it up!",
    },
    # Авто-пересчёт адаптивных калорий (Этап 3). {explanation} — готовый
    # локализованный текст пояснения из adaptive.run_adaptive_recalc.
    "adaptive_recalc": {
        "ru": (
            "📊 <b>Адаптивные калории обновлены</b>\n"
            "{explanation}"
        ),
        "en": (
            "📊 <b>Adaptive calories updated</b>\n"
            "{explanation}"
        ),
    },
    # Вечерняя сводка дня, когда цель НЕ задана. {eaten}.
    "summary_no_goal": {
        "ru": (
            "📊 <b>Итоги дня</b>\n"
            "Съедено: <b>{eaten}</b> ккал\n"
            "Цель по калориям не задана — задайте её в профиле 🎯"
        ),
        "en": (
            "📊 <b>Daily summary</b>\n"
            "Eaten: <b>{eaten}</b> kcal\n"
            "Calorie goal not set — set it in your profile 🎯"
        ),
    },
    # --- Недельный КОРОТКИЙ отчёт (Этап 5, БЕЗ AI) --------------------------- #
    # Заголовок недельного пуша. Тело собирается из строк ниже динамически
    # (показываем только те метрики, по которым есть данные), затем добавляется
    # подсказка открыть полный AI-отчёт в приложении.
    "weekreport_title": {
        "ru": "📅 <b>Итоги недели</b>",
        "en": "📅 <b>Your week in review</b>",
    },
    # Строка средних калорий за неделю. {value} — округлённое среднее.
    "weekreport_avg_calories": {
        "ru": "Средние калории: <b>{value}</b> ккал/день",
        "en": "Average calories: <b>{value}</b> kcal/day",
    },
    # Изменение веса за неделю. {value} — модуль изменения с одним знаком, {sign}
    # — "+"/"−"/"" (для нуля). Заполняется в _process_weekly_report.
    "weekreport_weight": {
        "ru": "Изменение веса: <b>{sign}{value}</b> кг",
        "en": "Weight change: <b>{sign}{value}</b> kg",
    },
    # Количество тренировок за неделю. {value} — целое число.
    "weekreport_workouts": {
        "ru": "Тренировок: <b>{value}</b>",
        "en": "Workouts: <b>{value}</b>",
    },
    # Подсказка-хвост: открыть полный отчёт в приложении.
    "weekreport_hint": {
        "ru": "Полный AI-отчёт по неделе — в приложении 📲",
        "en": "Full AI weekly report — in the app 📲",
    },
}


def _msg(key: str, lang: str, **kwargs) -> str:
    """Вернуть локализованный текст по ключу с подстановкой параметров.

    Хелпер выбирает русский/английский вариант (фолбэк на "ru", а затем на
    «первый доступный») и форматирует его через str.format(**kwargs). Любой сбой
    форматирования не должен ронять отправку — отдаём неформатированный шаблон.
    """
    variants = _TEXTS.get(key, {})
    template = variants.get(lang)
    if template is None:
        # Нет нужного языка — пробуем русский, затем любой доступный вариант.
        template = variants.get("ru") or (next(iter(variants.values()), "") if variants else "")
    try:
        return template.format(**kwargs) if kwargs else template
    except Exception:
        # Подстановка не удалась — возвращаем шаблон как есть (лучше, чем падение).
        return template


# --------------------------------------------------------------------------- #
#  Отправка сообщения в Telegram
# --------------------------------------------------------------------------- #
def send_telegram(chat_id: int, text: str) -> bool:
    """Отправить текстовое сообщение пользователю через Telegram Bot API.

    Возвращает True при успехе и False при любой ошибке (нет токена/httpx,
    сетевой сбой, бот заблокирован пользователем -> 403 и т.п.). Никогда не
    бросает исключение наружу.
    """
    if not BOT_TOKEN:
        # Без токена слать некуда — тихо выходим (это не ошибка приложения).
        logger.debug("send_telegram: BOT_TOKEN не задан, пропуск")
        return False
    if httpx is None:
        logger.warning("send_telegram: httpx не установлен, отправка невозможна")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                # Отключаем превью ссылок — в напоминаниях оно не нужно.
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        # Частые случаи: 403 — бот заблокирован пользователем; 400 — неверный chat_id.
        logger.warning(
            "send_telegram: chat_id=%s статус=%s ответ=%s",
            chat_id, resp.status_code, resp.text[:300],
        )
        return False
    except Exception as exc:
        # Любой сетевой/прочий сбой — логируем и считаем неуспехом.
        logger.warning("send_telegram: ошибка отправки chat_id=%s: %s", chat_id, exc)
        return False


# --------------------------------------------------------------------------- #
#  Дедупликация: журнал отправленных уведомлений (NotificationLog)
# --------------------------------------------------------------------------- #
def _was_sent(db, tid: int, kind: str, date: str) -> bool:
    """Проверить, отправлялось ли уведомление данного вида этому юзеру сегодня."""
    try:
        return (
            db.query(NotificationLog)
            .filter(
                NotificationLog.telegram_id == tid,
                NotificationLog.kind == kind,
                NotificationLog.date == date,
            )
            .first()
            is not None
        )
    except Exception as exc:
        # При сбое запроса считаем «не отправлено», но защищаемся от дублей выше.
        logger.warning("_was_sent: ошибка запроса (%s) tid=%s kind=%s", exc, tid, kind)
        _safe_rollback(db)
        return False


def _mark_sent(db, tid: int, kind: str, date: str) -> None:
    """Зафиксировать факт отправки уведомления (для дедупликации)."""
    try:
        db.add(
            NotificationLog(
                telegram_id=tid,
                kind=kind,
                date=date,
            )
        )
        db.commit()
    except Exception as exc:
        logger.warning("_mark_sent: не удалось записать лог (%s) tid=%s kind=%s", exc, tid, kind)
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)


# --------------------------------------------------------------------------- #
#  Вспомогательное: сравнение времени "HH:MM" с текущим
# --------------------------------------------------------------------------- #
def _time_reached(now: datetime, hhmm: str | None) -> bool:
    """True, если текущее время (now) уже достигло заданного "HH:MM".

    Сравниваем по минутам в пределах текущих суток. Некорректные/пустые
    значения трактуем как «время ещё не наступило» (False).
    """
    if not hhmm:
        return False
    try:
        parts = str(hhmm).strip().split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        return False
    now_minutes = now.hour * 60 + now.minute
    target_minutes = hour * 60 + minute
    return now_minutes >= target_minutes


def _parse_weekdays(raw: str | None) -> set:
    """Разобрать CSV дней недели ("0,2,4") в множество int (Пн=0..Вс=6).

    Некорректные/пустые элементы тихо игнорируются. Возвращает set чисел.
    """
    result: set = set()
    if not raw:
        return result
    try:
        for part in str(raw).split(","):
            part = part.strip()
            if part == "":
                continue
            try:
                day = int(part)
            except (ValueError, TypeError):
                continue
            if 0 <= day <= 6:
                result.add(day)
    except Exception as exc:
        logger.warning("_parse_weekdays: не удалось разобрать '%s' (%s)", raw, exc)
    return result


def _parse_meal_times(raw: str | None) -> list:
    """Разобрать JSON-массив времён приёмов пищи ("[\"09:00\",\"13:00\"]") в список строк "HH:MM".

    Некорректный/пустой JSON и не-строковые элементы тихо игнорируются —
    возвращаем то, что удалось распознать (в худшем случае пустой список).
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        logger.warning("_parse_meal_times: не удалось разобрать JSON '%s' (%s)", raw, exc)
        return []
    if not isinstance(parsed, list):
        return []
    result: list = []
    for item in parsed:
        try:
            s = str(item).strip()
        except Exception:
            continue
        if s:
            result.append(s)
    return result


def _has_diary_entry(db, tid: int, date: str, meal_type: str) -> bool:
    """Есть ли у пользователя за дату запись дневника указанного приёма пищи."""
    try:
        return (
            db.query(DiaryEntry)
            .filter(
                DiaryEntry.telegram_id == tid,
                DiaryEntry.date == date,
                DiaryEntry.meal_type == meal_type,
            )
            .first()
            is not None
        )
    except Exception as exc:
        logger.warning("_has_diary_entry: ошибка запроса (%s) tid=%s", exc, tid)
        _safe_rollback(db)
        # При сбое лучше НЕ слать напоминание (считаем, что запись есть).
        return True


# --------------------------------------------------------------------------- #
#  Обработка приёмов пищи и вечерней сводки (NotificationSettings)
# --------------------------------------------------------------------------- #
def _process_meal_reminder(db, tid: int, today: str, now: datetime,
                           kind: str, meal_time: str | None,
                           label_key: str, emoji: str, lang: str) -> None:
    """Напоминание о приёме пищи: шлём, только если запись ещё не сделана.

    Текст и метку приёма (label) берём на языке пользователя (lang). label_key —
    ключ локализованной метки приёма ("meal_label_breakfast" и т.п.).
    """
    if not _time_reached(now, meal_time):
        return
    if _was_sent(db, tid, kind, today):
        return
    # Если запись этого приёма пищи за сегодня уже есть — напоминать не нужно.
    if _has_diary_entry(db, tid, today, kind):
        return
    # Локализованная метка приёма пищи (завтрак/обед/ужин -> breakfast/lunch/dinner).
    label = _msg(label_key, lang)
    text = _msg("meal_reminder", lang, emoji=emoji, label=label)
    if send_telegram(tid, text):
        _mark_sent(db, tid, kind, today)


def _current_streak(db, tid: int, today: str) -> int:
    """Текущая серия дней подряд с записями дневника, считая от today назад.

    Возвращает 0, если за today записей нет (серия не активна на сегодня).
    """
    from datetime import date as _date, timedelta as _timedelta

    try:
        rows = (
            db.query(DiaryEntry.date)
            .filter(DiaryEntry.telegram_id == tid)
            .distinct()
            .all()
        )
    except Exception:
        _safe_rollback(db)
        return 0
    dates = {r[0] for r in rows if r[0]}
    try:
        d = _date.fromisoformat(today)
    except (ValueError, TypeError):
        return 0
    n = 0
    while d.isoformat() in dates:
        n += 1
        d = d - _timedelta(days=1)
    return n


def _process_daily_summary(db, tid: int, today: str, now: datetime,
                           user: "User", settings: "NotificationSettings") -> None:
    """Вечерняя сводка по дню: съедено / цель / осталось (на языке пользователя)."""
    if not getattr(settings, "daily_summary_enabled", False):
        return
    if not _time_reached(now, getattr(settings, "summary_time", None)):
        return
    if _was_sent(db, tid, "summary", today):
        return

    # Язык пользователя для текста сводки (объект User у нас уже на руках).
    lang = _norm_lang(getattr(user, "language", None))

    # Считаем съеденные за день калории.
    try:
        entries = (
            db.query(DiaryEntry)
            .filter(DiaryEntry.telegram_id == tid, DiaryEntry.date == today)
            .all()
        )
    except Exception as exc:
        logger.warning("_process_daily_summary: ошибка выборки дневника (%s) tid=%s", exc, tid)
        return

    eaten = sum((e.calories or 0) for e in entries)
    goal = getattr(user, "daily_goal_kcal", None)

    if goal:
        remaining = goal - eaten
        if remaining >= 0:
            tail = _msg("summary_remaining", lang, value=remaining)
        else:
            tail = _msg("summary_exceeded", lang, value=abs(remaining))
        text = _msg("summary_with_goal", lang, eaten=eaten, goal=goal, tail=tail)
    else:
        # Цель не задана — отдаём только факт съеденного.
        text = _msg("summary_no_goal", lang, eaten=eaten)

    # Строка серии («стрик»): добавляем, только если сегодня есть записи
    # (серия активна) и её длина ≥ 2 дней — иначе это не мотивирует.
    if entries:
        streak = _current_streak(db, tid, today)
        if streak >= 2:
            text = text + "\n" + _msg("summary_streak", lang, n=streak)

    if send_telegram(tid, text):
        _mark_sent(db, tid, "summary", today)


# --------------------------------------------------------------------------- #
#  Обработка напоминаний о тренировках (таблица TrainingReminder)
# --------------------------------------------------------------------------- #
def _process_training_reminder(db, reminder: "TrainingReminder",
                               today: str, now: datetime) -> None:
    """Напоминание о тренировке по строке TrainingReminder.

    Шлём, если включено, СЕГОДНЯШНИЙ день недели (now.weekday(): Пн=0..Вс=6)
    присутствует в CSV weekdays, время "HH:MM" уже наступило и сегодня ещё не
    отправляли (дедуп по kind="trainrem:{id}"). Текст — на языке пользователя
    (подтягиваем по telegram_id, фолбэк "ru").
    """
    tid = getattr(reminder, "telegram_id", None)
    rid = getattr(reminder, "id", None)
    if tid is None or rid is None:
        return
    if not getattr(reminder, "enabled", False):
        return

    # Проверяем день недели: сегодняшний weekday должен входить в список.
    weekdays = _parse_weekdays(getattr(reminder, "weekdays", None))
    if now.weekday() not in weekdays:
        return

    if not _time_reached(now, getattr(reminder, "time", None)):
        return

    kind = f"trainrem:{rid}"
    if _was_sent(db, tid, kind, today):
        return

    # Язык пользователя подтягиваем по telegram_id (у строки TrainingReminder
    # объекта User нет). При ошибке/отсутствии — "ru".
    lang = _user_language(db, tid)
    text = _msg("training_reminder", lang)

    # Дополняем напоминание планом дня из AI-тренера (ТЗ §4.6): «Сегодня по
    # плану: День 2 — Верх тела, 45 мин». Сбой тренера не должен мешать
    # отправке обычного напоминания — поэтому широкий except.
    try:
        from backend import trainer_notify

        text = trainer_notify.decorate_training_reminder(db, tid, today, lang, text)
    except Exception:
        logger.exception("_process_training_reminder: не удалось дополнить текст планом тренера")

    if send_telegram(tid, text):
        _mark_sent(db, tid, kind, today)


# --------------------------------------------------------------------------- #
#  Обработка напоминаний о спортпите (таблицы SupplementReminder + items)
# --------------------------------------------------------------------------- #
def _process_supplement_reminder(db, reminder: "SupplementReminder",
                                 today: str, now: datetime) -> None:
    """Напоминание о приёме спортпита по строке SupplementReminder.

    Шлём, если включено и время "HH:MM" уже наступило (дедуп по
    kind="supprem:{id}"). Названия добавок собираем через SupplementReminderItem
    -> Supplement.name, учитывая только добавки, принадлежащие тому же
    пользователю. Если список пуст — шлём общий текст по метке (label). Текст —
    на языке пользователя (подтягиваем по telegram_id, фолбэк "ru").
    """
    tid = getattr(reminder, "telegram_id", None)
    rid = getattr(reminder, "id", None)
    if tid is None or rid is None:
        return
    if not getattr(reminder, "enabled", False):
        return
    if not _time_reached(now, getattr(reminder, "time", None)):
        return

    kind = f"supprem:{rid}"
    if _was_sent(db, tid, kind, today):
        return

    # Язык пользователя (по telegram_id, фолбэк "ru").
    lang = _user_language(db, tid)

    # Метка напоминания ("Утро" / "Ночь" / своё). Если у строки метки нет —
    # подставляем локализованную метку по умолчанию.
    label = getattr(reminder, "label", None) or _msg("supplement_default_label", lang)

    # Собираем названия добавок: items -> Supplement (только этого пользователя).
    names: list[str] = []
    try:
        items = (
            db.query(SupplementReminderItem)
            .filter(SupplementReminderItem.reminder_id == rid)
            .all()
        )
        sup_ids = [
            getattr(it, "supplement_id", None)
            for it in items
            if getattr(it, "supplement_id", None) is not None
        ]
        if sup_ids:
            supplements = (
                db.query(Supplement)
                .filter(
                    Supplement.id.in_(sup_ids),
                    # Только добавки, принадлежащие этому же пользователю.
                    Supplement.telegram_id == tid,
                )
                .all()
            )
            names = [s.name for s in supplements if getattr(s, "name", None)]
    except Exception as exc:
        # Не удалось подтянуть названия — отправим хотя бы общий текст по метке.
        logger.warning(
            "_process_supplement_reminder: ошибка сбора добавок (%s) rid=%s tid=%s",
            exc, rid, tid,
        )
        names = []

    if names:
        text = _msg("supplement_reminder_named", lang, label=label, names=", ".join(names))
    else:
        # В напоминании не осталось добавок: отдельной формы напоминаний в
        # приложении больше нет, и выключить такое «пустое» сообщение человеку
        # было бы негде. Не шлём.
        logger.debug("_process_supplement_reminder: пустое напоминание rid=%s — пропуск", rid)
        # Отмечаем день: иначе строка перепроверялась бы запросами каждую минуту.
        _mark_sent(db, tid, kind, today)
        return

    if send_telegram(tid, text):
        _mark_sent(db, tid, kind, today)


# --------------------------------------------------------------------------- #
#  Авто-пересчёт адаптивных калорий раз в неделю (Этап 3)
# --------------------------------------------------------------------------- #
def _adaptive_due(user: "User", today: str) -> bool:
    """Пора ли пересчитать адаптивные калории пользователю.

    Пересчитываем, если расчёта ещё не было (adaptive_last_calc пусто) ИЛИ с
    последнего прошло 7 дней и более. Сравниваем ISO-даты ("YYYY-MM-DD"):
    лексикографическое сравнение строк дат корректно совпадает с хронологией.
    Любой сбой парсинга трактуем как «пора» — лучше пересчитать, чем застрять.
    """
    last = getattr(user, "adaptive_last_calc", None)
    if not last:
        return True
    try:
        from datetime import date as _date

        last_date = _date.fromisoformat(str(last)[:10])
        today_date = _date.fromisoformat(today)
        return (today_date - last_date).days >= 7
    except Exception:
        # Кривое значение в БД — считаем, что пора пересчитать.
        return True


def _process_adaptive_recalc(db, user: "User", today: str) -> None:
    """Раз в неделю пересчитать адаптивные калории и уведомить пользователя.

    Запускаем только для пользователей с adaptive_enabled и только если пора
    (adaptive_last_calc пуст или старше 7 дней). Сам пересчёт делает
    adaptive.run_adaptive_recalc: при достатке данных он сохраняет новую цель и
    обновляет adaptive_last_calc (это же обеспечивает дедуп — раз в 7 дней).
    Если данных хватило (enough_data) — шлём уведомление на языке пользователя.
    """
    tid = getattr(user, "telegram_id", None)
    if tid is None:
        return
    if not getattr(user, "adaptive_enabled", False):
        return
    if not _adaptive_due(user, today):
        return

    # Пересчёт полностью изолирован внутри run_adaptive_recalc (свой try/except),
    # но дополнительно страхуемся здесь, чтобы сбой не сорвал остальную рассылку.
    try:
        result = adaptive.run_adaptive_recalc(db, user, lang=getattr(user, "language", None))
    except Exception as exc:
        logger.warning("_process_adaptive_recalc: ошибка пересчёта tid=%s: %s", tid, exc)
        return

    if not isinstance(result, dict) or not result.get("enough_data"):
        # Данных пока недостаточно — ничего не шлём (повторим на следующей проверке).
        return

    lang = _norm_lang(getattr(user, "language", None))
    explanation = result.get("explanation") or ""
    text = _msg("adaptive_recalc", lang, explanation=explanation)
    # Уведомление не критично: если отправка не удалась — adaptive_last_calc уже
    # обновлён внутри recalc, поэтому повторного спама не будет.
    send_telegram(tid, text)


# --------------------------------------------------------------------------- #
#  Недельный КОРОТКИЙ отчёт раз в неделю (Этап 5, БЕЗ AI)
# --------------------------------------------------------------------------- #
def _iso_week_key(now: datetime) -> str:
    """Ключ текущей ISO-недели вида "2026-W27" — используется как «дата» дедупа.

    Раз в неделю недельный пуш должен уйти ровно один раз. NotificationLog
    дедуплицирует по паре «вид + дата», поэтому в качестве «даты» подставляем
    не календарный день, а номер ISO-недели — тогда повторный вызов в течение
    той же недели отсеется. Любой сбой -> фолбэк на ISO-дату дня (безопасно:
    в худшем случае пуш может уйти не более раза в день, а не раз в неделю).
    """
    try:
        iso_year, iso_week, _ = now.isocalendar()
        return f"{iso_year}-W{int(iso_week):02d}"
    except Exception:
        try:
            return now.date().isoformat()
        except Exception:
            return "weekreport"


def _collect_week_short_stats(db, tid: int, today: str) -> dict:
    """Собрать КОРОТКУЮ статистику за последние 7 дней (без AI, без OpenAI).

    Возвращает словарь с ключами:
      * avg_calories (int|None) — среднее по дням, в которых ЕСТЬ записи дневника;
      * weight_change_kg (float|None) — изменение веса (последний − первый замер
        за период), знак сохраняем; None, если замеров меньше двух;
      * workouts_count (int) — количество тренировок за период.

    Период — 7 дней, включая сегодня (ISO-даты в диапазоне [start; today]).
    Любая ошибка по конкретной метрике не должна срывать остальные: каждая
    выборка в своём try/except, при сбое метрика трактуется как «нет данных».
    """
    stats = {"avg_calories": None, "weight_change_kg": None, "workouts_count": 0}

    # Начало периода — 6 дней назад (итого 7 календарных дней вместе с сегодня).
    try:
        from datetime import date as _date, timedelta as _timedelta

        today_date = _date.fromisoformat(today)
        start_str = (today_date - _timedelta(days=6)).isoformat()
    except Exception as exc:
        logger.warning("_collect_week_short_stats: не удалось вычислить период (%s) tid=%s", exc, tid)
        return stats

    # --- Средние калории по дням с записями -------------------------------- #
    try:
        entries = (
            db.query(DiaryEntry)
            .filter(
                DiaryEntry.telegram_id == tid,
                DiaryEntry.date >= start_str,
                DiaryEntry.date <= today,
            )
            .all()
        )
        per_day: dict = {}
        for e in entries:
            d = getattr(e, "date", None)
            if not d:
                continue
            per_day[d] = per_day.get(d, 0) + (getattr(e, "calories", 0) or 0)
        if per_day:
            stats["avg_calories"] = int(round(sum(per_day.values()) / len(per_day)))
    except Exception as exc:
        logger.warning("_collect_week_short_stats: ошибка калорий (%s) tid=%s", exc, tid)
        _safe_rollback(db)

    # --- Изменение веса (последний − первый замер за период) --------------- #
    try:
        weights = (
            db.query(WeightLog)
            .filter(
                WeightLog.telegram_id == tid,
                WeightLog.date >= start_str,
                WeightLog.date <= today,
            )
            .order_by(WeightLog.date.asc())
            .all()
        )
        valid = [w for w in weights if getattr(w, "weight", None) is not None]
        if len(valid) >= 2:
            stats["weight_change_kg"] = round(
                float(valid[-1].weight) - float(valid[0].weight), 1
            )
    except Exception as exc:
        logger.warning("_collect_week_short_stats: ошибка веса (%s) tid=%s", exc, tid)
        _safe_rollback(db)

    # --- Количество тренировок -------------------------------------------- #
    try:
        workouts_count = (
            db.query(Workout)
            .filter(
                Workout.telegram_id == tid,
                Workout.date >= start_str,
                Workout.date <= today,
            )
            .count()
        )
        stats["workouts_count"] = int(workouts_count or 0)
    except Exception as exc:
        logger.warning("_collect_week_short_stats: ошибка тренировок (%s) tid=%s", exc, tid)
        _safe_rollback(db)

    return stats


def _process_weekly_report(db, tid: int, today: str, now: datetime,
                           user: "User", settings: "NotificationSettings") -> None:
    """Раз в неделю — КОРОТКИЙ статус-пуш за 7 дней (БЕЗ AI, без OpenAI).

    Шлём только премиум-пользователям с включённой вечерней сводкой
    (daily_summary_enabled). Дедуп — по kind="weekreport" и текущей ISO-неделе
    (см. _iso_week_key), поэтому за неделю уйдёт ровно один пуш. Время отправки
    привязываем к summary_time (как у вечерней сводки), чтобы не будить ночью.

    Текст собираем динамически из имеющихся метрик (средние калории, изменение
    веса, количество тренировок) на языке пользователя и добавляем подсказку
    открыть полный AI-отчёт в приложении. OpenAI здесь НЕ вызывается — чтобы не
    нагружать минутный планировщик.
    """
    # Только при включённой ежедневной сводке (повторно используем тот же тумблер).
    if not getattr(settings, "daily_summary_enabled", False):
        return

    # Только премиум (владелец/активная подписка). is_premium безопасен к None-полям.
    try:
        if not subscription.is_premium(user):
            return
    except Exception as exc:
        logger.warning("_process_weekly_report: ошибка проверки премиума tid=%s: %s", tid, exc)
        return

    # Не отправляем раньше времени вечерней сводки (фолбэк "21:00").
    if not _time_reached(now, getattr(settings, "summary_time", None) or "21:00"):
        return

    # Дедуп по ISO-неделе: «дата» = ключ недели, kind = "weekreport".
    week_key = _iso_week_key(now)
    if _was_sent(db, tid, "weekreport", week_key):
        return

    # Язык пользователя (объект User уже на руках).
    lang = _norm_lang(getattr(user, "language", None))

    # Короткая статистика за 7 дней (без AI).
    stats = _collect_week_short_stats(db, tid, today)

    # Собираем тело из доступных метрик (пропускаем те, по которым нет данных).
    lines = [_msg("weekreport_title", lang)]

    avg_cal = stats.get("avg_calories")
    if avg_cal is not None:
        lines.append(_msg("weekreport_avg_calories", lang, value=avg_cal))

    weight_change = stats.get("weight_change_kg")
    if weight_change is not None:
        # Знак выводим явно: "+" набор, "−" снижение, "" если ровно ноль.
        if weight_change > 0:
            sign = "+"
        elif weight_change < 0:
            sign = "−"  # настоящий минус (U+2212) — аккуратнее в тексте
        else:
            sign = ""
        lines.append(
            _msg("weekreport_weight", lang, sign=sign, value=abs(weight_change))
        )

    lines.append(_msg("weekreport_workouts", lang, value=stats.get("workouts_count", 0)))
    lines.append(_msg("weekreport_hint", lang))

    text = "\n".join(part for part in lines if part)

    if send_telegram(tid, text):
        _mark_sent(db, tid, "weekreport", week_key)


# --------------------------------------------------------------------------- #
#  Главная функция проверки (вызывается планировщиком каждую минуту)
# --------------------------------------------------------------------------- #
def check_notifications() -> None:
    """Проверить условия и разослать уведомления всем пользователям.

    Открывает собственную сессию БД и обрабатывает:
      1) приёмы пищи и вечернюю сводку — по строкам NotificationSettings;
      2) напоминания о тренировках — по строкам TrainingReminder;
      3) напоминания о спортпите — по строкам SupplementReminder.

    Каждая сущность обрабатывается в своём try/except, чтобы сбой одной не
    останавливал остальные. Сессия закрывается в finally.
    """
    db = SessionLocal()
    try:
        # Текущее время в часовом поясе приложения и ISO-дата «сегодня».
        try:
            now = datetime.now(APP_TZ) if APP_TZ is not None else datetime.now()
        except Exception:
            now = datetime.now()
        today = now.date().isoformat()

        # --- 1) Приёмы пищи и вечерняя сводка (NotificationSettings) ---------- #
        try:
            all_settings = db.query(NotificationSettings).all()
        except Exception as exc:
            logger.warning("check_notifications: не удалось прочитать настройки (%s)", exc)
            all_settings = []

        for settings in all_settings:
            tid = getattr(settings, "telegram_id", None)
            if tid is None:
                continue
            try:
                # Профиль пользователя нужен для вечерней сводки (цель калорий)
                # и для выбора языка уведомлений о приёмах пищи / сводки.
                user = (
                    db.query(User)
                    .filter(User.telegram_id == tid)
                    .first()
                )
                if user is None:
                    # Настройки без пользователя — пропускаем (целостность данных).
                    continue

                # Язык пользователя для приёмов пищи (фолбэк "ru").
                lang = _norm_lang(getattr(user, "language", None))

                # Напоминания о приёмах пищи: произвольный список времён из
                # meal_times_json (заменяет фиксированные завтрак/обед/ужин).
                # Для каждого времени шлём обобщённое напоминание; если список
                # пуст — не шлём ничего. Дедуп — по kind="meal:HH:MM".
                if getattr(settings, "meal_reminder_enabled", False):
                    meal_times = _parse_meal_times(
                        getattr(settings, "meal_times_json", None)
                    )
                    for meal_time in meal_times:
                        _process_meal_reminder(
                            db, tid, today, now,
                            kind="meal:" + meal_time,
                            meal_time=meal_time,
                            label_key="meal_label_generic", emoji="🍽️", lang=lang,
                        )

                # Вечерняя сводка дня (язык берётся из user внутри функции).
                _process_daily_summary(db, tid, today, now, user, settings)

                # Недельный КОРОТКИЙ отчёт (Этап 5, БЕЗ AI): раз в неделю, только
                # премиум + включённая сводка. Полностью изолирован собственным
                # try/except, чтобы новая логика не сорвала остальную рассылку.
                try:
                    _process_weekly_report(db, tid, today, now, user, settings)
                except Exception as exc_wr:
                    logger.warning(
                        "check_notifications: сбой недельного отчёта tid=%s: %s", tid, exc_wr
                    )
                    try:
                        db.rollback()
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Подавлено исключение: %r", exc)

            except Exception as exc:
                # Сбой по одному пользователю не должен прерывать рассылку.
                logger.warning("check_notifications: сбой по пользователю tid=%s: %s", tid, exc)
                try:
                    db.rollback()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Подавлено исключение: %r", exc)

        # --- 2) Напоминания о тренировках (TrainingReminder) ------------------ #
        try:
            training_reminders = (
                db.query(TrainingReminder)
                .filter(TrainingReminder.enabled == True)  # noqa: E712 — нужно для SQL
                .all()
            )
        except Exception as exc:
            logger.warning("check_notifications: не удалось прочитать TrainingReminder (%s)", exc)
            training_reminders = []

        for reminder in training_reminders:
            try:
                _process_training_reminder(db, reminder, today, now)
            except Exception as exc:
                # Сбой по одному напоминанию не должен прерывать остальные.
                rid = getattr(reminder, "id", None)
                logger.warning(
                    "check_notifications: сбой тренировочного напоминания id=%s: %s", rid, exc
                )
                try:
                    db.rollback()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Подавлено исключение: %r", exc)

        # --- 3) Напоминания о спортпите (SupplementReminder) ------------------ #
        try:
            supplement_reminders = (
                db.query(SupplementReminder)
                .filter(SupplementReminder.enabled == True)  # noqa: E712 — нужно для SQL
                .all()
            )
        except Exception as exc:
            logger.warning("check_notifications: не удалось прочитать SupplementReminder (%s)", exc)
            supplement_reminders = []

        for reminder in supplement_reminders:
            try:
                _process_supplement_reminder(db, reminder, today, now)
            except Exception as exc:
                # Сбой по одному напоминанию не должен прерывать остальные.
                rid = getattr(reminder, "id", None)
                logger.warning(
                    "check_notifications: сбой напоминания о спортпите id=%s: %s", rid, exc
                )
                try:
                    db.rollback()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Подавлено исключение: %r", exc)

        # --- 4.5) Жизненный цикл подписки: напоминания об окончании / win-back -- #
        try:
            _process_subscription_lifecycle(db, now, today)
        except Exception as exc:
            logger.warning("check_notifications: сбой жизненного цикла подписки: %s", exc)
            try:
                db.rollback()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Подавлено исключение: %r", exc)

        # --- 5) Авто-пересчёт адаптивных калорий раз в неделю (Этап 3) -------- #
        # Изолированно: для пользователей с adaptive_enabled, у кого пересчёт не
        # делался или старше 7 дней. Дедуп обеспечивается обновлением
        # adaptive_last_calc внутри run_adaptive_recalc. Весь блок в try/except,
        # чтобы новая логика не сорвала существующую рассылку.
        try:
            adaptive_users = (
                db.query(User)
                .filter(User.adaptive_enabled == True)  # noqa: E712 — нужно для SQL
                .all()
            )
        except Exception as exc:
            logger.warning(
                "check_notifications: не удалось прочитать adaptive-пользователей (%s)", exc
            )
            adaptive_users = []

        for user in adaptive_users:
            try:
                _process_adaptive_recalc(db, user, today)
            except Exception as exc:
                # Сбой по одному пользователю не должен прерывать остальные.
                tid = getattr(user, "telegram_id", None)
                logger.warning(
                    "check_notifications: сбой адаптивного пересчёта tid=%s: %s", tid, exc
                )
                try:
                    db.rollback()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Подавлено исключение: %r", exc)

    except Exception as exc:
        # Любой неожиданный сбой — логируем, приложение не роняем.
        logger.warning("check_notifications: общий сбой проверки уведомлений: %s", exc)
    finally:
        try:
            db.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)


def _process_subscription_lifecycle(db, now: datetime, today: str) -> None:
    """Напоминания о подписке: за 3 дня до конца, в день конца и win-back через 7 дней.

    Шлём только срочным платным тарифам monthly/quarterly/yearly
    (lifetime/free/trial/владельца — нет), и не ночью (после 12:00). Дедуп — по
    kind + дате. Продлить пользователь может в приложении (на экране подписки).
    """
    # Не будим ночью — шлём только во второй половине дня.
    if not _time_reached(now, "12:00"):
        return

    try:
        users = (
            db.query(User)
            .filter(
                User.subscription_until.isnot(None),
                # Набор типов перечислен явно, а не «всё, кроме free»: у триала
                # свой тип "trial", и напоминать «продлите подписку» тому, кто
                # ничего не покупал, нельзя. Добавляя срочный тариф в
                # config.TARIFFS, добавь его и сюда — иначе его подписчики
                # молча останутся без предупреждения об окончании.
                User.subscription_type.in_(("monthly", "quarterly", "yearly")),
            )
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("subscription lifecycle: не удалось прочитать пользователей: %s", exc)
        return

    for u in users:
        try:
            if getattr(u, "is_owner", False):
                continue
            tid = getattr(u, "telegram_id", None)
            until = getattr(u, "subscription_until", None)
            if tid is None or until is None:
                continue

            days_left = (until.date() - now.date()).days
            lang = _norm_lang(getattr(u, "language", None))
            date_str = until.strftime("%d.%m")

            kind = text = None
            if days_left == 3:
                kind = "sub_exp_3"
                text = (
                    f"⏳ Your Fitness Up subscription ends in 3 days ({date_str}). "
                    "Renew in the app to keep premium access."
                    if lang == "en" else
                    f"⏳ Подписка Fitness Up заканчивается через 3 дня ({date_str}). "
                    "Продлите в приложении, чтобы не потерять доступ."
                )
            elif days_left == 0:
                kind = "sub_exp_0"
                text = (
                    "⚠️ Your Fitness Up subscription ends today. "
                    "Renew in the app to keep premium."
                    if lang == "en" else
                    "⚠️ Подписка Fitness Up заканчивается сегодня. "
                    "Продлите в приложении, чтобы сохранить премиум."
                )
            elif days_left == -7:
                kind = "sub_winback"
                text = (
                    "We miss you! Your Fitness Up premium ended a week ago. "
                    "Come back and keep tracking — resubscribe in the app."
                    if lang == "en" else
                    "Скучаем! 😔 Премиум Fitness Up закончился неделю назад. "
                    "Возвращайтесь — оформить снова можно прямо в приложении."
                )

            if kind and text and not _was_sent(db, tid, kind, today):
                if send_telegram(tid, text):
                    _mark_sent(db, tid, kind, today)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "subscription lifecycle: сбой tid=%s: %s", getattr(u, "telegram_id", None), exc
            )
            try:
                db.rollback()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Подавлено исключение: %r", exc)


# --------------------------------------------------------------------------- #
#  Запуск / остановка планировщика
# --------------------------------------------------------------------------- #
# Ключ advisory-lock планировщика: произвольная константа, одна на приложение.
_SCHEDULER_LOCK_KEY = 811_407_231
_scheduler_lock_conn = None


def _acquire_scheduler_lock() -> bool:
    """Занять замок планировщика в PostgreSQL.

    Если приложение запущено в двух экземплярах (масштабирование Railway),
    без замка каждый прислал бы свои уведомления — люди получали бы всё
    дважды. Advisory-lock живёт, пока живо соединение: держим его на всём
    сроке процесса, при падении процесса база освобождает замок сама.
    На SQLite экземпляр один — замок не нужен.
    """
    global _scheduler_lock_conn
    try:
        from backend.database import engine

        if engine.dialect.name != "postgresql":
            return True
        conn = engine.raw_connection()
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_SCHEDULER_LOCK_KEY,))
        got = bool(cur.fetchone()[0])
        cur.close()
        # Транзакцию закрываем: замок сессионный и commit переживает,
        # а «idle in transaction» мешал бы обслуживанию базы.
        conn.commit()
        if got:
            _scheduler_lock_conn = conn
            return True
        conn.close()
        logger.info("Планировщик уведомлений уже работает в другом экземпляре — здесь не запускаем")
        return False
    except Exception as exc:  # noqa: BLE001 — сбой проверки не должен глушить уведомления
        logger.warning("Замок планировщика не проверен (%s) — запускаем без него", exc)
        return True


def start_scheduler():
    """Запустить фоновый планировщик проверки уведомлений.

    Возвращает объект планировщика или None, если запуск невозможен/не нужен:
      * ENABLE_SCHEDULER == "0" — планировщик принудительно отключён;
      * не задан BOT_TOKEN — отправлять уведомления некуда;
      * не установлен APScheduler.

    Никогда не бросает исключение наружу: при любом сбое возвращает None,
    чтобы запуск API не зависел от планировщика.
    """
    try:
        if os.getenv("ENABLE_SCHEDULER") == "0":
            logger.info("Планировщик уведомлений отключён (ENABLE_SCHEDULER=0)")
            return None
        if not BOT_TOKEN:
            logger.info("Планировщик уведомлений не запущен: не задан BOT_TOKEN")
            return None
        if BackgroundScheduler is None:
            logger.warning("Планировщик уведомлений не запущен: APScheduler не установлен")
            return None
        if not _acquire_scheduler_lock():
            return None

        # Планировщик в часовом поясе приложения (если доступен).
        try:
            scheduler = BackgroundScheduler(timezone=APP_TZ) if APP_TZ is not None else BackgroundScheduler()
        except Exception:
            # На случай несовместимости tz с APScheduler — без явного tz.
            scheduler = BackgroundScheduler()

        # Проверяем условия раз в 60 секунд. Все ошибки внутри job уже
        # перехвачены в check_notifications, так что job не «упадёт».
        scheduler.add_job(
            check_notifications,
            trigger="interval",
            seconds=60,
            id="check_notifications",
            replace_existing=True,
            # Если предыдущий запуск задержался — не накапливаем пропущенные.
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        logger.info("Планировщик уведомлений запущен (интервал 60 с, TZ=%s)", APP_TZ_NAME)
        return scheduler
    except Exception as exc:
        logger.warning("Не удалось запустить планировщик уведомлений: %s", exc)
        return None


def stop_scheduler(sched) -> None:
    """Остановить планировщик (если он был запущен). Ошибки игнорируем."""
    if sched is None:
        return
    try:
        sched.shutdown(wait=False)
        logger.info("Планировщик уведомлений остановлен")
    except Exception as exc:
        logger.warning("Ошибка остановки планировщика уведомлений: %s", exc)
