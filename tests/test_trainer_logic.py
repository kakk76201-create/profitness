"""Проверка backend/trainer_logic.py (ТЗ §5.5, §8.1): Epley, шаг веса, оценка
упражнения, таблица next_targets, PR, стрик, раскрытие программы, каталог для
промпта, альтернативы, объяснения RU/EN; плюс функции с БД на временном sqlite:
collect_week_stats, today_training_context, alternatives_for_db,
apply_review_changes."""
import os, sys, tempfile, pathlib, json
from unittest import mock
ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "logic.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"

from backend import trainer_logic as L
from backend.trainer_exercises_seed import EXERCISES

fails = []
def chk(n, cond, x=""):
    if not cond: fails.append(n + ("  " + str(x) if x else ""))

TODAY = "2026-09-08"  # вторник

# --- Epley и шаг веса ---
chk("Epley 60×8 → 76", L.est_1rm(60, 8) == 76.0, L.est_1rm(60, 8))
chk("Epley 13 повт. → None", L.est_1rm(60, 13) is None)
chk("Epley 0 кг → None", L.est_1rm(0, 5) is None)
chk("Epley мусор → None", L.est_1rm("x", 5) is None)
chk("round_to_step 41.3/2.5 → 42.5", L.round_to_step(41.3, 2.5) == 42.5, L.round_to_step(41.3, 2.5))
chk("round_to_step 41/2 → 42", L.round_to_step(41, 2) == 42.0, L.round_to_step(41, 2))
chk("step barbell низ 5", L.weight_step("barbell", "quads") == 5.0)
chk("step barbell верх 2.5", L.weight_step("barbell", "chest") == 2.5)
chk("step dumbbell 2", L.weight_step("dumbbell", "quads") == 2.0)
chk("step machine низ 5", L.weight_step("machine", "hamstrings") == 5.0)
chk("step kettlebell 4", L.weight_step("kettlebell", "glutes") == 4.0)
chk("step прочее 2.5", L.weight_step("band", "back") == 2.5)

# --- evaluate_exercise ---
PL = {"sets": 3, "reps_min": 8, "reps_max": 12, "measure_type": "reps_weight"}
def S(reps, t="work", done=True, w=40): return {"set_type": t, "is_done": done, "reps": reps, "weight_kg": w}
chk("eval success", L.evaluate_exercise(PL, [S(12), S(12), S(12)]) == "success")
chk("eval partial", L.evaluate_exercise(PL, [S(10), S(10), S(10)]) == "partial")
chk("eval fail", L.evaluate_exercise(PL, [S(6), S(5), S(4)]) == "fail")
chk("eval 2 из 3 сетов → partial", L.evaluate_exercise(PL, [S(12), S(12)]) == "partial")
chk("eval только warmup → fail", L.evaluate_exercise(PL, [S(12, "warmup")]) == "fail")
chk("eval не отмеченные → fail", L.evaluate_exercise(PL, [S(12, done=False)]) == "fail")
PT = {"sets": 2, "time_sec": 30, "measure_type": "time"}
chk("eval time success", L.evaluate_exercise(PT, [{"time_sec": 30}, {"time_sec": 35}]) == "success")
chk("eval time partial", L.evaluate_exercise(PT, [{"time_sec": 25}, {"time_sec": 25}]) == "partial")
chk("eval time fail", L.evaluate_exercise(PT, [{"time_sec": 10}]) == "fail")

# --- next_targets: таблица кейсов ---
EX = {"id": 5, "slug": "db_bench_press", "name_ru": "Жим гантелей лёжа", "name_en": "Dumbbell Bench Press",
      "equipment": "dumbbell", "muscle_group": "chest", "measure_type": "reps_weight", "reps_min": 8, "reps_max": 12}
def ST(**kw):
    base = {"working_weight_kg": 40, "target_reps_min": 8, "target_reps_max": 12, "success_streak": 0, "fail_streak": 0}
    base.update(kw); return base

r = L.next_targets(ST(), EX, "success", "ok")
chk("success+ok → +шаг", r["working_weight_kg"] == 42.0 and r["changes"][0]["kind"] == "weight", r)
chk("success → streak", r["success_streak"] == 1 and r["fail_streak"] == 0 and r["target_reps_min"] == 8, r)
r = L.next_targets(ST(), EX, "success", "hard")
chk("success+hard → без изменений", r["working_weight_kg"] == 40 and r["changes"][0]["kind"] == "keep", r)
r = L.next_targets(ST(), EX, "partial", "ok")
chk("partial → +1 повтор", r["working_weight_kg"] == 40 and r["target_reps_min"] == 9 and r["changes"][0]["kind"] == "reps", r)
r = L.next_targets(ST(), EX, "fail", "ok")
chk("fail №1 → вес прежний, fail_streak 1", r["working_weight_kg"] == 40 and r["fail_streak"] == 1 and r["changes"][0]["kind"] == "keep", r)
r = L.next_targets(ST(fail_streak=1), EX, "fail", "ok")
chk("fail×2 → ×0.9", r["working_weight_kg"] == 36.0 and r["fail_streak"] == 0 and r["changes"][0]["kind"] == "deload", r)
r = L.next_targets(ST(), EX, "partial", "easy")
chk("easy+partial → +шаг", r["working_weight_kg"] == 42.0 and r["changes"][0]["kind"] == "weight", r)
r = L.next_targets(ST(), EX, "fail", "hard")
chk("hard+fail → сразу ×0.9", r["working_weight_kg"] == 36.0 and r["changes"][0]["reason"] == "deload_hard", r)
r = L.next_targets(None, dict(EX, start_weight_kg=20), "success", "ok")
chk("без state: вес из плана +шаг", r["working_weight_kg"] == 22.0, r)
BW = {"id": 9, "slug": "pushup", "name_ru": "Отжимания", "name_en": "Push-Up", "equipment": "bodyweight",
      "muscle_group": "chest", "measure_type": "reps", "reps_min": 10, "reps_max": 15}
r = L.next_targets({"target_reps_min": 10, "target_reps_max": 15}, BW, "success", "ok")
chk("bodyweight success → +2 повтора", r["target_reps_min"] == 12 and r["working_weight_kg"] is None, r)
r = L.next_targets({"target_reps_min": 25, "target_reps_max": 25}, BW, "success", "ok")
chk("bodyweight cap 25 → «пора усложнять»", r["target_reps_min"] == 25 and r["changes"][0]["reason"] == "bw_reps_cap", r)
r = L.next_targets({"target_reps_min": 10}, BW, "partial", "ok")
chk("bodyweight partial → +1", r["target_reps_min"] == 11, r)
SQ = {"id": 7, "slug": "bb_back_squat", "name_ru": "Присед", "name_en": "Squat", "equipment": "barbell",
      "muscle_group": "quads", "measure_type": "reps_weight", "reps_min": 5, "reps_max": 8}
r = L.next_targets({"working_weight_kg": 60}, SQ, "success", "easy", level="beginner")
chk("easy+низ+beginner → +2 шага (60 → 70)", r["working_weight_kg"] == 70.0 and r["changes"][0]["reason"] == "weight_up_double", r)
r = L.next_targets({"working_weight_kg": 60}, SQ, "success", "easy", level="advanced")
chk("easy+низ+advanced → +1 шаг (65)", r["working_weight_kg"] == 65.0, r)
TM = {"id": 11, "slug": "plank", "name_ru": "Планка", "name_en": "Plank", "equipment": "bodyweight",
      "muscle_group": "core", "measure_type": "time", "time_sec": 30}
r = L.next_targets({"target_time_sec": 30}, TM, "success", "ok")
chk("time success → +10 с", r["target_time_sec"] == 40, r)
r = L.next_targets({"target_time_sec": 60, "fail_streak": 1}, TM, "fail", "ok")
chk("time fail×2 → ×0.9", r["target_time_sec"] == 55 and r["changes"][0]["kind"] == "deload", r)
r = L.next_targets(ST(), EX, "мусор", "мусор")
chk("мусорные result/feedback не роняют", r["working_weight_kg"] == 40 and r["last_result"] == "partial", r)

# --- explain_changes RU/EN ---
ch = L.next_targets(ST(), EX, "success", "ok")["changes"]
ru = L.explain_changes(ch, "ru"); en = L.explain_changes(ch, "en")
chk("explain RU имя и кг", ru and "Жим гантелей лёжа" in ru[0] and "40 → 42 кг" in ru[0], ru)
chk("explain EN имя и kg", en and "Dumbbell Bench Press" in en[0] and "40 → 42 kg" in en[0], en)
ch = L.next_targets(ST(fail_streak=1), EX, "fail", "ok")["changes"]
chk("explain −10%", "−10%" in L.explain_changes(ch, "ru")[0] and "after two misses" in L.explain_changes(ch, "en")[0])
chk("explain неизвестный reason не падает", L.explain_changes([{"reason": "nope", "name_ru": "X"}], "ru") == ["X: без изменений"])
chk("explain deload_next_week", "разгрузочная" in L.explain_changes([{"reason": "deload_next_week"}], "ru")[0])

# --- detect_prs ---
RW = {"measure_type": "reps_weight"}
first = L.detect_prs({}, {"set_type": "work", "is_done": True, "weight_kg": 60, "reps": 8}, RW)
chk("первая запись — не PR", first and all(not c["is_pr"] for c in first), first)
chk("первая запись — все типы", {c["type"] for c in first} == {"max_weight", "est_1rm", "set_volume", "max_reps"}, first)
recs = {"max_weight": 60, "est_1rm": {"value": 76}, "set_volume": 480, "max_reps": 8}
better = L.detect_prs(recs, {"set_type": "work", "is_done": True, "weight_kg": 62.5, "reps": 8}, RW)
prs = {c["type"] for c in better if c["is_pr"]}
chk("улучшение → PR нужных типов", prs == {"max_weight", "est_1rm", "set_volume"}, better)
chk("max_reps без улучшения не PR", "max_reps" not in {c["type"] for c in better}, better)
chk("prev_value заполнен", all(c["prev_value"] is not None for c in better), better)
chk("warmup игнорируется", L.detect_prs(recs, {"set_type": "warmup", "is_done": True, "weight_kg": 100, "reps": 8}, RW) == [])
chk("не отмеченный игнорируется", L.detect_prs(recs, {"set_type": "work", "is_done": False, "weight_kg": 100, "reps": 8}, RW) == [])
chk("reps: max_reps", [c["type"] for c in L.detect_prs({"max_reps": 10}, {"reps": 12, "is_done": True}, {"measure_type": "reps"})] == ["max_reps"])
chk("time: max_time", [c["type"] for c in L.detect_prs({}, {"time_sec": 40, "is_done": True}, {"measure_type": "time"})] == ["max_time"])

# --- weekly_streak / недели ---
chk("стрик 2 недели", L.weekly_streak(["2026-08-24", "2026-09-01"], TODAY) == 2)
chk("текущая неделя с сессией засчитывается", L.weekly_streak(["2026-08-24", "2026-09-01", "2026-09-08"], TODAY) == 3)
chk("пустая неделя обнуляет", L.weekly_streak(["2026-08-17", "2026-09-01"], TODAY) == 1)
chk("пустая текущая неделя не обнуляет", L.weekly_streak(["2026-09-01"], TODAY) == 1)
chk("нет сессий → 0", L.weekly_streak([], TODAY) == 0)
chk("week_bounds", L.week_bounds(TODAY) == ("2026-09-07", "2026-09-13"))
chk("review week: Вт без сессий → прошлая", L.pick_review_week_start([], TODAY) == "2026-08-31")
chk("review week: Вт с сессией → текущая", L.pick_review_week_start(["2026-09-07"], TODAY) == "2026-09-07")
chk("review week: Чт → текущая", L.pick_review_week_start([], "2026-09-10") == "2026-09-07")

# --- копии хелперов main, мелочи ---
chk("_weekdays_to_csv", L._weekdays_to_csv([4, 0, 2, 2, 9]) == "0,2,4")
chk("_csv_to_weekdays", L._csv_to_weekdays("0,2,x,9,") == [0, 2])
chk("_normalize_time 9:5", L._normalize_time("9:5") == "09:05")
try:
    L._normalize_time("25:00"); chk("_normalize_time 25:00 → ValueError", False)
except ValueError:
    pass
chk("default_weekdays", L.default_weekdays(3) == [0, 2, 4] and L.default_weekdays(2) == [0, 3])
chk("map_workout_type", L.map_workout_type("mobility") == "yoga" and L.map_workout_type("mixed") == "strength" and L.map_workout_type("cardio") == "cardio")
chk("duration clamp верх", L.session_duration_min(200, 45) == 68, L.session_duration_min(200, 45))
chk("duration clamp низ", L.session_duration_min(5, 45) == 10)
chk("duration requested", L.session_duration_min(0, 45, requested=50) == 50)
chk("median_weight", L.median_weight([S(10, w=40), S(10, w=42), S(10, w=44), S(10, "warmup", w=100)]) == 42.0)

# --- session_totals / muscle_volume_summary ---
tot = L.session_totals([S(10, w=40), S(10, "warmup", w=20), S(8, w=40), S(12, done=False)],
                       [{"block": "main", "status": "done"}, {"block": "main", "status": "skipped"}, {"block": "warmup", "status": "done"}])
chk("totals", tot == {"total_sets": 2, "total_reps": 18, "total_volume_kg": 720.0, "exercises_done": 1, "exercises_skipped": 1}, tot)
mv = L.muscle_volume_summary([dict(S(10, w=40), exercise_id=1), dict(S(10, w=40), exercise_id=1), dict(S(10, "warmup", w=40), exercise_id=1)], {1: "chest"})
chest = [m for m in mv if m["muscle_group"] == "chest"][0]
chk("muscle chest 2 сета (warmup мимо)", chest["sets"] == 2 and chest["volume_kg"] == 800.0 and chest["target_max"] == 20, chest)
chk("muscle все группы", len(mv) == len(L.STRENGTH_GROUPS))

# --- модификаторы: week_modifier, feedback_adjustments, apply_targets ---
PERIOD = [{"week": w, "phase": "deload" if w in (4, 6) else "base", "weight_pct": 85 if w in (4, 6) else 100,
           "sets_delta": -1 if w in (4, 6) else 0} for w in range(1, 7)]
chk("week_modifier deload", L.week_modifier(PERIOD, 4, 6)["weight_pct"] == 85 and L.week_modifier(PERIOD, 4, 6)["label_ru"] == "Разгрузка")
chk("week_modifier base", L.week_modifier(PERIOD, 2, 6)["phase"] == "base")
chk("week_modifier последняя без записи → deload", L.week_modifier([], 6, 6)["phase"] == "deload")
chk("week_modifier из JSON-строки", L.week_modifier(json.dumps(PERIOD), 6)["phase"] == "deload")
fa = L.feedback_adjustments("hard", [{"exercise_id": 1, "category": "isolation", "result": "success"},
                                     {"exercise_id": 2, "category": "compound", "result": "partial"},
                                     {"exercise_id": 3, "category": "compound", "result": "success"}])
chk("hard: изоляция −1 сет, компаунд −5% если не success", fa["items"] == [{"exercise_id": 1, "slug": None, "sets_delta": -1}, {"exercise_id": 2, "slug": None, "weight_pct": -5}], fa)
fe = L.feedback_adjustments("easy", [{"exercise_id": 2, "category": "compound"}, {"exercise_id": 1, "category": "isolation"}])
chk("easy: компаунд +1 сет", fe["items"] == [{"exercise_id": 2, "slug": None, "sets_delta": 1}], fe)
chk("ok → None", L.feedback_adjustments("ok", [{"exercise_id": 2, "category": "compound"}]) is None)
ITEM = {"slug": "db_bench_press", "exercise_id": 5, "sets": 3, "reps_min": 8, "reps_max": 12, "rest_sec": 90, "start_weight_kg": 20}
t = L.apply_targets(ITEM, EX, {"working_weight_kg": 24}, L.week_modifier(PERIOD, 4, 6))
chk("apply_targets deload: 24×0.85 → 20, сеты 2", t["planned_weight_kg"] == 20.0 and t["planned_sets"] == 2, t)
t = L.apply_targets(ITEM, EX, None, L.week_modifier(PERIOD, 1, 6), {"items": [{"exercise_id": 5, "sets_delta": 1}]})
chk("apply_targets правка дня +1 сет", t["planned_sets"] == 4 and t["planned_weight_kg"] == 20.0, t)
t = L.apply_targets(dict(ITEM, sets=5), EX, None, None, json.dumps({"items": [{"slug": "db_bench_press", "sets_delta": 1}]}))
chk("apply_targets +1 сет cap 5", t["planned_sets"] == 5, t)
t = L.apply_targets(ITEM, EX, None, None, {"deload": True})
chk("apply_targets deload-флаг дня", t["planned_weight_kg"] == 18.0 and t["planned_sets"] == 2, t)
merged = L.merge_adjustments('{"items":[{"exercise_id":1,"sets_delta":1}]}', {"deload": True, "items": [{"exercise_id": 2, "weight_pct": -5}]})
chk("merge_adjustments", merged["deload"] is True and len(merged["items"]) == 2, merged)

# --- expand_program ---
TEMPLATE = {"days": [
    {"day_index": 1, "title": "Верх", "session_type": "strength", "focus_muscles": ["chest", "back"], "duration_min": 45,
     "warmup": [], "exercises": [dict(ITEM), {"slug": "lat_pulldown", "exercise_id": 6, "sets": 3, "reps_min": 8, "reps_max": 12, "rest_sec": 90, "start_weight_kg": 40}],
     "cooldown": [{"slug": "chest_stretch", "exercise_id": None, "sets": 1, "reps": None, "time_sec": 30, "note": None}]},
    {"day_index": 2, "title": "Низ", "session_type": "strength", "focus_muscles": ["quads"], "duration_min": 45,
     "warmup": [{"slug": "leg_swings", "sets": 1, "reps": 15, "time_sec": None}], "exercises": [{"slug": "goblet_squat", "exercise_id": 7, "sets": 3, "reps_min": 8, "reps_max": 12, "rest_sec": 90, "start_weight_kg": 16}], "cooldown": []},
    {"day_index": 3, "title": "Full", "session_type": "mixed", "focus_muscles": ["full_body"], "duration_min": 40,
     "warmup": [], "exercises": [{"slug": "pushup", "exercise_id": 9, "sets": 3, "reps_min": 10, "reps_max": 15, "rest_sec": 60, "start_weight_kg": None}], "cooldown": []},
]}
PROFILE = {"days_per_week": 3, "preferred_weekdays": "0,2,4", "program_weeks": 6, "session_minutes": 45}
days = L.expand_program(TEMPLATE, PERIOD, PROFILE, TODAY)
chk("expand: 6×3 = 18 строк", len(days) == 18, len(days))
chk("expand: даты Ср/Пт/Пн с ближайшего", [d["scheduled_date"] for d in days[:3]] == ["2026-09-09", "2026-09-11", "2026-09-14"], [d["scheduled_date"] for d in days[:3]])
chk("expand: все дни по Пн/Ср/Пт", all(d["weekday"] in (0, 2, 4) for d in days))
chk("expand: week/day_index", [(d["week"], d["day_index"]) for d in days[:4]] == [(1, 1), (1, 2), (1, 3), (2, 1)])
chk("expand: day_index на том же дне недели", days[0]["weekday"] == days[3]["weekday"] == 2)
chk("expand: deload-неделя помечена", all(d["phase"] == "deload" for d in days if d["week"] in (4, 6)) and days[0]["phase"] == "base")
chk("expand: deload weight_pct", days[9]["weight_pct"] == 85 and days[9]["sets_delta"] == -1, days[9])
chk("expand: title/тип", days[1]["title"] == "Низ" and days[2]["session_type"] == "mixed")
ex0 = json.loads(days[0]["exercises_json"])
chk("expand: exercises_json с order", [e["order"] for e in ex0] == [1, 2] and ex0[0]["start_weight_kg"] == 20, ex0)
chk("expand: инжект warmup по умолчанию", json.loads(days[0]["warmup_json"])[0]["slug"] == "warmup_general_5min")
chk("expand: инжект cooldown по умолчанию", json.loads(days[1]["cooldown_json"])[0]["slug"] == "stretch_full_body_5min")
chk("expand: свой warmup сохранён", json.loads(days[1]["warmup_json"])[0]["slug"] == "leg_swings")
chk("expand: focus", json.loads(days[0]["focus_muscles_json"]) == ["chest", "back"])
chk("expand: status planned", all(d["status"] == "planned" and d["adjustments_json"] is None for d in days))
chk("day_to_row только колонки", set(L.day_to_row(days[0])) == set(L.DAY_COLUMNS))
chk("program_dates", L.program_dates(days) == ("2026-09-09", days[-1]["scheduled_date"]))
days_list = L.expand_program(TEMPLATE, PERIOD, dict(PROFILE, preferred_weekdays=[0, 2, 4]), "2026-09-07")
chk("expand: список дней недели, старт в Пн", days_list[0]["scheduled_date"] == "2026-09-07" and len(days_list) == 18)
days_bad = L.expand_program(TEMPLATE, PERIOD, dict(PROFILE, preferred_weekdays="1"), TODAY)
chk("expand: кривые weekdays → дефолт", all(d["weekday"] in (0, 2, 4) for d in days_bad))
chk("expand: пустой шаблон → []", L.expand_program({"days": []}, PERIOD, PROFILE, TODAY) == [])
chk("current_week", L.current_week(days, "2026-09-11") == 1 and L.current_week(days, "2026-09-16") == 2)
chk("next_planned_day", L.next_planned_day(days)["scheduled_date"] == "2026-09-09")

# --- catalog_for_prompt ---
CAT = [dict(e, id=i + 1) for i, e in enumerate(EXERCISES)]
def cols(text): return [[c.strip() for c in line.split("|")] for line in text.splitlines() if line.strip()]
bw = L.catalog_for_prompt(CAT, {"equipment": "bodyweight", "level": "intermediate", "limitations": ["knee"]})
rows = cols(bw)
chk("bodyweight-каталог не пуст и ≤110", 0 < len(rows) <= 110, len(rows))
chk("bodyweight: без штанги/тренажёров", all(r[3] in ("bodyweight", "none") for r in rows), {r[3] for r in rows})
chk("knee убирает jump_lunge", "jump_lunge" not in {r[0] for r in rows})
chk("bodyweight: есть отжимания", "pushup" in {r[0] for r in rows})
chk("формат 6 колонок", all(len(r) == 6 for r in rows), rows[:2])
chk("warmup/stretch в каталоге", {"warmup_general_5min", "stretch_full_body_5min"} <= {r[0] for r in rows})
gym = cols(L.catalog_for_prompt(CAT, {"equipment": "gym", "level": "advanced", "limitations": []}))
chk("gym: усечён до 110", len(gym) == 110, len(gym))
chk("gym: есть штанга", "bb_bench_press" in {r[0] for r in gym})
chk("gym: покрытие всех групп", {r[2] for r in gym} >= set(L.STRENGTH_GROUPS) | {"cardio", "mobility"}, {r[2] for r in gym})
beg = cols(L.catalog_for_prompt(CAT, {"equipment": "gym", "level": "beginner", "limitations_json": "[]"}))
chk("beginner: без сложности 3", all(int(r[5]) <= 2 for r in beg), [r for r in beg if int(r[5]) > 2][:3])
home = cols(L.catalog_for_prompt(CAT, {"equipment": "home_dumbbells", "equipment_extra": ["pullup_bar", "bands"], "level": "intermediate"}))
chk("home + чипы: подтягивания и резинки доступны", {"pullup", "band_row"} <= {r[0] for r in home} and "bb_bench_press" not in {r[0] for r in home})
chk("excluded_ids вычищает", "bb_bench_press" not in L.catalog_for_prompt(CAT, {"equipment": "gym"}, excluded_ids=[1]))

# --- alternatives_for ---
by_slug = {e["slug"]: e for e in CAT}
bb = by_slug["bb_bench_press"]
GYM = {"equipment": "gym", "limitations": []}
alt = L.alternatives_for(bb, CAT, GYM, "busy")
chk("alt: ≤5, та же мышца, без исходного", 1 <= len(alt) <= 5 and all(a["muscle_group"] == "chest" for a in alt) and bb not in alt, [a["slug"] for a in alt])
chk("alt: ручные первыми", alt[0]["slug"] == "db_bench_press", [a["slug"] for a in alt])
alt = L.alternatives_for(bb, CAT, GYM, "no_equipment")
chk("alt no_equipment: без штанги", alt and all(a["equipment"] != "barbell" for a in alt), [a["slug"] for a in alt])
alt = L.alternatives_for(bb, CAT, GYM, "pain")
chk("alt pain: без контриндицированных исходного (shoulder)", alt and all(not ({"shoulder", "pregnancy"} & set(json.loads(a["contraindications_json"]))) for a in alt), [a["slug"] for a in alt])
chk("alt pain: не сложнее исходного", all(a["difficulty"] <= bb["difficulty"] for a in alt))
alt = L.alternatives_for(bb, CAT, GYM, "busy", excluded_ids=[by_slug["db_bench_press"]["id"]])
chk("alt: excluded не возвращается", "db_bench_press" not in [a["slug"] for a in alt])
alt = L.alternatives_for(bb, CAT, {"equipment": "bodyweight"}, "busy")
chk("alt: фильтр оборудования профиля", alt and all(a["equipment"] in ("bodyweight", "none") for a in alt), [a["slug"] for a in alt])
alt = L.alternatives_for(bb, CAT, {"equipment": "gym", "limitations_json": '["wrist"]'}, "busy")
chk("alt: ограничения профиля всегда", all("wrist" not in json.loads(a["contraindications_json"]) for a in alt))
sq = by_slug["bb_back_squat"]
alt = L.alternatives_for(sq, CAT, GYM, "pain")
chk("alt pain: присед не остаётся без вариантов", 1 <= len(alt) <= 5 and all(a["muscle_group"] == "quads" for a in alt), [a["slug"] for a in alt])
chk("alt: мусорная причина → other", len(L.alternatives_for(bb, CAT, GYM, "xxx")) == len(L.alternatives_for(bb, CAT, GYM, "other")))

# --- функции с БД: временный sqlite ---
from backend.database import init_db, SessionLocal
from backend import models as M
from backend import trainer_seed
init_db()
db = SessionLocal()
trainer_seed.ensure_exercises(db)
db.add(M.User(telegram_id=1, language="ru", weight=80, daily_goal_kcal=2100, target_proteins=140, diet_goal="loss"))
db.add(M.TrainerProfile(telegram_id=1, goal="loss", level="intermediate", equipment="gym", equipment_extra_json="[]",
                        days_per_week=3, preferred_weekdays="0,2,4", session_minutes=45, program_weeks=6,
                        limitations_json='["lower_back"]', focus_json="[]", onboarding_completed=True))
db.commit()
lib = {e.slug: e for e in db.query(M.TrainerExercise).all()}
tpl = json.loads(json.dumps(TEMPLATE))
for d in tpl["days"]:
    for it in d["exercises"]:
        it["exercise_id"] = lib[it["slug"]].id
prog = M.TrainerProgram(telegram_id=1, status="active", title="Верх/Низ — 6 недель", split_type="upper_lower", goal="loss",
                        level="intermediate", equipment="gym", weeks=6, days_per_week=3, start_date="2026-09-07",
                        end_date="2026-10-16", periodization_json=json.dumps(PERIOD))
db.add(prog); db.commit()
day_rows = []
for d in L.expand_program(tpl, PERIOD, PROFILE, "2026-09-07"):
    row = M.TrainerProgramDay(telegram_id=1, program_id=prog.id, **L.day_to_row(d)); db.add(row); day_rows.append(row)
db.commit()
chk("db: 18 дней, первый Пн 07.09", len(day_rows) == 18 and day_rows[0].scheduled_date == "2026-09-07")
sess = M.TrainerSession(telegram_id=1, program_id=prog.id, program_day_id=day_rows[0].id, date="2026-09-07", status="completed",
                        title="День 1 — Верх", session_type="strength", week=1, day_index=1, duration_min=45,
                        total_sets=3, total_reps=36, total_volume_kg=720, calories_burned=300, feedback="hard", feedback_note="устал")
db.add(sess); db.commit()
day_rows[0].status = "done"; day_rows[0].session_id = sess.id
bench = lib["db_bench_press"]
sex = M.TrainerSessionExercise(telegram_id=1, session_id=sess.id, exercise_id=bench.id, block="main", order_index=1,
                               planned_sets=3, planned_reps_min=8, planned_reps_max=12, planned_weight_kg=20, planned_rest_sec=90, status="done")
db.add(sex); db.commit()
for i in range(1, 4):
    db.add(M.TrainerSetLog(telegram_id=1, session_id=sess.id, session_exercise_id=sex.id, exercise_id=bench.id, date="2026-09-07",
                           set_index=i, set_type="work", weight_kg=20, reps=12, is_done=True, volume_kg=240, est_1rm=28,
                           is_pr=(i == 1), pr_types_json='["max_weight"]' if i == 1 else None))
db.add(M.TrainerSetLog(telegram_id=1, session_id=sess.id, session_exercise_id=sex.id, exercise_id=bench.id, date="2026-09-07",
                       set_index=0, set_type="warmup", weight_kg=10, reps=10, is_done=True))
db.add(M.DiaryEntry(telegram_id=1, date="2026-09-07", meal_type="lunch", dish_name="Плов", calories=1800, proteins=120, fats=50, carbs=200))
db.add(M.DiaryEntry(telegram_id=1, date="2026-09-08", meal_type="lunch", dish_name="Гречка", calories=2000, proteins=130, fats=50, carbs=200))
db.add(M.WeightLog(telegram_id=1, weight=82.0, date="2026-09-01"))
db.add(M.WeightLog(telegram_id=1, weight=81.0, date="2026-09-07"))
db.add(M.TrainerExerciseState(telegram_id=1, exercise_id=bench.id, working_weight_kg=22, target_reps_min=8, target_reps_max=12))
db.commit()

stats = L.collect_week_stats(db, 1, "2026-09-07", today=TODAY)
chk("stats: неделя", stats["week_start"] == "2026-09-07" and stats["week_end"] == "2026-09-13")
chk("stats: программа неделя 1 из 6, фаза base", stats["program"] and stats["program"]["week"] == 1 and stats["program"]["weeks"] == 6 and stats["program"]["phase"] == "base", stats["program"])
chk("stats: план 3 / сделано 1", stats["plan"]["planned"] == 3 and stats["plan"]["done"] == 1 and stats["plan"]["missed"] == 0, stats["plan"])
chk("stats: сессия с отзывом", len(stats["sessions"]) == 1 and stats["sessions"][0]["feedback"] == "hard" and stats["sessions"][0]["note"] == "устал", stats["sessions"])
chk("stats: упражнение план→факт success + PR", stats["exercises"] and stats["exercises"][0]["result"] == "success" and stats["exercises"][0]["prs"] == ["max_weight"] and stats["exercises"][0]["fact"]["sets"] == 3, stats["exercises"])
chk("stats: сеты по мышцам (warmup мимо)", stats["muscle_sets"] == {"chest": 3}, stats["muscle_sets"])
chk("stats: питание ккал/белок vs цели", stats["nutrition"] == {"days_logged": 2, "avg_kcal": 1900, "avg_protein": 125, "goal_kcal": 2100, "goal_protein": 140}, stats["nutrition"])
chk("stats: вес первый/последний", stats["weight"]["first"] == 82.0 and stats["weight"]["last"] == 81.0 and stats["weight"]["delta"] == -1.0, stats["weight"])
chk("stats: diet_goal и ограничения", stats["diet_goal"] == "loss" and stats["limitations"] == ["lower_back"])
chk("stats: каталог для swap без lower_back", stats["catalog"] and "bb_deadlift" not in {c["slug"] for c in stats["catalog"]} and len(stats["catalog"]) <= 110, len(stats["catalog"]))
chk("stats: JSON-сериализуем", json.dumps(stats, ensure_ascii=False))
empty = L.collect_week_stats(db, 1, "2026-08-03", today=TODAY)
chk("stats: пустая неделя не падает", empty["sessions"] == [] and empty["nutrition"]["days_logged"] == 0)

ctx = L.today_training_context(db, 1, "2026-09-07", "ru")
chk("context: завершённая сессия", ctx and "День 1 — Верх" in ctx and "45 мин" in ctx and "300 ккал" in ctx and "белку 140" in ctx, ctx)
chk("context EN", "Workout done today" in (L.today_training_context(db, 1, "2026-09-07", "en") or ""), L.today_training_context(db, 1, "2026-09-07", "en"))
chk("context: planned день", "по плану" in (L.today_training_context(db, 1, "2026-09-09", "ru") or "") and "Низ" in (L.today_training_context(db, 1, "2026-09-09", "ru") or ""), L.today_training_context(db, 1, "2026-09-09", "ru"))
chk("context: без тренировки → None", L.today_training_context(db, 1, TODAY, "ru") is None, L.today_training_context(db, 1, TODAY, "ru"))
chk("context: чужой пользователь → None", L.today_training_context(db, 2, "2026-09-07", "ru") is None)

alt_db = L.alternatives_for_db(db, 1, lib["bb_bench_press"], "busy")
chk("alternatives_for_db", alt_db and all(a.muscle_group == "chest" for a in alt_db) and len(alt_db) <= 5, [a.slug for a in alt_db])
db.add(M.TrainerExerciseState(telegram_id=1, exercise_id=lib["db_bench_press"].id if False else lib["machine_chest_press"].id, excluded=True)); db.commit()
chk("alternatives_for_db: excluded мимо", "machine_chest_press" not in [a.slug for a in L.alternatives_for_db(db, 1, lib["bb_bench_press"], "busy")])

review = M.TrainerWeeklyReview(telegram_id=1, program_id=prog.id, week=1, week_start="2026-09-07", week_end="2026-09-13", stats_json="{}",
                               review_json=json.dumps({"summary": "ок", "changes": [
                                   {"id": 0, "type": "weight_pct", "exercise_slug": "db_bench_press", "exercise_id": bench.id, "value": -10, "reason": "тяжело"},
                                   {"id": 1, "type": "sets", "exercise_slug": "lat_pulldown", "exercise_id": lib["lat_pulldown"].id, "value": 1, "reason": "мало"},
                                   {"id": 2, "type": "swap", "exercise_slug": "goblet_squat", "exercise_id": lib["goblet_squat"].id, "new_slug": "leg_press", "new_exercise_id": lib["leg_press"].id, "reason": "колено"},
                                   {"id": 3, "type": "deload_next_week", "value": 1, "reason": "3 hard"},
                                   {"id": 4, "type": "rest_sec", "exercise_slug": "pushup", "exercise_id": lib["pushup"].id, "value": 45, "reason": "мало отдыха"},
                                   {"id": 5, "type": "nonsense", "value": 1},
                               ]}, ensure_ascii=False), applied=False)
db.add(review); db.commit()
res = L.apply_review_changes(db, 1, review, [0, 1, 2, 3, 9], "ru")
chk("apply: применены 0..3", res["applied"] == [0, 1, 2, 3] and res["next_week"] == 2, res)
chk("apply: 4 строки RU", len(res["lines"]) == 4 and any("18" in s and "кг" in s for s in res["lines"]) and any("разгрузочная" in s for s in res["lines"]), res["lines"])
db.expire_all()
w2 = {d.day_index: d for d in db.query(M.TrainerProgramDay).filter(M.TrainerProgramDay.program_id == prog.id, M.TrainerProgramDay.week == 2).all()}
w1 = {d.day_index: d for d in db.query(M.TrainerProgramDay).filter(M.TrainerProgramDay.program_id == prog.id, M.TrainerProgramDay.week == 1).all()}
ex2 = {e["slug"]: e for e in json.loads(w2[1].exercises_json)}
chk("apply: вес 20 → 18 на неделе 2", ex2["db_bench_press"]["start_weight_kg"] == 18.0, ex2)
chk("apply: +1 сет lat_pulldown", ex2["lat_pulldown"]["sets"] == 4, ex2)
chk("apply: неделя 1 не тронута", json.loads(w1[1].exercises_json)[0]["start_weight_kg"] == 20)
d2 = json.loads(w2[2].exercises_json)[0]
chk("apply: swap goblet → leg_press", d2["slug"] == "leg_press" and d2["exercise_id"] == lib["leg_press"].id, d2)
chk("apply: deload на всех днях недели 2", all(json.loads(d.adjustments_json or "{}").get("deload") for d in w2.values()), [d.adjustments_json for d in w2.values()])
st = db.query(M.TrainerExerciseState).filter_by(telegram_id=1, exercise_id=bench.id).first()
chk("apply: state 22 → 20", st.working_weight_kg == 20.0, st.working_weight_kg)
st2 = db.query(M.TrainerExerciseState).filter_by(telegram_id=1, exercise_id=lib["goblet_squat"].id).first()
chk("apply: preferred_alternative_id", st2 is not None and st2.preferred_alternative_id == lib["leg_press"].id)
db.refresh(review)
chk("apply: applied=True и ids записаны", review.applied is True and json.loads(review.review_json)["applied_change_ids"] == [0, 1, 2, 3], review.review_json[-80:])
res2 = L.apply_review_changes(db, 1, review, [0, 1], "en")
db.expire_all()
ex2b = {e["slug"]: e for e in json.loads(db.get(M.TrainerProgramDay, w2[1].id).exercises_json)}
chk("apply: повторно идемпотентно", res2["applied"] == [0, 1] and res2["lines"] == [] and ex2b["db_bench_press"]["start_weight_kg"] == 18.0 and ex2b["lat_pulldown"]["sets"] == 4, (res2, ex2b))
res3 = L.apply_review_changes(db, 1, review, [4], "en")
db.expire_all()
d3 = json.loads(db.get(M.TrainerProgramDay, w2[3].id).exercises_json)[0]
chk("apply: rest_sec EN", d3["rest_sec"] == 45 and res3["lines"] and "rest" in res3["lines"][0], (d3, res3))
db.close()

if fails:
    print("FAIL:"); [print("  -", f) for f in fails]; sys.exit(1)
print("OK: Epley/шаг, evaluate, таблица next_targets, explain RU/EN, PR (первая не PR, warmup мимо),")
print("    стрик, expand_program 6×3 с deload и датами, каталог/альтернативы, collect_week_stats,")
print("    today_training_context, alternatives_for_db, apply_review_changes (идемпотентно)")
