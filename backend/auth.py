"""
Модуль аутентификации Telegram WebApp.

Здесь реализована проверка подлинности данных, которые Telegram передаёт
mini-приложению (initData), а также FastAPI-зависимость get_current_user,
которая по этим данным находит или создаёт пользователя в базе.

Безопасность Telegram WebApp строится на HMAC-подписи: сервер Telegram
подписывает строку с данными пользователя секретом, производным от токена
бота. Зная BOT_TOKEN, бэкенд может пересчитать подпись и убедиться, что
данные не подделаны клиентом.
"""

import hashlib
import logging
import hmac
import json
import os
import time
from urllib.parse import parse_qsl

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend import config
from backend.database import get_db
from backend.models import User

logger = logging.getLogger(__name__)

# Имя HTTP-заголовка, в котором фронтенд присылает Telegram initData.
# Должно совпадать с тем, что отправляет клиент (см. App.api в js/app.js).
HEADER = "X-Telegram-Init-Data"

# Допустимый «возраст» initData в секундах (24 часа).
# Используется как мягкая проверка свежести — мы не отклоняем запрос
# жёстко, но при желании можно ужесточить логику.
_MAX_AUTH_AGE_SECONDS = 24 * 60 * 60


def validate_init_data(init_data: str, bot_token: str) -> dict | None:
    """
    Проверить подлинность строки Telegram WebApp initData.

    Возвращает словарь с распарсенными данными (включая поле "user" как dict),
    если подпись верна, иначе None.

    Алгоритм проверки строго соответствует официальной документации Telegram:
      1. Разбираем querystring init_data в список пар ключ-значение.
      2. Извлекаем (pop) из пар поле "hash" — это присланная подпись.
      3. Формируем data_check_string: оставшиеся пары сортируем по ключу и
         склеиваем строками вида "key=value", разделяя символом перевода
         строки "\\n".
      4. Вычисляем секретный ключ: HMAC-SHA256 от токена бота с фиксированным
         ключом b"WebAppData".
      5. Вычисляем собственную подпись data_check_string этим секретным ключом.
      6. Сравниваем свою подпись с присланной через hmac.compare_digest
         (защита от timing-атак). При несовпадении — данные подделаны.
      7. Поле "user" приходит как JSON-строка — разбираем его в словарь.
    """
    # Без входных данных или без токена проверять нечего — данные невалидны.
    if not init_data or not bot_token:
        return None

    # Шаг 1. Разбираем строку запроса. keep_blank_values=True важно, чтобы
    # пустые значения тоже попадали в проверку подписи (иначе подпись не сойдётся).
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))

    # Шаг 2. Достаём присланную подпись. Если её нет — проверять нечего.
    received_hash = pairs.pop("hash", None)
    if received_hash is None:
        return None

    # Шаг 3. Собираем строку для проверки: сортируем оставшиеся ключи и
    # склеиваем пары "key=value" через перевод строки.
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))

    # Шаг 4. Секретный ключ = HMAC-SHA256(key=b"WebAppData", msg=bot_token).
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()

    # Шаг 5. Наша подпись строки данных этим секретным ключом.
    calc_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    # Шаг 6. Безопасное сравнение подписей (защита от атак по времени).
    if not hmac.compare_digest(calc_hash, received_hash):
        return None

    # Проверка свежести: отклоняем устаревший или некорректный auth_date, чтобы
    # перехваченный initData нельзя было переиспользовать бесконечно (Telegram
    # выдаёт свежий initData при каждом открытии мини-аппа, так что легитимные
    # клиенты не страдают). Нет/битый/старый auth_date -> данные невалидны.
    auth_date = pairs.get("auth_date")
    try:
        if auth_date is None or (time.time() - int(auth_date)) > _MAX_AUTH_AGE_SECONDS:
            return None
    except (TypeError, ValueError):
        return None

    # Шаг 7. Разбираем поле "user" из JSON-строки в словарь.
    user_raw = pairs.get("user")
    if user_raw:
        try:
            pairs["user"] = json.loads(user_raw)
        except (TypeError, ValueError):
            # Не смогли распарсить пользователя — считаем данные невалидными.
            return None

    return pairs


def _upsert_user(
    db: Session,
    *,
    telegram_id: int,
    username,
    first_name,
    photo_url,
    language_code=None,
) -> User:
    """
    Создать пользователя, если его ещё нет, либо обновить изменяемые поля
    профиля Telegram (username/first_name/photo_url) у существующего.

    Поля weight/height/age/gender/goal не трогаем — это данные, которые
    пользователь задаёт сам в разделе «Мой аккаунт».

    Язык (user.language) инициализируется только при СОЗДАНИИ нового
    пользователя или если он ещё не задан: "ru", если Telegram language_code
    начинается с "ru", иначе "en". Уже заданный язык НЕ перетираем —
    пользователь мог сменить его вручную (через /profile).
    """
    user = db.query(User).filter(User.telegram_id == telegram_id).first()

    if user is None:
        # Новый пользователь — создаём запись. Между SELECT и INSERT возможна
        # гонка (бот и мини-приложение могут обрабатывать человека одновременно):
        # на PostgreSQL это даёт UniqueViolation и 500 у НОВОГО пользователя.
        # При конфликте откатываемся и перечитываем уже созданную запись.
        candidate = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            photo_url=photo_url,
        )
        db.add(candidate)
        changed = True
        try:
            db.flush()
            user = candidate
        except Exception:  # noqa: BLE001
            db.rollback()
            changed = False
            user = db.query(User).filter(User.telegram_id == telegram_id).first()
            if user is None:
                raise  # это не гонка, а настоящая ошибка вставки

    else:
        # Существующий пользователь — освежаем данные из Telegram, но ТОЛЬКО те,
        # что Telegram реально прислал. Безусловное присваивание затирало
        # сохранённый username в NULL (эти поля опциональны в initData), и после
        # этого владелец не мог найти человека командой /givepro @username.
        changed = False
        if username and user.username != username:
            user.username = username
            changed = True
        if first_name and user.first_name != first_name:
            user.first_name = first_name
            changed = True
        if photo_url and user.photo_url != photo_url:
            user.photo_url = photo_url
            changed = True

    # Инициализация языка при создании или если он ещё пуст. Заданный ранее
    # язык не трогаем, чтобы не сбросить ручной выбор пользователя.
    if not user.language:
        user.language = (
            "ru" if str(language_code or "").lower().startswith("ru") else "en"
        )
        changed = True

    # Пишем в БД только когда что-то изменилось: авторизация идёт на КАЖДЫЙ
    # запрос, и безусловный commit превращал каждое открытие экрана в запись
    # в PostgreSQL.
    if changed:
        db.commit()
        db.refresh(user)

    # Инициализация владельца приложения. Владелец определяется СТРОГО по
    # telegram_id == config.OWNER_ID (никогда по username). Один раз помечаем
    # его как is_owner и выдаём пожизненную подписку, чтобы premium-функции были
    # доступны без отдельной оплаты. Делаем это идемпотентно: если флаг уже стоит,
    # ничего не трогаем.
    if config.OWNER_ID and telegram_id == config.OWNER_ID and not user.is_owner:
        user.is_owner = True
        user.subscription_type = "lifetime"
        db.commit()
        db.refresh(user)

    # Отложенные выдачи («/givepro @username» до появления человека в базе)
    # применяем и на этом пути — вход в приложение тоже раскрывает связку
    # username ↔ telegram_id. Сбой здесь не должен ломать авторизацию.
    try:
        from backend import payment_providers

        if payment_providers.apply_pending_grants(db, telegram_id, user.username):
            db.refresh(user)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Подавлено исключение: %r", exc)

    return user


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    """
    FastAPI-зависимость: вернуть текущего пользователя по Telegram initData.

    Логика:
      - Читаем заголовок X-Telegram-Init-Data.
      - Если он есть и подпись валидна — извлекаем пользователя и делаем upsert.
      - Если заголовок отсутствует/невалиден:
          * при ALLOW_INSECURE_AUTH == "1" (ТОЛЬКО для разработки) возвращаем
            фиксированного dev-пользователя (telegram_id=1);
          * иначе отдаём HTTP 401.
    """
    init_data = request.headers.get(HEADER, "")
    bot_token = os.getenv("BOT_TOKEN", "")

    data = validate_init_data(init_data, bot_token) if init_data else None

    if data and isinstance(data.get("user"), dict):
        # Данные подписаны корректно — берём профиль из проверенного словаря.
        tg_user = data["user"]
        telegram_id = tg_user.get("id")
        if telegram_id is None:
            raise HTTPException(status_code=401, detail="Invalid Telegram auth")

        return _upsert_user(
            db,
            telegram_id=int(telegram_id),
            username=tg_user.get("username"),
            first_name=tg_user.get("first_name"),
            photo_url=tg_user.get("photo_url"),
            language_code=tg_user.get("language_code"),
        )

    # Проверка не прошла. Разрешён ли небезопасный режим для разработки?
    if os.getenv("ALLOW_INSECURE_AUTH") == "1":
        # ВНИМАНИЕ: только для локальной разработки! Подменяет реального
        # пользователя фиксированной учётной записью без проверки подписи.
        return _upsert_user(
            db,
            telegram_id=1,
            username="dev",
            first_name="Dev",
            photo_url=None,
        )

    # Боевой режим без валидных данных — доступ запрещён.
    raise HTTPException(status_code=401, detail="Invalid Telegram auth")
