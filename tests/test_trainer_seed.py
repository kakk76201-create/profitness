"""Проверка seed-библиотеки упражнений и загрузчика trainer_seed.ensure_exercises
(ТЗ §3, §8.1): идемпотентность, уникальные slug, валидные коды, обязательные
записи warmup_general_5min / stretch_full_body_5min, upsert не затирает
technique_json, покрытие групп мышц."""
import os, sys, tempfile, pathlib, json
ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "seed.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["OWNER_ID"] = "0"

from backend.database import init_db, SessionLocal
from backend import models as M
from backend import trainer_seed
from backend.trainer_exercises_seed import EXERCISES, EXERCISES_BY_SLUG, REQUIRED_SLUGS
init_db()
fails = []
def chk(n, cond, x=""):
    if not cond: fails.append(n + ("  " + str(x) if x else ""))

MG = {"chest", "back", "shoulders", "biceps", "triceps", "quads", "hamstrings", "glutes", "calves", "core", "full_body", "cardio", "mobility"}
EQ = {"barbell", "dumbbell", "machine", "cable", "bodyweight", "band", "kettlebell", "pullup_bar", "bench", "cardio_machine", "none"}
CAT = {"compound", "isolation", "cardio", "mobility", "stretch"}
MT = {"reps_weight", "reps", "time", "distance"}
CONTRA = {"knee", "lower_back", "shoulder", "wrist", "neck", "hip", "pregnancy", "heart_bp"}

# --- сам seed: размер, уникальность, ссылки ---
chk("~140 записей", 130 <= len(EXERCISES) <= 180, len(EXERCISES))
chk("slug уникальны", len(EXERCISES_BY_SLUG) == len(EXERCISES))
for row in EXERCISES:
    for alt in json.loads(row["alternatives_json"]):
        chk(f"{row['slug']}: альтернатива {alt} существует", alt in EXERCISES_BY_SLUG)
    if row["progression_next_slug"]:
        chk(f"{row['slug']}: next_slug существует", row["progression_next_slug"] in EXERCISES_BY_SLUG)
    for code in json.loads(row["contraindications_json"]):
        chk(f"{row['slug']}: код ограничения {code}", code in CONTRA)
chk("обязательные slug в seed", all(s in EXERCISES_BY_SLUG for s in REQUIRED_SLUGS))

# --- ensure_exercises: идемпотентность ---
db = SessionLocal()
r1 = trainer_seed.ensure_exercises(db)
n1 = db.query(M.TrainerExercise).count()
chk("первый запуск вставил всё", r1["inserted"] == len(EXERCISES) and n1 == len(EXERCISES), (r1, n1))
r2 = trainer_seed.ensure_exercises(db)
n2 = db.query(M.TrainerExercise).count()
chk("второй запуск: то же количество", n2 == n1, (n1, n2))
chk("второй запуск ничего не пишет", r2["inserted"] == 0 and r2["updated"] == 0, r2)

# --- строки в БД: уникальность и валидные коды ---
rows = db.query(M.TrainerExercise).all()
chk("slug уникальны в БД", len({r.slug for r in rows}) == len(rows))
for r in rows:
    chk(f"{r.slug}: muscle_group", r.muscle_group in MG, r.muscle_group)
    chk(f"{r.slug}: equipment", r.equipment in EQ, r.equipment)
    chk(f"{r.slug}: category", r.category in CAT, r.category)
    chk(f"{r.slug}: measure_type", r.measure_type in MT, r.measure_type)
    chk(f"{r.slug}: difficulty 1..3", 1 <= (r.difficulty or 0) <= 3, r.difficulty)
    chk(f"{r.slug}: имена RU/EN", bool(r.name_ru) and bool(r.name_en))
    chk(f"{r.slug}: is_active/created_by_ai", r.is_active is True and r.created_by_ai is False)
    chk(f"{r.slug}: technique_status none", r.technique_status == "none" and r.technique_json is None)
    for key in ("secondary_muscles_json", "contraindications_json", "alternatives_json"):
        chk(f"{r.slug}: {key} — JSON-список", isinstance(json.loads(getattr(r, key)), list))

# --- обязательные записи ---
for slug in REQUIRED_SLUGS:
    row = db.query(M.TrainerExercise).filter_by(slug=slug).first()
    chk(f"{slug} есть в БД", row is not None)
    if row is not None:
        chk(f"{slug}: time + none", row.measure_type == "time" and row.equipment == "none", (row.measure_type, row.equipment))

# --- покрытие по группам (ТЗ §3, «Seed библиотеки») ---
def count(pred): return sum(1 for r in rows if pred(r))
chk("грудь ≥ 10", count(lambda r: r.muscle_group == "chest") >= 10)
chk("отжимания ≥ 3 варианта", count(lambda r: r.muscle_group == "chest" and r.equipment == "bodyweight") >= 3)
chk("спина ≥ 12", count(lambda r: r.muscle_group == "back") >= 12)
chk("плечи ≥ 8", count(lambda r: r.muscle_group == "shoulders") >= 8)
chk("бицепс ≥ 6", count(lambda r: r.muscle_group == "biceps") >= 6)
chk("трицепс ≥ 6", count(lambda r: r.muscle_group == "triceps") >= 6)
chk("квадрицепсы ≥ 10", count(lambda r: r.muscle_group == "quads") >= 10)
chk("задняя/ягодицы ≥ 10", count(lambda r: r.muscle_group in ("hamstrings", "glutes")) >= 10)
chk("икры ≥ 3", count(lambda r: r.muscle_group == "calves") >= 3)
chk("кор ≥ 10", count(lambda r: r.muscle_group == "core") >= 10)
chk("full_body/cardio ≥ 12", count(lambda r: r.muscle_group in ("full_body", "cardio")) >= 12)
chk("mobility/warmup ≥ 15", count(lambda r: r.category == "mobility") >= 15)
chk("stretch ≥ 12", count(lambda r: r.category == "stretch") >= 12)
chk("кардио-тренажёры есть", count(lambda r: r.equipment == "cardio_machine") >= 4)
chk("bodyweight-варианты по всем силовым группам",
    all(count(lambda r, g=g: r.muscle_group == g and r.equipment in ("bodyweight", "none")) >= 1
        for g in ("chest", "back", "shoulders", "quads", "hamstrings", "glutes", "calves", "core")))

# --- upsert: обновляет поля, не трогает technique_json, не удаляет AI-строки ---
bench = db.query(M.TrainerExercise).filter_by(slug="db_bench_press").first()
bench.technique_json = json.dumps({"ru": {"steps": ["Лягте на скамью"], "cues": [], "mistakes": [], "breathing": "", "safety": "", "muscles_text": ""}}, ensure_ascii=False)
bench.technique_status = "ready"
bench.name_ru = "испорчено"
bench.difficulty = 3
db.add(M.TrainerExercise(slug="ai_custom_1", name_ru="Своё упражнение", name_en="Custom", muscle_group="full_body",
                         equipment="none", category="compound", measure_type="reps", created_by_ai=True))
db.commit()
r3 = trainer_seed.ensure_exercises(db)
db.expire_all()
bench = db.query(M.TrainerExercise).filter_by(slug="db_bench_press").first()
chk("upsert: имя восстановлено", bench.name_ru == EXERCISES_BY_SLUG["db_bench_press"]["name_ru"], bench.name_ru)
chk("upsert: difficulty восстановлен", bench.difficulty == EXERCISES_BY_SLUG["db_bench_press"]["difficulty"], bench.difficulty)
chk("upsert: technique_json цел", bench.technique_json and "Лягте на скамью" in bench.technique_json, bench.technique_json)
chk("upsert: technique_status цел", bench.technique_status == "ready", bench.technique_status)
chk("upsert: обновлена ровно одна строка", r3["updated"] == 1 and r3["inserted"] == 0, r3)
chk("upsert: AI-строка не удалена", db.query(M.TrainerExercise).filter_by(slug="ai_custom_1").first() is not None)
chk("upsert: количество = seed + 1", db.query(M.TrainerExercise).count() == len(EXERCISES) + 1)

# --- хелперы каталога ---
emap = trainer_seed.exercise_map(db)
chk("exercise_map по slug", "warmup_general_5min" in emap and emap["warmup_general_5min"].id > 0)
chk("load_catalog активные", len(trainer_seed.load_catalog(db)) == len(EXERCISES) + 1)
db.close()

if fails:
    print("FAIL:"); [print("  -", f) for f in fails]; sys.exit(1)
print(f"OK: seed {len(EXERCISES)} упражнений, ensure_exercises идемпотентна, slug уникальны, коды валидны,")
print("    warmup_general_5min/stretch_full_body_5min на месте, upsert не трогает technique_json")
