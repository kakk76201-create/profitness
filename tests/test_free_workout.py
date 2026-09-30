"""Своя тренировка: не по программе, упражнения человек выбирает сам.

Стартует без анкеты и без программы, начинается пустой, упражнения
добавляются из библиотеки, подходы пишутся как обычно, завершение создаёт
запись в дневнике с реальной длительностью (без плана она не обрезается до
30 минут). Вторая тренировка поверх идущей — 409.
"""
import os, pathlib, sys, tempfile
from datetime import datetime, timedelta

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "free.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"
os.environ.pop("RAILWAY_ENVIRONMENT", None); os.environ.pop("RAILWAY_PROJECT_ID", None)

from fastapi.testclient import TestClient
from backend import models as M, trainer_logic
from backend.database import init_db, SessionLocal
from backend.main import app

init_db()
with TestClient(app):  # lifespan загружает библиотеку упражнений
    pass
c = TestClient(app)
c.post("/consent", json={"pd": True, "terms": True, "health": True, "cross_border": True})
fails = []


def chk(name, cond, extra=""):
    if not cond:
        fails.append(name + ("  " + str(extra) if extra else ""))


today = datetime.utcnow().date().isoformat()
r = c.post("/trainer/session/start", json={"free": True, "date": today})
chk("без подписки — 402 (тренер платный)", r.status_code == 402, r.status_code)

with SessionLocal() as db:
    u = db.query(M.User).filter(M.User.telegram_id == 1).first()
    u.subscription_type = "lifetime"; u.language = "ru"; u.weight = 80
    db.commit()
    chk("анкеты тренера нет", db.query(M.TrainerProfile).count() == 0)
    chk("программы нет", db.query(M.TrainerProgram).count() == 0)

r = c.post("/trainer/session/start", json={"free": True, "date": today})
chk("старт своей тренировки 200", r.status_code == 200, r.text[:300])
s = r.json()
sid = s.get("id")
chk("без дня программы", s.get("program_day_id") is None, s.get("program_day_id"))
chk("пустая", s.get("exercises") == [], s.get("exercises"))
chk("название по умолчанию", s.get("title") == "Своя тренировка", s.get("title"))

r = c.post("/trainer/session/start", json={"free": True, "date": today})
chk("вторая поверх идущей — 409", r.status_code == 409, r.status_code)

lib = c.get("/trainer/exercises").json()
items = lib.get("items") or lib.get("exercises") or []
chk("библиотека есть", len(items) > 0, list(lib.keys()))
ex_id = items[0]["id"] if items else 1
r = c.post(f"/trainer/session/{sid}/exercise/add", json={"exercise_id": ex_id, "sets": 3, "reps_min": 8, "reps_max": 12})
chk("упражнение добавлено", r.status_code == 200, r.text[:300])
sex_id = r.json().get("id")

r = c.post(f"/trainer/session/{sid}/set",
           json={"session_exercise_id": sex_id, "set_index": 1, "weight_kg": 40, "reps": 10})
chk("подход записан", r.status_code == 200, r.text[:300])

# Тренировка шла 70 минут: без плана длительность не обрезается до 30.
with SessionLocal() as db:
    row = db.get(M.TrainerSession, sid)
    row.started_at = datetime.utcnow() - timedelta(minutes=70)
    db.commit()
r = c.post(f"/trainer/session/{sid}/finish", json={})
chk("завершение 200", r.status_code == 200, r.text[:300])
body = r.json()
chk("длительность 70 минут", body["summary"]["duration_min"] == 70, body["summary"])
with SessionLocal() as db:
    w = db.get(M.Workout, body["workout_id"])
    chk("запись в дневнике", w is not None and w.duration_min == 70 and (w.calories_burned or 0) > 0,
        (w.duration_min, w.calories_burned) if w else None)
    chk("подпись в дневнике", w is not None and "Своя тренировка" in (w.description or ""), w.description if w else None)

# Логика длительности.
chk("без плана: потолок 4 часа", trainer_logic.session_duration_min(300, None) == 240)
chk("по плану: как раньше", trainer_logic.session_duration_min(200, 45) == 68)

if fails:
    print("FAIL:\n  " + "\n  ".join(fails)); sys.exit(1)
print("OK: своя тренировка без анкеты и программы, пустая, 409 при второй, упражнение из\n"
      "    библиотеки, подход, завершение с реальной длительностью и записью в дневник")
