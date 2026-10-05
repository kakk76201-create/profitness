"""Автопродление (рекуррентные платежи ЮKassa): согласие, карта, списание, отвязка.

Деньги — поэтому проверяем всё, что может стоить человеку лишнего списания:
без флага и без галочки карта не сохраняется; магазину не разрешили — платёж
идёт без сохранения; продление списывается один раз за период, даже если
процесс упал между запросом и записью (тот же ключ идемпотентности);
временный отказ — не больше трёх попыток раз в сутки; окончательный отказ и
отвязка картой человека — списаний больше нет; вечная/пробная подписка и
владелец не продлеваются; удаление аккаунта убирает карты.
"""
import json, os, pathlib, sys, tempfile
from datetime import datetime, timedelta
from unittest.mock import patch

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "ap.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "999"
os.environ["OPENAI_API_KEY"] = "dummy"
os.environ.pop("RAILWAY_ENVIRONMENT", None); os.environ.pop("RAILWAY_PROJECT_ID", None)
os.environ["PAYMENT_PROVIDER"] = "yookassa"
os.environ["YOOKASSA_SHOP_ID"] = "shop"; os.environ["YOOKASSA_SECRET_KEY"] = "secret"
os.environ["YOOKASSA_WEBHOOK_SECRET"] = "hook-secret-0123456789abcdef0123456789"
os.environ.pop("YOOKASSA_RECEIPT", None); os.environ["YOOKASSA_ALLOW_TEST"] = "1"
os.environ["YOOKASSA_AUTOPAY"] = "1"
os.environ["PRICE_MONTHLY_RUB"] = "699"
os.environ["TEST_PAYMENT_RUB"] = "3"

from fastapi.testclient import TestClient
from backend import autopay, config, main as main_mod, models as M, telegram_bot, yookassa
from backend.database import init_db, SessionLocal
from backend.main import app

init_db()
c = TestClient(app)
fails = []


def chk(name, cond, extra=""):
    if not cond:
        fails.append(name + ("  " + str(extra) if extra else ""))


class Resp:
    def __init__(self, status, data=None, text=""):
        self.status_code, self._d, self.text = status, data, text or json.dumps(data or {})

    def json(self):
        return self._d


class FakeYK:
    """Подделка API ЮKassa: записывает запросы, отвечает по сценарию."""

    def __init__(self):
        self.posts, self.script, self.payments = [], [], {}

    def post(self, url, json=None, headers=None, auth=None, timeout=None):
        self.posts.append({"json": json, "key": (headers or {}).get("Idempotence-Key")})
        if self.script:
            return self.script.pop(0)
        return Resp(200, {"id": f"pay-{len(self.posts)}", "status": "pending",
                          "confirmation": {"type": "redirect", "confirmation_url": "https://yoomoney.ru/x"}})

    def get(self, url, auth=None, timeout=None, headers=None):
        pid = url.rsplit("/", 1)[-1]
        return Resp(200, self.payments.get(pid, {"id": pid, "status": "pending"}))


def card_payment(pid, tid, tariff, value, autopay_meta=True, saved=True, status="succeeded"):
    meta = {"telegram_id": str(tid), "tariff": tariff, "price": value}
    if autopay_meta:
        meta["autopay"] = "1"
    return {"id": pid, "status": status, "paid": status == "succeeded", "test": False,
            "amount": {"value": value, "currency": "RUB"}, "metadata": meta,
            "payment_method": {"type": "bank_card", "id": f"pm-{pid}", "saved": saved,
                               "card": {"first6": "220220", "last4": "4444", "expiry_month": "07",
                                        "expiry_year": "2029", "card_type": "Mir"}}}


def renewal(pid, tid, value, status, reason=None, period_key=None):
    p = {"id": pid, "status": status, "paid": status == "succeeded", "test": False,
         "amount": {"value": value, "currency": "RUB"},
         "metadata": {"telegram_id": str(tid), "tariff": "monthly", "price": value,
                      "renewal": "1", "period_key": period_key}}
    if reason:
        p["cancellation_details"] = {"party": "payment_network", "reason": reason}
    return p


said = []
bot_patch = patch.object(telegram_bot, "_bot_api", lambda m, p: said.append(p) or {"message_id": 1})
bot_patch.start()

c.post("/consent", json={"pd": True, "terms": True, "health": True, "cross_border": True})
c.get("/subscription/status")  # создаёт dev-пользователя (id=1)

# ---------- 1. Создание платежа: согласие и флаг ---------- #
fake = FakeYK()
with patch.object(yookassa, "httpx", fake):
    with patch.object(config, "YOOKASSA_AUTOPAY", False):
        r = c.post("/payment/yookassa/create", json={"tariff": "monthly", "autopay": True})
        chk("флаг выключен — карту не сохраняем", "save_payment_method" not in fake.posts[-1]["json"]
            and r.json()["autopay"] is False, fake.posts[-1]["json"])
        chk("флаг выключен — галочку не предлагаем", c.get("/subscription/status").json()["autopay_available"] is False)
    r = c.post("/payment/yookassa/create", json={"tariff": "monthly"})
    chk("без галочки — не сохраняем", "save_payment_method" not in fake.posts[-1]["json"], fake.posts[-1]["json"])
    r = c.post("/payment/yookassa/create", json={"tariff": "monthly", "autopay": True})
    body = fake.posts[-1]["json"]
    chk("с галочкой — save_payment_method", body.get("save_payment_method") is True
        and body["metadata"].get("autopay") == "1" and r.json()["autopay"] is True, body)
    chk("ключ идемпотентности отличается от платежа без сохранения",
        fake.posts[-1]["key"] != fake.posts[-2]["key"], (fake.posts[-1]["key"], fake.posts[-2]["key"]))
    # Магазину ещё не разрешили сохранять карты — платёж всё равно создаётся.
    fake.script = [Resp(403, {"type": "error", "code": "forbidden"}, text='{"description":"Forbidden to save payment method"}'),
                   Resp(200, {"id": "pay-nosave", "status": "pending",
                              "confirmation": {"type": "redirect", "confirmation_url": "https://yoomoney.ru/y"}})]
    r = c.post("/payment/yookassa/create", json={"tariff": "monthly", "autopay": True})
    chk("403 на сохранение — платёж без сохранения", r.status_code == 200 and r.json()["autopay"] is False
        and "save_payment_method" not in fake.posts[-1]["json"], (r.status_code, r.text[:200]))

# ---------- 2. Успешная оплата с согласием — карта сохранена ---------- #
with SessionLocal() as db:
    res = main_mod._yookassa_settle(db, card_payment("pay-aaa", 1, "monthly", "699.00"), "pay-aaa", "test")
    chk("оплата активирована", res == "activated", res)
    m = db.query(M.PaymentMethod).filter(M.PaymentMethod.telegram_id == 1).first()
    chk("карта сохранена", m is not None and m.active and m.last4 == "4444" and m.method_id == "pm-pay-aaa")
    chk("согласие зафиксировано", m.consented_at is not None and m.consent_version == config.LEGAL_VERSION)
    chk("сумма согласия", m.autopay_amount == 699.0 and m.autopay_tariff == "monthly", (m.autopay_amount, m.autopay_tariff))
    # Без согласия (нет metadata.autopay) сохранённая ЮKassa карта у нас не появляется.
    res = main_mod._yookassa_settle(db, card_payment("pay-bbb", 1, "monthly", "699.00", autopay_meta=False), "pay-bbb", "test")
    chk("без согласия карта не сохраняется", db.query(M.PaymentMethod).count() == 1, db.query(M.PaymentMethod).count())

lst = c.get("/payment/methods").json()
chk("в профиле видна карта", len(lst["methods"]) == 1 and lst["methods"][0]["title"] == "МИР •••• 4444", lst)
chk("следующее списание указано", lst["methods"][0]["next_charge_at"] is not None)
chk("на экране подписки — карта", (c.get("/subscription/status").json().get("autopay_card") or {}).get("last4") == "4444")

# ---------- 3. Проход планировщика: предупреждение, списание один раз ---------- #
def set_until(hours_from_now, tid=1, typ="monthly"):
    with SessionLocal() as db:
        u = db.query(M.User).filter(M.User.telegram_id == tid).first()
        u.subscription_type = typ
        u.subscription_until = datetime.utcnow() + timedelta(hours=hours_from_now)
        db.commit()
        return u.subscription_until


def run(now=None):
    with SessionLocal() as db:
        return autopay.run(db, now)


def set_period(days, hours=20, tid=1, typ="monthly"):
    """Срок подписки через days суток + hours часов — свой период на раздел."""
    return set_until(days * 24 + hours, tid=tid, typ=typ)


def run_at(until, hours_before=20):
    """Проход планировщика «за hours_before часов до конца подписки»."""
    return run(until - timedelta(hours=hours_before))


until = set_until(40)
fake = FakeYK()
said.clear()
with patch.object(yookassa, "httpx", fake):
    stats = run()
chk("за 40 ч: предупреждение, без списания", stats["noticed"] == 1 and not fake.posts, (stats, fake.posts))
chk("предупреждение называет сумму и карту", any("699" in p.get("text", "") and "4444" in p.get("text", "") for p in said), said)
with patch.object(yookassa, "httpx", fake):
    run()
chk("второе предупреждение не шлётся",
    sum(("Напоминание" in p.get("text", "")) or ("Reminder" in p.get("text", "")) for p in said) == 1, said)

until = set_until(20)
period = until.date().isoformat()
fake = FakeYK()
fake.script = [Resp(200, renewal("pay-r1", 1, "699.00", "succeeded", period_key=period))]
said.clear()
with patch.object(yookassa, "httpx", fake):
    run()
req = fake.posts[0]["json"] if fake.posts else {}
chk("списание: сохранённый способ, без подтверждения", req.get("payment_method_id") == "pm-pay-aaa"
    and "confirmation" not in req and req.get("capture") is True, req)
chk("списание: сумма согласия", req.get("amount") == {"value": "699.00", "currency": "RUB"}, req.get("amount"))
with SessionLocal() as db:
    u = db.query(M.User).filter(M.User.telegram_id == 1).first()
    chk("подписка продлена на 30 дней от текущего срока",
        abs((u.subscription_until - (until + timedelta(days=30))).total_seconds()) < 5, (u.subscription_until, until))
    row = db.query(M.AutopayCharge).filter(M.AutopayCharge.payment_id == "pay-r1").first()
    chk("журнал: succeeded", row is not None and row.status == "succeeded", row.status if row else None)
chk("человеку — «продлена»", any(("продлена" in p.get("text", "")) or ("renewed" in p.get("text", "")) for p in said), said)
n = len(fake.posts)
with patch.object(yookassa, "httpx", fake):
    with SessionLocal() as db:
        u = db.query(M.User).filter(M.User.telegram_id == 1).first()
        meth = db.query(M.PaymentMethod).filter(M.PaymentMethod.telegram_id == 1).first()
        res = autopay.charge(db, u, meth, "monthly", 699.0, period)
chk("тот же период второй раз не списывается", res == "already" and len(fake.posts) == n, (res, len(fake.posts)))
# Вебхук о том же платеже — без второго продления.
with SessionLocal() as db:
    before = db.query(M.User).filter(M.User.telegram_id == 1).first().subscription_until
    res = main_mod._yookassa_settle(db, renewal("pay-r1", 1, "699.00", "succeeded", period_key=period), "pay-r1", "webhook")
    after = db.query(M.User).filter(M.User.telegram_id == 1).first().subscription_until
chk("повторный вебхук не продлевает второй раз", res == "activated" and before == after, (before, after))

# ---------- 4. Падение между запросом и записью — тот же ключ ---------- #
until = set_period(10)
period = until.date().isoformat()
with SessionLocal() as db:
    db.add(M.AutopayCharge(telegram_id=1, method_id=1, period_key=period, attempt=1,
                           idempotence_key=f"autopay:1:{period}:1", status="pending",
                           amount=699.0, tariff="monthly"))
    db.commit()
fake = FakeYK()
fake.script = [Resp(200, renewal("pay-r2", 1, "699.00", "succeeded", period_key=period))]
with patch.object(yookassa, "httpx", fake):
    run_at(until)
chk("после падения — повтор с тем же ключом", len(fake.posts) == 1 and fake.posts[0]["key"] == f"autopay:1:{period}:1",
    [p["key"] for p in fake.posts])

# ---------- 5. Временный отказ: повтор через сутки, не больше трёх ---------- #
until = set_period(50)
period = until.date().isoformat()
fake = FakeYK()
for i in range(5):
    fake.script.append(Resp(200, renewal(f"pay-f{i}", 1, "699.00", "canceled", "insufficient_funds", period)))
said.clear()
with patch.object(yookassa, "httpx", fake):
    run_at(until)
    run_at(until)  # тот же день — без новой попытки
    chk("в тот же день вторая попытка не делается", len(fake.posts) == 1, len(fake.posts))
    with SessionLocal() as db:
        for row in db.query(M.AutopayCharge).filter(M.AutopayCharge.period_key == period).all():
            row.updated_at = datetime.utcnow() - timedelta(hours=25)
        db.commit()
    run_at(until)
    with SessionLocal() as db:
        for row in db.query(M.AutopayCharge).filter(M.AutopayCharge.period_key == period).all():
            row.updated_at = datetime.utcnow() - timedelta(hours=25)
        db.commit()
    run_at(until)
    with SessionLocal() as db:
        for row in db.query(M.AutopayCharge).filter(M.AutopayCharge.period_key == period).all():
            row.updated_at = datetime.utcnow() - timedelta(hours=25)
        db.commit()
    run_at(until)
chk("не больше трёх попыток за период", len(fake.posts) == 3, len(fake.posts))
chk("разные ключи на попытки", len({p["key"] for p in fake.posts}) == 3)
with SessionLocal() as db:
    chk("временный отказ карту не отвязывает",
        db.query(M.PaymentMethod).filter(M.PaymentMethod.telegram_id == 1, M.PaymentMethod.active.is_(True)).count() == 1)

# ---------- 6. Окончательный отказ: карта отвязана ---------- #
until = set_period(90)
period = until.date().isoformat()
fake = FakeYK()
fake.script = [Resp(200, renewal("pay-xxx", 1, "699.00", "canceled", "permission_revoked", period))]
said.clear()
with patch.object(yookassa, "httpx", fake):
    run_at(until)
with SessionLocal() as db:
    m = db.query(M.PaymentMethod).filter(M.PaymentMethod.telegram_id == 1).first()
    chk("permission_revoked — карта отвязана", m.active is False and m.revoke_reason == "permission_revoked", m.revoke_reason)
chk("человеку — «карта отвязана»", any(("отвязана" in p.get("text", "")) or ("unlinked" in p.get("text", "")) for p in said), said)
fake = FakeYK()
with patch.object(yookassa, "httpx", fake):
    run_at(until)
chk("после отвязки списаний нет", not fake.posts, fake.posts)

# ---------- 7. Отвязка человеком и гонка «отвязал за минуту до списания» ---------- #
with SessionLocal() as db:
    main_mod._yookassa_settle(db, card_payment("pay-ccc", 1, "monthly", "699.00"), "pay-ccc", "test")
lst = c.get("/payment/methods").json()
mid = lst["methods"][0]["id"]
until = set_period(130)
with SessionLocal() as db:
    u = db.query(M.User).filter(M.User.telegram_id == 1).first()
    stale = db.get(M.PaymentMethod, mid)
    _ = stale.active  # загружен как активный
    chk("отвязка 200", c.delete(f"/payment/methods/{mid}").status_code == 200)
    fake = FakeYK()
    with patch.object(yookassa, "httpx", fake):
        res = autopay.charge(db, u, stale, "monthly", 699.0, until.date().isoformat())
chk("отвязка перед списанием побеждает", res == "revoked" and not fake.posts, (res, fake.posts))
chk("в профиле карт нет", c.get("/payment/methods").json()["methods"] == [])
chk("чужую/несуществующую — 404", c.delete("/payment/methods/99999").status_code == 404)

# ---------- 8. Кого не продлеваем ---------- #
with SessionLocal() as db:
    main_mod._yookassa_settle(db, card_payment("pay-ddd", 1, "monthly", "699.00"), "pay-ddd", "test")
for typ in ("lifetime", "trial", "free"):
    u_until = set_period(170, typ=typ)
    fake = FakeYK()
    with patch.object(yookassa, "httpx", fake):
        run_at(u_until)
    chk(f"{typ} не продлевается", not fake.posts, typ)

# ---------- 9. Чек обязателен, e-mail нет — не списываем молча ---------- #
r_until = set_period(210)
fake = FakeYK()
alerts = []
with patch.object(yookassa, "httpx", fake), patch.object(config, "YOOKASSA_RECEIPT", True), \
        patch.object(telegram_bot, "alert_owner", lambda text, prefix="": alerts.append(text)):
    run_at(r_until)
chk("без e-mail для чека — без списания и с алертом", not fake.posts and alerts, (fake.posts, alerts))

# ---------- 10. /autopaytest владельца ---------- #
with SessionLocal() as db:
    db.add(M.User(telegram_id=999, username="owner", language="ru", subscription_type="lifetime"))
    db.commit()
    main_mod._yookassa_settle(db, card_payment("pay-ooo", 999, "test", "3.00"), "pay-ooo", "test")
    chk("тестовый платёж владельца сохраняет карту",
        db.query(M.PaymentMethod).filter(M.PaymentMethod.telegram_id == 999, M.PaymentMethod.active.is_(True)).count() == 1)
fake = FakeYK()
fake.script = [Resp(200, {"id": "pay-ttt", "status": "succeeded", "paid": True, "test": False,
                          "amount": {"value": "3.00", "currency": "RUB"},
                          "metadata": {"telegram_id": "999", "tariff": "test", "price": "3.00", "renewal": "1"}})]
said.clear()
with patch.object(yookassa, "httpx", fake):
    with SessionLocal() as db:
        telegram_bot.handle_update(db, {"message": {"message_id": 1, "from": {"id": 999}, "chat": {"id": 999},
                                                    "text": "/autopaytest"}})
        owner = db.query(M.User).filter(M.User.telegram_id == 999).first()
        chk("тест: lifetime не тронут", owner.subscription_type == "lifetime")
        chk("тест: платёж записан", db.query(M.Payment).filter(M.Payment.charge_id == "yk:pay-ttt").count() == 1)
chk("тест: 3 ₽ без подтверждения", fake.posts and fake.posts[0]["json"]["amount"]["value"] == "3.00"
    and "confirmation" not in fake.posts[0]["json"], fake.posts)
chk("тест: владельцу «прошло»", any("Тестовое автосписание прошло" in p.get("text", "")
                                   or "Test auto-charge succeeded" in p.get("text", "") for p in said), said)
with SessionLocal() as db:
    telegram_bot.handle_update(db, {"message": {"message_id": 2, "from": {"id": 1}, "chat": {"id": 1},
                                                "text": "/autopaytest"}})
chk("/autopaytest не владельцу — молчание", not any(p.get("chat_id") == 1 and "autopay" in p.get("text", "").lower() for p in said))

# ---------- 11. Удаление аккаунта убирает карты ---------- #
with SessionLocal() as db:
    data = main_mod._collect_export(db, 1)
chk("выгрузка: карты", len(data.get("payment_methods") or []) >= 1)
chk("удаление аккаунта 200", c.delete("/account/data").status_code == 200)
with SessionLocal() as db:
    chk("карт удалённого нет", db.query(M.PaymentMethod).filter(M.PaymentMethod.telegram_id == 1).count() == 0)
    chk("журнала автосписаний удалённого нет", db.query(M.AutopayCharge).filter(M.AutopayCharge.telegram_id == 1).count() == 0)

bot_patch.stop()
if fails:
    print("FAIL:\n  " + "\n  ".join(fails)); sys.exit(1)
print("OK: без флага и галочки карта не сохраняется; 403 — платёж без сохранения; карта с согласием;\n"
      "    предупреждение один раз; списание суммы согласия один раз за период (и после вебхука);\n"
      "    падение — тот же ключ; временный отказ ≤3 раз раз в сутки; окончательный — отвязка;\n"
      "    отвязка человеком побеждает гонку; lifetime/trial/free не продлеваются; чек без e-mail —\n"
      "    без списания; /autopaytest только владельцу; удаление аккаунта убирает карты")
