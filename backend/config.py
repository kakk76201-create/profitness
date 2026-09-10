"""
Конфигурация подписки и доступа (Этап 1).

Все значения читаются из переменных окружения (os.getenv). Цены НЕ хардкодим —
тарифы и лимиты задаются через env, чтобы их можно было менять без правки кода.

Модуль НЕ имеет зависимостей от других модулей backend (database/models/auth),
поэтому его безопасно импортировать откуда угодно — циклов импорта не возникает.
"""

import os


# Telegram ID владельца приложения. Доступ владельца определяется СТРОГО по id,
# никогда по username (username можно сменить/подделать). 0 — владелец не задан.
OWNER_ID: int = int(os.getenv("OWNER_ID", "0") or 0)

# Сколько бесплатных сканирований еды в сутки доступно free-пользователю.
FREE_SCAN_LIMIT: int = int(os.getenv("FREE_SCAN_LIMIT", "3"))

# Длительность одноразового бесплатного пробного периода (дней). 0 — триал выключен.
TRIAL_DAYS: int = int(os.getenv("TRIAL_DAYS", "7"))

# Секрет вебхука Telegram (заголовок X-Telegram-Bot-Api-Secret-Token).
# Если пусто — проверка секрета пропускается (dev-режим).
TELEGRAM_WEBHOOK_SECRET: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

# Секрет вебхука платёжного провайдера Tribute.
# Если пусто — проверка секрета пропускается (dev-режим).
TRIBUTE_WEBHOOK_SECRET: str = os.getenv("TRIBUTE_WEBHOOK_SECRET", "")

# Username бота (без "@") — для формирования ссылок/счётов.
BOT_USERNAME: str = os.getenv("BOT_USERNAME", "")

# Публичный https-URL мини-приложения (для кнопки «Открыть приложение» в /start).
# Если задан — к приветствию прикрепляется inline-кнопка web_app. Пример:
# https://f1tnesspro-production.up.railway.app
MINI_APP_URL: str = os.getenv("MINI_APP_URL", "")

# Ссылка на оплату через Tribute (создаётся в Tribute и кладётся в env).
# Если пусто — кнопка «Оплатить через Tribute» на фронте просто не показывается.
TRIBUTE_URL: str = os.getenv("TRIBUTE_URL", "")

# Каталог для приватных фото прогресса (Этап 7). Файлы НЕ раздаются статикой —
# только через авторизованный эндпоинт с проверкой владельца. По умолчанию —
# локальная папка в корне проекта (на Railway диск эфемерный; для постоянного
# хранения позже можно подключить том/R2, поменяв только этот путь).
PROGRESS_PHOTOS_DIR: str = os.getenv("PROGRESS_PHOTOS_DIR", "./progress_photos")

# Максимальный размер загружаемого фото прогресса (12 МБ).
PROGRESS_PHOTO_MAX_BYTES: int = int(os.getenv("PROGRESS_PHOTO_MAX_BYTES", str(12 * 1024 * 1024)))


# Тарифы подписки. Здесь — ТОЛЬКО сроки; цена у подписки одна и в рублях
# (см. RUB_PRICES ниже). Оплата Telegram Stars отключена, поэтому поля со
# стоимостью в звёздах у тарифов больше нет.
#   days — на сколько дней продлевается подписка (None = пожизненно).
TARIFFS: dict = {
    "monthly": {
        "days": int(os.getenv("SUBSCRIPTION_MONTHLY_DAYS", "30")),
    },
    "yearly": {
        "days": int(os.getenv("SUBSCRIPTION_YEARLY_DAYS", "365")),
    },
    "lifetime": {
        "days": None,  # None — пожизненная подписка (без срока окончания).
    },
}


def tariff_for(name):
    """
    Вернуть описание тарифа по его имени ("monthly" | "yearly" | "lifetime")
    или None, если тариф с таким именем не задан.
    """
    return TARIFFS.get(name)


# --------------------------------------------------------------------------- #
#  Оплата подписки картой в рублях (единственный способ оплаты)
#
#  ВАЖНО: оплата Telegram Stars из приложения убрана полностью. Подписка
#  продаётся за РУБЛИ, а деньги принимает один из провайдеров:
#    * CloudPayments — переменные CLOUDPAYMENTS_* ниже;
#    * ЮKassa        — переменные YOOKASSA_* ниже (подключается после модерации).
#
#  Пока ключи не заданы, приём карт выключен (card_provider() == "none"):
#  маршруты провайдера отвечают 503, но рублёвая ВИТРИНА (цена и кнопка) всё
#  равно показывается — её требует модерация платёжного сервиса, которую
#  проходят ДО получения ключей.
# --------------------------------------------------------------------------- #

# Public ID сайта из личного кабинета CloudPayments (можно светить на фронте).
CLOUDPAYMENTS_PUBLIC_ID = os.getenv("CLOUDPAYMENTS_PUBLIC_ID", "").strip()

# API Secret («пароль для API») — СЕКРЕТ. Им же проверяется подпись вебхуков.
CLOUDPAYMENTS_API_SECRET = os.getenv("CLOUDPAYMENTS_API_SECRET", "").strip()

# Валюта списания в виджете CloudPayments. МЕНЯТЬ НЕ НУЖНО: подписка продаётся
# только за рубли (PRICE_*_RUB), статус подписки отдаёт card_currency="RUB", и
# вебхук засчитывает платёж только в этой валюте — другое значение разойдётся
# с ценой на витрине.
CLOUDPAYMENTS_CURRENCY = os.getenv("CLOUDPAYMENTS_CURRENCY", "RUB").strip() or "RUB"

# Цены тарифов в РУБЛЯХ. Показываются на экране подписки и НЕ зависят от того,
# какая платёжная система подключена: витрина с рублёвой ценой нужна в том числе
# для модерации в платёжном сервисе (ЮKassa и т.п.) — её проходят ДО выдачи
# ключей. Значения перекрываются переменными PRICE_*_RUB без правки кода.
RUB_PRICES: dict = {
    "monthly": float(os.getenv("PRICE_MONTHLY_RUB", "699") or 0),
    "yearly": float(os.getenv("PRICE_YEARLY_RUB", "5590") or 0),
    "lifetime": float(os.getenv("PRICE_LIFETIME_RUB", "0") or 0),  # 0 — вечный тариф не продаётся (только ручная выдача владельцем)
}

# Какая платёжная система обрабатывает оплату картой:
#   "auto"          — определить по заданным ключам (по умолчанию);
#   "cloudpayments" — виджет CloudPayments;
#   "yookassa"      — ЮKassa (подключается после модерации);
#   "none"          — приём карт ещё не подключён: цена и кнопка показываются,
#                     но при нажатии честно сообщаем, что оплата картой скоро.
PAYMENT_PROVIDER = (os.getenv("PAYMENT_PROVIDER", "auto").strip().lower() or "auto")


# --- ЮKassa (API v3) ------------------------------------------------------- #
# Идентификатор магазина и секретный ключ из личного кабинета ЮKassa. Пока оба
# не заданы, интеграция выключена: /payment/yookassa/create отвечает 503.
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "").strip()
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "").strip()

# Секрет в АДРЕСЕ вебхука: уведомления ЮKassa не подписаны, поэтому адрес
# делаем неугадываемым — в кабинете ЮKassa указывается
#   https://<домен>/payment/yookassa/webhook/<YOOKASSA_WEBHOOK_SECRET>
# Любая случайная строка (32+ символов). В проде без секрета вебхук
# уведомления НЕ обрабатывает (иначе кто угодно мог бы гонять наш сервер
# в API ЮKassa с боевыми ключами).
YOOKASSA_WEBHOOK_SECRET = os.getenv("YOOKASSA_WEBHOOK_SECRET", "").strip()

# Куда ЮKassa вернёт пользователя из браузера после оплаты. По умолчанию —
# адрес мини-приложения, иначе чат бота (лишь бы человек вернулся в Telegram).
YOOKASSA_RETURN_URL = (
    os.getenv("YOOKASSA_RETURN_URL", "").strip()
    or MINI_APP_URL
    or (f"https://t.me/{BOT_USERNAME}" if BOT_USERNAME else "https://t.me")
)


# --- Реквизиты продавца (нужны для модерации в платёжном сервисе) ---------- #
# Показываются на странице оплаты: кто продаёт, ИНН, контакт поддержки и
# ссылки на оферту и политику конфиденциальности. Пустые -> None (не показываем).
LEGAL_SELLER = os.getenv("LEGAL_SELLER", "").strip()
LEGAL_INN = os.getenv("LEGAL_INN", "").strip()
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "").strip()
OFFER_URL = os.getenv("OFFER_URL", "").strip()
PRIVACY_URL = os.getenv("PRIVACY_URL", "").strip()


def cloudpayments_enabled() -> bool:
    """Настроен ли приём оплаты картой через CloudPayments."""
    return bool(CLOUDPAYMENTS_PUBLIC_ID and CLOUDPAYMENTS_API_SECRET)


def yookassa_enabled() -> bool:
    """Настроен ли приём оплаты картой через ЮKassa (заданы оба ключа)."""
    return bool(YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY)


def card_provider() -> str:
    """Активный провайдер оплаты картой: "cloudpayments" | "yookassa" | "none".

    Явное значение PAYMENT_PROVIDER имеет приоритет (им можно принудительно
    выключить приём карт, оставив витрину). В режиме "auto" провайдер
    определяется по наличию ключей: сначала ЮKassa, затем CloudPayments.
    Пока ключей нет — "none", но витрина с рублёвой ценой всё равно работает.
    """
    if PAYMENT_PROVIDER in ("cloudpayments", "yookassa", "none"):
        return PAYMENT_PROVIDER
    if yookassa_enabled():
        return "yookassa"
    if cloudpayments_enabled():
        return "cloudpayments"
    return "none"


def rub_price_for(tariff: str):
    """Цена тарифа в рублях или None, если рублёвая цена не задана."""
    price = RUB_PRICES.get(tariff)
    return price if price and price > 0 else None


def tariff_catalog() -> dict:
    """Каталог тарифов для фронта: срок + рублёвая цена.

    Формат (контракт с фронтом):
        {"monthly": {"days": 30, "price": 699.0, "currency": "RUB"}, ...}

    В каталог попадают ТОЛЬКО тарифы с заданной рублёвой ценой: продавать
    тариф, у которого цены нет, нечем (PRICE_*_RUB=0 убирает его с витрины).
    """
    catalog = {}
    for name, cfg in TARIFFS.items():
        price = rub_price_for(name)
        if price is None:
            continue
        catalog[name] = {
            "days": cfg.get("days"),
            "price": float(price),
            "currency": "RUB",
        }
    return catalog


def legal_info() -> dict:
    """Реквизиты продавца для страницы оплаты (пустые значения -> None).

    Их наличие проверяет модерация платёжного сервиса: покупатель должен
    видеть, кому платит, и иметь ссылки на оферту и политику.
    """
    return {
        "seller": LEGAL_SELLER or None,
        "inn": LEGAL_INN or None,
        "contact": SUPPORT_CONTACT or None,
        "offer_url": OFFER_URL or None,
        "privacy_url": PRIVACY_URL or None,
    }


# --------------------------------------------------------------------------- #
#  Витрина «Купить» (маркетплейс) для рекомендаций по спортпиту
#
#  Ссылка собирается ТОЛЬКО на бэкенде: партнёрские параметры не должны быть
#  зашиты во фронт, чтобы их можно было поменять одной переменной окружения.
#
#  Пока партнёрская программа не подключена, MARKET_CLID пуст — тогда отдаётся
#  ОБЫЧНАЯ поисковая ссылка без партнёрского хвоста (она работает сразу).
#  Когда получишь clid площадки в кабинете Яндекс.Дистрибуции — просто задай
#  MARKET_CLID (и при необходимости MARKET_VID / MARKET_ERID), код не меняется.
# --------------------------------------------------------------------------- #

# Базовый адрес поиска маркетплейса. Вынесен в env на случай смены площадки.
MARKET_SEARCH_URL = os.getenv("MARKET_SEARCH_URL", "https://market.yandex.ru/search")

# Идентификатор партнёра (выдаётся на КАЖДУЮ площадку отдельно после модерации).
MARKET_CLID = os.getenv("MARKET_CLID", "").strip()

# Произвольная метка партнёра для сегментации статистики (латиница и цифры).
MARKET_VID = os.getenv("MARKET_VID", "").strip()

# Рекламный токен маркировки (ЕРИР/ОРД). Требуется по ФЗ-38, когда по ссылке
# идёт вознаграждение. Без партнёрки не нужен.
MARKET_ERID = os.getenv("MARKET_ERID", "").strip()

# Фиксированная часть партнёрской ссылки Яндекс.Маркета — одинакова у всех
# партнёров (из официальной справки Яндекс.Дистрибуции).
_MARKET_AFFILIATE_TAIL = {"pp": "900", "mclid": "1003", "distr_type": "7"}


def market_search_url(query: str) -> str | None:
    """Собрать ссылку «Купить» на маркетплейс по названию товара.

    Без MARKET_CLID возвращается обычная поисковая ссылка (работает сразу).
    С заданным MARKET_CLID добавляется партнёрский хвост.
    Пустой запрос -> None (кнопку показывать не на что).
    """
    from urllib.parse import urlencode

    q = (query or "").strip()
    if not q:
        return None

    params = {"text": q}
    if MARKET_CLID:
        params.update(_MARKET_AFFILIATE_TAIL)
        params["clid"] = MARKET_CLID
        if MARKET_VID:
            params["vid"] = MARKET_VID
        if MARKET_ERID:
            params["erid"] = MARKET_ERID

    return f"{MARKET_SEARCH_URL}?{urlencode(params)}"


def market_is_affiliate() -> bool:
    """True, если ссылки партнёрские (тогда по ФЗ-38 нужна пометка «Реклама»)."""
    return bool(MARKET_CLID)
