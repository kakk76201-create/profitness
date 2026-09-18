"""
Адаптивные калории по реальной динамике веса (Этап 3).

Идея: пользователь регулярно вносит вес. По фактическому изменению веса за
~2–3 недели и среднему дневному потреблению калорий за тот же период мы
вычисляем РЕАЛЬНОЕ поддержание (maintenance) — сколько калорий в день держит
вес стабильным именно для этого человека. Затем подгоняем дневную цель под
цель диеты (похудение / поддержание / набор).

Физика расчёта (чистая математика, без сети и без новых зависимостей):
  - 1 кг массы тела ≈ 7700 ккал (KCAL_PER_KG);
  - по точкам веса строим линейную регрессию (наименьшие квадраты) и получаем
    наклон slope (кг/день): положительный — вес растёт, отрицательный — падает;
  - если при среднем потреблении avg_intake вес меняется на slope кг/день, то
    дневной профицит/дефицит = slope * KCAL_PER_KG ккал/день, а значит
    поддержание = avg_intake - slope * KCAL_PER_KG.

Все функции с обращением к БД обёрнуты в try/except, чтобы НИКОГДА не валить
приложение и планировщик уведомлений из-за нехватки данных или ошибок.
"""

import logging
from datetime import datetime

from backend.models import DiaryEntry, WeightLog

logger = logging.getLogger(__name__)

# Энергетическая ценность одного килограмма массы тела (ккал).
KCAL_PER_KG = 7700.0

# Минимальный охват по дням (разница между первой и последней датой веса),
# при котором расчёт считается достоверным.
MIN_DAYS = 7


def linear_trend(points):
    """
    Линейная регрессия методом наименьших квадратов.

    points — список кортежей (x, y), где x — номер дня от начала наблюдений
    (целое), y — вес (кг). Возвращает (slope_kg_per_day, intercept):
      slope     — наклон линии тренда (кг/день);
      intercept — значение веса в точке x=0 (свободный член).

    При менее чем 2 точках устойчиво возвращаем slope=0 (тренда нет);
    intercept при этом равен y единственной точки либо 0.0, если точек нет.
    """
    n = len(points)
    if n < 2:
        # Недостаточно данных для оценки наклона — тренд считаем нулевым.
        intercept = float(points[0][1]) if n == 1 else 0.0
        return 0.0, intercept

    sum_x = 0.0
    sum_y = 0.0
    sum_xy = 0.0
    sum_xx = 0.0
    for x, y in points:
        x = float(x)
        y = float(y)
        sum_x += x
        sum_y += y
        sum_xy += x * y
        sum_xx += x * x

    # Знаменатель формулы наклона: n*Σx² - (Σx)².
    denom = n * sum_xx - sum_x * sum_x
    if denom == 0:
        # Все x совпадают (теоретически невозможно при разных датах) —
        # наклон не определён, возвращаем 0 и среднее по y как intercept.
        return 0.0, sum_y / n

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


def build_trend(logs):
    """
    Построить сглаженную линию тренда веса для графика.

    logs — список записей веса (объекты ORM либо dict), отсортированных по дате
    по возрастанию, у каждого есть поля date ("YYYY-MM-DD") и weight (float).

    Возвращает список словарей [{date, weight}], где weight — значение линии
    тренда (intercept + slope*x) на ту же дату, округлённое до 0.1 кг.
    x — разница в днях от первой даты наблюдений.

    При пустом или некорректном входе возвращает пустой список.
    """
    try:
        prepared = _prepare_logs(logs)
        if not prepared:
            return []

        base_date = prepared[0][0]  # дата первого наблюдения (datetime.date)
        # Точки регрессии: x — число дней от первой даты, y — вес.
        points = [((d - base_date).days, w) for d, w in prepared]
        slope, intercept = linear_trend(points)

        trend = []
        for d, _w in prepared:
            x = (d - base_date).days
            y_trend = intercept + slope * x
            trend.append({"date": d.isoformat(), "weight": round(y_trend, 1)})
        return trend
    except Exception as exc:  # noqa: BLE001 — график не должен валить запрос
        logger.warning("build_trend: не удалось построить тренд: %s", exc)
        return []


def compute_adaptive(logs, avg_intake, diet_goal, lang="ru"):
    """
    Рассчитать фактическое поддержание и новую дневную цель по динамике веса.

    Аргументы:
      logs       — список записей веса (ORM или dict) с полями date/weight;
      avg_intake — среднее дневное потребление калорий за период (ккал);
      diet_goal  — цель диеты: "loss" | "maintain" | "gain" (или None);
      lang       — язык пояснения: "ru" | "en".

    Возвращает dict. Если данных мало (меньше 2 точек веса, охват < MIN_DAYS
    дней или avg_intake <= 0) — {enough_data: False, explanation, days_used}.
    Иначе — полный результат с maintenance / new_goal / weekly_change_kg и т.п.
    """
    is_en = (lang or "ru").lower().startswith("en")

    prepared = _prepare_logs(logs)
    # Охват в днях между первой и последней записью веса.
    days_used = (prepared[-1][0] - prepared[0][0]).days if len(prepared) >= 2 else 0

    # Проверяем достаточность данных для достоверного расчёта.
    if len(prepared) < 2 or days_used < MIN_DAYS or not avg_intake or avg_intake <= 0:
        if is_en:
            explanation = (
                "Not enough data yet. Keep logging your weight and meals for "
                "about a week so I can calculate your real maintenance calories."
            )
        else:
            explanation = (
                "Пока мало данных. Добавляйте вес и еду примерно неделю, чтобы я "
                "смог вычислить ваше фактическое поддержание калорий."
            )
        return {
            "enough_data": False,
            "maintenance": None,
            "new_goal": None,
            "weekly_change_kg": None,
            "avg_intake": round(avg_intake) if avg_intake else None,
            "days_used": days_used,
            "explanation": explanation,
        }

    # Линейная регрессия по точкам веса: x — дни от первой даты, y — вес.
    base_date = prepared[0][0]
    points = [((d - base_date).days, w) for d, w in prepared]
    slope, _intercept = linear_trend(points)  # slope в кг/день

    # Изменение веса за неделю (кг/нед) — для наглядности пользователю.
    weekly_change = slope * 7.0

    # Фактическое поддержание: если при потреблении avg_intake вес растёт
    # (slope > 0), значит это потребление ВЫШЕ поддержания, поэтому вычитаем.
    maintenance = avg_intake - slope * KCAL_PER_KG

    # Защитный диапазон, чтобы выбросы в данных не давали абсурдных значений.
    maintenance = max(1000.0, min(6000.0, maintenance))

    # Подгоняем дневную цель под цель диеты пользователя.
    goal = (diet_goal or "maintain").lower()
    if goal == "loss":
        new_goal = round(maintenance * 0.85)   # дефицит ~15% для похудения
    elif goal == "gain":
        new_goal = round(maintenance * 1.10)   # профицит ~10% для набора
    else:
        new_goal = round(maintenance)          # поддержание

    maintenance_int = round(maintenance)

    if is_en:
        explanation = (
            f"Your real maintenance is about {maintenance_int} kcal. "
            f"I adjusted your daily goal to {new_goal} kcal."
        )
    else:
        explanation = (
            f"Фактическое поддержание ≈ {maintenance_int} ккал. "
            f"Скорректировал дневную цель до {new_goal} ккал."
        )

    return {
        "enough_data": True,
        "maintenance": maintenance_int,
        "new_goal": new_goal,
        "weekly_change_kg": round(weekly_change, 2),
        "avg_intake": round(avg_intake),
        "days_used": days_used,
        "explanation": explanation,
    }


def run_adaptive_recalc(db, user, lang=None, window_days=21):
    """
    Пересчитать адаптивные калории для пользователя и применить результат.

    Собирает записи веса (WeightLog) пользователя за последние window_days дней,
    считает средний дневной калораж по дневнику (DiaryEntry) за тот же период
    (среднее по дням, в которых есть записи), вызывает compute_adaptive().

    Если данных достаточно (enough_data == True) — записывает в пользователя
    calculated_maintenance, обновляет daily_goal_kcal, проставляет дату
    последнего пересчёта adaptive_last_calc (ISO сегодня) и коммитит.

    Возвращает dict результата (для эндпоинта и планировщика уведомлений).
    Всё обёрнуто в try/except: при любой ошибке возвращает enough_data=False
    с понятным пояснением и НЕ роняет вызывающий код.
    """
    lang = lang or getattr(user, "language", None) or "ru"
    is_en = str(lang).lower().startswith("en")

    try:
        telegram_id = user.telegram_id

        # Граница периода: учитываем последние window_days календарных дней.
        today = datetime.utcnow().date()
        start_date = today - _days(window_days)
        start_iso = start_date.isoformat()
        today_iso = today.isoformat()

        # --- Записи веса за период (по возрастанию даты) ---
        weight_logs = (
            db.query(WeightLog)
            .filter(
                WeightLog.telegram_id == telegram_id,
                WeightLog.date >= start_iso,
                WeightLog.date <= today_iso,
            )
            .order_by(WeightLog.date.asc())
            .all()
        )

        # --- Средний дневной калораж из дневника за тот же период ---
        diary_rows = (
            db.query(DiaryEntry.date, DiaryEntry.calories)
            .filter(
                DiaryEntry.telegram_id == telegram_id,
                DiaryEntry.date >= start_iso,
                DiaryEntry.date <= today_iso,
            )
            .all()
        )
        # Суммируем калории по дням, затем усредняем по числу дней с записями.
        per_day = {}
        for d, cal in diary_rows:
            per_day[d] = per_day.get(d, 0) + (cal or 0)
        avg_intake = (sum(per_day.values()) / len(per_day)) if per_day else 0.0

        # --- Расчёт ---
        result = compute_adaptive(
            weight_logs,
            avg_intake,
            getattr(user, "diet_goal", None),
            lang=lang,
        )

        # --- Применение к пользователю при достаточности данных ---
        if result.get("enough_data"):
            user.calculated_maintenance = result["maintenance"]
            user.daily_goal_kcal = result["new_goal"]
            user.adaptive_last_calc = today_iso
            db.commit()

        return result
    except Exception as exc:  # noqa: BLE001 — пересчёт не должен валить вызов
        logger.warning("run_adaptive_recalc: ошибка пересчёта: %s", exc)
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
        if is_en:
            explanation = "Could not recalculate adaptive calories right now."
        else:
            explanation = "Не удалось пересчитать адаптивные калории сейчас."
        return {
            "enough_data": False,
            "maintenance": None,
            "new_goal": None,
            "weekly_change_kg": None,
            "avg_intake": None,
            "days_used": 0,
            "explanation": explanation,
        }


# --------------------------------------------------------------------------- #
#  Вспомогательные функции
# --------------------------------------------------------------------------- #
def _prepare_logs(logs):
    """
    Привести записи веса к списку (date, weight) и отсортировать по дате.

    Принимает список объектов ORM (с атрибутами date/weight) либо словарей
    (с ключами "date"/"weight"). Невалидные/пустые записи пропускаются.
    Возвращает список кортежей (datetime.date, float), отсортированный по дате.
    """
    prepared = []
    for item in logs or []:
        try:
            if isinstance(item, dict):
                raw_date = item.get("date")
                raw_weight = item.get("weight")
            else:
                raw_date = getattr(item, "date", None)
                raw_weight = getattr(item, "weight", None)
            if raw_date is None or raw_weight is None:
                continue
            d = datetime.strptime(str(raw_date), "%Y-%m-%d").date()
            w = float(raw_weight)
            prepared.append((d, w))
        except Exception:  # noqa: BLE001 — битую запись просто пропускаем
            continue
    prepared.sort(key=lambda pair: pair[0])
    return prepared


def _days(n):
    """Локальный помощник: timedelta в n дней (без импорта в шапке файла)."""
    from datetime import timedelta

    return timedelta(days=n)
