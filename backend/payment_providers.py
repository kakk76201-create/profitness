"""
Единый слой активации подписки (расширяемый под разных провайдеров).

Любой провайдер оплаты (CloudPayments, ЮKassa, Tribute, ручная выдача
владельцем и т.д.) в итоге вызывает один и тот же ``activate_premium``. Это держит
логику начисления доступа в одном месте: тип подписки, продление срока,
запись платежа. Так новый провайдер не дублирует бизнес-логику, а лишь
парсит свой формат и передаёт сюда нормализованные данные.

Состав:
  - activate_premium(...) — найти/создать пользователя и выдать/продлить доступ;
  - revoke_premium(...)   — снять доступ (вернуть на бесплатный тариф).

Обе функции максимально устойчивы: вся работа в try/except с логированием,
чтобы сбой начисления (например, гонка в БД) не ронял обработчик webhook
или апдейт бота.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend import config
from backend.models import Payment, User

logger = logging.getLogger(__name__)


def activate_premium(
    db: Session,
    telegram_id: int,
    tariff: str,
    provider: str,
    amount,
    currency: str,
    charge_id: str | None = None,
) -> User:
    """
    Активировать (или продлить) премиум-подписку пользователя.

    Параметры:
      - db          — сессия БД;
      - telegram_id — Telegram ID пользователя (если записи нет — создаём
                      минимального пользователя, чтобы не потерять оплату);
      - tariff      — имя тарифа ("monthly" | "quarterly" | "yearly" | "lifetime"), из config;
      - provider    — источник оплаты ("cloudpayments" | "yookassa" |
                      "tribute" | "trial" | "owner" и т.п.);
      - amount      — сумма платежа (как пришла от провайдера; может быть None/0);
      - currency    — валюта платежа ("RUB" для оплаты картой и т.д.).

    Логика срока:
      - если у тарифа days is None ("lifetime") — ставим subscription_type
        "lifetime", subscription_until = None (бессрочно);
      - иначе subscription_type = имя тарифа; продлеваем от максимума между
        «сейчас» и текущей датой окончания (чтобы покупки СКЛАДЫВАЛИСЬ, а не
        перезаписывали остаток), на tariff["days"] дней.

    В любом случае пишем запись Payment для аудита, коммитим и возвращаем
    обновлённого пользователя. Вся операция обёрнута в try/except: при ошибке
    откатываем транзакцию, логируем и пробрасываем исключение наверх, чтобы
    вызывающий код (webhook/бот) сам решил, как ответить.
    """
    try:
        now = datetime.utcnow()

        # 1) Находим пользователя; если его нет — создаём минимальную запись,
        #    чтобы оплата не потерялась (профиль он заполнит при входе в приложение).
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if user is None:
            user = User(telegram_id=telegram_id)
            db.add(user)

        # 2) Параметры тарифа из конфигурации (цены/сроки — из env, не хардкод).
        tariff_cfg = config.tariff_for(tariff)
        if tariff_cfg is None:
            # Неизвестный тариф — не угадываем, явно сообщаем об ошибке.
            raise ValueError(f"Неизвестный тариф: {tariff!r}")

        days = tariff_cfg.get("days")

        # 3) Выставляем тип подписки и дату окончания.
        if days is None:
            # Пожизненная подписка — без даты окончания.
            user.subscription_type = "lifetime"
            user.subscription_until = None
        elif getattr(user, "subscription_type", None) == "lifetime":
            # НИКОГДА не понижаем пожизненный доступ до срочного: у lifetime
            # subscription_until = None, и без этой ветки оплата месяца
            # превратила бы вечную подписку в 30-дневную. Платёж всё равно
            # записываем — деньги получены, факт нужно сохранить.
            logger.warning(
                "activate_premium: у telegram_id=%s уже lifetime — тариф %s не понижаем",
                telegram_id, tariff,
            )
        else:
            # Срочная подписка: продлеваем от большей из дат (now / текущий until),
            # чтобы оплаты складывались, а не «съедали» остаток.
            current_until = getattr(user, "subscription_until", None)
            base = max(now, current_until) if current_until else now
            new_until = base + timedelta(days=days)
            user.subscription_type = tariff
            # Дата окончания не должна двигаться назад ни при каких условиях.
            user.subscription_until = (
                max(new_until, current_until) if current_until else new_until
            )

        # 4) Аудит: запись о платеже (с идентификатором списания для дедупа).
        payment = Payment(
            telegram_id=telegram_id,
            provider=provider,
            amount=amount,
            currency=currency,
            subscription_type=user.subscription_type,
            charge_id=charge_id,
        )
        db.add(payment)

        db.commit()
        db.refresh(user)
        # Настоящие оплаты (не пробный период и не ручная выдача) — в воронку.
        if provider in ("yookassa", "cloudpayments", "tribute"):
            from backend import analytics

            analytics.track(telegram_id, "payment_success")

        logger.info(
            "Активирован премиум: telegram_id=%s tariff=%s provider=%s until=%s",
            telegram_id,
            tariff,
            provider,
            user.subscription_until,
        )
        return user

    except Exception as exc:  # noqa: BLE001 — начисление не должно «молча» падать
        logger.error(
            "Ошибка активации премиума (telegram_id=%s tariff=%s provider=%s): %s",
            telegram_id,
            tariff,
            provider,
            exc,
        )
        # Откатываем незавершённую транзакцию, чтобы сессия осталась рабочей.
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
        # Пробрасываем наверх — вызывающий слой решает, как ответить провайдеру.
        raise


def grant_days(
    db: Session,
    telegram_id: int,
    days: int,
    provider: str,
    subscription_type: str = "monthly",
    amount=None,
    currency: str | None = None,
) -> User:
    """Выдать/продлить доступ на явное число дней (для триала и /givepro N).

    Работает как activate_premium, но с произвольным числом дней и типом
    подписки (не привязано к config.TARIFFS). Продлеваем от max(now, until),
    чтобы дни складывались. Пишем Payment для аудита.
    """
    try:
        now = datetime.utcnow()
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if user is None:
            user = User(telegram_id=telegram_id)
            db.add(user)

        # Пожизненный доступ не понижаем срочным (см. activate_premium).
        if getattr(user, "subscription_type", None) == "lifetime":
            logger.warning(
                "grant_days: у telegram_id=%s уже lifetime — не понижаем до %s",
                telegram_id, subscription_type,
            )
        else:
            current_until = getattr(user, "subscription_until", None)
            base = max(now, current_until) if current_until else now
            new_until = base + timedelta(days=int(days))
            user.subscription_type = subscription_type
            user.subscription_until = (
                max(new_until, current_until) if current_until else new_until
            )

        db.add(Payment(
            telegram_id=telegram_id,
            provider=provider,
            amount=amount,
            currency=currency,
            subscription_type=subscription_type,
        ))
        db.commit()
        db.refresh(user)
        logger.info(
            "Выдан доступ на %s дн.: telegram_id=%s type=%s provider=%s until=%s",
            days, telegram_id, subscription_type, provider, user.subscription_until,
        )
        return user
    except Exception as exc:  # noqa: BLE001
        logger.error("Ошибка grant_days (telegram_id=%s days=%s): %s", telegram_id, days, exc)
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
        raise


def apply_pending_grants(db: Session, telegram_id: int, username: str | None) -> list:
    """Применить отложенные выдачи доступа для появившегося пользователя.

    Вызывается, когда мы впервые узнаём связку username ↔ telegram_id: при
    контакте с ботом (telegram_bot._touch_user) и при входе в мини-приложение
    (auth._upsert_user). Так «/givepro @username», отданная ДО того как человек
    появился в базе, срабатывает автоматически — владельцу не нужно повторять.

    Возвращает список применённых записей PendingGrant (может быть пустым).
    Никогда не пробрасывает исключения: сбой не должен ломать вход пользователя.
    """
    if not username:
        return []

    # Импорт внутри функции: PendingGrant появился позже, а модуль импортируется
    # из мест, где важно не тянуть лишние зависимости на старте.
    from backend.models import PendingGrant

    applied = []
    try:
        rows = (
            db.query(PendingGrant)
            .filter(
                PendingGrant.username_lower == username.strip().lower(),
                PendingGrant.applied_at.is_(None),
            )
            .all()
        )
        if not rows:
            return []

        for row in rows:
            try:
                if row.days and int(row.days) > 0:
                    grant_days(db, telegram_id, int(row.days), "owner", subscription_type="monthly")
                else:
                    activate_premium(db, telegram_id, "lifetime", "owner", 0, "RUB")
                row.applied_at = datetime.utcnow()
                row.applied_to = telegram_id
                db.commit()
                applied.append(row)
                logger.info(
                    "Отложенная выдача применена: @%s -> tid=%s (days=%s)",
                    row.username_lower, telegram_id, row.days,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("apply_pending_grants: сбой применения id=%s: %s", row.id, exc)
                try:
                    db.rollback()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Подавлено исключение: %r", exc)
    except Exception as exc:  # noqa: BLE001
        logger.error("apply_pending_grants: сбой выборки для @%s: %s", username, exc)
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
    return applied


def revoke_premium(db: Session, telegram_id: int) -> User | None:
    """
    Снять премиум-доступ у пользователя (вернуть на бесплатный тариф).

    Сбрасываем subscription_type на "free", обнуляем дату окончания и снимаем
    флаг владельца (is_owner). Используется командой /revokepro от владельца.

    Возвращаем обновлённого пользователя, либо None, если пользователь не
    найден. Ошибки логируем и откатываем, чтобы не уронить вызывающий код.
    """
    try:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if user is None:
            logger.warning(
                "revoke_premium: пользователь telegram_id=%s не найден", telegram_id
            )
            return None

        user.subscription_type = "free"
        user.subscription_until = None
        user.is_owner = False

        db.commit()
        db.refresh(user)

        logger.info("Снят премиум: telegram_id=%s", telegram_id)
        return user

    except Exception as exc:  # noqa: BLE001 — снятие не должно валить вызывающий код
        logger.error(
            "Ошибка снятия премиума (telegram_id=%s): %s", telegram_id, exc
        )
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
        return None
