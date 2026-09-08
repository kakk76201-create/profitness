"""
Идемпотентный загрузчик библиотеки упражнений AI-тренера (docs/TRAINER_SPEC.md §3).

`ensure_exercises(db)` — upsert записей `trainer_exercises_seed.EXERCISES` по
`slug`: новые строки вставляются, у существующих обновляются имена и поля
библиотеки, а `technique_json` / `technique_status` (кэш техники от ИИ) не
трогаются. Вызывается в `lifespan` после `init_db()` внутри try/except — сбой
seed не должен ронять приложение.
"""

from __future__ import annotations

import logging

from backend import models as M
from backend.trainer_exercises_seed import EXERCISES, REQUIRED_SLUGS

logger = logging.getLogger(__name__)

# Поля seed-строки, которые обновляются при каждом запуске. technique_json и
# technique_status намеренно не входят: кэш техники переживает деплой.
SEED_FIELDS: tuple[str, ...] = (
    "name_ru",
    "name_en",
    "muscle_group",
    "secondary_muscles_json",
    "equipment",
    "category",
    "measure_type",
    "difficulty",
    "is_unilateral",
    "contraindications_json",
    "alternatives_json",
    "progression_next_slug",
    "created_by_ai",
    "is_active",
)


def ensure_exercises(db) -> dict:
    """Upsert библиотеки по slug. Возвращает {"inserted", "updated", "total"}.

    Один SELECT всей библиотеки (она маленькая), затем вставка отсутствующих и
    точечное обновление изменившихся полей; один commit в конце. Повторный вызов
    без изменений seed ничего не пишет.
    """
    existing = {row.slug: row for row in db.query(M.TrainerExercise).all()}
    inserted = 0
    updated = 0
    for seed in EXERCISES:
        row = existing.get(seed["slug"])
        if row is None:
            values = {"slug": seed["slug"]}
            values.update({field: seed.get(field) for field in SEED_FIELDS})
            db.add(M.TrainerExercise(**values))
            inserted += 1
            continue
        changed = False
        for field in SEED_FIELDS:
            new_value = seed.get(field)
            if getattr(row, field) != new_value:
                setattr(row, field, new_value)
                changed = True
        if changed:
            updated += 1
    if inserted or updated:
        db.commit()

    seed_slugs = {seed["slug"] for seed in EXERCISES}
    missing = [slug for slug in REQUIRED_SLUGS if slug not in seed_slugs]
    if missing:
        logger.error("Seed библиотеки тренера: нет обязательных записей %s", missing)
    logger.info(
        "Библиотека упражнений тренера: %d записей (новых %d, обновлено %d)",
        len(EXERCISES), inserted, updated,
    )
    return {"inserted": inserted, "updated": updated, "total": len(EXERCISES)}


def load_catalog(db, active_only: bool = True) -> list:
    """Все упражнения библиотеки (по умолчанию только активные), по id."""
    query = db.query(M.TrainerExercise)
    if active_only:
        query = query.filter(M.TrainerExercise.is_active.is_(True))
    return query.order_by(M.TrainerExercise.id).all()


def exercise_map(db, active_only: bool = True) -> dict:
    """{slug: TrainerExercise} — для нормализации ответа ИИ и раскрытия программы."""
    return {row.slug: row for row in load_catalog(db, active_only)}
