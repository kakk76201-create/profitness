"""Проверка базы знаний AI-тренера (backend/trainer_knowledge.py) и её подключения к
генерации программы (trainer_ai.generate_program, ТЗ §5.1, docs/TRAINER_KNOWLEDGE.md).

Покрыто: копии констант совпадают с trainer_logic/trainer_ai; все slug паттернов и
правил безопасности есть в каталоге; select_split находит схему на любые дни × уровень
× оборудование × цель; build_week — только существующие и разрешённые упражнения,
оборудование пользователя, время сессии, объём крупных мышц в диапазоне и тяга ≥ жима
на типичных анкетах; audit_week ловит неделю без тяг, изоляцию раньше базового и
противопоказания; prompt_brief ru/en; periodization_for; generate_program с подменённым
ИИ — блок базы знаний в промпте, правки аудита, подсказка о методе, периодизация, а
при сбое ИИ — программа из шаблона базы знаний.

Модули чистые (без БД и приложения), но env выставляем ДО импорта backend, как во
всех тестах проекта.
"""
import os, sys, tempfile, pathlib, json, itertools, logging, re
from unittest import mock
ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "trknow.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"

from backend import ai_service
from backend import trainer_ai as T
from backend import trainer_knowledge as K
from backend import trainer_logic as L
from backend.ai_service import AIError
from backend.trainer_exercises_seed import EXERCISES_BY_SLUG as SEED

# Предупреждения «ИИ не дал программу» ожидаемы в сценариях сбоя — не засоряем вывод run_all.
logging.getLogger("trainer_ai").setLevel(logging.CRITICAL)

fails = []
def chk(n, cond, x=""):
    if not cond: fails.append(n + ("  " + str(x) if x else ""))

def contra(slug):
    return set(json.loads(SEED[slug].get("contraindications_json") or "[]"))

def week_pull_push(days, cat=SEED):
    pull = push = 0
    for day in days:
        for item in day["exercises"]:
            entry = cat.get(item["slug"])
            if entry is None or entry["category"] not in ("compound", "isolation"):
                continue
            pattern = K._guess_pattern(item["slug"], entry)
            pull += item["sets"] if pattern in K.PULL_PATTERNS else 0
            push += item["sets"] if pattern in K.PUSH_PATTERNS else 0
    return pull, push

def safety_problems(days, profile, cat=SEED):
    """Недопустимые упражнения недели: противопоказания каталога, SAFETY, оборудование, сложность."""
    p = K._norm_profile(profile)
    limits = set(p["limitations"])
    safety = K._safety_for(p)
    equipment = K.available_equipment(p)
    max_diff = K.LEVEL_PARAMS[p["level"]]["max_difficulty"]
    out = []
    for day in days:
        for item in day["exercises"]:
            slug = item["slug"]
            entry = cat.get(slug)
            if entry is None:
                out.append(("unknown", slug)); continue
            if contra(slug) & limits:
                out.append(("contraindicated", slug))
            if slug in safety["exclude"] or K._guess_pattern(slug, entry) in safety["avoid"]:
                out.append(("not_recommended", slug))
            if entry["equipment"] not in equipment:
                out.append(("equipment", slug))
            if (entry.get("difficulty") or 1) > max_diff:
                out.append(("difficulty", slug))
        for aux in day["warmup"] + day["cooldown"]:
            if aux["slug"] not in cat:
                out.append(("unknown_aux", aux["slug"]))
    return out

def day_over_time(days, minutes, cat=SEED):
    return [round(K.estimate_day_seconds(d, cat) / 60, 1) for d in days
            if K.estimate_day_seconds(d, cat) > minutes * 60 * K.TIME_MODEL["tolerance"] + 1]


# =========================================================================== #
#  0. Копии констант совпадают с trainer_logic / trainer_ai (модуль их не импортирует)
# =========================================================================== #
chk("GOALS = trainer_logic", K.GOALS == L.GOALS, (K.GOALS, L.GOALS))
chk("LEVELS = trainer_logic", K.LEVELS == L.LEVELS)
chk("EQUIPMENT_PROFILES = trainer_logic", K.EQUIPMENT_PROFILES == L.EQUIPMENT_PROFILES)
chk("LIMITATIONS = trainer_logic без none", set(K.LIMITATIONS) == set(L.LIMITATION_CODES) - {"none"})
chk("FOCUS_CODES = trainer_logic без none", set(K.FOCUS_CODES) == set(L.FOCUS_CODES) - {"none"})
chk("SESSION_MINUTES / PROGRAM_WEEKS = trainer_logic",
    K.SESSION_MINUTES == L.SESSION_MINUTES and K.PROGRAM_WEEKS == L.PROGRAM_WEEKS)
chk("MUSCLES = trainer_logic.STRENGTH_GROUPS", K.MUSCLES == L.STRENGTH_GROUPS, (K.MUSCLES, L.STRENGTH_GROUPS))
chk("EQUIPMENT_BY_PROFILE = trainer_logic",
    {k: set(v) for k, v in K.EQUIPMENT_BY_PROFILE.items()} == {k: set(v) for k, v in L.EQUIPMENT_BY_PROFILE.items()})
chk("EXTRA_TO_EQUIPMENT = trainer_logic", K.EXTRA_TO_EQUIPMENT == L.EXTRA_TO_EQUIPMENT)
chk("разминка/заминка = trainer_logic",
    K.WARMUP_SLUG == L.REQUIRED_WARMUP_SLUG and K.COOLDOWN_SLUG == L.REQUIRED_COOLDOWN_SLUG)
chk("разгрузка = DELOAD_WEIGHT_PCT / DELOAD_SETS_DELTA",
    K.PHASE_GUIDE["deload"]["weight_pct"] == L.DELOAD_WEIGHT_PCT and K.PHASE_GUIDE["deload"]["sets_delta"] == L.DELOAD_SETS_DELTA)
chk("пределы = нормализатор trainer_ai",
    (K.SETS_RANGE, K.REPS_RANGE, K.REST_RANGE, K.RPE_RANGE, K.MAX_MAIN_EXERCISES)
    == (T.SETS_RANGE, T.REPS_RANGE, T.REST_RANGE, T.RPE_RANGE, T.MAX_MAIN_EXERCISES))


# =========================================================================== #
#  1. Все slug базы знаний существуют в каталоге
# =========================================================================== #
missing = []
for name, row in K.PATTERNS.items():
    for slug in row["options"] + [s for values in row["first_by_goal"].values() for s in values]:
        if slug not in SEED:
            missing.append(("PATTERNS", name, slug))
    for fallback in row["fallback"]:
        if fallback not in K.PATTERNS:
            missing.append(("PATTERNS.fallback", name, fallback))
    if name != "carry" and not row["options"]:
        missing.append(("PATTERNS пустой", name))
for code, rule in K.SAFETY.items():
    for slug in rule["exclude_slugs"]:
        if slug not in SEED:
            missing.append(("SAFETY.exclude", code, slug))
    for pattern, slugs in rule["prefer"].items():
        if pattern not in K.PATTERNS:
            missing.append(("SAFETY.prefer паттерн", code, pattern))
        missing += [("SAFETY.prefer", code, s) for s in slugs if s not in SEED]
    for pattern, fallbacks in rule["pattern_fallback"].items():
        missing += [("SAFETY.fallback", code, x) for x in [pattern] + fallbacks if x not in K.PATTERNS]
    missing += [("SAFETY.avoid", code, x) for x in rule["avoid_patterns"] if x not in K.PATTERNS]
    chk(f"SAFETY_BRIEF для {code}", code in K.SAFETY_BRIEF)
missing += [("SLUG_PATTERN", s) for s in K.SLUG_PATTERN if s not in SEED]
missing += [("WARMUP_DRILLS", s) for values in K.WARMUP_DRILLS.values() for s in values if s not in SEED]
missing += [("HEAVY_LOWER_BACK", s) for s in K.HEAVY_LOWER_BACK if s not in SEED]
missing += [("WARMUP/COOLDOWN", s) for s in (K.WARMUP_SLUG, K.COOLDOWN_SLUG) if s not in SEED]
for split in K.SPLITS:
    for day in split["days"]:
        missing += [("SPLITS", split["id"], s["pattern"]) for s in day["slots"] if s["pattern"] not in K.PATTERNS]
chk("все slug и паттерны базы знаний есть в каталоге", not missing, missing[:10])
uncovered = [s for s, e in SEED.items() if e["category"] in ("compound", "isolation", "cardio") and s not in K.SLUG_PATTERN]
chk("SLUG_PATTERN покрывает силовые и кардио упражнения каталога", not uncovered, uncovered[:10])


# =========================================================================== #
#  2. select_split: схема на любые дни × уровень × оборудование × цель
# =========================================================================== #
bad_split = []
for days, level, equipment, goal in itertools.product(range(2, 7), K.LEVELS, K.EQUIPMENT_PROFILES, K.GOALS):
    split = K.select_split({"days_per_week": days, "level": level, "equipment": equipment, "goal": goal})
    if split.get("id") not in K.SPLITS_BY_ID or split["days_per_week"] != days or len(split["days"]) != days:
        bad_split.append((days, level, equipment, goal, split.get("id")))
chk("select_split возвращает шаблон с нужным числом дней", not bad_split, bad_split[:5])
chk("select_split: мусорный профиль → осторожная схема", K.select_split(None)["id"] in K.SPLITS_BY_ID
    and K.select_split({"days_per_week": "abc", "goal": 5})["days_per_week"] == K.DEFAULT_PROFILE["days_per_week"])
chk("select_split: ягодичный акцент → ягодичная схема",
    "glutes" in K.select_split({"days_per_week": 3, "goal": "tone", "equipment": "gym", "focus": ["glutes"]})["tags"])


# =========================================================================== #
#  3. build_week: безопасность, оборудование, время — широкая сетка анкет
# =========================================================================== #
grid = [dict(days_per_week=d, level=lv, equipment=eq, goal=g, session_minutes=m, limitations=[])
        for d, lv, eq, g, m in itertools.product(range(2, 7), K.LEVELS, K.EQUIPMENT_PROFILES, K.GOALS, (30, 60))]
grid += [dict(days_per_week=d, level=lv, equipment=eq, goal=g, session_minutes=45, limitations=[lim])
         for lim, d, lv, eq, g in itertools.product(K.LIMITATIONS, (3, 4, 5), ("beginner", "intermediate"),
                                                    K.EQUIPMENT_PROFILES, ("muscle", "loss"))]
grid += [dict(days_per_week=4, level="intermediate", equipment="gym", goal="tone", session_minutes=60,
              limitations=["pregnancy", "heart_bp", "knee"], focus=["glutes", "arms"])]
broken = []
for profile in grid:
    week = K.build_week(profile)
    days = week["days"]
    if len(days) != profile["days_per_week"] or any(not d["exercises"] for d in days):
        broken.append(("дни", profile, len(days)))
    problems = safety_problems(days, profile)
    if problems:
        broken.append(("безопасность", profile, problems[:3]))
    over = day_over_time(days, profile["session_minutes"])
    if over:
        broken.append(("время", profile, over))
    if week["split_type"] not in T.SPLIT_TYPES:
        broken.append(("split_type", profile, week["split_type"]))
chk(f"build_week: {len(grid)} анкет без запрещённых упражнений, в пределах времени", not broken, broken[:4])

# Качество дней глазами тренера (регрессии найденных проверкой проблем):
#  * замена движения не дублирует уже занятое в дне («присед → мост» при коленях + свой мост;
#    «жим вверх → задняя дельта» при плечах + тяга к лицу) — PPL-дни с двумя приседами задуманы;
#  * «+ кардио» в названии только если кардио в дне есть;
#  * отдых в упражнениях с весом тела и удержаниях ≤120 с (кроме нижнего предела ограничений);
#  * силовой день — не меньше 2 силовых упражнений;
#  * при беременности нет упражнений лёжа на спине или на животе.
SUPINE_PRONE = {"bb_bench_press", "db_bench_press", "db_fly", "skull_crusher", "close_grip_bench_press", "glute_bridge",
                "single_leg_glute_bridge", "hip_thrust", "db_hip_thrust", "hollow_hold", "crunch", "reverse_crunch",
                "bicycle_crunch", "dead_bug", "leg_raise", "russian_twist", "superman", "hyperextension", "leg_curl"}
FALLBACK_DUPES = ("hip_thrust", "rear_delt", "glute_abduction", "vertical_push", "lateral_delt", "knee_flexion")
quality = []
for d, lv, eq, g, lims, mins in itertools.product((2, 3, 4, 6), K.LEVELS, K.EQUIPMENT_PROFILES, K.GOALS,
                                                  ([], ["knee"], ["shoulder"], ["lower_back"], ["pregnancy", "knee"],
                                                   ["heart_bp"], ["wrist"]), (30, 60)):
    profile = dict(days_per_week=d, level=lv, equipment=eq, goal=g, limitations=lims, session_minutes=mins)
    week = K.build_week(profile)
    for day in week["days"]:
        strength = [i for i in day["exercises"] if SEED[i["slug"]]["category"] in ("compound", "isolation")]
        patterns = [K._guess_pattern(i["slug"], SEED[i["slug"]]) for i in strength]
        dupes = [pt for pt in FALLBACK_DUPES if patterns.count(pt) > 1]
        if dupes:
            quality.append(("повтор движения", profile, day["title"], [i["slug"] for i in strength]))
        if "+ кардио" in day["title"] and not any(SEED[i["slug"]]["category"] == "cardio" for i in day["exercises"]):
            quality.append(("кардио в названии", profile, day["title"]))
        long_rest = [(i["slug"], i["rest_sec"]) for i in strength
                     if SEED[i["slug"]]["measure_type"] in ("reps", "time") and i["rest_sec"] > 120]
        if long_rest:
            quality.append(("долгий отдых с весом тела", profile, long_rest))
        if day["session_type"] != "cardio" and len(strength) < 2:
            quality.append(("силовой день из одного упражнения", profile, day["title"], [i["slug"] for i in day["exercises"]]))
        if "pregnancy" in lims and {i["slug"] for i in day["exercises"]} & SUPINE_PRONE:
            quality.append(("беременность: лёжа", profile, {i["slug"] for i in day["exercises"]} & SUPINE_PRONE))
chk("build_week: дни без повторов движения, пустых обещаний кардио, долгого отдыха и дней из одного упражнения",
    not quality, quality[:4])
# Акцент поднимает объём до потолка приоритетной мышцы уровня (12/20/24), а не до MAV RP при приоритете (до 30).
for lv in K.LEVELS:
    for g in K.GOALS:
        t = K.volume_targets(dict(level=lv, goal=g, focus=["glutes", "arms", "chest", "back", "core"]))
        plain = K.volume_targets(dict(level=lv, goal=g))
        cap = K.LEVEL_PARAMS[lv]["weekly_sets"]["focus_max"]
        bad = {m: (t[m]["max"], plain[m]["max"]) for m in ("glutes", "biceps", "triceps", "chest", "back", "core")
               if t[m]["max"] > max(cap, plain[m]["max"]) or t[m]["target"] != t[m]["max"]}
        chk(f"volume_targets {lv}/{g}: акцент до потолка уровня", not bad, bad)

# Каждое ограничение отдельно: ни одного противопоказанного или исключённого правилами упражнения.
for lim in K.LIMITATIONS:
    rule = K.SAFETY[lim]
    for equipment, goal in itertools.product(K.EQUIPMENT_PROFILES, K.GOALS):
        profile = dict(days_per_week=4, level="intermediate", equipment=equipment, goal=goal,
                       session_minutes=60, limitations=[lim])
        slugs = [i["slug"] for d in K.build_week(profile)["days"] for i in d["exercises"]]
        bad = [s for s in slugs if lim in contra(s) or s in rule["exclude_slugs"]
               or K._guess_pattern(s, SEED[s]) in rule["avoid_patterns"]]
        chk(f"build_week {lim}/{equipment}/{goal}: нет противопоказанных", not bad, bad)

# Типичные анкеты: объём крупных мышц в диапазоне volume_targets, тяга ≥ жима, время.
TYPICAL = [
    dict(goal="muscle", level="beginner", equipment="gym", days_per_week=3, session_minutes=45),
    dict(goal="muscle", level="intermediate", equipment="gym", days_per_week=4, session_minutes=60),
    dict(goal="tone", level="beginner", equipment="home_dumbbells", days_per_week=3, session_minutes=45, focus=["glutes"]),
    dict(goal="strength", level="intermediate", equipment="gym", days_per_week=4, session_minutes=60),
    dict(goal="muscle", level="advanced", equipment="gym", days_per_week=5, session_minutes=75),
    dict(goal="loss", level="intermediate", equipment="home_dumbbells", days_per_week=4, session_minutes=60),
    dict(goal="endurance", level="intermediate", equipment="gym", days_per_week=3, session_minutes=45),
    dict(goal="muscle", level="advanced", equipment="gym", days_per_week=6, session_minutes=60),
    dict(goal="strength", level="beginner", equipment="gym", days_per_week=3, session_minutes=60),
    dict(goal="muscle", level="beginner", equipment="home_dumbbells", days_per_week=3, session_minutes=60,
         equipment_extra=["pullup_bar", "bench"]),
]
for profile in TYPICAL:
    label = f"{profile['goal']}/{profile['level']}/{profile['equipment']}/{profile['days_per_week']}×{profile['session_minutes']}"
    week = K.build_week(profile)
    volume = K.weekly_sets_by_muscle(week)
    targets = K.volume_targets(profile)
    out = {m: (volume[m], targets[m]["min"], targets[m]["max"]) for m in K.MAJOR_MUSCLES
           if not targets[m]["min"] <= volume[m] <= targets[m]["max"]}
    chk(f"build_week {label}: объём крупных мышц в диапазоне", not out, out)
    pull, push = week_pull_push(week["days"])
    chk(f"build_week {label}: тяга ≥ жим", pull >= push > 0, (pull, push))
    chk(f"build_week {label}: укладывается в {profile['session_minutes']} мин",
        not day_over_time(week["days"], profile["session_minutes"]))
    chk(f"build_week {label}: нет замечаний high", not [i for i in K.audit_week(week, profile) if i["severity"] == "high"])
    chk(f"build_week {label}: неделя проходит normalize_program без замен",
        not T.normalize_program({"week_template": {"days": week["days"]}}, list(SEED.values()),
                                days_per_week=profile["days_per_week"], weeks=6)["unmatched"])

# Неполный профиль и EN-заголовки.
week_none = K.build_week(None, None, "en")
chk("build_week(None): осторожная схема, дни с упражнениями",
    len(week_none["days"]) == K.DEFAULT_PROFILE["days_per_week"] and all(d["exercises"] for d in week_none["days"]))
chk("build_week EN: название схемы по-английски", week_none["title"] == K.SPLITS_BY_ID[week_none["split_id"]]["name_en"])


# =========================================================================== #
#  4. audit_week: неделя без тяг, изоляция раньше базового, противопоказания
# =========================================================================== #
def item(slug, sets=3, reps=(8, 12), rest=90):
    return {"slug": slug, "sets": sets, "reps_min": reps[0], "reps_max": reps[1], "time_sec": None, "rest_sec": rest}

def day(index, *items):
    return {"day_index": index, "duration_min": 60, "exercises": list(items),
            "warmup": [{"slug": K.WARMUP_SLUG, "time_sec": 300}], "cooldown": [{"slug": K.COOLDOWN_SLUG, "time_sec": 300}]}

GYM = dict(goal="muscle", level="intermediate", equipment="gym", days_per_week=2, session_minutes=60)
no_pull = [day(1, item("bb_bench_press", 4), item("db_shoulder_press"), item("leg_press")),
           day(2, item("db_incline_bench_press"), item("hack_squat"), item("leg_curl"))]
codes = {(i["code"], i["severity"]) for i in K.audit_week(no_pull, GYM)}
chk("audit: нет вертикальной тяги", ("no_vertical_pull", "medium") in codes, codes)
chk("audit: нет горизонтальной тяги", ("no_horizontal_pull", "medium") in codes, codes)
chk("audit: тяги меньше половины жимов → high", ("pull_push_ratio", "high") in codes, codes)
chk("audit: спина без подходов → volume_missing", ("volume_missing", "high") in codes, codes)

iso_first = [day(1, item("db_curl"), item("bb_bench_press"), item("lat_pulldown"), item("leg_press")),
             day(2, item("seated_cable_row"), item("hack_squat"), item("db_incline_bench_press"))]
issues = K.audit_week(iso_first, GYM)
chk("audit: изоляция раньше базового", any(i["code"] == "isolation_before_compound" and i.get("day_index") == 1 for i in issues),
    [i["code"] for i in issues])
chk("audit: день с базовыми первыми не помечен", not any(i["code"] == "isolation_before_compound" and i.get("day_index") == 2 for i in issues))
chk("audit: кор и икры перед базовым — не ошибка",
    not any(i["code"] == "isolation_before_compound"
            for i in K.audit_week([day(1, item("plank"), item("bb_bench_press"), item("seated_cable_row"))], GYM)))

knee = dict(GYM, limitations=["knee"])
issues = K.audit_week([day(1, item("bb_back_squat"), item("lat_pulldown"))], knee)
chk("audit: противопоказанное при колене → high", any(i["code"] == "contraindicated" and i["severity"] == "high"
                                                        and i.get("slug") == "bb_back_squat" for i in issues))
home = dict(GYM, equipment="home_dumbbells")
issues = K.audit_week([day(1, item("lat_pulldown"), item("db_bench_press"))], home)
chk("audit: нет оборудования", any(i["code"] == "equipment_unavailable" and i.get("slug") == "lat_pulldown" for i in issues))
chk("audit: неизвестный slug", any(i["code"] == "unknown_slug" for i in K.audit_week([day(1, item("zzz_nope"))], GYM)))
issues = K.audit_week([day(1, item("db_row", reps=(4, 6)), item("db_bench_press", reps=(4, 6)))],
                      dict(GYM, limitations=["heart_bp"]))
chk("audit: давление — мало повторов → intensity_limit", any(i["code"] == "intensity_limit" for i in issues))


# =========================================================================== #
#  5. prompt_brief и method_tip
# =========================================================================== #
BRIEF_PROFILE = dict(goal="muscle", level="beginner", equipment="gym", days_per_week=3, session_minutes=45,
                     program_weeks=6, limitations=["knee"], focus=["back"])
ru = K.prompt_brief(BRIEF_PROFILE, "ru")
en = K.prompt_brief(BRIEF_PROFILE, "en")
chk("prompt_brief RU: непустой и ≤3500", 0 < len(ru) <= K.PROMPT_BRIEF_LIMIT, len(ru))
chk("prompt_brief EN: непустой и ≤3500", 0 < len(en) <= K.PROMPT_BRIEF_LIMIT, len(en))
chk("prompt_brief RU: заголовок базы знаний", ru.startswith("БАЗА ЗНАНИЙ"), ru[:60])
chk("prompt_brief EN: заголовок базы знаний", en.startswith("KNOWLEDGE BASE"), en[:60])
chk("prompt_brief RU: схема", "Схема: " in ru and K.select_split(BRIEF_PROFILE)["name_ru"] in ru, ru[:300])
chk("prompt_brief EN: схема", "Scheme: " in en and K.select_split(BRIEF_PROFILE)["name_en"] in en, en[:300])
targets = K.volume_targets(BRIEF_PROFILE)
chk("prompt_brief RU: объём по мышцам", "Рабочих подходов на мышцу в неделю" in ru
    and f"грудь {targets['chest']['min']}–{targets['chest']['max']}" in ru, ru)
chk("prompt_brief EN: объём по мышцам", "Working sets per muscle per week" in en
    and f"chest {targets['chest']['min']}–{targets['chest']['max']}" in en, en)
chk("prompt_brief: повторы, запас и отдых цели", "в запасе" in ru and "отдых" in ru and "reps in reserve" in en and "rest" in en)
chk("prompt_brief: ограничение колени", K.SAFETY_BRIEF["knee"][0] in ru and K.SAFETY_BRIEF["knee"][1] in en)
chk("prompt_brief: паттерны слотов в примере недели", "(horizontal_pull)" in ru and "(hip_thrust)" in ru, ru)
# Границы слова: «leg_press_partial» — безопасная для колена замена и в примере быть может,
# а обычный жим ногами и присед со штангой — нет.
chk("prompt_brief: противопоказанное колену не в примере",
    not re.search(r"bb_back_squat", ru) and not re.search(r"leg_press(?!_)", ru), ru)
# Пример недели — только из переданного каталога (исключённое пользователем упражнение не предлагаем).
restricted = {s: e for s, e in SEED.items() if s not in ("seated_cable_row", "db_bench_press")}
chk("prompt_brief(catalog_map): исключённого нет в примере",
    "seated_cable_row" in ru and "seated_cable_row" not in K.prompt_brief(BRIEF_PROFILE, "ru", catalog_map=restricted))
longest = max(len(K.prompt_brief(dict(goal=g, level=lv, equipment=eq, days_per_week=d, session_minutes=90, program_weeks=8,
                                      limitations=["pregnancy", "heart_bp", "shoulder"], focus=["glutes", "arms", "core"]), lang))
              for g, lv, eq, d, lang in itertools.product(("muscle", "loss"), ("beginner", "advanced"),
                                                          ("gym", "bodyweight"), (3, 6), ("ru", "en")))
chk("prompt_brief: тяжёлые анкеты ≤3500", longest <= K.PROMPT_BRIEF_LIMIT, longest)
chk("prompt_brief(None): не падает", 0 < len(K.prompt_brief(None)) <= K.PROMPT_BRIEF_LIMIT)

# Беременность и давление: в выжимке — врач и красные флаги, в советах — строка «врач + флаги» ≤200 символов.
for lim, flag_ru, flag_en in (("pregnancy", "кровотечение", "bleeding"), ("heart_bp", "боль или давление в груди", "chest pain")):
    prof = dict(BRIEF_PROFILE, limitations=[lim])
    b_ru, b_en = K.prompt_brief(prof, "ru"), K.prompt_brief(prof, "en")
    chk(f"prompt_brief {lim}: врач и красные флаги", "врач" in b_ru and flag_ru in b_ru and "doctor" in b_en
        and flag_en in b_en, b_ru)
    tips = K.safety_tips(prof, "ru") + K.safety_tips(prof, "en")
    chk(f"safety_tips {lim}: врач, флаги, ≤200", len(tips) == 2 and all(len(t) <= 200 for t in tips)
        and "врач" in tips[0] and "doctor" in tips[1], tips)
chk("safety_tips: колени — без совета врача", K.safety_tips(BRIEF_PROFILE, "ru") == [])
chk("prompt_brief: при беременности нет совета «хип-траст 2–3 раза»",
    K.PRINCIPLES_BY_ID["glute_training"]["brief_ru"] not in K.prompt_brief(dict(BRIEF_PROFILE, goal="tone", limitations=["pregnancy"]), "ru"))

tip_ru = K.method_tip(BRIEF_PROFILE, "ru")
tip_en = K.method_tip(BRIEF_PROFILE, "en")
chk("method_tip RU: схема и объём", tip_ru.startswith("Схема «") and "рабочих подходов" in tip_ru, tip_ru)
chk("method_tip EN: схема и объём", "working sets per muscle" in tip_en, tip_en)


# =========================================================================== #
#  6. periodization_for
# =========================================================================== #
for level, weeks in itertools.product(K.LEVELS, K.PROGRAM_WEEKS):
    plan = K.periodization_for({"level": level, "program_weeks": weeks})
    phases = [p["phase"] for p in plan]
    label = f"{level}/{weeks}"
    chk(f"periodization_for {label}: недели 1..N", [p["week"] for p in plan] == list(range(1, weeks + 1)), plan)
    chk(f"periodization_for {label}: формат нормализатора",
        T._normalize_periodization(plan, weeks) == plan, plan)
    if level == "beginner" and weeks < 8:
        chk(f"periodization_for {label}: новичку без плановой разгрузки", "deload" not in phases, phases)
    else:
        chk(f"periodization_for {label}: разгрузка в конце", phases[-1] == "deload", phases)
    chk(f"periodization_for {label}: разгрузка 85% и −1 подход",
        all((p["weight_pct"], p["sets_delta"]) == (85, -1) for p in plan if p["phase"] == "deload")
        and all((p["weight_pct"], p["sets_delta"]) == (100, 0) for p in plan if p["phase"] != "deload"))
    chk(f"periodization_for {label}: проходит проверку генерации",
        T._periodization_problem(plan, plan, K._norm_profile({"level": level, "program_weeks": weeks})) is None)


# =========================================================================== #
#  7. generate_program: база знаний → ИИ (подменён) → аудит
# =========================================================================== #
ROWS = [dict(entry, id=index + 1) for index, entry in enumerate(SEED.values())]
ID_BY_SLUG = {row["slug"]: row["id"] for row in ROWS}
captured = {}
def fake(payload):
    def run(system_prompt, user_prompt, log_tag, max_tokens=None):
        captured.update(system=system_prompt, user=user_prompt, tag=log_tag)
        return json.loads(json.dumps(payload)), {"raw": "{...}", "finish_reason": "stop"}
    return run

def ex(slug, sets=3, reps=(8, 12), rest=90, **extra):
    return dict({"slug": slug, "sets": sets, "reps_min": reps[0], "reps_max": reps[1], "rest_sec": rest}, **extra)

P1 = dict(goal="muscle", level="intermediate", equipment="gym", equipment_extra=[], days_per_week=4,
          preferred_weekdays=[0, 1, 3, 4], session_minutes=60, program_weeks=6, limitations=["hip"], focus=[])
P1_TEXT = L.catalog_for_prompt(ROWS, P1)
AI_P1 = {
    "title": "Верх/Низ — 6 недель", "split_type": "upper_lower", "summary": "Четыре дня верх/низ.",
    "week_template": {"days": [
        # Изоляция раньше базового → перестановка.
        {"day_index": 1, "title": "Верх A", "exercises": [ex("db_curl", 2, (10, 12)), ex("bb_bench_press", 4, (6, 8), 150),
                                                           ex("lat_pulldown", 3)]},
        # Становая с пола при тазобедренных (SAFETY.exclude) → замена на наклон.
        {"day_index": 2, "title": "Низ A", "exercises": [ex("bb_deadlift", 3, (5, 6), 180), ex("leg_press", 3, (8, 12), 120)]},
        {"day_index": 3, "title": "Верх B", "exercises": [ex("cable_crossover", 2, (12, 15), 60), ex("db_shoulder_press", 3)]},
        {"day_index": 4, "title": "Низ B", "exercises": [ex("hip_thrust", 3), ex("leg_curl", 2, (10, 15), 60)]},
    ]},
    # «Каждая 4-я неделя и последняя — разгрузка», остальное «база»: нет роста и пика → замена.
    "periodization": [{"week": 1, "phase": "base"}, {"week": 4, "phase": "deload", "weight_pct": 85, "sets_delta": -1},
                      {"week": 6, "phase": "deload", "weight_pct": 85, "sets_delta": -1}],
    "tips": ["Спи 8 часов", K.method_tip(P1, "ru")],
}
with mock.patch.object(ai_service, "_run_text_completion", fake(AI_P1)):
    prog = T.generate_program(P1, {"weight": 80}, P1_TEXT, "ru", catalog_map=ROWS)
user = captured.get("user", "")
entries = T._knowledge_entries(T._Catalog(ROWS))
pick = T._prompt_entries(entries, P1_TEXT)
brief = K.prompt_brief(P1, "ru", catalog_map=pick)
chk("generate: tag trainer_program", captured.get("tag") == "trainer_program")
chk("generate: блок базы знаний в user_prompt целиком", brief and brief in user, user[:300])
chk("generate: база знаний перед каталогом", 0 <= user.find("БАЗА ЗНАНИЙ") < user.find("КАТАЛОГ ("),
    (user.find("БАЗА ЗНАНИЙ"), user.find("КАТАЛОГ (")))
chk("generate: каталог промпта как прежде", P1_TEXT in user)
chk("generate: правило базы знаний в system", "БАЗА ЗНАНИЙ" in captured.get("system", "")
    and "паттерны" in captured.get("system", ""), captured.get("system", "")[:400])
chk("generate: пример недели из каталога промпта",
    all(line.split(" | ")[0] in P1_TEXT for line in P1_TEXT.splitlines())
    and not any(slug in brief for slug in ("bb_deadlift", "sumo_deadlift_db", "good_morning")), brief)

days = prog["week_template"]["days"]
fix_types = [f["type"] for f in prog["knowledge"]["fixes"]]
slugs1 = [e["slug"] for e in days[0]["exercises"]]
chk("generate: ai_model модели", prog["ai_model"] == ai_service.TEXT_MODEL, prog["ai_model"])
chk("generate: источник ai и схема базы", prog["knowledge"]["source"] == "ai"
    and prog["knowledge"]["split_id"] == K.select_split(P1)["id"], prog["knowledge"])
chk("generate: title/split/summary модели сохранены", prog["title"] == "Верх/Низ — 6 недель"
    and prog["split_type"] == "upper_lower" and prog["summary"] == "Четыре дня верх/низ.")
chk("generate: базовые переставлены раньше изоляции", slugs1.index("bb_bench_press") < slugs1.index("db_curl")
    and "order" in fix_types, slugs1)
chk("generate: порядок перенумерован", [e["order"] for e in days[0]["exercises"]] == list(range(1, len(slugs1) + 1)))
slugs2 = [e["slug"] for e in days[1]["exercises"]]
chk("generate: становая при тазобедренных заменена", "bb_deadlift" not in slugs2
    and any(f["type"] == "replace" and f["slug"] == "bb_deadlift" and f["reason"] == "not_recommended"
            for f in prog["knowledge"]["fixes"]), (slugs2, prog["knowledge"]["fixes"]))
replaced = next((e for e in days[1]["exercises"] if e["slug"] not in ("leg_press",) and K._guess_pattern(e["slug"], SEED[e["slug"]]) == "hinge"), None)
chk("generate: замена — тот же паттерн, id из каталога, подходы сохранены",
    replaced is not None and replaced["exercise_id"] == ID_BY_SLUG[replaced["slug"]] and replaced["sets"] >= 3
    and replaced["start_weight_kg"] is None, replaced)
chk("generate: после правок нет недопустимых упражнений", not safety_problems(days, P1), safety_problems(days, P1))
chk("generate: жим лёжа — вес и подсказка модели не тронуты",
    next(e for e in days[0]["exercises"] if e["slug"] == "bb_bench_press")["rest_sec"] == 150)
before_issues = K.audit_week(T.normalize_program(AI_P1, ROWS, days_per_week=4, weeks=6, limitations=["hip"],
                                                 session_minutes=60)["week_template"]["days"], P1, entries)
after_issues = K.audit_week(days, P1, entries)
serious = lambda issues: [i for i in issues if i["severity"] in ("high", "medium") and i["code"].startswith("volume")]
chk("generate: объём выправлен (замечаний по объёму меньше)", len(serious(after_issues)) < len(serious(before_issues)),
    (serious(before_issues), serious(after_issues)))
chk("generate: правки подходов/упражнений записаны", "sets" in fix_types or "add_exercise" in fix_types, fix_types)
chk("generate: правки не вывели день за время", not day_over_time(days, 60, entries), day_over_time(days, 60, entries))
chk("generate: тяга не меньше жима после правок", week_pull_push(days, entries)[0] >= week_pull_push(days, entries)[1],
    week_pull_push(days, entries))
chk("generate: периодизация без роста → по базе знаний",
    prog["periodization"] == K.periodization_for(P1) and "periodization" in fix_types, prog["periodization"])
chk("generate: первая подсказка — метод, без дубля",
    prog["tips"] == [K.method_tip(P1, "ru"), "Спи 8 часов"], prog["tips"])
expanded = L.expand_program(prog["week_template"], prog["periodization"], P1, "2026-09-14")
chk("generate: программа раскрывается 6×4", len(expanded) == 24, len(expanded))

# Второй профиль: дом, колени, осмысленная периодизация модели сохраняется, нет оборудования → замена.
P2 = dict(goal="loss", level="intermediate", equipment="home_dumbbells", equipment_extra=[], days_per_week=4,
          session_minutes=45, program_weeks=6, limitations=["knee"], focus=[])
P2_TEXT = L.catalog_for_prompt(ROWS, P2)
AI_P2 = {
    "title": "Похудение — 6 недель", "split_type": "upper_lower", "summary": "x",
    "week_template": {"days": [
        {"day_index": 1, "exercises": [ex("db_bench_press"), ex("bb_row"), ex("brisk_walk", 1, rest=60, time_sec=900)]},
        {"day_index": 2, "focus_muscles": ["quads", "glutes"], "exercises": [ex("db_hip_thrust"), ex("rdl_db"), ex("goblet_squat")]},
        {"day_index": 3, "exercises": [ex("db_shoulder_press"), ex("lat_pulldown"), ex("db_row")]},
        {"day_index": 4, "exercises": [ex("glute_bridge"), ex("single_leg_rdl"), ex("plank", time_sec=40)]},
    ]},
    "periodization": [{"week": w, "phase": ph, "weight_pct": 85 if ph == "deload" else 100, "sets_delta": -1 if ph == "deload" else 0}
                      for w, ph in enumerate(["base", "build", "build", "peak", "deload", "build"], start=1)],
    "tips": ["Шаги 8–10 тысяч"],
}
with mock.patch.object(ai_service, "_run_text_completion", fake(AI_P2)):
    prog2 = T.generate_program(P2, {}, P2_TEXT, "en", catalog_map=ROWS)
days2 = prog2["week_template"]["days"]
chk("generate EN: KNOWLEDGE BASE перед CATALOG", 0 <= captured["user"].find("KNOWLEDGE BASE") < captured["user"].find("CATALOG ("))
chk("generate EN: правило базы знаний в system", "KNOWLEDGE BASE" in captured["system"] and "patterns" in captured["system"])
chk("generate: нет оборудования → замена (штанга, блок)",
    not {"bb_row", "lat_pulldown"} & {e["slug"] for d in days2 for e in d["exercises"]}
    and {f["slug"] for f in prog2["knowledge"]["fixes"] if f["type"] == "replace"} >= {"bb_row", "lat_pulldown"},
    prog2["knowledge"]["fixes"])
# Гоблет-присед противопоказан колену: аудит заменяет его на вариант с неполной амплитудой,
# а не выбрасывает квадрицепс из недели (Powers 2014, Willy 2019 — нагрузку не убирают, а дозируют).
quad_slugs2 = {e["slug"] for d in days2 for e in d["exercises"] if SEED[e["slug"]]["muscle_group"] == "quads"}
chk("generate: колени — квадрицепс остался, но только безопасными вариантами",
    quad_slugs2 and all("knee" not in json.loads(SEED[s_]["contraindications_json"]) for s_ in quad_slugs2), quad_slugs2)
chk("generate: колени — акцент дня соответствует упражнениям",
    "glutes" in days2[1]["focus_muscles"]
    and ("quads" not in days2[1]["focus_muscles"]
         or any(SEED[e["slug"]]["muscle_group"] == "quads" for e in days2[1]["exercises"])),
    (days2[1]["focus_muscles"], [e["slug"] for e in days2[1]["exercises"]]))
chk("generate: дом/колени после правок без недопустимых", not safety_problems(days2, P2), safety_problems(days2, P2))
chk("generate: осмысленная периодизация модели сохранена",
    [p["phase"] for p in prog2["periodization"]] == ["base", "build", "build", "peak", "deload", "build"]
    and "periodization" not in [f["type"] for f in prog2["knowledge"]["fixes"]], prog2["periodization"])
chk("generate EN: метод первым", prog2["tips"][0] == K.method_tip(P2, "en") and prog2["tips"][1:] == ["Шаги 8–10 тысяч"], prog2["tips"])

# Беременность: пределы интенсивности выправляются кодом.
P3 = dict(P2, limitations=["pregnancy"], equipment="gym", goal="tone")
AI_P3 = {"week_template": {"days": [
    {"day_index": d, "exercises": [ex("seated_cable_row", 3, (6, 8), 60, rpe=9), ex("machine_chest_press", 3, (6, 8), 60, rpe=9),
                                   ex("side_plank", 3, (8, 12), 60, time_sec=60)]} for d in range(1, 5)]},
         "periodization": [], "tips": []}
with mock.patch.object(ai_service, "_run_text_completion", fake(AI_P3)):
    prog3 = T.generate_program(P3, {}, L.catalog_for_prompt(ROWS, P3), "ru", catalog_map=ROWS)
limits = K._safety_for(K._norm_profile(P3))["overrides"]
strength_items = [e for d in prog3["week_template"]["days"] for e in d["exercises"]
                  if SEED[e["slug"]]["category"] in ("compound", "isolation")]
chk("generate: беременность — повторы, RPE, отдых и удержание в пределах",
    all((e["reps_min"] is None or e["reps_min"] >= limits["min_reps"]) and (e["rpe"] or 0) <= limits["max_rpe"]
        and e["rest_sec"] >= limits["min_rest_sec"] and (e["time_sec"] or 0) <= limits["max_hold_sec"] for e in strength_items),
    [(e["slug"], e["reps_min"], e["rpe"], e["rest_sec"], e["time_sec"]) for e in strength_items][:4])
chk("generate: пустая периодизация → по базе знаний", prog3["periodization"] == K.periodization_for(P3))
# Беременность: сразу за методом — «врач + красные флаги», даже если модель советов не дала.
chk("generate: пустые советы → метод и совет врача", prog3["tips"] == [K.method_tip(P3, "ru")] + K.safety_tips(P3, "ru"),
    prog3["tips"])


# =========================================================================== #
#  8. Сбой ИИ → программа из шаблона базы знаний
# =========================================================================== #
def boom(*a, **k):
    raise AIError("AI не ответил (trainer_program)", raw="")

def check_template(name, program, profile, lang="ru", cat_text=None):
    days = program["week_template"]["days"]
    chk(f"{name}: ai_model knowledge-template", program.get("ai_model") == T.KNOWLEDGE_TEMPLATE_MODEL, program.get("ai_model"))
    chk(f"{name}: источник template", program["knowledge"]["source"] == "template", program.get("knowledge"))
    chk(f"{name}: дней = days_per_week, в каждом упражнения",
        len(days) == profile["days_per_week"] and all(d["exercises"] for d in days), len(days))
    chk(f"{name}: только упражнения библиотеки с id",
        all(e["slug"] in SEED and e["exercise_id"] == ID_BY_SLUG[e["slug"]] for d in days for e in d["exercises"]))
    chk(f"{name}: без недопустимых упражнений", not safety_problems(days, profile), safety_problems(days, profile))
    if cat_text is not None:
        allowed = {line.split(" | ")[0] for line in cat_text.splitlines()}
        chk(f"{name}: только из каталога промпта", all(e["slug"] in allowed for d in days for e in d["exercises"]))
    chk(f"{name}: укладывается во время", not day_over_time(days, profile["session_minutes"]))
    chk(f"{name}: тяга ≥ жим", week_pull_push(days)[0] >= week_pull_push(days)[1], week_pull_push(days))
    chk(f"{name}: периодизация базы знаний", program["periodization"] == K.periodization_for(profile))
    chk(f"{name}: метод первой подсказкой", program["tips"][0] == K.method_tip(profile, lang) and len(program["tips"]) <= T.MAX_TIPS,
        program["tips"])
    chk(f"{name}: название и схема", program["title"] and program["split_type"] in T.SPLIT_TYPES and program["summary"],
        (program["title"], program["split_type"]))
    chk(f"{name}: раскрывается по неделям",
        len(L.expand_program(program["week_template"], program["periodization"], profile, "2026-09-14"))
        == profile["days_per_week"] * profile["program_weeks"])

with mock.patch.object(ai_service, "_run_text_completion", boom):
    tpl = T.generate_program(P1, {}, P1_TEXT, "ru", catalog_map=ROWS)
check_template("шаблон при AIError", tpl, P1, cat_text=P1_TEXT)
chk("шаблон: заголовок с неделями", tpl["title"].endswith("6 недель"), tpl["title"])
chk("шаблон: причина сбоя записана", "AI не ответил" in tpl["knowledge"]["reason"], tpl["knowledge"])

with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: ({}, {})):
    tpl2 = T.generate_program(P2, {}, P2_TEXT, "en", catalog_map=ROWS)
check_template("шаблон при пустом ответе (EN)", tpl2, P2, lang="en", cat_text=P2_TEXT)
chk("шаблон EN: заголовок и советы по-английски", tpl2["title"].endswith("6 weeks") and "Pain:" in " ".join(tpl2["tips"]),
    (tpl2["title"], tpl2["tips"]))

with mock.patch.object(ai_service, "_run_text_completion", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("нет сети"))):
    tpl3 = T.generate_program(P3, {}, L.catalog_for_prompt(ROWS, P3), "ru", catalog_map=ROWS)
check_template("шаблон при сетевой ошибке", tpl3, P3)
chk("шаблон при беременности: второй совет — врач и красные флаги", tpl3["tips"][1:2] == K.safety_tips(P3, "ru"), tpl3["tips"])

# Исключённое пользователем упражнение не попадает ни в пример недели, ни в шаблон.
excluded_slug = "seated_cable_row"
text_excl = L.catalog_for_prompt(ROWS, P1, excluded_ids=[ID_BY_SLUG[excluded_slug]])
with mock.patch.object(ai_service, "_run_text_completion", boom):
    tpl4 = T.generate_program(P1, {}, text_excl, "ru", catalog_map=ROWS)
chk("исключённое упражнение не в шаблоне", excluded_slug not in {e["slug"] for d in tpl4["week_template"]["days"] for e in d["exercises"]})
with mock.patch.object(ai_service, "_run_text_completion", fake(AI_P1)):
    T.generate_program(P1, {}, text_excl, "ru", catalog_map=ROWS)
chk("исключённое упражнение не в примере недели", excluded_slug not in captured["user"])

# Шаблон тоже не собрался → AIError (маршрут ответит 502).
def expect_aierror(name, fn):
    try:
        fn()
    except AIError:
        return
    except Exception as exc:  # noqa: BLE001
        fails.append(f"{name}: ожидали AIError, получили {type(exc).__name__}: {exc}"); return
    fails.append(f"{name}: AIError не выброшен")
with mock.patch.object(ai_service, "_run_text_completion", boom), \
        mock.patch.object(K, "build_week", side_effect=RuntimeError("сломано")):
    expect_aierror("шаблон не собрался → AIError", lambda: T.generate_program(P1, {}, P1_TEXT, "ru", catalog_map=ROWS))

# Шаблон на сетке анкет: безопасность, время, баланс тяг и жимов после правок аудита.
for d, lv, eq, g, lim, mins in itertools.product((2, 4, 6), ("beginner", "advanced"), K.EQUIPMENT_PROFILES,
                                                ("muscle", "loss", "strength"), ([], ["lower_back"]), (30, 60)):
    profile = dict(days_per_week=d, level=lv, equipment=eq, goal=g, limitations=lim, session_minutes=mins, program_weeks=6)
    text = L.catalog_for_prompt(ROWS, profile)
    program = T.knowledge_template_program(profile, text, "ru", catalog_map=ROWS)
    week_days = program["week_template"]["days"]
    label = f"шаблон {g}/{lv}/{eq}/{d}×{mins}/{lim}"
    chk(f"{label}: без недопустимых", not safety_problems(week_days, profile), safety_problems(week_days, profile))
    chk(f"{label}: во времени", not day_over_time(week_days, mins), day_over_time(week_days, mins))
    pull, push = week_pull_push(week_days)
    chk(f"{label}: тяга ≥ жим", pull >= push, (pull, push))
    chk(f"{label}: нет замечаний high",
        not [i for i in K.audit_week(week_days, profile, entries) if i["severity"] == "high"])


if fails:
    print("FAIL:"); [print("  -", f) for f in fails]; sys.exit(1)
print("OK: trainer_knowledge — константы = trainer_logic, slug паттернов/SAFETY в каталоге, select_split на все анкеты,")
print("    build_week безопасен и во времени, объём и тяга ≥ жим на типичных анкетах, audit_week (тяги, порядок,")
print("    противопоказания, оборудование, интенсивность), prompt_brief ru/en ≤3500, periodization_for;")
print("    generate_program: база знаний в промпте, правки аудита, метод первой подсказкой, периодизация,")
print("    сбой ИИ → программа из шаблона, шаблон не собрался → AIError")
