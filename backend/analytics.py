"""
Продуктовая аналитика: сколько людей пользуется приложением и где они
отваливаются на пути к оплате.

Без внешних сервисов — события пишутся в свою таблицу app_events, отчёт
владелец получает командой /stats в боте. Храним минимум: кто (telegram_id —
иначе не посчитать уникальных людей), что (имя события из белого списка) и
когда. Никакого содержимого: ни еды, ни текстов, ни сумм.

Записи старше EVENTS_RETENTION_DAYS удаляются раз в сутки планировщиком;
при удалении аккаунта события человека уходят вместе с остальными данными.

Запись события — best-effort: собственная сессия БД и глухой try/except,
чтобы сбой аналитики никогда не ломал сам запрос пользователя.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta

from sqlalchemy import func

from backend.database import SessionLocal
from backend.models import AppEvent, Payment, User

logger = logging.getLogger("analytics")

EVENTS_RETENTION_DAYS = int(os.getenv("EVENTS_RETENTION_DAYS", "180"))

# События, которые пишет сам сервер.
SERVER_EVENTS = {
    "app_open",           # приложение открыто (проверка подписи при запуске)
    "scan_photo",         # еда распознана по фото
    "scan_voice",         # еда распознана по голосу
    "food_text",          # КБЖУ посчитаны по тексту
    "trainer_program",    # тренер собрал программу
    "workout_done",       # тренировка завершена
    "payment_create",     # создан платёж (человек нажал «Оплатить»)
    "payment_success",    # платёж прошёл, доступ выдан
}
# События, которые присылает клиент (просмотры экранов воронки).
CLIENT_EVENTS = {
    "screen_subscription",  # открыт экран подписки
    "screen_payment",       # открыта страница оплаты
    "paywall",              # показан пейволл платной функции
}
ALL_EVENTS = SERVER_EVENTS | CLIENT_EVENTS


def track(telegram_id: int | None, name: str) -> None:
    """Записать событие. Никогда не бросает исключений."""
    if not telegram_id or name not in ALL_EVENTS:
        return
    try:
        with SessionLocal() as db:
            db.add(AppEvent(telegram_id=int(telegram_id), name=name, day=date.today().isoformat()))
            db.commit()
    except Exception as exc:  # noqa: BLE001 — аналитика не должна ломать запрос
        logger.debug("analytics.track(%s): %r", name, exc)


def purge_old(db) -> int:
    """Удалить события старше срока хранения. Возвращает число удалённых строк."""
    border = (date.today() - timedelta(days=EVENTS_RETENTION_DAYS)).isoformat()
    n = db.query(AppEvent).filter(AppEvent.day < border).delete(synchronize_session=False)
    db.commit()
    return n


def _uniq(db, name: str, since: str) -> int:
    """Сколько разных людей совершили событие с даты since (включительно)."""
    return (
        db.query(func.count(func.distinct(AppEvent.telegram_id)))
        .filter(AppEvent.name == name, AppEvent.day >= since)
        .scalar() or 0
    )


def _count(db, name: str, since: str) -> int:
    return db.query(func.count(AppEvent.id)).filter(AppEvent.name == name, AppEvent.day >= since).scalar() or 0


def _pct(part: int, whole: int) -> str:
    return f"{round(part * 100 / whole)}%" if whole else "—"


def report(db, days: int = 7) -> str:
    """Текстовый отчёт владельцу: аудитория, использование, воронка оплаты."""
    today = date.today()
    since = (today - timedelta(days=days - 1)).isoformat()
    t = today.isoformat()

    users_total = db.query(func.count(User.telegram_id)).scalar() or 0
    new_users = (
        db.query(func.count(User.telegram_id))
        .filter(User.created_at >= datetime.combine(today - timedelta(days=days - 1), datetime.min.time()))
        .scalar() or 0
    )
    dau = _uniq(db, "app_open", t)
    wau = _uniq(db, "app_open", since)

    sub = _uniq(db, "screen_subscription", since)
    pay_view = _uniq(db, "screen_payment", since)
    pay_create = _uniq(db, "payment_create", since)
    paid = _uniq(db, "payment_success", since)
    paywall = _uniq(db, "paywall", since)

    revenue = (
        db.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(
            Payment.created_at >= datetime.combine(today - timedelta(days=days - 1), datetime.min.time()),
            Payment.currency == "RUB",
            Payment.provider.in_(("yookassa", "cloudpayments")),
            Payment.subscription_type != "test",
        )
        .scalar() or 0
    )

    try:
        from backend import ratelimit

        ai_today = ratelimit.global_hits_today()
    except Exception:  # noqa: BLE001
        ai_today = None

    lines = [
        f"Fitness Up — отчёт за {days} дн. (с {since})",
        "",
        "Аудитория",
        f"• всего пользователей: {users_total}, новых: {new_users}",
        f"• открывали сегодня: {dau}, за период: {wau}",
        "",
        "Использование (событий / людей)",
        f"• фото еды: {_count(db, 'scan_photo', since)} / {_uniq(db, 'scan_photo', since)}",
        f"• голосом: {_count(db, 'scan_voice', since)} / {_uniq(db, 'scan_voice', since)}",
        f"• текстом: {_count(db, 'food_text', since)} / {_uniq(db, 'food_text', since)}",
        f"• программ тренера: {_count(db, 'trainer_program', since)}",
        f"• тренировок завершено: {_count(db, 'workout_done', since)} / {_uniq(db, 'workout_done', since)}",
        "",
        "Путь к оплате (люди)",
        f"• видели пейволл: {paywall}",
        f"• открыли подписку: {sub}",
        f"• открыли оплату: {pay_view} ({_pct(pay_view, sub)} от подписки)",
        f"• нажали «Оплатить»: {pay_create} ({_pct(pay_create, pay_view)} от оплаты)",
        f"• оплатили: {paid} ({_pct(paid, pay_create)} от нажавших)",
        f"• выручка: {round(float(revenue))} ₽",
    ]
    if ai_today is not None:
        lines += ["", f"ИИ-вызовов сегодня (с последнего перезапуска): {ai_today}"]
    return "\n".join(lines)
