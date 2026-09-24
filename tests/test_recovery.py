"""Проверка /recovery/advice: премиум-гейт, дисклеймер, нормализация зоны,
учёт тренировок, устойчивость к мусору от AI."""
import os, sys, tempfile, pathlib, json
from datetime import date as _date
from unittest import mock
ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "rec.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"

from fastapi.testclient import TestClient
from backend.database import init_db, SessionLocal
from backend import models as M, ai_service
from backend.main import app
init_db()
c = TestClient(app)
# Согласия на обработку данных (152-ФЗ): без них закрыты функции с ИИ
# и данными о здоровье — тест проверяет не их, поэтому даём сразу.
c.post("/consent", json={"pd": True, "terms": True, "health": True, "cross_border": True})
fails = []
def chk(n, cond, x=""):
    if not cond: fails.append(n + ("  " + str(x) if x else ""))

# Ловим, что уходит в модель.
captured = {}
GOOD = {
  "likely_cause": "Скорее всего обычная крепатура после становой тяги.",
  "is_typical_soreness": True,
  "today": ["Лёгкая прогулка 20 минут", "Мягкая растяжка задней поверхности", "Тёплый душ", "Выспаться 8 часов"],
  "avoid": ["Тяжёлых приседаний и становой", "Резких рывковых движений"],
  "training": "Лёгкая нагрузка через 1-2 дня, полная — когда боль пройдёт.",
  "red_flags": ["Резкая боль в момент движения", "Онемение или прострелы в ногу", "Отёк и покраснение"],
}
def fake_run(system_prompt, user_prompt, log_tag, max_tokens=None):
    captured["system"] = system_prompt; captured["user"] = user_prompt; captured["tag"] = log_tag
    return dict(GOOD), {}

# --- free -> 402 ---
r = c.post("/recovery/advice", json={"zone": "legs"})
chk("free -> 402", r.status_code == 402, r.status_code)

# делаем премиум
db = SessionLocal(); u = db.query(M.User).filter(M.User.telegram_id == 1).first()
u.subscription_type = "lifetime"; u.language = "ru"
db.add(M.Workout(telegram_id=1, date=_date.today().isoformat(), type="strength", duration_min=60, calories_burned=400, description="становая тяга"))
db.commit(); db.close()

with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.post("/recovery/advice", json={"zone": "legs", "complaint": "тянет заднюю поверхность после становой"})
chk("premium -> 200", r.status_code == 200, r.text)
j = r.json() if r.status_code == 200 else {}
chk("зона", j.get("zone") == "legs", j.get("zone"))
chk("причина", "крепатура" in (j.get("likely_cause") or "").lower(), j.get("likely_cause"))
chk("today 4 пункта", len(j.get("today", [])) == 4, j.get("today"))
chk("avoid 2 пункта", len(j.get("avoid", [])) == 2, j.get("avoid"))
chk("red_flags есть", len(j.get("red_flags", [])) >= 2, j.get("red_flags"))
chk("дисклеймер", "медицинской" in (j.get("disclaimer") or ""), j.get("disclaimer"))
chk("is_typical_soreness", j.get("is_typical_soreness") is True, j.get("is_typical_soreness"))
# промпт получил жалобу и тренировки
chk("жалоба в промпте", "заднюю поверхность" in captured.get("user", ""), captured.get("user"))
chk("тренировки в промпте", "становая тяга" in captured.get("user", ""), captured.get("user"))
chk("правила безопасности в системном промпте", "НЕ ставишь диагноз" in captured.get("system", ""), captured.get("system", "")[:200])
chk("tag телеметрии", captured.get("tag") == "recovery_advice", captured.get("tag"))

# --- неизвестная зона нормализуется в other, не падает ---
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.post("/recovery/advice", json={"zone": "жопа"})
chk("неизвестная зона -> other", r.status_code == 200 and r.json().get("zone") == "other", r.text[:200])

# --- мусор от AI: обрезаем лишнее, не падаем ---
JUNK = {"likely_cause": 123, "today": ["ок", 5, None, "два", "три", "четыре", "пять"],
        "avoid": "не массив", "training": None, "red_flags": [], "is_typical_soreness": "да"}
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: (dict(JUNK), {})):
    r = c.post("/recovery/advice", json={"zone": "back"})
chk("мусор -> 200", r.status_code == 200, r.text[:200])
if r.status_code == 200:
    jj = r.json()
    chk("today обрезан до 4", len(jj["today"]) <= 4, jj["today"])
    chk("нечисловые отброшены", all(isinstance(x, str) for x in jj["today"]), jj["today"])
    chk("avoid -> []", jj["avoid"] == [], jj["avoid"])

# --- пустой ответ AI -> 502, а не пустой совет ---
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: ({}, {})):
    r = c.post("/recovery/advice", json={"zone": "neck"})
chk("пустой ответ -> 502", r.status_code == 502, r.status_code)

# --- английский язык ---
db = SessionLocal(); u = db.query(M.User).filter(M.User.telegram_id == 1).first(); u.language = "en"; db.commit(); db.close()
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    c.post("/recovery/advice", json={"zone": "shoulders"})
chk("EN системный промпт", "do NOT diagnose" in captured.get("system", ""), captured.get("system", "")[:120])

if fails:
    print("FAIL:"); [print("  -", f) for f in fails]; sys.exit(1)
print("OK: премиум-гейт (402), дисклеймер, зоны нормализуются, жалоба и тренировки")
print("    попадают в промпт, мусор от AI не ломает ответ, пустой ответ -> 502, EN-промпт")
