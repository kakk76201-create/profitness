"""ЮKassa, боевой сценарий: чек по 54-ФЗ с e-mail, тестовые платежи по флагу,
return_url обратно в Telegram, проверка своего платежа клиентом
(GET /payment/yookassa/status/{id}) с выдачей доступа без вебхука."""
import importlib, os, pathlib, sys, tempfile
from unittest.mock import patch

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "ykf.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"
os.environ.pop("RAILWAY_ENVIRONMENT", None); os.environ.pop("RAILWAY_PROJECT_ID", None)
os.environ["PAYMENT_PROVIDER"] = "yookassa"
os.environ["YOOKASSA_SHOP_ID"] = "shop"; os.environ["YOOKASSA_SECRET_KEY"] = "secret"
os.environ["YOOKASSA_WEBHOOK_SECRET"] = "hook-secret-0123456789abcdef0123456789"
os.environ["YOOKASSA_RECEIPT"] = "1"; os.environ["YOOKASSA_VAT_CODE"] = "1"
os.environ["YOOKASSA_ALLOW_TEST"] = "1"
os.environ["BOT_USERNAME"] = "fitness_up_bot"; os.environ.pop("YOOKASSA_RETURN_URL", None)
os.environ["PRICE_MONTHLY_RUB"] = "699"; os.environ["PRICE_QUARTERLY_RUB"] = "1790"

from fastapi.testclient import TestClient
from backend import config, models as M
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
    def __init__(self, status_code, payload):
        self.status_code = status_code; self._payload = payload; self.text = str(payload)

    def json(self):
        return self._payload


def fake_httpx(captured):
    class H:
        @staticmethod
        def post(url, json=None, headers=None, auth=None, timeout=None):
            captured["json"] = json; captured["auth"] = auth
            return FakeResponse(200, {"id": "pay-flow-1", "status": "pending",
                                      "confirmation": {"type": "redirect", "confirmation_url": "https://yoomoney.ru/x"}})
    return H


def payment(pid, tid, tariff, value, status="succeeded", paid=True, test=False, meta_price=None):
    # meta_price — цена, зафиксированная НАМИ при создании; по умолчанию равна сумме.
    return {"id": pid, "status": status, "paid": paid, "test": test,
            "amount": {"value": value, "currency": "RUB"},
            "metadata": {"telegram_id": str(tid), "tariff": tariff, "price": meta_price or value}}


# ---------- 1. return_url ведёт обратно в Telegram ----------
chk("return_url = t.me/<бот>?startapp=paid", config.YOOKASSA_RETURN_URL == "https://t.me/fitness_up_bot?startapp=paid",
    config.YOOKASSA_RETURN_URL)

# ---------- 2. Статус подписки просит e-mail для чека ----------
st = c.get("/subscription/status").json()
chk("receipt_email_required", st.get("receipt_email_required") is True, st.get("receipt_email_required"))
chk("email пока пуст", st.get("email") in (None, ""))

# ---------- 3. Создание платежа: без e-mail 400, с e-mail — чек в запросе ----------
captured = {}
with patch.object(yookassa, "httpx", fake_httpx(captured)):
    r = c.post("/payment/yookassa/create", json={"tariff": "monthly"})
    chk("без e-mail → 400 email_required", r.status_code == 400 and r.json()["detail"]["error"] == "email_required", r.text[:200])
    r = c.post("/payment/yookassa/create", json={"tariff": "monthly", "email": "not-an-email"})
    chk("мусорный e-mail → 400", r.status_code == 400, r.status_code)
    r = c.post("/payment/yookassa/create", json={"tariff": "monthly", "email": "  User@Example.com "})
    chk("с e-mail → 200", r.status_code == 200, r.text[:200])
    chk("payment_id и ссылка", r.json().get("payment_id") == "pay-flow-1" and r.json().get("confirmation_url"))
    rc = captured["json"].get("receipt") or {}
    chk("чек: e-mail нормализован", rc.get("customer", {}).get("email") == "user@example.com", rc.get("customer"))
    item = (rc.get("items") or [{}])[0]
    chk("чек: одна позиция на всю сумму", item.get("amount") == {"value": "699.00", "currency": "RUB"}, item)
    chk("чек: НДС/предмет/способ", item.get("vat_code") == 1 and item.get("payment_subject") == "service"
        and item.get("payment_mode") == "full_payment", item)
    chk("чек: quantity 1.00", item.get("quantity") == "1.00")
    chk("чек: без tax_system_code по умолчанию", "tax_system_code" not in rc)
    chk("return_url в платеже", captured["json"]["confirmation"]["return_url"] == config.YOOKASSA_RETURN_URL)
    # e-mail запомнен: второй раз без него
    st = c.get("/subscription/status").json()
    chk("e-mail сохранён в профиле", st.get("email") == "user@example.com", st.get("email"))
    r = c.post("/payment/yookassa/create", json={"tariff": "quarterly"})
    chk("повтор без e-mail берёт сохранённый", r.status_code == 200 and captured["json"]["receipt"]["customer"]["email"] == "user@example.com")

# ---------- 4. Проверка своего платежа клиентом ----------
chk("битый id → 404", c.get("/payment/yookassa/status/../x").status_code in (404, 400))
with patch.object(yookassa, "fetch_payment", lambda pid: payment(pid, 1, "monthly", "699.00", status="pending", paid=False)):
    r = c.get("/payment/yookassa/status/pay-flow-1")
    chk("pending: 200, не активирован", r.status_code == 200 and r.json()["status"] == "pending"
        and r.json()["activated"] is False and r.json()["is_premium"] is False, r.text[:200])
with patch.object(yookassa, "fetch_payment", lambda pid: payment(pid, 1, "monthly", "699.00", test=True)):
    r = c.get("/payment/yookassa/status/pay-flow-1")
    chk("оплачен (тестовый, ALLOW_TEST=1): доступ выдан", r.status_code == 200 and r.json()["activated"] is True
        and r.json()["is_premium"] is True, r.text[:200])
    r = c.get("/payment/yookassa/status/pay-flow-1")
    chk("повтор: already, без второй активации", r.status_code == 200 and r.json()["activated"] is True)
with SessionLocal() as db:
    rows = db.query(M.Payment).filter(M.Payment.charge_id == "yk:pay-flow-1").all()
    chk("одна запись Payment на платёж", len(rows) == 1, len(rows))
    u = db.query(M.User).filter(M.User.telegram_id == 1).first()
    chk("подписка monthly", u.subscription_type == "monthly" and u.subscription_until is not None)
with patch.object(yookassa, "fetch_payment", lambda pid: payment(pid, 999, "monthly", "699.00")):
    chk("чужой платёж → 404", c.get("/payment/yookassa/status/pay-other").status_code == 404)
# Сумма меньше цены, зафиксированной при создании (metadata.price задаём мы).
with patch.object(yookassa, "fetch_payment", lambda pid: payment(pid, 1, "yearly", "1.00", meta_price="5590.00")):
    r = c.get("/payment/yookassa/status/pay-cheap")
    chk("недоплата не активирует", r.status_code == 200 and r.json()["activated"] is False, r.text[:120])

# ---------- 5. Вебхук работает через тот же _yookassa_settle ----------
with patch.object(yookassa, "fetch_payment", lambda pid: payment(pid, 1, "quarterly", "1790.00")):
    r = c.post("/payment/yookassa/webhook/" + os.environ["YOOKASSA_WEBHOOK_SECRET"],
               json={"event": "payment.succeeded", "object": {"id": "pay-hook-1"}})
    chk("вебхук 200", r.status_code == 200, r.text[:100])
with SessionLocal() as db:
    chk("вебхук записал платёж", db.query(M.Payment).filter(M.Payment.charge_id == "yk:pay-hook-1").count() == 1)

# ---------- 6. Тестовый платёж без флага не проходит ----------
config.YOOKASSA_ALLOW_TEST = False
with patch.object(yookassa, "fetch_payment", lambda pid: payment(pid, 1, "monthly", "699.00", test=True)):
    # DEV_MODE в тесте включён (ALLOW_INSECURE_AUTH=1) и сам разрешает тестовые
    # платежи — имитируем прод, выключая его.
    main.DEV_MODE = False
    r = c.get("/payment/yookassa/status/pay-test-3")
    chk("тестовый в проде: отклонён", r.status_code == 200 and r.json()["activated"] is False)
    main.DEV_MODE = True

if fails:
    print("FAIL:\n  " + "\n  ".join(fails)); sys.exit(1)
print("OK: return_url в Telegram; чек с e-mail (валидация, нормализация, память в профиле);\n"
      "    статус платежа клиентом выдаёт доступ идемпотентно, чужой — 404, недоплата — нет;\n"
      "    вебхук через общий settle; тестовые платежи только по флагу")
