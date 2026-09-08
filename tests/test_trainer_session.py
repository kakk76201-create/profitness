"""Проверки API сессии тренера (ТЗ §4.3 целиком, §5.5 прогрессия/PR, §8.1).

Покрыто: премиум-гейт 402, старт сессии (цели из плана + состояния, «прошлый
раз», 409 при второй), запись подходов (upsert, объём/1RM, PR только при
улучшении, разминка не в объём, чужая сессия 404), замена/пропуск/добавление
упражнения, завершение (400 без сетов, Workout + дневник, день done, состояние
прогрессии), отзыв (адаптация правилами, идемпотентность, RU/EN), отмена
(Workout не создаётся), история сессий и deload-неделя.
"""
import os, sys, tempfile, pathlib, json
from datetime import date as _date, timedelta

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "trses.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"

from fastapi.testclient import TestClient
from backend.database import init_db, SessionLocal
from backend import models as M, trainer_logic
from backend.main import app

init_db()
# Прогоняем lifespan: он загружает библиотеку упражнений (вставка (б) в main.py).
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
D = lambda n: (monday + timedelta(days=n)).isoformat()


# --------------------------------------------------------------------------- #
#  1. Free-пользователь: 402 на всех маршрутах сессии
# --------------------------------------------------------------------------- #
for name, resp in [
    ("session/start", c.post("/trainer/session/start", json={"date": D(0)})),
    ("session/active", c.get("/trainer/session/active")),
    ("session/{id}", c.get("/trainer/session/1")),
    ("session/set", c.post("/trainer/session/1/set", json={"session_exercise_id": 1, "set_index": 1})),
    ("session/set delete", c.delete("/trainer/session/1/set/1")),
    ("session/replace", c.post("/trainer/session/1/exercise/1/replace", json={"new_exercise_id": 2})),
    ("session/skip", c.post("/trainer/session/1/exercise/1/skip")),
    ("session/add", c.post("/trainer/session/1/exercise/add", json={"exercise_id": 1})),
    ("session/finish", c.post("/trainer/session/1/finish", json={})),
    ("session/feedback", c.post("/trainer/session/1/feedback", json={"feedback": "ok"})),
    ("session/abandon", c.post("/trainer/session/1/abandon")),
    ("sessions", c.get("/trainer/sessions")),
]:
    chk(f"free {name} -> 402", resp.status_code == 402, resp.status_code)


# --------------------------------------------------------------------------- #
#  2. Премиум-пользователь, анкета, программа на 2 недели × 3 дня
# --------------------------------------------------------------------------- #
db = SessionLocal(); u = db.query(M.User).filter(M.User.telegram_id == TID).first()
u.subscription_type = "lifetime"; u.language = "ru"
u.weight = 80.0; u.height = 180.0; u.age = 30; u.gender = "male"
u.diet_goal = "muscle"; u.daily_goal_kcal = 2600
db.commit(); db.close()

r = c.post("/trainer/profile", json={
    "goal": "muscle", "level": "beginner", "equipment": "gym",
    "equipment_extra": ["bench"], "days_per_week": 3, "preferred_weekdays": [0, 2, 4],
    "session_minutes": 45, "program_weeks": 6, "limitations": [], "focus": [],
    "reminder_enabled": False, "reminder_time": "18:00",
})
chk("анкета сохранена", r.status_code == 200, r.text[:200])

EX = q(lambda db: {e.slug: e.id for e in db.query(M.TrainerExercise).all()})
for slug in ("db_bench_press", "goblet_squat", "plank", "db_row",
             "warmup_general_5min", "stretch_full_body_5min"):
    chk(f"в библиотеке есть {slug}", slug in EX)

FULL = [
    {"slug": "db_bench_press", "sets": 3, "reps_min": 8, "reps_max": 12, "rest_sec": 90,
     "start_weight_kg": 20, "rpe": 7, "note": "локти 45°", "order": 1},
    {"slug": "goblet_squat", "sets": 3, "reps_min": 10, "reps_max": 15, "rest_sec": 90,
     "start_weight_kg": 24, "order": 2},
    {"slug": "plank", "sets": 3, "time_sec": 45, "rest_sec": 60, "order": 3},
]
SHORT = [{"slug": "db_bench_press", "sets": 3, "reps_min": 8, "reps_max": 12,
          "rest_sec": 90, "start_weight_kg": 20, "order": 1}]

db = SessionLocal()
program = M.TrainerProgram(
    telegram_id=TID, status="active", title="Тест-программа", split_type="full_body",
    goal="muscle", level="beginner", equipment="gym", weeks=2, days_per_week=3,
    start_date=D(0), end_date=D(11),
    periodization_json=json.dumps([
        {"week": 1, "phase": "base", "weight_pct": 100, "sets_delta": 0},
        {"week": 2, "phase": "deload", "weight_pct": 85, "sets_delta": -1},
    ]),
    tips_json="[]",
)
db.add(program); db.flush()
DAY_DATES = {(1, 1): D(0), (1, 2): D(2), (1, 3): D(4), (2, 1): D(7), (2, 2): D(9), (2, 3): D(11)}
for (week, di), day_date in DAY_DATES.items():
    db.add(M.TrainerProgramDay(
        telegram_id=TID, program_id=program.id, week=week, day_index=di,
        weekday=_date.fromisoformat(day_date).weekday(), scheduled_date=day_date,
        title=f"День {di}", session_type="strength", duration_min=45,
        focus_muscles_json=json.dumps(["chest"]),
        warmup_json=json.dumps([{"slug": "warmup_general_5min", "sets": 1, "time_sec": 300}]),
        exercises_json=json.dumps(FULL if di == 1 else SHORT),
        cooldown_json=json.dumps([{"slug": "stretch_full_body_5min", "sets": 1, "time_sec": 300}]),
        status="planned",
    ))

# Прошлая завершённая сессия — источник «прошлого раза» для жима.
old = M.TrainerSession(telegram_id=TID, date=D(-7), status="completed", title="День 1",
                       session_type="strength", week=1, day_index=1, duration_min=40,
                       total_sets=1, total_volume_kg=168.0, calories_burned=260)
db.add(old); db.flush()
old_sx = M.TrainerSessionExercise(telegram_id=TID, session_id=old.id, exercise_id=EX["db_bench_press"],
                                  block="main", order_index=1, planned_sets=3, status="done")
db.add(old_sx); db.flush()
db.add(M.TrainerSetLog(telegram_id=TID, session_id=old.id, session_exercise_id=old_sx.id,
                       exercise_id=EX["db_bench_press"], date=D(-7), set_index=1, set_type="warmup",
                       weight_kg=8, reps=10, is_done=True))
db.add(M.TrainerSetLog(telegram_id=TID, session_id=old.id, session_exercise_id=old_sx.id,
                       exercise_id=EX["db_bench_press"], date=D(-7), set_index=2, set_type="work",
                       weight_kg=14, reps=12, is_done=True, volume_kg=168.0))
db.commit()
PROG_ID, OLD_SID, OLD_SX = program.id, old.id, old_sx.id
DAY_IDS = q(lambda db: {(d.week, d.day_index): d.id for d in
                        db.query(M.TrainerProgramDay).filter(M.TrainerProgramDay.program_id == PROG_ID).all()})
db.close()


# --------------------------------------------------------------------------- #
#  3. Старт сессии: цели из плана, «прошлый раз», 409 на вторую
# --------------------------------------------------------------------------- #
r = c.post("/trainer/session/start", json={"program_day_id": DAY_IDS[(1, 1)], "date": D(0)})
chk("start -> 200", r.status_code == 200, r.text[:300])
S = r.json() if r.status_code == 200 else {}
SID = S.get("id")
chk("сессия in_progress", S.get("status") == "in_progress", S.get("status"))
chk("название дня", S.get("title") == "День 1", S.get("title"))
chk("неделя/день", (S.get("week"), S.get("day_index")) == (1, 1), (S.get("week"), S.get("day_index")))
chk("program_day_id", S.get("program_day_id") == DAY_IDS[(1, 1)], S.get("program_day_id"))

sx = {e["exercise"]["slug"]: e for e in S.get("exercises", []) if e.get("exercise")}
chk("5 упражнений (разминка+3+заминка)", len(S.get("exercises", [])) == 5, len(S.get("exercises", [])))
chk("порядок сквозной 1..5",
    [e["order_index"] for e in S.get("exercises", [])] == [1, 2, 3, 4, 5],
    [e["order_index"] for e in S.get("exercises", [])])
chk("блоки warmup/main/cooldown",
    [e["block"] for e in S.get("exercises", [])] == ["warmup", "main", "main", "main", "cooldown"],
    [e["block"] for e in S.get("exercises", [])])
bench = sx.get("db_bench_press", {})
chk("жим: вес цели 20", bench.get("planned_weight_kg") == 20.0, bench.get("planned_weight_kg"))
chk("жим: 3 подхода 8-12", (bench.get("planned_sets"), bench.get("planned_reps_min"),
                            bench.get("planned_reps_max")) == (3, 8, 12), bench)
chk("жим: отдых 90", bench.get("planned_rest_sec") == 90, bench.get("planned_rest_sec"))
chk("жим: подсказка ИИ", bench.get("note") == "локти 45°", bench.get("note"))
chk("жим: «прошлый раз» из прошлой сессии",
    [p["weight_kg"] for p in bench.get("previous", [])] == [14.0], bench.get("previous"))
chk("жим: разминочный сет не в «прошлый раз»", len(bench.get("previous", [])) == 1, bench.get("previous"))
chk("планка: цель по времени", (sx.get("plank") or {}).get("planned_time_sec") == 45, sx.get("plank"))
chk("планка: без веса", (sx.get("plank") or {}).get("planned_weight_kg") is None, sx.get("plank"))
chk("гоблет: вес цели 24", (sx.get("goblet_squat") or {}).get("planned_weight_kg") == 24.0, sx.get("goblet_squat"))
chk("у нового упражнения нет сетов", all(not e["sets"] for e in S.get("exercises", [])))

r2 = c.post("/trainer/session/start", json={"program_day_id": DAY_IDS[(1, 2)], "date": D(2)})
chk("вторая start -> 409", r2.status_code == 409, r2.status_code)
chk("409 отдаёт id активной сессии",
    (r2.json().get("detail") or {}).get("session_id") == SID, r2.text[:200])

r = c.get("/trainer/session/active")
chk("active -> та же сессия", r.status_code == 200 and r.json() and r.json()["id"] == SID, r.text[:200])
chk("GET session -> 200", c.get(f"/trainer/session/{SID}").status_code == 200)
chk("GET чужой сессии -> 404", c.get("/trainer/session/999999").status_code == 404)
chk("start с чужим днём -> 404",
    c.post("/trainer/session/start", json={"program_day_id": 999999, "date": D(0)}).status_code in (404, 409))

BENCH_SX, GOBLET_SX, PLANK_SX = bench["id"], sx["goblet_squat"]["id"], sx["plank"]["id"]
WARM_SX = sx["warmup_general_5min"]["id"]

# Завершение без единого рабочего подхода запрещено (ТЗ §4.3).
chk("finish без сетов -> 400", c.post(f"/trainer/session/{SID}/finish", json={}).status_code == 400)


# --------------------------------------------------------------------------- #
#  4. Подходы: upsert, объём, 1RM, PR только при улучшении
# --------------------------------------------------------------------------- #
def save_set(sex_id, index, **kw):
    body = {"session_exercise_id": sex_id, "set_index": index, "set_type": "work", "is_done": True}
    body.update(kw)
    return c.post(f"/trainer/session/{SID}/set", json=body)

r = c.post(f"/trainer/session/{SID}/set",
           json={"session_exercise_id": WARM_SX, "set_index": 1, "set_type": "warmup",
                 "time_sec": 300, "is_done": True})
chk("разминка отмечена -> 200", r.status_code == 200, r.text[:200])
chk("разминка: блок закрыт", r.json()["exercise_status"] == "done", r.json())

r = save_set(BENCH_SX, 1, set_type="warmup", weight_kg=10, reps=10)
chk("разминочный подход жима -> 200", r.status_code == 200, r.text[:200])
chk("разминочный не даёт PR", r.json()["prs"] == [], r.json()["prs"])
chk("разминочный не закрывает упражнение", r.json()["exercise_status"] == "pending", r.json())
chk("отдых из плана", r.json()["rest_sec"] == 90, r.json()["rest_sec"])

r = save_set(BENCH_SX, 2, weight_kg=20, reps=12)
chk("рабочий подход -> 200", r.status_code == 200, r.text[:200])
chk("первая запись — не рекорд", r.json()["prs"] == [], r.json()["prs"])
chk("1RM по Эпли 20x12 = 28", r.json()["set"]["is_pr"] is False and r.json()["set"]["reps"] == 12, r.json()["set"])
chk("записан 1RM в БД",
    q(lambda db: db.query(M.TrainerSetLog).filter(M.TrainerSetLog.session_exercise_id == BENCH_SX,
                                                  M.TrainerSetLog.set_index == 2).first().est_1rm) == 28.0)
chk("рекорды созданы молча",
    q(lambda db: db.query(M.TrainerRecord).filter(M.TrainerRecord.exercise_id == EX["db_bench_press"]).count()) == 4)

r = save_set(BENCH_SX, 3, weight_kg=22, reps=12)
chk("улучшение -> PR", r.status_code == 200 and r.json()["prs"], r.text[:200])
pr_types = {p["type"] for p in r.json()["prs"]}
chk("типы PR", pr_types == {"max_weight", "est_1rm", "set_volume"}, pr_types)
chk("PR: прошлое значение", [p for p in r.json()["prs"] if p["type"] == "max_weight"][0]["prev_value"] == 20.0,
    r.json()["prs"])
chk("PR: имя упражнения", r.json()["prs"][0]["exercise_name_ru"], r.json()["prs"][0])
chk("сет помечен рекордом", r.json()["set"]["is_pr"] is True, r.json()["set"])
chk("рекорд обновлён",
    q(lambda db: db.query(M.TrainerRecord).filter(
        M.TrainerRecord.exercise_id == EX["db_bench_press"],
        M.TrainerRecord.record_type == "max_weight").first().value) == 22.0)

r = save_set(BENCH_SX, 4, weight_kg=20, reps=5)
chk("слабый подход без PR", r.json()["prs"] == [], r.json()["prs"])
chk("план закрыт (3 рабочих)", r.json()["exercise_status"] == "done", r.json()["exercise_status"])

# Upsert: тот же set_index перезаписывает строку, а не добавляет новую.
r = save_set(BENCH_SX, 4, weight_kg=20, reps=6)
chk("upsert -> 200", r.status_code == 200, r.text[:200])
chk("сетов у жима 4", q(lambda db: db.query(M.TrainerSetLog).filter(
    M.TrainerSetLog.session_exercise_id == BENCH_SX).count()) == 4)
chk("значение обновилось", r.json()["set"]["reps"] == 6, r.json()["set"])

sess = c.get(f"/trainer/session/{SID}").json()
chk("объём без разминки: 20*12+22*12+20*6 = 624", sess["total_volume_kg"] == 624.0, sess["total_volume_kg"])
chk("подходов 3 (разминочные не в счёт)", sess["total_sets"] == 3, sess["total_sets"])
chk("повторов 30", sess["total_reps"] == 30, sess["total_reps"])
chk("рекорды сессии сохранены", len(sess["prs"]) == 3, sess["prs"])

# Чужая сессия и чужое упражнение → 404, завершённая → 409.
chk("несуществующая сессия -> 404",
    c.post("/trainer/session/999999/set", json={"session_exercise_id": BENCH_SX, "set_index": 1}).status_code == 404)
chk("упражнение из другой сессии -> 404", save_set(OLD_SX, 1, weight_kg=10, reps=10).status_code == 404)
r = c.post(f"/trainer/session/{OLD_SID}/set",
           json={"session_exercise_id": OLD_SX, "set_index": 3, "weight_kg": 10, "reps": 10})
chk("запись в завершённую сессию -> 409", r.status_code == 409, r.status_code)
chk("set_index 13 -> 422", save_set(BENCH_SX, 13, weight_kg=20, reps=10).status_code == 422)
chk("вес 900 кг -> 422", save_set(BENCH_SX, 5, weight_kg=900, reps=1).status_code == 422)

# Снятие отметки ✓ и удаление подхода.
r = save_set(BENCH_SX, 4, weight_kg=20, reps=6, is_done=False)
chk("снятая отметка -> 200", r.status_code == 200 and r.json()["set"]["is_done"] is False, r.text[:160])
chk("после снятия план не закрыт", r.json()["exercise_status"] == "pending", r.json()["exercise_status"])
r = save_set(BENCH_SX, 4, weight_kg=20, reps=6)
chk("отметка вернулась", r.json()["exercise_status"] == "done", r.json()["exercise_status"])

warm_set_id = q(lambda db: db.query(M.TrainerSetLog).filter(
    M.TrainerSetLog.session_exercise_id == BENCH_SX, M.TrainerSetLog.set_index == 1).first().id)
r = c.delete(f"/trainer/session/{SID}/set/{warm_set_id}")
chk("удаление подхода -> 200", r.status_code == 200 and r.json()["ok"] is True, r.text[:160])
chk("сетов у жима стало 3", q(lambda db: db.query(M.TrainerSetLog).filter(
    M.TrainerSetLog.session_exercise_id == BENCH_SX).count()) == 3)
chk("объём после удаления разминочного не изменился",
    c.get(f"/trainer/session/{SID}").json()["total_volume_kg"] == 624.0)
chk("удаление чужого подхода -> 404", c.delete(f"/trainer/session/{SID}/set/999999").status_code == 404)


# --------------------------------------------------------------------------- #
#  5. Замена, пропуск, добавление упражнения
# --------------------------------------------------------------------------- #
r = c.get(f"/trainer/exercises/{EX['goblet_squat']}/alternatives?reason=pain&session_id={SID}")
chk("альтернативы -> 200", r.status_code == 200, r.text[:200])
alts = r.json()["items"]
chk("альтернативы есть и не больше 5", 0 < len(alts) <= 5, len(alts))
ALT_ID = alts[0]["id"]

r = c.post(f"/trainer/session/{SID}/exercise/{GOBLET_SX}/replace",
           json={"new_exercise_id": ALT_ID, "reason": "pain", "remember": True})
chk("replace -> 200", r.status_code == 200, r.text[:300])
new_sx = r.json() if r.status_code == 200 else {}
NEW_SX = new_sx.get("id")
chk("новая строка с тем же порядком", new_sx.get("order_index") == sx["goblet_squat"]["order_index"],
    (new_sx.get("order_index"), sx["goblet_squat"]["order_index"]))
chk("новое упражнение другое", (new_sx.get("exercise") or {}).get("id") == ALT_ID, new_sx.get("exercise"))
chk("цели перенесены", (new_sx.get("planned_sets"), new_sx.get("planned_reps_min")) == (3, 10), new_sx)
chk("старая строка replaced",
    q(lambda db: db.query(M.TrainerSessionExercise).filter(
        M.TrainerSessionExercise.id == GOBLET_SX).first().status) == "replaced")
state_goblet = q(lambda db: db.query(M.TrainerExerciseState).filter(
    M.TrainerExerciseState.telegram_id == TID,
    M.TrainerExerciseState.exercise_id == EX["goblet_squat"]).first())
chk("замена запомнена", state_goblet is not None and state_goblet.preferred_alternative_id == ALT_ID,
    state_goblet and state_goblet.preferred_alternative_id)
chk("причина «болит» -> excluded", state_goblet is not None and bool(state_goblet.excluded) is True)
chk("замена на то же упражнение -> 409",
    c.post(f"/trainer/session/{SID}/exercise/{NEW_SX}/replace",
           json={"new_exercise_id": ALT_ID}).status_code == 409)
chk("мусорная причина -> 422",
    c.post(f"/trainer/session/{SID}/exercise/{NEW_SX}/replace",
           json={"new_exercise_id": EX["db_row"], "reason": "потому что"}).status_code == 422)

for idx in (1, 2, 3):
    rr = save_set(NEW_SX, idx, weight_kg=24, reps=15)
    chk(f"подход {idx} замены -> 200", rr.status_code == 200, rr.text[:160])
chk("замена выполнена", q(lambda db: db.query(M.TrainerSessionExercise).filter(
    M.TrainerSessionExercise.id == NEW_SX).first().status) == "done")

r = c.post(f"/trainer/session/{SID}/exercise/{PLANK_SX}/skip")
chk("skip -> 200", r.status_code == 200 and r.json()["status"] == "skipped", r.text[:160])
chk("пропуск в БД", q(lambda db: db.query(M.TrainerSessionExercise).filter(
    M.TrainerSessionExercise.id == PLANK_SX).first().status) == "skipped")
chk("skip чужого упражнения -> 404",
    c.post(f"/trainer/session/{SID}/exercise/999999/skip").status_code == 404)

r = c.post(f"/trainer/session/{SID}/exercise/add",
           json={"exercise_id": EX["db_row"], "sets": 3, "reps_min": 10, "reps_max": 12})
chk("add -> 200", r.status_code == 200, r.text[:300])
added = r.json() if r.status_code == 200 else {}
chk("добавлено в конец", added.get("order_index") == 6, added.get("order_index"))
chk("добавлено в основной блок", added.get("block") == "main", added.get("block"))
chk("цели добавленного", (added.get("planned_sets"), added.get("planned_reps_max")) == (3, 12), added)
chk("add с 9 подходами -> 422",
    c.post(f"/trainer/session/{SID}/exercise/add", json={"exercise_id": EX["db_row"], "sets": 9}).status_code == 422)


# --------------------------------------------------------------------------- #
#  6. Завершение: Workout, дневник, день программы, состояние прогрессии
# --------------------------------------------------------------------------- #
workouts_before = q(lambda db: db.query(M.Workout).filter(M.Workout.telegram_id == TID).count())
r = c.post(f"/trainer/session/{SID}/finish", json={"duration_min": 45})
chk("finish -> 200", r.status_code == 200, r.text[:300])
fin = r.json() if r.status_code == 200 else {}
summary = fin.get("summary", {})
chk("сессия completed", (fin.get("session") or {}).get("status") == "completed", fin.get("session", {}).get("status"))
chk("длительность 45", summary.get("duration_min") == 45, summary)
chk("подходов 6", summary.get("total_sets") == 6, summary)
chk("объём 624 + 3*360 = 1704", summary.get("total_volume_kg") == 1704.0, summary)
chk("калории по MET (5.0 * 80 * 0.75 = 300)", summary.get("calories_burned") == 300, summary)
chk("выполнено 2 упражнения", summary.get("exercises_done") == 2, summary)
chk("пропущено 1", summary.get("exercises_skipped") == 1, summary)
chk("рекорды в итоге", len(fin.get("prs", [])) == 3, fin.get("prs"))
chk("workout_id вернулся", isinstance(fin.get("workout_id"), int), fin.get("workout_id"))

wk = q(lambda db: db.query(M.Workout).filter(M.Workout.id == fin.get("workout_id")).first())
chk("Workout создан", wk is not None)
if wk is not None:
    chk("Workout: тип strength", wk.type == "strength", wk.type)
    chk("Workout: дата сессии", wk.date == D(0), wk.date)
    chk("Workout: длительность", wk.duration_min == 45, wk.duration_min)
    chk("Workout: калории > 0", (wk.calories_burned or 0) > 0, wk.calories_burned)
    chk("Workout: описание «Тренер: …»", (wk.description or "").startswith("Тренер:"), wk.description)
    chk("Workout: название дня в описании", "День 1" in (wk.description or ""), wk.description)
chk("создана ровно одна тренировка",
    q(lambda db: db.query(M.Workout).filter(M.Workout.telegram_id == TID).count()) == workouts_before + 1)

diary = c.get(f"/diary/{D(0)}")
chk("дневник -> 200", diary.status_code == 200, diary.text[:200])
chk("дневник видит сожжённые", diary.json()["total_burned"] == 300, diary.json()["total_burned"])
chk("баланс дня учитывает тренировку", diary.json()["net_calories"] == -300, diary.json()["net_calories"])

day1 = q(lambda db: db.query(M.TrainerProgramDay).filter(M.TrainerProgramDay.id == DAY_IDS[(1, 1)]).first())
chk("день программы выполнен", day1.status == "done", day1.status)
chk("день ссылается на сессию", day1.session_id == SID, day1.session_id)

st_bench = q(lambda db: db.query(M.TrainerExerciseState).filter(
    M.TrainerExerciseState.telegram_id == TID,
    M.TrainerExerciseState.exercise_id == EX["db_bench_press"]).first())
chk("рабочий вес = медиана (20, 22, 20)", st_bench is not None and st_bench.working_weight_kg == 20.0,
    st_bench and st_bench.working_weight_kg)
chk("результат жима — fail (6 < 8 повторов)", st_bench is not None and st_bench.last_result == "fail",
    st_bench and st_bench.last_result)
chk("состояние помнит сессию", st_bench is not None and st_bench.last_session_id == SID)
chk("повторный finish -> 409", c.post(f"/trainer/session/{SID}/finish", json={}).status_code == 409)
chk("после finish активной сессии нет", c.get("/trainer/session/active").json() is None)


# --------------------------------------------------------------------------- #
#  7. Отзыв: адаптация правилами, идемпотентность, RU/EN
# --------------------------------------------------------------------------- #
r = c.post(f"/trainer/session/{SID}/feedback", json={"feedback": "hard", "note": "тяжело шло"})
chk("feedback -> 200", r.status_code == 200, r.text[:300])
adapt = r.json() if r.status_code == 200 else {}
chk("есть строки объяснений", bool(adapt.get("lines")), adapt)
chk("сообщение на русском", "Учёл" in (adapt.get("message") or ""), adapt.get("message"))
deload = [ch for ch in adapt.get("changes", []) if ch["kind"] == "deload"]
chk("жим: откат после fail + «тяжело»", deload and deload[0]["new_value"] == 18.0, adapt.get("changes"))
chk("строка объяснения по-русски", any("тяжело" in l for l in adapt.get("lines", [])), adapt.get("lines"))
st_bench = q(lambda db: db.query(M.TrainerExerciseState).filter(
    M.TrainerExerciseState.exercise_id == EX["db_bench_press"]).first())
chk("состояние: 20 -> 18 кг (-10%)", st_bench.working_weight_kg == 18.0, st_bench.working_weight_kg)
chk("счётчик неудач сброшен", (st_bench.fail_streak or 0) == 0, st_bench.fail_streak)
chk("заметка отзыва сохранена",
    q(lambda db: db.query(M.TrainerSession).filter(M.TrainerSession.id == SID).first().feedback_note) == "тяжело шло")

# Повторный вызов пересчитывает от снимка «до» — вес не «съезжает» дальше.
r = c.post(f"/trainer/session/{SID}/feedback", json={"feedback": "hard"})
chk("повторный feedback -> 200", r.status_code == 200, r.text[:200])
st_bench = q(lambda db: db.query(M.TrainerExerciseState).filter(
    M.TrainerExerciseState.exercise_id == EX["db_bench_press"]).first())
chk("повторный отзыв идемпотентен", st_bench.working_weight_kg == 18.0, st_bench.working_weight_kg)
chk("отзыв сохранён в сессии",
    q(lambda db: db.query(M.TrainerSession).filter(M.TrainerSession.id == SID).first().feedback) == "hard")
chk("адаптация видна в сессии",
    bool((c.get(f"/trainer/session/{SID}").json().get("adaptation") or {}).get("lines")))

# Правки уехали в тот же день следующей недели и не задваиваются.
day21 = q(lambda db: db.query(M.TrainerProgramDay).filter(M.TrainerProgramDay.id == DAY_IDS[(2, 1)]).first())
adj = json.loads(day21.adjustments_json or "{}")
chk("правка отзыва в неделе 2", len(adj.get("items", [])) == 1, adj)
chk("правка про жим", adj.get("items", [{}])[0].get("exercise_id") == EX["db_bench_press"], adj)
chk("правка -5% на компаунд", adj.get("items", [{}])[0].get("weight_pct") == -5, adj)

# Английский язык: те же изменения, строки на EN.
db = SessionLocal(); u = db.query(M.User).filter(M.User.telegram_id == TID).first(); u.language = "en"; db.commit(); db.close()
r = c.post(f"/trainer/session/{SID}/feedback", json={"feedback": "hard"})
chk("EN feedback -> 200", r.status_code == 200, r.text[:200])
chk("EN строки", any("hard" in l for l in r.json().get("lines", [])), r.json().get("lines"))
chk("EN сообщение", "Noted" in (r.json().get("message") or ""), r.json().get("message"))
db = SessionLocal(); u = db.query(M.User).filter(M.User.telegram_id == TID).first(); u.language = "ru"; db.commit(); db.close()
day21 = q(lambda db: db.query(M.TrainerProgramDay).filter(M.TrainerProgramDay.id == DAY_IDS[(2, 1)]).first())
chk("правки не задвоились после трёх отзывов",
    len(json.loads(day21.adjustments_json or "{}").get("items", [])) == 1,
    day21.adjustments_json)
chk("feedback чужой сессии -> 404",
    c.post("/trainer/session/999999/feedback", json={"feedback": "ok"}).status_code == 404)
chk("мусорный отзыв -> 422",
    c.post(f"/trainer/session/{SID}/feedback", json={"feedback": "устал"}).status_code == 422)


# --------------------------------------------------------------------------- #
#  8. Отмена: Workout не создаётся, день остаётся плановым
# --------------------------------------------------------------------------- #
workouts_before = q(lambda db: db.query(M.Workout).filter(M.Workout.telegram_id == TID).count())
r = c.post("/trainer/session/start", json={"program_day_id": DAY_IDS[(1, 2)], "date": D(2)})
chk("старт второй тренировки -> 200", r.status_code == 200, r.text[:200])
SID2 = r.json().get("id")
chk("feedback до завершения -> 409",
    c.post(f"/trainer/session/{SID2}/feedback", json={"feedback": "ok"}).status_code == 409)
r = c.post(f"/trainer/session/{SID2}/abandon")
chk("abandon -> 200", r.status_code == 200 and r.json()["status"] == "abandoned", r.text[:160])
chk("статус abandoned", q(lambda db: db.query(M.TrainerSession).filter(
    M.TrainerSession.id == SID2).first().status) == "abandoned")
chk("Workout не создан",
    q(lambda db: db.query(M.Workout).filter(M.Workout.telegram_id == TID).count()) == workouts_before)
chk("день остался плановым", q(lambda db: db.query(M.TrainerProgramDay).filter(
    M.TrainerProgramDay.id == DAY_IDS[(1, 2)]).first().status) == "planned")
chk("после abandon активной нет", c.get("/trainer/session/active").json() is None)
chk("abandon завершённой -> 409", c.post(f"/trainer/session/{SID}/abandon").status_code == 409)


# --------------------------------------------------------------------------- #
#  9. История сессий: только завершённые, пагинация
# --------------------------------------------------------------------------- #
r = c.post("/trainer/session/start", json={"program_day_id": DAY_IDS[(1, 3)], "date": D(4)})
SID3 = r.json().get("id")
bench3 = [e for e in r.json()["exercises"] if (e.get("exercise") or {}).get("slug") == "db_bench_press"][0]
c.post(f"/trainer/session/{SID3}/set", json={"session_exercise_id": bench3["id"], "set_index": 1,
                                             "set_type": "work", "weight_kg": 18, "reps": 12, "is_done": True})
r = c.post(f"/trainer/session/{SID3}/finish", json={"duration_min": 30})
chk("вторая тренировка завершена", r.status_code == 200, r.text[:200])

r = c.get("/trainer/sessions?limit=1&offset=0")
chk("sessions -> 200", r.status_code == 200, r.text[:200])
page1 = r.json()
chk("всего завершённых 3 (включая старую)", page1["total"] == 3, page1["total"])
chk("на странице одна", len(page1["items"]) == 1, len(page1["items"]))
chk("свежая сверху", page1["items"][0]["id"] == SID3, page1["items"][0])
chk("в карточке есть объём и калории",
    page1["items"][0]["total_volume_kg"] > 0 and page1["items"][0]["calories_burned"] > 0, page1["items"][0])
page2 = c.get("/trainer/sessions?limit=1&offset=1").json()
chk("вторая страница другая", page2["items"][0]["id"] != SID3, page2["items"][0])
chk("количество PR в карточке",
    [i for i in c.get("/trainer/sessions").json()["items"] if i["id"] == SID][0]["prs_count"] == 3)
chk("отменённой сессии в истории нет",
    all(i["id"] != SID2 for i in c.get("/trainer/sessions").json()["items"]))


# --------------------------------------------------------------------------- #
#  10. Неделя разгрузки: вес и подходы снижены, замена подставлена
# --------------------------------------------------------------------------- #
st_bench = q(lambda db: db.query(M.TrainerExerciseState).filter(
    M.TrainerExerciseState.exercise_id == EX["db_bench_press"]).first())
base_weight = st_bench.working_weight_kg
r = c.post("/trainer/session/start", json={"program_day_id": DAY_IDS[(2, 1)], "date": D(7)})
chk("старт недели 2 -> 200", r.status_code == 200, r.text[:300])
S2 = r.json() if r.status_code == 200 else {}
sx2 = {(e.get("exercise") or {}).get("slug"): e for e in S2.get("exercises", [])}
bench2 = sx2.get("db_bench_press", {})
chk("deload снизил вес", (bench2.get("planned_weight_kg") or 0) < base_weight,
    (bench2.get("planned_weight_kg"), base_weight))
chk("deload + правка отзыва: 18 * 0.80 = 14.4 -> 14",
    bench2.get("planned_weight_kg") == 14.0, bench2.get("planned_weight_kg"))
chk("deload убрал один подход", bench2.get("planned_sets") == 2, bench2.get("planned_sets"))
chk("исключённое упражнение заменено",
    "goblet_squat" not in sx2 and any(
        (e.get("exercise") or {}).get("id") == ALT_ID for e in S2.get("exercises", [])),
    list(sx2))
chk("цели жима из состояния (повторы)", bench2.get("planned_reps_min") == st_bench.target_reps_min,
    (bench2.get("planned_reps_min"), st_bench.target_reps_min))
c.post(f"/trainer/session/{S2.get('id')}/abandon")


if fails:
    print("FAIL:"); [print("  -", f) for f in fails]; sys.exit(1)
print("OK: премиум-гейт 402 на 12 маршрутах сессии, старт (цели плана+состояния,")
print("    «прошлый раз» без разминочных, 409 со ссылкой на активную), подходы")
print("    (upsert, объём без разминки, 1RM, PR только при улучшении, 404/409/422),")
print("    замена (replaced + preferred_alternative_id + pain->excluded), пропуск,")
print("    добавление, finish (400 без сетов, Workout + /diary total_burned, день")
print("    done, медиана веса), отзыв (−10% после fail, идемпотентность, RU/EN,")
print("    правка в неделю+1), abandon без Workout, история и deload-неделя")
