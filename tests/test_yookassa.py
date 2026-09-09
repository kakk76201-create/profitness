"""ЮKassa: создание платежа и безопасность вебхука.

Самое опасное место этой интеграции — вебхук. Уведомления ЮKassa НЕ подписаны,
поэтому единственный источник истины — повторный запрос платежа по API. Тест
закрепляет именно это поведение:

  * без ключей маршрут создания платежа отвечает 503 (ничего не создаём);
  * при создании уходит корректное тело: сумма из прайса СЕРВЕРА, две дробные
    цифры, заголовок Idempotence-Key, telegram_id и тариф в metadata;
  * доступ выдаётся только когда перепроверенный по API платёж succeeded+paid;
  * заниженная сумма доступ НЕ открывает (сверка с прайсом на сервере);
  * тестовый платёж в проде доступ НЕ открывает;
  * платёж без telegram_id в metadata доступ НЕ открывает;
  * повторное уведомление не продлевает подписку второй раз;
  * сбой проверки платежа -> HTTP 500, чтобы ЮKassa повторила уведомление;
  * адрес вебхука с секретом: без него/с чужим — уведомление отбрасывается;
  * id из уведомления проверяется по формату ДО сети: "../me", "<id>?x=1",
    "x/../<id>" в сеть не уходят и ключ дедупа не портят;
  * ключ дедупа строится из id, который вернул API (несовпадение — отказ);
  * возвращённый платёж доступ не открывает; переплата засчитывается;
  * сверка суммы — с ценой, зафиксированной в metadata при создании.

Запуск:  .venv/Scripts/python.exe tests/test_yookassa.py
"""

import importlib
import os
import sys
import pathlib
import tempfile
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp()

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp, "yk.db").replace("\\", "/")
# Пустой токен бота — чтобы тест не стучался в реальный Telegram при отправке
# сообщения плательщику (load_dotenv не перезаписывает уже заданные переменные).
os.environ["BOT_TOKEN"] = ""
os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["OWNER_ID"] = "0"
os.environ["ALLOW_INSECURE_AUTH"] = "1"
os.environ["PRICE_MONTHLY_RUB"] = "499"
os.environ["PRICE_YEARLY_RUB"] = "3990"
# Вечный тариф намеренно без рублёвой цены — проверяем отказ по нему.
os.environ["PRICE_LIFETIME_RUB"] = "0"
# Лимит на создание платежей поднимаем: тест делает много вызовов подряд,
# а проверяем мы здесь не лимитер.
os.environ["RATE_LIMIT_PAYMENT_PER_MIN"] = "100"
# Секрет в адресе вебхука — как в проде.
HOOK_SECRET = "hook-secret-0123456789abcdef0123456789"
os.environ["YOOKASSA_WEBHOOK_SECRET"] = HOOK_SECRET

SHOP_ID = "test_shop_id"
SECRET_KEY = "test_secret_key_value"
CREATE = "/payment/yookassa/create"
WEBHOOK_PLAIN = "/payment/yookassa/webhook"
WEBHOOK = WEBHOOK_PLAIN + "/" + HOOK_SECRET


def build_app(with_keys: bool):
    """Пересобрать приложение с ключами ЮKassa или без них."""
    if with_keys:
        os.environ["YOOKASSA_SHOP_ID"] = SHOP_ID
        os.environ["YOOKASSA_SECRET_KEY"] = SECRET_KEY
    else:
        os.environ.pop("YOOKASSA_SHOP_ID", None)
        os.environ.pop("YOOKASSA_SECRET_KEY", None)

    import backend.config
    importlib.reload(backend.config)
    import backend.cloudpayments
    importlib.reload(backend.cloudpayments)
    import backend.yookassa
    importlib.reload(backend.yookassa)
    import backend.main
    importlib.reload(backend.main)

    from fastapi.testclient import TestClient
    from backend.database import init_db
    init_db()
    return TestClient(backend.main.app), backend.main, backend.yookassa


class FakeResponse:
    """Минимальный ответ httpx для подмены сетевых вызовов."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def make_fake_httpx(captured: dict, payload=None, status_code=200):
    """Заглушка httpx: запоминает аргументы POST и возвращает готовый ответ."""

    class FakeHttpx:
        @staticmethod
        def post(url, json=None, headers=None, auth=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers or {}
            captured["auth"] = auth
            captured["timeout"] = timeout
            return FakeResponse(status_code, payload or {
                "id": "pay-1",
                "status": "pending",
                "confirmation": {"type": "redirect",
                                 "confirmation_url": "https://yoomoney.ru/checkout/pay-1"},
            })

    return FakeHttpx


def make_payment(payment_id, telegram_id, tariff, value="499.00", currency="RUB",
                 status="succeeded", paid=True, test=False, metadata=None):
    """Собрать объект платежа в формате ответа API ЮKassa."""
    if metadata is None:
        metadata = {"telegram_id": str(telegram_id), "tariff": tariff}
    return {
        "id": payment_id,
        "status": status,
        "paid": paid,
        "test": test,
        "amount": {"value": value, "currency": currency},
        "metadata": metadata,
    }


def notification(payment_id, event="payment.succeeded"):
    """Тело уведомления ЮKassa (подписи у него нет — это лишь подсказка)."""
    return {"type": "notification", "event": event,
            "object": {"id": payment_id, "status": "succeeded"}}


def main():
    problems = []

    def check(name, condition, extra=""):
        if not condition:
            problems.append(f"{name}  {extra}")

    from backend.database import SessionLocal
    from backend import models as M

    def get_user(tid):
        db = SessionLocal()
        try:
            return db.query(M.User).filter(M.User.telegram_id == tid).first()
        finally:
            db.close()

    def payments_count(charge_id):
        db = SessionLocal()
        try:
            return db.query(M.Payment).filter(M.Payment.charge_id == charge_id).count()
        finally:
            db.close()

    # --- 1. Без ключей платёж не создаётся ---------------------------------
    client, main_module, yookassa = build_app(with_keys=False)
    check("без ключей is_enabled=False", yookassa.is_enabled() is False)
    resp = client.post(CREATE, json={"tariff": "monthly"})
    check("создание платежа -> 503", resp.status_code == 503, resp.status_code)
    detail = resp.json().get("detail") if resp.status_code == 503 else {}
    check("причина — yookassa_disabled",
          (detail or {}).get("error") == "yookassa_disabled", detail)

    # Вебхук при выключенной интеграции просто подтверждает доставку.
    resp = client.post(WEBHOOK, json=notification("pay-off"))
    check("вебхук при выключенной интеграции -> 200 ok",
          resp.status_code == 200 and resp.json() == {"ok": True}, resp.text[:200])

    # --- 2. С ключами: корректное тело запроса к API -----------------------
    client, main_module, yookassa = build_app(with_keys=True)
    check("с ключами is_enabled=True", yookassa.is_enabled() is True)

    captured = {}
    with patch.object(yookassa, "httpx", make_fake_httpx(captured)):
        resp = client.post(CREATE, json={"tariff": "monthly"})

    check("создание платежа -> 200", resp.status_code == 200, resp.text[:300])
    body = resp.json() if resp.status_code == 200 else {}
    check("отдан payment_id", body.get("payment_id") == "pay-1", body)
    check("отдана ссылка на оплату",
          body.get("confirmation_url") == "https://yoomoney.ru/checkout/pay-1", body)
    check("секретный ключ не утёк в ответ", SECRET_KEY not in resp.text, "секрет в ответе!")

    sent = captured.get("json") or {}
    check("адрес API v3", str(captured.get("url", "")).endswith("/v3/payments"),
          captured.get("url"))
    check("Basic-auth из ключей", captured.get("auth") == (SHOP_ID, SECRET_KEY),
          captured.get("auth"))
    check("есть ключ идемпотентности",
          bool((captured.get("headers") or {}).get("Idempotence-Key")),
          captured.get("headers"))
    check("сумма из прайса сервера с двумя знаками",
          (sent.get("amount") or {}).get("value") == "499.00", sent.get("amount"))
    check("валюта RUB", (sent.get("amount") or {}).get("currency") == "RUB",
          sent.get("amount"))
    check("одностадийный платёж", sent.get("capture") is True, sent.get("capture"))
    check("возврат через redirect",
          (sent.get("confirmation") or {}).get("type") == "redirect", sent.get("confirmation"))
    check("задан return_url",
          bool((sent.get("confirmation") or {}).get("return_url")), sent.get("confirmation"))
    check("в metadata телеграм-id плательщика",
          (sent.get("metadata") or {}).get("telegram_id") == "1", sent.get("metadata"))
    check("в metadata тариф", (sent.get("metadata") or {}).get("tariff") == "monthly",
          sent.get("metadata"))
    check("описание не длиннее 128 символов",
          len(str(sent.get("description") or "")) <= 128, sent.get("description"))
    check("описание содержит название приложения",
          "Калории" in str(sent.get("description") or ""), sent.get("description"))

    # Тариф без рублёвой цены и несуществующий тариф — до сети не доходим.
    with patch.object(yookassa, "httpx", make_fake_httpx({})):
        resp = client.post(CREATE, json={"tariff": "lifetime"})
        check("тариф без рублёвой цены -> 400", resp.status_code == 400, resp.status_code)
        resp = client.post(CREATE, json={"tariff": "нет-такого"})
        check("неизвестный тариф -> 400", resp.status_code == 400, resp.status_code)

    # Ошибка на стороне ЮKassa -> 502 «попробуйте позже».
    with patch.object(yookassa, "httpx",
                      make_fake_httpx({}, payload={"type": "error"}, status_code=400)):
        resp = client.post(CREATE, json={"tariff": "monthly"})
    check("ошибка провайдера -> 502", resp.status_code == 502, resp.status_code)

    # --- 3. Вебхук: не-succeeded событие доступ не выдаёт -------------------
    with patch.object(yookassa, "fetch_payment") as fetch:
        resp = client.post(WEBHOOK, json=notification("pay-2", event="payment.canceled"))
        check("отменённый платёж -> 200", resp.status_code == 200, resp.status_code)
        check("по чужому событию платёж даже не запрашивается",
              fetch.call_count == 0, fetch.call_count)

    resp = client.post(WEBHOOK, content="не json".encode("utf-8"),
                       headers={"Content-Type": "application/json"})
    check("мусор в теле -> 200 ok",
          resp.status_code == 200 and resp.json() == {"ok": True}, resp.text[:200])

    # --- 4. Успешный платёж включает премиум --------------------------------
    ok_payment = make_payment("pay-100", 6001, "monthly")
    with patch.object(yookassa, "fetch_payment", return_value=ok_payment) as fetch:
        resp = client.post(WEBHOOK, json=notification("pay-100"))
        check("успешная оплата -> 200 ok",
              resp.status_code == 200 and resp.json() == {"ok": True}, resp.text[:200])
        check("платёж перепрошен по API (id из уведомления)",
              fetch.call_args[0][0] == "pay-100", fetch.call_args)

    user = get_user(6001)
    check("премиум активирован", user is not None and user.subscription_type == "monthly",
          user and user.subscription_type)
    check("срок подписки задан", user is not None and user.subscription_until is not None)
    check("платёж записан один раз", payments_count("yk:pay-100") == 1,
          payments_count("yk:pay-100"))

    # --- 5. Повтор того же уведомления не продлевает второй раз -------------
    until_before = get_user(6001).subscription_until
    with patch.object(yookassa, "fetch_payment", return_value=ok_payment):
        resp = client.post(WEBHOOK, json=notification("pay-100"))
    check("повтор -> 200 ok", resp.json() == {"ok": True}, resp.json())
    check("повтор не продлил подписку",
          get_user(6001).subscription_until == until_before,
          (until_before, get_user(6001).subscription_until))
    check("повтор не создал вторую запись платежа",
          payments_count("yk:pay-100") == 1, payments_count("yk:pay-100"))

    # --- 6. Заниженная сумма доступ НЕ открывает ----------------------------
    cheap = make_payment("pay-101", 6002, "yearly", value="1.00")
    with patch.object(yookassa, "fetch_payment", return_value=cheap):
        resp = client.post(WEBHOOK, json=notification("pay-101"))
    check("заниженная сумма -> 200 ok", resp.json() == {"ok": True}, resp.json())
    check("заниженная сумма -> доступ НЕ выдан", get_user(6002) is None, get_user(6002))

    # Чужая валюта — тоже мимо.
    foreign = make_payment("pay-102", 6003, "monthly", value="499.00", currency="KZT")
    with patch.object(yookassa, "fetch_payment", return_value=foreign):
        client.post(WEBHOOK, json=notification("pay-102"))
    check("чужая валюта -> доступ НЕ выдан", get_user(6003) is None, get_user(6003))

    # --- 7. Неоплаченный платёж (status/paid) доступ НЕ открывает -----------
    pending = make_payment("pay-103", 6004, "monthly", status="pending", paid=False)
    with patch.object(yookassa, "fetch_payment", return_value=pending):
        client.post(WEBHOOK, json=notification("pay-103"))
    check("pending -> доступ НЕ выдан", get_user(6004) is None, get_user(6004))

    half = make_payment("pay-104", 6005, "monthly", status="succeeded", paid=False)
    with patch.object(yookassa, "fetch_payment", return_value=half):
        client.post(WEBHOOK, json=notification("pay-104"))
    check("succeeded без paid -> доступ НЕ выдан", get_user(6005) is None, get_user(6005))

    # --- 8. Тестовый платёж в проде доступ НЕ открывает ---------------------
    test_payment = make_payment("pay-105", 6006, "monthly", test=True)
    saved_dev = main_module.DEV_MODE
    main_module.DEV_MODE = False           # боевой режим
    with patch.object(yookassa, "fetch_payment", return_value=test_payment):
        resp = client.post(WEBHOOK, json=notification("pay-105"))
    check("тестовый платёж -> 200 ok", resp.json() == {"ok": True}, resp.json())
    check("тестовый платёж в проде -> доступ НЕ выдан", get_user(6006) is None,
          get_user(6006))

    # В dev-режиме тестовый платёж наоборот должен работать (иначе не проверить
    # интеграцию до боевых ключей).
    main_module.DEV_MODE = True
    dev_payment = make_payment("pay-106", 6007, "monthly", test=True)
    with patch.object(yookassa, "fetch_payment", return_value=dev_payment):
        client.post(WEBHOOK, json=notification("pay-106"))
    check("в dev-режиме тестовый платёж активирует премиум",
          get_user(6007) is not None and get_user(6007).subscription_type == "monthly",
          get_user(6007) and get_user(6007).subscription_type)
    main_module.DEV_MODE = saved_dev

    # --- 9. Битая metadata: доступ не выдаём, но доставку подтверждаем ------
    no_tid = make_payment("pay-107", None, "monthly", metadata={"tariff": "monthly"})
    with patch.object(yookassa, "fetch_payment", return_value=no_tid):
        resp = client.post(WEBHOOK, json=notification("pay-107"))
    check("платёж без telegram_id -> 200 ok", resp.json() == {"ok": True}, resp.json())
    check("платёж без telegram_id не записан",
          payments_count("yk:pay-107") == 0, payments_count("yk:pay-107"))

    bad_tariff = make_payment("pay-108", 6008, "premium-forever",
                              metadata={"telegram_id": "6008", "tariff": "premium-forever"})
    with patch.object(yookassa, "fetch_payment", return_value=bad_tariff):
        resp = client.post(WEBHOOK, json=notification("pay-108"))
    check("неизвестный тариф -> 200 ok", resp.json() == {"ok": True}, resp.json())
    check("неизвестный тариф -> доступ НЕ выдан", get_user(6008) is None, get_user(6008))

    # Уведомление без идентификатора платежа — просто подтверждаем.
    resp = client.post(WEBHOOK, json={"event": "payment.succeeded", "object": {}})
    check("уведомление без id -> 200 ok", resp.json() == {"ok": True}, resp.json())

    # --- 9a. Секрет в адресе вебхука ----------------------------------------
    with patch.object(yookassa, "fetch_payment", return_value=ok_payment) as fetch:
        resp = client.post(WEBHOOK_PLAIN, json=notification("pay-100"))
        check("адрес без секрета -> 200 ok", resp.json() == {"ok": True}, resp.text[:200])
        resp = client.post(WEBHOOK_PLAIN + "/wrong-secret", json=notification("pay-100"))
        check("чужой секрет -> 200 ok", resp.json() == {"ok": True}, resp.text[:200])
        check("без верного секрета платёж по API даже не запрашивается",
              fetch.call_count == 0, fetch.call_count)

    # --- 9b. Формат id: мусор не уходит в сеть и не портит дедуп ------------
    for bad_id in ("../me", "pay-100?x=1", "x/../pay-100", "./pay-100",
                   "../payments?limit=100", "pay 100", "a" * 65, "ab"):
        with patch.object(yookassa, "fetch_payment", return_value=ok_payment) as fetch:
            resp = client.post(WEBHOOK, json=notification(bad_id))
            check(f"битый id {bad_id!r} -> 200 ok", resp.json() == {"ok": True}, resp.text[:200])
            check(f"битый id {bad_id!r} не уходит в сеть", fetch.call_count == 0, fetch.call_count)
        check(f"битый id {bad_id!r} не создал запись платежа",
              payments_count("yk:" + bad_id) == 0, payments_count("yk:" + bad_id))
    check("подписка не продлена битыми id",
          get_user(6001).subscription_until == until_before,
          (until_before, get_user(6001).subscription_until))
    # fetch_payment сам отказывается идти в сеть с плохим id.
    try:
        yookassa.fetch_payment("../me")
        check("fetch_payment с плохим id бросает ValueError", False)
    except ValueError:
        pass
    except Exception as exc:  # noqa: BLE001
        check("fetch_payment с плохим id бросает ValueError", False, repr(exc))

    # --- 9c. Ключ дедупа — из id, который вернул API ------------------------
    other = make_payment("pay-999999", 6009, "monthly")
    with patch.object(yookassa, "fetch_payment", return_value=other):
        resp = client.post(WEBHOOK, json=notification("pay-200200"))
    check("id из API не совпал с уведомлением -> 200 ok", resp.json() == {"ok": True}, resp.json())
    check("id из API не совпал -> доступ НЕ выдан", get_user(6009) is None, get_user(6009))
    check("id из API не совпал -> платёж не записан",
          payments_count("yk:pay-200200") == 0 and payments_count("yk:pay-999999") == 0)

    # --- 9d. Возврат и переплата --------------------------------------------
    refunded = make_payment("pay-300300", 6010, "monthly")
    refunded["refunded_amount"] = {"value": "499.00", "currency": "RUB"}
    with patch.object(yookassa, "fetch_payment", return_value=refunded):
        client.post(WEBHOOK, json=notification("pay-300300"))
    check("возвращённый платёж -> доступ НЕ выдан", get_user(6010) is None, get_user(6010))

    over = make_payment("pay-400400", 6011, "monthly", value="500.00")
    with patch.object(yookassa, "fetch_payment", return_value=over):
        client.post(WEBHOOK, json=notification("pay-400400"))
    check("переплата засчитана как оплата тарифа",
          get_user(6011) is not None and get_user(6011).subscription_type == "monthly",
          get_user(6011) and get_user(6011).subscription_type)

    # --- 9e. Цена из metadata (прайс сменился после создания платежа) -------
    old_price = make_payment("pay-500500", 6012, "monthly", value="399.00",
                             metadata={"telegram_id": "6012", "tariff": "monthly",
                                       "price": "399.00"})
    with patch.object(yookassa, "fetch_payment", return_value=old_price):
        client.post(WEBHOOK, json=notification("pay-500500"))
    check("оплата по цене из metadata засчитана",
          get_user(6012) is not None and get_user(6012).subscription_type == "monthly",
          get_user(6012) and get_user(6012).subscription_type)

    # --- 10. Сбой проверки платежа -> 500 (ЮKassa повторит) ----------------
    with patch.object(yookassa, "fetch_payment",
                      side_effect=RuntimeError("Не удалось проверить платёж")):
        resp = client.post(WEBHOOK, json=notification("pay-109"))
    check("сбой проверки платежа -> HTTP 500", resp.status_code == 500, resp.status_code)

    # --- 11. Вспомогательные функции модуля --------------------------------
    check("charge_id с префиксом yk:", yookassa.build_charge_id("abc") == "yk:abc",
          yookassa.build_charge_id("abc"))
    check("разбор уведомления",
          yookassa.resolve_notification(notification("pay-0001")) == ("payment.succeeded", "pay-0001"),
          yookassa.resolve_notification(notification("pay-0001")))
    check("is_valid_payment_id: uuid проходит",
          yookassa.is_valid_payment_id("2c8f1a2b-000f-5000-8000-1a2b3c4d5e6f"))
    check("is_valid_payment_id: мусор не проходит",
          not yookassa.is_valid_payment_id("../me") and not yookassa.is_valid_payment_id("p?x=1"))
    check("Idempotence-Key одинаков в пределах окна",
          yookassa.idempotence_key(1, "monthly", now=1000.0) == yookassa.idempotence_key(1, "monthly", now=1100.0))
    check("Idempotence-Key разный для разных тарифов/окон",
          yookassa.idempotence_key(1, "monthly", now=1000.0) != yookassa.idempotence_key(1, "yearly", now=1000.0)
          and yookassa.idempotence_key(1, "monthly", now=1000.0) != yookassa.idempotence_key(1, "monthly", now=100000.0))
    for junk in (None, "строка", 42, {}, {"event": "payment.succeeded"},
                 {"event": "payment.succeeded", "object": "не словарь"},
                 {"object": {"id": ""}}):
        event, pid = yookassa.resolve_notification(junk)
        check(f"мусор в уведомлении не ломает разбор ({junk!r})", pid is None, (event, pid))

    check("is_success: succeeded+paid", yookassa.is_success(ok_payment) is True)
    check("is_success: мусор", yookassa.is_success("не платёж") is False)
    check("сверка суммы: верная проходит",
          yookassa.amount_matches_tariff("monthly", {"value": "499.00", "currency": "RUB"}))
    check("сверка суммы: заниженная не проходит",
          not yookassa.amount_matches_tariff("yearly", {"value": "1.00", "currency": "RUB"}))
    check("сверка суммы: переплата проходит",
          yookassa.amount_matches_tariff("monthly", {"value": "500.00", "currency": "RUB"}))
    check("сверка суммы: цена из metadata имеет приоритет",
          yookassa.amount_matches_tariff("monthly", {"value": "399.00", "currency": "RUB"}, "399.00"))
    check("сверка суммы: битая цена из metadata -> прайс сервера",
          not yookassa.amount_matches_tariff("monthly", {"value": "399.00", "currency": "RUB"}, "abc"))
    check("is_success: возвращённый платёж не проходит",
          not yookassa.is_success(dict(ok_payment, refunded_amount={"value": "10.00", "currency": "RUB"})))
    check("сверка суммы: чужая валюта не проходит",
          not yookassa.amount_matches_tariff("monthly", {"value": "499.00", "currency": "USD"}))
    check("сверка суммы: тариф без цены не проходит",
          not yookassa.amount_matches_tariff("lifetime", {"value": "7990.00", "currency": "RUB"}))
    check("сверка суммы: мусор не проходит",
          not yookassa.amount_matches_tariff("monthly", "499"))
    check("is_test", yookassa.is_test({"test": True}) is True
          and yookassa.is_test({"test": False}) is False)
    check("metadata платежа",
          yookassa.payment_metadata(ok_payment) == (6001, "monthly"),
          yookassa.payment_metadata(ok_payment))
    check("битая metadata -> (None, None)",
          yookassa.payment_metadata({"metadata": {"telegram_id": "абв"}}) == (None, None),
          yookassa.payment_metadata({"metadata": {"telegram_id": "абв"}}))

    if problems:
        print("FAIL:")
        for p in problems:
            print("  -", p)
        return 1

    print("OK: без ключей платёж не создаётся (503); тело запроса корректное")
    print("    (Idempotence-Key, сумма 499.00 из прайса сервера, metadata);")
    print("    доступ выдаётся только по перепроверенному платежу succeeded+paid,")
    print("    повтор уведомления не продлевает дважды; заниженная сумма, чужая валюта,")
    print("    тестовый платёж в проде и битая metadata доступ НЕ открывают;")
    print("    сбой проверки платежа отвечает 500, чтобы ЮKassa повторила;")
    print("    секрет в адресе вебхука, строгий формат id, дедуп по id из API,")
    print("    возврат не открывает доступ, переплата и цена из metadata засчитываются")
    return 0


if __name__ == "__main__":
    sys.exit(main())
