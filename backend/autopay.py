"""
Автопродление подписки: сохранённая карта, списание продления, отвязка.

КАК ЭТО УСТРОЕНО
  1. На странице оплаты — отдельная галочка «Продлевать автоматически»
     (по умолчанию выключена, отдельно от принятия оферты). Отмечена —
     платёж создаётся с save_payment_method, ЮKassa сохраняет карту.
  2. Успешный платёж с сохранённой картой → строка в payment_methods: маска
     карты, тариф и сумма, на которые человек согласился, дата и редакция
     документов (доказательство согласия).
  3. Раз в полчаса run(): за сутки до списания — предупреждение в боте;
     за AUTOPAY_CHARGE_BEFORE_HOURS до конца подписки — списание. Продление
     прибавляется к текущему сроку, поэтому ранний платёж дней не съедает.
  4. Отказ банка: окончательный (карта истекла, разрешение отозвано…) —
     карта отвязывается, человеку сообщение; временный (нет денег) — повтор
     через сутки, не больше AUTOPAY_MAX_ATTEMPTS раз за период.
  5. «Отвязать карту» в профиле — карта сразу неактивна, списаний больше
     нет (376-ФЗ: отказ принимается в электронной форме и останавливает
     списания).

ЗАЩИТА ОТ ДВОЙНОГО СПИСАНИЯ
  Строка журнала autopay_charges пишется и коммитится ДО запроса в ЮKassa,
  вместе с ключом идемпотентности. Если процесс упадёт между запросом и
  записью ответа, следующий проход повторит запрос с ТЕМ ЖЕ ключом — ЮKassa
  вернёт уже созданный платёж, а не создаст второй. Начисление доступа —
  идемпотентно по charge_id платежа (тот же, что у вебхука).

Всё выключено, пока не задан YOOKASSA_AUTOPAY=1 (автоплатежи магазину
подключает ЮKassa).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from backend import config, yookassa
from backend.models import AutopayCharge, Payment, PaymentMethod, User

logger = logging.getLogger("autopay")

_CARD_NAMES = {"Mir": "МИР", "MasterCard": "Mastercard", "Visa": "Visa"}
_TARIFF_RU = {"monthly": "месяц", "quarterly": "3 месяца", "yearly": "год", "test": "тест"}
_TARIFF_EN = {"monthly": "month", "quarterly": "3 months", "yearly": "year", "test": "test"}


def enabled() -> bool:
    return bool(config.YOOKASSA_AUTOPAY and yookassa.is_enabled())


def _lang(user) -> str:
    return "en" if str(getattr(user, "language", "") or "").lower().startswith("en") else "ru"


def _t(lang, ru, en):
    return en if lang == "en" else ru


def _send(tid: int, text: str) -> None:
    from backend import telegram_bot

    telegram_bot._bot_api("sendMessage", {"chat_id": tid, "text": text})


def _money(v) -> str:
    v = float(v or 0)
    return str(int(v)) if v == int(v) else f"{v:.2f}".replace(".", ",")


def card_label(m: PaymentMethod) -> str:
    """«МИР •••• 4444» / «СБП» — как показать способ оплаты человеку."""
    if m.last4:
        return f"{_CARD_NAMES.get(m.card_type or '', m.card_type or 'Карта')} •••• {m.last4}"
    return m.title or (m.method_type or "Способ оплаты")


def _date_ru(d: datetime) -> str:
    months = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря")
    return f"{d.day} {months[d.month - 1]}"


def _date(d: datetime, lang: str) -> str:
    return d.strftime("%b %d") if lang == "en" else _date_ru(d)


# --------------------------------------------------------------------------- #
#  Сохранение и отвязка
# --------------------------------------------------------------------------- #
def active_methods(db, tid: int) -> list:
    return (
        db.query(PaymentMethod)
        .filter(PaymentMethod.telegram_id == tid, PaymentMethod.active.is_(True))
        .order_by(PaymentMethod.id.desc())
        .all()
    )


def save_from_payment(db, payment: dict, payment_id: str) -> PaymentMethod | None:
    """Успешный платёж с согласием на автопродление → сохранённая карта.

    Карту сохраняем только если человек согласился (metadata.autopay) и
    ЮKassa действительно её сохранила (payment_method.saved). Прежние
    активные карты человека отвязываются: продлеваем с последней.
    """
    if not yookassa.wants_autopay(payment):
        return None
    info = yookassa.saved_method(payment)
    tid, tariff = yookassa.payment_metadata(payment)
    if not info or not tid or not tariff:
        return None
    now = datetime.utcnow()
    existing = (
        db.query(PaymentMethod)
        .filter(PaymentMethod.telegram_id == tid, PaymentMethod.method_id == info["method_id"])
        .first()
    )
    for old in active_methods(db, tid):
        if existing is None or old.id != existing.id:
            old.active = False
            old.revoked_at = now
            old.revoke_reason = "replaced"
    row = existing or PaymentMethod(telegram_id=tid, provider="yookassa", created_at=now)
    if existing is None:
        db.add(row)
    for key, value in info.items():
        setattr(row, key, value)
    row.active = True
    row.revoked_at = None
    row.revoke_reason = None
    row.autopay_tariff = tariff
    row.autopay_amount = yookassa.metadata_price(payment) or config.rub_price_for(tariff)
    row.consented_at = now
    row.consent_version = config.LEGAL_VERSION
    row.source_payment_id = payment_id
    row.updated_at = now
    db.commit()
    logger.info("autopay: сохранена карта %s для tid=%s (тариф %s)", card_label(row), tid, tariff)
    return row


def revoke(db, tid: int, method_pk: int | None = None, reason: str = "user") -> int:
    """Отвязать карту (все или одну). Возвращает число отвязанных."""
    q = db.query(PaymentMethod).filter(PaymentMethod.telegram_id == tid, PaymentMethod.active.is_(True))
    if method_pk is not None:
        q = q.filter(PaymentMethod.id == method_pk)
    rows = q.all()
    now = datetime.utcnow()
    for row in rows:
        row.active = False
        row.revoked_at = now
        row.revoke_reason = reason
        row.updated_at = now
    if rows:
        db.commit()
        logger.info("autopay: отвязано %d способ(ов) оплаты tid=%s (%s)", len(rows), tid, reason)
    return len(rows)


def next_charge_at(user: User):
    until = getattr(user, "subscription_until", None)
    if until is None or getattr(user, "subscription_type", None) not in config.AUTOPAY_TARIFFS:
        return None
    return until - timedelta(hours=config.AUTOPAY_CHARGE_BEFORE_HOURS)


def methods_out(db, user: User) -> list:
    """Карты человека для профиля и экрана подписки."""
    out = []
    nxt = next_charge_at(user)
    for m in active_methods(db, user.telegram_id):
        renews = m.autopay_tariff in config.AUTOPAY_TARIFFS
        out.append({
            "id": m.id,
            "title": card_label(m),
            "card_type": m.card_type,
            "last4": m.last4,
            "expiry": f"{m.expiry_month}/{str(m.expiry_year)[-2:]}" if m.expiry_month and m.expiry_year else None,
            "autopay_tariff": m.autopay_tariff,
            "autopay_amount": m.autopay_amount,
            "next_charge_at": nxt.isoformat() if (nxt and renews) else None,
            "consented_at": m.consented_at.isoformat() if m.consented_at else None,
        })
    return out


# --------------------------------------------------------------------------- #
#  Результат списания (из прохода планировщика и из вебхука)
# --------------------------------------------------------------------------- #
def _find_row(db, payment: dict) -> AutopayCharge | None:
    pid = yookassa.canonical_id(payment)
    if pid:
        row = db.query(AutopayCharge).filter(AutopayCharge.payment_id == pid).first()
        if row is not None:
            return row
    meta = payment.get("metadata") if isinstance(payment.get("metadata"), dict) else {}
    try:
        tid = int(meta.get("telegram_id"))
    except (TypeError, ValueError):
        return None
    return (
        db.query(AutopayCharge)
        .filter(AutopayCharge.telegram_id == tid, AutopayCharge.period_key == meta.get("period_key"),
                AutopayCharge.payment_id.is_(None))
        .order_by(AutopayCharge.id.desc())
        .first()
    )


def apply_result(db, payment: dict) -> str:
    """Применить исход списания продления. Идемпотентно: повтор ничего не меняет.

    Возвращает succeeded | canceled | pending | unknown.
    """
    from backend import payment_providers

    row = _find_row(db, payment)
    if row is None:
        logger.warning("autopay: результат платежа %s без записи в журнале", yookassa.canonical_id(payment))
        return "unknown"
    pid = yookassa.canonical_id(payment)
    if pid and not row.payment_id:
        row.payment_id = pid
    status = payment.get("status")
    user = db.query(User).filter(User.telegram_id == row.telegram_id).first()
    lang = _lang(user)
    method = db.get(PaymentMethod, row.method_id) if row.method_id else None
    label = card_label(method) if method else ""

    if yookassa.is_success(payment):
        if row.status == "succeeded":
            return "succeeded"
        amount_obj = payment.get("amount") if isinstance(payment.get("amount"), dict) else {}
        try:
            paid = float(amount_obj.get("value"))
        except (TypeError, ValueError):
            paid = None
        if paid is None or abs(paid - float(row.amount or 0)) > 0.009 or amount_obj.get("currency") != "RUB":
            logger.error("autopay: сумма платежа %s (%s) не совпала с журналом (%s)", pid, paid, row.amount)
            row.status = "canceled"
            row.reason = "amount_mismatch"
            db.commit()
            return "canceled"
        charge_id = yookassa.build_charge_id(pid)
        if db.query(Payment).filter(Payment.charge_id == charge_id).first() is None:
            if row.tariff == config.TEST_TARIFF:
                db.add(Payment(telegram_id=row.telegram_id, provider="yookassa", amount=paid,
                               currency="RUB", subscription_type=config.TEST_TARIFF, charge_id=charge_id))
                db.commit()
            else:
                payment_providers.activate_premium(db, row.telegram_id, row.tariff, "yookassa",
                                                   paid, "RUB", charge_id=charge_id)
        row.status = "succeeded"
        row.updated_at = datetime.utcnow()
        db.commit()
        from backend import analytics

        analytics.track(row.telegram_id, "autopay_success")
        if user is not None:
            db.refresh(user)
            until = user.subscription_until
            if row.tariff == config.TEST_TARIFF:
                text = _t(lang, f"✅ Тестовое автосписание прошло: {_money(paid)} ₽ с карты {label}.",
                          f"✅ Test auto-charge succeeded: {_money(paid)} RUB from {label}.")
            else:
                text = _t(
                    lang,
                    f"✅ Подписка продлена{(' до ' + _date(until, lang)) if until else ''}: "
                    f"списано {_money(paid)} ₽ с карты {label}.",
                    f"✅ Subscription renewed{(' until ' + _date(until, lang)) if until else ''}: "
                    f"{_money(paid)} RUB charged to {label}.",
                )
            _send(row.telegram_id, text)
        return "succeeded"

    if status == "canceled":
        if row.status == "canceled":
            return "canceled"
        reason = yookassa.cancellation_reason(payment) or "unknown"
        row.status = "canceled"
        row.reason = reason
        row.updated_at = datetime.utcnow()
        db.commit()
        from backend import analytics

        analytics.track(row.telegram_id, "autopay_fail")
        if reason in yookassa.PERMANENT_DECLINES:
            revoke(db, row.telegram_id, row.method_id, reason=reason)
            text = _t(
                lang,
                f"Не получилось продлить подписку: банк отклонил списание с карты {label}. "
                "Карта отвязана — продлить можно вручную в приложении.",
                f"Couldn't renew your subscription: the bank declined {label}. "
                "The card has been unlinked — you can renew manually in the app.",
            )
        elif row.attempt >= config.AUTOPAY_MAX_ATTEMPTS:
            text = _t(
                lang,
                "Автопродление не прошло: списать не получилось. Подписка закончится в срок — "
                "продлить можно вручную в приложении.",
                "Auto-renewal failed: we couldn't charge your card. Your subscription will end "
                "on schedule — you can renew manually in the app.",
            )
        else:
            text = _t(
                lang,
                f"Не получилось списать оплату продления с карты {label}. Попробуем ещё раз завтра.",
                f"Couldn't charge the renewal to {label}. We'll try again tomorrow.",
            )
        if row.tariff != config.TEST_TARIFF or reason in yookassa.PERMANENT_DECLINES:
            _send(row.telegram_id, text)
        else:
            _send(row.telegram_id, _t(lang, f"Тестовое автосписание отклонено: {reason}.",
                                      f"Test auto-charge declined: {reason}."))
        return "canceled"
    return "pending"


# --------------------------------------------------------------------------- #
#  Списание
# --------------------------------------------------------------------------- #
def charge(db, user: User, method: PaymentMethod, tariff: str, amount: float, period_key: str) -> str:
    """Одна попытка списания за период. Журнал — до запроса (см. шапку модуля)."""
    tid = user.telegram_id
    rows = (
        db.query(AutopayCharge)
        .filter(AutopayCharge.telegram_id == tid, AutopayCharge.period_key == period_key)
        .order_by(AutopayCharge.attempt.asc())
        .all()
    )
    now = datetime.utcnow()
    if any(r.status == "succeeded" for r in rows):
        return "already"
    pending = next((r for r in rows if r.status == "pending"), None)
    if pending is not None:
        if pending.payment_id:
            # Ответ ещё не окончательный — спросим ЮKassa, чем закончилось.
            try:
                return apply_result(db, yookassa.fetch_payment(pending.payment_id))
            except Exception as exc:  # noqa: BLE001
                logger.warning("autopay: не узнали статус %s: %s", pending.payment_id, exc)
                return "pending"
        row = pending
        if now - (row.created_at or now) > timedelta(hours=23):
            # Ключ идемпотентности ЮKassa живёт сутки: старый запрос без ответа
            # считаем неудачной попыткой, дальше — новая с новым ключом.
            row.status = "error"
            row.reason = row.reason or "no_response"
            db.commit()
            return "error"
    else:
        canceled = [r for r in rows if r.status in ("canceled", "error")]
        if len(canceled) >= config.AUTOPAY_MAX_ATTEMPTS:
            return "exhausted"
        if canceled and now - (canceled[-1].updated_at or canceled[-1].created_at or now) < timedelta(hours=23):
            return "wait"
        attempt = len(rows) + 1
        row = AutopayCharge(
            telegram_id=tid, method_id=method.id, period_key=period_key, attempt=attempt,
            idempotence_key=f"autopay:{tid}:{period_key}:{attempt}",
            status="pending", amount=amount, tariff=tariff, created_at=now, updated_at=now,
        )
        db.add(row)
        db.commit()

    # 376-ФЗ: отказ останавливает списания. Перечитываем карту прямо перед
    # запросом — отвязка за минуту до прохода должна победить.
    db.refresh(method)
    if not method.active:
        row.status = "canceled"
        row.reason = "revoked_by_user"
        db.commit()
        return "revoked"

    try:
        payment = yookassa.charge_saved(method.method_id, tid, tariff, amount, period_key,
                                        row.idempotence_key, email=getattr(user, "email", None))
    except RuntimeError as exc:
        row.reason = str(exc)[:200]
        row.updated_at = datetime.utcnow()
        db.commit()
        logger.warning("autopay: списание tid=%s не отправлено: %s", tid, exc)
        return "error"
    row.payment_id = yookassa.canonical_id(payment) or str(payment.get("id"))
    row.updated_at = datetime.utcnow()
    db.commit()
    return apply_result(db, payment)


def run(db, now: datetime | None = None) -> dict:
    """Проход планировщика: предупреждения и списания продлений."""
    stats = {"noticed": 0, "charged": 0}
    if not enabled():
        return stats
    from backend import notifications

    now = now or datetime.utcnow()
    methods = (
        db.query(PaymentMethod)
        .filter(PaymentMethod.active.is_(True), PaymentMethod.autopay_tariff.in_(config.AUTOPAY_TARIFFS))
        .all()
    )
    for method in methods:
        user = db.query(User).filter(User.telegram_id == method.telegram_id).first()
        if user is None or not user.subscription_until:
            continue
        if user.subscription_type not in config.AUTOPAY_TARIFFS:
            continue  # вечная, пробная, бесплатная — не продлеваем
        if config.OWNER_ID and user.telegram_id == config.OWNER_ID:
            continue
        until = user.subscription_until
        if now > until + timedelta(days=3):
            continue  # давно закончилась — без человека не возобновляем
        period_key = until.date().isoformat()
        charge_at = until - timedelta(hours=config.AUTOPAY_CHARGE_BEFORE_HOURS)
        notice_at = charge_at - timedelta(hours=config.AUTOPAY_NOTICE_BEFORE_HOURS)
        lang = _lang(user)
        amount = float(method.autopay_amount or config.rub_price_for(method.autopay_tariff) or 0)
        if amount <= 0:
            continue

        if now >= notice_at and now < charge_at and not notifications._was_sent(
            db, user.telegram_id, "autopay_notice", period_key
        ):
            tariff_name = (_TARIFF_EN if lang == "en" else _TARIFF_RU).get(method.autopay_tariff, "")
            _send(user.telegram_id, _t(
                lang,
                f"Напоминание: {_date(charge_at, lang)} продлим подписку на {tariff_name} — спишем "
                f"{_money(amount)} ₽ с карты {card_label(method)}. Отключить автопродление: "
                "«Профиль» → «Оплата» → «Отвязать карту».",
                f"Reminder: on {_date(charge_at, lang)} we'll renew your subscription for {tariff_name} — "
                f"{_money(amount)} RUB from {card_label(method)}. To turn off auto-renewal: "
                "Profile → Payment → Unlink card.",
            ))
            notifications._mark_sent(db, user.telegram_id, "autopay_notice", period_key)
            stats["noticed"] += 1

        if now >= charge_at:
            if config.YOOKASSA_RECEIPT and not (user.email or "").strip():
                # Чек обязателен, а адреса для него нет — не списываем молча.
                from backend import telegram_bot

                telegram_bot.alert_owner(
                    f"автопродление tid={user.telegram_id} пропущено: нет e-mail для чека", prefix="Оплата"
                )
                continue
            res = charge(db, user, method, method.autopay_tariff, amount, period_key)
            if res in ("succeeded", "canceled", "pending"):
                stats["charged"] += 1
    return stats


def run_job() -> None:
    """Задача планировщика (своя сессия, сбой не роняет планировщик)."""
    if not enabled():
        return
    from backend.database import SessionLocal

    try:
        with SessionLocal() as db:
            stats = run(db)
        if stats["noticed"] or stats["charged"]:
            logger.info("autopay: предупреждений %s, списаний %s", stats["noticed"], stats["charged"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("autopay.run_job: %s", exc)


def test_charge(db, user: User) -> str:
    """/autopaytest: списать тестовую сумму с карты владельца тем же путём."""
    methods = active_methods(db, user.telegram_id)
    if not methods:
        return "no_card"
    amount = config.TEST_PAYMENT_RUB or 1
    period_key = "test-" + datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return charge(db, user, methods[0], config.TEST_TARIFF, float(amount), period_key)
