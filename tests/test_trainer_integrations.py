"""Интеграции тренера с существующими частями приложения (ТЗ §4.6, §8.1).

Покрыто: `POST /food/suggest` знает про тренировку дня (строка в промпте +
`training_note` в ответе) и молчит, когда тренировки нет; `GET
/trainer/nutrition/today` — вид дня training/rest, кэш `TrainerDailyTip` (второй
вызов без ИИ), цифры из дневника и Workout; `trainer_notify.decorate_training_reminder`
дополняет напоминание планом дня и возвращает исходный текст без программы;
EN-язык во всех трёх местах.
"""
import os, sys, tempfile, pathlib, json
from datetime import date as _date, timedelta
from unittest import mock

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "trint.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"

from fastapi.testclient import TestClient
from backend.database import init_db, SessionLocal
from backend import models as M, ai_service, trainer_notify
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

TID = 1
today = _date.today()
ISO = lambda d: d.isoformat()
TOMORROW = ISO(today + timedelta(days=1))

FOOD = {"suggestions": [{"dish_name": "Курица с рисом", "calories": 500, "proteins": 40.0,
                         "fats": 10.0, "carbs": 50.0, "reason": "добирает белок"}]}
TIP = {"headline": "Тренировочный день", "calories_note": "Норма 2600 ккал",
       "protein_note": "Белок 150 г, съедено 50", "pre_workout": "За 1–2 ч: овсянка с бананом",
       "post_workout": "После: творог и фрукт", "hydration": "2 литра воды",
       "tips": ["Не пропускай белок", "Спи 8 часов"]}

calls = []
captured = {}
def fake_run(system_prompt, user_prompt, log_tag, max_tokens=None):
    calls.append(log_tag)
    captured[log_tag] = {"system": system_prompt, "user": user_prompt, "max_tokens": max_tokens}
    if log_tag == "trainer_nutrition":
        return json.loads(json.dumps(TIP)), {}
    if log_tag == "suggest_food":
        return json.loads(json.dumps(FOOD)), {}
    return {}, {}

SUGGEST_BODY = {"meal_type": "dinner", "remaining_calories": 700, "remaining_proteins": 60.0,
                "remaining_fats": 20.0, "remaining_carbs": 70.0}


# --------------------------------------------------------------------------- #
#  1. Free-пользователь: 402
# --------------------------------------------------------------------------- #
for name, resp in [
    ("nutrition/today", c.get("/trainer/nutrition/today")),
    ("food/suggest", c.post("/food/suggest", json=dict(SUGGEST_BODY))),
]:
    chk(f"free {name} -> 402", resp.status_code == 402, resp.status_code)


# --------------------------------------------------------------------------- #
#  2. Премиум + анкета
# --------------------------------------------------------------------------- #
db = SessionLocal(); u = db.query(M.User).filter(M.User.telegram_id == TID).first()
u.subscription_type = "lifetime"; u.language = "ru"
u.weight = 80.0; u.height = 180.0; u.age = 30; u.gender = "male"
u.diet_goal = "muscle"; u.daily_goal_kcal = 2600; u.target_proteins = 150
db.commit(); db.close()

r = c.post("/trainer/profile", json={
    "goal": "muscle", "level": "beginner", "equipment": "gym", "days_per_week": 3,
    "preferred_weekdays": [0, 2, 4], "session_minutes": 45, "program_weeks": 6,
    "reminder_enabled": False,
})
chk("анкета сохранена", r.status_code == 200, r.text[:200])


# --------------------------------------------------------------------------- #
#  3. Нет тренировок: food/suggest без контекста, напоминание без плана
# --------------------------------------------------------------------------- #
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.post("/food/suggest", json=dict(SUGGEST_BODY, date=ISO(today)))
chk("food/suggest -> 200", r.status_code == 200, r.text[:200])
chk("без тренировки: training_note пуст", r.json().get("training_note") is None, r.json().get("training_note"))
chk("без тренировки: контекста нет в промпте",
    "Контекст тренировок" not in captured.get("suggest_food", {}).get("user", ""),
    captured.get("suggest_food", {}).get("user", "")[:300])

BASE_TEXT = "Пора на тренировку!"
line = q(lambda db: trainer_notify.decorate_training_reminder(db, TID, ISO(today), "ru", BASE_TEXT))
chk("без программы: текст напоминания не тронут", line == BASE_TEXT, line)


# --------------------------------------------------------------------------- #
#  4. Активная программа с тренировкой на сегодня
# --------------------------------------------------------------------------- #
EX = q(lambda db: {e.slug: e.id for e in db.query(M.TrainerExercise).all()})
PLAN = [
    {"slug": "db_bench_press", "exercise_id": EX["db_bench_press"], "sets": 3,
     "reps_min": 8, "reps_max": 12, "rest_sec": 90, "start_weight_kg": 20, "order": 1},
    {"slug": "db_row", "exercise_id": EX["db_row"], "sets": 3, "reps_min": 10,
     "reps_max": 12, "rest_sec": 90, "start_weight_kg": 18, "order": 2},
]
db = SessionLocal()
program = M.TrainerProgram(
    telegram_id=TID, status="active", title="Тест", split_type="full_body",
    goal="muscle", level="beginner", equipment="gym", weeks=2, days_per_week=3,
    start_date=ISO(today), end_date=ISO(today + timedelta(days=13)),
    periodization_json=json.dumps([{"week": 1, "phase": "base", "weight_pct": 100, "sets_delta": 0}]),
)
db.add(program); db.flush()
PROGRAM_ID = program.id
today_day = M.TrainerProgramDay(
    telegram_id=TID, program_id=program.id, week=1, day_index=1, weekday=today.weekday(),
    scheduled_date=ISO(today), title="Верх тела", session_type="strength", duration_min=45,
    warmup_json="[]", exercises_json=json.dumps(PLAN), cooldown_json="[]", status="planned",
)
db.add(today_day); db.flush()
TODAY_DAY_ID = today_day.id
# Питание и сожжённые калории дня.
db.add(M.DiaryEntry(telegram_id=TID, date=ISO(today), meal_type="breakfast",
                    dish_name="овсянка", calories=800, proteins=50.0, fats=20.0, carbs=90.0))
db.add(M.Workout(telegram_id=TID, date=ISO(today), type="strength", duration_min=45,
                 calories_burned=300, description="Тренер: Верх тела"))
db.commit(); db.close()

line = q(lambda db: trainer_notify.decorate_training_reminder(db, TID, ISO(today), "ru", BASE_TEXT))
chk("напоминание: исходный текст сохранён", line.startswith(BASE_TEXT), line)
chk("напоминание: название дня", "День 1 — Верх тела" in line, line)
chk("напоминание: длительность", "45 мин" in line, line)
chk("напоминание: число упражнений", "2 упр." in line, line)
line_en = q(lambda db: trainer_notify.decorate_training_reminder(db, TID, ISO(today), "en", "Time to train!"))
chk("напоминание EN", "Today's plan: Day 1 — Верх тела" in line_en, line_en)
# День без плана — текст не трогаем.
line_rest = q(lambda db: trainer_notify.decorate_training_reminder(db, TID, TOMORROW, "ru", BASE_TEXT))
chk("день без плана: текст не тронут", line_rest == BASE_TEXT, line_rest)

# food/suggest теперь знает про план дня.
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.post("/food/suggest", json=dict(SUGGEST_BODY, date=ISO(today)))
chk("food/suggest с планом -> 200", r.status_code == 200, r.text[:200])
note = r.json().get("training_note")
chk("training_note в ответе", note and "Верх тела" in note, note)
prompt = captured.get("suggest_food", {}).get("user", "")
chk("контекст тренировки в промпте", "Контекст тренировок" in prompt and "тренировк" in prompt.lower(),
    prompt[:400])
chk("цель по белку в контексте", "белку 150" in prompt, prompt[:400])
chk("правило про тренировку в system-промпте",
    "тренировка" in captured.get("suggest_food", {}).get("system", "").lower(),
    captured.get("suggest_food", {}).get("system", "")[:200])
chk("предложения не сломались", len(r.json().get("suggestions", [])) == 1, r.json().get("suggestions"))


# --------------------------------------------------------------------------- #
#  5. GET /trainer/nutrition/today: вид дня, цифры, кэш
# --------------------------------------------------------------------------- #
calls.clear()
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.get("/trainer/nutrition/today", params={"date": ISO(today)})
chk("nutrition/today -> 200", r.status_code == 200, r.text[:300])
tip = r.json() if r.status_code == 200 else {}
chk("вид дня training", tip.get("kind") == "training", tip.get("kind"))
chk("ИИ вызван один раз", calls.count("trainer_nutrition") == 1, calls)
chk("лимит токенов 500", captured.get("trainer_nutrition", {}).get("max_tokens") == 500,
    captured.get("trainer_nutrition", {}).get("max_tokens"))
chk("headline", tip.get("headline") == "Тренировочный день", tip.get("headline"))
chk("до/после тренировки заполнены", tip.get("pre_workout") and tip.get("post_workout"), tip)
chk("советы", len(tip.get("tips", [])) == 2, tip.get("tips"))
nums = tip.get("numbers", {})
chk("цифры: цель 2600", nums.get("goal_kcal") == 2600, nums)
chk("цифры: съедено 800", nums.get("eaten_kcal") == 800, nums)
chk("цифры: белок 50 из 150", nums.get("protein_eaten") == 50 and nums.get("protein_goal") == 150, nums)
chk("цифры: сожжено 300 (из Workout)", nums.get("burned_kcal") == 300, nums)
chk("название дня в промпте ИИ", "Верх тела" in captured.get("trainer_nutrition", {}).get("user", ""),
    captured.get("trainer_nutrition", {}).get("user", "")[:300])

# Второй вызов — из кэша, ИИ не дёргается.
calls.clear()
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r2 = c.get("/trainer/nutrition/today", params={"date": ISO(today)})
chk("повтор -> 200", r2.status_code == 200, r2.text[:200])
chk("повтор: ИИ не вызван (кэш)", calls.count("trainer_nutrition") == 0, calls)
chk("повтор: тот же совет", r2.json().get("headline") == tip.get("headline"), r2.json().get("headline"))
chk("одна строка кэша", q(lambda db: db.query(M.TrainerDailyTip).filter(
    M.TrainerDailyTip.telegram_id == TID, M.TrainerDailyTip.date == ISO(today)).count()) == 1)

# Обновление дневника не ломает кэш, но цифры пересчитываются.
db = SessionLocal()
db.add(M.DiaryEntry(telegram_id=TID, date=ISO(today), meal_type="lunch",
                    dish_name="курица", calories=600, proteins=45.0, fats=15.0, carbs=40.0))
db.commit(); db.close()
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r3 = c.get("/trainer/nutrition/today", params={"date": ISO(today)})
chk("цифры пересчитаны: 1400 ккал", r3.json().get("numbers", {}).get("eaten_kcal") == 1400,
    r3.json().get("numbers"))

# День без плановой тренировки — kind=rest, свой кэш, без pre/post.
calls.clear()
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r4 = c.get("/trainer/nutrition/today", params={"date": TOMORROW})
chk("день отдыха -> 200", r4.status_code == 200, r4.text[:200])
rest = r4.json() if r4.status_code == 200 else {}
chk("вид дня rest", rest.get("kind") == "rest", rest.get("kind"))
chk("день отдыха: ИИ вызван (другой ключ кэша)", calls.count("trainer_nutrition") == 1, calls)
chk("день отдыха: без pre/post", rest.get("pre_workout") is None and rest.get("post_workout") is None, rest)
chk("день отдыха: цифры пустые", rest.get("numbers", {}).get("eaten_kcal") == 0, rest.get("numbers"))


# --------------------------------------------------------------------------- #
#  6. Завершённая сессия сегодня: контекст меняется на «уже была»
# --------------------------------------------------------------------------- #
db = SessionLocal()
session = M.TrainerSession(
    telegram_id=TID, program_id=PROGRAM_ID, program_day_id=TODAY_DAY_ID, date=ISO(today),
    status="completed", title="Верх тела", session_type="strength", week=1, day_index=1,
    duration_min=48, total_sets=6, total_reps=60, total_volume_kg=1080.0, calories_burned=300,
)
db.add(session)
day_row = db.query(M.TrainerProgramDay).filter(M.TrainerProgramDay.id == TODAY_DAY_ID).first()
day_row.status = "done"
db.commit(); db.close()

with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.post("/food/suggest", json=dict(SUGGEST_BODY, date=ISO(today)))
note = r.json().get("training_note")
chk("после тренировки: training_note про факт", note and "уже была" in note, note)
chk("после тренировки: сожжённые ккал в промпте", "300 ккал" in captured.get("suggest_food", {}).get("user", ""),
    captured.get("suggest_food", {}).get("user", "")[:400])

line = q(lambda db: trainer_notify.decorate_training_reminder(db, TID, ISO(today), "ru", BASE_TEXT))
chk("напоминание после тренировки", "уже потренировались" in line, line)


# --------------------------------------------------------------------------- #
#  7. EN: контекст тренировки и совет дня на английском
# --------------------------------------------------------------------------- #
db = SessionLocal(); u = db.query(M.User).filter(M.User.telegram_id == TID).first()
u.language = "en"; db.commit(); db.close()

with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.post("/food/suggest", json=dict(SUGGEST_BODY, date=ISO(today)))
chk("EN food/suggest -> 200", r.status_code == 200, r.text[:200])
chk("EN контекст тренировки", "Training context" in captured.get("suggest_food", {}).get("user", ""),
    captured.get("suggest_food", {}).get("user", "")[:400])
chk("EN training_note", "Workout done today" in (r.json().get("training_note") or ""),
    r.json().get("training_note"))
chk("EN правило в system-промпте", "workout today" in captured.get("suggest_food", {}).get("system", "").lower(),
    captured.get("suggest_food", {}).get("system", "")[:200])

calls.clear()
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.get("/trainer/nutrition/today", params={"date": ISO(today)})
chk("EN nutrition -> 200", r.status_code == 200, r.text[:200])
chk("EN: отдельный кэш по языку", calls.count("trainer_nutrition") == 1, calls)
chk("EN system-промпт совета", "coach" in captured.get("trainer_nutrition", {}).get("system", "").lower(),
    captured.get("trainer_nutrition", {}).get("system", "")[:160])
chk("EN: две строки кэша на дату", q(lambda db: db.query(M.TrainerDailyTip).filter(
    M.TrainerDailyTip.telegram_id == TID, M.TrainerDailyTip.date == ISO(today)).count()) == 2)


# --------------------------------------------------------------------------- #
#  8. Сбой ИИ в совете дня -> 502, мусор в кэш не попадает
# --------------------------------------------------------------------------- #
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: ({}, {})):
    r = c.get("/trainer/nutrition/today", params={"date": ISO(today + timedelta(days=2))})
chk("пустой ответ ИИ -> 502", r.status_code == 502, r.status_code)
chk("мусор не закэширован", q(lambda db: db.query(M.TrainerDailyTip).filter(
    M.TrainerDailyTip.date == ISO(today + timedelta(days=2))).count()) == 0)

if fails:
    print("FAIL:"); [print("  -", f) for f in fails]; sys.exit(1)
print("OK: food/suggest знает про тренировку дня (промпт + training_note), совет дня")
print("    training/rest с кэшем и цифрами дня, напоминание дополняется планом, EN, 502")
