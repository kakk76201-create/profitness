"""Проверки недельного разбора тренера (ТЗ §4.5, §5.3, §8.1).

Покрыто: премиум-гейт 402; 409 при нулевом числе тренировок за неделю (и ИИ не
дёргается); контекст промпта (ккал/белок из дневника, вес из WeightLog, план vs
факт, каталог для swap); теги и лимит токенов; нормализация правок (clamp −40 →
−15, мусорный тип и swap на чужую мышцу отброшены); сохранение и /review/latest;
регенерация перезаписывает разбор; apply правит следующую неделю программы и
состояние упражнения, идемпотентен; EN-промпт и EN-дисклеймер.
"""
import os, sys, tempfile, pathlib, json
from datetime import date as _date, timedelta
from unittest import mock

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "trrev.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"

from fastapi.testclient import TestClient
from backend.database import init_db, SessionLocal
from backend import models as M, ai_service, ratelimit
from backend.main import app

init_db()
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

def reset_limits():
    """Сбросить счётчики rate-limit между вызовами (heavy — всего 2/мин)."""
    ratelimit._minute_hits.clear(); ratelimit._day_hits.clear()

TID = 1
today = _date.today()
monday = today - timedelta(days=today.weekday())
ISO = lambda d: d.isoformat()

captured = {}
calls = {"n": 0}
REVIEW = {
    "summary": "Неделя ровная: три тренировки из трёх.",
    "wins": ["Все тренировки сделаны"],
    "issues": ["Белка меньше цели"],
    "nutrition": ["Добавь 30 г белка в обед"],
    "changes": [
        {"type": "weight_pct", "exercise_slug": "db_bench_press", "value": -40, "reason": "два раза не добил"},
        {"type": "sets", "exercise_slug": "db_row", "value": 1, "reason": "спина недогружена"},
        {"type": "телепатия", "exercise_slug": "lat_pulldown", "value": 5, "reason": "мусорный тип"},
        {"type": "swap", "exercise_slug": "plank", "new_slug": "db_bench_press", "reason": "чужая мышца"},
    ],
    "next_week_focus": "Спина и кор",
    "motivation": "Так держать!",
}

def fake_run(system_prompt, user_prompt, log_tag, max_tokens=None):
    calls["n"] += 1
    captured["system"] = system_prompt; captured["user"] = user_prompt
    captured["tag"] = log_tag; captured["max_tokens"] = max_tokens
    return json.loads(json.dumps(REVIEW)), {}


# --------------------------------------------------------------------------- #
#  1. Free-пользователь: 402
# --------------------------------------------------------------------------- #
for name, resp in [
    ("review/weekly", c.post("/trainer/review/weekly", json={})),
    ("review/latest", c.get("/trainer/review/latest")),
    ("review/apply", c.post("/trainer/review/1/apply", json={"change_ids": [0]})),
]:
    chk(f"free {name} -> 402", resp.status_code == 402, resp.status_code)


# --------------------------------------------------------------------------- #
#  2. Премиум, анкета, программа 2 недели × 3 дня
# --------------------------------------------------------------------------- #
db = SessionLocal(); u = db.query(M.User).filter(M.User.telegram_id == TID).first()
u.subscription_type = "lifetime"; u.language = "ru"
u.weight = 80.0; u.height = 180.0; u.age = 30; u.gender = "male"
u.diet_goal = "muscle"; u.daily_goal_kcal = 2600; u.target_proteins = 150
db.commit(); db.close()

r = c.post("/trainer/profile", json={
    "goal": "muscle", "level": "beginner", "equipment": "gym",
    "equipment_extra": ["bench"], "days_per_week": 3, "preferred_weekdays": [0, 2, 4],
    "session_minutes": 45, "program_weeks": 6, "limitations": ["knee"],
    "limitations_text": "старая травма колена", "focus": ["back"], "reminder_enabled": False,
})
chk("анкета сохранена", r.status_code == 200, r.text[:200])

EX = q(lambda db: {e.slug: e.id for e in db.query(M.TrainerExercise).all()})
BENCH, ROW = EX["db_bench_press"], EX["db_row"]

PLAN = [
    {"slug": "db_bench_press", "exercise_id": BENCH, "sets": 3, "reps_min": 8, "reps_max": 12,
     "rest_sec": 90, "start_weight_kg": 30, "order": 1},
    {"slug": "db_row", "exercise_id": ROW, "sets": 3, "reps_min": 10, "reps_max": 15,
     "rest_sec": 90, "start_weight_kg": 24, "order": 2},
]

db = SessionLocal()
program = M.TrainerProgram(
    telegram_id=TID, status="active", title="Тест-программа", split_type="full_body",
    goal="muscle", level="beginner", equipment="gym", weeks=2, days_per_week=3,
    start_date=ISO(monday), end_date=ISO(monday + timedelta(days=11)),
    periodization_json=json.dumps([
        {"week": 1, "phase": "base", "weight_pct": 100, "sets_delta": 0},
        {"week": 2, "phase": "build", "weight_pct": 100, "sets_delta": 0},
    ]),
)
db.add(program); db.flush()
PROGRAM_ID = program.id
DAY_IDS = {}
for week in (1, 2):
    for di, offset in ((1, 0), (2, 2), (3, 4)):
        day = M.TrainerProgramDay(
            telegram_id=TID, program_id=program.id, week=week, day_index=di,
            weekday=(monday + timedelta(days=offset)).weekday(),
            scheduled_date=ISO(monday + timedelta(days=offset + (week - 1) * 7)),
            title=f"День {di}", session_type="strength", duration_min=45,
            focus_muscles_json=json.dumps(["chest"]),
            warmup_json="[]", exercises_json=json.dumps(PLAN), cooldown_json="[]",
            status="planned",
        )
        db.add(day); db.flush()
        DAY_IDS[(week, di)] = day.id
# Состояние прогрессии по жиму — его тоже должен поправить apply.
db.add(M.TrainerExerciseState(telegram_id=TID, exercise_id=BENCH, working_weight_kg=30.0,
                              target_reps_min=8, target_reps_max=12))
db.commit(); db.close()


# --------------------------------------------------------------------------- #
#  3. Нет сессий за неделю -> 409, ИИ не дёргается
# --------------------------------------------------------------------------- #
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.post("/trainer/review/weekly", json={"date": ISO(today)})
chk("0 сессий -> 409", r.status_code == 409, r.text[:200])
chk("0 сессий: ИИ не вызван", calls["n"] == 0, calls["n"])


# --------------------------------------------------------------------------- #
#  4. Данные недели: сессия, дневник, вес
# --------------------------------------------------------------------------- #
db = SessionLocal()
session = M.TrainerSession(
    telegram_id=TID, program_id=PROGRAM_ID, program_day_id=DAY_IDS[(1, 1)],
    date=ISO(monday), status="completed", title="День 1", session_type="strength",
    week=1, day_index=1, duration_min=48, total_sets=6, total_reps=60,
    total_volume_kg=1080.0, calories_burned=320, feedback="hard",
    feedback_note="тяжело шло",
)
db.add(session); db.flush()
sex = M.TrainerSessionExercise(
    telegram_id=TID, session_id=session.id, exercise_id=BENCH, block="main", order_index=1,
    planned_sets=3, planned_reps_min=8, planned_reps_max=12, planned_weight_kg=30.0,
    status="done",
)
db.add(sex); db.flush()
for i in range(1, 4):
    db.add(M.TrainerSetLog(
        telegram_id=TID, session_id=session.id, session_exercise_id=sex.id, exercise_id=BENCH,
        date=ISO(monday), set_index=i, set_type="work", weight_kg=30.0, reps=8,
        is_done=True, volume_kg=240.0,
    ))
day_row = db.query(M.TrainerProgramDay).filter(M.TrainerProgramDay.id == DAY_IDS[(1, 1)]).first()
day_row.status = "done"; day_row.session_id = session.id

for meal, kcal, prot in (("breakfast", 800, 50.0), ("lunch", 1200, 70.0)):
    db.add(M.DiaryEntry(telegram_id=TID, date=ISO(monday), meal_type=meal,
                        dish_name="еда", calories=kcal, proteins=prot, fats=20.0, carbs=100.0))
db.add(M.WeightLog(telegram_id=TID, date=ISO(monday - timedelta(days=7)), weight=81.0))
db.add(M.WeightLog(telegram_id=TID, date=ISO(monday), weight=80.0))
db.commit(); db.close()


# --------------------------------------------------------------------------- #
#  5. POST /trainer/review/weekly
# --------------------------------------------------------------------------- #
reset_limits()
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.post("/trainer/review/weekly", json={"date": ISO(today)})
chk("разбор -> 200", r.status_code == 200, r.text[:300])
rev = r.json() if r.status_code == 200 else {}
REVIEW_ID = rev.get("id")

chk("tag телеметрии", captured.get("tag") == "trainer_review", captured.get("tag"))
chk("лимит токенов 1500", captured.get("max_tokens") == 1500, captured.get("max_tokens"))
user_prompt = captured.get("user", "")
chk("в промпте средние ккал из дневника", '"avg_kcal": 2000' in user_prompt, user_prompt[:400])
chk("в промпте белок из дневника", '"avg_protein": 120' in user_prompt, user_prompt[:400])
chk("в промпте вес из WeightLog", '"first": 81.0' in user_prompt and '"last": 80.0' in user_prompt,
    user_prompt[:400])
chk("в промпте цель по ккал", '"goal_kcal": 2600' in user_prompt, user_prompt[:400])
chk("в промпте план vs факт", '"done": 1' in user_prompt, user_prompt[:400])
chk("в промпте отзыв сессии", "hard" in user_prompt, user_prompt[:200])
chk("в промпте ограничения", "knee" in user_prompt, user_prompt[:200])
chk("в промпте каталог для swap", "db_bench_press | " in user_prompt, user_prompt[-400:])
chk("правила безопасности в system", "не врач" in captured.get("system", "").lower(),
    captured.get("system", "")[:200])

chk("неделя разбора — текущая", rev.get("week_start") == ISO(monday), rev.get("week_start"))
chk("конец недели", rev.get("week_end") == ISO(monday + timedelta(days=6)), rev.get("week_end"))
chk("номер недели программы", rev.get("week") == 1, rev.get("week"))
chk("дисклеймер RU", "не врач" in (rev.get("disclaimer") or "").lower(), rev.get("disclaimer"))
chk("applied=False", rev.get("applied") is False, rev.get("applied"))

body = rev.get("review", {})
chk("summary есть", "ровная" in (body.get("summary") or ""), body.get("summary"))
chk("wins/issues/nutrition", body.get("wins") and body.get("issues") and body.get("nutrition"), body)
changes = body.get("changes", [])
chk("две валидные правки (мусор и чужая мышца отброшены)", len(changes) == 2,
    [ch.get("type") for ch in changes])
if len(changes) == 2:
    chk("weight_pct clamp −40 → −15", changes[0]["type"] == "weight_pct" and changes[0]["value"] == -15,
        changes[0])
    chk("правка знает упражнение", changes[0]["exercise_id"] == BENCH, changes[0])
    chk("sets +1", changes[1]["type"] == "sets" and changes[1]["value"] == 1, changes[1])
    chk("id правок 0 и 1", [ch["id"] for ch in changes] == [0, 1], [ch["id"] for ch in changes])
    chk("правки ещё не применены", all(ch["applied"] is False for ch in changes), changes)

stats = rev.get("stats", {})
chk("stats без каталога (не тащим сотню строк)", "catalog" not in stats, list(stats)[:15])
chk("stats с сессиями", len(stats.get("sessions", [])) == 1, stats.get("sessions"))


# --------------------------------------------------------------------------- #
#  6. GET /trainer/review/latest
# --------------------------------------------------------------------------- #
r = c.get("/trainer/review/latest")
chk("latest -> 200", r.status_code == 200, r.text[:200])
chk("latest — тот же разбор", r.json().get("id") == REVIEW_ID, r.json().get("id"))

# Регенерация: та же неделя перезаписывает строку, а не плодит новые.
reset_limits()
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r2 = c.post("/trainer/review/weekly", json={"date": ISO(today)})
chk("регенерация -> 200", r2.status_code == 200, r2.text[:200])
chk("регенерация: тот же id", r2.json().get("id") == REVIEW_ID, r2.json().get("id"))
count = q(lambda db: db.query(M.TrainerWeeklyReview).filter(M.TrainerWeeklyReview.telegram_id == TID).count())
chk("один разбор на неделю", count == 1, count)


# --------------------------------------------------------------------------- #
#  7. POST /trainer/review/{id}/apply
# --------------------------------------------------------------------------- #
r = c.post(f"/trainer/review/{REVIEW_ID}/apply", json={"change_ids": [0, 1]})
chk("apply -> 200", r.status_code == 200, r.text[:300])
ap = r.json() if r.status_code == 200 else {}
chk("применены обе правки", ap.get("applied") == [0, 1], ap.get("applied"))
chk("строки объяснений на русском", ap.get("lines") and any("кг" in x for x in ap["lines"]), ap.get("lines"))
chk("правки уехали в неделю 2", ap.get("next_week") == 2, ap.get("next_week"))

def week2_plan():
    return q(lambda db: [
        json.loads(d.exercises_json)
        for d in db.query(M.TrainerProgramDay)
        .filter(M.TrainerProgramDay.program_id == PROGRAM_ID, M.TrainerProgramDay.week == 2)
        .order_by(M.TrainerProgramDay.day_index).all()
    ])

plan2 = week2_plan()
bench2 = [it for day in plan2 for it in day if it["slug"] == "db_bench_press"]
row2 = [it for day in plan2 for it in day if it["slug"] == "db_row"]
chk("вес жима недели 2: 30 → 26 кг (−15%, шаг 2)", all(it["start_weight_kg"] == 26.0 for it in bench2),
    [it["start_weight_kg"] for it in bench2])
chk("подходы тяги недели 2: 3 → 4", all(it["sets"] == 4 for it in row2), [it["sets"] for it in row2])
plan1 = q(lambda db: json.loads(
    db.query(M.TrainerProgramDay).filter(M.TrainerProgramDay.id == DAY_IDS[(1, 2)]).first().exercises_json))
chk("неделя 1 не тронута", plan1[0]["start_weight_kg"] == 30, plan1[0])

state = q(lambda db: db.query(M.TrainerExerciseState).filter(
    M.TrainerExerciseState.telegram_id == TID, M.TrainerExerciseState.exercise_id == BENCH).first())
chk("рабочий вес состояния: 30 → 26", state.working_weight_kg == 26.0, state.working_weight_kg)
chk("разбор помечен applied", q(lambda db: db.query(M.TrainerWeeklyReview).filter(
    M.TrainerWeeklyReview.id == REVIEW_ID).first().applied) is True)

# Повторный apply тех же правок ничего не меняет.
r = c.post(f"/trainer/review/{REVIEW_ID}/apply", json={"change_ids": [0, 1]})
chk("повторный apply -> 200", r.status_code == 200, r.text[:200])
chk("повторный apply: те же id", r.json().get("applied") == [0, 1], r.json().get("applied"))
plan2b = week2_plan()
bench2b = [it for day in plan2b for it in day if it["slug"] == "db_bench_press"]
row2b = [it for day in plan2b for it in day if it["slug"] == "db_row"]
chk("идемпотентно: вес не уехал дважды", all(it["start_weight_kg"] == 26.0 for it in bench2b),
    [it["start_weight_kg"] for it in bench2b])
chk("идемпотентно: подходы не уехали дважды", all(it["sets"] == 4 for it in row2b),
    [it["sets"] for it in row2b])
state2 = q(lambda db: db.query(M.TrainerExerciseState).filter(
    M.TrainerExerciseState.telegram_id == TID, M.TrainerExerciseState.exercise_id == BENCH).first())
chk("идемпотентно: состояние прежнее", state2.working_weight_kg == 26.0, state2.working_weight_kg)

# В ответе разбора правки теперь помечены применёнными.
r = c.get("/trainer/review/latest")
chk("latest: правки помечены applied", all(ch["applied"] for ch in r.json()["review"]["changes"]),
    r.json()["review"]["changes"])

r = c.post("/trainer/review/999999/apply", json={"change_ids": [0]})
chk("чужой разбор -> 404", r.status_code == 404, r.status_code)
r = c.post(f"/trainer/review/{REVIEW_ID}/apply", json={"change_ids": ["ой"]})
chk("мусорный change_id -> 422", r.status_code == 422, r.status_code)
r = c.post("/trainer/review/weekly", json={"week_start": "31-02-2026"})
chk("битая дата недели -> 422", r.status_code == 422, r.status_code)


# --------------------------------------------------------------------------- #
#  8. EN: английский system-промпт и английский дисклеймер
# --------------------------------------------------------------------------- #
db = SessionLocal(); u = db.query(M.User).filter(M.User.telegram_id == TID).first()
u.language = "en"; db.commit(); db.close()
reset_limits()
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.post("/trainer/review/weekly", json={"date": ISO(today)})
chk("EN разбор -> 200", r.status_code == 200, r.text[:200])
chk("EN системный промпт", "not a doctor" in captured.get("system", "").lower(),
    captured.get("system", "")[:200])
chk("EN дисклеймер", "not a doctor" in (r.json().get("disclaimer") or "").lower(), r.json().get("disclaimer"))

# Регенерация обнуляет флаг применения — правки нужно применить заново.
chk("после регенерации applied=False", r.json().get("applied") is False, r.json().get("applied"))
r = c.post(f"/trainer/review/{REVIEW_ID}/apply", json={"change_ids": [0]})
chk("EN строки объяснений", r.status_code == 200 and any("kg" in x for x in r.json().get("lines", [])),
    r.text[:200])


# --------------------------------------------------------------------------- #
#  9. Пустой ответ ИИ -> 502, разбор не перезаписан мусором
# --------------------------------------------------------------------------- #
reset_limits()
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: ({}, {})):
    r = c.post("/trainer/review/weekly", json={"date": ISO(today)})
chk("пустой ответ ИИ -> 502", r.status_code == 502, r.status_code)
chk("разбор на месте", q(lambda db: db.query(M.TrainerWeeklyReview).filter(
    M.TrainerWeeklyReview.telegram_id == TID).count()) == 1)

# Третий тяжёлый вызов за минуту -> 429.
reset_limits()
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    c.post("/trainer/review/weekly", json={"date": ISO(today)})
    c.post("/trainer/review/weekly", json={"date": ISO(today)})
    before = calls["n"]
    r = c.post("/trainer/review/weekly", json={"date": ISO(today)})
chk("третий разбор за минуту -> 429", r.status_code == 429, r.status_code)
chk("при 429 ИИ не вызван", calls["n"] == before, (before, calls["n"]))

if fails:
    print("FAIL:"); [print("  -", f) for f in fails]; sys.exit(1)
print("OK: разбор недели (409 без тренировок, контекст ккал/белка/веса в промпте, clamp правок,")
print("    latest, регенерация), apply правит неделю+1 и состояние идемпотентно, EN, 502 и 429")
