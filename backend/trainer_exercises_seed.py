"""
Встроенная библиотека упражнений AI-тренера (docs/TRAINER_SPEC.md §3, «Seed библиотеки»).

`EXERCISES` — список dict, ключи которых ровно совпадают с колонками модели
`TrainerExercise` (без technique_*): каждую строку можно вставить как
`TrainerExercise(**row)`. Списки (secondary_muscles, contraindications,
alternatives) уже сериализованы в JSON-строки, как и в БД.

Загрузчик — `backend/trainer_seed.ensure_exercises(db)`: идемпотентный upsert
по `slug`; он обновляет имена/поля, но НЕ трогает `technique_json`.

Коды (см. ТЗ §3):
  muscle_group: chest|back|shoulders|biceps|triceps|quads|hamstrings|glutes|
                calves|core|full_body|cardio|mobility
  equipment:    barbell|dumbbell|machine|cable|bodyweight|band|kettlebell|
                pullup_bar|bench|cardio_machine|none
  category:     compound|isolation|cardio|mobility|stretch
  measure_type: reps_weight|reps|time|distance
  contraindications: knee|lower_back|shoulder|wrist|neck|hip|pregnancy|heart_bp

Обязательные записи: `warmup_general_5min` и `stretch_full_body_5min` —
их бэкенд подставляет, если ИИ не дал разминку/заминку.
"""

from __future__ import annotations

import json

# Растяжки и подвижность хранятся под muscle_group="mobility" (категория
# stretch/mobility), чтобы не смешиваться с рабочими сетами по группам мышц.


def _ex(
    slug: str,
    name_ru: str,
    name_en: str,
    muscle_group: str,
    equipment: str,
    category: str,
    measure_type: str,
    difficulty: int = 1,
    secondary: list[str] | None = None,
    unilateral: bool = False,
    contra: list[str] | None = None,
    alternatives: list[str] | None = None,
    next_slug: str | None = None,
) -> dict:
    """Собрать строку библиотеки в виде dict с ключами-колонками TrainerExercise."""
    return {
        "slug": slug,
        "name_ru": name_ru,
        "name_en": name_en,
        "muscle_group": muscle_group,
        "secondary_muscles_json": json.dumps(secondary or [], ensure_ascii=False),
        "equipment": equipment,
        "category": category,
        "measure_type": measure_type,
        "difficulty": int(difficulty),
        "is_unilateral": bool(unilateral),
        "contraindications_json": json.dumps(contra or [], ensure_ascii=False),
        "alternatives_json": json.dumps(alternatives or [], ensure_ascii=False),
        "progression_next_slug": next_slug,
        "created_by_ai": False,
        "is_active": True,
    }


EXERCISES: list[dict] = [
    # ------------------------------------------------------------------ #
    #  Грудь
    # ------------------------------------------------------------------ #
    _ex("bb_bench_press", "Жим штанги лёжа", "Barbell Bench Press", "chest", "barbell", "compound", "reps_weight", 2,
        ["triceps", "shoulders"], contra=["shoulder", "pregnancy"],
        alternatives=["db_bench_press", "machine_chest_press", "pushup"]),
    _ex("bb_incline_bench_press", "Жим штанги на наклонной скамье", "Incline Barbell Bench Press", "chest", "barbell", "compound", "reps_weight", 2,
        ["shoulders", "triceps"], contra=["shoulder"], alternatives=["db_incline_bench_press", "machine_chest_press"]),
    _ex("db_bench_press", "Жим гантелей лёжа", "Dumbbell Bench Press", "chest", "dumbbell", "compound", "reps_weight", 1,
        ["triceps", "shoulders"], contra=["shoulder", "pregnancy"], alternatives=["bb_bench_press", "pushup", "machine_chest_press"]),
    _ex("db_incline_bench_press", "Жим гантелей на наклонной скамье", "Incline Dumbbell Press", "chest", "dumbbell", "compound", "reps_weight", 2,
        ["shoulders", "triceps"], contra=["shoulder"], alternatives=["db_bench_press", "pushup_decline"]),
    _ex("db_fly", "Разведение гантелей лёжа", "Dumbbell Fly", "chest", "dumbbell", "isolation", "reps_weight", 1,
        ["shoulders"], contra=["shoulder", "pregnancy"], alternatives=["pec_deck", "cable_crossover"]),
    _ex("machine_chest_press", "Жим в тренажёре сидя", "Machine Chest Press", "chest", "machine", "compound", "reps_weight", 1,
        ["triceps", "shoulders"], alternatives=["db_bench_press", "pushup"]),
    _ex("pec_deck", "Сведение рук в тренажёре («бабочка»)", "Pec Deck Fly", "chest", "machine", "isolation", "reps_weight", 1,
        alternatives=["db_fly", "cable_crossover"]),
    _ex("cable_crossover", "Сведение рук в кроссовере", "Cable Crossover", "chest", "cable", "isolation", "reps_weight", 1,
        ["shoulders"], alternatives=["pec_deck", "db_fly"]),
    _ex("pushup", "Отжимания от пола", "Push-Up", "chest", "bodyweight", "compound", "reps", 1,
        ["triceps", "shoulders", "core"], contra=["wrist"], alternatives=["pushup_knees", "pushup_incline", "db_bench_press"],
        next_slug="pushup_decline"),
    _ex("pushup_knees", "Отжимания с колен", "Knee Push-Up", "chest", "bodyweight", "compound", "reps", 1,
        ["triceps", "shoulders"], contra=["wrist"], alternatives=["pushup_incline"], next_slug="pushup"),
    _ex("pushup_incline", "Отжимания от возвышения", "Incline Push-Up", "chest", "bodyweight", "compound", "reps", 1,
        ["triceps", "shoulders"], contra=["wrist"], alternatives=["pushup_knees"], next_slug="pushup"),
    _ex("pushup_decline", "Отжимания с ногами на возвышении", "Decline Push-Up", "chest", "bodyweight", "compound", "reps", 2,
        ["shoulders", "triceps", "core"], contra=["wrist", "shoulder"], alternatives=["pushup"]),
    _ex("dips_chest", "Отжимания на брусьях", "Chest Dips", "chest", "bodyweight", "compound", "reps", 3,
        ["triceps", "shoulders"], contra=["shoulder"], alternatives=["pushup_decline", "db_bench_press"]),

    # ------------------------------------------------------------------ #
    #  Спина
    # ------------------------------------------------------------------ #
    _ex("bb_row", "Тяга штанги в наклоне", "Barbell Row", "back", "barbell", "compound", "reps_weight", 2,
        ["biceps", "core"], contra=["lower_back"], alternatives=["db_row", "seated_cable_row", "machine_row"]),
    _ex("db_row", "Тяга гантели в наклоне одной рукой", "One-Arm Dumbbell Row", "back", "dumbbell", "compound", "reps_weight", 1,
        ["biceps"], unilateral=True, alternatives=["seated_cable_row", "band_row"]),
    _ex("pullup", "Подтягивания", "Pull-Up", "back", "pullup_bar", "compound", "reps", 3,
        ["biceps", "core"], alternatives=["lat_pulldown", "pullup_negative", "inverted_row"]),
    _ex("chinup", "Подтягивания обратным хватом", "Chin-Up", "back", "pullup_bar", "compound", "reps", 3,
        ["biceps"], alternatives=["lat_pulldown", "pullup_negative"]),
    _ex("pullup_negative", "Негативные подтягивания", "Negative Pull-Up", "back", "pullup_bar", "compound", "reps", 2,
        ["biceps"], alternatives=["inverted_row", "lat_pulldown"], next_slug="pullup"),
    _ex("pullup_band_assisted", "Подтягивания с резинкой", "Band-Assisted Pull-Up", "back", "pullup_bar", "compound", "reps", 2,
        ["biceps"], alternatives=["pullup_negative", "lat_pulldown"], next_slug="pullup"),
    _ex("lat_pulldown", "Тяга верхнего блока", "Lat Pulldown", "back", "cable", "compound", "reps_weight", 1,
        ["biceps"], alternatives=["pullup", "band_pulldown"]),
    _ex("seated_cable_row", "Тяга горизонтального блока сидя", "Seated Cable Row", "back", "cable", "compound", "reps_weight", 1,
        ["biceps"], alternatives=["machine_row", "db_row", "band_row"]),
    _ex("machine_row", "Тяга в тренажёре", "Machine Row", "back", "machine", "compound", "reps_weight", 1,
        ["biceps"], alternatives=["seated_cable_row", "db_row"]),
    _ex("tbar_row", "Т-тяга", "T-Bar Row", "back", "machine", "compound", "reps_weight", 2,
        ["biceps", "core"], contra=["lower_back"], alternatives=["bb_row", "machine_row"]),
    _ex("inverted_row", "Австралийские подтягивания", "Inverted Row", "back", "bodyweight", "compound", "reps", 1,
        ["biceps", "core"], alternatives=["band_row", "db_row"], next_slug="pullup_negative"),
    _ex("band_row", "Тяга резинки к поясу", "Band Row", "back", "band", "compound", "reps", 1,
        ["biceps"], alternatives=["inverted_row", "db_row"]),
    _ex("band_pulldown", "Тяга резинки сверху", "Band Pulldown", "back", "band", "compound", "reps", 1,
        ["biceps"], alternatives=["band_row", "lat_pulldown"]),
    _ex("straight_arm_pulldown", "Пуловер на блоке прямыми руками", "Straight-Arm Pulldown", "back", "cable", "isolation", "reps_weight", 1,
        ["triceps"], alternatives=["lat_pulldown"]),
    _ex("hyperextension", "Гиперэкстензия", "Back Extension", "back", "bodyweight", "isolation", "reps", 1,
        ["glutes", "hamstrings"], contra=["lower_back", "pregnancy"], alternatives=["superman", "bird_dog"]),
    _ex("superman", "«Супермен» лёжа на животе", "Superman", "back", "bodyweight", "isolation", "reps", 1,
        ["glutes", "core"], contra=["lower_back", "pregnancy"], alternatives=["bird_dog"]),

    # ------------------------------------------------------------------ #
    #  Плечи
    # ------------------------------------------------------------------ #
    _ex("bb_overhead_press", "Жим штанги стоя", "Barbell Overhead Press", "shoulders", "barbell", "compound", "reps_weight", 2,
        ["triceps", "core"], contra=["shoulder", "lower_back"], alternatives=["db_shoulder_press", "machine_shoulder_press"]),
    _ex("db_shoulder_press", "Жим гантелей сидя", "Seated Dumbbell Shoulder Press", "shoulders", "dumbbell", "compound", "reps_weight", 1,
        ["triceps"], contra=["shoulder"], alternatives=["machine_shoulder_press", "pike_pushup"]),
    _ex("arnold_press", "Жим Арнольда", "Arnold Press", "shoulders", "dumbbell", "compound", "reps_weight", 2,
        ["triceps"], contra=["shoulder"], alternatives=["db_shoulder_press"]),
    _ex("machine_shoulder_press", "Жим в тренажёре на плечи", "Machine Shoulder Press", "shoulders", "machine", "compound", "reps_weight", 1,
        ["triceps"], contra=["shoulder"], alternatives=["db_shoulder_press"]),
    _ex("pike_pushup", "Отжимания «домиком»", "Pike Push-Up", "shoulders", "bodyweight", "compound", "reps", 2,
        ["triceps", "core"], contra=["shoulder", "wrist"], alternatives=["db_shoulder_press"]),
    _ex("db_lateral_raise", "Махи гантелями в стороны", "Dumbbell Lateral Raise", "shoulders", "dumbbell", "isolation", "reps_weight", 1,
        contra=["shoulder"], alternatives=["cable_lateral_raise", "band_lateral_raise"]),
    _ex("cable_lateral_raise", "Отведение руки в сторону на блоке", "Cable Lateral Raise", "shoulders", "cable", "isolation", "reps_weight", 1,
        unilateral=True, contra=["shoulder"], alternatives=["db_lateral_raise"]),
    _ex("band_lateral_raise", "Махи в стороны с резинкой", "Band Lateral Raise", "shoulders", "band", "isolation", "reps", 1,
        contra=["shoulder"], alternatives=["db_lateral_raise"]),
    _ex("db_front_raise", "Подъём гантелей перед собой", "Dumbbell Front Raise", "shoulders", "dumbbell", "isolation", "reps_weight", 1,
        contra=["shoulder"], alternatives=["db_lateral_raise"]),
    _ex("db_rear_delt_fly", "Разведение гантелей в наклоне", "Rear Delt Fly", "shoulders", "dumbbell", "isolation", "reps_weight", 1,
        ["back"], contra=["lower_back"], alternatives=["face_pull", "band_face_pull"]),
    _ex("face_pull", "Тяга к лицу на блоке", "Face Pull", "shoulders", "cable", "isolation", "reps_weight", 1,
        ["back"], alternatives=["band_face_pull", "db_rear_delt_fly"]),
    _ex("band_face_pull", "Тяга резинки к лицу", "Band Face Pull", "shoulders", "band", "isolation", "reps", 1,
        ["back"], alternatives=["db_rear_delt_fly"]),

    # ------------------------------------------------------------------ #
    #  Бицепс
    # ------------------------------------------------------------------ #
    _ex("bb_curl", "Подъём штанги на бицепс", "Barbell Curl", "biceps", "barbell", "isolation", "reps_weight", 1,
        contra=["wrist"], alternatives=["db_curl", "cable_curl"]),
    _ex("db_curl", "Подъём гантелей на бицепс", "Dumbbell Curl", "biceps", "dumbbell", "isolation", "reps_weight", 1,
        alternatives=["db_hammer_curl", "band_curl"]),
    _ex("db_hammer_curl", "«Молотки» с гантелями", "Hammer Curl", "biceps", "dumbbell", "isolation", "reps_weight", 1,
        alternatives=["db_curl"]),
    _ex("incline_db_curl", "Подъём гантелей на наклонной скамье", "Incline Dumbbell Curl", "biceps", "dumbbell", "isolation", "reps_weight", 2,
        contra=["shoulder"], alternatives=["db_curl"]),
    _ex("concentration_curl", "Концентрированный подъём на бицепс", "Concentration Curl", "biceps", "dumbbell", "isolation", "reps_weight", 1,
        unilateral=True, alternatives=["db_curl"]),
    _ex("cable_curl", "Сгибание рук на блоке", "Cable Curl", "biceps", "cable", "isolation", "reps_weight", 1,
        alternatives=["db_curl", "bb_curl"]),
    _ex("band_curl", "Сгибание рук с резинкой", "Band Curl", "biceps", "band", "isolation", "reps", 1,
        alternatives=["db_curl"]),

    # ------------------------------------------------------------------ #
    #  Трицепс
    # ------------------------------------------------------------------ #
    _ex("cable_pushdown", "Разгибание рук на блоке", "Cable Triceps Pushdown", "triceps", "cable", "isolation", "reps_weight", 1,
        alternatives=["band_pushdown", "db_overhead_extension"]),
    _ex("db_overhead_extension", "Французский жим с гантелью стоя", "Overhead Dumbbell Extension", "triceps", "dumbbell", "isolation", "reps_weight", 1,
        contra=["shoulder"], alternatives=["db_kickback", "cable_pushdown"]),
    _ex("skull_crusher", "Французский жим лёжа", "Skull Crusher", "triceps", "barbell", "isolation", "reps_weight", 2,
        contra=["wrist", "pregnancy"], alternatives=["db_overhead_extension", "cable_pushdown"]),
    _ex("db_kickback", "Разгибание руки в наклоне с гантелью", "Dumbbell Kickback", "triceps", "dumbbell", "isolation", "reps_weight", 1,
        unilateral=True, alternatives=["cable_pushdown"]),
    _ex("close_grip_bench_press", "Жим узким хватом", "Close-Grip Bench Press", "triceps", "barbell", "compound", "reps_weight", 2,
        ["chest", "shoulders"], contra=["shoulder", "wrist", "pregnancy"], alternatives=["diamond_pushup", "bench_dips"]),
    _ex("bench_dips", "Обратные отжимания от скамьи", "Bench Dips", "triceps", "bench", "compound", "reps", 1,
        ["shoulders"], contra=["shoulder"], alternatives=["diamond_pushup", "cable_pushdown"]),
    _ex("diamond_pushup", "Отжимания «алмаз»", "Diamond Push-Up", "triceps", "bodyweight", "compound", "reps", 2,
        ["chest"], contra=["wrist"], alternatives=["bench_dips", "pushup"]),
    _ex("band_pushdown", "Разгибание рук с резинкой", "Band Pushdown", "triceps", "band", "isolation", "reps", 1,
        alternatives=["cable_pushdown", "diamond_pushup"]),

    # ------------------------------------------------------------------ #
    #  Квадрицепсы
    # ------------------------------------------------------------------ #
    _ex("bb_back_squat", "Приседания со штангой", "Barbell Back Squat", "quads", "barbell", "compound", "reps_weight", 2,
        ["glutes", "hamstrings", "core"], contra=["knee", "lower_back", "heart_bp"],
        alternatives=["goblet_squat", "leg_press", "hack_squat"]),
    _ex("bb_front_squat", "Фронтальные приседания", "Front Squat", "quads", "barbell", "compound", "reps_weight", 3,
        ["glutes", "core"], contra=["knee", "wrist", "lower_back"], alternatives=["goblet_squat", "leg_press"]),
    _ex("goblet_squat", "Гоблет-присед", "Goblet Squat", "quads", "dumbbell", "compound", "reps_weight", 1,
        ["glutes", "core"], contra=["knee"], alternatives=["air_squat", "leg_press"]),
    _ex("air_squat", "Приседания без веса", "Bodyweight Squat", "quads", "bodyweight", "compound", "reps", 1,
        ["glutes"], contra=["knee"], alternatives=["wall_sit", "reverse_lunge"], next_slug="goblet_squat"),
    _ex("leg_press", "Жим ногами", "Leg Press", "quads", "machine", "compound", "reps_weight", 1,
        ["glutes"], contra=["knee"], alternatives=["goblet_squat", "hack_squat"]),
    _ex("hack_squat", "Гакк-приседания", "Hack Squat", "quads", "machine", "compound", "reps_weight", 2,
        ["glutes"], contra=["knee"], alternatives=["leg_press", "bb_back_squat"]),
    _ex("leg_extension", "Разгибание ног в тренажёре", "Leg Extension", "quads", "machine", "isolation", "reps_weight", 1,
        contra=["knee"], alternatives=["wall_sit", "air_squat"]),
    _ex("db_lunge", "Выпады с гантелями", "Dumbbell Lunge", "quads", "dumbbell", "compound", "reps_weight", 1,
        ["glutes", "hamstrings"], unilateral=True, contra=["knee"], alternatives=["reverse_lunge", "bulgarian_split_squat"]),
    _ex("walking_lunge", "Выпады в движении", "Walking Lunge", "quads", "bodyweight", "compound", "reps", 1,
        ["glutes"], unilateral=True, contra=["knee"], alternatives=["reverse_lunge", "step_up"]),
    _ex("reverse_lunge", "Обратные выпады", "Reverse Lunge", "quads", "bodyweight", "compound", "reps", 1,
        ["glutes"], unilateral=True, contra=["knee"], alternatives=["step_up", "air_squat"]),
    _ex("bulgarian_split_squat", "Болгарские выпады", "Bulgarian Split Squat", "quads", "dumbbell", "compound", "reps_weight", 2,
        ["glutes", "hamstrings"], unilateral=True, contra=["knee"], alternatives=["db_lunge", "step_up"]),
    _ex("step_up", "Зашагивания на возвышение", "Step-Up", "quads", "bench", "compound", "reps", 1,
        ["glutes"], unilateral=True, contra=["knee"], alternatives=["reverse_lunge"]),
    _ex("jump_lunge", "Выпады с прыжком", "Jump Lunge", "quads", "bodyweight", "compound", "reps", 2,
        ["glutes", "cardio"], contra=["knee", "hip", "pregnancy", "heart_bp"], alternatives=["reverse_lunge"]),
    _ex("jump_squat", "Приседания с выпрыгиванием", "Jump Squat", "quads", "bodyweight", "compound", "reps", 2,
        ["glutes", "cardio"], contra=["knee", "pregnancy", "heart_bp"], alternatives=["air_squat"]),
    _ex("wall_sit", "«Стульчик» у стены", "Wall Sit", "quads", "bodyweight", "isolation", "time", 1,
        ["glutes"], contra=["knee"], alternatives=["air_squat"]),

    # ------------------------------------------------------------------ #
    #  Задняя поверхность бедра
    # ------------------------------------------------------------------ #
    _ex("bb_deadlift", "Становая тяга", "Barbell Deadlift", "hamstrings", "barbell", "compound", "reps_weight", 3,
        ["glutes", "back", "core"], contra=["lower_back", "pregnancy", "heart_bp"],
        alternatives=["bb_rdl", "rdl_db", "kb_swing"]),
    _ex("bb_rdl", "Румынская тяга со штангой", "Romanian Deadlift", "hamstrings", "barbell", "compound", "reps_weight", 2,
        ["glutes", "back"], contra=["lower_back"], alternatives=["rdl_db", "leg_curl"]),
    _ex("rdl_db", "Румынская тяга с гантелями", "Dumbbell Romanian Deadlift", "hamstrings", "dumbbell", "compound", "reps_weight", 1,
        ["glutes", "back"], contra=["lower_back"], alternatives=["leg_curl", "single_leg_rdl", "glute_bridge"]),
    _ex("single_leg_rdl", "Румынская тяга на одной ноге", "Single-Leg Romanian Deadlift", "hamstrings", "dumbbell", "compound", "reps_weight", 2,
        ["glutes", "core"], unilateral=True, contra=["lower_back"], alternatives=["rdl_db"]),
    _ex("good_morning", "Наклоны со штангой («доброе утро»)", "Good Morning", "hamstrings", "barbell", "compound", "reps_weight", 3,
        ["glutes", "back"], contra=["lower_back", "pregnancy"], alternatives=["bb_rdl", "hyperextension"]),
    _ex("leg_curl", "Сгибание ног в тренажёре", "Leg Curl", "hamstrings", "machine", "isolation", "reps_weight", 1,
        alternatives=["rdl_db", "nordic_curl"]),
    _ex("nordic_curl", "Скандинавские сгибания", "Nordic Hamstring Curl", "hamstrings", "bodyweight", "isolation", "reps", 3,
        ["glutes"], contra=["knee"], alternatives=["leg_curl", "glute_bridge"]),
    _ex("kb_swing", "Махи гирей", "Kettlebell Swing", "hamstrings", "kettlebell", "compound", "reps_weight", 2,
        ["glutes", "core", "cardio"], contra=["lower_back", "pregnancy"], alternatives=["rdl_db", "glute_bridge"]),

    # ------------------------------------------------------------------ #
    #  Ягодицы
    # ------------------------------------------------------------------ #
    _ex("glute_bridge", "Ягодичный мост", "Glute Bridge", "glutes", "bodyweight", "compound", "reps", 1,
        ["hamstrings", "core"], contra=["pregnancy"], alternatives=["db_hip_thrust", "single_leg_glute_bridge"],
        next_slug="single_leg_glute_bridge"),
    _ex("single_leg_glute_bridge", "Ягодичный мост на одной ноге", "Single-Leg Glute Bridge", "glutes", "bodyweight", "compound", "reps", 2,
        ["hamstrings"], unilateral=True, contra=["pregnancy"], alternatives=["glute_bridge"]),
    _ex("hip_thrust", "Ягодичный мост со штангой (hip thrust)", "Barbell Hip Thrust", "glutes", "barbell", "compound", "reps_weight", 2,
        ["hamstrings"], contra=["pregnancy"], alternatives=["db_hip_thrust", "glute_bridge"]),
    _ex("db_hip_thrust", "Hip thrust с гантелью", "Dumbbell Hip Thrust", "glutes", "dumbbell", "compound", "reps_weight", 1,
        ["hamstrings"], contra=["pregnancy"], alternatives=["glute_bridge", "hip_thrust"]),
    _ex("sumo_deadlift_db", "Сумо-тяга с гантелью", "Dumbbell Sumo Deadlift", "glutes", "dumbbell", "compound", "reps_weight", 1,
        ["quads", "hamstrings"], contra=["lower_back"], alternatives=["goblet_squat", "db_hip_thrust"]),
    _ex("cable_kickback", "Отведение ноги назад на блоке", "Cable Glute Kickback", "glutes", "cable", "isolation", "reps_weight", 1,
        unilateral=True, alternatives=["band_glute_kickback", "single_leg_glute_bridge"]),
    _ex("band_glute_kickback", "Отведение ноги назад с резинкой", "Band Glute Kickback", "glutes", "band", "isolation", "reps", 1,
        unilateral=True, alternatives=["single_leg_glute_bridge"]),
    _ex("band_lateral_walk", "Шаги в сторону с резинкой", "Band Lateral Walk", "glutes", "band", "isolation", "reps", 1,
        ["quads"], alternatives=["hip_abduction_machine"]),
    _ex("hip_abduction_machine", "Разведение ног в тренажёре", "Hip Abduction Machine", "glutes", "machine", "isolation", "reps_weight", 1,
        alternatives=["band_lateral_walk"]),

    # ------------------------------------------------------------------ #
    #  Икры
    # ------------------------------------------------------------------ #
    _ex("standing_calf_raise", "Подъёмы на носки стоя", "Standing Calf Raise", "calves", "bodyweight", "isolation", "reps", 1,
        alternatives=["db_standing_calf_raise", "single_leg_calf_raise"], next_slug="single_leg_calf_raise"),
    _ex("db_standing_calf_raise", "Подъёмы на носки с гантелями", "Dumbbell Calf Raise", "calves", "dumbbell", "isolation", "reps_weight", 1,
        alternatives=["standing_calf_raise", "seated_calf_raise"]),
    _ex("seated_calf_raise", "Подъёмы на носки сидя в тренажёре", "Seated Calf Raise", "calves", "machine", "isolation", "reps_weight", 1,
        alternatives=["db_standing_calf_raise"]),
    _ex("single_leg_calf_raise", "Подъёмы на носок одной ноги", "Single-Leg Calf Raise", "calves", "bodyweight", "isolation", "reps", 2,
        unilateral=True, alternatives=["standing_calf_raise"]),

    # ------------------------------------------------------------------ #
    #  Кор
    # ------------------------------------------------------------------ #
    _ex("plank", "Планка", "Plank", "core", "bodyweight", "isolation", "time", 1,
        ["shoulders"], contra=["wrist"], alternatives=["dead_bug", "side_plank"]),
    _ex("side_plank", "Боковая планка", "Side Plank", "core", "bodyweight", "isolation", "time", 1,
        ["shoulders"], unilateral=True, contra=["shoulder"], alternatives=["plank"]),
    _ex("plank_shoulder_tap", "Планка с касанием плеч", "Plank Shoulder Tap", "core", "bodyweight", "isolation", "reps", 2,
        ["shoulders"], contra=["wrist"], alternatives=["plank"]),
    _ex("hollow_hold", "«Лодочка» (hollow hold)", "Hollow Hold", "core", "bodyweight", "isolation", "time", 2,
        contra=["lower_back", "pregnancy"], alternatives=["dead_bug"]),
    _ex("crunch", "Скручивания", "Crunch", "core", "bodyweight", "isolation", "reps", 1,
        contra=["neck", "pregnancy"], alternatives=["dead_bug", "reverse_crunch"]),
    _ex("reverse_crunch", "Обратные скручивания", "Reverse Crunch", "core", "bodyweight", "isolation", "reps", 1,
        contra=["lower_back", "pregnancy"], alternatives=["dead_bug"]),
    _ex("bicycle_crunch", "«Велосипед»", "Bicycle Crunch", "core", "bodyweight", "isolation", "reps", 1,
        contra=["neck", "lower_back", "pregnancy"], alternatives=["dead_bug", "bird_dog"]),
    _ex("dead_bug", "«Мёртвый жук»", "Dead Bug", "core", "bodyweight", "isolation", "reps", 1,
        contra=["pregnancy"], alternatives=["bird_dog", "plank"]),
    _ex("bird_dog", "«Охотничья собака» (bird dog)", "Bird Dog", "core", "bodyweight", "isolation", "reps", 1,
        ["back", "glutes"], unilateral=True, contra=["wrist"], alternatives=["dead_bug"]),
    _ex("leg_raise", "Подъём ног лёжа", "Lying Leg Raise", "core", "bodyweight", "isolation", "reps", 2,
        contra=["lower_back", "pregnancy"], alternatives=["reverse_crunch", "dead_bug"]),
    _ex("hanging_knee_raise", "Подъём коленей в висе", "Hanging Knee Raise", "core", "pullup_bar", "isolation", "reps", 2,
        contra=["shoulder"], alternatives=["leg_raise", "reverse_crunch"]),
    _ex("russian_twist", "«Русские скручивания»", "Russian Twist", "core", "bodyweight", "isolation", "reps", 1,
        contra=["lower_back", "pregnancy"], alternatives=["pallof_press", "side_plank"]),
    _ex("pallof_press", "Жим Паллофа с резинкой", "Pallof Press", "core", "band", "isolation", "reps", 1,
        unilateral=True, alternatives=["side_plank", "dead_bug"]),
    _ex("cable_crunch", "Скручивания на блоке", "Cable Crunch", "core", "cable", "isolation", "reps_weight", 1,
        contra=["neck", "lower_back"], alternatives=["crunch"]),

    # ------------------------------------------------------------------ #
    #  Всё тело
    # ------------------------------------------------------------------ #
    _ex("burpee", "Бёрпи", "Burpee", "full_body", "bodyweight", "cardio", "reps", 2,
        ["chest", "quads", "cardio"], contra=["knee", "wrist", "pregnancy", "heart_bp"],
        alternatives=["mountain_climber", "jumping_jack", "air_squat"]),
    _ex("thruster_db", "Трастеры с гантелями", "Dumbbell Thruster", "full_body", "dumbbell", "compound", "reps_weight", 2,
        ["quads", "shoulders", "glutes"], contra=["knee", "shoulder", "heart_bp"], alternatives=["goblet_squat", "db_shoulder_press"]),
    _ex("kb_clean_press", "Взятие гири на грудь с жимом", "Kettlebell Clean and Press", "full_body", "kettlebell", "compound", "reps_weight", 3,
        ["shoulders", "glutes", "core"], contra=["shoulder", "lower_back", "wrist"], alternatives=["thruster_db", "kb_swing"]),
    _ex("bear_crawl", "«Медвежья походка»", "Bear Crawl", "full_body", "bodyweight", "compound", "time", 2,
        ["core", "shoulders"], contra=["wrist", "knee"], alternatives=["mountain_climber", "plank"]),

    # ------------------------------------------------------------------ #
    #  Кардио
    # ------------------------------------------------------------------ #
    _ex("jumping_jack", "Прыжки «джампинг-джек»", "Jumping Jacks", "cardio", "bodyweight", "cardio", "reps", 1,
        contra=["knee", "pregnancy"], alternatives=["high_knees", "brisk_walk"]),
    _ex("mountain_climber", "«Скалолаз»", "Mountain Climber", "cardio", "bodyweight", "cardio", "reps", 1,
        ["core"], contra=["wrist", "pregnancy"], alternatives=["high_knees", "jumping_jack"]),
    _ex("high_knees", "Бег на месте с высоким подниманием колен", "High Knees", "cardio", "bodyweight", "cardio", "time", 1,
        contra=["knee", "heart_bp"], alternatives=["jumping_jack", "brisk_walk"]),
    _ex("skater_jump", "Прыжки «конькобежец»", "Skater Jumps", "cardio", "bodyweight", "cardio", "reps", 2,
        ["glutes"], contra=["knee", "hip", "pregnancy", "heart_bp"], alternatives=["jumping_jack"]),
    _ex("shadow_boxing", "Бой с тенью", "Shadow Boxing", "cardio", "bodyweight", "cardio", "time", 1,
        ["shoulders", "core"], alternatives=["jumping_jack", "brisk_walk"]),
    _ex("treadmill_run", "Бег на дорожке", "Treadmill Run", "cardio", "cardio_machine", "cardio", "time", 2,
        contra=["knee", "heart_bp"], alternatives=["treadmill_walk", "stationary_bike"]),
    _ex("treadmill_walk", "Ходьба на дорожке в горку", "Incline Treadmill Walk", "cardio", "cardio_machine", "cardio", "time", 1,
        alternatives=["brisk_walk", "stationary_bike"]),
    _ex("stationary_bike", "Велотренажёр", "Stationary Bike", "cardio", "cardio_machine", "cardio", "time", 1,
        alternatives=["elliptical", "treadmill_walk"]),
    _ex("elliptical", "Эллипсоид", "Elliptical Trainer", "cardio", "cardio_machine", "cardio", "time", 1,
        alternatives=["stationary_bike", "treadmill_walk"]),
    _ex("rowing_machine", "Гребной тренажёр", "Rowing Machine", "cardio", "cardio_machine", "cardio", "time", 2,
        ["back", "quads"], contra=["lower_back"], alternatives=["stationary_bike", "elliptical"]),
    _ex("stair_climber", "Степпер / лестница", "Stair Climber", "cardio", "cardio_machine", "cardio", "time", 2,
        ["glutes", "quads"], contra=["knee", "heart_bp"], alternatives=["stationary_bike"]),
    _ex("jump_rope", "Скакалка", "Jump Rope", "cardio", "none", "cardio", "time", 2,
        ["calves"], contra=["knee", "pregnancy", "heart_bp"], alternatives=["jumping_jack", "high_knees"]),
    _ex("brisk_walk", "Быстрая ходьба", "Brisk Walk", "cardio", "none", "cardio", "time", 1,
        alternatives=["treadmill_walk", "stationary_bike"]),
    _ex("jogging_outdoor", "Лёгкий бег на улице", "Outdoor Jog", "cardio", "none", "cardio", "time", 2,
        contra=["knee", "heart_bp"], alternatives=["brisk_walk"]),

    # ------------------------------------------------------------------ #
    #  Подвижность и разминка
    # ------------------------------------------------------------------ #
    _ex("warmup_general_5min", "Общая разминка 5 минут (лёгкое кардио + суставы)", "General Warm-Up (5 min)", "mobility", "none", "mobility", "time", 1),
    _ex("arm_circles", "Вращения руками", "Arm Circles", "mobility", "bodyweight", "mobility", "reps", 1, ["shoulders"]),
    _ex("shoulder_rolls", "Вращения плечами", "Shoulder Rolls", "mobility", "bodyweight", "mobility", "reps", 1, ["shoulders"]),
    _ex("band_pull_apart", "Разведение резинки перед собой", "Band Pull-Apart", "mobility", "band", "mobility", "reps", 1, ["shoulders", "back"]),
    _ex("band_dislocates", "Провороты плеч с резинкой", "Band Shoulder Dislocates", "mobility", "band", "mobility", "reps", 1, ["shoulders"]),
    _ex("wrist_circles", "Вращения запястьями", "Wrist Circles", "mobility", "bodyweight", "mobility", "reps", 1),
    _ex("neck_rolls", "Мягкие наклоны и повороты шеи", "Neck Mobility", "mobility", "bodyweight", "mobility", "reps", 1),
    _ex("ankle_circles", "Вращения стопами", "Ankle Circles", "mobility", "bodyweight", "mobility", "reps", 1, ["calves"]),
    _ex("hip_circles", "Вращения тазом", "Hip Circles", "mobility", "bodyweight", "mobility", "reps", 1, ["glutes"]),
    _ex("leg_swings", "Махи ногами вперёд-назад", "Leg Swings", "mobility", "bodyweight", "mobility", "reps", 1,
        ["hamstrings", "glutes"], unilateral=True),
    _ex("cat_cow", "«Кошка-корова»", "Cat-Cow", "mobility", "bodyweight", "mobility", "reps", 1, ["back", "core"], contra=["wrist"]),
    _ex("torso_twists", "Повороты корпуса стоя", "Standing Torso Twists", "mobility", "bodyweight", "mobility", "reps", 1, ["core"]),
    _ex("thoracic_rotation", "Ротация грудного отдела на четвереньках", "Quadruped Thoracic Rotation", "mobility", "bodyweight", "mobility", "reps", 1,
        ["back"], unilateral=True, contra=["wrist"]),
    _ex("worlds_greatest_stretch", "«Лучшая растяжка в мире» (динамическая)", "World's Greatest Stretch", "mobility", "bodyweight", "mobility", "reps", 2,
        ["glutes", "hamstrings", "back"], unilateral=True, contra=["knee", "wrist"]),
    _ex("inchworm", "«Гусеница» (inchworm)", "Inchworm", "mobility", "bodyweight", "mobility", "reps", 2,
        ["hamstrings", "core", "shoulders"], contra=["wrist"]),
    _ex("knee_hug_walk", "Ходьба с подтягиванием колена к груди", "Walking Knee Hug", "mobility", "bodyweight", "mobility", "reps", 1, ["glutes"]),
    _ex("deep_squat_hold", "Удержание глубокого приседа", "Deep Squat Hold", "mobility", "bodyweight", "mobility", "time", 1,
        ["quads", "glutes"], contra=["knee"]),

    # ------------------------------------------------------------------ #
    #  Растяжка (заминка)
    # ------------------------------------------------------------------ #
    _ex("stretch_full_body_5min", "Растяжка всего тела 5 минут", "Full-Body Stretch (5 min)", "mobility", "none", "stretch", "time", 1),
    _ex("chest_stretch", "Растяжка груди в дверном проёме", "Doorway Chest Stretch", "mobility", "bodyweight", "stretch", "time", 1, ["chest", "shoulders"]),
    _ex("shoulder_cross_stretch", "Растяжка плеча поперёк груди", "Cross-Body Shoulder Stretch", "mobility", "bodyweight", "stretch", "time", 1,
        ["shoulders"], unilateral=True),
    _ex("triceps_stretch", "Растяжка трицепса за головой", "Overhead Triceps Stretch", "mobility", "bodyweight", "stretch", "time", 1,
        ["triceps"], unilateral=True, contra=["shoulder"]),
    _ex("lat_stretch", "Растяжка широчайших у опоры", "Lat Stretch", "mobility", "bodyweight", "stretch", "time", 1, ["back"], unilateral=True),
    _ex("neck_side_stretch", "Боковая растяжка шеи", "Side Neck Stretch", "mobility", "bodyweight", "stretch", "time", 1, unilateral=True),
    _ex("child_pose", "Поза ребёнка", "Child's Pose", "mobility", "bodyweight", "stretch", "time", 1, ["back"], contra=["knee"]),
    _ex("cobra_stretch", "Поза кобры", "Cobra Stretch", "mobility", "bodyweight", "stretch", "time", 1,
        ["core"], contra=["lower_back", "pregnancy", "wrist"]),
    _ex("downward_dog", "Собака мордой вниз", "Downward Dog", "mobility", "bodyweight", "stretch", "time", 1,
        ["hamstrings", "calves", "shoulders"], contra=["wrist", "shoulder"]),
    _ex("hamstring_stretch", "Растяжка задней поверхности бедра", "Hamstring Stretch", "mobility", "bodyweight", "stretch", "time", 1,
        ["hamstrings"], unilateral=True),
    _ex("seated_forward_fold", "Наклон к ногам сидя", "Seated Forward Fold", "mobility", "bodyweight", "stretch", "time", 1,
        ["hamstrings", "back"], contra=["lower_back"]),
    _ex("quad_stretch", "Растяжка квадрицепса стоя", "Standing Quad Stretch", "mobility", "bodyweight", "stretch", "time", 1,
        ["quads"], unilateral=True, contra=["knee"]),
    _ex("hip_flexor_stretch", "Растяжка сгибателей бедра в выпаде", "Kneeling Hip Flexor Stretch", "mobility", "bodyweight", "stretch", "time", 1,
        ["quads", "glutes"], unilateral=True, contra=["knee"]),
    _ex("glute_stretch", "Растяжка ягодиц («четвёрка»)", "Figure-Four Glute Stretch", "mobility", "bodyweight", "stretch", "time", 1,
        ["glutes"], unilateral=True, contra=["hip"]),
    _ex("pigeon_stretch", "Поза голубя", "Pigeon Pose", "mobility", "bodyweight", "stretch", "time", 2,
        ["glutes"], unilateral=True, contra=["knee", "hip"]),
    _ex("butterfly_stretch", "«Бабочка» сидя", "Butterfly Stretch", "mobility", "bodyweight", "stretch", "time", 1, ["glutes"], contra=["hip"]),
    _ex("calf_stretch", "Растяжка икр у стены", "Wall Calf Stretch", "mobility", "bodyweight", "stretch", "time", 1,
        ["calves"], unilateral=True),
]

# Быстрый доступ по slug (например, для проверки альтернатив и цепочек прогрессии).
EXERCISES_BY_SLUG: dict[str, dict] = {row["slug"]: row for row in EXERCISES}

# Обязательные записи, которые бэкенд подставляет при отсутствии разминки/заминки.
REQUIRED_SLUGS: tuple[str, ...] = ("warmup_general_5min", "stretch_full_body_5min")
