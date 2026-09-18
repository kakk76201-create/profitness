"""
ORM-модели приложения (SQLAlchemy).

Базовые таблицы:
  - User        — пользователь Telegram и его профиль (для расчёта нормы калорий);
  - DiaryEntry  — запись в дневнике питания (один приём пищи / блюдо).

Расширенные таблицы (фитнес, спортпит, уведомления, избранное):
  - Workout              — тренировка пользователя и сожжённые калории;
  - Supplement           — спортивное питание / добавки пользователя;
  - NotificationSettings — настройки пуш-уведомлений пользователя
                           (теперь только приёмы пищи и вечерняя сводка);
  - FavoriteFood         — сохранённые/недавние блюда пользователя;
  - NotificationLog      — журнал отправленных уведомлений (для дедупликации);
  - TrainingReminder       — гибкое напоминание о тренировке (дни недели + время);
  - SupplementReminder     — напоминание о приёме спортпита (метка + время);
  - SupplementReminderItem — связь напоминания спортпита с конкретной добавкой.

Таблицы подписки и доступа (Этап 1):
  - Payment   — журнал успешных платежей за подписку (карта / Tribute и т.п.);
  - ProGrant  — журнал ручной выдачи/отзыва доступа владельцем (через бота).

Трекинг веса и адаптивные калории (Этап 3):
  - WeightLog — замер веса пользователя за конкретный день
                (для построения тренда и расчёта фактического поддержания).

Шаблоны питания (Этап 4):
  - MealTemplate — сохранённый шаблон (блюдо / приём / целый день),
                   из которого пользователь быстро добавляет записи в дневник.

Трекинг цикла (Этап 6):
  - CycleLog — запись о менструальном цикле (дата начала, длина цикла и
               менструации) для расчёта текущей фазы и прогнозов.

Фото-прогресс (Этап 7):
  - ProgressPhoto — приватное фото прогресса (имя файла на диске, дата, вес);
                    файлы отдаются только через авторизованный эндпоинт.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)

from backend.database import Base


class User(Base):
    """Пользователь Telegram и параметры его профиля."""

    __tablename__ = "users"

    # Telegram ID — первичный ключ. Без автоинкремента: значение задаём сами.
    telegram_id = Column(BigInteger, primary_key=True, autoincrement=False)

    # Данные из Telegram (могут отсутствовать).
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    photo_url = Column(String, nullable=True)

    # E-mail для чеков ЮKassa (54-ФЗ). Telegram его не отдаёт — человек
    # вводит сам на странице оплаты, и второй раз мы не спрашиваем.
    email = Column(String, nullable=True)

    # Язык интерфейса/сообщений пользователя: "ru" | "en".
    # Стартовое значение задаётся при первом входе по Telegram language_code:
    # "ru" если код начинается с "ru", иначе "en". Пользователь может сменить
    # язык вручную (через /profile), поэтому заданное значение не перетирается.
    language = Column(String, nullable=True)

    # Физические параметры для формулы Миффлина — Сан Жеора.
    weight = Column(Float, nullable=True)   # вес, кг
    height = Column(Float, nullable=True)   # рост, см
    age = Column(Integer, nullable=True)    # возраст, лет

    # Пол: "male" | "female" — нужен для формулы расчёта BMR. По умолчанию НЕ
    # задаём (NULL): авто-расчёт цели включается только когда пол выбран явно,
    # иначе женщинам цель считалась бы по мужской формуле (+166 ккал завышение).
    gender = Column(String, nullable=True)

    # Коэффициент активности для расчёта суточной нормы (TDEE).
    activity_level = Column(Float, nullable=True, default=1.375)

    # Целевая суточная норма калорий (ккал).
    daily_goal_kcal = Column(Integer, nullable=True)

    # --- Новые поля цели по питанию (добавлены поверх существующей таблицы) ---
    # Цель диеты: "loss" (похудение) | "maintain" (поддержание) | "gain" (набор).
    diet_goal = Column(String, nullable=True, default="maintain")

    # Целевые нормы БЖУ (граммы) — рассчитываются вместе с дневной нормой калорий.
    target_proteins = Column(Float, nullable=True)  # белки, г
    target_fats = Column(Float, nullable=True)      # жиры, г
    target_carbs = Column(Float, nullable=True)     # углеводы, г

    # Выбранная пользователем цель улучшения для AI-советов по спортпиту
    # (например: "сон", "восстановление", "сила", "энергия", "иммунитет"
    # или произвольный текст). Может отсутствовать.
    supplement_goal = Column(String, nullable=True)

    # --- Поля подписки и доступа (Этап 1, добавлены поверх таблицы) ---
    # Тип подписки: "free" | "trial" | "monthly" | "quarterly" | "yearly" | "lifetime".
    subscription_type = Column(String, nullable=True, default="free")

    # До какой даты (UTC) действует подписка. None — нет срока:
    # либо подписки нет (free), либо она пожизненная (lifetime).
    subscription_until = Column(DateTime, nullable=True)

    # Является ли пользователь владельцем приложения (определяется по OWNER_ID).
    # Владельцу всегда доступен premium-функционал.
    is_owner = Column(Boolean, nullable=True, default=False)

    # Счётчик использованных бесплатных сканирований за текущие сутки.
    daily_scans_used = Column(Integer, nullable=True, default=0)

    # Дата (ISO "YYYY-MM-DD"), к которой относится счётчик daily_scans_used.
    # При наступлении новых суток счётчик обнуляется.
    daily_scans_date = Column(String, nullable=True)

    # --- Поля адаптивных калорий (Этап 3, добавлены поверх таблицы) ---
    # Включён ли адаптивный пересчёт дневной цели по реальной динамике веса.
    adaptive_enabled = Column(Boolean, nullable=True, default=False)

    # Вычисленное фактическое поддержание (ккал/день) по последнему пересчёту.
    # None — пока не рассчитывалось (мало данных).
    calculated_maintenance = Column(Integer, nullable=True)

    # ISO-дата ("YYYY-MM-DD") последнего авто/ручного адаптивного пересчёта.
    # Используется планировщиком для дедупликации (не чаще раза в 7 дней).
    adaptive_last_calc = Column(String, nullable=True)

    # Использован ли одноразовый бесплатный пробный период (триал).
    used_trial = Column(Boolean, nullable=True, default=False)

    # Дата создания записи (UTC).
    created_at = Column(DateTime, default=datetime.utcnow)


class DiaryEntry(Base):
    """Одна запись дневника питания: блюдо в рамках приёма пищи за конкретный день."""

    __tablename__ = "diary_entries"

    # Идентификатор записи (автоинкремент).
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Владелец записи — ссылка на пользователя по Telegram ID.
    telegram_id = Column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )

    # Дата в формате ISO "YYYY-MM-DD" (с индексом для быстрых выборок по дню).
    date = Column(String, index=True)

    # Тип приёма пищи: breakfast | lunch | dinner | snack.
    meal_type = Column(String)

    # Название блюда (на русском).
    dish_name = Column(String)

    # Пищевая ценность порции.
    calories = Column(Integer)   # калории, ккал
    proteins = Column(Float)     # белки, г
    fats = Column(Float)         # жиры, г
    carbs = Column(Float)        # углеводы, г

    # Количество и единица измерения (добавлены поверх таблицы, nullable).
    quantity = Column(Float, nullable=True)   # число (2, 100, ...)
    unit = Column(String, nullable=True)      # канонический ключ: pcs | g | ml | serving

    # Дата создания записи (UTC).
    created_at = Column(DateTime, default=datetime.utcnow)


class Workout(Base):
    """Тренировка пользователя за конкретный день и сожжённые калории."""

    __tablename__ = "workouts"

    # Идентификатор тренировки (автоинкремент).
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Владелец записи — ссылка на пользователя по Telegram ID.
    telegram_id = Column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )

    # Дата в формате ISO "YYYY-MM-DD" (с индексом для выборок по дню).
    date = Column(String, index=True)

    # Тип тренировки: cardio | strength | walking | yoga | other.
    type = Column(String)

    # Длительность тренировки, минуты.
    duration_min = Column(Integer)

    # Сожжённые калории, ккал.
    calories_burned = Column(Integer)

    # Свободное описание активности для type="other" (например, "скалолазание").
    # Используется AI-оценкой сожжённых калорий; для остальных типов обычно None.
    description = Column(String, nullable=True)

    # Дата создания записи (UTC).
    created_at = Column(DateTime, default=datetime.utcnow)


class Supplement(Base):
    """Спортивное питание / добавка пользователя."""

    __tablename__ = "supplements"

    # Идентификатор добавки (автоинкремент).
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Владелец записи — ссылка на пользователя по Telegram ID.
    telegram_id = Column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )

    # Название добавки (например, "Креатин").
    name = Column(String)

    # Тип добавки (например, "протеин", "витамины").
    type = Column(String)

    # Дозировка (например, "5 г").
    dosage = Column(String)

    # Время приёма в формате "HH:MM" (может отсутствовать).
    intake_time = Column(String, nullable=True)

    # Включено ли напоминание о приёме.
    reminder_enabled = Column(Boolean, default=False)

    # Дата создания записи (UTC).
    created_at = Column(DateTime, default=datetime.utcnow)


class NotificationSettings(Base):
    """
    Настройки пуш-уведомлений пользователя (одна строка на пользователя).

    ВАЖНО: теперь эта таблица используется ТОЛЬКО для приёмов пищи (meal_*)
    и вечерней сводки (daily_summary_*). Напоминания о тренировках и приёме
    спортпита переехали в отдельные таблицы TrainingReminder и
    SupplementReminder. Старые поля (training_*, supplement_reminder_enabled)
    НЕ удаляются, чтобы не потерять существующие данные пользователей.
    """

    __tablename__ = "notification_settings"

    # Первичный ключ — Telegram ID пользователя (одна строка на юзера).
    telegram_id = Column(
        BigInteger,
        ForeignKey("users.telegram_id"),
        primary_key=True,
        autoincrement=False,
    )

    # Напоминания о приёмах пищи.
    meal_reminder_enabled = Column(Boolean, default=False)
    breakfast_time = Column(String, default="09:00")  # время завтрака "HH:MM"
    lunch_time = Column(String, default="13:00")       # время обеда "HH:MM"
    dinner_time = Column(String, default="19:00")      # время ужина "HH:MM"

    # Произвольный список времён приёмов пищи в виде JSON-массива строк "HH:MM"
    # (заменяет фиксированные breakfast/lunch/dinner). Nullable: старые записи
    # без него трактуются как пустой список. Легаси-колонки выше не удаляются.
    meal_times_json = Column(Text, nullable=True)

    # Напоминание о тренировке.
    # (Устаревшее: оставлено для совместимости, переехало в TrainingReminder.)
    training_reminder_enabled = Column(Boolean, default=False)
    training_time = Column(String, default="18:00")    # время тренировки "HH:MM"

    # Напоминание о приёме спортпита.
    # (Устаревшее: оставлено для совместимости, переехало в SupplementReminder.)
    supplement_reminder_enabled = Column(Boolean, default=False)

    # Ежедневная сводка по питанию.
    daily_summary_enabled = Column(Boolean, default=False)
    summary_time = Column(String, default="21:00")     # время сводки "HH:MM"


class FavoriteFood(Base):
    """Сохранённое / недавнее блюдо пользователя (для быстрого повторного добавления)."""

    __tablename__ = "favorite_foods"

    # Идентификатор записи (автоинкремент).
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Владелец записи — ссылка на пользователя по Telegram ID.
    telegram_id = Column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )

    # Название блюда (на русском).
    dish_name = Column(String)

    # Пищевая ценность порции.
    calories = Column(Integer)   # калории, ккал
    proteins = Column(Float)     # белки, г
    fats = Column(Float)         # жиры, г
    carbs = Column(Float)        # углеводы, г

    # Дата создания записи (UTC).
    created_at = Column(DateTime, default=datetime.utcnow)


class NotificationLog(Base):
    """
    Журнал отправленных уведомлений.

    Используется для дедупликации: чтобы одно и то же уведомление
    (например, "завтрак") не отправлялось пользователю дважды за один день.
    """

    __tablename__ = "notification_log"

    # Идентификатор записи (автоинкремент).
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Кому отправлено — Telegram ID пользователя.
    telegram_id = Column(BigInteger, index=True)

    # Вид уведомления: "breakfast" | "lunch" | "dinner" | "summary" |
    # "trainrem:{id}" | "supprem:{id}" (и устаревшие "training" / "supplement:{id}").
    kind = Column(String)

    # Дата отправки в формате ISO "YYYY-MM-DD".
    date = Column(String)

    # Дата создания записи (UTC).
    created_at = Column(DateTime, default=datetime.utcnow)


class TrainingReminder(Base):
    """
    Гибкое напоминание о тренировке.

    Заменяет старые поля training_* в NotificationSettings: пользователь может
    задать несколько напоминаний, каждое со своим набором дней недели и временем.
    """

    __tablename__ = "training_reminders"

    # Идентификатор напоминания (автоинкремент).
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Владелец записи — ссылка на пользователя по Telegram ID.
    telegram_id = Column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )

    # Дни недели в виде CSV по нумерации Python (Пн=0 .. Вс=6),
    # например "0,2,4" — понедельник, среда, пятница.
    weekdays = Column(String)

    # Время напоминания в формате "HH:MM".
    time = Column(String)

    # Включено ли напоминание.
    enabled = Column(Boolean, default=True)

    # Дата создания записи (UTC).
    created_at = Column(DateTime, default=datetime.utcnow)


class SupplementReminder(Base):
    """
    Напоминание о приёме спортпита.

    Заменяет supplement_reminder_enabled в NotificationSettings и поля
    напоминаний у самих Supplement: пользователь задаёт метку (например,
    "Утро"/"Ночь"), время и список добавок к приёму (через SupplementReminderItem).
    """

    __tablename__ = "supplement_reminders"

    # Идентификатор напоминания (автоинкремент).
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Владелец записи — ссылка на пользователя по Telegram ID.
    telegram_id = Column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )

    # Метка напоминания ("Утро" / "Ночь" / произвольный текст).
    label = Column(String)

    # Время напоминания в формате "HH:MM".
    time = Column(String)

    # Включено ли напоминание.
    enabled = Column(Boolean, default=True)

    # Дата создания записи (UTC).
    created_at = Column(DateTime, default=datetime.utcnow)


class SupplementReminderItem(Base):
    """
    Связь напоминания спортпита с конкретной добавкой пользователя.

    Одно напоминание (SupplementReminder) может включать несколько добавок
    (Supplement); каждая такая связь — отдельная строка.
    """

    __tablename__ = "supplement_reminder_items"

    # Идентификатор связи (автоинкремент).
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Ссылка на напоминание спортпита.
    reminder_id = Column(
        Integer, ForeignKey("supplement_reminders.id"), index=True
    )

    # Ссылка на конкретную добавку пользователя.
    supplement_id = Column(Integer, ForeignKey("supplements.id"))


class Payment(Base):
    """
    Журнал успешных платежей за подписку.

    Сюда пишется каждая успешная оплата (карта, Tribute и т.п.) —
    для истории и аналитики. На доступ напрямую не влияет: доступ определяется
    полями подписки в таблице users, которые обновляются при активации.
    """

    __tablename__ = "payments"

    # Идентификатор платежа (автоинкремент).
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Telegram ID плательщика (с индексом для выборок по пользователю).
    telegram_id = Column(BigInteger, index=True)

    # Платёжный провайдер: "cloudpayments" | "yookassa" | "tribute" | "trial" |
    # "owner" (ручная выдача); "stars" — только у старых записей (оплата
    # звёздами отключена).
    provider = Column(String)

    # Сумма платежа (в единицах провайдера; для карты — рубли).
    amount = Column(Float)

    # Валюта платежа ("RUB" для оплаты картой; "XTR" в старых Stars-записях).
    currency = Column(String)

    # Какой тариф был оплачен: "monthly" | "quarterly" | "yearly" | "lifetime".
    subscription_type = Column(String)

    # Идентификатор списания провайдера ("cp:<id>" у CloudPayments, "yk:<id>"
    # у ЮKassa, telegram_payment_charge_id у старых Stars-платежей) —
    # для ИДЕМПОТЕНТНОСТИ: по нему отсекаем повторную доставку одного платежа,
    # чтобы подписка не продлевалась дважды. UNIQUE-индекс создаётся в миграции.
    charge_id = Column(String, nullable=True)

    # Дата создания записи (UTC).
    created_at = Column(DateTime, default=datetime.utcnow)


class ProGrant(Base):
    """
    Журнал ручной выдачи/отзыва premium-доступа владельцем приложения.

    Заполняется при выполнении команд бота /givepro и /revokepro (доступны
    строго владельцу по OWNER_ID). Служит для аудита действий владельца.
    """

    __tablename__ = "pro_grants"

    # Идентификатор записи (автоинкремент).
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Кто выдал/отозвал доступ — Telegram ID владельца (OWNER_ID).
    granted_by = Column(BigInteger)

    # Кому выдан/отозван доступ — Telegram ID целевого пользователя.
    granted_to = Column(BigInteger)

    # Действие: "give" (выдать) | "revoke" (отозвать).
    action = Column(String)

    # Дата создания записи (UTC).
    created_at = Column(DateTime, default=datetime.utcnow)


class PendingGrant(Base):
    """
    Отложенная выдача доступа по @username (когда человека ещё нет в базе).

    ЗАЧЕМ: Bot API Telegram НЕ умеет резолвить @username в telegram_id — узнать
    id человека можно только когда он сам написал боту или открыл приложение.
    Поэтому команда владельца «/givepro @username» для незнакомого пользователя
    раньше была тупиком. Теперь она записывается сюда и применяется
    АВТОМАТИЧЕСКИ при первом же появлении этого username (см.
    payment_providers.apply_pending_grants).

    days = None означает пожизненный доступ.
    """

    __tablename__ = "pending_grants"

    # Идентификатор записи (автоинкремент).
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Username цели в НИЖНЕМ регистре, без "@" (логины Telegram нечувствительны
    # к регистру, поэтому храним и сравниваем в одном виде).
    username_lower = Column(String, index=True)

    # На сколько дней выдать доступ. None — пожизненно.
    days = Column(Integer, nullable=True)

    # Кто поставил в очередь (telegram_id владельца).
    created_by = Column(BigInteger, nullable=True)

    # Когда применена (None — ещё ждёт). Применённые не удаляем: это аудит.
    applied_at = Column(DateTime, nullable=True)

    # Кому в итоге применена (telegram_id), заполняется при применении.
    applied_to = Column(BigInteger, nullable=True)

    # Дата создания записи (UTC).
    created_at = Column(DateTime, default=datetime.utcnow)


class WeightLog(Base):
    """
    Замер веса пользователя за конкретный день (Этап 3).

    На один день храним одну запись (логика upsert на уровне эндпоинта):
    повторный ввод за ту же дату обновляет существующий вес. По набору
    замеров строится линия тренда и вычисляется фактическое поддержание калорий.
    """

    __tablename__ = "weight_logs"

    # Идентификатор записи (автоинкремент).
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Владелец записи — ссылка на пользователя по Telegram ID.
    telegram_id = Column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )

    # Вес, кг.
    weight = Column(Float)

    # Дата замера в формате ISO "YYYY-MM-DD" (с индексом для выборок по периоду).
    date = Column(String, index=True)

    # Дата создания записи (UTC).
    created_at = Column(DateTime, default=datetime.utcnow)


class MealTemplate(Base):
    """
    Шаблон питания пользователя (Этап 4).

    Позволяет сохранить набор блюд (одно блюдо, целый приём пищи или целый день)
    и быстро применять его к выбранной дате, создавая записи дневника. Список
    блюд хранится сериализованным в JSON-строке (items_json), чтобы не плодить
    дополнительную таблицу-связку — для шаблонов этого достаточно.
    """

    __tablename__ = "meal_templates"

    # Идентификатор шаблона (автоинкремент).
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Владелец записи — ссылка на пользователя по Telegram ID.
    telegram_id = Column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )

    # Человекочитаемое имя шаблона (задаёт пользователь).
    name = Column(String)

    # Тип шаблона: "dish" (одно блюдо) | "meal" (приём пищи) | "day" (целый день).
    template_type = Column(String)

    # Тип приёма пищи по умолчанию (breakfast|lunch|dinner|snack) или None.
    # Для day-шаблона обычно None: приём берётся у каждого блюда отдельно.
    meal_type = Column(String, nullable=True)

    # Список блюд шаблона в виде JSON-строки (массив объектов с КБЖУ и meal_type).
    items_json = Column(Text)

    # Дата создания записи (UTC).
    created_at = Column(DateTime, default=datetime.utcnow)


class CycleLog(Base):
    """
    Запись о менструальном цикле пользователя (Этап 6).

    Хранит дату начала последней менструации, среднюю длину цикла и длительность
    менструации. По самой свежей записи (по created_at) рассчитываются текущий
    день цикла, фаза, прогноз следующей менструации и фертильное окно
    (см. backend/cycle.py). История записей сохраняется — это позволяет позже
    уточнять среднюю длину цикла и не терять введённые пользователем данные.

    Приватность: как и остальные персональные данные, доступно только владельцу
    (все эндпоинты фильтруют по telegram_id текущего пользователя).
    """

    __tablename__ = "cycle_logs"

    # Идентификатор записи (автоинкремент).
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Владелец записи — ссылка на пользователя по Telegram ID.
    telegram_id = Column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )

    # Дата начала последней менструации в формате ISO "YYYY-MM-DD".
    cycle_start_date = Column(String)

    # Средняя длина цикла в днях (обычно 21..35, по умолчанию 28).
    cycle_length = Column(Integer, default=28)

    # Длительность менструации в днях (обычно 3..7, по умолчанию 5).
    period_length = Column(Integer, default=5)

    # Необязательная заметка пользователя (самочувствие и т.п.).
    notes = Column(Text, nullable=True)

    # Дата создания записи (UTC).
    created_at = Column(DateTime, default=datetime.utcnow)


class ProgressPhoto(Base):
    """
    Приватное фото прогресса пользователя (Этап 7).

    ПРИВАТНОСТЬ: сами файлы НЕ раздаются статикой. В БД хранится только имя файла
    (photo_path) внутри защищённого каталога (config.PROGRESS_PHOTOS_DIR); отдаётся
    файл исключительно через авторизованный эндпоинт с проверкой владельца
    (telegram_id). Никаких публичных ссылок на изображения не существует.
    """

    __tablename__ = "progress_photos"

    # Идентификатор записи (автоинкремент).
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Владелец записи — ссылка на пользователя по Telegram ID.
    telegram_id = Column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )

    # Имя файла на диске — ТОЛЬКО у старых снимков; новые лежат в БД
    # (image_data), потому что диск контейнера Railway стирается при деплое.
    photo_path = Column(String, nullable=True)

    # Само изображение (пережатый JPEG без EXIF) и его MIME-тип.
    image_data = Column(LargeBinary, nullable=True)
    image_mime = Column(String, nullable=True)

    # Дата снимка в формате ISO "YYYY-MM-DD" (с индексом для сортировки по времени).
    date = Column(String, index=True)

    # Вес на момент снимка, кг (необязательно).
    weight = Column(Float, nullable=True)

    # Дата создания записи (UTC).
    created_at = Column(DateTime, default=datetime.utcnow)


# --------------------------------------------------------------------------- #
#  AI-тренер (docs/TRAINER_SPEC.md §3): 11 новых таблиц, ALTER'ов нет.
#  Все таблицы, кроме глобальной библиотеки упражнений, привязаны к пользователю
#  по telegram_id. Даты — строки ISO, вложенные структуры — JSON в Text.
# --------------------------------------------------------------------------- #


class TrainerExercise(Base):
    """Библиотека упражнений (глобальная, без telegram_id).

    Источник правды для ИИ: программа собирается только из slug'ов каталога.
    Техника/ошибки генерируются ИИ один раз на упражнение и язык и кэшируются
    в technique_json. Незнакомый slug от ИИ → строка с created_by_ai=True.
    """

    __tablename__ = "trainer_exercises"
    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String, unique=True, index=True)          # "db_bench_press"
    name_ru = Column(String); name_en = Column(String)
    muscle_group = Column(String, index=True)  # chest|back|shoulders|biceps|triceps|quads|hamstrings|glutes|calves|core|full_body|cardio|mobility
    secondary_muscles_json = Column(Text, nullable=True)     # ["triceps","shoulders"]
    equipment = Column(String, index=True)     # barbell|dumbbell|machine|cable|bodyweight|band|kettlebell|pullup_bar|bench|cardio_machine|none
    category = Column(String)                  # compound|isolation|cardio|mobility|stretch
    measure_type = Column(String)              # reps_weight|reps|time|distance
    difficulty = Column(Integer, default=1)    # 1..3
    is_unilateral = Column(Boolean, default=False)
    contraindications_json = Column(Text, nullable=True)     # ["knee","lower_back"] — коды ограничений
    alternatives_json = Column(Text, nullable=True)          # ["slug1","slug2"] ручные альтернативы
    progression_next_slug = Column(String, nullable=True)    # should: цепочка bodyweight
    technique_json = Column(Text, nullable=True)  # {"ru": {steps,cues,mistakes,breathing,safety,muscles_text}, "en": {...}}
    technique_status = Column(String, default="none")        # none|ready|failed
    created_by_ai = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TrainerProfile(Base):
    """Анкета тренера (1:1 с пользователем): цель, уровень, оборудование, график.

    Данные тела (вес/рост/пол/возраст), diet_goal и язык берутся из User —
    здесь не дублируются.
    """

    __tablename__ = "trainer_profiles"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), unique=True, index=True)
    goal = Column(String)                      # loss|muscle|strength|endurance|tone
    level = Column(String)                     # beginner|intermediate|advanced
    equipment = Column(String)                 # gym|home_dumbbells|bodyweight
    equipment_extra_json = Column(Text, nullable=True)       # ["pullup_bar","bands",...]
    days_per_week = Column(Integer)            # 2..6
    preferred_weekdays = Column(String)        # CSV "0,2,4" (Пн=0)
    session_minutes = Column(Integer)          # 20|30|45|60|75|90
    program_weeks = Column(Integer, default=6) # 4|6|8
    limitations_json = Column(Text, nullable=True)           # ["knee","lower_back"]
    limitations_text = Column(Text, nullable=True)
    focus_json = Column(Text, nullable=True)                 # ["glutes","core"]
    rest_default_sec = Column(Integer, default=90)
    reminder_enabled = Column(Boolean, default=False)
    reminder_time = Column(String, nullable=True)            # "HH:MM"
    reminder_id = Column(Integer, nullable=True)             # -> training_reminders.id (без FK)
    onboarding_completed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TrainerProgram(Base):
    """Программа тренировок: шаблон недели от ИИ + периодизация (снимок профиля)."""

    __tablename__ = "trainer_programs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    status = Column(String, index=True)        # active|completed|archived
    title = Column(String)
    split_type = Column(String)                # full_body|upper_lower|ppl|custom
    goal = Column(String); level = Column(String); equipment = Column(String)   # снимок профиля
    weeks = Column(Integer); days_per_week = Column(Integer)
    start_date = Column(String); end_date = Column(String)   # ISO
    summary = Column(Text, nullable=True)                    # «почему так» от ИИ
    periodization_json = Column(Text, nullable=True)         # [{"week":1,"phase":"base","weight_pct":100,"sets_delta":0}, ...]
    tips_json = Column(Text, nullable=True)                  # советы ИИ по программе
    ai_model = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TrainerProgramDay(Base):
    """Раскрытый день программы (неделя × день) — источник правды по плану."""

    __tablename__ = "trainer_program_days"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    program_id = Column(Integer, ForeignKey("trainer_programs.id"), index=True)
    week = Column(Integer); day_index = Column(Integer)      # 1..weeks, 1..days_per_week
    weekday = Column(Integer, nullable=True)                 # 0..6 из preferred_weekdays
    scheduled_date = Column(String, nullable=True, index=True)
    title = Column(String)                     # "Верх тела"
    session_type = Column(String)              # strength|cardio|mixed|mobility
    duration_min = Column(Integer)
    focus_muscles_json = Column(Text, nullable=True)
    warmup_json = Column(Text)                 # [{"slug","exercise_id","sets","reps","time_sec","note"}]
    exercises_json = Column(Text)              # [{"slug","exercise_id","sets","reps_min","reps_max","rest_sec","start_weight_kg","rpe","tempo","note","order"}]
    cooldown_json = Column(Text)
    adjustments_json = Column(Text, nullable=True)           # правки недельного разбора/отзыва к этому дню
    status = Column(String, default="planned", index=True)   # planned|done|skipped
    session_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class TrainerSession(Base):
    """Тренировочная сессия пользователя (по дню программы или внеплановая).

    Завершённая сессия порождает строку Workout (workout_id) — так дневник
    учитывает сожжённые калории, ничего не зная о тренере.
    """

    __tablename__ = "trainer_sessions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    program_id = Column(Integer, nullable=True, index=True)
    program_day_id = Column(Integer, nullable=True)
    date = Column(String, index=True)          # ISO дата старта (локальная дата клиента, см. §4)
    status = Column(String, index=True)        # in_progress|completed|abandoned
    title = Column(String); session_type = Column(String)
    week = Column(Integer, nullable=True); day_index = Column(Integer, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    duration_min = Column(Integer, nullable=True)
    total_sets = Column(Integer, default=0); total_reps = Column(Integer, default=0)
    total_volume_kg = Column(Float, default=0.0)
    calories_burned = Column(Integer, nullable=True)
    workout_id = Column(Integer, nullable=True)              # -> workouts.id, без FK
    feedback = Column(String, nullable=True)                 # easy|ok|hard
    feedback_note = Column(Text, nullable=True)
    adaptation_json = Column(Text, nullable=True)            # {"changes":[...], "lines":[...]}
    prs_json = Column(Text, nullable=True)                   # [{"exercise_id","type","value","prev"}]
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TrainerSessionExercise(Base):
    """Упражнение внутри сессии: план (цели) и статус выполнения."""

    __tablename__ = "trainer_session_exercises"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    session_id = Column(Integer, ForeignKey("trainer_sessions.id"), index=True)
    exercise_id = Column(Integer, ForeignKey("trainer_exercises.id"), index=True)
    block = Column(String, default="main")     # warmup|main|cooldown
    order_index = Column(Integer)
    planned_sets = Column(Integer); planned_reps_min = Column(Integer, nullable=True)
    planned_reps_max = Column(Integer, nullable=True); planned_weight_kg = Column(Float, nullable=True)
    planned_time_sec = Column(Integer, nullable=True); planned_rest_sec = Column(Integer, nullable=True)
    planned_rpe = Column(Integer, nullable=True)
    superset_group = Column(Integer, nullable=True)          # should
    status = Column(String, default="pending") # pending|done|skipped|replaced
    replaced_from_exercise_id = Column(Integer, nullable=True)
    replace_reason = Column(String, nullable=True)           # busy|no_equipment|pain|other
    note = Column(Text, nullable=True)         # подсказка ИИ/адаптации для UI
    created_at = Column(DateTime, default=datetime.utcnow)


class TrainerSetLog(Base):
    """Один подход (сет) в сессии: вес/повторы/время, объём, расчётный 1RM, PR."""

    __tablename__ = "trainer_set_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    session_id = Column(Integer, index=True); session_exercise_id = Column(Integer, index=True)
    exercise_id = Column(Integer, index=True)
    date = Column(String, index=True)
    set_index = Column(Integer)                # 1..N
    set_type = Column(String, default="work")  # warmup|work|drop|failure
    weight_kg = Column(Float, nullable=True); reps = Column(Integer, nullable=True)
    time_sec = Column(Integer, nullable=True); rpe = Column(Float, nullable=True)
    is_done = Column(Boolean, default=False)
    volume_kg = Column(Float, default=0.0)     # weight*reps для work
    est_1rm = Column(Float, nullable=True)     # Epley, только reps<=12
    is_pr = Column(Boolean, default=False)
    pr_types_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TrainerRecord(Base):
    """Личный рекорд: одна строка на (пользователь, упражнение, тип) — текущий лучший."""

    __tablename__ = "trainer_records"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    exercise_id = Column(Integer, index=True)
    record_type = Column(String)               # max_weight|est_1rm|max_reps|set_volume|max_time
    value = Column(Float)
    weight_kg = Column(Float, nullable=True); reps = Column(Integer, nullable=True)
    set_log_id = Column(Integer, nullable=True); session_id = Column(Integer, nullable=True)
    date = Column(String)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TrainerExerciseState(Base):
    """Состояние прогрессии пользователя по упражнению (рабочий вес, цели, стрики)."""

    __tablename__ = "trainer_exercise_states"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    exercise_id = Column(Integer, index=True)
    working_weight_kg = Column(Float, nullable=True)
    target_reps_min = Column(Integer, nullable=True); target_reps_max = Column(Integer, nullable=True)
    target_time_sec = Column(Integer, nullable=True)
    rest_sec = Column(Integer, nullable=True)
    last_result = Column(String, nullable=True)   # success|partial|fail
    success_streak = Column(Integer, default=0); fail_streak = Column(Integer, default=0)
    last_session_id = Column(Integer, nullable=True); last_date = Column(String, nullable=True)
    excluded = Column(Boolean, default=False)     # «никогда не предлагать»
    preferred_alternative_id = Column(Integer, nullable=True)  # запомненная замена
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TrainerWeeklyReview(Base):
    """Недельный разбор от ИИ: собранный контекст, ответ и флаг применения."""

    __tablename__ = "trainer_weekly_reviews"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    program_id = Column(Integer, nullable=True); week = Column(Integer, nullable=True)
    week_start = Column(String, index=True); week_end = Column(String)
    stats_json = Column(Text)                  # собранный контекст (sessions, volume, muscles, kcal, protein, weight)
    review_json = Column(Text)                 # ответ ИИ (нормализованный)
    applied = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class TrainerDailyTip(Base):
    """Кэш совета по питанию на день (один на дату, вид дня и язык)."""

    __tablename__ = "trainer_daily_tips"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    date = Column(String, index=True); kind = Column(String)  # training|rest
    lang = Column(String, default="ru")
    tip_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
