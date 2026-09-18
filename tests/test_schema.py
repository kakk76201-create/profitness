"""Аудит схемы: типы колонок в миграциях должны совпадать с типами моделей,
скомпилированными под диалект PostgreSQL.

Ловит класс ошибок «на SQLite работает, на PostgreSQL падает» БЕЗ живого сервера.
Именно такой баг однажды сломал регистрацию всех новых пользователей:
used_trial объявлена как Boolean, а миграция добавляла её как INTEGER.

Запуск:  .venv/Scripts/python.exe tests/test_schema.py
"""

import os
import re
import sys
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Изолированная временная БД: тест ничего не трогает в рабочей.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp, "schema.db").replace("\\", "/")
os.environ["ENABLE_SCHEDULER"] = "0"

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy import types as sa_types    # noqa: E402

from backend import database as D           # noqa: E402
from backend import models as M             # noqa: E402

PG = postgresql.dialect()

# Совместимые пары: «тип модели под PostgreSQL» -> допустимые типы в миграции.
COMPATIBLE = {
    "BOOLEAN": {"BOOLEAN"},
    "INTEGER": {"INTEGER"},
    "BIGINT": {"BIGINT", "INTEGER"},
    "FLOAT": {"REAL", "DOUBLE PRECISION", "FLOAT"},
    "VARCHAR": {"TEXT", "VARCHAR"},
    "TEXT": {"TEXT", "VARCHAR"},
    "TIMESTAMP WITHOUT TIME ZONE": {"TIMESTAMP"},
    "DATETIME": {"TIMESTAMP"},
    "BYTEA": {"BYTEA", "BLOB"},
}


def _migration_columns():
    """Считать списки колонок из run_migrations (единый источник правды — код)."""
    src = (ROOT / "backend" / "database.py").read_text(encoding="utf-8")
    pairs = []
    tables = [
        ("users", "user_columns"),
        ("diary_entries", "diary_columns"),
        ("workouts", "workout_columns"),
        ("notification_settings", "notification_settings_columns"),
    ]
    for table, list_name in tables:
        block = re.search(list_name + r"\s*=\s*\[(.*?)\n    \]", src, re.S)
        assert block, f"не найден список колонок {list_name}"
        for m in re.finditer(r'\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*([^)]+)\)', block.group(1)):
            pairs.append((table, m.group(1), m.group(2)))
    # Отдельная миграция payments.charge_id задаётся не списком.
    pairs.append(("payments", "charge_id", "TEXT"))
    return pairs


def main():
    problems = []
    tables = {t.name: t for t in M.Base.metadata.tables.values()}

    # 1) Каждая колонка миграции совместима с типом модели под PostgreSQL.
    checked = 0
    for table, name, sql_type in _migration_columns():
        t = tables.get(table)
        if t is None or name not in t.c:
            problems.append(f"{table}.{name}: нет такой колонки в моделях")
            continue
        model_pg = str(t.c[name].type.compile(dialect=PG)).upper()
        migration_type, _ = D.ddl_type_and_default(table, name, sql_type, None, is_sqlite=False)
        migration_type = migration_type.upper()
        allowed = COMPATIBLE.get(model_pg)
        checked += 1
        if allowed is None:
            problems.append(f"{table}.{name}: неизвестный тип модели {model_pg}")
        elif migration_type not in allowed:
            problems.append(
                f"{table}.{name}: модель на PG = {model_pg}, миграция = {migration_type}"
                " -> INSERT сломается"
            )

    # 2) Все Boolean-колонки моделей попадают в починку типов.
    bool_map = D._boolean_columns()
    for tname, t in tables.items():
        for c in t.c:
            if isinstance(c.type, sa_types.Boolean) and c.name not in bool_map.get(tname, ()):
                problems.append(f"{tname}.{c.name}: Boolean не покрыт починкой типов")

    # 3) DEFAULT булевых колонок берётся из модели (а не жёстко FALSE).
    for table, name, sql_type in _migration_columns():
        if name in bool_map.get(table, ()):
            _, default = D.ddl_type_and_default(table, name, sql_type, "0", is_sqlite=False)
            expected = D._bool_default_sql(table, name) or "FALSE"
            if default != expected:
                problems.append(f"{table}.{name}: DEFAULT = {default!r}, ожидалось {expected!r}")

    if problems:
        print("FAIL:")
        for p in problems:
            print("  -", p)
        return 1

    total_bools = sum(len(v) for v in bool_map.values())
    print(f"OK: {checked} колонок миграций совместимы с моделями под PostgreSQL;")
    print(f"    {total_bools} булевых колонок покрыты починкой; DEFAULT берётся из моделей")
    return 0


if __name__ == "__main__":
    sys.exit(main())
