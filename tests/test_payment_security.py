"""Защита денежного пути: проверки, найденные состязательным аудитом.

Каждый блок здесь соответствует реальной дыре, через которую можно было
получить премиум бесплатно или дешевле. Тест закрепляет исправления, чтобы
они не «отвалились» при будущих правках.

Запуск:  .venv/Scripts/python.exe tests/test_payment_security.py
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
SECRET = "sec_audit"

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp, "sec.db").replace("\\", "/")
os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["OWNER_ID"] = "0"
os.environ["ALLOW_INSECURE_AUTH"] = "1"
os.environ["CLOUDPAYMENTS_PUBLIC_ID"] = "pk_sec"
os.environ["CLOUDPAYMENTS_API_SECRET"] = SECRET
os.environ["CLOUDPAYMENTS_CURRENCY"] = "RUB"
os.environ["PRICE_MONTHLY_RUB"] = "499"
os.environ["PRICE_YEARLY_RUB"] = "3990"
os.environ["TRIAL_DAYS"] = "7"

from backend import config  # noqa: E402
importlib.reload(config)

from fastapi.testclient import TestClient       # noqa: E402
from backend.database import init_db, SessionLocal  # noqa: E402
from backend import models as M                 # noqa: E402
from backend import cloudpayments               # noqa: E402
from backend import payment_providers           # noqa: E402
from backend import telegram_bot                # noqa: E402
import backend.main as main_module              # noqa: E402

init_db()
client = TestClient(main_module.app)
WEBHOOK = "/payment/cloudpayments/webhook"

# Успешная оплата = OperationType Payment + Status Completed.
BASE_PAY = {"OperationType": "Payment", "Status": "Completed", "TestMode": "0"}


def post_webhook(fields: dict):
    body = urlencode(fields).encode()
    sig = base64.b64encode(
        hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
    ).decode()
    return client.post(
        WEBHOOK, content=body,
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

    # === 1. Тип уведомления: отказ/проверка/возврат НЕ дают доступ ==========
    for label, extra_fields in [
        ("Check (до списания)", {"OperationType": "Payment", "Status": ""}),
        ("Fail (отказ)", {"OperationType": "Payment", "Status": "Declined"}),
        ("Authorized (холд)", {"OperationType": "Payment", "Status": "Authorized"}),
        ("Refund (возврат)", {"OperationType": "Refund", "Status": "Completed"}),
    ]:
        tid = 6100 + abs(hash(label)) % 100
        fields = {"TransactionId": f"t{tid}", "Amount": "499", "Currency": "RUB",
                  "AccountId": str(tid), "InvoiceId": f"monthly:{tid}:x",
                  "TestMode": "0", **extra_fields}
        resp = post_webhook(fields)
        check(f"{label}: HTTP 200", resp.status_code == 200, resp.status_code)
        check(f"{label}: доступ НЕ выдан", get_user(tid) is None, get_user(tid))

    # Контроль: нормальная оплата всё-таки проходит.
    ok_fields = {**BASE_PAY, "TransactionId": "t7000", "Amount": "499",
                 "Currency": "RUB", "AccountId": "7000", "InvoiceId": "monthly:7000:x"}
    resp = post_webhook(ok_fields)
    check("успешная оплата -> {'code': 0}", resp.json() == {"code": 0}, resp.json())
    check("успешная оплата -> премиум", get_user(7000) is not None
          and get_user(7000).subscription_type == "monthly",
          get_user(7000) and get_user(7000).subscription_type)

    # === 2. Валюта: чужая валюта не закрывает рублёвый тариф ===============
    cheat = {**BASE_PAY, "TransactionId": "t7001", "Amount": "3990",
             "Currency": "UZS", "AccountId": "7001", "InvoiceId": "yearly:7001:x"}
    resp = post_webhook(cheat)
    check("чужая валюта -> код 12",
          resp.json().get("code") == cloudpayments.CODE_INVALID_AMOUNT, resp.json())
    check("чужая валюта -> доступ НЕ выдан", get_user(7001) is None, get_user(7001))
    check("сверка валюты в функции",
          not cloudpayments.amount_matches_tariff("yearly", "3990", "UZS"))
    check("верная валюта проходит",
          cloudpayments.amount_matches_tariff("yearly", "3990", "RUB"))

    # === 3. Без TransactionId дедуп невозможен -> не принимаем =============
    no_tx = {**BASE_PAY, "Amount": "499", "Currency": "RUB",
             "AccountId": "7002", "InvoiceId": "monthly:7002:x"}
    resp = post_webhook(no_tx)
    check("без TransactionId -> отказ",
          resp.json().get("code") == cloudpayments.CODE_REJECTED, resp.json())
    check("без TransactionId -> доступ НЕ выдан", get_user(7002) is None, get_user(7002))

    # === 4. Пожизненный доступ не понижается срочным тарифом ===============
    db = SessionLocal()
    db.add(M.User(telegram_id=7003, subscription_type="lifetime", subscription_until=None))
    db.commit()
    db.close()
    payment_providers.activate_premium(SessionLocal(), 7003, "monthly", "cloudpayments",
                                       499, "RUB", charge_id="cp:t7003")
    user = get_user(7003)
    check("lifetime не понижен до monthly", user.subscription_type == "lifetime",
          user.subscription_type)
    check("lifetime остался бессрочным", user.subscription_until is None,
          user.subscription_until)

    # То же для ручной выдачи владельцем на N дней.
    payment_providers.grant_days(SessionLocal(), 7003, 30, "owner")
    check("grant_days не понижает lifetime", get_user(7003).subscription_type == "lifetime",
          get_user(7003).subscription_type)

    # === 5. Дата окончания не двигается назад ==============================
    db = SessionLocal()
    from datetime import datetime, timedelta
    far = datetime.utcnow() + timedelta(days=300)
    db.add(M.User(telegram_id=7004, subscription_type="yearly", subscription_until=far))
    db.commit()
    db.close()
    payment_providers.activate_premium(SessionLocal(), 7004, "monthly", "cloudpayments",
                                       499, "RUB", charge_id="cp:t7004")
    check("дата окончания не уменьшилась", get_user(7004).subscription_until >= far,
          (far, get_user(7004).subscription_until))

    # === 6. Триал не повторяется после удаления аккаунта ===================
    resp = client.post("/subscription/trial")
    check("триал выдан", resp.status_code == 200, resp.text[:200])
    resp = client.request("DELETE", "/account/data")
    check("аккаунт удалён", resp.status_code == 200, resp.text[:200])
    resp = client.post("/subscription/trial")
    check("повторный триал после удаления запрещён", resp.status_code == 400, resp.status_code)
    if resp.status_code == 400:
        detail = resp.json().get("detail") or {}
        check("причина — триал уже использован",
              (detail.get("error") if isinstance(detail, dict) else "") == "trial_used", detail)

    # === 7. Оплата звёздами отключена: pre_checkout ВСЕГДА отклоняется =====
    # Новых Stars-счетов приложение не выпускает, поэтому любой запрос на
    # подтверждение оплаты — либо древний счёт, либо попытка провести платёж
    # мимо витрины. Отказ на pre-checkout безопасен: деньги ещё не списаны.
    for label, pcq in [
        ("обычный счёт", {"invoice_payload": "monthly:555", "total_amount": 250,
                          "from": {"id": 555}}),
        ("чужой плательщик", {"invoice_payload": "monthly:555", "total_amount": 250,
                              "from": {"id": 999}}),
        ("заниженная сумма", {"invoice_payload": "monthly:555", "total_amount": 1,
                              "from": {"id": 555}}),
        ("пустой запрос", {}),
    ]:
        ok, reason = telegram_bot._validate_pre_checkout(pcq)
        check(f"pre_checkout ({label}) отклонён", ok is False, (ok, reason))
        check(f"pre_checkout ({label}): понятная причина",
              isinstance(reason, str) and "звёзд" in reason.lower(), reason)

    check("создание Stars-счетов удалено",
          not hasattr(telegram_bot, "create_stars_invoice_link"),
          "функция create_stars_invoice_link всё ещё существует")
    check("у тарифов нет цен в звёздах",
          all("stars" not in cfg for cfg in config.TARIFFS.values()), config.TARIFFS)

    if problems:
        print("FAIL:")
        for p in problems:
            print("  -", p)
        return 1

    print("OK: неплатёжные уведомления (Check/Fail/Authorized/Refund) доступ не выдают;")
    print("    чужая валюта и отсутствие TransactionId отклоняются; lifetime не понижается;")
    print("    дата окончания не уходит назад; триал не повторяется после удаления аккаунта;")
    print("    оплата звёздами отключена — pre_checkout всегда отклоняется")
    return 0


if __name__ == "__main__":
    sys.exit(main())
