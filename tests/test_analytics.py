"""Аналитика: события пишутся в ключевых местах, клиент может прислать только
просмотры экранов из белого списка, отчёт /stats видит только владелец,
события удаляются с аккаунтом и по сроку хранения."""
import os, pathlib, sys, tempfile
from datetime import date, timedelta
from unittest.mock import patch

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "an.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "1"
os.environ["OPENAI_API_KEY"] = "dummy"
os.environ.pop("RAILWAY_ENVIRONMENT", None); os.environ.pop("RAILWAY_PROJECT_ID", None)

from fastapi.testclient import TestClient
from backend import analytics, models as M, payment_providers, telegram_bot
from backend.database import init_db, SessionLocal
from backend.main import app

init_db()
c = TestClient(app)
fails = []


def chk(n, cond, x=""):
    if not cond:
        fails.append(n + ("  " + str(x) if x else ""))


def events(name=None, tid=1):
    with SessionLocal() as db:
        q = db.query(M.AppEvent).filter(M.AppEvent.telegram_id == tid)
        if name:
            q = q.filter(M.AppEvent.name == name)
        return q.count()


# ---------- 1. Открытие приложения ----------
c.post("/auth/verify")
chk("app_open записан", events("app_open") == 1, events("app_open"))

# ---------- 2. Клиентские события: только белый список ----------
chk("screen_subscription принят", c.post("/events", json={"name": "screen_subscription"}).status_code == 200)
c.post("/events", json={"name": "screen_payment"})
c.post("/events", json={"name": "paywall"})
c.post("/events", json={"name": "payment_success"})   # серверное — клиенту нельзя
c.post("/events", json={"name": "hacker_event"})
c.post("/events", json={"name": "x" * 5000})
chk("просмотры записаны", events("screen_subscription") == 1 and events("screen_payment") == 1 and events("paywall") == 1)
chk("серверное событие от клиента отклонено", events("payment_success") == 0)
chk("чужие имена отклонены", events() == 4, events())
for _ in range(40):
    c.post("/events", json={"name": "paywall"})
chk("частота ограничена (≤30 в минуту)", events("paywall") <= 30, events("paywall"))

# ---------- 3. Оплата: payment_success только за настоящие деньги ----------
with SessionLocal() as db:
    payment_providers.activate_premium(db, 1, "monthly", "yookassa", 699.0, "RUB", charge_id="yk:t1")
    payment_providers.activate_premium(db, 1, "monthly", "trial", 0, "RUB")
chk("оплата ЮKassa → payment_success", events("payment_success") == 1, events("payment_success"))

# ---------- 4. Отчёт владельцу ----------
with SessionLocal() as db:
    text = analytics.report(db, 7)
chk("отчёт: аудитория", "открывали сегодня: 1" in text, text)
chk("отчёт: воронка", "открыли подписку: 1" in text and "оплатили: 1" in text, text)
chk("отчёт: выручка", "выручка: 699 ₽" in text, text)

sent = []
with patch.object(telegram_bot, "_bot_api", lambda m, p: sent.append(p)):
    with SessionLocal() as db:
        telegram_bot.handle_update(db, {"message": {"message_id": 1, "from": {"id": 2}, "chat": {"id": 2},
                                                    "text": "/stats"}})
        chk("/stats чужому — молчание", sent == [], sent)
        telegram_bot.handle_update(db, {"message": {"message_id": 2, "from": {"id": 1}, "chat": {"id": 1},
                                                    "text": "/stats 30"}})
chk("/stats владельцу — отчёт за 30 дней", len(sent) == 1 and "за 30 дн." in sent[0]["text"], sent)

# ---------- 5. Срок хранения ----------
with SessionLocal() as db:
    old = (date.today() - timedelta(days=analytics.EVENTS_RETENTION_DAYS + 1)).isoformat()
    db.add(M.AppEvent(telegram_id=1, name="app_open", day=old)); db.commit()
    before = db.query(M.AppEvent).count()
    removed = analytics.purge_old(db)
    chk("старые события удаляются", removed == 1 and db.query(M.AppEvent).count() == before - 1, (removed, before))

# ---------- 6. Удаление аккаунта уносит события ----------
chk("удаление данных 200", c.delete("/account/data").status_code == 200)
chk("событий пользователя не осталось", events() == 0, events())

# ---------- 7. Сбой аналитики не ломает запрос ----------
with patch.object(analytics, "SessionLocal", side_effect=RuntimeError("db down")):
    r = c.post("/auth/verify")
chk("вход работает при сбое аналитики", r.status_code == 200, r.status_code)

if fails:
    print("FAIL:\n  " + "\n  ".join(fails)); sys.exit(1)
print("OK: открытие, белый список и лимит клиентских событий, payment_success только за деньги,\n"
      "    отчёт и /stats только владельцу, срок хранения, удаление с аккаунтом, сбой не ломает вход")
