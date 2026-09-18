"""Тестовый платёж владельца (3 ₽): виден и оплачивается только OWNER_ID,
на витрину не попадает, подписку не меняет, записывается в журнал один раз,
вебхук сообщает владельцу, что уведомление дошло."""
import os, pathlib, sys, tempfile
from unittest.mock import patch

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "ykt.db").replace("\\", "/")
# dev-пользователь авторизации — telegram_id=1; он же владелец.
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "1"
os.environ["OPENAI_API_KEY"] = "dummy"
os.environ.pop("RAILWAY_ENVIRONMENT", None); os.environ.pop("RAILWAY_PROJECT_ID", None)
os.environ["PAYMENT_PROVIDER"] = "yookassa"
os.environ["YOOKASSA_SHOP_ID"] = "shop"; os.environ["YOOKASSA_SECRET_KEY"] = "secret"
os.environ["YOOKASSA_WEBHOOK_SECRET"] = "hook-secret-0123456789abcdef0123456789"
os.environ.pop("YOOKASSA_RECEIPT", None); os.environ.pop("TEST_PAYMENT_RUB", None)

from fastapi.testclient import TestClient
from backend import config, models as M, telegram_bot
from backend.database import init_db, SessionLocal
import backend.yookassa as yookassa
import backend.main as main

init_db()
c = TestClient(main.app)
fails = []


def chk(n, cond, x=""):
    if not cond:
        fails.append(n + ("  " + str(x) if x else ""))


class FakeResponse:
    def __init__(self, payload):
        self.status_code = 200; self._payload = payload; self.text = str(payload)

    def json(self):
        return self._payload


captured = {}


class FakeHttpx:
    @staticmethod
    def post(url, json=None, headers=None, auth=None, timeout=None):
        captured["json"] = json
        return FakeResponse({"id": "pay-test-1", "status": "pending",
                             "confirmation": {"type": "redirect", "confirmation_url": "https://yoomoney.ru/t"}})


def paid(pid, tid, tariff, value):
    return {"id": pid, "status": "succeeded", "paid": True, "test": False,
            "amount": {"value": value, "currency": "RUB"},
            "metadata": {"telegram_id": str(tid), "tariff": tariff, "price": value}}


# ---------- 1. По умолчанию 3 ₽, только владельцу ----------
chk("по умолчанию 3 ₽", config.TEST_PAYMENT_RUB == 3.0, config.TEST_PAYMENT_RUB)
st = c.get("/subscription/status").json()
chk("владелец видит сумму теста", st.get("test_payment_price") == 3.0, st.get("test_payment_price"))
chk("тест не на витрине", "test" not in (st.get("tariffs") or {}) and "test" not in (st.get("card_prices") or {}))
chk("владелец — вечный доступ", st.get("is_premium") is True)

# ---------- 2. Создание: владельцу 200 на 3.00 ₽, чужому 403 ----------
with patch.object(yookassa, "httpx", FakeHttpx):
    r = c.post("/payment/yookassa/create", json={"tariff": "test"})
    chk("владелец создаёт тест", r.status_code == 200, r.text[:200])
    chk("сумма 3.00 RUB", captured["json"]["amount"] == {"value": "3.00", "currency": "RUB"}, captured["json"]["amount"])
    chk("описание «тестовый платёж»", "тестовый платёж" in captured["json"]["description"], captured["json"]["description"])
    config.OWNER_ID = 999
    r = c.post("/payment/yookassa/create", json={"tariff": "test"})
    chk("не владелец → 403", r.status_code == 403, r.status_code)
    st = c.get("/subscription/status").json()
    chk("не владелец не видит сумму", st.get("test_payment_price") is None)
    config.OWNER_ID = 1

# ---------- 3. Оплата: запись в журнал, подписка не меняется ----------
with SessionLocal() as db:
    before = db.query(M.User).filter(M.User.telegram_id == 1).first()
    b_type, b_until = before.subscription_type, before.subscription_until
with patch.object(yookassa, "fetch_payment", lambda pid: paid(pid, 1, "test", "3.00")):
    r = c.get("/payment/yookassa/status/pay-test-1")
    chk("status: activated, tariff=test", r.status_code == 200 and r.json()["activated"] is True
        and r.json()["tariff"] == "test", r.text[:200])
    sent = []
    with patch.object(telegram_bot, "_bot_api", lambda m, p: sent.append(p)):
        r = c.post("/payment/yookassa/webhook/" + os.environ["YOOKASSA_WEBHOOK_SECRET"],
                   json={"event": "payment.succeeded", "object": {"id": "pay-test-1"}})
    chk("вебхук 200", r.status_code == 200)
    chk("вебхук сообщил владельцу о тесте (даже после status)", len(sent) == 1 and "вебхук работает" in sent[0]["text"], sent)
with SessionLocal() as db:
    rows = db.query(M.Payment).filter(M.Payment.charge_id == "yk:pay-test-1").all()
    chk("одна запись в журнале", len(rows) == 1, len(rows))
    chk("запись помечена test", rows and rows[0].subscription_type == "test" and rows[0].amount == 3.0)
    after = db.query(M.User).filter(M.User.telegram_id == 1).first()
    chk("подписка владельца не изменилась", (after.subscription_type, after.subscription_until) == (b_type, b_until),
        (after.subscription_type, after.subscription_until))

# ---------- 4. Недоплата по тесту не проходит ----------
with patch.object(yookassa, "fetch_payment", lambda pid: paid(pid, 1, "test", "1.00") | {"metadata": {"telegram_id": "1", "tariff": "test", "price": "3.00"}}):
    r = c.get("/payment/yookassa/status/pay-test-2")
    chk("1 ₽ вместо 3 — не засчитан", r.status_code == 200 and r.json()["activated"] is False, r.text[:120])

# ---------- 5. TEST_PAYMENT_RUB=0 выключает ----------
config.TEST_PAYMENT_RUB = 0
chk("выключен: тариф неизвестен", config.tariff_for("test") is None and config.rub_price_for("test") is None)
with patch.object(yookassa, "httpx", FakeHttpx):
    chk("выключен: создание 403", c.post("/payment/yookassa/create", json={"tariff": "test"}).status_code == 403)
chk("выключен: сумма не приходит", c.get("/subscription/status").json().get("test_payment_price") is None)

if fails:
    print("FAIL:\n  " + "\n  ".join(fails)); sys.exit(1)
print("OK: тест 3 ₽ только владельцу, не на витрине, подписку не меняет, одна запись,\n"
      "    вебхук сообщает о доставке, недоплата не проходит, выключается TEST_PAYMENT_RUB=0")
