"""Общий smoke-тест: синтаксис всех модулей, безопасность миграции на СТАРОЙ
базе (данные пользователей целы) и работоспособность ключевых маршрутов.

Главное правило проекта: обновление НЕ должно терять данные пользователей —
здесь это проверяется на реальной базе со старой схемой.

Запуск:  .venv/Scripts/python.exe tests/test_smoke.py
"""

import ast
import os
import sys
import pathlib
import sqlite3
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def check_syntax():
    errors = []
    modules = sorted((ROOT / "backend").glob("*.py"))
    for path in modules:
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            errors.append(f"{path.name}: {exc}")
    return modules, errors


def build_legacy_db(path):
    """Создать БД со СТАРОЙ схемой (без колонок, добавленных позже)."""
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE users (
             telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
             photo_url TEXT, weight REAL, height REAL, age INTEGER, gender TEXT,
             activity_level REAL, daily_goal_kcal INTEGER, created_at TIMESTAMP)"""
    )
    con.execute(
        "INSERT INTO users (telegram_id, username, weight, daily_goal_kcal)"
        " VALUES (777, 'legacy', 70.5, 2100)"
    )
    con.execute(
        """CREATE TABLE diary_entries (
             id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER, date TEXT,
             meal_type TEXT, dish_name TEXT, calories INTEGER, proteins REAL,
             fats REAL, carbs REAL, created_at TIMESTAMP)"""
    )
    con.execute(
        "INSERT INTO diary_entries (telegram_id, date, meal_type, dish_name,"
        " calories, proteins, fats, carbs)"
        " VALUES (777, '2026-06-01', 'lunch', 'Плов', 600, 20, 25, 70)"
    )
    con.commit()
    con.close()


def main():
    modules, errors = check_syntax()
    if errors:
        print("FAIL synta:", *errors, sep="\n  - ")
        return 1
    print(f"1) синтаксис: OK ({len(modules)} модулей)")

    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "legacy.db")
    build_legacy_db(db_path)

    os.environ["DATABASE_URL"] = "sqlite:///" + db_path.replace("\\", "/")
    os.environ["ALLOW_INSECURE_AUTH"] = "1"
    os.environ["ENABLE_SCHEDULER"] = "0"
    os.environ["OWNER_ID"] = "0"
    os.environ["PROGRESS_PHOTOS_DIR"] = os.path.join(tmp, "photos")

    from backend import database as D  # noqa: E402
    from backend import models as M    # noqa: E402

    D.init_db()

    problems = []

    con = sqlite3.connect(db_path)
    user_row = con.execute(
        "SELECT username, weight, daily_goal_kcal FROM users WHERE telegram_id=777"
    ).fetchone()
    diary_row = con.execute(
        "SELECT dish_name, calories FROM diary_entries WHERE telegram_id=777"
    ).fetchone()
    user_cols = {r[1] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    con.close()

    if user_row != ("legacy", 70.5, 2100):
        problems.append(f"данные пользователя потеряны: {user_row}")
    if diary_row != ("Плов", 600):
        problems.append(f"данные дневника потеряны: {diary_row}")
    for col in ("used_trial", "subscription_type", "is_owner", "adaptive_enabled"):
        if col not in user_cols:
            problems.append(f"миграция не добавила users.{col}")
    if D.SCHEMA_ISSUES:
        problems.append(f"проблемы схемы: {D.SCHEMA_ISSUES}")

    session = D.SessionLocal()
    for model in (M.PendingGrant, M.Payment, M.ProGrant, M.ProgressPhoto, M.NotificationLog,
                  # AI-тренер (docs/TRAINER_SPEC.md §3): 11 новых таблиц, create_all без ALTER'ов.
                  M.TrainerExercise, M.TrainerProfile, M.TrainerProgram, M.TrainerProgramDay,
                  M.TrainerSession, M.TrainerSessionExercise, M.TrainerSetLog, M.TrainerRecord,
                  M.TrainerExerciseState, M.TrainerWeeklyReview, M.TrainerDailyTip):
        try:
            session.query(model).count()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"нет таблицы {model.__name__}: {exc}")
    session.close()

    if problems:
        print("FAIL:", *problems, sep="\n  - ")
        return 1
    print("2) миграция: OK (старые данные целы, новые колонки и таблицы добавлены)")

    from fastapi.testclient import TestClient  # noqa: E402
    from backend.main import app               # noqa: E402

    client = TestClient(app)

    def expect(name, condition, extra=""):
        if not condition:
            problems.append(f"{name} {extra}")

    expect("health", client.get("/api/health").status_code == 200)
    expect("subscription/status", client.get("/subscription/status").status_code == 200)
    expect("scans/remaining", client.get("/scans/remaining").status_code == 200)

    resp = client.post(
        "/diary/add",
        json={"date": "2026-07-01", "meal_type": "lunch", "dish_name": "Тест",
              "calories": 200, "proteins": 10, "fats": 5, "carbs": 20},
    )
    expect("diary/add", resp.status_code == 200, resp.text)
    if resp.status_code == 200:
        entry_id = resp.json()["id"]
        expect("diary PATCH",
               client.patch(f"/diary/{entry_id}", json={"calories": 150}).status_code == 200)

    expect("stats/streak", client.get("/stats/streak").status_code == 200)
    expect("food/recent", client.get("/food/recent").status_code == 200)
    expect("subscription/trial", client.post("/subscription/trial").status_code == 200)
    expect("account/data DELETE",
           client.request("DELETE", "/account/data").status_code == 200)

    if problems:
        print("FAIL:", *problems, sep="\n  - ")
        return 1

    print("3) маршруты: OK (health, статус, дневник+правка, стрик, недавние, триал, удаление)")
    print("\nSMOKE OK — регрессий не обнаружено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
