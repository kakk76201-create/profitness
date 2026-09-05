r"""Перенос базы данных проекта с одного хостинга на другой БЕЗ потери данных.

Зачем: при переезде (смена аккаунта/провайдера) в проде остаются живые
пользователи с оплаченными подписками. Их нужно перенести один-в-один.

Почему не pg_dump: он требует установленных клиентских утилит PostgreSQL,
которых на Windows обычно нет. Этот скрипт использует уже установленные
зависимости проекта (SQLAlchemy + psycopg2) и модели самого приложения,
поэтому работает «из коробки» и создаёт схему правильных типов.

Использование (PowerShell, из корня проекта):

    .venv\Scripts\python.exe tools\db_copy.py ^
        --source "postgresql://...СТАРАЯ..." ^
        --target "postgresql://...НОВАЯ..."

Сначала прогоняется «сухой» просмотр. Чтобы реально записать — добавьте --apply.
Повторный запуск безопасен: строки с уже существующими ключами пропускаются.
"""

from __future__ import annotations

import argparse
import os
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def normalize(url: str) -> str:
    """Railway/Heroku отдают устаревшую схему postgres:// — SQLAlchemy её не понимает."""
    url = (url or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def main() -> int:
    parser = argparse.ArgumentParser(description="Копирование базы проекта между хостингами")
    parser.add_argument("--source", required=True, help="DATABASE_URL откуда копируем")
    parser.add_argument("--target", required=True, help="DATABASE_URL куда копируем")
    parser.add_argument("--apply", action="store_true",
                        help="реально записать (без флага — только показать план)")
    parser.add_argument("--batch", type=int, default=500, help="размер пачки вставки")
    args = parser.parse_args()

    source_url = normalize(args.source)
    target_url = normalize(args.target)
    if source_url == target_url:
        print("ОШИБКА: источник и приёмник совпадают.")
        return 1

    # Модели импортируем ДО создания движков: они наполняют Base.metadata.
    # DATABASE_URL здесь не важен — движки создаём вручную ниже.
    os.environ.setdefault("DATABASE_URL", "sqlite:///./_copy_tmp.db")
    os.environ.setdefault("ENABLE_SCHEDULER", "0")

    from sqlalchemy import create_engine, select, insert
    from sqlalchemy.orm import sessionmaker
    from backend.database import Base
    from backend import models  # noqa: F401 — регистрирует таблицы в Base.metadata

    src_engine = create_engine(source_url)
    dst_engine = create_engine(target_url)

    # Порядок важен: сначала родительские таблицы (users), потом зависимые.
    tables = list(Base.metadata.sorted_tables)

    print(f"Источник : {src_engine.url.render_as_string(hide_password=True)}")
    print(f"Приёмник : {dst_engine.url.render_as_string(hide_password=True)}")
    print(f"Режим    : {'ЗАПИСЬ' if args.apply else 'просмотр (--apply не указан)'}")
    print()

    # Создаём недостающие таблицы в приёмнике по моделям (правильные типы).
    if args.apply:
        Base.metadata.create_all(bind=dst_engine)
        try:
            from backend.database import _repair_boolean_columns  # noqa: F401
        except Exception:
            pass

    SrcSession = sessionmaker(bind=src_engine)
    DstSession = sessionmaker(bind=dst_engine)

    total_copied = 0
    problems: list[str] = []

    for table in tables:
        src = SrcSession()
        dst = DstSession()
        try:
            try:
                rows = [dict(r._mapping) for r in src.execute(select(table))]
            except Exception as exc:
                print(f"  {table.name:26} — пропуск (нет в источнике): {exc.__class__.__name__}")
                continue

            if not rows:
                print(f"  {table.name:26} — пусто")
                continue

            if not args.apply:
                print(f"  {table.name:26} — будет скопировано строк: {len(rows)}")
                total_copied += len(rows)
                continue

            # Уже существующие ключи пропускаем, чтобы повтор был безопасен.
            pk_cols = [c.name for c in table.primary_key.columns]
            existing = set()
            if pk_cols:
                for r in dst.execute(select(*[table.c[c] for c in pk_cols])):
                    existing.add(tuple(r))

            fresh = [r for r in rows
                     if not pk_cols or tuple(r[c] for c in pk_cols) not in existing]

            written = 0
            for i in range(0, len(fresh), args.batch):
                chunk = fresh[i:i + args.batch]
                if not chunk:
                    continue
                dst.execute(insert(table), chunk)
                dst.commit()
                written += len(chunk)

            skipped = len(rows) - len(fresh)
            note = f" (пропущено уже существующих: {skipped})" if skipped else ""
            print(f"  {table.name:26} — записано {written}{note}")
            total_copied += written

        except Exception as exc:
            dst.rollback()
            problems.append(f"{table.name}: {exc}")
            print(f"  {table.name:26} — ОШИБКА: {exc}")
        finally:
            src.close()
            dst.close()

    print()
    if problems:
        print("ЗАВЕРШЕНО С ОШИБКАМИ:")
        for p in problems:
            print("  -", p)
        return 1

    if args.apply:
        print(f"ГОТОВО. Перенесено строк: {total_copied}")
        print("Проверьте в приложении: /users в боте должен показать тех же пользователей.")
    else:
        print(f"План: будет перенесено строк: {total_copied}")
        print("Повторите команду с флагом --apply, чтобы выполнить перенос.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
