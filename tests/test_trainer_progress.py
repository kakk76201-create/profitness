"""Проверки /trainer/progress и /trainer/exercises/{id}/history (ТЗ §4.4, §8.1).

Покрыто: премиум-гейт 402; объём по группам мышц за 7 дней считает только
выполненные РАБОЧИЕ подходы (разминочные и неотмеченные — мимо); итоги за 4
недели и сравнение недель по завершённым сессиям; стрик по неделям; топ
упражнений по частоте; точки графика по exercise_id и период 4w/all; рекорды;
история упражнения (сессии с подходами, точки, 404 на чужой id).
"""
import os, sys, tempfile, pathlib, json
from datetime import date as _date, timedelta

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "trprog.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"

from fastapi.testclient import TestClient
from backend.database import init_db, SessionLocal
from backend import models as M
from backend.main import app

init_db()
# Прогоняем lifespan: он загружает библиотеку упражнений.
with TestClient(app):
    pass
c = TestClient(app)

fails = []
def chk(n, cond, x=""):
    if not cond: fails.append(n + ("  " + str(x) if x else ""))

def q(fn):
    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()

TID = 1
today = _date.today()
monday = today - timedelta(days=today.weekday())
ISO = lambda d: d.isoformat()


# --------------------------------------------------------------------------- #
#  1. Free-пользователь: 402
# --------------------------------------------------------------------------- #
for name, resp in [
    ("progress", c.get("/trainer/progress")),
    ("exercise history", c.get("/trainer/exercises/1/history")),
]:
    chk(f"free {name} -> 402", resp.status_code == 402, resp.status_code)


# --------------------------------------------------------------------------- #
#  2. Премиум + анкета + программа этой недели
# --------------------------------------------------------------------------- #
db = SessionLocal(); u = db.query(M.User).filter(M.User.telegram_id == TID).first()
u.subscription_type = "lifetime"; u.language = "ru"
u.weight = 80.0; u.height = 180.0; u.age = 30; u.gender = "male"
u.diet_goal = "muscle"; u.daily_goal_kcal = 2600; u.target_proteins = 150
db.commit(); db.close()

# Пока данных нет — экран прогресса всё равно должен открываться.
r = c.get("/trainer/progress")
chk("пустой прогресс -> 200", r.status_code == 200, r.text[:200])
if r.status_code == 200:
    e = r.json()
    chk("пустой прогресс: график None", e.get("chart") is None, e.get("chart"))
    chk("пустой прогресс: стрик 0", e.get("streak", {}).get("weeks") == 0, e.get("streak"))
    chk("пустой прогресс: 10 групп мышц", len(e.get("muscle_volume_7d", [])) == 10, len(e.get("muscle_volume_7d", [])))
    chk("пустой прогресс: рекордов нет", e.get("records") == [], e.get("records"))

r = c.post("/trainer/profile", json={
    "goal": "muscle", "level": "beginner", "equipment": "gym",
    "equipment_extra": ["bench"], "days_per_week": 3, "preferred_weekdays": [0, 2, 4],
    "session_minutes": 45, "program_weeks": 6, "limitations": [], "focus": [],
    "reminder_enabled": False,
})
chk("анкета сохранена", r.status_code == 200, r.text[:200])

EX = q(lambda db: {e.slug: e.id for e in db.query(M.TrainerExercise).all()})
BENCH, SQUAT = EX["db_bench_press"], EX["goblet_squat"]
BENCH_GROUP = q(lambda db: db.query(M.TrainerExercise).filter(M.TrainerExercise.id == BENCH).first().muscle_group)
chk("жим — грудь", BENCH_GROUP == "chest", BENCH_GROUP)

db = SessionLocal()
program = M.TrainerProgram(
    telegram_id=TID, status="active", title="Тест", split_type="full_body",
    goal="muscle", level="beginner", equipment="gym", weeks=2, days_per_week=3,
    start_date=ISO(monday), end_date=ISO(monday + timedelta(days=11)),
    periodization_json=json.dumps([{"week": 1, "phase": "base", "weight_pct": 100, "sets_delta": 0}]),
)
db.add(program); db.flush()
for di, offset in ((1, 0), (2, 2), (3, 4)):
    db.add(M.TrainerProgramDay(
        telegram_id=TID, program_id=program.id, week=1, day_index=di,
        weekday=(monday + timedelta(days=offset)).weekday(),
        scheduled_date=ISO(monday + timedelta(days=offset)),
        title=f"День {di}", session_type="strength", duration_min=45,
        warmup_json="[]", exercises_json="[]", cooldown_json="[]",
        status="planned",
    ))

# Три завершённые сессии: эта неделя, прошлая неделя и «давняя» (вне 4 недель).
SESSIONS = {}
for key, day, minutes, sets_n, volume in (
    ("now", monday, 45, 6, 600.0),
    ("prev", monday - timedelta(days=7), 60, 4, 400.0),
    ("old", today - timedelta(days=40), 30, 2, 150.0),
):
    s = M.TrainerSession(
        telegram_id=TID, program_id=program.id, date=ISO(day), status="completed",
        title=f"Сессия {key}", session_type="strength", week=1, day_index=1,
        duration_min=minutes, total_sets=sets_n, total_reps=sets_n * 10,
        total_volume_kg=volume, calories_burned=300,
    )
    db.add(s); db.flush()
    SESSIONS[key] = (s.id, ISO(day))

def add_set(session_key, exercise_id, index, weight, reps, set_type="work", done=True):
    sid, day = SESSIONS[session_key]
    db.add(M.TrainerSetLog(
        telegram_id=TID, session_id=sid, session_exercise_id=1000 + index,
        exercise_id=exercise_id, date=day, set_index=index, set_type=set_type,
        weight_kg=weight, reps=reps, is_done=done, volume_kg=weight * reps,
    ))

# Эта неделя: 3 рабочих жима (20×10), 1 разминочный и 1 неотмеченный — не в счёт.
add_set("now", BENCH, 1, 10.0, 10, set_type="warmup")
add_set("now", BENCH, 2, 20.0, 10)
add_set("now", BENCH, 3, 20.0, 10)
add_set("now", BENCH, 4, 20.0, 10)
add_set("now", BENCH, 5, 20.0, 10, done=False)
# И два рабочих гоблет-приседа 40×10.
add_set("now", SQUAT, 6, 40.0, 10)
add_set("now", SQUAT, 7, 40.0, 10)
# Прошлая неделя и «давняя» сессия — только жим (для графика и топа).
add_set("prev", BENCH, 1, 18.0, 10)
add_set("prev", BENCH, 2, 18.0, 10)
add_set("old", BENCH, 1, 15.0, 10)

db.add(M.TrainerRecord(
    telegram_id=TID, exercise_id=BENCH, record_type="max_weight", value=20.0,
    weight_kg=20.0, reps=10, session_id=SESSIONS["now"][0], date=ISO(monday),
))
db.add(M.TrainerRecord(
    telegram_id=TID, exercise_id=BENCH, record_type="est_1rm", value=26.7,
    weight_kg=20.0, reps=10, session_id=SESSIONS["now"][0], date=ISO(monday),
))
db.commit(); db.close()


# --------------------------------------------------------------------------- #
#  3. GET /trainer/progress
# --------------------------------------------------------------------------- #
r = c.get("/trainer/progress", params={"date": ISO(today)})
chk("progress -> 200", r.status_code == 200, r.text[:300])
p = r.json() if r.status_code == 200 else {}

muscles = {m["muscle_group"]: m for m in p.get("muscle_volume_7d", [])}
chk("грудь: только рабочие выполненные (3 сета)", muscles.get("chest", {}).get("sets") == 3, muscles.get("chest"))
chk("грудь: объём 600 кг", muscles.get("chest", {}).get("volume_kg") == 600.0, muscles.get("chest"))
chk("квадрицепсы: 2 сета", muscles.get("quads", {}).get("sets") == 2, muscles.get("quads"))
chk("квадрицепсы: объём 800 кг", muscles.get("quads", {}).get("volume_kg") == 800.0, muscles.get("quads"))
chk("спина: 0 сетов", muscles.get("back", {}).get("sets") == 0, muscles.get("back"))
chk("зона 10–20", muscles.get("chest", {}).get("target_min") == 10 and muscles.get("chest", {}).get("target_max") == 20,
    muscles.get("chest"))
chk("кардио не в списке мышц", "cardio" not in muscles, list(muscles))

t4 = p.get("totals_4w", {})
chk("4 недели: 2 тренировки", t4.get("sessions") == 2, t4)
chk("4 недели: подходы 10", t4.get("sets") == 10, t4)
chk("4 недели: минуты 105", t4.get("minutes") == 105, t4)
chk("4 недели: объём 1000", t4.get("volume_kg") == 1000.0, t4)
chk("давняя сессия вне 4 недель", t4.get("sessions") == 2, t4)

wc = p.get("week_compare", {})
chk("эта неделя: 1 тренировка", wc.get("this", {}).get("sessions") == 1, wc.get("this"))
chk("прошлая неделя: 1 тренировка", wc.get("prev", {}).get("sessions") == 1, wc.get("prev"))
chk("эта неделя: объём 600", wc.get("this", {}).get("volume_kg") == 600.0, wc.get("this"))

st = p.get("streak", {})
chk("стрик 2 недели", st.get("weeks") == 2, st)
chk("на этой неделе 1 из 3", st.get("this_week_done") == 1 and st.get("this_week_goal") == 3, st)

top = [x["id"] for x in p.get("top_exercises", [])]
chk("топ упражнений: жим первый", top and top[0] == BENCH, top)
chk("в топе есть присед", SQUAT in top, top)

chart = p.get("chart") or {}
chk("график по умолчанию — самый частый", chart.get("exercise_id") == BENCH, chart.get("exercise_id"))
points = {pt["date"]: pt for pt in chart.get("points", [])}
chk("4w: две точки (давняя вне периода)", len(points) == 2, sorted(points))
now_point = points.get(ISO(monday), {})
chk("точка: max_weight 20", now_point.get("max_weight") == 20.0, now_point)
chk("точка: объём 600 (без разминки и неотмеченного)", now_point.get("volume") == 600.0, now_point)
chk("точка: 1RM по Эпли 26.7", now_point.get("est_1rm") == 26.7, now_point)

recs = {x["record_type"]: x for x in p.get("records", [])}
chk("рекорд max_weight 20", recs.get("max_weight", {}).get("value") == 20.0, recs.get("max_weight"))
chk("рекорд знает упражнение", (recs.get("max_weight", {}).get("exercise") or {}).get("slug") == "db_bench_press",
    recs.get("max_weight"))
chk("рекорд est_1rm есть", "est_1rm" in recs, list(recs))

# Явный выбор упражнения и период.
r = c.get("/trainer/progress", params={"date": ISO(today), "exercise_id": SQUAT})
chk("progress по приседу -> 200", r.status_code == 200, r.text[:200])
sq = (r.json().get("chart") or {}) if r.status_code == 200 else {}
chk("график приседа", sq.get("exercise_id") == SQUAT, sq.get("exercise_id"))
chk("присед: одна точка, объём 800", len(sq.get("points", [])) == 1 and sq["points"][0]["volume"] == 800.0,
    sq.get("points"))

r = c.get("/trainer/progress", params={"date": ISO(today), "exercise_id": BENCH, "period": "all"})
allp = (r.json().get("chart") or {}) if r.status_code == 200 else {}
chk("period=all: три точки", len(allp.get("points", [])) == 3, allp.get("points"))
chk("period возвращается", r.json().get("period") == "all", r.json().get("period"))


# --------------------------------------------------------------------------- #
#  4. GET /trainer/exercises/{id}/history
# --------------------------------------------------------------------------- #
r = c.get(f"/trainer/exercises/{BENCH}/history")
chk("history -> 200", r.status_code == 200, r.text[:300])
h = r.json() if r.status_code == 200 else {}
chk("history: упражнение", (h.get("exercise") or {}).get("slug") == "db_bench_press", h.get("exercise"))
chk("history: два рекорда", len(h.get("records", [])) == 2, h.get("records"))
sess = h.get("sessions", [])
chk("history: три сессии", len(sess) == 3, [s["session_id"] for s in sess])
chk("history: сначала свежая", sess and sess[0]["date"] == ISO(monday), sess[:1])
chk("history: в сессии все 5 строк (с разминкой)", sess and len(sess[0]["sets"]) == 5, sess[:1])
chk("history: разминочный помечен", sess and sess[0]["sets"][0]["set_type"] == "warmup", sess[:1])
chk("history: точки только по рабочим", len(h.get("points", [])) == 3, h.get("points"))
hp = {pt["date"]: pt for pt in h.get("points", [])}
chk("history: точка недели — 600 кг", hp.get(ISO(monday), {}).get("volume") == 600.0, hp.get(ISO(monday)))

r = c.get("/trainer/exercises/999999/history")
chk("history чужого упражнения -> 404", r.status_code == 404, r.status_code)

# Упражнение без истории отдаёт пустые списки, а не 500.
r = c.get(f"/trainer/exercises/{EX['plank']}/history")
chk("history без данных -> 200", r.status_code == 200, r.text[:200])
if r.status_code == 200:
    empty = r.json()
    chk("history без данных: пусто", empty.get("sessions") == [] and empty.get("points") == [], empty)

if fails:
    print("FAIL:"); [print("  -", f) for f in fails]; sys.exit(1)
print("OK: прогресс (мышцы только work/done, итоги 4 недель, сравнение недель, стрик,")
print("    топ и график по exercise_id с периодами, рекорды) и история упражнения")
