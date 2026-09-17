"""Проверка backend/trainer_ai.py (ТЗ §5.1–5.4, §8.1): промпты RU/EN, теги
телеметрии и лимиты токенов, контекст в user_prompt, нормализация ответов
(фаззи-матч slug, усечение дней, инжект разминки, clamp диапазонов),
устойчивость к мусору от AI и AIError на пустом ответе.

Генерация программы опирается на базу знаний (подробно — tests/test_trainer_knowledge.py):
здесь проверяем, что она встроена в промпт и результат, а сбой ИИ даёт программу из
шаблона, а не AIError. Детали нормализации проверяем на normalize_program напрямую —
аудит базы знаний потом законно меняет подходы, порядок и упражнения без оборудования.

Модуль чистый (без БД и приложения), поэтому TestClient не нужен — но env
выставляем ДО импорта backend, как во всех тестах проекта."""
import os, sys, tempfile, pathlib, json, types
from unittest import mock
ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "trai.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"

import logging
import re
from backend import ai_service
from backend.ai_service import AIError
from backend import trainer_ai as T
from backend import trainer_knowledge as K

# Сценарии сбоя ИИ пишут предупреждения «собираем по базе знаний» — ожидаемо, не шумим.
logging.getLogger("trainer_ai").setLevel(logging.CRITICAL)

fails = []
def chk(n, cond, x=""):
    if not cond: fails.append(n + ("  " + str(x) if x else ""))

# Ловим, что уходит в модель, и считаем вызовы.
captured = {}
calls = {"n": 0}
def make_fake(payload):
    def fake_run(system_prompt, user_prompt, log_tag, max_tokens=None):
        captured.clear()
        captured["system"] = system_prompt; captured["user"] = user_prompt
        captured["tag"] = log_tag; captured["max_tokens"] = max_tokens
        calls["n"] += 1
        return json.loads(json.dumps(payload)), {"raw": "{...}", "finish_reason": "stop", "refusal": None}
    return fake_run

def expect_aierror(name, fn):
    try:
        fn()
    except AIError:
        return
    except Exception as exc:  # noqa: BLE001
        fails.append(f"{name}: ожидали AIError, получили {type(exc).__name__}: {exc}"); return
    fails.append(f"{name}: AIError не выброшен")

# --------------------------------------------------------------------------- #
#  Каталог упражнений — приходит параметром (список dict, как из trainer_logic)
# --------------------------------------------------------------------------- #
def _ex(id_, slug, name_ru, name_en, muscle, equipment, category, measure, difficulty=1, contra=None):
    return {"id": id_, "slug": slug, "name_ru": name_ru, "name_en": name_en, "muscle_group": muscle,
            "equipment": equipment, "category": category, "measure_type": measure,
            "difficulty": difficulty, "contraindications_json": json.dumps(contra or [])}

CATALOG = [
    _ex(1, "warmup_general_5min", "Общая разминка 5 минут", "General Warm-Up (5 min)", "mobility", "none", "mobility", "time"),
    _ex(2, "stretch_full_body_5min", "Растяжка всего тела 5 минут", "Full-Body Stretch (5 min)", "mobility", "none", "stretch", "time"),
    _ex(3, "arm_circles", "Вращения руками", "Arm Circles", "mobility", "none", "mobility", "reps"),
    _ex(4, "chest_stretch", "Растяжка груди", "Chest Stretch", "mobility", "none", "stretch", "time"),
    _ex(5, "db_bench_press", "Жим гантелей лёжа", "Dumbbell Bench Press", "chest", "dumbbell", "compound", "reps_weight", 2, ["shoulder"]),
    _ex(6, "pushup", "Отжимания", "Push-Up", "chest", "bodyweight", "compound", "reps", 1, ["wrist"]),
    _ex(7, "bb_squat", "Присед со штангой", "Barbell Back Squat", "quads", "barbell", "compound", "reps_weight", 3, ["knee", "lower_back"]),
    _ex(8, "goblet_squat", "Гоблет-присед", "Goblet Squat", "quads", "dumbbell", "compound", "reps_weight", 1),
    _ex(9, "leg_press", "Жим ногами", "Leg Press", "quads", "machine", "compound", "reps_weight", 2),
    _ex(10, "jump_lunge", "Выпады с прыжком", "Jump Lunge", "quads", "bodyweight", "compound", "reps", 2, ["knee"]),
    _ex(11, "lat_pulldown", "Тяга верхнего блока", "Lat Pulldown", "back", "cable", "compound", "reps_weight", 1),
    _ex(12, "db_row", "Тяга гантели в наклоне", "Dumbbell Row", "back", "dumbbell", "compound", "reps_weight", 1),
    _ex(13, "plank", "Планка", "Plank", "core", "bodyweight", "isolation", "time"),
    _ex(14, "bb_deadlift", "Становая тяга", "Barbell Deadlift", "hamstrings", "barbell", "compound", "reps_weight", 3, ["lower_back"]),
    _ex(15, "rdl_db", "Румынская тяга с гантелями", "Dumbbell Romanian Deadlift", "hamstrings", "dumbbell", "compound", "reps_weight", 2),
    _ex(16, "treadmill_walk", "Ходьба на дорожке", "Treadmill Walk", "cardio", "cardio_machine", "cardio", "time"),
]
BY_SLUG = {e["slug"]: e for e in CATALOG}

PROFILE = {"goal": "loss", "level": "beginner", "equipment": "home_dumbbells", "equipment_extra": ["bench"],
           "days_per_week": 3, "preferred_weekdays": [0, 2, 4], "session_minutes": 45, "program_weeks": 6,
           "limitations": ["knee"], "limitations_text": "болит правое колено при глубоком приседе",
           "focus": ["glutes"], "known_weights": {"db_row": 10}}
BODY = {"gender": "female", "age": 29, "weight": 62, "height": 168, "diet_goal": "loss", "daily_goal_kcal": 1700}

# ======================================================================
#  §5.1 generate_program
# ======================================================================
GOOD_PROGRAM = {
    "title": "Всё тело — 6 недель", "split_type": "full_body", "summary": "Три дня full body под похудение.",
    "week_template": {"days": [
        {"day_index": 1, "title": "Всё тело A", "session_type": "strength", "focus_muscles": ["chest", "back"], "duration_min": 45,
         "warmup": [{"slug": "warmup_general_5min", "time_sec": 300}, {"slug": "arm_circles", "sets": 1, "reps": 15}],
         "exercises": [
             # опечатка в slug → фаззи-матч; значения за диапазонами → clamp
             {"slug": "db_bench_pres", "sets": 12, "reps_min": 10, "reps_max": 50, "rest_sec": 1000, "start_weight_kg": 999,
              "rpe": 7, "tempo": "2-0-2", "note": "локти под 45°"},
             # выдуманный slug → отброс
             {"slug": "zzz_unknown_xyz", "sets": 3, "reps_min": 10, "reps_max": 15, "rest_sec": 60},
             # вместо slug — название на английском → матч по имени
             {"slug": "Dumbbell Row", "sets": 3, "reps_min": 10, "reps_max": 15, "rest_sec": 60, "start_weight_kg": 10},
             # measure time: секунды вместо повторов
             {"slug": "plank", "sets": 3, "time_sec": 40, "rest_sec": 45},
         ],
         "cooldown": [{"slug": "chest_stretch", "time_sec": 30}]},
        # День без warmup/cooldown → инжект дефолтов; bb_squat при ограничении knee → замена
        {"day_index": 2, "title": "Всё тело B", "session_type": "strength", "focus_muscles": "quads, back", "duration_min": 45,
         "exercises": [
             {"slug": "bb_squat", "sets": 3, "reps_min": 10, "reps_max": 15, "rest_sec": 60, "start_weight_kg": 30},
             {"slug": "lat_pulldown", "sets": 3, "reps_min": 15, "reps_max": 10, "rest_sec": 60, "start_weight_kg": 25},
             # measure reps: вес должен быть отброшен
             {"slug": "pushup", "sets": 3, "reps_min": 8, "reps_max": 12, "rest_sec": 60, "start_weight_kg": 20},
         ]},
        {"day_index": 3, "title": "Всё тело C", "session_type": "mixed", "focus_muscles": ["hamstrings"], "duration_min": 45,
         "warmup": [{"slug": "warmup_general_5min", "time_sec": 300}],
         "exercises": [
             {"slug": "rdl_db", "sets": 3, "reps_min": 10, "reps_max": 15, "rest_sec": 60, "start_weight_kg": 12},
             {"slug": "treadmill_walk", "sets": 1, "time_sec": 600, "rest_sec": 60},
         ],
         "cooldown": [{"slug": "stretch_full_body_5min", "time_sec": 300}]},
        # Лишний (4-й) день при days_per_week=3 → усечение
        {"day_index": 4, "title": "Лишний день", "session_type": "strength",
         "exercises": [{"slug": "plank", "sets": 3, "time_sec": 30}]},
    ]},
    "periodization": [{"week": 1, "phase": "base", "weight_pct": 100, "sets_delta": 0},
                      {"week": 4, "phase": "deload", "weight_pct": 85, "sets_delta": -1},
                      {"week": 9, "phase": "peak"}],
    "tips": ["Пей воду", "Спи 8 часов", 42, None],
}

calls["n"] = 0
with mock.patch.object(ai_service, "_run_text_completion", make_fake(GOOD_PROGRAM)):
    prog = T.generate_program(PROFILE, BODY, CATALOG, lang="ru")
chk("program: один вызов модели", calls["n"] == 1, calls["n"])
chk("program: tag trainer_program", captured.get("tag") == "trainer_program", captured.get("tag"))
chk("program: max_tokens=4000", captured.get("max_tokens") == 4000, captured.get("max_tokens"))
sysp, userp = captured.get("system", ""), captured.get("user", "")
chk("program RU: «не врач» в system", "не врач" in sysp, sysp[:200])
chk("program RU: правила про slug в system", "slug" in sysp and "warmup_general_5min" in sysp)
chk("program RU: system на русском", "ПРАВИЛА" in sysp)
chk("program: цель в user_prompt", "похудеть" in userp and "loss" in userp, userp[:300])
chk("program: уровень в user_prompt", "новичок" in userp)
chk("program: ограничения в user_prompt", "колени" in userp and "болит правое колено" in userp)
chk("program: дни недели в user_prompt", "Пн, Ср, Пт" in userp)
chk("program: минуты/недели в user_prompt", "45 мин" in userp and "6 недель" in userp)
chk("program: тело в user_prompt", "62" in userp and "1700" in userp and "женский" in userp)
chk("program: известные веса в user_prompt", "db_row — 10 кг" in userp, userp[-600:])
chk("program: каталог строками slug|name_en|muscle|equipment|measure|difficulty",
    "db_bench_press | Dumbbell Bench Press | chest | dumbbell | reps_weight | 2" in userp, userp[-900:])
chk("program: каталог ≤110 строк", userp.count(" | ") <= 110 * 5)

# База знаний в промпте: выжимка под анкету перед каталогом, правило — в system.
chk("program RU: блок базы знаний в user_prompt", "БАЗА ЗНАНИЙ" in userp and "Рабочих подходов на мышцу" in userp
    and "Схема: " in userp, userp[:600])
chk("program RU: база знаний перед каталогом", 0 <= userp.find("БАЗА ЗНАНИЙ") < userp.find("КАТАЛОГ ("))
chk("program RU: правило базы знаний в system", "БАЗА ЗНАНИЙ" in sysp and "ОБЯЗАТЕЛЬНА" in sysp, sysp[:600])
sample_slugs = re.findall(r"([a-z_0-9]+) \([a-z_]+\) \d", userp)
chk("program: пример недели только из каталога запроса", sample_slugs and all(s in BY_SLUG for s in sample_slugs), sample_slugs)

days = prog["week_template"]["days"]
chk("program: лишний день усечён до days_per_week", len(days) == 3, len(days))
chk("program: day_index 1..3", [d["day_index"] for d in days] == [1, 2, 3], [d["day_index"] for d in days])
chk("program: unmatched содержит мусорный slug (generate)",
    any(u.get("slug") == "zzz_unknown_xyz" and u.get("reason") == "unknown" for u in prog["unmatched"]), prog["unmatched"])
chk("program: контриндицированное (knee) не осталось",
    not any("knee" in json.loads(BY_SLUG[e["slug"]]["contraindications_json"]) for d in days for e in d["exercises"]))
chk("program: без оборудования зала (дом с гантелями и скамьёй)",
    all(BY_SLUG[e["slug"]]["equipment"] in K.available_equipment(PROFILE) for d in days for e in d["exercises"]),
    [e["slug"] for d in days for e in d["exercises"]])
chk("program: разминка/заминка на месте", all(d["warmup"] and d["cooldown"] for d in days))
chk("program: периодизация модели (разгрузка новичку на 4-й неделе) → база знаний",
    prog["periodization"] == K.periodization_for(PROFILE), [p["phase"] for p in prog["periodization"]])
chk("program: первая подсказка — метод базы знаний",
    prog["tips"] == [K.method_tip(PROFILE, "ru"), "Пей воду", "Спи 8 часов"], prog["tips"])
chk("program: knowledge — источник ai и правки",
    prog["knowledge"]["source"] == "ai" and isinstance(prog["knowledge"]["fixes"], list), prog.get("knowledge"))

# Детали нормализации — на normalize_program (тот же ответ модели, без аудита базы знаний).
norm = T.normalize_program(GOOD_PROGRAM, CATALOG, days_per_week=3, weeks=6, limitations=["knee"], lang="ru", session_minutes=45)
days = norm["week_template"]["days"]
d1, d2, d3 = days
s1 = [e["slug"] for e in d1["exercises"]]
chk("program: опечатка slug → фаззи-матч по slug", "db_bench_press" in s1, s1)
chk("program: название вместо slug → матч по имени", "db_row" in s1, s1)
chk("program: мусорный slug отброшен", "zzz_unknown_xyz" not in s1 and len(s1) == 3, s1)
chk("program: unmatched содержит мусорный slug",
    any(u.get("slug") == "zzz_unknown_xyz" and u.get("reason") == "unknown" for u in norm["unmatched"]), norm["unmatched"])
bench = d1["exercises"][0]
chk("program: sets clamp 1–6", bench["sets"] == 6, bench["sets"])
chk("program: reps clamp 1–30", bench["reps_min"] == 10 and bench["reps_max"] == 30, (bench["reps_min"], bench["reps_max"]))
chk("program: rest clamp 20–300", bench["rest_sec"] == 300, bench["rest_sec"])
chk("program: weight clamp 0–300", bench["start_weight_kg"] == 300.0, bench["start_weight_kg"])
chk("program: exercise_id из каталога", bench["exercise_id"] == 5 and bench["muscle_group"] == "chest", bench)
chk("program: rpe/tempo/note/order", bench["rpe"] == 7 and bench["tempo"] == "2-0-2" and "локти" in bench["note"] and bench["order"] == 1, bench)
plank = next(e for e in d1["exercises"] if e["slug"] == "plank")
chk("program: time-упражнение — time_sec, без повторов", plank["time_sec"] == 40 and plank["reps_min"] is None and plank["start_weight_kg"] is None, plank)
chk("program: warmup дня 1 из ответа", [w["slug"] for w in d1["warmup"]] == ["warmup_general_5min", "arm_circles"], d1["warmup"])
chk("program: cooldown дня 1 из ответа", [w["slug"] for w in d1["cooldown"]] == ["chest_stretch"] and d1["cooldown"][0]["time_sec"] == 30, d1["cooldown"])
chk("program: focus_muscles дня 1", d1["focus_muscles"] == ["chest", "back"], d1["focus_muscles"])

chk("program: отсутствие warmup → инжект warmup_general_5min",
    len(d2["warmup"]) == 1 and d2["warmup"][0]["slug"] == "warmup_general_5min"
    and d2["warmup"][0]["time_sec"] == 300 and d2["warmup"][0]["exercise_id"] == 1, d2["warmup"])
chk("program: отсутствие cooldown → инжект stretch_full_body_5min",
    len(d2["cooldown"]) == 1 and d2["cooldown"][0]["slug"] == "stretch_full_body_5min" and d2["cooldown"][0]["exercise_id"] == 2, d2["cooldown"])
s2 = [e["slug"] for e in d2["exercises"]]
chk("program: контриндицированное (knee) заменено альтернативой на ту же мышцу",
    "bb_squat" not in s2 and "jump_lunge" not in s2 and s2[0] in ("goblet_squat", "leg_press"), s2)
chk("program: замена отмечена в unmatched",
    any(u.get("slug") == "bb_squat" and u.get("reason") == "contraindicated" and u.get("replaced_with") == s2[0] for u in norm["unmatched"]), norm["unmatched"])
lat = next(e for e in d2["exercises"] if e["slug"] == "lat_pulldown")
chk("program: reps_min > reps_max → меняем местами", lat["reps_min"] == 10 and lat["reps_max"] == 15, lat)
push = next(e for e in d2["exercises"] if e["slug"] == "pushup")
chk("program: вес у bodyweight (reps) отброшен", push["start_weight_kg"] is None and push["reps_min"] == 8, push)
chk("program: focus_muscles из CSV-строки", d2["focus_muscles"] == ["quads", "back"], d2["focus_muscles"])
chk("program: session_type mixed сохранён", d3["session_type"] == "mixed", d3["session_type"])
chk("program: кардио на время", next(e for e in d3["exercises"] if e["slug"] == "treadmill_walk")["time_sec"] == 600)

per = norm["periodization"]
chk("program: периодизация дополнена до 6 недель", [p["week"] for p in per] == [1, 2, 3, 4, 5, 6], per)
chk("program: недели без фазы → base", per[1]["phase"] == "base" and per[1]["weight_pct"] == 100 and per[1]["sets_delta"] == 0, per[1])
chk("program: неделя 4 deload из ответа", per[3]["phase"] == "deload" and per[3]["weight_pct"] == 85 and per[3]["sets_delta"] == -1, per[3])
chk("program: последняя неделя → deload", per[5]["phase"] == "deload", per[5])
chk("program: неделя 9 (за пределами) отброшена", all(p["week"] <= 6 for p in per))
chk("program: подписи фаз ru/en", per[3]["label_ru"] == "Разгрузка" and per[3]["label_en"] == "Deload", per[3])
chk("program: tips — только строки", norm["tips"] == ["Пей воду", "Спи 8 часов"], norm["tips"])
chk("program: title/split/summary", prog["title"] == "Всё тело — 6 недель" and prog["split_type"] == "full_body" and "full body" in prog["summary"], prog["title"])
chk("program: ai_model заполнен", bool(prog.get("ai_model")), prog.get("ai_model"))

# --- EN промпт по lang ---
with mock.patch.object(ai_service, "_run_text_completion", make_fake(GOOD_PROGRAM)):
    prog_en = T.generate_program(PROFILE, BODY, CATALOG, lang="en")
chk("program EN: «not a doctor» в system", "not a doctor" in captured.get("system", ""), captured.get("system", "")[:200])
chk("program EN: system на английском", "PROGRAM RULES" in captured.get("system", ""))
chk("program EN: user_prompt на английском", "Goal: fat loss (loss)" in captured.get("user", "") and "Limitations/injuries: knees" in captured.get("user", ""), captured.get("user", "")[:300])
chk("program EN: KNOWLEDGE BASE перед CATALOG",
    0 <= captured.get("user", "").find("KNOWLEDGE BASE") < captured.get("user", "").find("CATALOG ("))
chk("program EN: правило базы знаний в system", "KNOWLEDGE BASE" in captured.get("system", "") and "MANDATORY" in captured.get("system", ""))
chk("program EN: метод базы знаний первой подсказкой", prog_en["tips"][0] == K.method_tip(PROFILE, "en"), prog_en["tips"])
chk("program EN: те же ключи JSON", set(prog_en) == set(prog), set(prog_en) ^ set(prog))
# Неизвестный/пустой lang → русский
with mock.patch.object(ai_service, "_run_text_completion", make_fake(GOOD_PROGRAM)):
    T.generate_program(PROFILE, BODY, CATALOG, lang=None)
chk("program lang=None → RU", "не врач" in captured.get("system", ""))
with mock.patch.object(ai_service, "_run_text_completion", make_fake(GOOD_PROGRAM)):
    T.generate_program(PROFILE, BODY, CATALOG, lang="EN")
chk("program lang='EN' → EN", "not a doctor" in captured.get("system", ""))

# --- каталог строкой (как из trainer_logic.catalog_for_prompt) + catalog_map ---
catalog_str = "\n".join(f"{e['slug']} | {e['name_en']} | {e['muscle_group']} | {e['equipment']} | {e['measure_type']} | {e['difficulty']}" for e in CATALOG)
with mock.patch.object(ai_service, "_run_text_completion", make_fake(GOOD_PROGRAM)):
    prog_s = T.generate_program(PROFILE, BODY, catalog_str, lang="ru", catalog_map=CATALOG)
chk("program: строка каталога уходит в промпт как есть", catalog_str in captured.get("user", ""))
chk("program: catalog_map даёт exercise_id",
    any(e["slug"] == "db_bench_press" and e["exercise_id"] == 5 for e in prog_s["week_template"]["days"][0]["exercises"]))
with mock.patch.object(ai_service, "_run_text_completion", make_fake(GOOD_PROGRAM)):
    prog_s2 = T.generate_program(PROFILE, BODY, catalog_str, lang="ru")
s2_all = [e for d in prog_s2["week_template"]["days"] for e in d["exercises"]]
chk("program: каталог только строкой — slug резолвятся, id нет",
    [e["slug"] for e in T.normalize_program(GOOD_PROGRAM, catalog_str, days_per_week=3)["week_template"]["days"][0]["exercises"]]
    == ["db_bench_press", "db_row", "plank"]
    and "db_bench_press" in [e["slug"] for e in s2_all] and all(e["exercise_id"] is None for e in s2_all), s2_all)

# --- пустой/непригодный ответ или сбой ИИ → программа из шаблона базы знаний, не AIError ---
def expect_template(name, fn, days_expected=3):
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        fails.append(f"{name}: ожидали программу из шаблона, получили {type(exc).__name__}: {exc}"); return
    tdays = result["week_template"]["days"]
    chk(f"{name}: ai_model knowledge-template", result.get("ai_model") == T.KNOWLEDGE_TEMPLATE_MODEL, result.get("ai_model"))
    chk(f"{name}: дни с упражнениями из каталога", len(tdays) == days_expected
        and all(d["exercises"] and all(e["slug"] in BY_SLUG for e in d["exercises"]) for d in tdays),
        [[e["slug"] for e in d["exercises"]] for d in tdays])
    chk(f"{name}: те же ключи, что у ответа модели", set(result) == set(prog), set(result) ^ set(prog))
    chk(f"{name}: метод первой подсказкой",
        result["tips"][0] == K.method_tip(dict(PROFILE, days_per_week=days_expected), "ru"), result["tips"])
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: ({}, {})):
    expect_template("program: ({}, {}) → шаблон", lambda: T.generate_program(PROFILE, BODY, CATALOG, "ru"))
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: ([], {})):
    expect_template("program: не dict → шаблон", lambda: T.generate_program(PROFILE, BODY, CATALOG, "ru"))
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: ({"title": "x", "week_template": {"days": "нет"}}, {})):
    expect_template("program: days не список → шаблон", lambda: T.generate_program(PROFILE, BODY, CATALOG, "ru"))
one_day = {"week_template": {"days": [GOOD_PROGRAM["week_template"]["days"][0]]}}
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: (one_day, {})):
    expect_template("program: дней меньше days_per_week → шаблон", lambda: T.generate_program(PROFILE, BODY, CATALOG, "ru"))
only_junk = {"week_template": {"days": [{"day_index": 1, "exercises": [{"slug": "qqq_zzz_1"}, {"slug": "qqq_zzz_2"}]}]}}
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: (only_junk, {})):
    expect_template("program: все slug мусорные → шаблон на 1 день",
                    lambda: T.generate_program(dict(PROFILE, days_per_week=1), BODY, CATALOG, "ru"), days_expected=1)
def _boom(*a, **k):
    raise AIError("AI не ответил (trainer_program)", raw="")
with mock.patch.object(ai_service, "_run_text_completion", _boom):
    expect_template("program: AIError движка → шаблон", lambda: T.generate_program(PROFILE, BODY, CATALOG, "ru"))
# Нормализатор сам по себе по-прежнему строг: непригодный ответ → AIError (его ловит generate_program).
expect_aierror("normalize_program: дней меньше days_per_week → AIError",
               lambda: T.normalize_program(one_day, CATALOG, days_per_week=3))
expect_aierror("normalize_program: все slug мусорные → AIError", lambda: T.normalize_program(only_junk, CATALOG, days_per_week=1))
# Не собрался и шаблон — AIError движка пробрасывается (маршрут ответит 502).
with mock.patch.object(ai_service, "_run_text_completion", _boom), mock.patch.object(K, "build_week", side_effect=ValueError("x")):
    expect_aierror("program: шаблон не собрался → AIError", lambda: T.generate_program(PROFILE, BODY, CATALOG, "ru"))

# --- normalize_program напрямую: мусор в полях не роняет ---
junk_prog = {"title": 123, "split_type": "weird", "summary": None,
             "week_template": {"days": [
                 {"day_index": "abc", "title": None, "session_type": "dance", "focus_muscles": ["chest", "nope", 5],
                  "duration_min": "много", "warmup": "не список", "cooldown": None,
                  "exercises": ["db_bench_press", {"slug": "plank", "sets": "три", "time_sec": "x"}, 7, None]},
                 "не день"]},
             "periodization": "нет", "tips": "одна строка\nвторая строка"}
np_ = T.normalize_program(junk_prog, CATALOG, days_per_week=1, weeks=4, lang="ru", session_minutes=30)
nd = np_["week_template"]["days"][0]
chk("normalize_program: мусор → 1 день, 2 упражнения", len(np_["week_template"]["days"]) == 1 and [e["slug"] for e in nd["exercises"]] == ["db_bench_press", "plank"], nd["exercises"])
chk("normalize_program: дефолты sets/reps/rest", nd["exercises"][0]["sets"] == 3 and nd["exercises"][0]["reps_min"] == 8 and nd["exercises"][0]["reps_max"] == 12 and nd["exercises"][0]["rest_sec"] == 90, nd["exercises"][0])
chk("normalize_program: session_type мусор → strength, title дефолт", nd["session_type"] == "strength" and nd["title"] == "День 1", nd)
chk("normalize_program: duration_min из session_minutes", nd["duration_min"] == 30, nd["duration_min"])
chk("normalize_program: focus только известные группы", nd["focus_muscles"] == ["chest"], nd["focus_muscles"])
chk("normalize_program: warmup/cooldown инжект при мусоре", nd["warmup"][0]["slug"] == "warmup_general_5min" and nd["cooldown"][0]["slug"] == "stretch_full_body_5min")
chk("normalize_program: split мусор → по числу дней", np_["split_type"] == "full_body", np_["split_type"])
chk("normalize_program: title дефолт с неделями", np_["title"] == "Всё тело — 4 недели", np_["title"])
chk("normalize_program: периодизация мусор → 4 недели, последняя deload", [p["phase"] for p in np_["periodization"]] == ["base", "base", "base", "deload"], np_["periodization"])
chk("normalize_program: tips из строки с переносами", np_["tips"] == ["одна строка", "вторая строка"], np_["tips"])
expect_aierror("normalize_program: {} → AIError", lambda: T.normalize_program({}, CATALOG))
expect_aierror("normalize_program: None → AIError", lambda: T.normalize_program(None, CATALOG))

# ======================================================================
#  §5.3 weekly_review
# ======================================================================
STATS = {"program": {"goal": "loss", "week": 2, "weeks": 6, "phase": "base"},
         "plan_vs_fact": {"planned": 3, "done": 2, "skipped": 1},
         "sessions": [{"date": "2026-09-01", "duration_min": 48, "volume_kg": 3200, "feedback": "hard", "note": "устал"}],
         "exercises": [{"slug": "bb_squat", "planned_weight": 30, "result": "fail", "pr": False}],
         "muscle_sets": {"quads": 6, "back": 6},
         "nutrition": {"avg_kcal": 1650, "goal_kcal": 1700, "avg_protein": 95, "target_protein": 120},
         "weight": {"first": 62.5, "last": 61.8}, "diet_goal": "loss", "limitations": ["knee"],
         "catalog": CATALOG}
GOOD_REVIEW = {
    "summary": "Неделя средняя: два дня из трёх.", "wins": ["Стабильные тренировки", 5], "issues": ["Присед не идёт"],
    "nutrition": ["Белок 95 г при норме 120 — добавь творог"],
    "changes": [
        {"type": "weight_pct", "exercise_slug": "bb_squat", "value": -40, "reason": "два раза не добил"},   # clamp → -15
        {"type": "volume", "exercise_slug": "bb_squat", "value": 3, "reason": "недопустимый тип"},          # отброс
        {"type": "swap", "exercise_slug": "bb_deadlift", "new_slug": "db_bench_press", "reason": "чужая мышца"},  # отброс
        {"type": "swap", "exercise_slug": "bb_deadlift", "new_slug": "rdl_db", "reason": "поясница"},       # ок
        {"type": "sets", "exercise_slug": "lat_pulldown", "value": 3, "reason": "спина недогружена"},       # → +1
        {"type": "rest_sec", "exercise_slug": "db_row", "reason": "без значения"},                          # отброс
        {"type": "rest_sec", "exercise_slug": "db_row", "value": 5, "reason": "мало отдыха"},               # clamp → 20
        {"type": "weight_pct", "exercise_slug": "lat_pulldown", "value": 5, "reason": "вторая правка того же"},  # отброс
        {"type": "weight_pct", "exercise_slug": "no_such_exercise_qqq", "value": 5, "reason": "неизвестный slug"},  # отброс
        {"type": "deload_next_week", "value": 1, "reason": "тяжёлые отзывы"},                               # ок (5-я)
        {"type": "weight_pct", "exercise_slug": "db_bench_press", "value": 5, "reason": "шестая — за лимитом"},  # отброс
        "мусор",
    ],
    "next_week_focus": "Техника приседа", "motivation": "Ты молодец!",
}
calls["n"] = 0
with mock.patch.object(ai_service, "_run_text_completion", make_fake(GOOD_REVIEW)):
    rev = T.weekly_review(STATS, lang="ru")
chk("review: один вызов модели", calls["n"] == 1)
chk("review: tag trainer_review", captured.get("tag") == "trainer_review", captured.get("tag"))
chk("review: max_tokens=1500", captured.get("max_tokens") == 1500, captured.get("max_tokens"))
sysp, userp = captured.get("system", ""), captured.get("user", "")
chk("review RU: «не врач» в system", "не врач" in sysp)
chk("review RU: правила правок в system", "weight_pct" in sysp and "deload_next_week" in sysp and "-15" in sysp)
chk("review: stats в user_prompt (ккал/белок/вес)", '"avg_protein": 95' in userp and '"goal_kcal": 1700' in userp and "61.8" in userp, userp[:400])
chk("review: отзыв и заметка в user_prompt", '"hard"' in userp and "устал" in userp)
chk("review: каталог для swap строками", "Разрешённые slug для swap" in userp and "rdl_db | Dumbbell Romanian Deadlift | hamstrings" in userp, userp[-500:])
chk("review: каталог не дублируется JSON-ом", '"catalog"' not in userp)
ch = rev["changes"]
chk("review: 5 изменений (лимит)", len(ch) == 5, [(c["type"], c["exercise_slug"], c["value"]) for c in ch])
chk("review: id = индекс", [c["id"] for c in ch] == [0, 1, 2, 3, 4], [c["id"] for c in ch])
chk("review: типы по порядку", [c["type"] for c in ch] == ["weight_pct", "swap", "sets", "rest_sec", "deload_next_week"], [c["type"] for c in ch])
chk("review: weight_pct −40 → clamp −15", ch[0]["exercise_slug"] == "bb_squat" and ch[0]["value"] == -15, ch[0])
chk("review: exercise_id/имена из каталога", ch[0]["exercise_id"] == 7 and ch[0]["exercise_name_ru"] == "Присед со штангой" and ch[0]["exercise_name_en"] == "Barbell Back Squat", ch[0])
chk("review: swap на чужую мышцу отброшен, на свою — принят",
    ch[1]["exercise_slug"] == "bb_deadlift" and ch[1]["new_slug"] == "rdl_db" and ch[1]["new_exercise_id"] == 15
    and ch[1]["new_exercise_name_ru"] == "Румынская тяга с гантелями", ch[1])
chk("review: sets 3 → +1", ch[2]["exercise_slug"] == "lat_pulldown" and ch[2]["value"] == 1, ch[2])
chk("review: rest_sec без значения отброшен, 5 → clamp 20", ch[3]["exercise_slug"] == "db_row" and ch[3]["value"] == 20 and ch[3]["reason"] == "мало отдыха", ch[3])
chk("review: deload_next_week без упражнения", ch[4]["exercise_slug"] is None and ch[4]["value"] == 1, ch[4])
chk("review: недопустимый тип отброшен", all(c["type"] != "volume" for c in ch))
chk("review: неизвестный slug отброшен", all(c["exercise_slug"] != "no_such_exercise_qqq" for c in ch))
chk("review: одна переменная на упражнение", len([c for c in ch if c["exercise_slug"] == "lat_pulldown"]) == 1)
chk("review: тексты", rev["summary"].startswith("Неделя средняя") and rev["wins"] == ["Стабильные тренировки"] and rev["issues"] == ["Присед не идёт"]
    and rev["nutrition"][0].startswith("Белок 95") and rev["next_week_focus"] == "Техника приседа" and rev["motivation"] == "Ты молодец!", rev)
chk("review: ключи ответа по ТЗ", set(rev) == {"summary", "wins", "issues", "nutrition", "changes", "next_week_focus", "motivation"}, set(rev))

# EN + каталог параметром (без ключа catalog в stats)
stats_no_cat = {k: v for k, v in STATS.items() if k != "catalog"}
with mock.patch.object(ai_service, "_run_text_completion", make_fake(GOOD_REVIEW)):
    rev_en = T.weekly_review(stats_no_cat, lang="en", catalog=CATALOG)
chk("review EN: «not a doctor» в system", "not a doctor" in captured.get("system", ""))
chk("review EN: user_prompt на английском с каталогом", "Allowed slugs for swap" in captured.get("user", "") and "Week data (JSON)" in captured.get("user", ""))
chk("review EN: swap проверен по каталогу из параметра", [c["type"] for c in rev_en["changes"]] == [c["type"] for c in ch])
# Без каталога вовсе: неизвестный тип всё равно отброшен, swap не может проверить мышцу — принимается «как есть»
nr = T.normalize_review({"summary": "ок", "changes": [{"type": "volume", "exercise_slug": "a"}, {"type": "weight_pct", "exercise_slug": "bb_squat", "value": 40}]})
chk("normalize_review без каталога: тип отброшен, +40 → +10", len(nr["changes"]) == 1 and nr["changes"][0]["value"] == 10 and nr["changes"][0]["exercise_id"] is None, nr["changes"])
# Пусто/мусор → AIError
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: ({}, {})):
    expect_aierror("review: ({}, {}) → AIError", lambda: T.weekly_review(STATS, "ru"))
# Строка вместо списка — режется на пункты (частая причуда модели), не-строка/не-список → []
nr2 = T.normalize_review({"summary": "ок", "wins": "первое\nвторое", "issues": 7, "nutrition": {"x": 1}})
chk("normalize_review: строка → пункты, мусор → []", nr2["wins"] == ["первое", "второе"] and nr2["issues"] == [] and nr2["nutrition"] == [], nr2)
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: ({"summary": 5, "wins": 7, "changes": {"x": 1}}, {})):
    expect_aierror("review: мусор без содержимого → AIError", lambda: T.weekly_review(STATS, "ru"))

# ======================================================================
#  §5.2 exercise_technique
# ======================================================================
EX = BY_SLUG["db_bench_press"]
GOOD_TECH = {
    "steps": ["Ляг на скамью", "Опусти гантели к груди", "Выжми вверх", 7, None, "Пять", "Шесть", "Семь", "Восемь"],
    "cues": "локти под 45°\nлопатки сведены\n- стопы в пол\n1. не отрывай таз\n\nпоследний\nлишний",
    "mistakes": ["Отбив от груди", "  Локти   в стороны  "],
    "breathing": 123, "safety": None, "muscles_text": "  Грудь,   трицепс ",
}
calls["n"] = 0
with mock.patch.object(ai_service, "_run_text_completion", make_fake(GOOD_TECH)):
    tech = T.exercise_technique(EX, limitations=["knee", "lower_back"], lang="ru")
chk("technique: один вызов", calls["n"] == 1)
chk("technique: tag trainer_technique", captured.get("tag") == "trainer_technique", captured.get("tag"))
chk("technique: max_tokens=700", captured.get("max_tokens") == 700, captured.get("max_tokens"))
sysp, userp = captured.get("system", ""), captured.get("user", "")
chk("technique RU: «не врач» в system", "не врач" in sysp)
chk("technique: упражнение в user_prompt", "Жим гантелей лёжа" in userp and "db_bench_press" in userp and "chest" in userp and "dumbbell" in userp, userp)
chk("technique: ограничения пользователя НЕ в промпте (кэш общий)", "колен" not in userp.lower() and "knee" not in userp.lower() and "поясниц" not in userp.lower(), userp)
chk("technique: steps ≤6, только строки", tech["steps"] == ["Ляг на скамью", "Опусти гантели к груди", "Выжми вверх", "Пять", "Шесть", "Семь"], tech["steps"])
chk("technique: cues из строки с переносами, ≤5, маркеры сняты", tech["cues"] == ["локти под 45°", "лопатки сведены", "стопы в пол", "не отрывай таз", "последний"], tech["cues"])
chk("technique: mistakes — пробелы схлопнуты", tech["mistakes"] == ["Отбив от груди", "Локти в стороны"], tech["mistakes"])
chk("technique: не-строки → пустые строки", tech["breathing"] == "" and tech["safety"] == "", (tech["breathing"], tech["safety"]))
chk("technique: muscles_text обрезан", tech["muscles_text"] == "Грудь, трицепс", tech["muscles_text"])
chk("technique: ключи по ТЗ", set(tech) == {"steps", "cues", "mistakes", "breathing", "safety", "muscles_text"}, set(tech))
# ORM-подобный объект (атрибуты вместо ключей) + EN
ex_obj = types.SimpleNamespace(**dict(EX, secondary_muscles_json='["triceps", "shoulders"]', is_unilateral=False))
with mock.patch.object(ai_service, "_run_text_completion", make_fake(GOOD_TECH)):
    tech_en = T.exercise_technique(ex_obj, None, lang="en")
chk("technique EN: «not a doctor» в system", "not a doctor" in captured.get("system", ""))
chk("technique EN: name_en и вторичные мышцы в промпте", "Dumbbell Bench Press" in captured.get("user", "") and "triceps" in captured.get("user", ""), captured.get("user", ""))
chk("technique EN: те же ключи", set(tech_en) == set(tech))
# Обрезка длинных строк
long_tech = {"steps": ["x" * 500], "breathing": "y" * 1000}
nt = T.normalize_technique(long_tech)
chk("normalize_technique: обрезка до лимита", len(nt["steps"][0]) <= 200 and len(nt["breathing"]) <= 300, (len(nt["steps"][0]), len(nt["breathing"])))
chk("normalize_technique: не-dict → пусто", T.normalize_technique("мусор")["steps"] == [])
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: ({}, {})):
    expect_aierror("technique: ({}, {}) → AIError", lambda: T.exercise_technique(EX, [], "ru"))
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: ({"steps": [], "cues": ["x"]}, {})):
    expect_aierror("technique: без шагов → AIError", lambda: T.exercise_technique(EX, [], "ru"))

# ======================================================================
#  §5.4 nutrition_day_tip
# ======================================================================
CTX_TRAIN = {"kind": "training", "workout_title": "День 1 — Верх", "session_type": "strength", "duration_min": 45,
             "calories_burned": 320, "status": "planned", "diet_goal": "loss", "daily_goal_kcal": 2100,
             "target_proteins": 140, "eaten_kcal": 900, "eaten_protein": 60, "weight": 78.5, "sleep_hours": 6}
GOOD_TIP = {"headline": "Тренировочный день — белок в приоритете " + "х" * 300,
            "calories_note": "Цель 2100 ккал, съедено 900", "protein_note": "140 г белка, съедено 60",
            "pre_workout": "За 1–2 ч: каша с творогом", "post_workout": "После: курица с рисом",
            "hydration": "Пей 2 л", "tips": ["раз", 2, "два", None, "три", "четыре", "пять"]}
calls["n"] = 0
with mock.patch.object(ai_service, "_run_text_completion", make_fake(GOOD_TIP)):
    tip = T.nutrition_day_tip(CTX_TRAIN, lang="ru")
chk("tip: один вызов", calls["n"] == 1)
chk("tip: tag trainer_nutrition", captured.get("tag") == "trainer_nutrition", captured.get("tag"))
chk("tip: max_tokens=500", captured.get("max_tokens") == 500, captured.get("max_tokens"))
sysp, userp = captured.get("system", ""), captured.get("user", "")
chk("tip RU: «не врач» в system", "не врач" in sysp)
chk("tip RU: не менять цель калорий — в system", "НЕ меняй дневную цель" in sysp)
chk("tip: тип дня в user_prompt", "Тип дня: тренировочный" in userp, userp)
chk("tip: тренировка в user_prompt", "День 1 — Верх" in userp and "45 мин" in userp and "320 ккал" in userp and "по плану" in userp, userp)
chk("tip: цифры питания в user_prompt", "2100" in userp and "140" in userp and "900" in userp and "60" in userp and "78.5" in userp, userp)
chk("tip: прочие ключи контекста уходят JSON-ом", "sleep_hours" in userp, userp)
chk("tip: headline обрезан до 160", len(tip["headline"]) <= 160 and tip["headline"].startswith("Тренировочный день"), len(tip["headline"]))
chk("tip: tips ≤3, только строки", tip["tips"] == ["раз", "два", "три"], tip["tips"])
chk("tip: pre/post в тренировочный день", tip["pre_workout"].startswith("За 1–2 ч") and tip["post_workout"].startswith("После"), tip)
chk("tip: kind=training", tip["kind"] == "training")
chk("tip: ключи по ТЗ", set(tip) == {"kind", "headline", "calories_note", "protein_note", "pre_workout", "post_workout", "hydration", "tips"}, set(tip))
# День отдыха: pre/post → None даже если модель вернула; EN
CTX_REST = {"kind": "rest", "diet_goal": "loss", "daily_goal_kcal": 2100, "target_proteins": 140}
with mock.patch.object(ai_service, "_run_text_completion", make_fake(GOOD_TIP)):
    tip_rest = T.nutrition_day_tip(CTX_REST, lang="en")
chk("tip rest EN: «not a doctor» в system", "not a doctor" in captured.get("system", ""))
chk("tip rest EN: rest day в user_prompt", "rest day" in captured.get("user", "") and "Nothing logged today yet" in captured.get("user", ""), captured.get("user", ""))
chk("tip rest: pre/post → None", tip_rest["pre_workout"] is None and tip_rest["post_workout"] is None and tip_rest["kind"] == "rest", tip_rest)
chk("tip: неизвестный kind → rest", T.normalize_tip({"headline": "x", "pre_workout": "y"}, "party")["pre_workout"] is None)
chk("normalize_tip: не-dict → пусто", T.normalize_tip(None)["tips"] == [] and T.normalize_tip(None)["headline"] == "")
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: ({}, {})):
    expect_aierror("tip: ({}, {}) → AIError", lambda: T.nutrition_day_tip(CTX_TRAIN, "ru"))
with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: ({"headline": 5, "tips": 7, "hydration": None}, {})):
    expect_aierror("tip: мусор без содержимого → AIError", lambda: T.nutrition_day_tip(CTX_TRAIN, "ru"))

# --- Одинаковые JSON-ключи в RU/EN промптах (ТЗ §5) ---
import re as _re
def _keys(text):
    # Ключи JSON: в примере ответа — «"key":», в описании полей — «"key" — ...».
    return set(_re.findall(r'"([a-z_]+)"\s*(?::|—)', text))
for name, ru, en in (("program", T.PROGRAM_SYSTEM_PROMPT, T.PROGRAM_SYSTEM_PROMPT_EN),
                     ("technique", T.TECHNIQUE_SYSTEM_PROMPT, T.TECHNIQUE_SYSTEM_PROMPT_EN),
                     ("review", T.REVIEW_SYSTEM_PROMPT, T.REVIEW_SYSTEM_PROMPT_EN),
                     ("nutrition", T.NUTRITION_SYSTEM_PROMPT, T.NUTRITION_SYSTEM_PROMPT_EN)):
    chk(f"prompts {name}: одинаковые JSON-ключи RU/EN", _keys(ru) == _keys(en) and _keys(ru), _keys(ru) ^ _keys(en))
    chk(f"prompts {name}: правила безопасности в обоих", "не врач" in ru and "not a doctor" in en)
chk("prompts technique: ключи по ТЗ", {"steps", "cues", "mistakes", "breathing", "safety", "muscles_text"} <= _keys(T.TECHNIQUE_SYSTEM_PROMPT))
chk("prompts nutrition: ключи по ТЗ", {"headline", "calories_note", "protein_note", "pre_workout", "post_workout", "hydration", "tips"} <= _keys(T.NUTRITION_SYSTEM_PROMPT))
chk("prompts review: ключи по ТЗ", {"summary", "wins", "issues", "nutrition", "changes", "next_week_focus", "motivation", "type", "exercise_slug", "new_slug", "value", "reason"} <= _keys(T.REVIEW_SYSTEM_PROMPT))
chk("prompts program: ключи по ТЗ", {"title", "split_type", "summary", "week_template", "days", "day_index", "warmup", "exercises", "cooldown", "periodization", "tips", "start_weight_kg", "rest_sec", "reps_min", "reps_max"} <= _keys(T.PROGRAM_SYSTEM_PROMPT))

if fails:
    print("FAIL:"); [print("  -", f) for f in fails]; sys.exit(1)
print("OK: trainer_ai — теги/лимиты токенов, RU/EN промпты с правилами безопасности, контекст в user_prompt,")
print("    база знаний в промпте и результате (метод, периодизация, без противопоказанных и недоступного), сбой ИИ →")
print("    программа из шаблона, фаззи-матч и отброс slug, усечение дней, инжект разминки/заминки, clamp, замена контриндицированных,")
print("    разбор недели (типы/clamp/swap по мышце/лимит 5), техника и совет дня нормализуются, пусто → AIError")
