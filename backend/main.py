"""Точка входа FastAPI-приложения «Calorie Mini App».

Запуск из корня проекта:
    uvicorn backend.main:app

Здесь собирается весь backend: загрузка .env, CORS, инициализация БД,
все API-маршруты и раздача статики фронтенда (монтируется ПОСЛЕДНЕЙ,
чтобы API-маршруты имели приоритет).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path

# .env загружаем в самом начале, ДО чтения переменных окружения сервисами.
from dotenv import load_dotenv

load_dotenv()

# Базовая настройка логирования, чтобы INFO-логи (включая сырой ответ модели
# из ai_service) были видны в консоли uvicorn и в логах Railway.
logging.basicConfig(level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))
logger = logging.getLogger("main")

# Мониторинг ошибок: включается одной переменной SENTRY_DSN. Без неё — только
# логи Railway, которые никто не читает, пока не стало поздно.
_SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
if _SENTRY_DSN:
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            environment=os.getenv("RAILWAY_ENVIRONMENT", "local"),
            # Персональные данные (telegram_id, тексты) в Sentry не уходят.
            send_default_pii=False,
            traces_sample_rate=0.0,
        )
        logger.info("Sentry подключён")
    except Exception as exc:  # noqa: BLE001 — мониторинг не должен ронять старт
        logger.warning("Sentry не подключён: %s", exc)

# Dev-режим (ТОЛЬКО для локальной разработки): небезопасная авторизация.
# В проде эта переменная НЕ должна быть выставлена.
DEV_MODE = os.getenv("ALLOW_INSECURE_AUTH") == "1"

# Режим отладки ИИ: при включении в ответ добавляется «сырой» ответ модели и
# причина ошибки. ОТВЯЗАН от dev-режима (иначе утечка в прод) — включается
# ТОЛЬКО явной переменной DEBUG_AI=1. Никогда не включать в проде.
DEBUG_AI = os.getenv("DEBUG_AI") == "1"

# Признаки прода: Railway кладёт эти переменные в окружение контейнера.
IS_PRODUCTION = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))
if DEV_MODE and IS_PRODUCTION:
    # Не предупреждение, а отказ старта: с этой переменной ЛЮБОЙ запрос без
    # подписи Telegram получает доступ к данным dev-пользователя, а платные
    # функции — без подписки. Пусть деплой упадёт, чем тихо откроет дверь.
    raise RuntimeError(
        "ALLOW_INSECURE_AUTH=1 в проде запрещён: уберите переменную в Railway."
    )
if DEBUG_AI and IS_PRODUCTION:
    logger.error("DEBUG_AI=1 в проде: сырые ответы модели уходят клиенту — выключите.")

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from backend import (
    adaptive,
    cloudpayments,
    config,
    cycle,
    fitness,
    food_search,
    notifications,
    nutrition,
    ratelimit,
    payment_providers,
    subscription,
    telegram_bot,
    trainer,
    trainer_logic,
    trainer_seed,
    yookassa,
)
from backend.ai_service import (
    AIError,
    analyze_food_image,
    calculate_food,
    estimate_workout_calories,
    generate_meal_plan,
    generate_weekly_report,
    healthy_snacks,
    parse_food_text,
    recommend_meals,
    recommend_supplements,
    recovery_advice,
    regenerate_meal_item,
    suggest_food,
    suggest_supplements,
    transcribe_audio,
)
from backend.auth import get_current_user
from backend.database import SessionLocal, get_db, init_db
from backend.models import (
    CycleLog,
    DiaryEntry,
    FavoriteFood,
    MealTemplate,
    NotificationLog,
    Payment,
    ProgressPhoto,
    NotificationSettings,
    Supplement,
    SupplementReminder,
    SupplementReminderItem,
    TrainerDailyTip,
    TrainerExerciseState,
    TrainerProfile,
    TrainerProgram,
    TrainerProgramDay,
    TrainerRecord,
    TrainerSession,
    TrainerSessionExercise,
    TrainerSetLog,
    TrainerWeeklyReview,
    TrainingReminder,
    User,
    WeightLog,
    Workout,
)
from backend.schemas import (
    AdaptiveResultOut,
    AnalyzeOut,
    CopyYesterdayIn,
    CycleLogIn,
    CycleStatusOut,
    DiaryDayOut,
    DiaryEntryIn,
    DiaryEntryOut,
    DiaryEntryPatchIn,
    FoodCalculateIn,
    FoodCalculateOut,
    FoodSearchItem,
    FoodSearchOut,
    FoodSuggestIn,
    FoodSuggestOut,
    GoalCalcIn,
    GoalCalcOut,
    HealthySnacksOut,
    HistoryDay,
    HistoryOut,
    ManualFoodIn,
    MealPlanDay,
    MealPlanDish,
    MealPlanIn,
    MealPlanOut,
    MealsOut,
    NotificationSettingsIn,
    NotificationSettingsOut,
    ProfileIn,
    ProfileOut,
    ProgressListOut,
    ProgressPhotoOut,
    RecentFoodOut,
    RecentFoodsOut,
    RecommendIn,
    RecommendItem,
    RecommendOut,
    RegenerateItemIn,
    RegenerateItemOut,
    RecoveryAdviceIn,
    RecoveryAdviceOut,
    ScansRemainingOut,
    StreakOut,
    SubscriptionStatusOut,
    SupplementIn,
    SupplementListOut,
    SupplementOut,
    SupplementRecommendIn,
    SupplementRecommendOut,
    SupplementReminderIn,
    SupplementReminderItemOut,
    SupplementReminderOut,
    SupplementRemindersOut,
    SupplementSuggestItem,
    SupplementSuggestOut,
    TemplateApplyIn,
    TemplateItem,
    TemplateListOut,
    TemplateOut,
    TemplateSaveIn,
    TrainingReminderIn,
    TrainingReminderOut,
    TrainingRemindersOut,
    VoiceFoodOut,
    VoiceItemOut,
    WeeklyReportOut,
    WeightAddIn,
    WeightHistoryOut,
    WeightLogOut,
    WeightPoint,
    WorkoutDayOut,
    WorkoutEstimateIn,
    WorkoutEstimateOut,
    WorkoutIn,
    WorkoutOut,
    YesterdayItem,
    YesterdayOut,
    YookassaCreateIn,
    YookassaCreateOut,
)

# Допустимые типы приёмов пищи (порядок важен для группировки/вывода).
MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")

# Максимальный размер загружаемого фото (8 МБ) — защита от перерасхода памяти.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# Максимальный размер загружаемого голосового сообщения (20 МБ) — защита памяти.
MAX_AUDIO_BYTES = 20 * 1024 * 1024


# --------------------------------------------------------------------------- #
#  Хелперы для напоминаний (дни недели <-> CSV, мягкая валидация времени)
# --------------------------------------------------------------------------- #
def _weekdays_to_csv(weekdays: list[int]) -> str:
    """Превратить список дней недели (Пн=0..Вс=6) в CSV-строку "0,2,4".

    Оставляем только корректные значения 0..6, убираем дубликаты и сортируем,
    чтобы строка в БД была предсказуемой.
    """
    cleaned = sorted({int(d) for d in weekdays if 0 <= int(d) <= 6})
    return ",".join(str(d) for d in cleaned)


def _csv_to_weekdays(csv: str | None) -> list[int]:
    """Разобрать CSV-строку дней недели обратно в список int (Пн=0..Вс=6).

    Пустые/битые элементы молча пропускаем, чтобы кривые данные в БД не валили
    выдачу всего списка напоминаний.
    """
    if not csv:
        return []
    result: list[int] = []
    for part in csv.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 6:
            result.append(value)
    return result


def _normalize_time(value: str | None) -> str:
    """Мягко привести время к виду "HH:MM".

    Принимаем "9:5" / "09:05" и т.п.; при явной ошибке формата возвращаем 400.
    Значения вне диапазона (часы 0..23, минуты 0..59) считаем ошибкой клиента.
    """
    if value is None:
        raise HTTPException(status_code=400, detail="Не указано время (HH:MM)")
    raw = str(value).strip()
    parts = raw.split(":")
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="Некорректное время, нужен формат HH:MM")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректное время, нужен формат HH:MM")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise HTTPException(status_code=400, detail="Некорректное время, нужен формат HH:MM")
    return f"{hour:02d}:{minute:02d}"


# Жизненный цикл приложения: создаём таблицы БД при старте и запускаем
# планировщик уведомлений. Используем lifespan вместо устаревшего
# @app.on_event("startup").
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Создаём/мигрируем БД. Это критично — без таблиц приложение не работает.
    init_db()

    # Библиотека упражнений AI-тренера: идемпотентный upsert по slug.
    # НЕОБЯЗАТЕЛЬНАЯ часть — сбой seed не должен мешать старту API.
    try:
        with SessionLocal() as db:
            trainer_seed.ensure_exercises(db)
    except Exception as exc:  # noqa: BLE001 — seed не должен валить старт
        logger.exception("Не удалось загрузить библиотеку упражнений тренера: %s", exc)

    # Планировщик пуш-уведомлений — НЕОБЯЗАТЕЛЬНАЯ часть. Любая его ошибка
    # не должна мешать старту API, поэтому оборачиваем в try/except.
    scheduler = None
    try:
        scheduler = notifications.start_scheduler()
        if scheduler is not None:
            logger.info("Планировщик уведомлений запущен")
        else:
            logger.info("Планировщик уведомлений отключён (нет токена/ENABLE_SCHEDULER=0)")
    except Exception as exc:  # noqa: BLE001 — планировщик не должен валить старт
        logger.warning("Не удалось запустить планировщик уведомлений: %s", exc)
        scheduler = None

    try:
        yield
    finally:
        # На выходе аккуратно останавливаем планировщик, если он был запущен.
        if scheduler is not None:
            try:
                notifications.stop_scheduler(scheduler)
                logger.info("Планировщик уведомлений остановлен")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ошибка при остановке планировщика: %s", exc)


app = FastAPI(title="Calorie Mini App", lifespan=lifespan)

# CORS. Фронтенд раздаётся тем же бэкендом (запросы same-origin), поэтому
# CORS не нужен вовсе: без заголовков браузер просто не пустит чужой сайт к
# API. Включается ТОЛЬКО явным списком в CORS_ORIGINS (через запятую) — для
# случая, когда фронт хостится отдельно.
_cors_env = os.getenv("CORS_ORIGINS", "").strip()
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip() and o.strip() != "*"]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
elif _cors_env == "*":
    logger.warning("CORS_ORIGINS=* игнорируется: перечислите домены явно или оставьте пусто")


# --------------------------------------------------------------------------- #
#  Заголовки безопасности
# --------------------------------------------------------------------------- #
# CSP отключается переменной SECURITY_CSP=0 — запасной выход, если новый
# платёжный виджет или клиент Telegram окажется под запретом политики.
SECURITY_CSP_ENABLED = os.getenv("SECURITY_CSP", "1") != "0"
_CSP_CACHE: dict = {}


def _inline_script_hashes() -> list[str]:
    """sha256-хеши инлайн-скриптов index.html для CSP.

    index.html статичен, поэтому хеши считаем один раз при первом запросе.
    Хеш вместо 'unsafe-inline' означает: даже если через XSS в страницу
    попадёт <script>, браузер его не выполнит.
    """
    try:
        html = (frontend_dir / "index.html").read_text(encoding="utf-8")
    except OSError:
        return []
    hashes = []
    for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S | re.I):
        body = m.group(1)
        if body.strip():
            digest = hashlib.sha256(body.encode("utf-8")).digest()
            hashes.append("'sha256-" + base64.b64encode(digest).decode("ascii") + "'")
    return hashes


def build_csp() -> str:
    """Собрать Content-Security-Policy под текущую конфигурацию.

    frame-ancestors — самое важное: мини-приложение можно встраивать только
    клиентам Telegram (web.telegram.org и поддомены), чужой сайт не сможет
    показать его в iframe и накладывать поверх свои элементы. Хосты
    платёжного виджета добавляются только когда провайдер включён.
    """
    if "value" in _CSP_CACHE:
        return _CSP_CACHE["value"]
    extra = [h for h in os.getenv("CSP_EXTRA_HOSTS", "").split() if h]
    hashes = _inline_script_hashes()
    script = ["'self'", "https://telegram.org"] + (hashes or ["'unsafe-inline'"])
    connect = ["'self'"]
    frame = ["'self'"]
    provider = getattr(config, "card_provider", lambda: "")()
    if provider == "cloudpayments":
        script.append("https://widget.cloudpayments.ru")
        connect.append("https://*.cloudpayments.ru")
        frame.append("https://*.cloudpayments.ru")
    parts = [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "form-action 'self'",
        "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org",
        "script-src " + " ".join(script + extra),
        # style-атрибуты (фон геройских блоков) требуют 'unsafe-inline';
        # стили не исполняют код, риск минимальный.
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' data: https://fonts.gstatic.com",
        # Аватары Telegram живут на разных CDN-хостах с редиректами.
        "img-src 'self' data: blob: https:",
        "media-src 'self' blob:",
        "connect-src " + " ".join(connect + extra),
        "frame-src " + " ".join(frame + extra),
    ]
    value = "; ".join(parts)
    _CSP_CACHE["value"] = value
    return value


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Стандартный набор защитных заголовков на каждый ответ, включая статику."""
    response = await call_next(request)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault(
        "Permissions-Policy",
        "camera=(self), microphone=(self), geolocation=(), payment=(self)",
    )
    if IS_PRODUCTION:
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if SECURITY_CSP_ENABLED:
        h.setdefault("Content-Security-Policy", build_csp())
    return response


# --------------------------------------------------------------------------- #
#  Служебный маршрут (без авторизации)
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Health-check: проверяем и доступность БД (чтобы Railway видел сбои)."""
    from sqlalchemy import text as _sql_text

    try:
        db.execute(_sql_text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        logger.error("health: БД недоступна: %s", exc)
        raise HTTPException(status_code=503, detail="db unavailable")

    # Сообщаем, к какой БД реально подключены: тихий фолбэк на файловый SQLite
    # в облаке означает потерю данных при каждом деплое — это должно быть видно.
    from backend import database as _db

    out = {"status": "ok", "db": _db.engine.url.get_backend_name()}
    if _db.IS_EPHEMERAL_SQLITE:
        out["warning"] = "DATABASE_URL не задан — используется эфемерный SQLite"
    # Схема не в порядке = регистрация новых пользователей может не работать.
    # Отдаём 503, чтобы деплой не считался успешным при мёртвой регистрации.
    if _db.SCHEMA_ISSUES:
        logger.error("health: проблемы схемы БД: %s", _db.SCHEMA_ISSUES)
        raise HTTPException(
            status_code=503,
            detail={"error": "schema mismatch", "issues": _db.SCHEMA_ISSUES},
        )
    return out


# --------------------------------------------------------------------------- #
#  Авторизация / профиль
# --------------------------------------------------------------------------- #
@app.post("/auth/verify", response_model=ProfileOut)
def auth_verify(user: User = Depends(get_current_user)) -> User:
    """Проверка initData и upsert пользователя.

    Зависимость get_current_user уже выполняет валидацию и создание/обновление
    записи пользователя, поэтому достаточно вернуть текущего пользователя.
    """
    return user


@app.get("/profile", response_model=ProfileOut)
def get_profile(user: User = Depends(get_current_user)) -> User:
    """Вернуть профиль текущего пользователя."""
    return user


@app.post("/profile", response_model=ProfileOut)
def update_profile(
    data: ProfileIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Обновить профиль пользователя.

    Обновляются только переданные поля. Если цель по калориям не задана
    напрямую, но известны вес, рост, возраст и пол — рассчитываем её по
    формуле Миффлина — Сан Жеора.
    """
    payload = data.model_dump(exclude_unset=True)

    # Применяем только реально переданные поля профиля.
    # Помимо базовых параметров теперь поддерживаем цель диеты, целевые БЖУ
    # и язык интерфейса/ИИ (language: "ru"|"en") — пользователь может сменить язык.
    for field in (
        "weight",
        "height",
        "age",
        "gender",
        "activity_level",
        "daily_goal_kcal",
        "diet_goal",
        "target_proteins",
        "target_fats",
        "target_carbs",
        "language",
        # Этап 3: флаг адаптивных калорий (вкл/выкл авто-пересчёт по динамике веса).
        "adaptive_enabled",
    ):
        if field in payload:
            setattr(user, field, payload[field])

    # Автоматический расчёт дневной нормы калорий, если она не указана явно.
    if (
        user.daily_goal_kcal is None
        and user.weight is not None
        and user.height is not None
        and user.age is not None
        and user.gender is not None
    ):
        # Формула Миффлина — Сан Жеора (BMR).
        bmr = 10 * user.weight + 6.25 * user.height - 5 * user.age
        bmr += 5 if user.gender == "male" else -161
        # Умножаем BMR на коэффициент активности (по умолчанию 1.375).
        activity = user.activity_level if user.activity_level else 1.375
        user.daily_goal_kcal = round(bmr * activity)

    # #7: единый ввод веса. Если в запросе пришёл валидный положительный вес —
    # дублируем его в замер веса за сегодня (upsert по user+date, как /weight/add),
    # чтобы тренд и адаптивный расчёт питались тем же единственным вводом.
    if "weight" in payload:
        try:
            weight_val = float(payload["weight"])
        except (TypeError, ValueError):
            weight_val = None
        if weight_val is not None and weight_val > 0:
            today_iso = date_cls.today().isoformat()
            existing = (
                db.query(WeightLog)
                .filter(
                    WeightLog.telegram_id == user.telegram_id,
                    WeightLog.date == today_iso,
                )
                .first()
            )
            if existing is not None:
                # Запись за сегодня уже есть — обновляем вес.
                existing.weight = weight_val
            else:
                # Замера за сегодня ещё не было — создаём новый.
                db.add(
                    WeightLog(
                        telegram_id=user.telegram_id,
                        date=today_iso,
                        weight=weight_val,
                    )
                )

    db.commit()
    db.refresh(user)
    return user


# --------------------------------------------------------------------------- #
#  Распознавание еды
# --------------------------------------------------------------------------- #
@app.post("/food/analyze", response_model=AnalyzeOut)
async def food_analyze(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalyzeOut:
    """Принять фото, проанализировать его ИИ и вернуть КБЖУ блюда.

    Для бесплатных пользователей действует дневной лимит сканирований:
    ПЕРЕД обращением к ИИ проверяем доступность скана (assert_scan_available
    бросит 402, если лимит исчерпан), а ПОСЛЕ успешного результата фиксируем
    использование (record_scan). Для премиум-пользователей лимита нет.
    """
    # Проверяем лимит ДО любой тяжёлой работы: не читаем файл впустую и не
    # дёргаем ИИ, если бесплатный лимит на сегодня уже исчерпан (бросит 402).
    subscription.assert_scan_available(db, user)
    # Дополнительный анти-абьюз лимит частоты AI-вызовов.
    ratelimit.enforce_ai(user.telegram_id)

    # Читаем не больше лимита + 1 байт, чтобы поймать превышение размера.
    image_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
    if not image_bytes:
        # Пустой файл — некорректная загрузка.
        raise HTTPException(status_code=400, detail="Пустой файл изображения")
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        # Слишком большой файл — не тратим память и не дёргаем ИИ впустую.
        raise HTTPException(status_code=413, detail="Файл слишком большой (макс. 8 МБ)")

    mime = file.content_type or "image/jpeg"
    # Язык распознавания берём из профиля пользователя ("ru" по умолчанию).
    lang = user.language or "ru"
    try:
        # Синхронный вызов OpenAI выносим в threadpool, чтобы не блокировать
        # event loop (иначе один скан морозит запросы всех пользователей).
        result = await run_in_threadpool(analyze_food_image, image_bytes, mime, lang)
    except AIError as exc:
        # Сырой ответ модели всегда пишем в лог сервера (виден в логах Railway).
        logger.warning(
            "food/analyze: %s | finish=%s refusal=%s raw=%s",
            exc, exc.finish_reason, exc.refusal, (exc.raw or "")[:600],
        )
        # Пользователю — понятный текст; при DEBUG_AI добавляем причину и сырой ответ.
        detail = "Не удалось распознать еду на фото. Попробуйте кадр чётче и при хорошем освещении."
        if DEBUG_AI:
            detail = f"{exc} | finish={exc.finish_reason} | raw={(exc.raw or 'пусто')[:1500]}"
        raise HTTPException(status_code=502, detail=detail)
    except RuntimeError as exc:
        # Прочие сбои сервиса (нет ключа, недоступность OpenAI и т.п.).
        logger.warning("food/analyze: %s", exc)
        detail = "Сервис распознавания временно недоступен. Попробуйте позже."
        if DEBUG_AI:
            detail = str(exc)
        raise HTTPException(status_code=502, detail=detail)

    # Скан успешно выполнен — фиксируем использование (для премиум ничего не делает).
    subscription.record_scan(db, user)

    return AnalyzeOut(
        dish_name=result["dish_name"],
        calories=result["calories"],
        proteins=result["proteins"],
        fats=result["fats"],
        carbs=result["carbs"],
        note=result["note"],
        # Новые поля оценки порции — берём из результата, если модель их вернула.
        weight_grams=result.get("weight_grams"),
        confidence=result.get("confidence"),
        # debug отдаём только в режиме отладки, чтобы не светить «сырой» ответ в проде.
        debug=result.get("_debug") if DEBUG_AI else None,
    )


# --------------------------------------------------------------------------- #
#  Голосовой ввод еды (Этап 2): речь -> текст (Whisper) -> разбор блюд (GPT)
# --------------------------------------------------------------------------- #
@app.post("/food/voice", response_model=VoiceFoodOut)
async def food_voice(
    file: UploadFile = File(...),
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> VoiceFoodOut:
    """Принять голосовое сообщение, распознать речь и разобрать блюда с КБЖУ.

    Премиум-функция (доступ только через require_premium, иначе 402).
    Шаги: читаем аудио с лимитом размера -> Whisper переводит речь в текст ->
    GPT извлекает блюда с количеством, оценивает КБЖУ и определяет приём пищи.
    Язык распознавания/разбора берём из профиля пользователя ("ru" по умолчанию).
    Запись в дневник здесь НЕ создаём — клиент сам решает, что добавить.
    """
    ratelimit.enforce_ai(user.telegram_id)
    # Читаем не больше лимита + 1 байт, чтобы поймать превышение размера.
    audio_bytes = await file.read(MAX_AUDIO_BYTES + 1)
    if not audio_bytes:
        # Пустой файл — некорректная загрузка.
        raise HTTPException(status_code=400, detail="Пустой аудиофайл")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        # Слишком большой файл — не тратим память и не дёргаем ИИ впустую.
        raise HTTPException(status_code=413, detail="Аудиофайл слишком большой (макс. 20 МБ)")

    # Язык распознавания берём из профиля пользователя ("ru" по умолчанию).
    lang = user.language or "ru"
    # Имя файла нужно Whisper для определения формата; даём дефолт, если пусто.
    filename = file.filename or "audio.ogg"

    try:
        # Синхронные вызовы OpenAI — в threadpool, чтобы не блокировать event loop.
        # 1) Речь -> текст (Whisper / gpt-4o-transcribe).
        text = await run_in_threadpool(transcribe_audio, audio_bytes, filename, lang)
        # 2) Текст -> блюда с количеством, КБЖУ и определением приёма пищи (GPT).
        parsed = await run_in_threadpool(parse_food_text, text, lang)
    except AIError as exc:
        # Сырой ответ модели всегда пишем в лог сервера (виден в логах Railway).
        logger.warning(
            "food/voice: %s | finish=%s refusal=%s raw=%s",
            exc, exc.finish_reason, exc.refusal, (exc.raw or "")[:600],
        )
        # Пользователю — понятный текст; при DEBUG_AI добавляем причину и сырой ответ.
        detail = "Не удалось распознать еду из голосового сообщения. Попробуйте сказать чётче."
        if DEBUG_AI:
            detail = f"{exc} | finish={exc.finish_reason} | raw={(exc.raw or 'пусто')[:1500]}"
        raise HTTPException(status_code=502, detail=detail)
    except RuntimeError as exc:
        # Прочие сбои сервиса (нет ключа, недоступность OpenAI и т.п.).
        logger.warning("food/voice: %s", exc)
        detail = "Сервис распознавания речи временно недоступен. Попробуйте позже."
        if DEBUG_AI:
            detail = str(exc)
        raise HTTPException(status_code=502, detail=detail)

    # Собираем список блюд; «мусорные» элементы parse_food_text уже отфильтровал.
    items = [
        VoiceItemOut(
            dish_name=it["dish_name"],
            calories=it["calories"],
            proteins=it["proteins"],
            fats=it["fats"],
            carbs=it["carbs"],
            # Количество и единица измерения (parse_food_text теперь их отдаёт).
            quantity=it.get("quantity"),
            unit=it.get("unit"),
        )
        for it in parsed.get("items", [])
    ]
    return VoiceFoodOut(
        transcript=text,
        meal_type=parsed.get("meal_type"),
        items=items,
    )


# --------------------------------------------------------------------------- #
#  Дневник питания
# --------------------------------------------------------------------------- #
@app.post("/diary/add", response_model=DiaryEntryOut)
def diary_add(
    entry: DiaryEntryIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DiaryEntry:
    """Добавить запись в дневник текущего пользователя."""
    db_entry = DiaryEntry(
        telegram_id=user.telegram_id,
        date=entry.date,
        meal_type=entry.meal_type,
        dish_name=entry.dish_name,
        calories=entry.calories,
        proteins=entry.proteins,
        fats=entry.fats,
        carbs=entry.carbs,
        # Количество и единица измерения (канонический ключ) — опциональны.
        quantity=entry.quantity,
        unit=entry.unit,
    )
    db.add(db_entry)

    # Сохраняем блюдо в «недавние» (FavoriteFood), чтобы фото/голос-добавления
    # тоже попадали в быстрый повтор. Дедуп по названию делается при чтении
    # (/food/recent). Без названия — не сохраняем.
    if (entry.dish_name or "").strip():
        db.add(
            FavoriteFood(
                telegram_id=user.telegram_id,
                dish_name=entry.dish_name,
                calories=entry.calories,
                proteins=entry.proteins,
                fats=entry.fats,
                carbs=entry.carbs,
            )
        )

    db.commit()
    db.refresh(db_entry)
    return db_entry


@app.get("/diary/{date}", response_model=DiaryDayOut)
def diary_day(
    date: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DiaryDayOut:
    """Вернуть все записи за день, сгруппированные по приёмам пищи, и итоги."""
    entries = (
        db.query(DiaryEntry)
        .filter(DiaryEntry.telegram_id == user.telegram_id, DiaryEntry.date == date)
        .order_by(DiaryEntry.created_at.asc(), DiaryEntry.id.asc())
        .all()
    )

    # Группируем записи по типу приёма пищи.
    grouped: dict[str, list[DiaryEntryOut]] = {mt: [] for mt in MEAL_TYPES}
    total_calories = 0
    total_proteins = 0.0
    total_fats = 0.0
    total_carbs = 0.0

    for e in entries:
        out = DiaryEntryOut.model_validate(e)
        # Неизвестные типы относим к перекусам, чтобы запись не потерялась.
        bucket = e.meal_type if e.meal_type in grouped else "snack"
        grouped[bucket].append(out)
        total_calories += e.calories or 0
        total_proteins += e.proteins or 0.0
        total_fats += e.fats or 0.0
        total_carbs += e.carbs or 0.0

    meals = MealsOut(**grouped)

    # Сожжённые за день калории — сумма по тренировкам этой даты.
    workouts = (
        db.query(Workout)
        .filter(Workout.telegram_id == user.telegram_id, Workout.date == date)
        .all()
    )
    total_burned = sum((w.calories_burned or 0) for w in workouts)
    # Чистые калории = съедено − сожжено (может быть отрицательным).
    net_calories = total_calories - total_burned

    return DiaryDayOut(
        date=date,
        daily_goal_kcal=user.daily_goal_kcal,
        total_calories=total_calories,
        total_proteins=round(total_proteins, 1),
        total_fats=round(total_fats, 1),
        total_carbs=round(total_carbs, 1),
        total_burned=total_burned,
        net_calories=net_calories,
        meals=meals,
    )


@app.delete("/diary/{entry_id}")
def diary_delete(
    entry_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Удалить запись дневника, если она принадлежит текущему пользователю."""
    entry = db.query(DiaryEntry).filter(DiaryEntry.id == entry_id).first()
    if entry is None or entry.telegram_id != user.telegram_id:
        # Чужую или несуществующую запись прячем за 404.
        raise HTTPException(status_code=404, detail="Запись не найдена")

    db.delete(entry)
    db.commit()
    return {"ok": True}


@app.patch("/diary/{entry_id}", response_model=DiaryEntryOut)
def diary_update(
    entry_id: int,
    patch: DiaryEntryPatchIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DiaryEntry:
    """Частично отредактировать запись дневника (только свою).

    Обновляются лишь переданные поля (exclude_unset). Числовые значения
    приводятся к неотрицательным; meal_type проверяется по списку допустимых.
    """
    entry = db.query(DiaryEntry).filter(DiaryEntry.id == entry_id).first()
    if entry is None or entry.telegram_id != user.telegram_id:
        raise HTTPException(status_code=404, detail="Запись не найдена")

    data = patch.model_dump(exclude_unset=True)

    # Тип приёма пищи — только из допустимого набора.
    if "meal_type" in data:
        if data["meal_type"] not in MEAL_TYPES:
            raise HTTPException(status_code=400, detail="Некорректный приём пищи")
        entry.meal_type = data["meal_type"]

    # Название — не затираем пустым.
    if "dish_name" in data and (data["dish_name"] or "").strip():
        entry.dish_name = data["dish_name"].strip()

    # Калории — целые, неотрицательные.
    if "calories" in data and data["calories"] is not None:
        entry.calories = max(0, int(data["calories"]))

    # Макросы и количество — неотрицательные вещественные.
    for f in ("proteins", "fats", "carbs", "quantity"):
        if f in data and data[f] is not None:
            setattr(entry, f, max(0.0, float(data[f])))

    # Единица измерения (может быть сброшена в None).
    if "unit" in data:
        entry.unit = data["unit"]

    db.commit()
    db.refresh(entry)
    return entry


@app.get("/stats/streak", response_model=StreakOut)
def stats_streak(
    today: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreakOut:
    """Серия дней подряд с записями в дневнике («стрик»).

    Текущая серия считается от «сегодня» (или от вчера, если сегодня ещё нет
    записей — чтобы не сбрасывать огонёк в течение дня). «Сегодня» берётся из
    параметра ?today=YYYY-MM-DD (локальная дата клиента), иначе — серверная.
    """
    rows = (
        db.query(DiaryEntry.date)
        .filter(DiaryEntry.telegram_id == user.telegram_id)
        .distinct()
        .all()
    )
    dates = {r[0] for r in rows if r[0]}

    try:
        anchor_today = date_cls.fromisoformat(today) if today else date_cls.today()
    except (ValueError, TypeError):
        anchor_today = date_cls.today()

    today_s = anchor_today.isoformat()
    logged_today = today_s in dates

    # Опорный день для текущей серии: сегодня, либо вчера (если сегодня пусто).
    if today_s in dates:
        cur_anchor = anchor_today
    else:
        y = anchor_today - timedelta(days=1)
        cur_anchor = y if y.isoformat() in dates else None

    current = 0
    if cur_anchor is not None:
        d = cur_anchor
        while d.isoformat() in dates:
            current += 1
            d = d - timedelta(days=1)

    # Самая длинная серия за всё время.
    parsed = []
    for s in dates:
        try:
            parsed.append(date_cls.fromisoformat(s))
        except ValueError:
            continue
    parsed.sort()
    longest = 0
    run = 0
    prev = None
    for d in parsed:
        run = run + 1 if (prev is not None and (d - prev).days == 1) else 1
        prev = d
        if run > longest:
            longest = run

    return StreakOut(current=current, longest=longest, logged_today=logged_today)


# --------------------------------------------------------------------------- #
#  История
# --------------------------------------------------------------------------- #
@app.get("/history", response_model=HistoryOut)
def history(
    days: int = 30,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HistoryOut:
    """Вернуть суммарные калории по дням за последние <days> календарных дней.

    В ответ попадают только дни, по которым есть записи; список отсортирован
    по возрастанию даты.
    """
    # Защита от некорректных значений параметра.
    if days < 1:
        days = 1

    today = date_cls.today()
    start = today - timedelta(days=days - 1)
    start_str = start.isoformat()
    end_str = today.isoformat()

    rows = (
        db.query(DiaryEntry)
        .filter(
            DiaryEntry.telegram_id == user.telegram_id,
            DiaryEntry.date >= start_str,
            DiaryEntry.date <= end_str,
        )
        .all()
    )

    # Суммируем калории по каждой дате.
    totals: dict[str, int] = {}
    for r in rows:
        totals[r.date] = totals.get(r.date, 0) + (r.calories or 0)

    day_items = [
        HistoryDay(date=d, total_calories=totals[d]) for d in sorted(totals.keys())
    ]

    return HistoryOut(goal=user.daily_goal_kcal, days=day_items)


# --------------------------------------------------------------------------- #
#  Тренировки (расход калорий)
# --------------------------------------------------------------------------- #
@app.post("/workout/add", response_model=WorkoutOut)
def workout_add(
    data: WorkoutIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> Workout:
    """Добавить тренировку текущего пользователя."""
    db_workout = Workout(
        telegram_id=user.telegram_id,
        date=data.date,
        type=data.type,
        duration_min=data.duration_min,
        calories_burned=data.calories_burned,
        description=data.description,
    )
    db.add(db_workout)
    db.commit()
    db.refresh(db_workout)
    return db_workout


@app.get("/workout/{date}", response_model=WorkoutDayOut)
def workout_day(
    date: str,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> WorkoutDayOut:
    """Вернуть тренировки за дату и суммарный расход калорий."""
    workouts = (
        db.query(Workout)
        .filter(Workout.telegram_id == user.telegram_id, Workout.date == date)
        .order_by(Workout.created_at.asc(), Workout.id.asc())
        .all()
    )
    items = [WorkoutOut.model_validate(w) for w in workouts]
    total_burned = sum((w.calories_burned or 0) for w in workouts)
    return WorkoutDayOut(date=date, workouts=items, total_burned=total_burned)


@app.delete("/workout/{workout_id}")
def workout_delete(
    workout_id: int,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> dict:
    """Удалить тренировку, если она принадлежит текущему пользователю."""
    workout = db.query(Workout).filter(Workout.id == workout_id).first()
    if workout is None or workout.telegram_id != user.telegram_id:
        # Чужую или несуществующую тренировку прячем за 404.
        raise HTTPException(status_code=404, detail="Тренировка не найдена")

    db.delete(workout)
    db.commit()
    return {"ok": True}


@app.post("/workout/estimate", response_model=WorkoutEstimateOut)
def workout_estimate(
    data: WorkoutEstimateIn,
    user: User = Depends(subscription.require_premium),
) -> WorkoutEstimateOut:
    """Оценить расход калорий за тренировку по типу и длительности.

    Для типа "other" со свободным описанием расчёт ведёт ИИ по тексту
    активности (реалистичные калории + MET). Для остальных типов (и для
    "other" без описания) — по таблице MET с учётом веса пользователя (если он
    указан в профиле, иначе берётся усреднённое значение внутри fitness-модуля).
    """
    # Для произвольной активности с описанием — AI-оценка калорий по тексту.
    if data.type == "other" and (data.description or "").strip():
        ratelimit.enforce_ai(user.telegram_id)
        try:
            result = estimate_workout_calories(
                data.description,
                data.duration_min,
                user.weight,
                lang=user.language or "ru",
            )
        except AIError as exc:
            logger.warning(
                "workout/estimate: %s | finish=%s raw=%s",
                exc, exc.finish_reason, (exc.raw or "")[:600],
            )
            detail = "Не удалось оценить калории. Попробуйте позже."
            if DEBUG_AI:
                detail = f"{exc} | finish={exc.finish_reason} | raw={(exc.raw or 'пусто')[:1500]}"
            raise HTTPException(status_code=502, detail=detail)
        except RuntimeError as exc:
            logger.warning("workout/estimate: %s", exc)
            detail = "Сервис оценки калорий временно недоступен. Попробуйте позже."
            if DEBUG_AI:
                detail = str(exc)
            raise HTTPException(status_code=502, detail=detail)
        return WorkoutEstimateOut(
            calories_burned=result["calories"], met=result["met"]
        )

    kcal, met = fitness.estimate_calories_burned(
        data.type, data.duration_min, user.weight
    )
    return WorkoutEstimateOut(calories_burned=kcal, met=met)


# --------------------------------------------------------------------------- #
#  Еда: ручной ввод, недавние блюда, рекомендации
# --------------------------------------------------------------------------- #
@app.post("/food/manual", response_model=DiaryEntryOut)
def food_manual(
    data: ManualFoodIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DiaryEntry:
    """Добавить блюдо вручную: создать запись дневника и сохранить его
    в «избранное/недавние» (FavoriteFood) для быстрого повторного добавления."""
    # 1) Запись в дневник питания.
    db_entry = DiaryEntry(
        telegram_id=user.telegram_id,
        date=data.date,
        meal_type=data.meal_type,
        dish_name=data.dish_name,
        calories=data.calories,
        proteins=data.proteins,
        fats=data.fats,
        carbs=data.carbs,
        # Количество и единица измерения (канонический ключ) — опциональны.
        quantity=data.quantity,
        unit=data.unit,
    )
    db.add(db_entry)

    # 2) Сохраняем блюдо в «недавние», чтобы его можно было повторно выбрать.
    favorite = FavoriteFood(
        telegram_id=user.telegram_id,
        dish_name=data.dish_name,
        calories=data.calories,
        proteins=data.proteins,
        fats=data.fats,
        carbs=data.carbs,
    )
    db.add(favorite)

    db.commit()
    db.refresh(db_entry)
    return db_entry


@app.get("/food/recent", response_model=RecentFoodsOut)
def food_recent(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RecentFoodsOut:
    """Вернуть недавно добавленные блюда пользователя без повторов по названию."""
    rows = (
        db.query(FavoriteFood)
        .filter(FavoriteFood.telegram_id == user.telegram_id)
        .order_by(FavoriteFood.created_at.desc(), FavoriteFood.id.desc())
        .limit(100)
        .all()
    )

    # Дедупликация по названию блюда — оставляем первое (самое свежее) вхождение.
    seen: set[str] = set()
    items: list[RecentFoodOut] = []
    for r in rows:
        key = (r.dish_name or "").strip().lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(
            RecentFoodOut(
                dish_name=r.dish_name,
                calories=r.calories or 0,
                proteins=r.proteins or 0.0,
                fats=r.fats or 0.0,
                carbs=r.carbs or 0.0,
            )
        )
        # Возвращаем примерно 20 уникальных недавних блюд.
        if len(items) >= 20:
            break

    return RecentFoodsOut(items=items)


@app.get("/food/search", response_model=FoodSearchOut)
async def food_search_route(
    q: str = "",
    user: User = Depends(get_current_user),
) -> FoodSearchOut:
    """Поиск блюд с готовыми КБЖУ во внешней базе продуктов.

    Бесплатный маршрут (базовый дневник не платный), но с ограничением частоты:
    внешний сервис считает лимит по IP ВСЕГО сервера, поэтому внутри модуля
    есть кэш и глобальный троттлинг. Пустой результат — не ошибка: фронт
    предложит ввести блюдо вручную.
    """
    query = (q or "").strip()
    if len(query) < 2:
        return FoodSearchOut(query=query, items=[])

    # Защита от «поиска на каждую букву» со стороны одного пользователя.
    ratelimit.enforce_search(user.telegram_id)

    # Сетевой запрос — в пул потоков, чтобы не блокировать event loop.
    raw = await run_in_threadpool(
        food_search.search_products, query, user.language or "ru", 12
    )

    return FoodSearchOut(
        query=query,
        items=[FoodSearchItem(**item) for item in raw],
    )


@app.post("/food/recommend", response_model=RecommendOut)
def food_recommend(
    data: RecommendIn,
    user: User = Depends(subscription.require_premium),
) -> RecommendOut:
    """Подобрать 2-3 блюда под оставшиеся на день КБЖУ с помощью ИИ."""
    # Если цель диеты не передана явно — берём из профиля пользователя.
    diet_goal = data.diet_goal if data.diet_goal is not None else getattr(user, "diet_goal", None)

    try:
        ratelimit.enforce_ai(user.telegram_id)
        result = recommend_meals(
            remaining_calories=data.remaining_calories,
            remaining_proteins=data.remaining_proteins,
            remaining_fats=data.remaining_fats,
            remaining_carbs=data.remaining_carbs,
            diet_goal=diet_goal,
            time_of_day=data.time_of_day,
            # Язык рекомендаций берём из профиля пользователя ("ru" по умолчанию).
            lang=user.language or "ru",
        )
    except AIError as exc:
        logger.warning(
            "food/recommend: %s | finish=%s raw=%s",
            exc, exc.finish_reason, (exc.raw or "")[:600],
        )
        detail = "Не удалось подобрать рекомендации. Попробуйте позже."
        if DEBUG_AI:
            detail = f"{exc} | finish={exc.finish_reason} | raw={(exc.raw or 'пусто')[:1500]}"
        raise HTTPException(status_code=502, detail=detail)
    except RuntimeError as exc:
        logger.warning("food/recommend: %s", exc)
        detail = "Сервис рекомендаций временно недоступен. Попробуйте позже."
        if DEBUG_AI:
            detail = str(exc)
        raise HTTPException(status_code=502, detail=detail)

    suggestions = [
        RecommendItem(
            dish_name=s["dish_name"],
            calories=s["calories"],
            proteins=s["proteins"],
            fats=s["fats"],
            carbs=s["carbs"],
            reason=s["reason"],
        )
        for s in result.get("suggestions", [])
    ]
    return RecommendOut(suggestions=suggestions)


# --------------------------------------------------------------------------- #
#  Умный расчёт КБЖУ по названию/количеству (базовый дневник — БЕЗ премиума)
# --------------------------------------------------------------------------- #
@app.post("/food/calculate", response_model=FoodCalculateOut)
def food_calculate(
    data: FoodCalculateIn,
    user: User = Depends(get_current_user),
) -> FoodCalculateOut:
    """Оценить ИТОГОВЫЕ КБЖУ продукта в заданном количестве и единице (ИИ).

    Базовая функция дневника — доступна без премиума (get_current_user).
    Если количество/единица не заданы, ИИ подставляет разумное значение по
    умолчанию (1 шт/порция или типичные 100 г) и возвращает применённые
    quantity/unit. Язык расчёта берём из профиля пользователя ("ru" по умолчанию).
    При сбое ИИ отдаём 502 (как в /food/recommend).
    """
    # Бесплатный AI-эндпоинт — обязательно ограничиваем частоту (иначе цикл
    # запросов «нажёг» бы неограниченный счёт OpenAI). Free — узкий дневной кап.
    ratelimit.enforce_calc(user.telegram_id, subscription.is_premium(user))
    try:
        result = calculate_food(
            data.name,
            data.quantity,
            data.unit,
            lang=user.language or "ru",
        )
    except AIError as exc:
        logger.warning(
            "food/calculate: %s | finish=%s raw=%s",
            exc, exc.finish_reason, (exc.raw or "")[:600],
        )
        detail = "Не удалось рассчитать калорийность. Попробуйте позже."
        if DEBUG_AI:
            detail = f"{exc} | finish={exc.finish_reason} | raw={(exc.raw or 'пусто')[:1500]}"
        raise HTTPException(status_code=502, detail=detail)
    except RuntimeError as exc:
        logger.warning("food/calculate: %s", exc)
        detail = "Сервис расчёта калорийности временно недоступен. Попробуйте позже."
        if DEBUG_AI:
            detail = str(exc)
        raise HTTPException(status_code=502, detail=detail)

    return FoodCalculateOut(**result)


# --------------------------------------------------------------------------- #
#  «Вчерашние» блюда для быстрого повтора (базовый дневник — БЕЗ премиума)
# --------------------------------------------------------------------------- #
@app.get("/food/yesterday", response_model=YesterdayOut)
def food_yesterday(
    date: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> YesterdayOut:
    """Вернуть блюда, залогированные пользователем во «вчерашний» день.

    Базовая функция дневника — доступна без премиума (get_current_user).
    ref — переданная дата (если валидна) либо сегодня; «вчера» = ref - 1 день.
    Дедупликация по нижнему регистру названия: оставляем ПЕРВОЕ вхождение
    (самое раннее по времени добавления).
    """
    # Опорная дата: переданная (если валидна) либо сегодняшняя.
    try:
        ref = date_cls.fromisoformat(date) if date else date_cls.today()
    except (TypeError, ValueError):
        ref = date_cls.today()
    yesterday = (ref - timedelta(days=1)).isoformat()

    rows = (
        db.query(DiaryEntry)
        .filter(
            DiaryEntry.telegram_id == user.telegram_id,
            DiaryEntry.date == yesterday,
        )
        .order_by(DiaryEntry.created_at.asc(), DiaryEntry.id.asc())
        .all()
    )

    # Дедупликация по названию блюда — оставляем первое (самое раннее) вхождение.
    seen: set[str] = set()
    items: list[YesterdayItem] = []
    for r in rows:
        key = (r.dish_name or "").strip().lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(
            YesterdayItem(
                dish_name=r.dish_name,
                quantity=r.quantity,
                unit=r.unit,
                calories=r.calories or 0,
                proteins=r.proteins or 0.0,
                fats=r.fats or 0.0,
                carbs=r.carbs or 0.0,
                meal_type=r.meal_type,
            )
        )

    return YesterdayOut(items=items)


# --------------------------------------------------------------------------- #
#  Спортивное питание / добавки
# --------------------------------------------------------------------------- #
@app.post("/supplement/add", response_model=SupplementOut)
def supplement_add(
    data: SupplementIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> Supplement:
    """Добавить запись о приёме спортивного питания / добавки."""
    db_supp = Supplement(
        telegram_id=user.telegram_id,
        name=data.name,
        # Тип убран из UI — терпим его отсутствие (по умолчанию пустая строка).
        type=data.type or "",
        dosage=data.dosage,
        intake_time=data.intake_time,
        reminder_enabled=data.reminder_enabled,
    )
    db.add(db_supp)
    db.commit()
    db.refresh(db_supp)
    return db_supp


@app.get("/supplement/list", response_model=SupplementListOut)
def supplement_list(
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> SupplementListOut:
    """Вернуть список добавок пользователя."""
    rows = (
        db.query(Supplement)
        .filter(Supplement.telegram_id == user.telegram_id)
        .order_by(Supplement.created_at.asc(), Supplement.id.asc())
        .all()
    )
    items = [SupplementOut.model_validate(s) for s in rows]
    return SupplementListOut(items=items)


@app.delete("/supplement/{supplement_id}")
def supplement_delete(
    supplement_id: int,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> dict:
    """Удалить добавку, если она принадлежит текущему пользователю."""
    supp = db.query(Supplement).filter(Supplement.id == supplement_id).first()
    if supp is None or supp.telegram_id != user.telegram_id:
        # Чужую или несуществующую запись прячем за 404.
        raise HTTPException(status_code=404, detail="Добавка не найдена")

    # Сначала снимаем ссылки из напоминаний: на PostgreSQL внешний ключ
    # supplement_reminder_items.supplement_id иначе даст ForeignKeyViolation
    # (на SQLite ограничения по умолчанию не проверяются, поэтому баг был незаметен).
    db.query(SupplementReminderItem).filter(
        SupplementReminderItem.supplement_id == supplement_id
    ).delete(synchronize_session=False)

    db.delete(supp)
    db.commit()
    return {"ok": True}


@app.get("/supplement/suggest", response_model=SupplementSuggestOut)
def supplement_suggest(
    user: User = Depends(subscription.require_premium),
) -> SupplementSuggestOut:
    """Подсказать базовые добавки под цель пользователя с помощью ИИ.

    Важно: это НЕ медицинская рекомендация — об этом сообщаем в disclaimer.
    """
    diet_goal = getattr(user, "diet_goal", None)
    try:
        # Язык подсказок берём из профиля пользователя ("ru" по умолчанию).
        ratelimit.enforce_ai(user.telegram_id)
        result = suggest_supplements(diet_goal, lang=user.language or "ru")
    except AIError as exc:
        logger.warning(
            "supplement/suggest: %s | finish=%s raw=%s",
            exc, exc.finish_reason, (exc.raw or "")[:600],
        )
        detail = "Не удалось получить подсказки по добавкам. Попробуйте позже."
        if DEBUG_AI:
            detail = f"{exc} | finish={exc.finish_reason} | raw={(exc.raw or 'пусто')[:1500]}"
        raise HTTPException(status_code=502, detail=detail)
    except RuntimeError as exc:
        logger.warning("supplement/suggest: %s", exc)
        detail = "Сервис подсказок временно недоступен. Попробуйте позже."
        if DEBUG_AI:
            detail = str(exc)
        raise HTTPException(status_code=502, detail=detail)

    suggestions = [
        SupplementSuggestItem(
            name=s["name"],
            dosage=s["dosage"],
            note=s["note"],
            # Ссылка «Купить» собирается на бэкенде (партнёрские параметры — в env).
            buy_url=config.market_search_url(s["name"]),
        )
        for s in result.get("suggestions", [])
    ]
    return SupplementSuggestOut(
        suggestions=suggestions,
        disclaimer="Не является медицинской рекомендацией, проконсультируйтесь со специалистом",
    )


# --------------------------------------------------------------------------- #
#  Расчёт цели (калории и БЖУ)
# --------------------------------------------------------------------------- #
@app.post("/goal/calculate", response_model=GoalCalcOut)
def goal_calculate(
    data: GoalCalcIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GoalCalcOut:
    """Рассчитать дневную норму калорий и БЖУ.

    Недостающие во входных данных параметры берём из профиля пользователя.
    Результат сохраняем в профиль (цель, БЖУ, цель диеты, при наличии — пол
    и активность) и возвращаем клиенту.
    """
    # Берём значения из тела запроса, недостающие подставляем из профиля.
    weight = data.weight if data.weight is not None else user.weight
    height = data.height if data.height is not None else user.height
    age = data.age if data.age is not None else user.age
    gender = data.gender if data.gender is not None else user.gender
    activity_level = (
        data.activity_level if data.activity_level is not None else user.activity_level
    )
    diet_goal = (
        data.diet_goal if data.diet_goal is not None else getattr(user, "diet_goal", None)
    )

    try:
        result = nutrition.compute_goal(
            weight=weight,
            height=height,
            age=age,
            gender=gender,
            activity_level=activity_level,
            diet_goal=diet_goal,
        )
    except ValueError as exc:
        # Не хватает исходных данных (вес/рост/возраст) — это ошибка клиента.
        raise HTTPException(status_code=400, detail=str(exc))

    # Сохраняем рассчитанные значения в профиль пользователя.
    user.daily_goal_kcal = result["daily_goal_kcal"]
    user.target_proteins = result["target_proteins"]
    user.target_fats = result["target_fats"]
    user.target_carbs = result["target_carbs"]
    user.diet_goal = result["diet_goal"]
    # Пол и активность сохраняем, только если они были вычислены/переданы.
    if gender is not None:
        user.gender = gender
    if activity_level is not None:
        user.activity_level = activity_level

    db.commit()
    db.refresh(user)

    return GoalCalcOut(
        daily_goal_kcal=result["daily_goal_kcal"],
        target_proteins=result["target_proteins"],
        target_fats=result["target_fats"],
        target_carbs=result["target_carbs"],
        diet_goal=result["diet_goal"],
        bmr=result["bmr"],
        tdee=result["tdee"],
    )


# --------------------------------------------------------------------------- #
#  Настройки уведомлений
# --------------------------------------------------------------------------- #
def _get_or_create_notification_settings(
    db: Session, telegram_id: int
) -> NotificationSettings:
    """Вернуть настройки уведомлений пользователя, создав их при отсутствии."""
    settings = (
        db.query(NotificationSettings)
        .filter(NotificationSettings.telegram_id == telegram_id)
        .first()
    )
    if settings is None:
        # Создаём строку с дефолтами (значения по умолчанию заданы в модели).
        settings = NotificationSettings(telegram_id=telegram_id)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _notification_settings_out(settings: NotificationSettings) -> NotificationSettingsOut:
    """Собрать NotificationSettingsOut из ORM + распарсить список времён приёмов.

    meal_times НЕ маппится авто-магией из ORM (в БД это JSON-строка
    meal_times_json), поэтому заполняем его вручную с защитой от битого JSON.
    """
    out = NotificationSettingsOut.model_validate(settings)
    try:
        parsed = json.loads(getattr(settings, "meal_times_json", None) or "[]")
        # На всякий случай проверяем, что это именно список строк.
        if isinstance(parsed, list):
            out.meal_times = [str(t) for t in parsed]
        else:
            out.meal_times = []
    except Exception:
        # Битый/некорректный JSON — отдаём пустой список.
        out.meal_times = []
    return out


@app.get("/notifications/settings", response_model=NotificationSettingsOut)
def notifications_get(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationSettingsOut:
    """Вернуть настройки уведомлений (создав их с дефолтами при первом запросе)."""
    settings = _get_or_create_notification_settings(db, user.telegram_id)
    return _notification_settings_out(settings)


@app.post("/notifications/settings", response_model=NotificationSettingsOut)
def notifications_update(
    data: NotificationSettingsIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationSettingsOut:
    """Обновить настройки уведомлений (upsert), меняя только переданные поля."""
    settings = _get_or_create_notification_settings(db, user.telegram_id)

    payload = data.model_dump(exclude_unset=True)
    # meal_times обрабатываем отдельно: в БД это JSON-строка meal_times_json.
    meal_times = payload.pop("meal_times", None)
    for field, value in payload.items():
        # Применяем только реально переданные поля, чтобы не сбросить остальные.
        setattr(settings, field, value)

    # Если пришёл список времён — очищаем "HH:MM" и сериализуем в JSON.
    if isinstance(meal_times, list):
        cleaned = [_normalize_time(t) for t in meal_times if str(t or "").strip()]
        settings.meal_times_json = json.dumps(cleaned)

    db.commit()
    db.refresh(settings)
    return _notification_settings_out(settings)


# --------------------------------------------------------------------------- #
#  Напоминания о тренировках (новые таблицы TrainingReminder)
# --------------------------------------------------------------------------- #
@app.get("/reminders/training", response_model=TrainingRemindersOut)
def training_reminders_list(
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainingRemindersOut:
    """Вернуть список напоминаний о тренировках текущего пользователя.

    Дни недели хранятся в БД как CSV (Пн=0..Вс=6) и разворачиваются в List[int].
    """
    rows = (
        db.query(TrainingReminder)
        .filter(TrainingReminder.telegram_id == user.telegram_id)
        .order_by(TrainingReminder.created_at.asc(), TrainingReminder.id.asc())
        .all()
    )
    items = [
        TrainingReminderOut(
            id=r.id,
            weekdays=_csv_to_weekdays(r.weekdays),
            time=r.time,
            enabled=bool(r.enabled),
        )
        for r in rows
    ]
    return TrainingRemindersOut(items=items)


@app.post("/reminders/training", response_model=TrainingReminderOut)
def training_reminder_add(
    data: TrainingReminderIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TrainingReminderOut:
    """Создать напоминание о тренировке.

    Дни недели (List[int]) сворачиваем в CSV, время мягко валидируем как HH:MM.
    """
    time_str = _normalize_time(data.time)
    weekdays_csv = _weekdays_to_csv(data.weekdays)

    reminder = TrainingReminder(
        telegram_id=user.telegram_id,
        weekdays=weekdays_csv,
        time=time_str,
        enabled=bool(data.enabled),
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)

    return TrainingReminderOut(
        id=reminder.id,
        weekdays=_csv_to_weekdays(reminder.weekdays),
        time=reminder.time,
        enabled=bool(reminder.enabled),
    )


@app.delete("/reminders/training/{reminder_id}")
def training_reminder_delete(
    reminder_id: int,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> dict:
    """Удалить напоминание о тренировке, если оно принадлежит пользователю."""
    reminder = (
        db.query(TrainingReminder)
        .filter(TrainingReminder.id == reminder_id)
        .first()
    )
    if reminder is None or reminder.telegram_id != user.telegram_id:
        # Чужое или несуществующее напоминание прячем за 404.
        raise HTTPException(status_code=404, detail="Напоминание не найдено")

    db.delete(reminder)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
#  Напоминания о приёме спортпита (новые таблицы SupplementReminder/Item)
# --------------------------------------------------------------------------- #
def _build_supplement_reminder_out(
    db: Session, reminder: SupplementReminder, telegram_id: int
) -> SupplementReminderOut:
    """Собрать схему ответа для напоминания спортпита с названиями добавок.

    Названия подтягиваем через SupplementReminderItem -> Supplement, оставляя
    только добавки, принадлежащие пользователю (чужие связи игнорируем).
    """
    items = (
        db.query(SupplementReminderItem)
        .filter(SupplementReminderItem.reminder_id == reminder.id)
        .all()
    )
    supplements: list[SupplementReminderItemOut] = []
    for it in items:
        supp = (
            db.query(Supplement)
            .filter(
                Supplement.id == it.supplement_id,
                Supplement.telegram_id == telegram_id,
            )
            .first()
        )
        if supp is not None:
            supplements.append(
                SupplementReminderItemOut(id=supp.id, name=supp.name)
            )

    return SupplementReminderOut(
        id=reminder.id,
        label=reminder.label,
        time=reminder.time,
        enabled=bool(reminder.enabled),
        supplements=supplements,
    )


@app.get("/reminders/supplement", response_model=SupplementRemindersOut)
def supplement_reminders_list(
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> SupplementRemindersOut:
    """Вернуть напоминания о приёме спортпита с названиями привязанных добавок."""
    rows = (
        db.query(SupplementReminder)
        .filter(SupplementReminder.telegram_id == user.telegram_id)
        .order_by(SupplementReminder.created_at.asc(), SupplementReminder.id.asc())
        .all()
    )
    items = [
        _build_supplement_reminder_out(db, r, user.telegram_id) for r in rows
    ]
    return SupplementRemindersOut(items=items)


@app.post("/reminders/supplement", response_model=SupplementReminderOut)
def supplement_reminder_add(
    data: SupplementReminderIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> SupplementReminderOut:
    """Создать напоминание о приёме спортпита.

    Для каждого supplement_id, ПРИНАДЛЕЖАЩЕГО пользователю, создаём связь
    SupplementReminderItem; чужие/несуществующие добавки молча пропускаем.
    Время мягко валидируем как HH:MM.
    """
    time_str = _normalize_time(data.time)

    reminder = SupplementReminder(
        telegram_id=user.telegram_id,
        # Метка убрана из UI — терпим её отсутствие (по умолчанию пустая строка).
        label=data.label or "",
        time=time_str,
        enabled=bool(data.enabled),
    )
    db.add(reminder)
    # flush, чтобы получить reminder.id до создания связей.
    db.flush()

    # Привязываем только добавки, реально принадлежащие пользователю.
    for supplement_id in data.supplement_ids or []:
        supp = (
            db.query(Supplement)
            .filter(
                Supplement.id == supplement_id,
                Supplement.telegram_id == user.telegram_id,
            )
            .first()
        )
        if supp is None:
            continue
        db.add(
            SupplementReminderItem(
                reminder_id=reminder.id,
                supplement_id=supp.id,
            )
        )

    db.commit()
    db.refresh(reminder)

    return _build_supplement_reminder_out(db, reminder, user.telegram_id)


@app.delete("/reminders/supplement/{reminder_id}")
def supplement_reminder_delete(
    reminder_id: int,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> dict:
    """Удалить напоминание о спортпите вместе со связанными элементами."""
    reminder = (
        db.query(SupplementReminder)
        .filter(SupplementReminder.id == reminder_id)
        .first()
    )
    if reminder is None or reminder.telegram_id != user.telegram_id:
        # Чужое или несуществующее напоминание прячем за 404.
        raise HTTPException(status_code=404, detail="Напоминание не найдено")

    # Сначала удаляем связанные элементы, затем сам reminder.
    db.query(SupplementReminderItem).filter(
        SupplementReminderItem.reminder_id == reminder.id
    ).delete(synchronize_session=False)
    db.delete(reminder)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
#  Персональные рекомендации по спортпиту (ИИ, с учётом тренировок и цели)
# --------------------------------------------------------------------------- #
@app.post("/supplement/recommend", response_model=SupplementRecommendOut)
def supplement_recommend(
    data: SupplementRecommendIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> SupplementRecommendOut:
    """Персональные рекомендации по спортпиту от ИИ.

    Учитываем цель улучшения (improvement_goal), частоту/тип тренировок за
    последние 14 дней и цель диеты из профиля. Выбранную цель улучшения
    сохраняем в профиль (user.supplement_goal). НЕ медицинская рекомендация.
    """
    # Сохраняем выбранную цель улучшения в профиль (для будущих советов).
    user.supplement_goal = data.improvement_goal
    db.commit()

    # Считаем тренировки за последние 14 дней и собираем их типы.
    today = date_cls.today()
    start = today - timedelta(days=13)  # 14 календарных дней включительно
    start_str = start.isoformat()
    end_str = today.isoformat()

    workouts = (
        db.query(Workout)
        .filter(
            Workout.telegram_id == user.telegram_id,
            Workout.date >= start_str,
            Workout.date <= end_str,
        )
        .all()
    )
    training_count = len(workouts)
    # Уникальные типы тренировок (без пустых значений), порядок не важен.
    workout_types = sorted(
        {(w.type or "").strip() for w in workouts if (w.type or "").strip()}
    )

    diet_goal = getattr(user, "diet_goal", None)

    try:
        ratelimit.enforce_ai(user.telegram_id)
        result = recommend_supplements(
            improvement_goal=data.improvement_goal,
            training_count=training_count,
            workout_types=workout_types,
            diet_goal=diet_goal,
            # Язык рекомендаций берём из профиля пользователя ("ru" по умолчанию).
            lang=user.language or "ru",
        )
    except AIError as exc:
        logger.warning(
            "supplement/recommend: %s | finish=%s raw=%s",
            exc, exc.finish_reason, (exc.raw or "")[:600],
        )
        detail = "Не удалось получить рекомендации по добавкам. Попробуйте позже."
        if DEBUG_AI:
            detail = f"{exc} | finish={exc.finish_reason} | raw={(exc.raw or 'пусто')[:1500]}"
        raise HTTPException(status_code=502, detail=detail)
    except RuntimeError as exc:
        logger.warning("supplement/recommend: %s", exc)
        detail = "Сервис рекомендаций временно недоступен. Попробуйте позже."
        if DEBUG_AI:
            detail = str(exc)
        raise HTTPException(status_code=502, detail=detail)

    suggestions = [
        SupplementSuggestItem(
            name=s["name"],
            dosage=s["dosage"],
            note=s["note"],
            # Ссылка «Купить» собирается на бэкенде (партнёрские параметры — в env).
            buy_url=config.market_search_url(s["name"]),
        )
        for s in result.get("suggestions", [])
    ]
    return SupplementRecommendOut(
        suggestions=suggestions,
        disclaimer="Не является медицинской рекомендацией, проконсультируйтесь со специалистом",
        training_count=training_count,
        improvement_goal=data.improvement_goal,
    )


# --------------------------------------------------------------------------- #
#  Восстановление после тренировок (ИИ)
# --------------------------------------------------------------------------- #
@app.post("/recovery/advice", response_model=RecoveryAdviceOut)
def recovery_advice_route(
    data: RecoveryAdviceIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> RecoveryAdviceOut:
    """Совет по восстановлению для выбранной зоны тела (премиум).

    Учитываем последние тренировки пользователя (7 дней) как контекст.
    НЕ является медицинской рекомендацией — дисклеймер отдаём в ответе,
    фронт обязан его показывать.
    """
    # Контекст: тренировки за последнюю неделю (тип + длительность).
    today = date_cls.today()
    start_str = (today - timedelta(days=6)).isoformat()
    recent = (
        db.query(Workout)
        .filter(
            Workout.telegram_id == user.telegram_id,
            Workout.date >= start_str,
            Workout.date <= today.isoformat(),
        )
        .order_by(Workout.date.desc())
        .limit(10)
        .all()
    )
    recent_workouts = []
    for w in recent:
        label = (w.description or w.type or "").strip()
        if not label:
            continue
        mins = w.duration_min or 0
        recent_workouts.append(f"{w.date}: {label}" + (f", {mins} мин" if mins else ""))

    try:
        ratelimit.enforce_ai(user.telegram_id)
        result = recovery_advice(
            zone=data.zone,
            complaint=data.complaint,
            recent_workouts=recent_workouts,
            lang=user.language or "ru",
        )
    except AIError as exc:
        logger.warning(
            "recovery/advice: %s | finish=%s raw=%s",
            exc, exc.finish_reason, (exc.raw or "")[:600],
        )
        detail = "Не удалось получить совет по восстановлению. Попробуйте позже."
        if DEBUG_AI:
            detail = f"{exc} | finish={exc.finish_reason} | raw={(exc.raw or 'пусто')[:1500]}"
        raise HTTPException(status_code=502, detail=detail)
    except RuntimeError as exc:
        logger.warning("recovery/advice: %s", exc)
        detail = "Сервис советов временно недоступен. Попробуйте позже."
        if DEBUG_AI:
            detail = str(exc)
        raise HTTPException(status_code=502, detail=detail)

    return RecoveryAdviceOut(
        zone=result["zone"],
        likely_cause=result["likely_cause"],
        is_typical_soreness=result["is_typical_soreness"],
        today=result["today"],
        avoid=result["avoid"],
        training=result["training"],
        red_flags=result["red_flags"],
        disclaimer="Не является медицинской рекомендацией, проконсультируйтесь со специалистом",
    )


# --------------------------------------------------------------------------- #
#  Подписка и доступ (Этап 1): статус, лимит сканов, оплата картой, вебхуки
# --------------------------------------------------------------------------- #
@app.get("/subscription/status", response_model=SubscriptionStatusOut)
def subscription_status(
    user: User = Depends(get_current_user),
) -> SubscriptionStatusOut:
    """Вернуть статус подписки текущего пользователя и доступные тарифы.

    is_premium вычисляется ТОЛЬКО на бэкенде (owner / lifetime / активная дата).
    Эндпоинт бесплатный — нужен в т.ч. для показа экрана оформления подписки.
    """
    return _subscription_status_out(user)


def _subscription_status_out(user: User) -> SubscriptionStatusOut:
    """Собрать статус подписки (+ признаки триала и истечения) для клиента."""
    premium = subscription.is_premium(user)
    stype = user.subscription_type or "free"
    # «Истекло» — была платная подписка, но она больше не активна.
    is_expired = (not premium) and stype not in ("free",) and not user.is_owner
    is_trial_available = (
        config.TRIAL_DAYS > 0 and not premium and not bool(getattr(user, "used_trial", False))
    )
    # Рублёвая витрина. Показывается по наличию ЦЕНЫ, а не по подключённой
    # платёжной системе: цена и кнопка нужны в том числе для модерации в
    # платёжном сервисе, которую проходят ДО получения ключей.
    card_prices = {}
    for name in config.TARIFFS:
        price = config.rub_price_for(name)
        if price:
            card_prices[name] = price
    card_enabled = bool(card_prices)

    return SubscriptionStatusOut(
        subscription_type=stype,
        subscription_until=(
            user.subscription_until.isoformat() if user.subscription_until else None
        ),
        is_premium=premium,
        is_owner=bool(user.is_owner),
        # Каталог тарифов — только со сроком и рублёвой ценой (цен в звёздах нет).
        tariffs=config.tariff_catalog(),
        tribute_url=config.TRIBUTE_URL or None,
        is_trial_available=is_trial_available,
        trial_days=config.TRIAL_DAYS,
        is_expired=is_expired,
        card_enabled=card_enabled,
        # Валюта одна и та же у обоих провайдеров — рубли (цены задаются
        # PRICE_*_RUB), поэтому здесь константа, а не настройка провайдера.
        card_currency="RUB",
        card_prices=card_prices,
        card_provider=config.card_provider(),
        legal=config.legal_info(),
    )


@app.post("/subscription/trial", response_model=SubscriptionStatusOut)
def subscription_trial(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SubscriptionStatusOut:
    """Активировать одноразовый бесплатный пробный период (TRIAL_DAYS дней).

    Нельзя, если пользователь уже премиум или триал уже использован.
    """
    if config.TRIAL_DAYS <= 0:
        raise HTTPException(status_code=400, detail={"error": "trial_disabled", "message": "Пробный период недоступен"})
    if subscription.is_premium(user):
        raise HTTPException(status_code=400, detail={"error": "already_premium", "message": "Премиум уже активен"})
    if bool(getattr(user, "used_trial", False)):
        raise HTTPException(status_code=400, detail={"error": "trial_used", "message": "Пробный период уже был использован"})

    # Признак used_trial живёт в профиле, а профиль можно удалить через
    # /account/data — тогда триал брался бы заново бесконечно. Журнал платежей
    # удаление ПЕРЕЖИВАЕТ, поэтому проверяем ещё и по нему.
    already_had_trial = (
        db.query(Payment)
        .filter(Payment.telegram_id == user.telegram_id, Payment.provider == "trial")
        .first()
    )
    if already_had_trial is not None:
        raise HTTPException(
            status_code=400,
            detail={"error": "trial_used", "message": "Пробный период уже был использован"},
        )

    payment_providers.grant_days(
        db, user.telegram_id, config.TRIAL_DAYS, "trial", subscription_type="trial"
    )
    user.used_trial = True
    db.commit()
    db.refresh(user)
    return _subscription_status_out(user)


@app.get("/scans/remaining", response_model=ScansRemainingOut)
def scans_remaining(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScansRemainingOut:
    """Вернуть остаток бесплатных сканирований еды на сегодня.

    Для премиум-пользователей remaining = -1 (безлимит). Эндпоинт бесплатный —
    фронт показывает счётчик «осталось сканов» как для free, так и для premium.
    """
    info = subscription.scans_info(db, user)
    return ScansRemainingOut(
        used=info["used"],
        limit=info["limit"],
        remaining=info["remaining"],
        is_premium=info["is_premium"],
    )


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Приём апдейтов бота от Telegram (webhook).

    Без авторизации пользователя: запросы приходят напрямую от Telegram.
    Если задан секрет вебхука, сверяем заголовок X-Telegram-Bot-Api-Secret-Token
    и при несовпадении отвечаем 403. Любые ошибки разбора апдейта глушим и всегда
    отвечаем {"ok": True}, чтобы Telegram не ретраил доставку бесконечно.
    """
    # Проверка секрета вебхука. FAIL-CLOSED: в боевом режиме секрет ОБЯЗАТЕЛЕН,
    # иначе кто угодно смог бы прислать поддельный successful_payment и получить
    # премиум. Пропуск проверки допустим ТОЛЬКО в dev-режиме (ALLOW_INSECURE_AUTH).
    secret = config.TELEGRAM_WEBHOOK_SECRET
    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret:
        if not hmac.compare_digest(header_secret, secret):
            raise HTTPException(status_code=403, detail="Неверный секрет вебхука")
    elif not DEV_MODE:
        logger.error("telegram/webhook: TELEGRAM_WEBHOOK_SECRET не задан в проде — запрос отклонён")
        raise HTTPException(status_code=403, detail="Webhook secret not configured")

    try:
        update = await request.json()
        # Обработка синхронная (внутри бывают блокирующие вызовы OpenAI при
        # голосовых). Выносим в threadpool, чтобы не морозить event loop.
        await run_in_threadpool(telegram_bot.handle_update, db, update)
    except telegram_bot.PaymentActivationError:
        # Оплата прошла, но активация не удалась — просим Telegram повторить
        # доставку (200 не отдаём). Дедуп по charge_id защищает от двойного
        # начисления при повторе.
        logger.error("telegram/webhook: активация после оплаты не удалась — вернём 500 для ретрая")
        raise HTTPException(status_code=500, detail="activation failed, retry")
    except Exception as exc:  # noqa: BLE001 — вебхук не должен падать наружу
        logger.warning("telegram/webhook: ошибка обработки апдейта: %s", exc)

    # Telegram ждёт 200 OK; детали обработки наружу не раскрываем.
    return {"ok": True}


@app.post("/payment/tribute/webhook")
async def payment_tribute_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Приём вебхука об оплате от Tribute (альтернативный провайдер подписки).

    ВНИМАНИЕ: точный формат payload Tribute уточняется при подключении провайдера —
    ниже сделан гибкий разбор (telegram_id и сумма ищутся в нескольких возможных
    местах). Секрет проверяем по config.TRIBUTE_WEBHOOK_SECRET (заголовок или поле
    тела); если секрет не задан — в dev-режиме пропускаем. Любые ошибки глушим и
    ВСЕГДА отвечаем 200 {"ok": True}, не раскрывая детали наружу.
    """
    try:
        # Пытаемся прочитать тело как JSON; при сбое — пустой словарь.
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 — тело может быть не-JSON
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        # Проверка секрета. FAIL-CLOSED: без секрета в проде НЕ активируем ничего
        # (иначе любой мог бы прислать {telegram_id, tariff:'lifetime'}). Точный
        # формат подписи Tribute уточняется при подключении провайдера.
        if config.TRIBUTE_WEBHOOK_SECRET:
            header_secret = (
                request.headers.get("X-Tribute-Signature")
                or request.headers.get("X-Webhook-Secret")
                or request.headers.get("Authorization")
                or ""
            )
            body_secret = payload.get("secret") or payload.get("signature") or ""
            if not (
                hmac.compare_digest(str(header_secret), config.TRIBUTE_WEBHOOK_SECRET)
                or hmac.compare_digest(str(body_secret), config.TRIBUTE_WEBHOOK_SECRET)
            ):
                logger.warning("payment/tribute/webhook: неверный секрет, апдейт пропущен")
                return {"ok": True}
        elif not DEV_MODE:
            # Секрет не задан в проде — не доверяем запросу (Tribute отключён).
            logger.error("payment/tribute/webhook: TRIBUTE_WEBHOOK_SECRET не задан — апдейт пропущен")
            return {"ok": True}

        # Гибко извлекаем telegram_id из payload/metadata (формат уточняется).
        meta = payload.get("metadata") or payload.get("data") or {}
        if not isinstance(meta, dict):
            meta = {}
        raw_tid = (
            payload.get("telegram_id")
            or payload.get("user_id")
            or meta.get("telegram_id")
            or meta.get("user_id")
        )
        telegram_id = int(raw_tid) if raw_tid is not None else None

        if telegram_id:
            # Сумма/валюта и тариф: маппинг продукта/суммы -> тариф, по умолчанию monthly.
            amount = payload.get("amount") or meta.get("amount")
            currency = payload.get("currency") or meta.get("currency") or "RUB"
            tariff = (
                payload.get("tariff")
                or payload.get("product")
                or meta.get("tariff")
                or "monthly"
            )
            # Если пришёл неизвестный тариф — откатываемся на monthly.
            if tariff not in config.TARIFFS:
                tariff = "monthly"

            payment_providers.activate_premium(
                db, telegram_id, tariff, "tribute", amount, currency
            )
    except Exception as exc:  # noqa: BLE001 — вебхук всегда отвечает 200
        logger.warning("payment/tribute/webhook: ошибка обработки: %s", exc)

    # Всегда 200, чтобы провайдер не ретраил и мы не палили детали.
    return {"ok": True}


# --------------------------------------------------------------------------- #
#  CloudPayments — оплата подписки картой в рублях (один из двух провайдеров)
# --------------------------------------------------------------------------- #
@app.get("/payment/cloudpayments/config")
def cloudpayments_config(
    tariff: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Параметры для открытия виджета CloudPayments по выбранному тарифу.

    Возвращает ТОЛЬКО публичные данные (Public ID светить можно, API Secret —
    никогда). Доступ по итогам оплаты выдаётся не здесь, а вебхуком Pay с
    проверенной подписью.
    """
    if not config.cloudpayments_enabled():
        raise HTTPException(
            status_code=503,
            detail={"error": "cloudpayments_disabled",
                    "message": "Оплата картой пока не подключена"},
        )

    if config.tariff_for(tariff) is None:
        raise HTTPException(status_code=400, detail="Неизвестный тариф")

    amount = config.rub_price_for(tariff)
    if amount is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "tariff_not_available",
                    "message": "Этот тариф картой не оплачивается"},
        )

    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return {
        "public_id": config.CLOUDPAYMENTS_PUBLIC_ID,
        "amount": amount,
        "currency": config.CLOUDPAYMENTS_CURRENCY,
        # Видят плательщик и модератор CloudPayments: продукт и тариф по-русски
        # («Fitness Up — подписка на год»), а не код тарифа.
        "description": config.payment_description(tariff),
        # accountId — по нему вебхук поймёт, кому начислять доступ.
        "account_id": str(user.telegram_id),
        "invoice_id": cloudpayments.build_invoice_id(tariff, user.telegram_id, stamp),
        "tariff": tariff,
    }


@app.post("/payment/cloudpayments/webhook")
async def payment_cloudpayments_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Приём уведомлений CloudPayments (Check / Pay / Recurrent и прочие).

    КРИТИЧНО по протоколу: отвечать нужно HTTP 200 и телом РОВНО {"code": 0},
    иначе CloudPayments считает доставку неуспешной и повторяет её до 100 раз.

    КРИТИЧНО по безопасности:
      * подпись считается от СЫРОГО тела, поэтому читаем байты ДО разбора формы;
      * при неверной подписи ничего не начисляем;
      * платежи с TestMode=1 в проде доступ не выдают;
      * повторная доставка одного платежа отсекается по TransactionId.
    """
    # 1) Сырое тело — обязательно до любого парсинга (иначе подпись не сойдётся).
    raw = await request.body()

    signature = (
        request.headers.get("Content-HMAC")
        or request.headers.get("X-Content-HMAC")
        or ""
    )

    if not cloudpayments.is_enabled():
        logger.error("cloudpayments/webhook: интеграция не настроена")
        return {"code": cloudpayments.CODE_REJECTED}

    if not cloudpayments.verify_signature(raw, signature):
        logger.warning("cloudpayments/webhook: неверная подпись — уведомление отброшено")
        return {"code": cloudpayments.CODE_REJECTED}

    # 2) Разбор тела. По умолчанию приходит form-urlencoded, но в кабинете
    #    формат можно переключить на JSON — поддерживаем оба.
    data: dict = {}
    try:
        form = await request.form()
        data = {k: v for k, v in form.items()}
    except Exception:  # noqa: BLE001
        data = {}
    if not data:
        try:
            parsed = await request.json()
            if isinstance(parsed, dict):
                data = parsed
        except Exception:  # noqa: BLE001
            data = {}

    try:
        # Тестовые платежи в проде доступ не открывают.
        if cloudpayments.is_test_mode(data) and not DEV_MODE:
            logger.warning("cloudpayments/webhook: TestMode=1 в проде — доступ не выдан")
            return {"code": cloudpayments.CODE_OK}

        # ТИП УВЕДОМЛЕНИЯ. На один адрес приходят Check/Fail/Refund/Recurrent —
        # у всех подпись настоящая. Доступ выдаём только по реальному списанию,
        # иначе отказ в оплате открывал бы премиум так же, как успешная оплата.
        if not cloudpayments.is_successful_payment(data):
            logger.info(
                "cloudpayments/webhook: неплатёжное уведомление op=%s status=%s — доступ не выдан",
                data.get("OperationType"), data.get("Status"),
            )
            return {"code": cloudpayments.CODE_OK}

        tariff, telegram_id, transaction_id = cloudpayments.resolve_payment(data)

        # Без идентификатора транзакции невозможна защита от повторной доставки
        # (CloudPayments ретраит до 100 раз) — такое уведомление не принимаем.
        if not transaction_id:
            logger.error("cloudpayments/webhook: уведомление без TransactionId — доступ не выдан")
            return {"code": cloudpayments.CODE_REJECTED}

        if not telegram_id:
            logger.error("cloudpayments/webhook: не определён плательщик, данные=%s", data)
            return {"code": cloudpayments.CODE_INVALID_ACCOUNT}

        # Уведомление Check приходит ДО списания — только подтверждаем готовность.
        if not tariff:
            logger.warning("cloudpayments/webhook: не определён тариф, данные=%s", data)
            return {"code": cloudpayments.CODE_UNKNOWN_INVOICE}

        # СВЕРКА СУММЫ — обязательна. Виджет запускается на клиенте, а Public ID
        # публичен, поэтому номеру заказа доверять нельзя: без этой проверки
        # можно было бы оплатить 1 рубль с номером заказа "lifetime:<свой id>".
        if not cloudpayments.amount_matches_tariff(
            tariff, data.get("Amount"), data.get("Currency")
        ):
            logger.error(
                "cloudpayments/webhook: сумма %s %s не соответствует тарифу %s (tid=%s) — доступ НЕ выдан",
                data.get("Amount"), data.get("Currency"), tariff, telegram_id,
            )
            return {"code": cloudpayments.CODE_INVALID_AMOUNT}

        # 3) Идемпотентность: этот платёж уже обработан?
        already = (
            db.query(Payment)
            .filter(Payment.charge_id == f"cp:{transaction_id}")
            .first()
        )
        if already is not None:
            logger.info("cloudpayments/webhook: платёж %s уже обработан", transaction_id)
            return {"code": cloudpayments.CODE_OK}

        payment_providers.activate_premium(
            db,
            telegram_id,
            tariff,
            "cloudpayments",
            data.get("Amount"),
            data.get("Currency") or config.CLOUDPAYMENTS_CURRENCY,
            charge_id=f"cp:{transaction_id}",
        )
        logger.info(
            "cloudpayments/webhook: премиум активирован tid=%s тариф=%s транзакция=%s",
            telegram_id, tariff, transaction_id,
        )
    except Exception as exc:  # noqa: BLE001
        # Отвечаем не-нулевым кодом: CloudPayments повторит доставку, а дедуп
        # по TransactionId защитит от двойного начисления.
        logger.error("cloudpayments/webhook: сбой обработки: %s", exc)
        return {"code": cloudpayments.CODE_REJECTED}

    return {"code": cloudpayments.CODE_OK}


# --------------------------------------------------------------------------- #
#  ЮKassa — оплата подписки картой в рублях (второй провайдер)
#
#  Схема: create -> пользователь платит на стороне ЮKassa -> вебхук
#  payment.succeeded -> ПЕРЕПРОВЕРКА платежа по API -> активация доступа.
#  Почему нельзя верить телу уведомления и зачем сверять сумму — подробно
#  расписано в шапке backend/yookassa.py.
# --------------------------------------------------------------------------- #
@app.post("/payment/yookassa/create", response_model=YookassaCreateOut)
def yookassa_create(
    data: YookassaCreateIn,
    user: User = Depends(get_current_user),
) -> YookassaCreateOut:
    """Создать платёж ЮKassa для выбранного тарифа и вернуть ссылку на оплату.

    Сумму берём из прайса на сервере: клиент передаёт только имя тарифа, иначе
    можно было бы «назначить» себе цену. Доступ по факту оплаты выдаётся не
    здесь, а вебхуком с перепроверкой платежа по API.
    """
    # Создание платежа — обращение к внешнему API, поэтому ограничиваем частоту
    # (иначе кнопкой «Оплатить» можно накидать сотни платежей в кабинете).
    ratelimit.enforce_payment(user.telegram_id)

    if not yookassa.is_enabled():
        raise HTTPException(
            status_code=503,
            detail={"error": "yookassa_disabled",
                    "message": "Оплата картой пока не подключена"},
        )

    if config.tariff_for(data.tariff) is None:
        raise HTTPException(status_code=400, detail="Неизвестный тариф")

    if config.rub_price_for(data.tariff) is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "tariff_not_available",
                    "message": "Этот тариф картой не оплачивается"},
        )

    try:
        created = yookassa.create_payment(
            data.tariff, user.telegram_id, getattr(user, "language", "ru") or "ru"
        )
    except RuntimeError as exc:
        logger.warning("payment/yookassa/create: %s", exc)
        raise HTTPException(
            status_code=502, detail="Не удалось создать платёж. Попробуйте позже."
        )

    return YookassaCreateOut(
        payment_id=created["id"], confirmation_url=created["confirmation_url"]
    )


# Уведомления ЮKassa не подписаны, поэтому адрес вебхука содержит секрет:
#   /payment/yookassa/webhook/<YOOKASSA_WEBHOOK_SECRET>
# Короткий адрес без секрета оставлен для dev-режима (локальная отладка).
YOOKASSA_WEBHOOK_MAX_BODY = 64 * 1024


@app.post("/payment/yookassa/webhook")
@app.post("/payment/yookassa/webhook/{token}")
async def payment_yookassa_webhook(
    request: Request,
    token: str = "",
    db: Session = Depends(get_db),
) -> dict:
    """Приём уведомлений ЮKassa (без авторизации — запрос идёт от провайдера).

    КРИТИЧНО: уведомления ЮKassa НЕ ПОДПИСАНЫ, поэтому тело запроса — только
    подсказка «посмотри платёж N». Реальные статус, сумму и metadata берём
    ПОВТОРНЫМ запросом платежа по API (yookassa.fetch_payment).

    Защита самого входа (он публичный и на каждый запрос ходит в API ЮKassa
    с боевыми ключами): секрет в адресе (fail-closed в проде), лимит по IP,
    ограничение размера тела, строгий формат id платежа.

    Ответ всегда HTTP 200 {"ok": true} — ЮKassa повторяет доставку, пока не
    получит 200. Исключения: не удалось перепроверить платёж по API или
    активация упала (сбой БД) — тогда 500, чтобы ЮKassa повторила и мы не
    потеряли доступ оплатившего (дедуп по charge_id защищает от двойного
    начисления).
    """
    # 0) Секрет в адресе. Без него в проде уведомления не обрабатываем —
    #    иначе любой мог бы гонять наш сервер в API ЮKassa.
    secret = config.YOOKASSA_WEBHOOK_SECRET
    if secret:
        if not hmac.compare_digest(str(token or ""), secret):
            logger.warning("yookassa/webhook: неверный секрет в адресе — уведомление отброшено")
            return {"ok": True}
    elif not DEV_MODE:
        logger.error("yookassa/webhook: YOOKASSA_WEBHOOK_SECRET не задан в проде — уведомление отброшено")
        return {"ok": True}

    # Лимит по IP: перепроверка платежа — сетевой вызов, пул потоков общий.
    client_ip = request.client.host if request.client else "unknown"
    allowed, _why = ratelimit.check(f"ykhook:{client_ip}", 60, 5000)
    if not allowed:
        logger.warning("yookassa/webhook: слишком много уведомлений с %s — пропуск", client_ip)
        return {"ok": True}

    # Слишком большое тело — не наш формат (уведомление ЮKassa — пара килобайт).
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > YOOKASSA_WEBHOOK_MAX_BODY:
        logger.warning("yookassa/webhook: тело %s байт — отброшено", declared)
        return {"ok": True}

    # 1) Тело уведомления. Мусор — просто подтверждаем и выходим.
    try:
        raw = await request.body()
        if len(raw) > YOOKASSA_WEBHOOK_MAX_BODY:
            return {"ok": True}
        body = json.loads(raw.decode("utf-8")) if raw else None
    except Exception:  # noqa: BLE001 — тело может быть не-JSON
        body = None

    if not yookassa.is_enabled():
        logger.error("yookassa/webhook: интеграция не настроена — уведомление пропущено")
        return {"ok": True}

    event, payment_id = yookassa.resolve_notification(body)
    if event != "payment.succeeded":
        logger.info("yookassa/webhook: событие %r — доступ не выдаём", event)
        return {"ok": True}
    if not payment_id:
        logger.warning("yookassa/webhook: уведомление без id платежа — пропуск")
        return {"ok": True}

    # 2) ИСТОЧНИК ИСТИНЫ — платёж, запрошенный по API. Сбой связи -> 500,
    #    чтобы ЮKassa повторила уведомление позже. Запрос сетевой и
    #    блокирующий, поэтому уводим его в threadpool (не морозим event loop).
    try:
        payment = await run_in_threadpool(yookassa.fetch_payment, payment_id)
    except RuntimeError as exc:
        logger.error("yookassa/webhook: не проверили платёж %s: %s", payment_id, exc)
        raise HTTPException(status_code=500, detail="payment check failed, retry")

    if not yookassa.is_success(payment):
        logger.info(
            "yookassa/webhook: платёж %s не оплачен (status=%s paid=%s) — доступ не выдан",
            payment_id, payment.get("status"), payment.get("paid"),
        )
        return {"ok": True}

    # 3) Тестовые платежи в проде доступ не открывают.
    if yookassa.is_test(payment) and not DEV_MODE:
        logger.warning("yookassa/webhook: тестовый платёж %s в проде — доступ не выдан", payment_id)
        return {"ok": True}

    # 4) Кому и что активировать — ТОЛЬКО из metadata проверенного платежа.
    telegram_id, tariff = yookassa.payment_metadata(payment)
    if not telegram_id or not tariff:
        logger.error(
            "yookassa/webhook: платёж %s без корректной metadata (tid=%s tariff=%s)",
            payment_id, telegram_id, tariff,
        )
        telegram_bot._alert_owner_payment(
            f"ЮKassa: оплата {payment_id} без данных получателя — нужен ручной разбор"
        )
        return {"ok": True}

    if tariff not in config.TARIFFS:
        logger.error(
            "yookassa/webhook: платёж %s с неизвестным тарифом %r — доступ не выдан",
            payment_id, tariff,
        )
        return {"ok": True}

    # 5) СВЕРКА СУММЫ на сервере: metadata задаём мы, но платёж мог быть создан
    #    на другую сумму — без этой проверки 1 ₽ закрывал бы любой тариф.
    #    Ожидаемая цена — та, что зафиксирована в metadata при создании
    #    (смена прайса между созданием и оплатой не должна «съесть» платёж).
    amount_obj = payment.get("amount") if isinstance(payment.get("amount"), dict) else {}
    if not yookassa.amount_matches_tariff(tariff, amount_obj, yookassa.metadata_price(payment)):
        logger.error(
            "yookassa/webhook: сумма %s %s не соответствует тарифу %s (платёж %s) — доступ НЕ выдан",
            amount_obj.get("value"), amount_obj.get("currency"), tariff, payment_id,
        )
        telegram_bot._alert_owner_payment(
            f"ЮKassa: платёж {payment_id} на {amount_obj.get('value')} "
            f"{amount_obj.get('currency')} не совпал с тарифом {tariff}"
        )
        return {"ok": True}

    # 6) Идемпотентность: ЮKassa повторяет уведомление до получения 200.
    #    Ключ — из id, который вернул API (канонический), а не из тела
    #    уведомления: разные написания одного id не должны давать разные ключи.
    real_id = yookassa.canonical_id(payment)
    if not real_id or real_id != payment_id:
        logger.error(
            "yookassa/webhook: id платежа в ответе API (%r) не совпал с уведомлением (%r) — доступ не выдан",
            real_id, payment_id,
        )
        return {"ok": True}
    charge_id = yookassa.build_charge_id(real_id)
    already = db.query(Payment).filter(Payment.charge_id == charge_id).first()
    if already is not None:
        logger.info("yookassa/webhook: платёж %s уже обработан", payment_id)
        return {"ok": True}

    # 7) Активация доступа. Сбой -> 500: оплата прошла, терять её нельзя.
    try:
        paid_amount = float(amount_obj.get("value"))
    except (TypeError, ValueError):
        paid_amount = None
    try:
        payment_providers.activate_premium(
            db, telegram_id, tariff, "yookassa",
            paid_amount, "RUB", charge_id=charge_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "yookassa/webhook: сбой активации (платёж=%s tid=%s тариф=%s): %s",
            payment_id, telegram_id, tariff, exc,
        )
        telegram_bot._alert_owner_payment(
            f"ЮKassa: НЕ активирован премиум после оплаты {payment_id} "
            f"(tid={telegram_id}, тариф={tariff}): {exc}"
        )
        raise HTTPException(status_code=500, detail="activation failed, retry")

    logger.info(
        "yookassa/webhook: премиум активирован tid=%s тариф=%s платёж=%s",
        telegram_id, tariff, payment_id,
    )

    # 8) Сообщение плательщику в бот — best-effort: сбой Telegram не повод
    #    просить ЮKassa о повторе (доступ уже выдан).
    try:
        text = telegram_bot._payment_success_text(
            telegram_bot._user_language(db, telegram_id)
        )
        await run_in_threadpool(
            telegram_bot._bot_api, "sendMessage",
            {"chat_id": telegram_id, "text": text},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("yookassa/webhook: не удалось уведомить плательщика: %s", exc)

    return {"ok": True}


# --------------------------------------------------------------------------- #
#  Трекинг веса и адаптивные калории (Этап 3) — ПРЕМИУМ
#
#  Пользователь вносит вес; по реальной динамике веса за ~2-3 недели против
#  среднего потребления калорий вычисляем фактическое поддержание и
#  корректируем дневную цель под цель диеты. Все три эндпоинта — премиум
#  (require_premium -> 402 без подписки) и объявлены ВЫШЕ app.mount.
# --------------------------------------------------------------------------- #
@app.post("/weight/add", response_model=WeightLogOut)
def weight_add(
    data: WeightAddIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> WeightLog:
    """Добавить замер веса (upsert по дате).

    Если за указанную дату у пользователя уже есть запись — обновляем её вес,
    иначе создаём новую. Так на одну дату приходится ровно один замер, что
    важно для корректного построения тренда и адаптивного расчёта.
    """
    existing = (
        db.query(WeightLog)
        .filter(
            WeightLog.telegram_id == user.telegram_id,
            WeightLog.date == data.date,
        )
        .first()
    )
    if existing is not None:
        # Запись за эту дату уже есть — просто обновляем вес.
        existing.weight = data.weight
        db.commit()
        db.refresh(existing)
        return existing

    # Новой даты ещё не было — создаём замер.
    log = WeightLog(
        telegram_id=user.telegram_id,
        date=data.date,
        weight=data.weight,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@app.get("/weight/history", response_model=WeightHistoryOut)
def weight_history(
    days: int = 90,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> WeightHistoryOut:
    """Вернуть историю веса за период: точки замеров, линию тренда и сводку.

    logs — фактические замеры за последние <days> дней по возрастанию даты;
    trend — сглаженная линия тренда (линейная регрессия) через adaptive.build_trend;
    latest — последний внесённый вес; change_kg — изменение веса за период
    (последний минус первый, округлённо).
    """
    # Защита от некорректных значений параметра.
    if days < 1:
        days = 1

    today = date_cls.today()
    start = today - timedelta(days=days - 1)
    start_str = start.isoformat()
    end_str = today.isoformat()

    rows = (
        db.query(WeightLog)
        .filter(
            WeightLog.telegram_id == user.telegram_id,
            WeightLog.date >= start_str,
            WeightLog.date <= end_str,
        )
        .order_by(WeightLog.date.asc(), WeightLog.id.asc())
        .all()
    )

    # Точки замеров для графика (по возрастанию даты).
    logs = [WeightPoint(date=r.date, weight=r.weight) for r in rows]

    # Линия тренда строится в adaptive.build_trend (чистая математика, без зависимостей).
    # Передаём ему список dict с полями date/weight, который он ожидает.
    trend_raw = adaptive.build_trend([{"date": r.date, "weight": r.weight} for r in rows])
    trend = [WeightPoint(date=p["date"], weight=p["weight"]) for p in trend_raw]

    # Последний вес и изменение за период (последний - первый).
    latest = rows[-1].weight if rows else None
    change_kg = round(rows[-1].weight - rows[0].weight, 1) if len(rows) >= 2 else None

    return WeightHistoryOut(
        logs=logs,
        trend=trend,
        latest=latest,
        change_kg=change_kg,
    )


@app.post("/calories/recalculate-adaptive", response_model=AdaptiveResultOut)
def calories_recalculate_adaptive(
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> AdaptiveResultOut:
    """Пересчитать дневную цель по фактической динамике веса (по кнопке).

    Делегируем всю логику adaptive.run_adaptive_recalc: он собирает замеры веса
    и средний калораж за окно, вычисляет фактическое поддержание и при достатке
    данных сохраняет новую дневную цель в профиль. Возвращаем результат расчёта
    (включая enough_data и пояснение) клиенту. Язык — из профиля пользователя.
    """
    result = adaptive.run_adaptive_recalc(db, user, lang=user.language)
    # run_adaptive_recalc всегда возвращает dict с полем enough_data и понятным
    # пояснением (даже при ошибке/нехватке данных), поэтому собираем ответ мягко.
    return AdaptiveResultOut(
        enough_data=bool(result.get("enough_data", False)),
        maintenance=result.get("maintenance"),
        new_goal=result.get("new_goal"),
        weekly_change_kg=result.get("weekly_change_kg"),
        avg_intake=result.get("avg_intake"),
        days_used=result.get("days_used", 0),
        explanation=result.get("explanation", ""),
    )


# --------------------------------------------------------------------------- #
#  Шаблоны питания (Этап 4) — ПРЕМИУМ
#
#  Пользователь сохраняет набор блюд (одно блюдо / приём / целый день) как
#  шаблон и быстро применяет его к выбранной дате, создавая записи дневника.
#  Отдельно — быстрое копирование вчерашнего дня. Все эндпоинты премиум
#  (require_premium -> 402 без подписки) и объявлены ВЫШЕ app.mount.
# --------------------------------------------------------------------------- #
def _parse_template_items(items_json: str | None) -> list[TemplateItem]:
    """Распарсить items_json шаблона в список TemplateItem, устойчиво к битым данным.

    Если JSON повреждён или это не список — возвращаем пустой список, чтобы
    кривые данные одного шаблона не валили выдачу всего списка. Каждое блюдо
    разбираем мягко: недостающие/нечисловые КБЖУ заменяем нулями.
    """
    if not items_json:
        return []
    try:
        raw = json.loads(items_json)
    except (TypeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []

    items: list[TemplateItem] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        try:
            items.append(
                TemplateItem(
                    dish_name=str(it.get("dish_name") or ""),
                    calories=int(it.get("calories") or 0),
                    proteins=float(it.get("proteins") or 0.0),
                    fats=float(it.get("fats") or 0.0),
                    carbs=float(it.get("carbs") or 0.0),
                    meal_type=it.get("meal_type") or None,
                )
            )
        except (TypeError, ValueError):
            # Одно битое блюдо не должно ломать весь шаблон — просто пропускаем.
            continue
    return items


@app.post("/template/save", response_model=TemplateOut)
def template_save(
    data: TemplateSaveIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TemplateOut:
    """Сохранить шаблон питания текущего пользователя.

    Список блюд (items) сериализуем в JSON-строку (items_json). В ответ
    возвращаем шаблон с уже распарсенными блюдами.
    """
    # Сериализуем блюда в JSON-строку (ensure_ascii=False — храним кириллицу как есть).
    items_payload = [it.model_dump() for it in data.items]
    items_json = json.dumps(items_payload, ensure_ascii=False)

    template = MealTemplate(
        telegram_id=user.telegram_id,
        name=data.name,
        template_type=data.template_type,
        meal_type=data.meal_type,
        items_json=items_json,
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    return TemplateOut(
        id=template.id,
        name=template.name,
        template_type=template.template_type,
        meal_type=template.meal_type,
        items=_parse_template_items(template.items_json),
    )


@app.get("/template/list", response_model=TemplateListOut)
def template_list(
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> TemplateListOut:
    """Вернуть шаблоны питания пользователя (новые сверху).

    items_json каждого шаблона разворачиваем в список блюд устойчиво к битым
    данным (повреждённый JSON -> пустой список блюд).
    """
    rows = (
        db.query(MealTemplate)
        .filter(MealTemplate.telegram_id == user.telegram_id)
        .order_by(MealTemplate.created_at.desc(), MealTemplate.id.desc())
        .all()
    )
    items = [
        TemplateOut(
            id=t.id,
            name=t.name,
            template_type=t.template_type,
            meal_type=t.meal_type,
            items=_parse_template_items(t.items_json),
        )
        for t in rows
    ]
    return TemplateListOut(items=items)


@app.post("/template/apply/{template_id}")
def template_apply(
    template_id: int,
    data: TemplateApplyIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> dict:
    """Применить шаблон к указанной дате: создать записи дневника по его блюдам.

    Берём шаблон, ТОЛЬКО если он принадлежит пользователю (иначе 404). Для
    каждого блюда тип приёма пищи определяем по приоритету:
      item.meal_type (для day-шаблона — у каждого блюда) -> data.meal_type
      -> meal_type самого шаблона -> "snack" (на крайний случай).
    Возвращаем число добавленных записей.
    """
    template = (
        db.query(MealTemplate)
        .filter(MealTemplate.id == template_id)
        .first()
    )
    if template is None or template.telegram_id != user.telegram_id:
        # Чужой или несуществующий шаблон прячем за 404.
        raise HTTPException(status_code=404, detail="Шаблон не найден")

    items = _parse_template_items(template.items_json)

    added = 0
    for it in items:
        # Приём пищи: у блюда (day) -> переопределение запроса -> у шаблона -> snack.
        meal_type = (
            it.meal_type
            or data.meal_type
            or template.meal_type
            or "snack"
        )
        db_entry = DiaryEntry(
            telegram_id=user.telegram_id,
            date=data.date,
            meal_type=meal_type,
            dish_name=it.dish_name,
            calories=it.calories,
            proteins=it.proteins,
            fats=it.fats,
            carbs=it.carbs,
        )
        db.add(db_entry)
        added += 1

    db.commit()
    return {"added": added}


@app.delete("/template/{template_id}")
def template_delete(
    template_id: int,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> dict:
    """Удалить шаблон питания, если он принадлежит текущему пользователю."""
    template = (
        db.query(MealTemplate)
        .filter(MealTemplate.id == template_id)
        .first()
    )
    if template is None or template.telegram_id != user.telegram_id:
        # Чужой или несуществующий шаблон прячем за 404.
        raise HTTPException(status_code=404, detail="Шаблон не найден")

    db.delete(template)
    db.commit()
    return {"ok": True}


@app.post("/diary/copy-yesterday")
def diary_copy_yesterday(
    data: CopyYesterdayIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> dict:
    """Скопировать все записи дневника со вчерашнего дня на указанную дату.

    «Вчера» вычисляется как (data.date - 1 день) по ISO-дате. Для каждой записи
    создаём новую с теми же полями (блюдо, КБЖУ, приём пищи). Возвращаем число
    добавленных записей. Если дата некорректна — 400.
    """
    # Вычисляем дату «вчера» относительно целевой даты.
    try:
        target = date_cls.fromisoformat(data.date)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректная дата (нужен формат YYYY-MM-DD)")
    yesterday_str = (target - timedelta(days=1)).isoformat()

    # Берём все записи пользователя за «вчера».
    rows = (
        db.query(DiaryEntry)
        .filter(
            DiaryEntry.telegram_id == user.telegram_id,
            DiaryEntry.date == yesterday_str,
        )
        .order_by(DiaryEntry.created_at.asc(), DiaryEntry.id.asc())
        .all()
    )

    added = 0
    for e in rows:
        db_entry = DiaryEntry(
            telegram_id=user.telegram_id,
            date=data.date,
            meal_type=e.meal_type,
            dish_name=e.dish_name,
            calories=e.calories,
            proteins=e.proteins,
            fats=e.fats,
            carbs=e.carbs,
        )
        db.add(db_entry)
        added += 1

    db.commit()
    return {"added": added}


# --------------------------------------------------------------------------- #
#  AI-функции (Этап 5) — ПРЕМИУМ
#
#  Недельный AI-отчёт с инсайтами, AI-планировщик меню (день/неделя) и умные
#  предложения еды/перекусов. Все эндпоинты премиум (require_premium -> 402 без
#  подписки) и объявлены ВЫШЕ app.mount. При сбое ИИ отдаём 502 (как в остальных
#  AI-роутах); при включённом DEBUG_AI добавляем причину и «сырой» ответ модели.
# --------------------------------------------------------------------------- #
@app.get("/report/weekly", response_model=WeeklyReportOut)
def report_weekly(
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> WeeklyReportOut:
    """Собрать статистику за последние 7 дней и сгенерировать AI-отчёт с инсайтами.

    Статистику считаем на бэкенде (средние калории/БЖУ по дням с записями,
    кол-во залогированных дней, тренировки и сожжённое, изменение веса, средний
    дефицит и тренд калорий «первая половина недели против второй»), затем
    передаём её в ai.generate_weekly_report. При сбое ИИ -> 502.
    """
    today = date_cls.today()
    start = today - timedelta(days=6)  # 7 календарных дней включительно
    start_str = start.isoformat()
    end_str = today.isoformat()

    # --- Питание: суммы по дням, затем средние по дням С ЗАПИСЯМИ ---
    diary_rows = (
        db.query(DiaryEntry)
        .filter(
            DiaryEntry.telegram_id == user.telegram_id,
            DiaryEntry.date >= start_str,
            DiaryEntry.date <= end_str,
        )
        .all()
    )

    # Накапливаем КБЖУ по каждой дате (чтобы усреднять именно по дням, а не записям).
    per_day: dict[str, dict[str, float]] = {}
    for r in diary_rows:
        d = per_day.setdefault(
            r.date, {"calories": 0.0, "proteins": 0.0, "fats": 0.0, "carbs": 0.0}
        )
        d["calories"] += r.calories or 0
        d["proteins"] += r.proteins or 0.0
        d["fats"] += r.fats or 0.0
        d["carbs"] += r.carbs or 0.0

    days_logged = len(per_day)
    if days_logged > 0:
        avg_calories = round(sum(v["calories"] for v in per_day.values()) / days_logged)
        avg_proteins = round(sum(v["proteins"] for v in per_day.values()) / days_logged, 1)
        avg_fats = round(sum(v["fats"] for v in per_day.values()) / days_logged, 1)
        avg_carbs = round(sum(v["carbs"] for v in per_day.values()) / days_logged, 1)
    else:
        avg_calories = 0
        avg_proteins = avg_fats = avg_carbs = 0.0

    # Тренд калорий: средняя за первую половину недели против второй (по датам).
    # Чёткая граница — середина 7-дневного окна (3 дня : 4 дня).
    mid = start + timedelta(days=3)
    mid_str = mid.isoformat()
    first_half = [v["calories"] for d, v in per_day.items() if d < mid_str]
    second_half = [v["calories"] for d, v in per_day.items() if d >= mid_str]
    if first_half and second_half:
        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)
        diff = avg_second - avg_first
        # Небольшие колебания считаем стабильностью, чтобы не выдумывать тренд.
        if diff > 75:
            calories_trend = "up"
        elif diff < -75:
            calories_trend = "down"
        else:
            calories_trend = "stable"
    else:
        # Недостаточно данных в одной из половин — тренд неизвестен.
        calories_trend = "unknown"

    # --- Тренировки за неделю: количество и суммарно сожжено ---
    workouts = (
        db.query(Workout)
        .filter(
            Workout.telegram_id == user.telegram_id,
            Workout.date >= start_str,
            Workout.date <= end_str,
        )
        .all()
    )
    workouts_count = len(workouts)
    total_burned = sum((w.calories_burned or 0) for w in workouts)

    # --- Вес: изменение за период (последний минус первый замер) ---
    weight_rows = (
        db.query(WeightLog)
        .filter(
            WeightLog.telegram_id == user.telegram_id,
            WeightLog.date >= start_str,
            WeightLog.date <= end_str,
        )
        .order_by(WeightLog.date.asc(), WeightLog.id.asc())
        .all()
    )
    weight_change_kg = (
        round(weight_rows[-1].weight - weight_rows[0].weight, 1)
        if len(weight_rows) >= 2
        else None
    )

    # --- Цель и средний дефицит ---
    goal = user.daily_goal_kcal
    avg_deficit = (goal - avg_calories) if (goal and days_logged > 0) else None

    # Собранную статистику кладём в stats (отдаётся клиенту) и передаём в ИИ.
    stats = {
        "avg_calories": avg_calories,
        "goal": goal,
        "calories_trend": calories_trend,
        "avg_proteins": avg_proteins,
        "avg_fats": avg_fats,
        "avg_carbs": avg_carbs,
        "days_logged": days_logged,
        "workouts_count": workouts_count,
        "total_burned": total_burned,
        "weight_change_kg": weight_change_kg,
        "avg_deficit": avg_deficit,
    }

    try:
        ratelimit.enforce_ai(user.telegram_id)
        result = generate_weekly_report(stats, lang=user.language or "ru")
    except AIError as exc:
        logger.warning(
            "report/weekly: %s | finish=%s raw=%s",
            exc, exc.finish_reason, (exc.raw or "")[:600],
        )
        detail = "Не удалось сформировать недельный отчёт. Попробуйте позже."
        if DEBUG_AI:
            detail = f"{exc} | finish={exc.finish_reason} | raw={(exc.raw or 'пусто')[:1500]}"
        raise HTTPException(status_code=502, detail=detail)
    except RuntimeError as exc:
        logger.warning("report/weekly: %s", exc)
        detail = "Сервис отчётов временно недоступен. Попробуйте позже."
        if DEBUG_AI:
            detail = str(exc)
        raise HTTPException(status_code=502, detail=detail)

    # insights нормализуем в список строк (на случай нестрогого ответа модели).
    raw_insights = result.get("insights")
    insights = [str(s) for s in raw_insights if str(s).strip()] if isinstance(raw_insights, list) else []

    return WeeklyReportOut(
        summary=str(result.get("summary") or ""),
        insights=insights,
        focus=result.get("focus"),
        stats=stats,
    )


@app.post("/meal-plan/generate", response_model=MealPlanOut)
def meal_plan_generate(
    data: MealPlanIn,
    user: User = Depends(subscription.require_premium),
) -> MealPlanOut:
    """Сгенерировать AI-план меню на день или неделю под цель калорий/БЖУ.

    scope="day" — 1 день, "week" — 7 дней. Цель и целевые БЖУ берём из профиля
    (если цель не задана — 2000 ккал как разумный дефолт). Предпочтения и бюджет
    передаёт клиент. При сбое ИИ -> 502.
    """
    # Нормализуем scope: всё, кроме "week", трактуем как "day".
    scope = "week" if str(data.scope or "").strip().lower() == "week" else "day"

    # Тяжёлая генерация (особенно неделя, ~3500 токенов) — отдельный кулдаун.
    if scope == "week":
        ratelimit.enforce_heavy(user.telegram_id)
    else:
        ratelimit.enforce_ai(user.telegram_id)

    try:
        result = generate_meal_plan(
            scope=scope,
            daily_goal_kcal=user.daily_goal_kcal or 2000,
            target_proteins=user.target_proteins,
            target_fats=user.target_fats,
            target_carbs=user.target_carbs,
            diet_goal=getattr(user, "diet_goal", None),
            preferences=data.preferences,
            budget=data.budget,
            lang=user.language or "ru",
        )
    except AIError as exc:
        logger.warning(
            "meal-plan/generate: %s | finish=%s raw=%s",
            exc, exc.finish_reason, (exc.raw or "")[:600],
        )
        detail = "Не удалось составить план меню. Попробуйте позже."
        if DEBUG_AI:
            detail = f"{exc} | finish={exc.finish_reason} | raw={(exc.raw or 'пусто')[:1500]}"
        raise HTTPException(status_code=502, detail=detail)
    except RuntimeError as exc:
        logger.warning("meal-plan/generate: %s", exc)
        detail = "Сервис планировщика меню временно недоступен. Попробуйте позже."
        if DEBUG_AI:
            detail = str(exc)
        raise HTTPException(status_code=502, detail=detail)

    # Собираем дни плана; блюда группируем по приёмам пищи в той же форме.
    days: list[MealPlanDay] = []
    for d in result.get("days", []):
        if not isinstance(d, dict):
            continue
        meals_in = d.get("meals") if isinstance(d.get("meals"), dict) else {}
        meals_out: dict[str, list[MealPlanDish]] = {}
        for meal_key, dishes in meals_in.items():
            if not isinstance(dishes, list):
                continue
            dish_list: list[MealPlanDish] = []
            for dish in dishes:
                if not isinstance(dish, dict):
                    continue
                dish_list.append(
                    MealPlanDish(
                        dish_name=str(dish.get("dish_name") or ""),
                        calories=int(dish.get("calories") or 0),
                        proteins=float(dish.get("proteins") or 0.0),
                        fats=float(dish.get("fats") or 0.0),
                        carbs=float(dish.get("carbs") or 0.0),
                    )
                )
            meals_out[str(meal_key)] = dish_list
        days.append(MealPlanDay(label=str(d.get("label") or ""), meals=meals_out))

    shopping_list = [
        str(s) for s in result.get("shopping_list", []) if str(s).strip()
    ]

    return MealPlanOut(days=days, shopping_list=shopping_list)


@app.post("/meal-plan/regenerate-item", response_model=RegenerateItemOut)
def meal_plan_regenerate_item(
    data: RegenerateItemIn,
    user: User = Depends(subscription.require_premium),
) -> RegenerateItemOut:
    """Заменить одно блюдо в плане альтернативным под приём пищи и ~калории.

    Удобно, когда конкретное блюдо не нравится: возвращаем ровно одну замену.
    Цель диеты берём из профиля. При сбое ИИ -> 502.
    """
    try:
        ratelimit.enforce_ai(user.telegram_id)
        result = regenerate_meal_item(
            meal_type=data.meal_type,
            around_calories=data.around_calories,
            diet_goal=getattr(user, "diet_goal", None),
            preferences=data.preferences,
            lang=user.language or "ru",
        )
    except AIError as exc:
        logger.warning(
            "meal-plan/regenerate-item: %s | finish=%s raw=%s",
            exc, exc.finish_reason, (exc.raw or "")[:600],
        )
        detail = "Не удалось подобрать замену блюда. Попробуйте позже."
        if DEBUG_AI:
            detail = f"{exc} | finish={exc.finish_reason} | raw={(exc.raw or 'пусто')[:1500]}"
        raise HTTPException(status_code=502, detail=detail)
    except RuntimeError as exc:
        logger.warning("meal-plan/regenerate-item: %s", exc)
        detail = "Сервис планировщика меню временно недоступен. Попробуйте позже."
        if DEBUG_AI:
            detail = str(exc)
        raise HTTPException(status_code=502, detail=detail)

    return RegenerateItemOut(
        dish_name=str(result.get("dish_name") or ""),
        calories=int(result.get("calories") or 0),
        proteins=float(result.get("proteins") or 0.0),
        fats=float(result.get("fats") or 0.0),
        carbs=float(result.get("carbs") or 0.0),
    )


@app.post("/food/suggest", response_model=FoodSuggestOut)
def food_suggest(
    data: FoodSuggestIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> FoodSuggestOut:
    """Умное предложение еды под остаток КБЖУ, приём пищи и/или пожелание.

    Если задан meal_type — варианты под этот приём; если задан free_text —
    учитываем пожелание пользователя. Все варианты должны влезать в остаток КБЖУ.
    Цель диеты берём из профиля. При сбое ИИ -> 502.

    Дополнительно (ТЗ AI-тренера §4.6): если на дату есть тренировка (план или
    факт), её контекст уходит в промпт и возвращается фронту в training_note.
    """
    training_context = trainer_logic.today_training_context(
        db, user.telegram_id, data.date or date_cls.today().isoformat(), user.language or "ru"
    )
    try:
        ratelimit.enforce_ai(user.telegram_id)
        result = suggest_food(
            meal_type=data.meal_type,
            free_text=data.free_text,
            remaining_calories=data.remaining_calories,
            remaining_proteins=data.remaining_proteins,
            remaining_fats=data.remaining_fats,
            remaining_carbs=data.remaining_carbs,
            diet_goal=getattr(user, "diet_goal", None),
            lang=user.language or "ru",
            training_context=training_context,
        )
    except AIError as exc:
        logger.warning(
            "food/suggest: %s | finish=%s raw=%s",
            exc, exc.finish_reason, (exc.raw or "")[:600],
        )
        detail = "Не удалось подобрать предложения еды. Попробуйте позже."
        if DEBUG_AI:
            detail = f"{exc} | finish={exc.finish_reason} | raw={(exc.raw or 'пусто')[:1500]}"
        raise HTTPException(status_code=502, detail=detail)
    except RuntimeError as exc:
        logger.warning("food/suggest: %s", exc)
        detail = "Сервис предложений еды временно недоступен. Попробуйте позже."
        if DEBUG_AI:
            detail = str(exc)
        raise HTTPException(status_code=502, detail=detail)

    suggestions = [
        RecommendItem(
            dish_name=s["dish_name"],
            calories=s["calories"],
            proteins=s["proteins"],
            fats=s["fats"],
            carbs=s["carbs"],
            reason=s["reason"],
        )
        for s in result.get("suggestions", [])
    ]
    return FoodSuggestOut(suggestions=suggestions, training_note=training_context)


@app.get("/food/healthy-snacks", response_model=HealthySnacksOut)
def food_healthy_snacks(
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> HealthySnacksOut:
    """Подобрать низкокалорийные перекусы-«вкусняшки» под остаток калорий на сегодня.

    Остаток считаем как (дневная цель − съедено сегодня), не меньше нуля и целым.
    Если цель не задана — берём разумный дефолт 2000 ккал. При сбое ИИ -> 502.
    """
    # Считаем съеденное за сегодня, чтобы вычислить остаток калорий.
    today_str = date_cls.today().isoformat()
    eaten_today = (
        db.query(DiaryEntry)
        .filter(
            DiaryEntry.telegram_id == user.telegram_id,
            DiaryEntry.date == today_str,
        )
        .all()
    )
    consumed = sum((e.calories or 0) for e in eaten_today)
    goal = user.daily_goal_kcal or 2000
    # Остаток — целое и не отрицательное (на крайний случай съели больше цели).
    remaining = max(0, int(goal - consumed))

    try:
        ratelimit.enforce_ai(user.telegram_id)
        result = healthy_snacks(remaining, lang=user.language or "ru")
    except AIError as exc:
        logger.warning(
            "food/healthy-snacks: %s | finish=%s raw=%s",
            exc, exc.finish_reason, (exc.raw or "")[:600],
        )
        detail = "Не удалось подобрать полезные перекусы. Попробуйте позже."
        if DEBUG_AI:
            detail = f"{exc} | finish={exc.finish_reason} | raw={(exc.raw or 'пусто')[:1500]}"
        raise HTTPException(status_code=502, detail=detail)
    except RuntimeError as exc:
        logger.warning("food/healthy-snacks: %s", exc)
        detail = "Сервис подсказок временно недоступен. Попробуйте позже."
        if DEBUG_AI:
            detail = str(exc)
        raise HTTPException(status_code=502, detail=detail)

    suggestions = [
        RecommendItem(
            dish_name=s["dish_name"],
            calories=s["calories"],
            proteins=s["proteins"],
            fats=s["fats"],
            carbs=s["carbs"],
            reason=s["reason"],
        )
        for s in result.get("suggestions", [])
    ]
    return HealthySnacksOut(suggestions=suggestions)


# --------------------------------------------------------------------------- #
#  Трекинг цикла (Этап 6) — ПРЕМИУМ
#
#  Пользователь вводит дату начала последней менструации, среднюю длину цикла и
#  длительность менструации; бэкенд рассчитывает текущую фазу, день цикла, прогноз
#  следующей менструации и фертильное окно (backend/cycle.py). Данные приватны —
#  всё фильтруется по telegram_id текущего пользователя. Эндпоинты премиум
#  (require_premium -> 402 без подписки) и объявлены ВЫШЕ app.mount. Значения
#  ОРИЕНТИРОВОЧНЫЕ (не медицинская рекомендация) — дисклеймер показывает фронтенд.
# --------------------------------------------------------------------------- #
def _latest_cycle_log(db: Session, telegram_id: int) -> CycleLog | None:
    """Вернуть самую свежую запись цикла пользователя (или None)."""
    return (
        db.query(CycleLog)
        .filter(CycleLog.telegram_id == telegram_id)
        .order_by(CycleLog.created_at.desc(), CycleLog.id.desc())
        .first()
    )


def _build_cycle_status_out(log: CycleLog | None) -> CycleStatusOut:
    """Собрать ответ статуса цикла из записи БД (или пустой, если записи нет)."""
    if log is None:
        return CycleStatusOut(has_data=False)

    status = cycle.compute_status(
        log.cycle_start_date,
        cycle_length=log.cycle_length,
        period_length=log.period_length,
    )
    if status is None:
        # Дата в записи некорректна — считаем, что данных нет.
        return CycleStatusOut(has_data=False)

    return CycleStatusOut(
        has_data=True,
        cycle_start_date=status["cycle_start_date"],
        cycle_length=status["cycle_length"],
        period_length=status["period_length"],
        day_of_cycle=status["day_of_cycle"],
        phase=status["phase"],
        next_period_date=status["next_period_date"],
        days_until_next_period=status["days_until_next_period"],
        ovulation_date=status["ovulation_date"],
        fertile_start=status["fertile_start"],
        fertile_end=status["fertile_end"],
        notes=log.notes,
    )


@app.get("/cycle/status", response_model=CycleStatusOut)
def cycle_status(
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> CycleStatusOut:
    """Вернуть текущий статус цикла пользователя (по самой свежей записи).

    Если пользователь ещё ничего не вводил — has_data=False (остальные поля None).
    """
    log = _latest_cycle_log(db, user.telegram_id)
    return _build_cycle_status_out(log)


@app.post("/cycle/log", response_model=CycleStatusOut)
def cycle_log(
    data: CycleLogIn,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> CycleStatusOut:
    """Сохранить данные о цикле и вернуть пересчитанный статус.

    Дату начала валидируем строго (нужен формат YYYY-MM-DD); длину цикла и
    менструации приводим к разумному диапазону в backend/cycle.py. Каждая запись
    добавляется в историю (существующие не перезаписываем).
    """
    start = cycle.parse_date(data.cycle_start_date)
    if start is None:
        raise HTTPException(
            status_code=400, detail="Некорректная дата (нужен формат YYYY-MM-DD)"
        )

    # Приводим числовые параметры к валидному диапазону (или к значениям по умолчанию).
    cl = cycle.clamp_cycle_length(
        data.cycle_length if data.cycle_length is not None else cycle.DEFAULT_CYCLE_LENGTH
    )
    pl = cycle.clamp_period_length(
        data.period_length if data.period_length is not None else cycle.DEFAULT_PERIOD_LENGTH
    )

    notes = (data.notes or "").strip() or None

    entry = CycleLog(
        telegram_id=user.telegram_id,
        cycle_start_date=start.isoformat(),
        cycle_length=cl,
        period_length=pl,
        notes=notes,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    return _build_cycle_status_out(entry)


@app.delete("/cycle")
def cycle_reset(
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> dict:
    """Удалить все записи цикла пользователя (сброс данных трекера)."""
    deleted = (
        db.query(CycleLog)
        .filter(CycleLog.telegram_id == user.telegram_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"deleted": int(deleted or 0)}


# --------------------------------------------------------------------------- #
#  Фото-прогресс (Этап 7) — ПРЕМИУМ, ПРИВАТНО
#
#  Пользователь загружает фото прогресса с датой и (опц.) весом. ПРИВАТНОСТЬ:
#  файлы сохраняются в защищённый каталог (config.PROGRESS_PHOTOS_DIR) и НЕ
#  раздаются статикой — отдаются ТОЛЬКО через авторизованный эндпоинт
#  GET /progress/{id}/image с проверкой владельца (telegram_id). Публичных
#  ссылок на изображения не существует. Все эндпоинты премиум (402 без подписки)
#  и объявлены ВЫШЕ app.mount.
# --------------------------------------------------------------------------- #
# Разрешённые типы изображений и соответствующие расширения файлов.
_PROGRESS_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}


# Большая сторона снимка после пережатия. Хватает для просмотра на телефоне
# и для сравнения «до/после»; исходник в 12 МБ хранить незачем.
PROGRESS_IMAGE_MAX_SIDE = 1600


def _prepare_progress_image(data: bytes, mime: str) -> tuple[bytes, str]:
    """Пережать фото для хранения в БД: ≤1600px, JPEG 85, без метаданных.

    Пересохранение через Pillow заодно стирает EXIF — там бывают GPS-координаты
    и модель телефона, хранить это про пользователя незачем. HEIC/HEIF Pillow
    без плагина не читает — такие файлы храним как есть.
    """
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img.thumbnail((PROGRESS_IMAGE_MAX_SIDE, PROGRESS_IMAGE_MAX_SIDE))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85, optimize=True)
        return out.getvalue(), "image/jpeg"
    except Exception:
        if mime in ("image/heic", "image/heif"):
            return data, mime
        raise


def _mime_for_path(path: Path) -> str:
    """MIME по расширению старого файла на диске (для переноса в БД)."""
    ext = path.suffix.lower()
    for m, e in _PROGRESS_MIME_EXT.items():
        if e == ext:
            return m
    return "image/jpeg"


def _progress_dir() -> Path:
    """Абсолютный путь к каталогу приватных фото (создаёт его при необходимости)."""
    d = Path(config.PROGRESS_PHOTOS_DIR)
    if not d.is_absolute():
        d = Path(__file__).resolve().parent.parent / d
    d.mkdir(parents=True, exist_ok=True)
    return d


def _progress_photo_out(photo: ProgressPhoto) -> ProgressPhotoOut:
    """Собрать метаданные фото для клиента (без публичной ссылки на файл)."""
    return ProgressPhotoOut(
        id=photo.id,
        date=photo.date,
        weight=photo.weight,
        image_url=f"/progress/{photo.id}/image",
        created_at=photo.created_at.isoformat() if photo.created_at else None,
    )


@app.post("/progress/upload", response_model=ProgressPhotoOut)
async def progress_upload(
    file: UploadFile = File(...),
    date: str | None = Form(None),
    weight: float | None = Form(None),
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> ProgressPhotoOut:
    """Принять фото прогресса, сохранить в приватный каталог и создать запись.

    Дата снимка (date) по умолчанию — сегодня; вес (weight) необязателен. Тип
    файла проверяем по content-type; размер ограничен config.PROGRESS_PHOTO_MAX_BYTES.
    Возвращаем метаданные (без публичной ссылки — только путь авторизованной выдачи).
    """
    mime = (file.content_type or "").lower().split(";")[0].strip()
    ext = _PROGRESS_MIME_EXT.get(mime)
    if ext is None:
        raise HTTPException(
            status_code=400,
            detail="Неподдерживаемый формат. Загрузите изображение (JPG/PNG/WEBP).",
        )

    max_bytes = config.PROGRESS_PHOTO_MAX_BYTES
    data = await file.read(max_bytes + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл изображения")
    if len(data) > max_bytes:
        mb = max_bytes // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"Файл слишком большой (макс. {mb} МБ)")

    # Дата снимка: валидируем ISO; при отсутствии/ошибке — сегодня.
    snap_date = cycle.parse_date(date) if date else None
    if snap_date is None:
        snap_date = date_cls.today()

    # Фото уходит в БД, а не на диск: диск контейнера Railway эфемерный и
    # очищается при каждом деплое — люди теряли снимки. Пережатие — в
    # threadpool, чтобы большой файл не морозил остальные запросы.
    try:
        stored, stored_mime = await run_in_threadpool(_prepare_progress_image, data, mime)
    except Exception as exc:  # noqa: BLE001 — битый файл = ошибка клиента
        logger.warning("progress/upload: не удалось обработать изображение: %s", exc)
        raise HTTPException(
            status_code=400, detail="Не удалось прочитать изображение. Попробуйте другой файл."
        )

    photo = ProgressPhoto(
        telegram_id=user.telegram_id,
        photo_path=None,
        image_data=stored,
        image_mime=stored_mime,
        date=snap_date.isoformat(),
        weight=weight,
    )
    db.add(photo)
    db.commit()
    db.refresh(photo)

    return _progress_photo_out(photo)


@app.get("/progress/list", response_model=ProgressListOut)
def progress_list(
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> ProgressListOut:
    """Вернуть все фото прогресса пользователя по возрастанию даты (для таймлайна)."""
    rows = (
        db.query(ProgressPhoto)
        .filter(ProgressPhoto.telegram_id == user.telegram_id)
        .order_by(ProgressPhoto.date.asc(), ProgressPhoto.id.asc())
        .all()
    )
    return ProgressListOut(items=[_progress_photo_out(p) for p in rows])


@app.get("/progress/{photo_id}/image")
def progress_image(
    photo_id: int,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
):
    """Отдать файл фото прогресса — ТОЛЬКО владельцу (проверка telegram_id).

    Файлы лежат вне статики; сюда попадают лишь авторизованные запросы. Если фото
    принадлежит другому пользователю или отсутствует — 404 (без утечки факта).
    """
    from fastapi.responses import FileResponse, Response

    photo = (
        db.query(ProgressPhoto)
        .filter(
            ProgressPhoto.id == photo_id,
            ProgressPhoto.telegram_id == user.telegram_id,
        )
        .first()
    )
    if photo is None:
        raise HTTPException(status_code=404, detail="Фото не найдено")

    # Новые снимки лежат в БД.
    if photo.image_data:
        return Response(
            content=bytes(photo.image_data),
            media_type=photo.image_mime or "image/jpeg",
            headers={"Cache-Control": "private, no-store"},
        )

    # Старые снимки — на диске. Пока файл ещё жив, переносим его в БД:
    # следующий деплой сотрёт диск, а запись в БД останется.
    path = _progress_dir() / (photo.photo_path or "")
    if photo.photo_path and path.exists():
        try:
            photo.image_data = path.read_bytes()
            photo.image_mime = _mime_for_path(path)
            db.commit()
        except Exception as exc:  # noqa: BLE001 — перенос best-effort
            db.rollback()
            logger.warning("progress/image: не удалось перенести %s в БД: %s", photo.photo_path, exc)

    if not photo.photo_path or not path.exists():
        # Файл пропал (например, эфемерный диск после передеплоя без тома) —
        # чистим осиротевшую запись, чтобы список не показывал «битые» фото.
        try:
            db.delete(photo)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        raise HTTPException(status_code=410, detail="Файл фото недоступен")

    # Приватный ответ: запрещаем кеширование на общих узлах.
    return FileResponse(
        str(path),
        headers={"Cache-Control": "private, no-store"},
    )


@app.delete("/progress/{photo_id}")
def progress_delete(
    photo_id: int,
    user: User = Depends(subscription.require_premium),
    db: Session = Depends(get_db),
) -> dict:
    """Удалить фото прогресса (запись в БД и файл на диске) — только своё."""
    photo = (
        db.query(ProgressPhoto)
        .filter(
            ProgressPhoto.id == photo_id,
            ProgressPhoto.telegram_id == user.telegram_id,
        )
        .first()
    )
    if photo is None:
        raise HTTPException(status_code=404, detail="Фото не найдено")

    # Старые снимки лежали на диске — подчищаем файл (ошибка не фатальна).
    try:
        fpath = _progress_dir() / photo.photo_path if photo.photo_path else None
        if fpath is not None and fpath.exists():
            fpath.unlink()
    except OSError as exc:
        logger.warning("progress/delete: не удалось удалить файл %s: %s", photo.photo_path, exc)

    db.delete(photo)
    db.commit()
    return {"deleted": 1}


# --------------------------------------------------------------------------- #
#  Удаление данных пользователя (право на забвение / «начать заново»)
# --------------------------------------------------------------------------- #
@app.delete("/account/data")
def account_delete_data(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Полностью удалить персональные данные текущего пользователя.

    Удаляются ВСЕ записи пользователя (дневник, тренировки, спортпит, напоминания,
    вес, шаблоны, цикл, фото-прогресс) и сам профиль. Операция НЕОБРАТИМА; активная
    подписка при этом прекращается (возврат средств не выполняется).

    Финансовые записи (payments / pro_grants) НЕ удаляются: они нужны для
    бухгалтерского учёта и разбора спорных платежей и не содержат данных о здоровье.

    После удаления при следующем входе создаётся чистый профиль (онбординг заново).
    """
    tid = user.telegram_id

    # 1. Файлы фото-прогресса удаляем с диска (записи в БД чистим ниже).
    photos = db.query(ProgressPhoto).filter(ProgressPhoto.telegram_id == tid).all()
    pdir = _progress_dir()
    for ph in photos:
        if not ph.photo_path:
            continue  # снимок в БД — уйдёт вместе с записью
        try:
            fp = pdir / ph.photo_path
            if fp.exists():
                fp.unlink()
        except OSError as exc:
            logger.warning(
                "account/delete: не удалось удалить файл %s: %s", ph.photo_path, exc
            )

    # 2. Связи напоминаний спортпита привязаны к reminder_id, а не к telegram_id —
    #    удаляем их по идентификаторам напоминаний текущего пользователя.
    rem_ids = [
        r.id
        for r in db.query(SupplementReminder.id)
        .filter(SupplementReminder.telegram_id == tid)
        .all()
    ]
    if rem_ids:
        db.query(SupplementReminderItem).filter(
            SupplementReminderItem.reminder_id.in_(rem_ids)
        ).delete(synchronize_session=False)

    # 3. Все таблицы, привязанные к telegram_id пользователя.
    for model in (
        DiaryEntry,
        Workout,
        Supplement,
        FavoriteFood,
        NotificationSettings,
        NotificationLog,
        TrainingReminder,
        SupplementReminder,
        WeightLog,
        MealTemplate,
        CycleLog,
        ProgressPhoto,
        # AI-тренер: все таблицы с telegram_id. TrainerExercise — глобальная
        # библиотека, она не персональная и не удаляется.
        TrainerProfile,
        TrainerProgram,
        TrainerProgramDay,
        TrainerSession,
        TrainerSessionExercise,
        TrainerSetLog,
        TrainerRecord,
        TrainerExerciseState,
        TrainerWeeklyReview,
        TrainerDailyTip,
    ):
        db.query(model).filter(model.telegram_id == tid).delete(
            synchronize_session=False
        )

    # 4. Сам профиль пользователя. При следующем входе будет создан заново (чистый).
    db.query(User).filter(User.telegram_id == tid).delete(synchronize_session=False)

    db.commit()
    return {"ok": True, "deleted": True}


# --------------------------------------------------------------------------- #
#  AI-тренер — отдельный роутер (backend/trainer.py), все маршруты премиумные.
#  Подключаем ВЫШЕ app.mount, иначе статика перехватит пути /trainer/*.
# --------------------------------------------------------------------------- #
app.include_router(trainer.router)


# --------------------------------------------------------------------------- #
#  Статика фронтенда — монтируется ПОСЛЕДНЕЙ, чтобы API-маршруты были в приоритете
# --------------------------------------------------------------------------- #
frontend_dir = Path(__file__).resolve().parent.parent / "frontend"


# index.html отдаём с no-cache, чтобы Telegram не кешировал старую «оболочку» и
# всегда видел свежие ссылки на ассеты (css/js?v=...). Сами ассеты кешируются по
# версии в query — это лечит «приложение не обновляется после деплоя».
@app.get("/", include_in_schema=False)
def serve_index():
    from fastapi.responses import FileResponse

    return FileResponse(
        str(frontend_dir / "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="static")
