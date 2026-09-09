"""Локальный сервер с «заглушкой» ИИ для проверки интерфейса без ключа OpenAI.

Запуск (из корня проекта):
    .venv/Scripts/python.exe tools/dev_ai_mock.py            # порт 8000
    .venv/Scripts/python.exe tools/dev_ai_mock.py --port 8010

Что делает: поднимает backend.main:app через uvicorn, но подменяет
`ai_service._run_text_completion` — единственную точку, где приложение ходит
в OpenAI. Вместо сети возвращаются готовые JSON-ответы по тегу запроса
(программа, техника, недельный разбор, совет по питанию, подбор блюд).
Нужен только для разработки: продовые настройки, подписка и платежи не трогаются.
"""
from __future__ import annotations

import argparse
import copy
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import ai_service  # noqa: E402

PROGRAM = {
    "title": "Верх/Низ — база",
    "split_type": "upper_lower",
    "summary": "Три силовых дня: верх, низ и всё тело. Колени бережём, спину и кор грузим регулярно.",
    "week_template": {"days": [
        {"day_index": 1, "title": "Верх тела", "session_type": "strength",
         "focus_muscles": ["chest", "back"], "duration_min": 45,
         "warmup": [{"slug": "warmup_general_5min", "time_sec": 300},
                    {"slug": "arm_circles", "sets": 1, "reps": 15}],
         "exercises": [
             {"slug": "db_bench_press", "sets": 3, "reps_min": 8, "reps_max": 12, "rest_sec": 90,
              "start_weight_kg": 16, "rpe": 7, "note": "Локти под 45°, лопатки сведены"},
             {"slug": "db_row", "sets": 3, "reps_min": 8, "reps_max": 12, "rest_sec": 90, "start_weight_kg": 18},
             {"slug": "db_shoulder_press", "sets": 3, "reps_min": 8, "reps_max": 12, "rest_sec": 90, "start_weight_kg": 12},
             {"slug": "plank", "sets": 3, "time_sec": 45, "rest_sec": 60},
         ],
         "cooldown": [{"slug": "chest_stretch", "time_sec": 30}]},
        {"day_index": 2, "title": "Низ тела", "session_type": "strength",
         "focus_muscles": ["quads", "glutes"], "duration_min": 45,
         "warmup": [{"slug": "warmup_general_5min", "time_sec": 300}],
         "exercises": [
             {"slug": "goblet_squat", "sets": 3, "reps_min": 10, "reps_max": 15, "rest_sec": 90, "start_weight_kg": 16},
             {"slug": "glute_bridge", "sets": 3, "reps_min": 12, "reps_max": 15, "rest_sec": 60},
             {"slug": "plank", "sets": 2, "time_sec": 40, "rest_sec": 60},
         ],
         "cooldown": []},
        {"day_index": 3, "title": "Всё тело", "session_type": "strength",
         "focus_muscles": ["back", "shoulders"], "duration_min": 45,
         "warmup": [{"slug": "arm_circles", "sets": 1, "reps": 15}],
         "exercises": [
             {"slug": "lat_pulldown", "sets": 3, "reps_min": 8, "reps_max": 12, "rest_sec": 90, "start_weight_kg": 35},
             {"slug": "db_shoulder_press", "sets": 3, "reps_min": 8, "reps_max": 12, "rest_sec": 90, "start_weight_kg": 12},
             {"slug": "db_curl", "sets": 2, "reps_min": 10, "reps_max": 12, "rest_sec": 60, "start_weight_kg": 8},
         ],
         "cooldown": [{"slug": "chest_stretch", "time_sec": 30}]},
        {"day_index": 4, "title": "Руки и кор", "session_type": "strength",
         "focus_muscles": ["arms", "core"], "duration_min": 40,
         "warmup": [{"slug": "arm_circles", "sets": 1, "reps": 15}],
         "exercises": [
             {"slug": "db_curl", "sets": 3, "reps_min": 10, "reps_max": 12, "rest_sec": 60, "start_weight_kg": 8},
             {"slug": "plank", "sets": 3, "time_sec": 45, "rest_sec": 60},
         ],
         "cooldown": []},
        {"day_index": 5, "title": "Лёгкий день", "session_type": "strength",
         "focus_muscles": ["glutes"], "duration_min": 30,
         "warmup": [],
         "exercises": [{"slug": "glute_bridge", "sets": 3, "reps_min": 12, "reps_max": 15, "rest_sec": 60}],
         "cooldown": []},
    ]},
    "periodization": [
        {"week": 1, "phase": "base", "weight_pct": 100, "sets_delta": 0},
        {"week": 3, "phase": "build", "weight_pct": 100, "sets_delta": 1},
        {"week": 4, "phase": "deload", "weight_pct": 85, "sets_delta": -1},
        {"week": 6, "phase": "deload", "weight_pct": 85, "sets_delta": -1},
        {"week": 8, "phase": "peak", "weight_pct": 100, "sets_delta": 0},
    ],
    "tips": ["Разминка — не пропускай, это 5 минут.", "Белок 1.6–2 г на кг веса в день.", "Сон 7–9 часов важнее добавок."],
}

TECHNIQUE = {
    "steps": ["Займи исходное положение", "Опускай снаряд под контролем 2 секунды", "Выжми вверх, не выпрямляя локти до щелчка"],
    "cues": ["Лопатки сведены", "Пресс напряжён", "Дыши ровно"],
    "mistakes": ["Прогиб в пояснице", "Отбив снаряда", "Рывок в начале движения"],
    "breathing": "Вдох на опускании, выдох на усилии",
    "safety": "Не работай до полного отказа на первых неделях",
    "muscles_text": "Целевая мышца + стабилизаторы кора",
}

REVIEW = {
    "summary": "Неделя ровная: тренировки на месте, объём растёт аккуратно.",
    "wins": ["Все запланированные тренировки сделаны", "Жим гантелей — рабочий вес вырос"],
    "issues": ["Белок в среднем ниже цели", "Ноги недогружены: 8 подходов вместо 10–20"],
    "nutrition": ["Добавь 30 г белка в обед", "Вода — 2 литра в тренировочный день"],
    "changes": [
        {"type": "sets", "exercise_slug": "goblet_squat", "value": 1, "reason": "квадрицепсы ниже рекомендуемой зоны"},
        {"type": "weight_pct", "exercise_slug": "db_bench_press", "value": 5, "reason": "три подхода на верхней границе повторов"},
        {"type": "rest_sec", "exercise_slug": "db_row", "value": 75, "reason": "отдых можно сократить"},
    ],
    "next_week_focus": "Ноги и белок",
    "motivation": "Так держать — стабильность важнее рекордов.",
}

TIP = {"headline": "Сегодня тренировочный день", "calories_note": "Норма на день с учётом тренировки",
       "protein_note": "Держи белок около 150 г", "pre_workout": "За 1–2 ч: овсянка с бананом",
       "post_workout": "После: творог или курица с рисом", "hydration": "1.5–2 литра воды",
       "tips": ["Не тренируйся натощак", "Ужин — белок + овощи"]}

FOOD = {"suggestions": [
    {"dish_name": "Курица с рисом", "calories": 520, "proteins": 42.0, "fats": 9.0, "carbs": 58.0,
     "reason": "добирает белок после тренировки"},
    {"dish_name": "Творог с ягодами", "calories": 260, "proteins": 30.0, "fats": 5.0, "carbs": 20.0,
     "reason": "лёгкий белковый перекус"},
]}

RESPONSES = {
    "trainer_program": PROGRAM,
    "trainer_technique": TECHNIQUE,
    "trainer_review": REVIEW,
    "trainer_nutrition": TIP,
    "suggest_food": FOOD,
}


def fake_run(system_prompt, user_prompt, log_tag=None, max_tokens=None, **kw):
    """Возвращает готовый JSON по тегу; неизвестный тег → пустой ответ (как «ИИ промолчал»)."""
    body = RESPONSES.get(log_tag)
    kind = "canned" if body else "empty"
    print(f"[ai-mock] {log_tag}: {kind} (max_tokens={max_tokens})", flush=True)
    return (copy.deepcopy(body) if body else {}), {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Dev-сервер с заглушкой ИИ")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    ai_service._run_text_completion = fake_run  # type: ignore[attr-defined]
    print("[ai-mock] OpenAI отключён, ответы ИИ — из tools/dev_ai_mock.py", flush=True)

    import uvicorn
    from backend.main import app

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
