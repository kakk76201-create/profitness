"""Проверки API AI-тренера (ТЗ §4.1, §4.2, §4.3 /today, §4.4, §8.1 test_trainer_api).

Покрыто: премиум-гейт 402 на всех маршрутах, валидация анкеты (422), upsert
TrainingReminder, генерация программы (409 без анкеты, раскрытие weeks×days,
архивация прошлой, heavy-лимит 429, пустой ответ ИИ → 502), /today
(planned / rest / week_done / no_program), библиотека (фильтры, поиск, техника
с кэшем и AIError → 200, альтернативы, исключение), удаление данных аккаунта.
"""
import os, sys, tempfile, pathlib, json
from datetime import date as _date, timedelta
from unittest import mock

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "trapi.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"

from fastapi.testclient import TestClient
from backend.database import init_db, SessionLocal
from backend import models as M, ai_service, ratelimit
from backend.main import app

init_db()
# Прогоняем lifespan приложения: он обязан загрузить библиотеку упражнений
# (вставка (б) в main.py). Отдельный клиент — чтобы дальше работать без него.
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
D = lambda n: (monday + timedelta(days=n)).isoformat()   # день текущей недели по номеру


# --------------------------------------------------------------------------- #
#  Библиотека загружена в lifespan
# --------------------------------------------------------------------------- #
lib_total = q(lambda db: db.query(M.TrainerExercise).count())
chk("lifespan загрузил библиотеку", lib_total > 100, lib_total)
chk("есть warmup_general_5min",
    q(lambda db: db.query(M.TrainerExercise).filter(M.TrainerExercise.slug == "warmup_general_5min").first()) is not None)


# --------------------------------------------------------------------------- #
#  1. Free-пользователь: 402 на всех маршрутах тренера
# --------------------------------------------------------------------------- #
for name, resp in [
    ("overview", c.get("/trainer/overview")),
    ("profile GET", c.get("/trainer/profile")),
    ("profile POST", c.post("/trainer/profile", json={})),
    ("program GET", c.get("/trainer/program")),
    ("program generate", c.post("/trainer/program/generate", json={})),
    ("today", c.get("/trainer/today")),
    ("exercises", c.get("/trainer/exercises")),
    ("exercise", c.get("/trainer/exercises/1")),
    ("alternatives", c.get("/trainer/exercises/1/alternatives")),
    ("exclude", c.post("/trainer/exercises/1/exclude", json={"excluded": True})),
]:
    chk(f"free {name} -> 402", resp.status_code == 402, resp.status_code)

# Делаем пользователя премиумом и заполняем тело.
db = SessionLocal(); u = db.query(M.User).filter(M.User.telegram_id == TID).first()
u.subscription_type = "lifetime"; u.language = "ru"
u.weight = 80.0; u.height = 180.0; u.age = 30; u.gender = "male"
u.diet_goal = "muscle"; u.daily_goal_kcal = 2600
db.commit(); db.close()

chk("премиум overview -> 200", c.get("/trainer/overview").status_code == 200)
chk("нет анкеты -> profile 404", c.get("/trainer/profile").status_code == 404)
ov = c.get("/trainer/overview").json()
chk("overview без программы: no_program", ov["today"]["kind"] == "no_program", ov["today"])
chk("overview: лента недели 7 дней", len(ov["week"]) == 7, len(ov["week"]))
chk("overview: pending_review False", ov["pending_review"] is False)


# --------------------------------------------------------------------------- #
#  2. Валидация анкеты (422)
# --------------------------------------------------------------------------- #
BASE = {
    "goal": "muscle", "level": "beginner", "equipment": "gym",
    "equipment_extra": ["bench", "pullup_bar"],
    "days_per_week": 3, "preferred_weekdays": [0, 2, 4],
    "session_minutes": 45, "program_weeks": 6,
    "limitations": ["knee"], "limitations_text": "болит правое колено",
    "focus": ["back"], "reminder_enabled": True, "reminder_time": "18:00",
}
def post_profile(**over):
    body = dict(BASE); body.update(over)
    return c.post("/trainer/profile", json=body)

chk("days=7 -> 422", post_profile(days_per_week=7, preferred_weekdays=[0,1,2,3,4,5,6]).status_code == 422)
chk("weekdays != days -> 422", post_profile(preferred_weekdays=[0, 2]).status_code == 422)
chk("неизвестная цель -> 422", post_profile(goal="fly").status_code == 422)
chk("неизвестное оборудование -> 422", post_profile(equipment="spaceship").status_code == 422)
chk("неизвестное ограничение -> 422", post_profile(limitations=["tail"]).status_code == 422)
chk("минуты не из списка -> 422", post_profile(session_minutes=37).status_code == 422)
chk("недели не из списка -> 422", post_profile(program_weeks=5).status_code == 422)
chk("битое время -> 422", post_profile(reminder_time="25:99").status_code == 422)


# --------------------------------------------------------------------------- #
#  3. Генерация без анкеты -> 409
# --------------------------------------------------------------------------- #
r = c.post("/trainer/program/generate", json={})
chk("generate без анкеты -> 409", r.status_code == 409, r.text[:160])


# --------------------------------------------------------------------------- #
#  4. Сохранение анкеты + TrainingReminder
# --------------------------------------------------------------------------- #
r = post_profile()
chk("profile POST -> 200", r.status_code == 200, r.text[:300])
p = r.json() if r.status_code == 200 else {}
chk("onboarding_completed", p.get("onboarding_completed") is True, p)
chk("дни недели вернулись", p.get("preferred_weekdays") == [0, 2, 4], p.get("preferred_weekdays"))
chk("ограничения вернулись", p.get("limitations") == ["knee"], p.get("limitations"))
chk("доп. оборудование вернулось", sorted(p.get("equipment_extra", [])) == ["bench", "pullup_bar"], p.get("equipment_extra"))
chk("reminder_id проставлен", isinstance(p.get("reminder_id"), int), p.get("reminder_id"))

rem = q(lambda db: db.query(M.TrainingReminder).filter(M.TrainingReminder.telegram_id == TID).all())
chk("создано одно напоминание", len(rem) == 1, len(rem))
if rem:
    chk("weekdays = 0,2,4", rem[0].weekdays == "0,2,4", rem[0].weekdays)
    chk("время 18:00", rem[0].time == "18:00", rem[0].time)
    chk("напоминание включено", bool(rem[0].enabled) is True)

# Повторный POST обновляет ту же строку, а не создаёт новую.
r2 = post_profile(reminder_enabled=False, preferred_weekdays=[0, 2, 4])
chk("повторный profile POST -> 200", r2.status_code == 200, r2.text[:200])
rem2 = q(lambda db: db.query(M.TrainingReminder).filter(M.TrainingReminder.telegram_id == TID).all())
chk("напоминание всё ещё одно", len(rem2) == 1, len(rem2))
if rem and rem2:
    chk("та же строка напоминания", rem2[0].id == rem[0].id, (rem[0].id, rem2[0].id))
    chk("reminder_enabled=false -> enabled=False", bool(rem2[0].enabled) is False, rem2[0].enabled)
chk("профиль один", q(lambda db: db.query(M.TrainerProfile).count()) == 1)


# --------------------------------------------------------------------------- #
#  5. Генерация программы (мок ИИ)
# --------------------------------------------------------------------------- #
calls = {"program": 0, "technique": 0}
captured = {}

PROGRAM = {
    "title": "Верх/Низ — 6 недель",
    "split_type": "upper_lower",
    "summary": "Три full body дня с акцентом на спину, колени не грузим глубокими приседами.",
    "week_template": {"days": [
        {"day_index": 1, "title": "Верх тела", "session_type": "strength",
         "focus_muscles": ["chest", "back"], "duration_min": 45,
         "warmup": [{"slug": "warmup_general_5min", "time_sec": 300},
                    {"slug": "arm_circles", "sets": 1, "reps": 15}],
         "exercises": [
             {"slug": "db_bench_press", "sets": 3, "reps_min": 8, "reps_max": 12, "rest_sec": 90,
              "start_weight_kg": 16, "rpe": 7, "note": "локти 45°"},
             {"slug": "db_row", "sets": 3, "reps_min": 8, "reps_max": 12, "rest_sec": 90, "start_weight_kg": 18},
             {"slug": "plank", "sets": 3, "time_sec": 45, "rest_sec": 60},
         ],
         "cooldown": [{"slug": "chest_stretch", "time_sec": 30}]},
        {"day_index": 2, "title": "Низ тела", "session_type": "strength",
         "focus_muscles": ["quads", "glutes"], "duration_min": 45,
         "warmup": [{"slug": "warmup_general_5min", "time_sec": 300}],
         "exercises": [
             {"slug": "goblet_squat", "sets": 3, "reps_min": 10, "reps_max": 15, "rest_sec": 90, "start_weight_kg": 16},
             {"slug": "glute_bridge", "sets": 3, "reps_min": 12, "reps_max": 15, "rest_sec": 60},
         ],
         "cooldown": []},
        {"day_index": 3, "title": "Всё тело", "session_type": "strength",
         "focus_muscles": ["back", "shoulders"], "duration_min": 45,
         "warmup": [],
         "exercises": [
             {"slug": "lat_pulldown", "sets": 3, "reps_min": 8, "reps_max": 12, "rest_sec": 90, "start_weight_kg": 35},
             {"slug": "db_shoulder_press", "sets": 3, "reps_min": 8, "reps_max": 12, "rest_sec": 90, "start_weight_kg": 12},
         ],
         "cooldown": []},
        # Лишний, четвёртый день — нормализация обязана его усечь (days_per_week=3).
        {"day_index": 4, "title": "Лишний день", "session_type": "strength",
         "exercises": [{"slug": "db_curl", "sets": 3, "reps_min": 10, "reps_max": 12}]},
    ]},
    "periodization": [
        {"week": 1, "phase": "base", "weight_pct": 100, "sets_delta": 0},
        {"week": 4, "phase": "deload", "weight_pct": 85, "sets_delta": -1},
        {"week": 6, "phase": "deload", "weight_pct": 85, "sets_delta": -1},
    ],
    "tips": ["Ешь белок", "Спи 8 часов"],
}
TECHNIQUE = {
    "steps": ["Ляг на скамью", "Опусти гантели", "Выжми вверх"],
    "cues": ["Лопатки сведены", "Локти 45°"],
    "mistakes": ["Мост", "Разведённые локти"],
    "breathing": "Вдох вниз, выдох вверх",
    "safety": "Не роняй гантели",
    "muscles_text": "Грудь, трицепс, передняя дельта",
}

def fake_run(system_prompt, user_prompt, log_tag, max_tokens=None):
    captured[log_tag] = {"system": system_prompt, "user": user_prompt, "max_tokens": max_tokens}
    if log_tag == "trainer_program":
        calls["program"] += 1
        return json.loads(json.dumps(PROGRAM)), {}
    if log_tag == "trainer_technique":
        calls["technique"] += 1
        return json.loads(json.dumps(TECHNIQUE)), {}
    return {}, {}

start = D(0)   # понедельник текущей недели
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.post("/trainer/program/generate", json={"start_date": start})
chk("generate -> 200", r.status_code == 200, r.text[:400])
prog = r.json() if r.status_code == 200 else {}

chk("tag трейсинга trainer_program", "trainer_program" in captured, list(captured))
if "trainer_program" in captured:
    cap = captured["trainer_program"]
    chk("max_tokens=4000", cap["max_tokens"] == 4000, cap["max_tokens"])
    chk("каталог в user_prompt", "db_bench_press" in cap["user"], cap["user"][-300:])
    chk("ограничение в user_prompt", "колен" in cap["user"].lower(), cap["user"][:400])
    chk("тело в user_prompt", "80" in cap["user"], cap["user"][:400])
    chk("«не врач» в system", "не врач" in cap["system"].lower(), cap["system"][:200])
    chk("контриндицированные не в каталоге", "bb_back_squat" not in cap["user"], "колено -> присед со штангой исключён")

chk("статус active", prog.get("status") == "active", prog.get("status"))
chk("title из ИИ", prog.get("title") == "Верх/Низ — 6 недель", prog.get("title"))
chk("split_type", prog.get("split_type") == "upper_lower", prog.get("split_type"))
chk("weeks=6", prog.get("weeks") == 6, prog.get("weeks"))
chk("days_per_week=3", prog.get("days_per_week") == 3, prog.get("days_per_week"))
chk("дней 6x3=18", len(prog.get("days", [])) == 18, len(prog.get("days", [])))
chk("периодизация на 6 недель", len(prog.get("periodization", [])) == 6, prog.get("periodization"))
chk("подпись фазы по-русски", (prog.get("periodization") or [{}])[0].get("label") in ("База", "Рост", "Пик", "Разгрузка"),
    (prog.get("periodization") or [{}])[0])
chk("4-я неделя — deload", any(p["week"] == 4 and p["phase"] == "deload" for p in prog.get("periodization", [])),
    prog.get("periodization"))
chk("советы сохранены", prog.get("tips") == ["Ешь белок", "Спи 8 часов"], prog.get("tips"))
chk("summary сохранён", "full body" in (prog.get("summary") or ""), prog.get("summary"))

days = prog.get("days", [])
if days:
    chk("старт в понедельник", days[0]["scheduled_date"] == D(0), days[0]["scheduled_date"])
    chk("даты Пн/Ср/Пт", [d["scheduled_date"] for d in days[:3]] == [D(0), D(2), D(4)],
        [d["scheduled_date"] for d in days[:3]])
    chk("неделя 2 начинается через 7 дней", days[3]["scheduled_date"] == D(7), days[3]["scheduled_date"])
    chk("лишний 4-й день усечён", {d["day_index"] for d in days} == {1, 2, 3}, sorted({d["day_index"] for d in days}))
    first = days[0]
    chk("название дня", first["title"] == "Верх тела", first["title"])
    chk("упражнения дня подтянули имена", first["exercises"][0]["name_ru"], first["exercises"][0])
    chk("exercise_id проставлен", isinstance(first["exercises"][0]["exercise_id"], int), first["exercises"][0])
    chk("вес цели", first["exercises"][0]["target_weight_kg"] == 16, first["exercises"][0])
    chk("подсказка ИИ сохранена", first["exercises"][0]["note"] == "локти 45°", first["exercises"][0])
    chk("разминка есть", len(first["warmup"]) == 2, first["warmup"])
    day3 = [d for d in days if d["day_index"] == 3][0]
    chk("пустая разминка -> дефолт", [w["slug"] for w in day3["warmup"]] == ["warmup_general_5min"], day3["warmup"])
    chk("пустая заминка -> дефолт", [w["slug"] for w in day3["cooldown"]] == ["stretch_full_body_5min"], day3["cooldown"])
    chk("фаза недели у дня", days[0].get("phase") == "base", days[0].get("phase"))

rows = q(lambda db: db.query(M.TrainerProgramDay).count())
chk("в БД 18 дней", rows == 18, rows)
chk("program_id у дней",
    q(lambda db: db.query(M.TrainerProgramDay).filter(M.TrainerProgramDay.program_id == prog["id"]).count()) == 18)

# GET /trainer/program отдаёт ту же активную программу.
r = c.get("/trainer/program")
chk("GET /program -> 200", r.status_code == 200, r.text[:200])
chk("GET /program = активная", r.json().get("id") == prog.get("id"), (r.json().get("id"), prog.get("id")))
chk("current_week=1 на первой неделе", r.json().get("current_week") == 1, r.json().get("current_week"))
chk("GET /program?program_id чужой -> 404", c.get("/trainer/program?program_id=99999").status_code == 404)

# Вторая генерация: прошлая программа уходит в архив.
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.post("/trainer/program/generate", json={"start_date": start})
chk("вторая generate -> 200", r.status_code == 200, r.text[:200])
prog2 = r.json() if r.status_code == 200 else {}
chk("новая программа другая", prog2.get("id") != prog.get("id"), (prog.get("id"), prog2.get("id")))
old_status = q(lambda db: db.query(M.TrainerProgram).filter(M.TrainerProgram.id == prog["id"]).first().status)
chk("старая программа archived", old_status == "archived", old_status)
active_count = q(lambda db: db.query(M.TrainerProgram).filter(
    M.TrainerProgram.telegram_id == TID, M.TrainerProgram.status == "active").count())
chk("активная одна", active_count == 1, active_count)
chk("дней в БД 36 (старые сохранены)", q(lambda db: db.query(M.TrainerProgramDay).count()) == 36)

# Третий вызов за минуту — heavy-лимит (2/мин).
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.post("/trainer/program/generate", json={"start_date": start})
chk("третья generate за минуту -> 429", r.status_code == 429, r.status_code)
chk("на 429 ИИ не вызывался", calls["program"] == 2, calls["program"])

# Сбрасываем лимиты и проверяем пустой ответ ИИ.
ratelimit._minute_hits.clear(); ratelimit._day_hits.clear()
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: ({}, {})):
    r = c.post("/trainer/program/generate", json={"start_date": start})
chk("пустой ответ ИИ -> 502", r.status_code == 502, r.status_code)
chk("после 502 активная программа прежняя",
    q(lambda db: db.query(M.TrainerProgram).filter(M.TrainerProgram.status == "active").first().id) == prog2.get("id"))
ratelimit._minute_hits.clear(); ratelimit._day_hits.clear()

# Архивация вручную.
r = c.post(f"/trainer/program/{prog2['id']}/archive")
chk("archive -> 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
chk("после archive активной нет", c.get("/trainer/program").status_code == 404)
chk("archive чужой -> 404", c.post("/trainer/program/99999/archive").status_code == 404)
# Возвращаем программу в строй для дальнейших проверок.
db = SessionLocal()
db.query(M.TrainerProgram).filter(M.TrainerProgram.id == prog2["id"]).update({"status": "active"})
db.commit(); db.close()


# --------------------------------------------------------------------------- #
#  6. /today: planned / rest / week_done
# --------------------------------------------------------------------------- #
r = c.get(f"/trainer/today?date={D(0)}")
chk("today понедельник -> 200", r.status_code == 200, r.text[:200])
t = r.json()
chk("понедельник — тренировочный день", t["is_training_day"] is True and t["kind"] == "planned", t["kind"])
chk("today: план дня отдан", (t.get("day") or {}).get("title") == "Верх тела", (t.get("day") or {}).get("title"))
chk("today: активной сессии нет", t.get("active_session") is None)

t = c.get(f"/trainer/today?date={D(1)}").json()
chk("вторник — отдых", t["kind"] == "rest" and t["is_training_day"] is False, t["kind"])
chk("отдых: следующая среда", t.get("next_date") == D(2), t.get("next_date"))
chk("отдых: показан следующий день", (t.get("day") or {}).get("title") == "Низ тела", (t.get("day") or {}).get("title"))

# Закрываем все дни этой недели — «неделя закрыта».
db = SessionLocal()
db.query(M.TrainerProgramDay).filter(
    M.TrainerProgramDay.program_id == prog2["id"],
    M.TrainerProgramDay.scheduled_date <= D(6),
).update({"status": "done"}, synchronize_session=False)
db.commit(); db.close()
# Понедельник теперь закрыт → kind=done, показана следующая тренировка (следующая неделя).
t = c.get(f"/trainer/today?date={D(0)}").json()
chk("сделанный день -> done", t["kind"] == "done" and t["is_training_day"] is True, t["kind"])
chk("done: отдан сам день", (t.get("day") or {}).get("title") == "Верх тела", (t.get("day") or {}).get("title"))
chk("done: следующая дата", t.get("next_date") == D(7), t.get("next_date"))
chk("done: название следующей", t.get("next_title") == "Верх тела", t.get("next_title"))

t = c.get(f"/trainer/today?date={D(5)}").json()
chk("неделя закрыта -> week_done", t["kind"] == "week_done", t["kind"])
chk("week_done: следующая — на той неделе", t.get("next_date") == D(7), t.get("next_date"))

ov = c.get(f"/trainer/overview?date={D(5)}").json()
chk("overview: программа отдана", (ov.get("program") or {}).get("id") == prog2["id"], ov.get("program"))
chk("overview: цель недели 3", ov["streak"]["this_week_goal"] == 3, ov["streak"])
chk("overview: дни недели помечены done",
    sum(1 for d in ov["week"] if d["status"] == "done") == 3, [d["status"] for d in ov["week"]])
chk("overview: фаза недели", (ov.get("program") or {}).get("phase") == "base", (ov.get("program") or {}).get("phase"))

# Возвращаем дни в план.
db = SessionLocal()
db.query(M.TrainerProgramDay).filter(M.TrainerProgramDay.program_id == prog2["id"]).update(
    {"status": "planned"}, synchronize_session=False)
db.commit(); db.close()


# --------------------------------------------------------------------------- #
#  6b. Сериализация активной сессии внутри /today и /overview
# --------------------------------------------------------------------------- #
bench0 = q(lambda db: db.query(M.TrainerExercise).filter(M.TrainerExercise.slug == "db_bench_press").first())
db = SessionLocal()
day1 = db.query(M.TrainerProgramDay).filter(
    M.TrainerProgramDay.program_id == prog2["id"], M.TrainerProgramDay.week == 1,
    M.TrainerProgramDay.day_index == 1).first()
# Прошлая (завершённая) сессия — источник «прошлого раза».
old = M.TrainerSession(telegram_id=TID, date=D(-7), status="completed", title="День 1",
                       session_type="strength", week=1, day_index=1)
db.add(old); db.flush()
old_sx = M.TrainerSessionExercise(telegram_id=TID, session_id=old.id, exercise_id=bench0.id,
                                  block="main", order_index=1, planned_sets=3)
db.add(old_sx); db.flush()
db.add(M.TrainerSetLog(telegram_id=TID, session_id=old.id, session_exercise_id=old_sx.id,
                       exercise_id=bench0.id, date=D(-7), set_index=1, set_type="work",
                       weight_kg=14, reps=12, is_done=True, volume_kg=168))
db.add(M.TrainerSetLog(telegram_id=TID, session_id=old.id, session_exercise_id=old_sx.id,
                       exercise_id=bench0.id, date=D(-7), set_index=2, set_type="warmup",
                       weight_kg=8, reps=10, is_done=True))
# Текущая (незавершённая) сессия.
cur = M.TrainerSession(telegram_id=TID, date=D(0), status="in_progress", title="Верх тела",
                       session_type="strength", week=1, day_index=1, program_day_id=day1.id,
                       prs_json=json.dumps([{"exercise_id": bench0.id, "type": "max_weight",
                                             "value": 16.0, "prev": 14.0}]),
                       adaptation_json=json.dumps({"changes": [{"exercise_id": bench0.id, "kind": "weight",
                                                                "old_value": 14, "new_value": 16}],
                                                   "lines": ["Жим гантелей: 14 → 16 кг"], "message": "Учёл"}))
db.add(cur); db.flush()
cur_sx = M.TrainerSessionExercise(telegram_id=TID, session_id=cur.id, exercise_id=bench0.id,
                                  block="main", order_index=1, planned_sets=3, planned_reps_min=8,
                                  planned_reps_max=12, planned_weight_kg=16.0, planned_rest_sec=90)
db.add(cur_sx); db.flush()
db.add(M.TrainerSetLog(telegram_id=TID, session_id=cur.id, session_exercise_id=cur_sx.id,
                       exercise_id=bench0.id, date=D(0), set_index=1, set_type="work",
                       weight_kg=16, reps=10, is_done=True, volume_kg=160, est_1rm=21.3,
                       is_pr=True, pr_types_json=json.dumps(["max_weight"])))
db.commit()
cur_id, cur_sx_id = cur.id, cur_sx.id
db.close()

t = c.get(f"/trainer/today?date={D(0)}").json()
sess = t.get("active_session")
chk("today: активная сессия отдана", sess is not None and sess["id"] == cur_id, sess)
if sess:
    chk("сессия: статус", sess["status"] == "in_progress", sess["status"])
    chk("сессия: упражнение одно", len(sess["exercises"]) == 1, len(sess["exercises"]))
    sx = sess["exercises"][0]
    chk("сессия: имя упражнения", (sx["exercise"] or {}).get("slug") == "db_bench_press", sx["exercise"])
    chk("сессия: цели", sx["planned_weight_kg"] == 16.0 and sx["planned_sets"] == 3, sx)
    chk("сессия: сет записан", len(sx["sets"]) == 1 and sx["sets"][0]["reps"] == 10, sx["sets"])
    chk("сессия: PR-типы сета", sx["sets"][0]["pr_types"] == ["max_weight"], sx["sets"][0])
    chk("сессия: «прошлый раз» из прошлой сессии",
        [p["weight_kg"] for p in sx["previous"]] == [14.0], sx["previous"])
    chk("сессия: разминочный сет не в «прошлый раз»", len(sx["previous"]) == 1, sx["previous"])
    chk("сессия: рекорды", sess["prs"] and sess["prs"][0]["prev_value"] == 14.0, sess["prs"])
    chk("сессия: имя упражнения в рекорде", sess["prs"][0]["exercise_name_ru"], sess["prs"][0])
    chk("сессия: адаптация", (sess.get("adaptation") or {}).get("lines"), sess.get("adaptation"))

ov = c.get(f"/trainer/overview?date={D(0)}").json()
chk("overview: active_session_id", ov["active_session_id"] == cur_id, ov["active_session_id"])
chk("overview: стрик по неделям", ov["streak"]["weeks"] >= 0, ov["streak"])

db = SessionLocal()
db.query(M.TrainerSetLog).delete(); db.query(M.TrainerSessionExercise).delete()
db.query(M.TrainerSession).delete(); db.commit(); db.close()


# --------------------------------------------------------------------------- #
#  7. Библиотека упражнений
# --------------------------------------------------------------------------- #
r = c.get("/trainer/exercises?limit=300")
chk("exercises -> 200", r.status_code == 200, r.status_code)
items = r.json()["items"]
chk("библиотека не пуста", len(items) > 100, len(items))
chk("в списке есть difficulty/category", "difficulty" in items[0] and "category" in items[0], items[0])

r = c.get("/trainer/exercises?muscle=chest")
chest = r.json()["items"]
chk("фильтр по мышце", chest and all(i["muscle_group"] == "chest" for i in chest), len(chest))
r = c.get("/trainer/exercises?equipment=barbell&muscle=chest")
chk("фильтр по оборудованию", all(i["equipment"] == "barbell" for i in r.json()["items"]), r.json()["items"][:2])
r = c.get("/trainer/exercises?q=планк")
chk("поиск по имени", any("план" in (i["name_ru"] or "").lower() for i in r.json()["items"]), r.json()["items"][:3])

bench = q(lambda db: db.query(M.TrainerExercise).filter(M.TrainerExercise.slug == "db_bench_press").first())
bench_id = bench.id

# Техника: первый вызов идёт в ИИ, второй — из кэша.
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.get(f"/trainer/exercises/{bench_id}?technique=1")
chk("техника -> 200", r.status_code == 200, r.text[:200])
ex = r.json()
chk("ИИ вызван один раз", calls["technique"] == 1, calls["technique"])
chk("шаги техники", ex["technique"]["steps"][0] == "Ляг на скамью", ex.get("technique"))
chk("technique_status ready", ex["technique_status"] == "ready", ex["technique_status"])
chk("вторичные мышцы", "triceps" in ex["secondary_muscles"], ex["secondary_muscles"])
chk("дисклеймер при ограничениях", "не врач" in (ex.get("disclaimer") or "").lower(), ex.get("disclaimer"))
chk("max_tokens техники 700", captured["trainer_technique"]["max_tokens"] == 700, captured["trainer_technique"]["max_tokens"])

with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.get(f"/trainer/exercises/{bench_id}?technique=1")
chk("повтор — из кэша, без ИИ", calls["technique"] == 1, calls["technique"])
chk("кэш отдал ту же технику", r.json()["technique"]["steps"][0] == "Ляг на скамью", r.json().get("technique"))
cached_raw = q(lambda db: db.query(M.TrainerExercise).filter(M.TrainerExercise.id == bench_id).first().technique_json)
chk("кэш сохранён по языкам", "ru" in json.loads(cached_raw), cached_raw[:80])

# Без technique=1 ИИ не дёргаем, но кэш отдаём.
other = q(lambda db: db.query(M.TrainerExercise).filter(M.TrainerExercise.slug == "db_row").first())
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    r = c.get(f"/trainer/exercises/{other.id}")
chk("без technique=1 ИИ не вызывается", calls["technique"] == 1, calls["technique"])
chk("без техники -> technique None", r.json()["technique"] is None, r.json()["technique_status"])

# Сбой ИИ не ломает экран: 200 + technique=None + failed.
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: ({}, {})):
    r = c.get(f"/trainer/exercises/{other.id}?technique=1")
chk("AIError -> 200", r.status_code == 200, r.status_code)
chk("AIError -> technique None", r.json()["technique"] is None, r.json()["technique"])
chk("AIError -> status failed", r.json()["technique_status"] == "failed", r.json()["technique_status"])
chk("несуществующее упражнение -> 404", c.get("/trainer/exercises/999999").status_code == 404)

# Альтернативы.
r = c.get(f"/trainer/exercises/{bench_id}/alternatives?reason=busy")
chk("альтернативы -> 200", r.status_code == 200, r.text[:200])
alts = r.json()["items"]
chk("альтернатив не больше 5", 0 < len(alts) <= 5, len(alts))
chk("та же группа мышц", all(i["muscle_group"] == "chest" for i in alts), [i["slug"] for i in alts])
chk("сам себя не предлагает", all(i["id"] != bench_id for i in alts), [i["id"] for i in alts])
chk("причина вернулась", r.json()["reason"] == "busy", r.json()["reason"])
r = c.get(f"/trainer/exercises/{bench_id}/alternatives?reason=no_equipment")
chk("no_equipment: без гантелей", all(i["equipment"] != "dumbbell" for i in r.json()["items"]),
    [i["equipment"] for i in r.json()["items"]])
chk("мусорная причина -> other", c.get(f"/trainer/exercises/{bench_id}/alternatives?reason=xxx").json()["reason"] == "other")

# Исключение упражнения.
first_alt_id = alts[0]["id"]
r = c.post(f"/trainer/exercises/{first_alt_id}/exclude", json={"excluded": True})
chk("exclude -> 200", r.status_code == 200 and r.json()["excluded"] is True, r.text[:200])
chk("карточка помечена excluded", c.get(f"/trainer/exercises/{first_alt_id}").json()["excluded"] is True)
r = c.get(f"/trainer/exercises/{bench_id}/alternatives?reason=busy")
chk("исключённое не в альтернативах", all(i["id"] != first_alt_id for i in r.json()["items"]),
    [i["id"] for i in r.json()["items"]])
r = c.post(f"/trainer/exercises/{first_alt_id}/exclude", json={"excluded": False})
chk("вернули упражнение", r.json()["excluded"] is False, r.text[:120])
chk("состояние не задвоилось",
    q(lambda db: db.query(M.TrainerExerciseState).filter(M.TrainerExerciseState.exercise_id == first_alt_id).count()) == 1)


# --------------------------------------------------------------------------- #
#  8. Английский язык: подписи фаз и промпт
# --------------------------------------------------------------------------- #
db = SessionLocal(); u = db.query(M.User).filter(M.User.telegram_id == TID).first(); u.language = "en"; db.commit(); db.close()
r = c.get("/trainer/program")
chk("EN подпись фазы", (r.json().get("periodization") or [{}])[0].get("label") in ("Base", "Build", "Peak", "Deload"),
    (r.json().get("periodization") or [{}])[0])
with mock.patch.object(ai_service, "_run_text_completion", fake_run):
    c.post("/trainer/program/generate", json={"start_date": start})
chk("EN system-промпт программы", "not a doctor" in captured["trainer_program"]["system"].lower(),
    captured["trainer_program"]["system"][:160])
db = SessionLocal(); u = db.query(M.User).filter(M.User.telegram_id == TID).first(); u.language = "ru"; db.commit(); db.close()
ratelimit._minute_hits.clear(); ratelimit._day_hits.clear()


# --------------------------------------------------------------------------- #
#  9. Право на забвение: DELETE /account/data чистит данные тренера
# --------------------------------------------------------------------------- #
TRAINER_MODELS = (
    M.TrainerProfile, M.TrainerProgram, M.TrainerProgramDay, M.TrainerSession,
    M.TrainerSessionExercise, M.TrainerSetLog, M.TrainerRecord,
    M.TrainerExerciseState, M.TrainerWeeklyReview, M.TrainerDailyTip,
)
db = SessionLocal()
db.add(M.TrainerSession(telegram_id=TID, date=D(0), status="completed", title="День 1", session_type="strength"))
db.add(M.TrainerRecord(telegram_id=TID, exercise_id=bench_id, record_type="max_weight", value=60.0, date=D(0)))
db.add(M.TrainerWeeklyReview(telegram_id=TID, week_start=D(0), week_end=D(6), stats_json="{}", review_json="{}"))
db.add(M.TrainerDailyTip(telegram_id=TID, date=D(0), kind="training", lang="ru", tip_json="{}"))
db.add(M.TrainerSetLog(telegram_id=TID, session_id=1, session_exercise_id=1, exercise_id=bench_id,
                       date=D(0), set_index=1, set_type="work", weight_kg=40, reps=10, is_done=True))
db.add(M.TrainerSessionExercise(telegram_id=TID, session_id=1, exercise_id=bench_id, block="main", order_index=1))
db.add(M.TrainerExerciseState(telegram_id=TID, exercise_id=bench_id, working_weight_kg=40))
db.commit(); db.close()

before = {m.__name__: q(lambda db, m=m: db.query(m).filter(m.telegram_id == TID).count()) for m in TRAINER_MODELS}
chk("данные тренера до удаления есть", all(v > 0 for v in before.values()), before)
lib_before = q(lambda db: db.query(M.TrainerExercise).count())

r = c.delete("/account/data")
chk("DELETE /account/data -> 200", r.status_code == 200, r.text[:200])
after = {m.__name__: q(lambda db, m=m: db.query(m).filter(m.telegram_id == TID).count()) for m in TRAINER_MODELS}
chk("все строки тренера удалены", all(v == 0 for v in after.values()), after)
chk("библиотека упражнений не тронута", q(lambda db: db.query(M.TrainerExercise).count()) == lib_before,
    (lib_before, q(lambda db: db.query(M.TrainerExercise).count())))

if fails:
    print("FAIL:"); [print("  -", f) for f in fails]; sys.exit(1)
print("OK: премиум-гейт 402, валидация анкеты 422, upsert TrainingReminder,")
print("    generate (409/200/архивация/429/502), раскрытие 6x3=18 по Пн-Ср-Пт,")
print("    today planned/rest/week_done, сериализация активной сессии (сеты, PR,")
print("    «прошлый раз»), библиотека (фильтры, техника с кэшем, AIError -> 200,")
print("    альтернативы, exclude), EN-подписи и промпт, DELETE /account/data")
