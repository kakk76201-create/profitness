"""Интеграция с CloudPayments (приём оплаты подписки картой).

ЗАЧЕМ: подписка продаётся ТОЛЬКО за рубли (оплата Telegram Stars убрана), а
деньги принимает один из двух провайдеров — CloudPayments (этот модуль) или
ЮKassa (backend/yookassa.py). Какой активен, решает config.card_provider().
Начисление доступа в обоих случаях идёт через единый слой
payment_providers.activate_premium, поэтому бизнес-логика не дублируется.

КАК ЭТО РАБОТАЕТ:
  1. Фронт открывает виджет CloudPayments с publicId, суммой и параметрами:
       accountId = telegram_id плательщика (по нему мы узнаём, кому начислять),
       invoiceId = "{тариф}:{telegram_id}:{метка времени}".
  2. После оплаты CloudPayments шлёт на наш сервер уведомление (вебхук) Pay.
  3. Мы ПРОВЕРЯЕМ ПОДПИСЬ, находим тариф и активируем подписку.

БЕЗОПАСНОСТЬ — ключевые правила, заложенные здесь:
  * доступ начисляется ТОЛЬКО по вебхуку с корректной подписью. Колбэк успеха
    в браузере (onSuccess) подделывается тривиально и НЕ является основанием;
  * подпись = base64(HMAC-SHA256(СЫРОЕ тело запроса, API Secret)); приходит в
    заголовке Content-HMAC (дублируется в X-Content-HMAC). Тело нужно читать
    в исходных байтах ДО разбора формы, иначе подпись не сойдётся;
  * сравнение подписи — только hmac.compare_digest (защита от тайминг-атак);
  * платежи с TestMode=1 в проде НЕ активируют подписку, иначе тестовой картой
    можно выдать себе премиум;
  * повторные доставки одного платежа отсекаются по TransactionId
    (CloudPayments ретраит до 100 раз, пока не получит корректный ответ).

ФОРМАТ ОТВЕТА: CloudPayments считает доставку успешной ТОЛЬКО при HTTP 200 и
теле ровно {"code": 0}. Любой другой ответ запускает череду повторов.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from backend import config

logger = logging.getLogger("cloudpayments")

# Коды ответа на уведомление Check (набор из официального SDK).
CODE_OK = 0                 # платёж принят
CODE_UNKNOWN_INVOICE = 10   # неизвестный номер заказа
CODE_INVALID_ACCOUNT = 11   # неверный идентификатор плательщика
CODE_INVALID_AMOUNT = 12    # неверная сумма
CODE_REJECTED = 13          # платёж отклонён
CODE_EXPIRED = 20           # истёк срок


def is_enabled() -> bool:
    """Настроен ли приём оплаты через CloudPayments."""
    return bool(config.CLOUDPAYMENTS_PUBLIC_ID and config.CLOUDPAYMENTS_API_SECRET)


def verify_signature(raw_body: bytes, header_value: str | None) -> bool:
    """Проверить подпись уведомления CloudPayments.

    Подпись считается от СЫРОГО тела запроса ключом API Secret:
        base64(HMAC-SHA256(raw_body, api_secret))

    Возвращает False, если секрет не задан или подпись не совпала. Сравнение —
    через hmac.compare_digest (постоянное время).
    """
    secret = config.CLOUDPAYMENTS_API_SECRET
    if not secret:
        logger.error("verify_signature: CLOUDPAYMENTS_API_SECRET не задан")
        return False
    if not header_value:
        return False

    digest = hmac.new(
        secret.encode("utf-8"), raw_body or b"", hashlib.sha256
    ).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, header_value.strip())


def build_invoice_id(tariff: str, telegram_id: int, stamp: str) -> str:
    """Собрать номер заказа: "{тариф}:{telegram_id}:{метка времени}"."""
    return f"{tariff}:{int(telegram_id)}:{stamp}"


def parse_invoice_id(invoice_id: str | None) -> tuple[str | None, int | None]:
    """Разобрать номер заказа обратно в (тариф, telegram_id).

    Возвращает (None, None), если формат не наш — тогда полагаемся на AccountId.
    """
    if not invoice_id or not isinstance(invoice_id, str):
        return None, None
    parts = invoice_id.split(":")
    if len(parts) < 2:
        return None, None
    tariff = parts[0].strip()
    try:
        telegram_id = int(parts[1])
    except (TypeError, ValueError):
        return None, None
    if config.tariff_for(tariff) is None:
        return None, telegram_id
    return tariff, telegram_id


def resolve_payment(data: dict) -> tuple[str | None, int | None, str | None]:
    """Определить (тариф, telegram_id, идентификатор транзакции) из уведомления.

    Источник истины по получателю — AccountId (мы кладём туда telegram_id),
    номер заказа используем для тарифа и как запасной источник идентификатора.
    """
    tariff, tid_from_invoice = parse_invoice_id(data.get("InvoiceId"))

    telegram_id = None
    raw_account = data.get("AccountId")
    if raw_account not in (None, ""):
        try:
            telegram_id = int(str(raw_account).strip())
        except (TypeError, ValueError):
            telegram_id = None
    if telegram_id is None:
        telegram_id = tid_from_invoice

    # Если тариф не удалось понять из номера заказа — подбираем по сумме.
    if tariff is None:
        tariff = tariff_by_amount(data.get("Amount"))

    transaction_id = data.get("TransactionId")
    transaction_id = str(transaction_id).strip() if transaction_id not in (None, "") else None

    return tariff, telegram_id, transaction_id


def is_successful_payment(data: dict) -> bool:
    """Это уведомление о РЕАЛЬНО СПИСАННЫХ деньгах?

    КРИТИЧНО: на один и тот же адрес CloudPayments может слать разные типы
    уведомлений — Check (до списания), Fail (отказ), Refund (возврат),
    Recurrent (событие подписки). Подпись у всех настоящая, её ставит сам
    провайдер, поэтому одной проверки подписи НЕДОСТАТОЧНО: без разбора типа
    отказ в оплате выдавал бы премиум так же, как успешная оплата.

    Считаем оплатой только OperationType=Payment со Status=Completed.
    Статус Authorized — это ХОЛД (деньги ещё не списаны), доступ по нему не даём.
    """
    operation = str(data.get("OperationType") or "Payment").strip()
    status = str(data.get("Status") or "").strip()
    return operation == "Payment" and status == "Completed"


def amount_matches_tariff(tariff: str, amount, currency=None) -> bool:
    """Совпадает ли фактически оплаченная сумма с ценой тарифа.

    КРИТИЧНО ДЛЯ ДЕНЕГ: виджет CloudPayments запускается НА КЛИЕНТЕ, а Public ID
    по своей природе публичен. Значит пользователь может вызвать виджет сам и
    указать произвольные amount и invoiceId — например, заплатить 1 рубль с
    номером заказа "lifetime:<свой id>". Поэтому в вебхуке сумму нужно
    сверять с прайсом на сервере, а не доверять номеру заказа.

    ВАЛЮТА проверяется обязательно: без этого 3990 узбекских сумов (≈28 ₽)
    закрыли бы годовой тариф, потому что сравнивалось бы голое число.

    Возвращает False, если сумма/валюта не совпали или цена тарифа не задана.
    Допуск в 1 копейку — на случай округления у провайдера.
    """
    expected = config.rub_price_for(tariff)
    if not expected:
        return False

    if str(currency or "").strip().upper() != config.CLOUDPAYMENTS_CURRENCY.upper():
        return False

    try:
        paid = float(amount)
    except (TypeError, ValueError):
        return False
    return abs(paid - float(expected)) < 0.01


def tariff_by_amount(amount) -> str | None:
    """Подобрать тариф по сумме платежа (в рублях), если задан прайс в рублях.

    Цены в рублях задаются переменными окружения PRICE_*_RUB. Если они не
    заданы, вернётся None — тогда тариф обязан приходить в номере заказа.
    """
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return None

    for name, rub in config.RUB_PRICES.items():
        if rub and abs(value - float(rub)) < 0.01:
            return name
    return None


def is_test_mode(data: dict) -> bool:
    """Тестовый платёж? (в проде такие не должны давать доступ)."""
    raw = data.get("TestMode")
    return str(raw).strip() in ("1", "true", "True")
