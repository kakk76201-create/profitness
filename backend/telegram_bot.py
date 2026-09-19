"""
Приём апдейтов Telegram-бота и работа с Bot API (Этап 1, система подписки).

Здесь сосредоточена вся «бот-сторона» подписки:
  * _bot_api(method, payload)        — низкоуровневый вызов Telegram Bot API;
  * handle_update(db, update)        — обработка входящего апдейта (webhook):
        - pre_checkout_query     -> ВСЕГДА отказ (оплата звёздами отключена);
        - successful_payment     -> активируем premium (payment_providers);
        - /givepro | /revokepro  -> ручная выдача/отзыв доступа ВЛАДЕЛЬЦЕМ;
        - /start                 -> приветственное сообщение.

Язык сообщений:
  Пользовательские сообщения выдаются на двух языках (RU/EN):
    * /start — язык определяется по message["from"]["language_code"]
      (начинается на "ru" -> русский, иначе английский);
    * подтверждение оплаты (successful_payment) — по User.language активированного
      пользователя (фолбэк "ru").
  Ответы ВЛАДЕЛЬЦУ на /givepro и /revokepro оставлены на русском (владелец один).

Безопасность (критично):
  * Команды /givepro и /revokepro доступны ТОЛЬКО владельцу. Владелец
    определяется СТРОГО по telegram_id == config.OWNER_ID, НИКОГДА по username.
    Если команду прислал не владелец — мы просто молча выходим, не отвечая и
    не раскрывая сам факт существования команды.
  * Активация premium происходит только на бэкенде — фронт обойти не может.

Надёжность: ВЕСЬ разбор апдейта обёрнут в try/except. Любой сбой (битый апдейт,
недоступный Bot API, ошибка БД) логируется и не валит обработку вебхука —
наружу исключения не пробрасываются, чтобы Telegram не ретраил вечно.
"""

import logging
import os

# httpx — для обращения к Telegram Bot API. Импортируем мягко, чтобы отсутствие
# зависимости не ломало импорт всего приложения (тот же паттерн, что в notifications.py).
try:
    import httpx
except Exception:  # pragma: no cover - на случай отсутствия httpx
    httpx = None

from datetime import datetime

from sqlalchemy import func

from backend.config import OWNER_ID, BOT_USERNAME, MINI_APP_URL
from backend.models import User, ProGrant, DiaryEntry, Payment
from backend import payment_providers
from backend import subscription
from backend import ai_service

logger = logging.getLogger("telegram_bot")


class PaymentActivationError(Exception):
    """Оплата прошла, но активация подписки не удалась.

    Пробрасывается наружу из handle_update, чтобы /telegram/webhook вернул не-200
    и Telegram повторил доставку апдейта (дедуп по charge_id защищает от двойного
    начисления). Для всех остальных сбоев апдейт «глушится» и отвечаем 200.
    """

# Токен Telegram-бота (без него вызовы Bot API невозможны).
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()


# --------------------------------------------------------------------------- #
#  Локализация пользовательских сообщений (RU / EN)
# --------------------------------------------------------------------------- #
def _norm_lang(lang) -> str:
    """Нормализовать язык к "ru" или "en" (по умолчанию "ru").

    Значение, начинающееся на "en" (без учёта регистра), трактуем как
    английский; всё остальное (None/пусто/"ru"/"ru-RU"/мусор) — как русский.
    Подходит и для Telegram language_code ("en", "en-US", "ru", "ru-RU"...),
    и для сохранённого в БД User.language.
    """
    try:
        if str(lang or "").strip().lower().startswith("en"):
            return "en"
    except Exception as exc:  # noqa: BLE001
        logger.debug("Подавлено исключение: %r", exc)
    return "ru"


def _user_language(db, tid: int) -> str:
    """Подтянуть язык пользователя по telegram_id (фолбэк "ru").

    При любой ошибке/отсутствии пользователя возвращаем "ru".
    """
    try:
        user = db.query(User).filter(User.telegram_id == tid).first()
        if user is not None:
            return _norm_lang(getattr(user, "language", None))
    except Exception as exc:
        logger.warning("_user_language: ошибка получения языка tid=%s: %s", tid, exc)
    return "ru"


def _greeting_text(lang: str, name: str) -> str:
    """Собрать приветственное сообщение /start на нужном языке.

    name — имя пользователя (может быть пустым). HTML не используется, поэтому
    спецсимволы в имени безопасны для отправки как обычный текст.
    """
    if lang == "en":
        return (
            (f"Hi, {name}! " if name else "Hi! ")
            + "This is the Fitness Up bot 🥗\n\n"
            "Open the mini app to count calories from photos, keep a food diary, "
            "track workouts and supplements.\n"
            "You can subscribe right inside the app."
        )
    # Русский вариант (по умолчанию) — без изменений относительно прежнего текста.
    return (
        (f"Привет, {name}! " if name else "Привет! ")
        + "Это бот Fitness Up 🥗\n\n"
        "Открывайте мини-приложение, чтобы считать калории по фото, "
        "вести дневник питания, тренировки и спортпит.\n"
        "Оформить подписку можно прямо в приложении."
    )


def _payment_success_text(lang: str) -> str:
    """Текст подтверждения успешной оплаты на нужном языке."""
    if lang == "en":
        return (
            "✅ Payment received! Premium access is now active.\n"
            "Thank you for your support — enjoy Fitness Up."
        )
    # Русский вариант (по умолчанию) — без изменений относительно прежнего текста.
    return (
        "✅ Оплата получена! Премиум-доступ активирован.\n"
        "Спасибо за поддержку — приятного пользования Fitness Up."
    )


def _payment_pending_text(lang: str) -> str:
    """Текст на случай, если оплата прошла, но активация временно не удалась."""
    if lang == "en":
        return (
            "✅ Payment received. We're activating your premium access — it will "
            "appear within a few minutes. If it doesn't, please contact support."
        )
    return (
        "✅ Оплата получена. Активируем премиум-доступ — он появится в течение "
        "нескольких минут. Если не появился — напишите в поддержку."
    )


def alert_owner(text: str, prefix: str = "Сервис") -> None:
    """Отправить владельцу (OWNER_ID) служебный алерт (best-effort).

    Общая точка для платежей, лимитов ИИ и прочих тревог: один чат, один формат.
    """
    if not OWNER_ID:
        return
    try:
        _bot_api("sendMessage", {"chat_id": OWNER_ID, "text": "⚠️ " + prefix + ": " + text})
    except Exception as exc:  # noqa: BLE001
        logger.warning("alert_owner: не удалось уведомить владельца: %s", exc)


def _alert_owner_payment(text: str) -> None:
    """Алерт по проблемному платежу."""
    alert_owner(text, prefix="Платёж")


def _validate_pre_checkout(pcq: dict) -> tuple[bool, str | None]:
    """Проверить pre_checkout_query перед подтверждением оплаты.

    ОПЛАТА ЗВЁЗДАМИ ОТКЛЮЧЕНА: приложение продаёт подписку только за рубли и
    новых Stars-счетов не выпускает. Значит любой pre_checkout_query сейчас —
    либо очень старый счёт, либо чужая попытка провести платёж мимо витрины,
    поэтому ВСЕГДА отвечаем отказом. Отказ именно на этом шаге безопасен:
    деньги ещё не списаны, пользователь ничего не теряет.
    """
    return False, "Оплата звёздами отключена"


# --------------------------------------------------------------------------- #
#  Локализация голосового ввода еды (Этап 2)
# --------------------------------------------------------------------------- #
# Человекочитаемые названия приёмов пищи для сводки (RU / EN).
_MEAL_TITLES = {
    "ru": {
        "breakfast": "завтрак",
        "lunch": "обед",
        "dinner": "ужин",
        "snack": "перекус",
    },
    "en": {
        "breakfast": "breakfast",
        "lunch": "lunch",
        "dinner": "dinner",
        "snack": "snack",
    },
}


def _voice_premium_required_text(lang: str) -> str:
    """Вежливый отказ free-пользователю на голосовой ввод (нужна подписка)."""
    if lang == "en":
        return (
            "🎤 Voice food logging is a premium feature.\n"
            "Open Fitness Up to subscribe and add meals just by speaking."
        )
    # Русский вариант (по умолчанию).
    return (
        "🎤 Голосовой ввод еды — премиум-функция.\n"
        "Откройте Fitness Up, оформите подписку — и добавляйте "
        "приёмы пищи просто голосом."
    )


def _voice_error_text(lang: str) -> str:
    """Вежливое сообщение об ошибке обработки голосового (не распознали и т.п.)."""
    if lang == "en":
        return (
            "😔 Couldn't process your voice message. "
            "Try again and describe what you ate a bit more clearly."
        )
    # Русский вариант (по умолчанию).
    return (
        "😔 Не удалось обработать голосовое сообщение. "
        "Попробуйте ещё раз и опишите чуть чётче, что вы съели."
    )


def _voice_summary_text(lang: str, transcript: str, meal_type: str, items: list) -> str:
    """
    Собрать сводку по распознанному голосовому приёму пищи (RU / EN).

    transcript — распознанный Whisper текст; meal_type — итоговый приём пищи
    ("breakfast"/"lunch"/"dinner"/"snack"); items — список словарей блюд с
    полями dish_name/calories. Возвращает готовый текст сообщения пользователю:
    распознанный текст + список «блюдо — N ккал» + «Итого X ккал» + приём пищи.
    """
    meal_title = _MEAL_TITLES.get(lang, _MEAL_TITLES["ru"]).get(meal_type, meal_type)

    # Итоговая калорийность по всем добавленным блюдам.
    total = 0
    lines = []
    for it in items:
        try:
            cal = int(it.get("calories") or 0)
        except Exception:
            cal = 0
        total += cal
        name = str(it.get("dish_name") or "").strip() or ("dish" if lang == "en" else "блюдо")
        if lang == "en":
            lines.append(f"• {name} — {cal} kcal")
        else:
            lines.append(f"• {name} — {cal} ккал")

    body = "\n".join(lines)
    if lang == "en":
        return (
            f"🎤 Recognized: «{transcript}»\n\n"
            f"{body}\n\n"
            f"Total: {total} kcal\n"
            f"Added to: {meal_title}."
        )
    # Русский вариант (по умолчанию).
    return (
        f"🎤 Распознано: «{transcript}»\n\n"
        f"{body}\n\n"
        f"Итого: {total} ккал\n"
        f"Добавлено в приём: {meal_title}."
    )


# --------------------------------------------------------------------------- #
#  Низкоуровневый вызов Telegram Bot API
# --------------------------------------------------------------------------- #
def _bot_api(method: str, payload: dict):
    """
    Вызвать произвольный метод Telegram Bot API.

    Делает POST на https://api.telegram.org/bot{BOT_TOKEN}/{method} с телом
    payload (JSON). Возвращает поле "result" из ответа Telegram при успехе,
    иначе None. Никогда не бросает исключение наружу.

    Если не задан BOT_TOKEN или не установлен httpx — тихо возвращаем None
    (это не ошибка приложения, просто бот сейчас «немой»).
    """
    if not BOT_TOKEN:
        logger.debug("_bot_api: BOT_TOKEN не задан, метод %s пропущен", method)
        return None
    if httpx is None:
        logger.warning("_bot_api: httpx не установлен, метод %s невозможен", method)
        return None

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        resp = httpx.post(url, json=payload, timeout=10)
        # Разбираем JSON-ответ Telegram. Поле ok=true означает успех.
        try:
            data = resp.json()
        except Exception:
            data = None

        if resp.status_code == 200 and isinstance(data, dict) and data.get("ok"):
            return data.get("result")

        # Любой неуспех — логируем (без падения).
        logger.warning(
            "_bot_api: метод=%s статус=%s ответ=%s",
            method, resp.status_code, (resp.text or "")[:300],
        )
        return None
    except Exception as exc:
        # Сетевой/прочий сбой — логируем и считаем неуспехом.
        logger.warning("_bot_api: ошибка вызова метода %s: %s", method, exc)
        return None


# --------------------------------------------------------------------------- #
#  Скачивание файла из Telegram (для голосовых сообщений)
# --------------------------------------------------------------------------- #
def _download_file(file_path: str) -> bytes | None:
    """
    Скачать содержимое файла Telegram по его file_path.

    file_path берётся из ответа метода getFile (result["file_path"]). Сам файл
    лежит по адресу https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}.
    Возвращает байты файла при успехе, иначе None (никогда не бросает наружу).

    Без BOT_TOKEN или httpx, а также при любой сетевой/HTTP-ошибке — тихо
    возвращаем None: это не должно валить обработку апдейта.
    """
    if not BOT_TOKEN:
        logger.debug("_download_file: BOT_TOKEN не задан, скачивание пропущено")
        return None
    if httpx is None:
        logger.warning("_download_file: httpx не установлен, скачивание невозможно")
        return None
    if not file_path:
        return None

    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    try:
        resp = httpx.get(url, timeout=30)
        if resp.status_code == 200 and resp.content:
            return resp.content
        logger.warning(
            "_download_file: статус=%s длина=%s",
            resp.status_code, len(resp.content or b""),
        )
        return None
    except Exception as exc:
        logger.warning("_download_file: ошибка скачивания файла: %s", exc)
        return None


# --------------------------------------------------------------------------- #
#  Вспомогательное: разбор @username из текста команды
# --------------------------------------------------------------------------- #
def _parse_username_arg(text: str) -> str | None:
    """
    Извлечь username из текста команды вида "/givepro @vasya" или "/givepro vasya".

    Возвращает username БЕЗ ведущего "@" или None, если аргумент не указан.
    """
    try:
        parts = str(text).split()
        if len(parts) < 2:
            return None
        uname = parts[1].strip()
        if uname.startswith("@"):
            uname = uname[1:]
        uname = uname.strip()
        return uname or None
    except Exception:
        return None


def _parse_days_arg(text: str) -> int | None:
    """Извлечь число дней из "/givepro @user 30" (третий токен). None, если нет/не число."""
    try:
        parts = str(text).split()
        if len(parts) < 3:
            return None
        return int(parts[2].strip())
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  Регистрация пользователя по ЛЮБОМУ контакту с ботом
# --------------------------------------------------------------------------- #
def _touch_user(db, from_user: dict) -> None:
    """Создать/обновить запись User по данным из апдейта Telegram.

    ЗАЧЕМ: раньше пользователь появлялся в БД ТОЛЬКО после первого открытия
    мини-приложения (backend/auth.py::_upsert_user). Поэтому «человек запустил
    бота», но /givepro @username его не находил. Telegram присылает id и
    username в КАЖДОМ апдейте — грех это выбрасывать.

    Вызывается на любое сообщение боту (/start, текст, голос, оплата).
    Username при смене — обновляем; НЕ затираем сохранённый, если в апдейте
    его нет (у пользователя может не быть username).

    Полностью безопасна: любые сбои логируются и не пробрасываются, чтобы не
    сломать основную обработку апдейта (особенно платежи).
    """
    if not isinstance(from_user, dict):
        return
    # Ботов (в т.ч. самого себя) в пользователи не записываем.
    if from_user.get("is_bot"):
        return

    try:
        tid = int(from_user.get("id"))
    except (TypeError, ValueError):
        return

    try:
        uname = from_user.get("username") or None
        fname = from_user.get("first_name") or None
        lang_code = from_user.get("language_code") or ""

        user = db.query(User).filter(User.telegram_id == tid).first()
        created = False
        if user is None:
            # Гонка «бот + приложение одновременно» на PostgreSQL даёт
            # UniqueViolation: при конфликте перечитываем созданную запись.
            candidate = User(telegram_id=tid)
            db.add(candidate)
            try:
                db.flush()
                user = candidate
                created = True
            except Exception:  # noqa: BLE001
                db.rollback()
                user = db.query(User).filter(User.telegram_id == tid).first()
                if user is None:
                    raise

        # Профильные поля обновляем, только если Telegram их прислал.
        if uname:
            user.username = uname
        if fname:
            user.first_name = fname
        # Язык ставим один раз (дальше пользователь меняет его сам в приложении).
        if not getattr(user, "language", None):
            user.language = "ru" if str(lang_code).lower().startswith("ru") else "en"

        # Владелец приложения определяется СТРОГО по telegram_id (не по username).
        if OWNER_ID and tid == OWNER_ID and not getattr(user, "is_owner", False):
            user.is_owner = True
            if getattr(user, "subscription_type", None) != "lifetime":
                user.subscription_type = "lifetime"
                user.subscription_until = None

        db.commit()
        if created:
            logger.info("Новый пользователь через бота: tid=%s username=%s", tid, uname)

        # Применяем отложенные выдачи доступа («/givepro @username», отданную до
        # того как человек появился в базе). Сообщаем владельцу и пользователю.
        try:
            applied = payment_providers.apply_pending_grants(db, tid, user.username)
            for row in applied:
                srok = f"{row.days} дн." if row.days else "навсегда"
                if OWNER_ID:
                    _bot_api("sendMessage", {
                        "chat_id": OWNER_ID,
                        "text": f"✅ Отложенная выдача применена: @{user.username} ({tid}) — {srok}",
                    })
                _bot_api("sendMessage", {
                    "chat_id": tid,
                    "text": "🎉 Вам открыт премиум-доступ. Откройте приложение — всё уже доступно.",
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
    except Exception as exc:  # noqa: BLE001 — регистрация не должна ронять апдейт
        logger.error("_touch_user: НЕ СОХРАНЁН пользователь %s (@%s): %s", tid, uname, exc)
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
        # Немой сбой регистрации — худший вариант: человек «пользуется ботом»,
        # а в базе его нет, и владелец не может выдать ему доступ. Поэтому
        # сообщаем владельцу СРАЗУ, с текстом ошибки и id (по нему можно выдать).
        try:
            if OWNER_ID:
                _bot_api("sendMessage", {
                    "chat_id": OWNER_ID,
                    "text": (
                        "⚠️ Не удалось записать пользователя в базу\n"
                        f"id: {tid}\n"
                        f"username: @{uname}\n"
                        f"ошибка: {exc}\n\n"
                        f"Выдать доступ можно напрямую: /givepro {tid}"
                    ),
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)


# --------------------------------------------------------------------------- #
#  Диагностика для владельца: /users и /whois
# --------------------------------------------------------------------------- #
def _db_target_info() -> str:
    """Краткое описание БД, в которую реально пишет ЭТОТ процесс (без пароля).

    Нужно, чтобы владелец видел: работает ли прод на PostgreSQL или свалился
    на эфемерный SQLite (тогда данные исчезают при каждом редеплое).
    """
    try:
        from backend.database import engine, IS_EPHEMERAL_SQLITE

        url = engine.url
        if url.get_backend_name().startswith("sqlite"):
            warn = " ⚠️ DATABASE_URL НЕ ЗАДАН — данные стираются при каждом деплое!" \
                if IS_EPHEMERAL_SQLITE else ""
            return f"SQLite (файл: {url.database}){warn}"
        return f"{url.get_backend_name()} @ {url.host}/{url.database}"
    except Exception as exc:  # noqa: BLE001
        return f"неизвестно ({exc})"


def _handle_stats_command(db, message: dict, text: str) -> None:
    """/stats [дней] — отчёт владельцу: аудитория, использование, путь к оплате.

    Только владелец (по OWNER_ID); остальным — молчание, как у других
    служебных команд. Период по умолчанию 7 дней, можно «/stats 30».
    """
    try:
        from_id = int(message.get("from", {}).get("id"))
    except Exception:  # noqa: BLE001
        return
    if not OWNER_ID or from_id != OWNER_ID:
        return
    chat_id = message.get("chat", {}).get("id")
    if chat_id is None:
        return
    days = 7
    parts = text.split()
    if len(parts) > 1 and parts[1].isdigit():
        days = max(1, min(90, int(parts[1])))
    try:
        from backend import analytics

        body = analytics.report(db, days)
    except Exception as exc:  # noqa: BLE001
        try:
            db.rollback()
        except Exception as exc2:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc2)
        body = f"Не удалось собрать отчёт: {exc}"
    _bot_api("sendMessage", {"chat_id": chat_id, "text": body})


def _handle_users_command(db, message: dict) -> None:
    """/users — показать владельцу последних пользователей из базы."""
    try:
        from_id = int(message.get("from", {}).get("id"))
    except Exception:  # noqa: BLE001
        return
    if not OWNER_ID or from_id != OWNER_ID:
        return
    chat_id = message.get("chat", {}).get("id")
    if chat_id is None:
        return

    try:
        total = db.query(User).count()
        rows = (
            db.query(User)
            .order_by(User.created_at.desc().nullslast(), User.telegram_id.desc())
            .limit(15)
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
        _bot_api("sendMessage", {"chat_id": chat_id, "text": f"Ошибка чтения базы: {exc}"})
        return

    lines = [f"База: {_db_target_info()}", f"Всего пользователей: {total}", ""]
    if not rows:
        lines.append("Пусто. Никто ещё не попал в базу.")
    else:
        for u in rows:
            uname = f"@{u.username}" if u.username else "(без username)"
            sub = u.subscription_type or "free"
            lines.append(f"{u.telegram_id} — {uname} — {sub}")
    _bot_api("sendMessage", {"chat_id": chat_id, "text": "\n".join(lines)})


def _queue_pending_grant(db, uname: str, days, created_by) -> str:
    """Поставить выдачу доступа в очередь по @username. Возвращает текст ответа."""
    from backend.models import PendingGrant

    key = uname.strip().lower()
    srok = f"{days} дн." if days and int(days) > 0 else "навсегда"
    try:
        existing = (
            db.query(PendingGrant)
            .filter(PendingGrant.username_lower == key, PendingGrant.applied_at.is_(None))
            .first()
        )
        if existing is not None:
            existing.days = int(days) if days and int(days) > 0 else None
            db.commit()
            return (
                f"@{uname} пока нет в базе — заявка уже стояла, обновил срок: {srok}.\n"
                "Применится автоматически, как только он напишет боту или откроет приложение.\n"
                "Очередь: /pending"
            )

        db.add(PendingGrant(
            username_lower=key,
            days=int(days) if days and int(days) > 0 else None,
            created_by=created_by,
        ))
        db.commit()
        return (
            f"@{uname} пока нет в базе — поставил в очередь ({srok}).\n\n"
            "Доступ выдастся АВТОМАТИЧЕСКИ, как только он напишет боту или откроет "
            "приложение. Вам придёт подтверждение.\n\n"
            "Быстрее: /givepro <telegram_id> — если знаете его числовой ID.\n"
            "Очередь: /pending"
        )
    except Exception as exc:  # noqa: BLE001
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
        return f"Не удалось поставить в очередь: {exc}"


def _handle_pending_command(db, message: dict) -> None:
    """/pending — показать владельцу очередь отложенных выдач."""
    try:
        from_id = int(message.get("from", {}).get("id"))
    except Exception:  # noqa: BLE001
        return
    if not OWNER_ID or from_id != OWNER_ID:
        return
    chat_id = message.get("chat", {}).get("id")
    if chat_id is None:
        return

    from backend.models import PendingGrant

    try:
        rows = (
            db.query(PendingGrant)
            .filter(PendingGrant.applied_at.is_(None))
            .order_by(PendingGrant.id.desc())
            .limit(20)
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
        _bot_api("sendMessage", {"chat_id": chat_id, "text": f"Ошибка чтения базы: {exc}"})
        return

    if not rows:
        _bot_api("sendMessage", {"chat_id": chat_id, "text": "Очередь пуста."})
        return

    lines = ["Ждут появления пользователя:"]
    for r in rows:
        lines.append(f"@{r.username_lower} — {str(r.days) + ' дн.' if r.days else 'навсегда'}")
    _bot_api("sendMessage", {"chat_id": chat_id, "text": "\n".join(lines)})


def _handle_whois_command(db, message: dict, text: str) -> None:
    """/whois @username — показать, что именно находит поиск (диагностика)."""
    try:
        from_id = int(message.get("from", {}).get("id"))
    except Exception:  # noqa: BLE001
        return
    if not OWNER_ID or from_id != OWNER_ID:
        return
    chat_id = message.get("chat", {}).get("id")
    if chat_id is None:
        return

    arg = _parse_username_arg(text)
    if not arg:
        _bot_api("sendMessage", {"chat_id": chat_id, "text": "Использование: /whois @username"})
        return

    try:
        exact = db.query(User).filter(User.username == arg).first()
        ci = db.query(User).filter(func.lower(User.username) == arg.lower()).first()
        like = (
            db.query(User)
            .filter(func.lower(User.username).like("%" + arg.lower() + "%"))
            .limit(5)
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
        _bot_api("sendMessage", {"chat_id": chat_id, "text": f"Ошибка чтения базы: {exc}"})
        return

    lines = [
        f"Ищем: {arg!r} (длина {len(arg)})",
        f"База: {_db_target_info()}",
        f"Точное совпадение: {exact.telegram_id if exact else 'нет'}",
        f"Без учёта регистра: {ci.telegram_id if ci else 'нет'}",
    ]
    if like:
        lines.append("Похожие: " + ", ".join(f"@{u.username}({u.telegram_id})" for u in like))
    else:
        lines.append("Похожих не найдено.")
    _bot_api("sendMessage", {"chat_id": chat_id, "text": "\n".join(lines)})


# --------------------------------------------------------------------------- #
#  Команды владельца: /givepro и /revokepro
# --------------------------------------------------------------------------- #
def _handle_owner_command(db, message: dict, text: str) -> None:
    """
    Обработать команду /givepro или /revokepro.

    БЕЗОПАСНОСТЬ: команду выполняет ТОЛЬКО владелец (from.id == OWNER_ID).
    Если отправитель не владелец — молча выходим (не отвечаем, не раскрываем
    существование команды). Владелец определяется строго по id, не по username.

    Цель команды задаётся через @username: ищем пользователя по User.username.
    Если такого пользователя нет в БД — просим владельца, чтобы цель сначала
    открыла приложение (так у нас появится её telegram_id).

    Примечание по языку: ответы адресованы ВЛАДЕЛЬЦУ (он один), поэтому
    оставлены на русском — локализация здесь не требуется.
    """
    from_id = None
    try:
        from_id = int(message.get("from", {}).get("id"))
    except Exception:
        from_id = None

    # Только владелец. Без OWNER_ID (==0) команда недоступна никому.
    if not OWNER_ID or from_id != OWNER_ID:
        # Молча игнорируем — не раскрываем команду посторонним.
        return

    # Чат, куда отвечать владельцу (его личный чат с ботом).
    chat_id = None
    try:
        chat_id = message.get("chat", {}).get("id")
    except Exception:
        chat_id = None

    is_give = text.strip().startswith("/givepro")
    action = "give" if is_give else "revoke"

    # Аргумент цели: либо @username, либо числовой telegram_id.
    arg = _parse_username_arg(text)

    target_id = None
    uname_display = arg or ""

    # СПОСОБ БЕЗ USERNAME: команда отправлена ОТВЕТОМ на пересланное от человека
    # сообщение — берём его id прямо из апдейта. Работает, даже если username
    # не задан или человека ещё нет в базе.
    reply = message.get("reply_to_message")
    if isinstance(reply, dict):
        # forward_from — исходный автор пересланного сообщения; from — отправитель.
        src = reply.get("forward_from") if isinstance(reply.get("forward_from"), dict) else None
        if src is None and isinstance(reply.get("from"), dict):
            # Не берём самого бота (у пересланных без forward_from автор скрыт).
            if not reply["from"].get("is_bot"):
                src = reply["from"]
        if isinstance(src, dict):
            try:
                target_id = int(src.get("id"))
                uname_display = src.get("username") or src.get("first_name") or str(target_id)
                # Заодно регистрируем цель, чтобы она была в базе с username.
                _touch_user(db, src)
            except (TypeError, ValueError):
                target_id = None

    if target_id is None and not arg:
        if chat_id is not None:
            _bot_api("sendMessage", {
                "chat_id": chat_id,
                "text": (
                    "Укажите цель одним из способов:\n"
                    "• /givepro @username\n"
                    "• /givepro <telegram_id>\n"
                    "• ответом (reply) на пересланное от человека сообщение\n\n"
                    "Посмотреть, кто есть в базе: /users\n"
                    "Проверить конкретного: /whois @username"
                ),
            })
        return

    if target_id is None:
        if arg.isdigit():
            # По числовому ID — работает, даже если человека ещё нет в базе
            # (activate_premium/grant_days создадут запись пользователя по id).
            target_id = int(arg)
            try:
                u = db.query(User).filter(User.telegram_id == target_id).first()
                if u is not None and u.username:
                    uname_display = u.username
            except Exception:  # noqa: BLE001
                try:
                    db.rollback()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Подавлено исключение: %r", exc)
        else:
            # По username — регистронезависимо (логины Telegram нечувствительны
            # к регистру). Ошибку БД НЕ маскируем под «не найден».
            target = None
            db_error = None
            try:
                target = (
                    db.query(User)
                    .filter(func.lower(User.username) == arg.lower())
                    .first()
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("_handle_owner_command: ошибка поиска %s: %s", arg, exc)
                db_error = exc
                try:
                    db.rollback()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Подавлено исключение: %r", exc)

            if db_error is not None:
                if chat_id is not None:
                    _bot_api("sendMessage", {
                        "chat_id": chat_id,
                        "text": f"Ошибка обращения к базе: {db_error}",
                    })
                return

            if target is None:
                # НЕ ТУПИК: Bot API не умеет резолвить @username в telegram_id,
                # поэтому ставим выдачу в очередь. Она применится сама, как только
                # человек напишет боту или откроет приложение.
                if is_give:
                    days = _parse_days_arg(text)
                    msg = _queue_pending_grant(db, arg, days, from_id)
                else:
                    msg = (
                        f"@{arg} не найден в базе — отзывать нечего.\n"
                        "Посмотреть, кто есть: /users"
                    )
                if chat_id is not None:
                    _bot_api("sendMessage", {"chat_id": chat_id, "text": msg})
                return

            target_id = int(getattr(target, "telegram_id"))
            uname_display = target.username or arg

    # Выполняем выдачу/отзыв доступа через единый слой активации.
    try:
        if is_give:
            # Опциональный аргумент — число дней: "/givepro @user 30". Без него —
            # пожизненный доступ (как раньше). Для промо/подарков на срок.
            days = _parse_days_arg(text)
            if days and days > 0:
                payment_providers.grant_days(db, int(target_id), days, "owner", subscription_type="monthly")
                result_text = f"Готово: @{uname_display} получил доступ на {days} дн."
            else:
                payment_providers.activate_premium(db, int(target_id), "lifetime", "owner", 0, "RUB")
                result_text = f"Готово: @{uname_display} получил пожизненный доступ."
        else:
            payment_providers.revoke_premium(db, int(target_id))
            result_text = f"Готово: доступ для @{uname_display} отозван."
    except Exception as exc:
        logger.warning("_handle_owner_command: сбой %s для %s: %s", action, uname_display, exc)
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
        if chat_id is not None:
            # Показываем ПРИЧИНУ, а не безликое «не удалось» — иначе диагностика слепая.
            _bot_api("sendMessage", {
                "chat_id": chat_id,
                "text": f"Не удалось выполнить операцию для @{uname_display}.\nПричина: {exc}",
            })
        return

    # Журналируем факт ручной выдачи/отзыва доступа (ProGrant).
    try:
        db.add(ProGrant(
            granted_by=OWNER_ID,
            granted_to=int(target_id),
            action=action,
        ))
        db.commit()
    except Exception as exc:
        logger.warning("_handle_owner_command: не удалось записать ProGrant (%s)", exc)
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)

    # Сообщаем владельцу результат.
    if chat_id is not None:
        _bot_api("sendMessage", {"chat_id": chat_id, "text": result_text})


# --------------------------------------------------------------------------- #
#  Голосовой ввод еды (Этап 2): voice / audio -> Whisper -> GPT -> дневник
# --------------------------------------------------------------------------- #
def _handle_voice_message(db, message: dict) -> None:
    """
    Обработать голосовое (voice) или аудио (audio) сообщение пользователя.

    Сценарий (всё внутри try/except — сбой не валит обработку апдейта):
      1) определяем отправителя и его язык;
      2) проверяем премиум: free-пользователю вежливо отвечаем про подписку
         и выходим (subscription.is_premium);
      3) скачиваем файл (getFile -> file_path -> _download_file);
      4) распознаём речь (ai_service.transcribe_audio) и парсим блюда
         (ai_service.parse_food_text) на языке пользователя;
      5) добавляем каждое блюдо в DiaryEntry за сегодня (meal_type из фразы,
         либо "snack" по умолчанию), коммитим;
      6) отправляем пользователю сводку на его языке (RU/EN).

    При любой ошибке ИИ/скачивания — вежливое сообщение пользователю, без падения.
    """
    # --- 1) Отправитель и чат для ответа -------------------------------------- #
    from_id = None
    try:
        from_id = int(message.get("from", {}).get("id"))
    except Exception:
        from_id = None

    chat_id = None
    try:
        chat_id = message.get("chat", {}).get("id")
    except Exception:
        chat_id = None
    if chat_id is None:
        chat_id = from_id

    if from_id is None or chat_id is None:
        # Без отправителя/чата ответить и сохранить данные некуда.
        return

    # --- 2) Премиум-проверка -------------------------------------------------- #
    # Ищем пользователя в БД. Язык: по User.language, а для незнакомого
    # пользователя — по language_code из самого апдейта.
    user = None
    try:
        user = db.query(User).filter(User.telegram_id == from_id).first()
    except Exception as exc:
        logger.warning("_handle_voice_message: ошибка поиска пользователя tid=%s: %s", from_id, exc)
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
        user = None

    if user is not None:
        lang = _norm_lang(getattr(user, "language", None))
    else:
        lang_code = ""
        try:
            lang_code = message.get("from", {}).get("language_code", "") or ""
        except Exception:
            lang_code = ""
        lang = _norm_lang(lang_code)

    # Нет пользователя в БД или нет активной подписки — вежливый отказ.
    if user is None or not subscription.is_premium(user):
        _bot_api("sendMessage", {
            "chat_id": chat_id,
            "text": _voice_premium_required_text(lang),
        })
        return

    # --- Дальше работаем в защищённом блоке: любая ошибка -> вежливый ответ ---- #
    try:
        # --- 3) Достаём file_id из voice или audio и скачиваем файл ----------- #
        voice = message.get("voice")
        audio = message.get("audio")
        media = voice if isinstance(voice, dict) else audio
        file_id = None
        if isinstance(media, dict):
            file_id = media.get("file_id")
        if not file_id:
            logger.warning("_handle_voice_message: не найден file_id (tid=%s)", from_id)
            _bot_api("sendMessage", {"chat_id": chat_id, "text": _voice_error_text(lang)})
            return

        file_info = _bot_api("getFile", {"file_id": file_id})
        file_path = None
        if isinstance(file_info, dict):
            file_path = file_info.get("file_path")
        if not file_path:
            logger.warning("_handle_voice_message: getFile не вернул file_path (tid=%s)", from_id)
            _bot_api("sendMessage", {"chat_id": chat_id, "text": _voice_error_text(lang)})
            return

        audio_bytes = _download_file(file_path)
        if not audio_bytes:
            logger.warning("_handle_voice_message: не удалось скачать файл (tid=%s)", from_id)
            _bot_api("sendMessage", {"chat_id": chat_id, "text": _voice_error_text(lang)})
            return

        # --- 4) Распознавание речи и парсинг блюд ----------------------------- #
        text = ai_service.transcribe_audio(audio_bytes, "voice.ogg", lang=lang)
        parsed = ai_service.parse_food_text(text, lang=lang)

        items = parsed.get("items") or []
        if not items:
            # GPT не выделил ни одного блюда — сообщаем пользователю.
            _bot_api("sendMessage", {"chat_id": chat_id, "text": _voice_error_text(lang)})
            return

        # Приём пищи: из фразы, иначе "snack" по умолчанию.
        meal_type = parsed.get("meal_type")
        if meal_type not in ("breakfast", "lunch", "dinner", "snack"):
            meal_type = "snack"

        # --- 5) Добавляем каждое блюдо в дневник за сегодня ------------------- #
        today = datetime.utcnow().date().isoformat()
        added = []
        for it in items:
            try:
                entry = DiaryEntry(
                    telegram_id=from_id,
                    date=today,
                    meal_type=meal_type,
                    dish_name=str(it.get("dish_name") or "").strip(),
                    calories=int(it.get("calories") or 0),
                    proteins=float(it.get("proteins") or 0),
                    fats=float(it.get("fats") or 0),
                    carbs=float(it.get("carbs") or 0),
                )
                db.add(entry)
                added.append(it)
            except Exception as exc:
                logger.warning("_handle_voice_message: пропуск блюда %r: %s", it, exc)

        if not added:
            # Ничего не удалось добавить — откатываем и сообщаем об ошибке.
            try:
                db.rollback()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Подавлено исключение: %r", exc)
            _bot_api("sendMessage", {"chat_id": chat_id, "text": _voice_error_text(lang)})
            return

        db.commit()

        # --- 6) Сводка пользователю на его языке ------------------------------ #
        summary = _voice_summary_text(lang, text, meal_type, added)
        _bot_api("sendMessage", {"chat_id": chat_id, "text": summary})

    except ai_service.AIError as exc:
        # Ошибка ИИ (нет речи / не распознали / GPT не ответил) — вежливый ответ.
        logger.warning("_handle_voice_message: AIError (tid=%s): %s", from_id, exc)
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
        _bot_api("sendMessage", {"chat_id": chat_id, "text": _voice_error_text(lang)})
    except Exception as exc:
        # Любой иной сбой — логируем, вежливо отвечаем, не падаем.
        logger.warning("_handle_voice_message: общий сбой (tid=%s): %s", from_id, exc)
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
        _bot_api("sendMessage", {"chat_id": chat_id, "text": _voice_error_text(lang)})


# --------------------------------------------------------------------------- #
#  Главный обработчик входящего апдейта (webhook)
# --------------------------------------------------------------------------- #
def handle_update(db, update: dict) -> None:
    """
    Обработать один входящий апдейт Telegram (приходит на /telegram/webhook).

    Поддерживаемые виды апдейтов:
      * pre_checkout_query        — ВСЕГДА отказ (оплата звёздами отключена);
      * message.successful_payment — оплата прошла, активируем premium
        (оставлено для старых, ещё не оплаченных Stars-счетов);
      * message.voice|audio       — голосовой ввод еды (премиум, Этап 2);
      * message.text /givepro|/revokepro — команды владельца (см. выше);
      * message.text /start       — приветствие.

    ВСЁ обёрнуто в try/except — любой сбой логируется и не пробрасывается наружу.
    """
    if not isinstance(update, dict):
        return

    try:
        # --- 1) Предварительная проверка оплаты (нужно ответить за ≤10 сек) --- #
        pre_checkout = update.get("pre_checkout_query")
        if isinstance(pre_checkout, dict):
            pcq_id = pre_checkout.get("id")
            if pcq_id is not None:
                # Оплата звёздами отключена — счета больше не выпускаются,
                # поэтому _validate_pre_checkout всегда возвращает отказ, и мы
                # отвечаем ok=false. Деньги на этом шаге ещё не списаны.
                ok, err = _validate_pre_checkout(pre_checkout)
                payload = {"pre_checkout_query_id": pcq_id, "ok": ok}
                if not ok:
                    payload["error_message"] = err or "Счёт недействителен. Попробуйте ещё раз."
                _bot_api("answerPreCheckoutQuery", payload)
            return

        # Дальше работаем с message (обычное сообщение / событие оплаты).
        # Поддерживаем и edited_message: пользователь мог отредактировать команду.
        message = update.get("message")
        if not isinstance(message, dict):
            message = update.get("edited_message")
        if not isinstance(message, dict):
            # Нет сообщения — обрабатывать нечего.
            return

        # РЕГИСТРАЦИЯ: любой контакт с ботом заносит пользователя в БД, чтобы
        # /givepro @username работал сразу после /start (не дожидаясь, пока
        # человек откроет мини-приложение).
        _touch_user(db, message.get("from") or {})

        # --- 2) Успешная оплата -> активируем premium ------------------------- #
        # LEGACY: новых Stars-счетов приложение не выпускает (оплата только
        # картой в рублях), но обработчик оставлен — если где-то остался старый
        # неоплаченный счёт и деньги всё же спишутся, доступ нужно выдать.
        successful_payment = message.get("successful_payment")
        if isinstance(successful_payment, dict):
            # payload мы задавали при создании счёта: "{tariff}:{telegram_id}".
            payload = successful_payment.get("invoice_payload", "") or ""
            charge_id = successful_payment.get("telegram_payment_charge_id") or None
            chat_id = message.get("chat", {}).get("id")

            try:
                tariff, tid_raw = payload.split(":", 1)
                tid = int(tid_raw)
            except Exception as exc:
                # Оплата есть, но не понимаем, кому активировать — ретрай не поможет,
                # поэтому алертим владельца и молча выходим (ответим 200).
                logger.error("successful_payment: битый payload %r: %s", payload, exc)
                _alert_owner_payment(f"оплата с непонятным payload={payload!r}, charge={charge_id}")
                return

            # ИДЕМПОТЕНТНОСТЬ: этот платёж (charge_id) уже обработан? Повторная
            # доставка апдейта не должна продлевать подписку второй раз.
            if charge_id:
                try:
                    already = db.query(Payment).filter(Payment.charge_id == charge_id).first()
                except Exception:  # noqa: BLE001
                    already = None
                if already is not None:
                    logger.info("successful_payment: charge %s уже обработан — пропуск", charge_id)
                    if chat_id is not None:
                        _bot_api("sendMessage", {
                            "chat_id": chat_id,
                            "text": _payment_success_text(_user_language(db, tid)),
                        })
                    return

            try:
                payment_providers.activate_premium(
                    db, tid, tariff, "stars",
                    successful_payment.get("total_amount"),
                    successful_payment.get("currency", "XTR"),
                    charge_id=charge_id,
                )
                lang = _user_language(db, tid)
                _bot_api("sendMessage", {
                    "chat_id": chat_id if chat_id is not None else tid,
                    "text": _payment_success_text(lang),
                })
            except Exception as exc:
                # Оплата ПРОШЛА, но активация не удалась — НЕ глушим:
                logger.error("successful_payment: сбой активации (tid=%s charge=%s): %s", tid, charge_id, exc)
                try:
                    db.rollback()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Подавлено исключение: %r", exc)
                # (1) сообщаем плательщику, что доступ появится; (2) алертим владельца;
                if chat_id is not None:
                    _bot_api("sendMessage", {
                        "chat_id": chat_id,
                        "text": _payment_pending_text(_user_language(db, tid)),
                    })
                _alert_owner_payment(
                    f"НЕ активирован премиум после оплаты: tid={tid} tariff={tariff} charge={charge_id} err={exc}"
                )
                # (3) пробрасываем — /telegram/webhook вернёт 500, Telegram повторит.
                raise PaymentActivationError(str(exc))
            return

        # --- 3) Голосовой / аудио ввод еды (премиум, Этап 2) ----------------- #
        # Если в сообщении есть voice или audio — обрабатываем как голосовой
        # ввод еды. Премиум-проверка и вся обработка — внутри _handle_voice_message.
        if isinstance(message.get("voice"), dict) or isinstance(message.get("audio"), dict):
            _handle_voice_message(db, message)
            return

        # --- 4) Текстовые команды -------------------------------------------- #
        text = message.get("text")
        if isinstance(text, str):
            stripped = text.strip()

            # Команды владельца: выдача/отзыв доступа.
            if stripped.startswith("/givepro") or stripped.startswith("/revokepro"):
                _handle_owner_command(db, message, text)
                return

            # Диагностика владельца: кто есть в базе / что находит поиск.
            if stripped.startswith("/users"):
                _handle_users_command(db, message)
                return
            if stripped.startswith("/whois"):
                _handle_whois_command(db, message, text)
                return
            if stripped.startswith("/pending"):
                _handle_pending_command(db, message)
                return
            if stripped.startswith("/stats"):
                _handle_stats_command(db, message, stripped)
                return

            # Приветствие по /start.
            if stripped == "/start" or stripped.startswith("/start"):
                chat_id = message.get("chat", {}).get("id")
                if chat_id is not None:
                    name = ""
                    try:
                        name = message.get("from", {}).get("first_name", "") or ""
                    except Exception:
                        name = ""
                    # Язык приветствия — по language_code из апдейта
                    # (ru* -> русский, иначе английский). В БД пользователя ещё
                    # может не быть, поэтому опираемся именно на апдейт.
                    lang_code = ""
                    try:
                        lang_code = message.get("from", {}).get("language_code", "") or ""
                    except Exception:
                        lang_code = ""
                    lang = _norm_lang(lang_code)
                    greeting = _greeting_text(lang, name)
                    msg = {"chat_id": chat_id, "text": greeting}
                    # Кнопка открытия мини-приложения (если задан MINI_APP_URL) —
                    # иначе пользователю негде нажать «открыть». Ключевой рост-фикс.
                    if MINI_APP_URL:
                        btn_text = "🥗 Open the app" if lang == "en" else "🥗 Открыть приложение"
                        msg["reply_markup"] = {
                            "inline_keyboard": [[
                                {"text": btn_text, "web_app": {"url": MINI_APP_URL}}
                            ]]
                        }
                    _bot_api("sendMessage", msg)
                return

    except PaymentActivationError:
        # Сбой активации ПОСЛЕ оплаты — пробрасываем, чтобы вебхук вернул не-200
        # и Telegram повторил доставку (дедуп по charge_id защитит от дубля).
        raise
    except Exception as exc:
        # Любой неожиданный сбой — логируем, наружу не пробрасываем.
        logger.warning("handle_update: общий сбой обработки апдейта: %s", exc)
        try:
            db.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Подавлено исключение: %r", exc)
