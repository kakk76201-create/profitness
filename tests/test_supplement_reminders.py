"""Добавки: переключатель «напоминать» реально включает рассылку.

Раньше Supplement.reminder_enabled был декоративным — рассылка шла только по
SupplementReminder. Теперь сервер держит связь добавка ↔ напоминание в её
время: одинаковое время — одно сообщение, выключение/удаление не оставляет
пустых напоминаний, старые напоминания показываются честно. ИИ-совет по
добавкам знает, что человек уже принимает, и не повторяет это."""
import os, pathlib, sys, tempfile
from unittest.mock import patch

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "supp.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "1"
os.environ["OPENAI_API_KEY"] = "dummy"
os.environ.pop("RAILWAY_ENVIRONMENT", None); os.environ.pop("RAILWAY_PROJECT_ID", None)

from fastapi.testclient import TestClient
from backend import ai_service, models as M, notifications
from backend.database import init_db, SessionLocal
from backend.main import app

init_db()
c = TestClient(app)
fails = []


def chk(n, cond, x=""):
    if not cond:
        fails.append(n + ("  " + str(x) if x else ""))


def reminders():
    """{время: [id добавок]} по включённым напоминаниям пользователя 1."""
    with SessionLocal() as db:
        out = {}
        for r in db.query(M.SupplementReminder).filter(M.SupplementReminder.telegram_id == 1).all():
            ids = sorted(i.supplement_id for i in db.query(M.SupplementReminderItem)
                         .filter(M.SupplementReminderItem.reminder_id == r.id).all())
            out.setdefault(r.time, []).extend(ids)
        return out


def add(name, time=None, remind=False, dosage="5 г"):
    body = {"name": name, "dosage": dosage, "reminder_enabled": remind}
    if time is not None:
        body["intake_time"] = time
    return c.post("/supplement/add", json=body)


# ---------- 1. Добавка с напоминанием → напоминание в её время ----------
r = add("Креатин", "9:00", True)
chk("add 200", r.status_code == 200, r.text[:200])
cr = r.json()
chk("время нормализовано", cr["intake_time"] == "09:00", cr)
chk("флаг включён", cr["reminder_enabled"] is True)
chk("напоминание создано", reminders() == {"09:00": [cr["id"]]}, reminders())

# ---------- 2. Вторая добавка в то же время → то же сообщение ----------
om = add("Омега-3", "09:00", True).json()
chk("одно напоминание на время", reminders() == {"09:00": sorted([cr["id"], om["id"]])}, reminders())

# ---------- 3. Без времени напоминать нельзя; без напоминания — можно ----------
r = add("Магний", None, True)
chk("напоминание без времени → 400", r.status_code == 400 and "время" in r.json()["detail"], r.text[:200])
mg = add("Магний", "22:00", False).json()
chk("без напоминания — нет рассылки", mg["reminder_enabled"] is False and "22:00" not in reminders())
chk("мусорное время → 400", add("Цинк", "25:99", False).status_code == 400)

# ---------- 4. Переключатель в списке ----------
r = c.patch(f"/supplement/{mg['id']}", json={"reminder_enabled": True})
chk("patch вкл → 200", r.status_code == 200 and r.json()["reminder_enabled"] is True, r.text[:200])
chk("появилось 22:00", reminders().get("22:00") == [mg["id"]], reminders())
r = c.patch(f"/supplement/{om['id']}", json={"reminder_enabled": False})
chk("patch выкл", r.json()["reminder_enabled"] is False)
chk("омега ушла из 09:00, креатин остался", reminders().get("09:00") == [cr["id"]], reminders())
r = c.patch(f"/supplement/{cr['id']}", json={"intake_time": "08:30"})
chk("смена времени переносит напоминание", reminders().get("08:30") == [cr["id"]] and "09:00" not in reminders(),
    reminders())
r = c.patch(f"/supplement/{cr['id']}", json={"intake_time": ""})
chk("убрать время при включённом напоминании → 400", r.status_code == 400, r.status_code)
chk("чужая/несуществующая → 404", c.patch("/supplement/99999", json={"reminder_enabled": True}).status_code == 404)

# ---------- 5. Список показывает фактическое состояние ----------
items = {s["id"]: s for s in c.get("/supplement/list").json()["items"]}
chk("список: креатин вкл", items[cr["id"]]["reminder_enabled"] is True)
chk("список: омега выкл", items[om["id"]]["reminder_enabled"] is False)
# Старое напоминание из отдельной формы: флаг добавки False, но она в рассылке.
with SessionLocal() as db:
    legacy = M.Supplement(telegram_id=1, name="ZMA", type="", dosage="1 капс", intake_time=None, reminder_enabled=False)
    db.add(legacy); db.flush()
    rem = M.SupplementReminder(telegram_id=1, label="Ночь", time="23:00", enabled=True)
    db.add(rem); db.flush()
    db.add(M.SupplementReminderItem(reminder_id=rem.id, supplement_id=legacy.id)); db.commit()
    legacy_id = legacy.id
items = {s["id"]: s for s in c.get("/supplement/list").json()["items"]}
chk("старое напоминание видно включённым", items[legacy_id]["reminder_enabled"] is True, items[legacy_id])
chk("время подставлено из напоминания", items[legacy_id]["intake_time"] == "23:00", items[legacy_id])
r = c.patch(f"/supplement/{legacy_id}", json={"reminder_enabled": False})
chk("старое можно выключить", r.status_code == 200 and "23:00" not in reminders(), reminders())

# Две добавки в одно время: выключить первую — сообщение остаётся со второй,
# выключить вторую — напоминание исчезает.
a = add("Витамин D", "10:00", True).json()
b = add("Цинк", "10:00", True).json()
chk("две добавки в 10:00", reminders().get("10:00") == sorted([a["id"], b["id"]]), reminders())
c.patch(f"/supplement/{a['id']}", json={"reminder_enabled": False})
chk("после первой — осталась вторая", reminders().get("10:00") == [b["id"]], reminders())
c.patch(f"/supplement/{b['id']}", json={"reminder_enabled": False})
chk("после второй — напоминания нет", "10:00" not in reminders(), reminders())
# Время у добавки без времени задаётся из списка, потом напоминание включается.
nt = add("Глицин", None, False).json()
r = c.patch(f"/supplement/{nt['id']}", json={"intake_time": "21:30"})
chk("время задано из списка", r.status_code == 200 and r.json()["intake_time"] == "21:30", r.text[:200])
r = c.patch(f"/supplement/{nt['id']}", json={"reminder_enabled": True})
chk("и напоминание включилось", reminders().get("21:30") == [nt["id"]], reminders())

# ---------- 6. Удаление не оставляет пустых напоминаний ----------
chk("удаление 200", c.delete(f"/supplement/{mg['id']}").status_code == 200)
chk("22:00 исчезло вместе с последней добавкой", "22:00" not in reminders(), reminders())

# ---------- 7. Рассылка: пустое напоминание не отправляется ----------
with SessionLocal() as db:
    empty = M.SupplementReminder(telegram_id=1, label="", time="07:00", enabled=True)
    db.add(empty); db.commit(); db.refresh(empty)
    sent = []
    from datetime import datetime
    now = datetime(2026, 9, 18, 7, 1)  # время напоминания наступило
    with patch.object(notifications, "send_telegram", lambda tid, text: sent.append(text) or True):
        notifications._process_supplement_reminder(db, empty, "2026-09-18", now)
    chk("пустое напоминание не шлётся", sent == [], sent)
    # Контроль: напоминание с добавкой в то же время — шлётся.
    full = db.query(M.SupplementReminder).filter(M.SupplementReminder.time == "08:30").first()
    with patch.object(notifications, "send_telegram", lambda tid, text: sent.append(text) or True):
        notifications._process_supplement_reminder(db, full, "2026-09-18", datetime(2026, 9, 18, 8, 31))
    chk("напоминание с добавкой шлётся и называет её", len(sent) == 1 and "Креатин" in sent[0], sent)

# ---------- 8. ИИ-совет знает, что уже принимают ----------
captured = {}


def fake_run(system_prompt, user_prompt, log_tag=None, max_tokens=None, **kw):
    captured["sys"], captured["user"] = system_prompt, user_prompt
    return ({"suggestions": [
        {"name": "Креатин моногидрат", "dosage": "5 г", "note": "сила"},
        {"name": "Бета-аланин", "dosage": "3 г", "note": "выносливость"}],
        "current_note": "Креатин уже закрывает силу — продолжайте."}, {})


with patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.post("/supplement/recommend", json={"improvement_goal": "сила"})
chk("recommend 200", r.status_code == 200, r.text[:200])
# Язык промпта — из профиля (у dev-пользователя английский), проверяем оба.
u = captured.get("user", "")
chk("в промпте — что уже принимают", ("Уже принимаю:" in u or "I already take:" in u) and "Креатин, 5 г" in u, u)
sysp = captured.get("sys", "")
chk("системный промпт запрещает повторы", "УЖЕ ПРИНИМАЕТ" in sysp or "ALREADY TAKES" in sysp)
names = [s["name"] for s in r.json()["suggestions"]]
chk("креатин отфильтрован (уже пьёт)", names == ["Бета-аланин"], names)
chk("комментарий к текущему набору", r.json().get("current_note", "").startswith("Креатин"), r.json().get("current_note"))


def only_taken(system_prompt, user_prompt, log_tag=None, max_tokens=None, **kw):
    return ({"suggestions": [{"name": "Сывороточный протеин", "dosage": "30 г", "note": ""}]}, {})


c.post("/supplement/add", json={"name": "Протеин", "dosage": "30 г"})
with patch.object(ai_service, "_run_text_completion", only_taken):
    r = c.post("/supplement/recommend", json={"improvement_goal": "восстановление"})
chk("всё уже принимается → 200, не 502", r.status_code == 200 and r.json()["suggestions"] == []
    and r.json().get("current_note"), r.text[:200])

if fails:
    print("FAIL:\n  " + "\n  ".join(fails)); sys.exit(1)
print("OK: напоминание в время добавки, общее на одно время, переключатель, перенос времени,\n"
      "    старые напоминания видны и выключаются, удаление без сирот, пустые не шлются;\n"
      "    ИИ-совет видит принимаемое, повторы отфильтрованы, «всё уже есть» — не ошибка")
