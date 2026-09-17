"""
Ядро системы доступа (подписки) Telegram Mini App Fitness Up.

Здесь сосредоточена ВСЯ логика проверки прав доступа на бэкенде. Фронтенд
не может её обойти: премиум-эндпоинты защищены FastAPI-зависимостью
``require_premium``, а лимит бесплатных сканирований проверяется на сервере
до и после распознавания еды.

Состав модуля:
  - is_premium(user)              — есть ли у пользователя активный доступ;
  - require_premium(...)          — FastAPI-зависимость (402 при отсутствии);
  - assert_scan_available(db,u)   — проверка лимита бесплатных сканирований;
  - record_scan(db, user)         — учёт одного использованного сканирования;
  - scans_info(db, user)          — сводка по лимиту сканирований для фронта.

ВАЖНО про безопасность: владелец определяется СТРОГО по telegram_id
(флаг is_owner, который проставляется в auth по сравнению с config.OWNER_ID),
а НЕ по username. Здесь мы доверяем уже выставленному флагу is_owner.
"""

import logging
from datetime import datetime

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from backend import config
from backend.auth import get_current_user
from backend.database import get_db
from backend.models import User

logger = logging.getLogger(__name__)


def is_premium(user: User) -> bool:
    """
    Определить, есть ли у пользователя активный премиум-доступ.

    Доступ считается активным, если выполнено ХОТЯ БЫ одно условие:
      - пользователь — владелец (is_owner == True);
      - тип подписки — пожизненный ("lifetime");
      - задана дата окончания подписки и она ещё не наступила
        (subscription_until > текущего момента UTC).

    Используем getattr с дефолтами, чтобы функция не падала, даже если
    у объекта по какой-то причине ещё нет новых полей (например, в тестах
    или при частичных данных).
    """
    if user is None:
        return False

    # Владелец — всегда премиум (флаг выставляется по telegram_id в auth).
    if getattr(user, "is_owner", False):
        return True

    # Пожизненная подписка — премиум без срока окончания.
    if getattr(user, "subscription_type", None) == "lifetime":
        return True

    # Срочная подписка: активна, пока не истекла дата окончания.
    until = getattr(user, "subscription_until", None)
    if until is not None and until > datetime.utcnow():
        return True

    return False


def require_premium(user: User = Depends(get_current_user)) -> User:
    """
    FastAPI-зависимость: пропустить запрос только при наличии премиума.

    Если у пользователя нет активного доступа — отвечаем HTTP 402
    (Payment Required) с машиночитаемой деталью, по которой фронтенд
    показывает экран оформления подписки. Иначе возвращаем пользователя
    дальше в обработчик эндпоинта.
    """
    if not is_premium(user):
        raise HTTPException(
            status_code=402,
            detail={"error": "premium_required", "message": "Нужна подписка"},
        )
    return user


def _today_str() -> str:
    """Текущая дата (UTC) в формате ISO 'YYYY-MM-DD' — ключ суточного лимита."""
    return datetime.utcnow().date().isoformat()


def _sync_scan_day(db: Session, user: User) -> None:
    """
    Синхронизировать счётчик сканирований с текущими сутками.

    Если сохранённая дата сканирований не совпадает с сегодняшней (UTC),
    обнуляем счётчик и проставляем новую дату — то есть лимит бесплатных
    сканирований сбрасывается каждый день. Изменения коммитим.

    Вспомогательная функция; на ошибке БД логируем и откатываем, чтобы
    не оставить сессию в нерабочем состоянии.
    """
    today = _today_str()
    if getattr(user, "daily_scans_date", None) != today:
        try:
            user.daily_scans_used = 0
            user.daily_scans_date = today
            db.commit()
            db.refresh(user)
        except Exception as exc:  # noqa: BLE001 — лимит не должен валить запрос
            logger.warning("Не удалось сбросить счётчик сканирований: %s", exc)
            db.rollback()


def assert_scan_available(db: Session, user: User) -> int:
    """
    Проверить, доступно ли пользователю ещё одно сканирование еды.

    Логика:
      - премиум (или владелец) — безлимит, возвращаем -1;
      - иначе сначала синхронизируем счётчик с текущими сутками (сброс по дате);
      - если использовано >= лимита (config.FREE_SCAN_LIMIT) — бросаем HTTP 402
        с деталью "scan_limit", фронт покажет предложение оформить подписку;
      - иначе возвращаем число ОСТАВШИХСЯ бесплатных сканирований на сегодня.

    Вызывается ПЕРЕД распознаванием еды.
    """
    # Премиум — без ограничений.
    if is_premium(user):
        return -1

    # Сбрасываем счётчик, если наступил новый день.
    _sync_scan_day(db, user)

    used = getattr(user, "daily_scans_used", 0) or 0
    limit = config.FREE_SCAN_LIMIT

    if used >= limit:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "scan_limit",
                "message": "Лимит бесплатных сканирований на сегодня исчерпан",
            },
        )

    # Сколько бесплатных сканирований ещё осталось на сегодня.
    return limit - used


def record_scan(db: Session, user: User) -> None:
    """
    Учесть одно использованное сканирование еды.

    Для премиум-пользователей ничего не делаем (у них безлимит). Для
    бесплатных — синхронизируем дату (на случай смены суток между проверкой
    и учётом), увеличиваем счётчик и коммитим.

    Вызывается ПОСЛЕ успешного распознавания еды.
    """
    if is_premium(user):
        return

    # На случай, если сутки сменились между assert и record.
    _sync_scan_day(db, user)

    try:
        user.daily_scans_used = (getattr(user, "daily_scans_used", 0) or 0) + 1
        db.commit()
        db.refresh(user)
    except Exception as exc:  # noqa: BLE001 — учёт не должен валить ответ
        logger.warning("Не удалось записать использованное сканирование: %s", exc)
        db.rollback()


def scans_info(db: Session, user: User) -> dict:
    """
    Сводка по лимиту сканирований для фронтенда.

    Возвращает словарь:
      {"used": int, "limit": int, "remaining": int, "is_premium": bool}

    Для премиум-пользователей remaining = -1 (безлимит), used = 0.
    Для бесплатных учитываем сброс по дате (если наступил новый день —
    счётчик уже считается обнулённым).
    """
    premium = is_premium(user)
    limit = config.FREE_SCAN_LIMIT

    if premium:
        # Премиум: лимит не действует, отдаём безлимит-маркер.
        return {"used": 0, "limit": limit, "remaining": -1, "is_premium": True}

    # Бесплатный пользователь: синхронизируем дату (возможен сброс счётчика).
    _sync_scan_day(db, user)

    used = getattr(user, "daily_scans_used", 0) or 0
    remaining = max(0, limit - used)

    return {
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "is_premium": False,
    }
