"""Интеграция с ЮKassa (приём оплаты подписки картой в рублях), API v3.

ЗАЧЕМ: подписка продаётся за рубли, и деньги принимает один из провайдеров —
CloudPayments или ЮKassa. Начисление доступа в обоих случаях идёт через единый
слой payment_providers.activate_premium, поэтому бизнес-логика не дублируется.

КАК ЭТО РАБОТАЕТ:
  1. Фронт просит POST /payment/yookassa/create с именем тарифа.
  2. Мы создаём платёж (POST /v3/payments) и отдаём confirmation_url.
  3. Пользователь платит в браузере, ЮKassa шлёт нам уведомление
     POST /payment/yookassa/webhook (событие payment.succeeded).
  4. Мы ПЕРЕЗАПРАШИВАЕМ платёж по id через API и только после этого решаем,
     выдавать ли доступ.

БЕЗОПАСНОСТЬ — почему сделано именно так:

  * ВЕБХУКИ ЮKASSA НЕ ПОДПИСАНЫ. У уведомления нет ни HMAC, ни иного секрета в
    теле — подлинность подтверждается лишь тем, что запрос пришёл с IP-адресов
    ЮKassa (список меняется, за прокси-серверами проверять его ненадёжно).
    Поэтому тело уведомления мы считаем ПОДСКАЗКОЙ («посмотри платёж N»), а не
    фактом. Единственный источник истины — повторный запрос платежа через API
    по его id: он идёт по HTTPS с нашим секретным ключом, подделать его нельзя.
    Из уведомления берём ТОЛЬКО идентификатор платежа.

  * СУММУ СВЕРЯЕМ НА СЕРВЕРЕ. Сумма и тариф платежа приходят из ответа API,
    но сравниваем их с прайсом из config.rub_price_for: если оплачено меньше
    цены тарифа (или в другой валюте) — доступ НЕ выдаём и алертим владельца.
    Без этой сверки платёж на 1 ₽ с metadata.tariff="lifetime" открыл бы
    вечную подписку.

  * ТЕСТОВЫЕ ПЛАТЕЖИ В ПРОДЕ НЕ АКТИВИРУЮТ ДОСТУП. У платежа есть флаг
    test=true (тестовый магазин/тестовая карта). В боевом режиме такой платёж
    доступ не открывает, иначе премиум выдавался бы тестовой картой.

  * ИДЕМПОТЕНТНОСТЬ. ЮKassa повторяет уведомление, пока не получит HTTP 200
    (до суток). Повтор не должен продлевать подписку второй раз, поэтому
    каждый платёж пишется в Payment.charge_id = build_charge_id(<id>)
    ("yk:<id>"), и перед активацией мы проверяем, нет ли уже такой записи.
    КЛЮЧ ДЕДУПА СТРОИТСЯ ИЗ id, КОТОРЫЙ ВЕРНУЛ API, а не из тела уведомления:
    иначе строки "<id>", "x/../<id>", "<id>?x=1" вели бы к одному платежу,
    но давали разные ключи — и одна оплата продлевала бы подписку сколько
    угодно раз. По той же причине id из уведомления проверяется по строгому
    шаблону (is_valid_payment_id) ДО любого сетевого вызова и экранируется
    при подстановке в путь запроса.
    При создании платежа передаём заголовок Idempotence-Key, одинаковый для
    повторных нажатий в течение пяти минут: повторный клик по кнопке вернёт
    тот же платёж, а не создаст новый в кабинете.

  * АДРЕС ВЕБХУКА — С СЕКРЕТОМ. Раз подписи нет, неугадываемый адрес
    (config.YOOKASSA_WEBHOOK_SECRET в пути) плюс лимит по IP не дают чужим
    запросам гонять наш сервер в API ЮKassa с боевыми ключами.

  * СЕКРЕТ НЕ ПОПАДАЕТ В ЛОГИ: наружу отдаются только тексты ошибок без ключа.

ЧТО ЗДЕСЬ НЕТ: чеков по 54-ФЗ (объект receipt). Их добавляют, когда подключена
онлайн-касса; сейчас модуль — рабочая заготовка приёма оплаты.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from urllib.parse import quote

# httpx — HTTP-клиент (уже используется в проекте). Импортируем мягко, чтобы
# отсутствие зависимости не ломало импорт всего приложения.
try:
    import httpx
except Exception:  # pragma: no cover — на случай отсутствия httpx
    httpx = None

from backend import config

logger = logging.getLogger("yookassa")

# Базовый адрес API ЮKassa (версия 3).
API_BASE = "https://api.yookassa.ru/v3"

# Таймаут запросов к API. Больше десяти секунд ждать нельзя: create вызывается
# из пользовательского запроса, а вебхук ЮKassa считает доставку неуспешной.
TIMEOUT = 15.0

# Формат идентификатора платежа ЮKassa (UUID-подобная строка). Всё, что не
# подходит под шаблон, — не наш платёж и в сеть не уходит.
PAYMENT_ID_RE = re.compile(r"[A-Za-z0-9_-]{6,64}")

# Окно, в котором повторные нажатия «Оплатить» получают один Idempotence-Key.
IDEMPOTENCE_WINDOW_SEC = 300

# Названия тарифов для описания платежа здесь НЕ храним: единый словарь —
# config.TARIFF_TITLES_RU рядом с TARIFFS. CloudPayments берёт названия оттуда
# же, и новый тариф («3 месяца») достаточно назвать в одном месте.


def is_enabled() -> bool:
    """Настроен ли приём оплаты через ЮKassa (заданы shop_id и секретный ключ)."""
    return config.yookassa_enabled()


def _auth() -> tuple:
    """Basic-auth для API: (shop_id, secret_key)."""
    return (config.YOOKASSA_SHOP_ID, config.YOOKASSA_SECRET_KEY)


def _format_amount(value) -> str:
    """Сумма в формате ЮKassa: строка с ДВУМЯ дробными знаками ("499.00")."""
    return f"{float(value):.2f}"


def is_valid_payment_id(payment_id) -> bool:
    """Похож ли идентификатор на id платежа ЮKassa (только буквы, цифры, - и _)."""
    return isinstance(payment_id, str) and PAYMENT_ID_RE.fullmatch(payment_id) is not None


def idempotence_key(telegram_id: int, tariff: str, now: float | None = None) -> str:
    """Ключ идемпотентности для создания платежа.

    Детерминирован на окно IDEMPOTENCE_WINDOW_SEC: повторный клик по кнопке
    в течение пяти минут отдаёт ЮKassa тот же ключ, и она возвращает уже
    созданный платёж вместо нового.
    """
    stamp = int((now if now is not None else time.time()) // IDEMPOTENCE_WINDOW_SEC)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"calories:{int(telegram_id)}:{tariff}:{stamp}"))


def build_charge_id(payment_id: str) -> str:
    """Идентификатор списания для журнала платежей: "yk:<id платежа>".

    Префикс отделяет платежи ЮKassa от CloudPayments ("cp:") и Telegram —
    по этому полю работает защита от повторной активации.
    """
    return "yk:" + str(payment_id)


def build_description(tariff: str, telegram_id: int) -> str:
    """Описание платежа (видно плательщику и в кабинете ЮKassa).

    Пример: «Fitness Up — подписка на 3 месяца (123456789)». Название продукта
    и тарифа по-русски — это описание сверяет модератор ЮKassa с витриной;
    telegram_id в скобках нужен поддержке, чтобы по выписке найти плательщика.
    Ограничение API — 128 символов, поэтому строку жёстко подрезаем.
    """
    text = f"{config.payment_description(tariff)} ({int(telegram_id)})"
    return text[:128]


def create_payment(tariff: str, telegram_id: int, lang: str = "ru") -> dict:
    """Создать платёж в ЮKassa и вернуть {"id", "confirmation_url"}.

    Сумма берётся из прайса на СЕРВЕРЕ (config.rub_price_for) — клиент её не
    передаёт и повлиять на неё не может. Параметр lang — язык плательщика;
    описание платежа пока всегда по-русски (магазин российский), параметр
    оставлен для будущей локализации. В metadata кладём telegram_id и тариф:
    именно оттуда вебхук потом узнает, кому и что активировать (тело самого
    уведомления источником истины не является — см. шапку модуля).

    Любая проблема (нет ключей, нет цены, сбой сети, ошибка API) -> RuntimeError
    с понятным текстом; секретный ключ в сообщение и логи не попадает.
    """
    if not is_enabled():
        raise RuntimeError("ЮKassa не настроена")
    if httpx is None:
        raise RuntimeError("httpx не установлен — платёж создать нельзя")

    amount = config.rub_price_for(tariff)
    if amount is None:
        raise RuntimeError(f"У тарифа {tariff!r} не задана рублёвая цена")

    payload = {
        "amount": {"value": _format_amount(amount), "currency": "RUB"},
        # capture=True — одностадийный платёж: деньги списываются сразу,
        # отдельного подтверждения не требуется.
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": config.YOOKASSA_RETURN_URL,
        },
        "description": build_description(tariff, telegram_id),
        # Метаданные возвращаются в платеже при запросе по API — по ним и
        # активируем доступ.
        "metadata": {
            "telegram_id": str(int(telegram_id)),
            "tariff": tariff,
            # Цена на момент создания: вебхук сверяет оплату именно с ней,
            # чтобы смена прайса между созданием и оплатой не «съела» платёж.
            "price": _format_amount(amount),
        },
    }

    headers = {
        # Ключ идемпотентности: повторные нажатия в пятиминутном окне отдают
        # один ключ, и ЮKassa возвращает уже созданный платёж.
        "Idempotence-Key": idempotence_key(telegram_id, tariff),
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(
            f"{API_BASE}/payments",
            json=payload,
            headers=headers,
            auth=_auth(),
            timeout=TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 — сетевой сбой
        logger.warning("create_payment: сбой связи с ЮKassa: %s", exc)
        raise RuntimeError("Платёжный сервис недоступен")

    if resp.status_code not in (200, 201):
        # Тело ошибки логируем усечённым: там нет секрета, но и раздувать логи
        # незачем. Наружу текст провайдера не отдаём.
        logger.warning(
            "create_payment: ЮKassa ответила %s: %s",
            resp.status_code, (getattr(resp, "text", "") or "")[:300],
        )
        raise RuntimeError("Платёжный сервис отклонил запрос")

    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_payment: не разобрали ответ ЮKassa: %s", exc)
        raise RuntimeError("Платёжный сервис вернул неожиданный ответ")

    if not isinstance(data, dict):
        raise RuntimeError("Платёжный сервис вернул неожиданный ответ")

    payment_id = data.get("id")
    confirmation = data.get("confirmation") or {}
    url = confirmation.get("confirmation_url") if isinstance(confirmation, dict) else None

    if not payment_id or not url:
        logger.warning(
            "create_payment: в ответе нет id/confirmation_url (tariff=%s tid=%s)",
            tariff, telegram_id,
        )
        raise RuntimeError("Платёжный сервис не вернул ссылку на оплату")

    logger.info(
        "create_payment: создан платёж %s (tariff=%s tid=%s)",
        payment_id, tariff, telegram_id,
    )
    return {"id": str(payment_id), "confirmation_url": str(url)}


def fetch_payment(payment_id: str) -> dict:
    """Запросить платёж по id (GET /v3/payments/{id}) — ИСТОЧНИК ИСТИНЫ.

    Вебхуки ЮKassa не подписаны, поэтому статус, сумму и metadata берём только
    отсюда. Любая проблема связи/ответа -> RuntimeError: вызывающий код ответит
    ЮKassa не-200, и она повторит уведомление.
    """
    if not is_enabled():
        raise RuntimeError("ЮKassa не настроена")
    if httpx is None:
        raise RuntimeError("httpx не установлен — платёж проверить нельзя")
    if not is_valid_payment_id(payment_id):
        # Сюда попадать не должны (id проверяется раньше), но в сеть с чужой
        # строкой в пути не идём ни при каких условиях.
        raise ValueError("Некорректный идентификатор платежа")

    try:
        resp = httpx.get(
            f"{API_BASE}/payments/{quote(payment_id, safe='')}",
            auth=_auth(),
            timeout=TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_payment: сбой связи с ЮKassa (%s): %s", payment_id, exc)
        raise RuntimeError("Не удалось проверить платёж")

    if resp.status_code != 200:
        logger.warning(
            "fetch_payment: ЮKassa ответила %s по платежу %s", resp.status_code, payment_id
        )
        raise RuntimeError("Не удалось проверить платёж")

    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_payment: не разобрали ответ по платежу %s: %s", payment_id, exc)
        raise RuntimeError("Не удалось проверить платёж")

    if not isinstance(data, dict):
        raise RuntimeError("Не удалось проверить платёж")
    return data


def resolve_notification(body) -> tuple:
    """Разобрать тело уведомления в (событие, id платежа).

    Формат: {"event": "payment.succeeded", "object": {"id": "...", ...}}.
    Любой мусор (не словарь, нет полей, чужие типы) -> (None, None): такое
    уведомление мы просто подтверждаем и ничего не делаем.
    """
    if not isinstance(body, dict):
        return None, None

    event = body.get("event")
    event = event.strip() if isinstance(event, str) else None

    obj = body.get("object")
    payment_id = None
    if isinstance(obj, dict):
        raw_id = obj.get("id")
        if isinstance(raw_id, (str, int)) and str(raw_id).strip():
            candidate = str(raw_id).strip()
            # Только строки формата id ЮKassa: "../me" или "<id>?x=1" — мусор,
            # который не должен ни уходить в сеть, ни попадать в ключ дедупа.
            if is_valid_payment_id(candidate):
                payment_id = candidate

    return event, payment_id


def is_success(payment) -> bool:
    """Платёж действительно оплачен? (статус succeeded И признак paid).

    Проверяем оба поля: статус описывает стадию платежа, paid — факт списания
    денег. Доступ выдаём, только когда сходится и то, и другое.
    """
    if not isinstance(payment, dict):
        return False
    if payment.get("status") != "succeeded" or payment.get("paid") is not True:
        return False
    # Полностью/частично возвращённый платёж остаётся succeeded+paid, но
    # деньги у нас уже не все — доступ по нему не открываем.
    refunded = payment.get("refunded_amount")
    if isinstance(refunded, dict):
        try:
            if float(refunded.get("value") or 0) > 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


def canonical_id(payment) -> str | None:
    """Идентификатор платежа из ответа API (источник истины для дедупа)."""
    if not isinstance(payment, dict):
        return None
    raw = payment.get("id")
    return raw if is_valid_payment_id(raw) else None


def amount_matches_tariff(tariff: str, amount_obj, expected_price=None) -> bool:
    """Покрывает ли оплаченная сумма цену тарифа (валюта строго RUB).

    Сверка обязательна: metadata платежа задаём мы, но полагаться на неё без
    проверки суммы нельзя — иначе платёж на 1 ₽ закрыл бы дорогой тариф.
    expected_price — цена, зафиксированная в metadata при создании платежа
    (чтобы смена прайса между созданием и оплатой не отбрасывала честную
    оплату); если её нет — берём текущий прайс. Переплата засчитывается,
    недоплата (с допуском в копейку) — нет.
    """
    expected = None
    try:
        if expected_price not in (None, ""):
            expected = float(expected_price)
    except (TypeError, ValueError):
        expected = None
    if not expected or expected <= 0:
        expected = config.rub_price_for(tariff)
    if not expected:
        return False
    if not isinstance(amount_obj, dict):
        return False
    if str(amount_obj.get("currency") or "").strip().upper() != "RUB":
        return False
    try:
        paid = float(amount_obj.get("value"))
    except (TypeError, ValueError):
        return False
    return paid >= float(expected) - 0.01


def is_test(payment) -> bool:
    """Тестовый платёж? (в проде такие доступ не открывают)."""
    if not isinstance(payment, dict):
        return False
    return bool(payment.get("test"))


def payment_metadata(payment) -> tuple:
    """Достать (telegram_id, tariff) из metadata платежа.

    Берём ТОЛЬКО из ответа API (не из тела уведомления). Битые/отсутствующие
    значения -> (None, None): такой платёж требует ручного разбора.
    """
    if not isinstance(payment, dict):
        return None, None
    meta = payment.get("metadata")
    if not isinstance(meta, dict):
        return None, None

    telegram_id = None
    raw_tid = meta.get("telegram_id")
    if raw_tid not in (None, ""):
        try:
            telegram_id = int(str(raw_tid).strip())
        except (TypeError, ValueError):
            telegram_id = None

    tariff = meta.get("tariff")
    tariff = tariff.strip() if isinstance(tariff, str) and tariff.strip() else None

    return telegram_id, tariff


def metadata_price(payment):
    """Цена, зафиксированная в metadata при создании платежа (или None)."""
    if not isinstance(payment, dict):
        return None
    meta = payment.get("metadata")
    if not isinstance(meta, dict):
        return None
    raw = meta.get("price")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
