"""CloudPayments: проверка подписи вебхука, идемпотентность, TestMode, формат ответа.

Самое опасное место интеграции — подпись. Если её проверять неправильно, любой
желающий сможет прислать поддельное уведомление и выдать себе подписку.

Ключевые требования протокола, которые здесь закреплены:
  * подпись = base64(HMAC-SHA256(СЫРОЕ тело, API Secret)), заголовок Content-HMAC;
  * ответ — HTTP 200 и тело РОВНО {"code": 0}, иначе будут повторы (до 100 раз);
  * платежи с TestMode=1 в проде не должны выдавать доступ;
  * повторная доставка одного платежа не должна продлевать подписку дважды;
  * тариф «3 месяца» (quarterly) засчитывается только на свою сумму, а описание
    платежа в виджете — по-русски с названием продукта.

Запуск:  .venv/Scripts/python.exe tests/test_cloudpayments.py
"""

import base64
import hashlib
import hmac
import importlib
import os
import sys
import pathlib
import tempfile
from urllib.parse import urlencode

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp()
SECRET = "test_api_secret_value"

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp, "cp.db").replace("\\", "/")
os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["OWNER_ID"] = "0"
os.environ["ALLOW_INSECURE_AUTH"] = "1"
os.environ["CLOUDPAYMENTS_PUBLIC_ID"] = "pk_test_public_id"
os.environ["CLOUDPAYMENTS_API_SECRET"] = SECRET
os.environ["PRICE_MONTHLY_RUB"] = "499"
os.environ["PRICE_YEARLY_RUB"] = "3990"
os.environ["PRICE_QUARTERLY_RUB"] = "1790"
# Вечный тариф намеренно без рублёвой цены — проверяем отказ по нему.
os.environ["PRICE_LIFETIME_RUB"] = "0"

from backend import config as _config     # noqa: E402
importlib.reload(_config)

from fastapi.testclient import TestClient  # noqa: E402
from backend.database import init_db, SessionLocal  # noqa: E402
from backend import models as M            # noqa: E402
from backend import cloudpayments          # noqa: E402
from backend.main import app               # noqa: E402

init_db()
client = TestClient(app)

WEBHOOK = "/payment/cloudpayments/webhook"


def sign(body: bytes) -> str:
    return base64.b64encode(
        hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
    ).decode()


def post_webhook(fields: dict, signature=None, tamper=False):
    """Отправить уведомление с корректной (или испорченной) подписью."""
    body = urlencode(fields).encode()
    sig = signature if signature is not None else sign(body)
    if tamper:
        sig = sign(body + b"x")
    return client.post(
        WEBHOOK,
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Content-HMAC": sig},
    )


def get_user(tid):
    db = SessionLocal()
    try:
        return db.query(M.User).filter(M.User.telegram_id == tid).first()
    finally:
        db.close()


def main():
    problems = []

    def check(name, condition, extra=""):
        if not condition:
            problems.append(f"{name}  {extra}")

    # --- 1. Подпись: HMAC-SHA256 в base64 от сырого тела ---------------------
    body = b"TransactionId=1&Amount=499"
    check("верная подпись принимается", cloudpayments.verify_signature(body, sign(body)))
    check("подпись от другого тела отвергается",
          not cloudpayments.verify_signature(body, sign(b"other-body")))
    check("пустая подпись отвергается", not cloudpayments.verify_signature(body, ""))
    check("None отвергается", not cloudpayments.verify_signature(body, None))
    check("пробелы по краям не мешают",
          cloudpayments.verify_signature(body, "  " + sign(body) + "  "))

    # --- 2. Поддельное уведомление НЕ выдаёт доступ -------------------------
    fields = {
        "OperationType": "Payment", "Status": "Completed", "TransactionId": "1001", "Amount": "499", "Currency": "RUB",
        "AccountId": "5001", "InvoiceId": "monthly:5001:20260727120000", "TestMode": "0",
    }
    resp = post_webhook(fields, tamper=True)
    check("подделка -> HTTP 200", resp.status_code == 200, resp.status_code)
    check("подделка -> код не 0", resp.json().get("code") != 0, resp.json())
    check("подделка -> доступ НЕ выдан", get_user(5001) is None, get_user(5001))

    # --- 3. Корректное уведомление выдаёт подписку --------------------------
    resp = post_webhook(fields)
    check("оплата -> HTTP 200", resp.status_code == 200, resp.status_code)
    check("оплата -> {'code': 0}", resp.json() == {"code": 0}, resp.json())
    user = get_user(5001)
    check("подписка активирована", user is not None and user.subscription_type == "monthly",
          user and user.subscription_type)
    check("срок подписки задан", user is not None and user.subscription_until is not None)

    # --- 4. Идемпотентность: повтор не продлевает дважды --------------------
    until_before = get_user(5001).subscription_until
    resp = post_webhook(fields)
    check("повтор -> {'code': 0}", resp.json() == {"code": 0}, resp.json())
    check("повтор не продлил подписку", get_user(5001).subscription_until == until_before,
          (until_before, get_user(5001).subscription_until))
    db = SessionLocal()
    count = db.query(M.Payment).filter(M.Payment.charge_id == "cp:1001").count()
    db.close()
    check("платёж записан ровно один раз", count == 1, count)

    # --- 5. TestMode=1 в проде доступ не выдаёт ----------------------------
    import backend.main as main_module
    saved_dev = main_module.DEV_MODE
    main_module.DEV_MODE = False
    test_fields = dict(fields, TransactionId="1002", AccountId="5002",
                       InvoiceId="monthly:5002:20260727120001", TestMode="1")
    resp = post_webhook(test_fields)
    check("TestMode -> {'code': 0}", resp.json() == {"code": 0}, resp.json())
    check("TestMode не выдал доступ", get_user(5002) is None, get_user(5002))
    main_module.DEV_MODE = saved_dev

    # --- 6. Годовой тариф по номеру заказа ---------------------------------
    year_fields = {
        "OperationType": "Payment", "Status": "Completed", "TransactionId": "1003", "Amount": "3990", "Currency": "RUB",
        "AccountId": "5003", "InvoiceId": "yearly:5003:20260727120002", "TestMode": "0",
    }
    post_webhook(year_fields)
    check("годовой тариф распознан",
          get_user(5003) is not None and get_user(5003).subscription_type == "yearly",
          get_user(5003) and get_user(5003).subscription_type)

    # --- 6b. Тариф «3 месяца»: своя сумма открывает, цена месяца — нет -----
    quarter_fields = {
        "OperationType": "Payment", "Status": "Completed", "TransactionId": "1007", "Amount": "1790", "Currency": "RUB",
        "AccountId": "5007", "InvoiceId": "quarterly:5007:20260916120000", "TestMode": "0",
    }
    resp = post_webhook(quarter_fields)
    check("3 месяца за 1790 -> {'code': 0}", resp.json() == {"code": 0}, resp.json())
    check("тариф «3 месяца» распознан",
          get_user(5007) is not None and get_user(5007).subscription_type == "quarterly",
          get_user(5007) and get_user(5007).subscription_type)
    quarter_cheap = dict(quarter_fields, TransactionId="1008", AccountId="5008", Amount="499",
                         InvoiceId="quarterly:5008:20260916120001")
    resp = post_webhook(quarter_cheap)
    check("цена месяца за 3 месяца -> код 12",
          resp.json().get("code") == cloudpayments.CODE_INVALID_AMOUNT, resp.json())
    check("цена месяца за 3 месяца -> доступ НЕ выдан", get_user(5008) is None, get_user(5008))
    check("разбор номера заказа quarterly",
          cloudpayments.parse_invoice_id("quarterly:42:20260101") == ("quarterly", 42))

    # --- 7. Тариф по сумме, когда номер заказа чужого формата --------------
    odd_fields = {
        "OperationType": "Payment", "Status": "Completed", "TransactionId": "1004", "Amount": "499", "Currency": "RUB",
        "AccountId": "5004", "InvoiceId": "случайный-номер", "TestMode": "0",
    }
    post_webhook(odd_fields)
    check("тариф подобран по сумме",
          get_user(5004) is not None and get_user(5004).subscription_type == "monthly",
          get_user(5004) and get_user(5004).subscription_type)

    # --- 7b. ЗАНИЖЕННАЯ СУММА не выдаёт доступ ------------------------------
    # Виджет запускается на клиенте, Public ID публичен -> пользователь может
    # сам вызвать оплату на 1 рубль с номером заказа "lifetime:<свой id>".
    cheat = {
        "OperationType": "Payment", "Status": "Completed", "TransactionId": "1006", "Amount": "1", "Currency": "RUB",
        "AccountId": "5006", "InvoiceId": "yearly:5006:20260727120003", "TestMode": "0",
    }
    resp = post_webhook(cheat)
    check("заниженная сумма -> код 12",
          resp.json().get("code") == cloudpayments.CODE_INVALID_AMOUNT, resp.json())
    check("заниженная сумма -> доступ НЕ выдан", get_user(5006) is None, get_user(5006))

    check("сверка суммы: верная проходит",
          cloudpayments.amount_matches_tariff("monthly", "499", "RUB"))
    check("сверка суммы: заниженная не проходит",
          not cloudpayments.amount_matches_tariff("yearly", "1", "RUB"))
    check("сверка суммы: тариф без рублёвой цены не проходит",
          not cloudpayments.amount_matches_tariff("lifetime", "4000", "RUB"))
    check("сверка суммы: мусор не проходит",
          not cloudpayments.amount_matches_tariff("monthly", "не число", "RUB"))
    check("сверка суммы: чужая валюта не проходит",
          not cloudpayments.amount_matches_tariff("monthly", "499", "USD"))

    # --- 8. Без плательщика — понятный код отказа ---------------------------
    resp = post_webhook({"OperationType": "Payment", "Status": "Completed", "TransactionId": "1005", "Amount": "499", "TestMode": "0"})
    check("нет плательщика -> код 11",
          resp.json().get("code") == cloudpayments.CODE_INVALID_ACCOUNT, resp.json())

    # --- 9. Разбор номера заказа -------------------------------------------
    check("разбор номера заказа",
          cloudpayments.parse_invoice_id("monthly:42:20260101") == ("monthly", 42))
    check("чужой формат -> тарифа нет",
          cloudpayments.parse_invoice_id("что-то") == (None, None))
    check("сборка номера заказа",
          cloudpayments.build_invoice_id("yearly", 7, "X") == "yearly:7:X")

    # --- 10. Конфиг виджета не раскрывает секрет ---------------------------
    resp = client.get("/payment/cloudpayments/config", params={"tariff": "monthly"})
    check("конфиг виджета 200", resp.status_code == 200, resp.text[:200])
    if resp.status_code == 200:
        body_json = resp.json()
        check("отдан public_id", body_json.get("public_id") == "pk_test_public_id")
        check("СЕКРЕТ НЕ УТЁК", SECRET not in resp.text, "секрет виден в ответе!")
        check("сумма из прайса", body_json.get("amount") == 499, body_json.get("amount"))
        check("номер заказа содержит тариф",
              str(body_json.get("invoice_id", "")).startswith("monthly:"),
              body_json.get("invoice_id"))
        # Описание видят плательщик и модератор: без кода тарифа и старого имени.
        check("описание виджета по-русски",
              body_json.get("description") == "Fitness Up — подписка на месяц",
              body_json.get("description"))

    resp = client.get("/payment/cloudpayments/config", params={"tariff": "lifetime"})
    check("тариф без рублёвой цены -> 400", resp.status_code == 400, resp.status_code)

    if problems:
        print("FAIL:")
        for p in problems:
            print("  -", p)
        return 1

    print("OK: подпись проверяется корректно (подделка не проходит), ответ ровно")
    print("    {'code': 0}, идемпотентность по TransactionId, TestMode не выдаёт")
    print("    доступ в проде, тариф из номера заказа и по сумме, секрет не утекает;")
    print("    «3 месяца» — только за свою сумму; описание виджета по-русски")
    return 0


if __name__ == "__main__":
    sys.exit(main())
