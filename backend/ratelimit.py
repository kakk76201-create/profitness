"""
Простой in-memory rate-limiter (по пользователю) для AI-эндпоинтов.

Цель — ограничить стоимость/злоупотребление OpenAI: без лимитов бесплатный
/food/calculate и премиум-функции можно было бы дёргать в цикле и «нажечь»
неограниченный счёт. Храним счётчики в памяти процесса — для одного инстанса
Railway этого достаточно (после передеплоя счётчики сбрасываются, но деплои
редки, а всплеск отсекается в пределах жизни процесса).

Две границы на ключ:
  * per_min — не больше N запросов за скользящее окно 60 секунд;
  * per_day — не больше M запросов за календарные сутки (UTC).

Потокобезопасно (Lock), т.к. эндпоинты выполняются в threadpool.
"""

import logging
import os
import threading
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_minute_hits: dict[str, list] = defaultdict(list)   # ключ -> список timestamp'ов
_day_hits: dict[str, list] = defaultdict(lambda: [0, 0])  # ключ -> [день, счётчик]

# Лимиты по умолчанию (переопределяются через env).
AI_PER_MIN = int(os.getenv("RATE_LIMIT_AI_PER_MIN", "20"))
AI_PER_DAY = int(os.getenv("RATE_LIMIT_AI_PER_DAY", "200"))
# Отдельный (более узкий) дневной лимит для бесплатного /food/calculate.
CALC_PER_MIN = int(os.getenv("RATE_LIMIT_CALC_PER_MIN", "8"))
CALC_PER_DAY_FREE = int(os.getenv("RATE_LIMIT_CALC_PER_DAY", "40"))
# Тяжёлая генерация (план меню на неделю) — с кулдауном.
HEAVY_PER_MIN = int(os.getenv("RATE_LIMIT_HEAVY_PER_MIN", "2"))
HEAVY_PER_DAY = int(os.getenv("RATE_LIMIT_HEAVY_PER_DAY", "30"))
# Создание платежа у платёжного провайдера. Это обращение к внешнему API и
# запись в кабинете магазина, поэтому частоту ограничиваем — но заметно мягче
# «тяжёлых» генераций: человек может передумать, вернуться и нажать «Оплатить»
# ещё раз, и упереться в лимит на пути к деньгам он не должен.
PAYMENT_PER_MIN = int(os.getenv("RATE_LIMIT_PAYMENT_PER_MIN", "5"))
PAYMENT_PER_DAY = int(os.getenv("RATE_LIMIT_PAYMENT_PER_DAY", "40"))
# Поиск блюд во внешней базе. Лимит щедрее (это строка поиска, человек
# уточняет запрос), но не безграничен: у внешнего сервиса лимит считается по IP
# ВСЕГО сервера, поэтому один пользователь не должен его выедать.
SEARCH_PER_MIN = int(os.getenv("RATE_LIMIT_SEARCH_PER_MIN", "15"))
SEARCH_PER_DAY = int(os.getenv("RATE_LIMIT_SEARCH_PER_DAY", "300"))
# Общий потолок на ВСЕ AI-вызовы сервиса за сутки. Лимиты «на пользователя»
# не спасают от сотни дешёвых Telegram-аккаунтов: каждый в своём лимите, а
# счёт OpenAI общий. Владелец получает предупреждение на 80 % и при упоре.
AI_GLOBAL_PER_MIN = int(os.getenv("RATE_LIMIT_AI_GLOBAL_PER_MIN", "300"))
AI_GLOBAL_PER_DAY = int(os.getenv("RATE_LIMIT_AI_GLOBAL_PER_DAY", "3000"))
_GLOBAL_KEY = "global:ai"
_ALERT_SHARE = 0.8
# Состояние алертов: день (UTC) и что уже отправляли, чтобы не спамить.
_alert_state = {"day": -1, "warned": False, "capped": False}


def check(key: str, per_min: int, per_day: int) -> tuple[bool, str | None]:
    """
    Проверить и учесть один «хит» по ключу.

    Возвращает (allowed, reason): allowed=False + reason("minute"|"day"),
    если лимит превышен (хит НЕ засчитывается). Иначе (True, None).
    """
    now = time.time()
    with _lock:
        # Окно в минуту.
        recent = [t for t in _minute_hits[key] if now - t < 60.0]
        if len(recent) >= per_min:
            _minute_hits[key] = recent
            return False, "minute"

        # Суточное окно (UTC-сутки).
        day = int(now // 86400)
        d = _day_hits[key]
        if d[0] != day:
            d[0], d[1] = day, 0
        if d[1] >= per_day:
            _minute_hits[key] = recent
            return False, "day"

        # Засчитываем хит.
        recent.append(now)
        _minute_hits[key] = recent
        d[1] += 1
        return True, None


def global_hits_today() -> int:
    """Сколько AI-вызовов уже сделано сервисом за текущие сутки (UTC)."""
    with _lock:
        d = _day_hits[_GLOBAL_KEY]
        return d[1] if d[0] == int(time.time() // 86400) else 0


def _alert_owner_async(text: str) -> None:
    """Сообщение владельцу в отдельном потоке: запрос пользователя не ждёт Telegram."""
    def _send():
        try:
            from backend import telegram_bot

            telegram_bot.alert_owner(text, prefix="ИИ-лимит")
        except Exception as exc:  # noqa: BLE001 — алерт best-effort
            logger.debug("Подавлено исключение: %r", exc)

    threading.Thread(target=_send, daemon=True).start()


def _maybe_alert(used: int, capped: bool) -> None:
    """Раз в сутки предупредить на 80 % потолка и раз — при упоре в него."""
    today = int(time.time() // 86400)
    send = None
    with _lock:
        if _alert_state["day"] != today:
            _alert_state.update(day=today, warned=False, capped=False)
        if capped and not _alert_state["capped"]:
            _alert_state["capped"] = True
            send = (f"Дневной потолок ИИ исчерпан: {used}/{AI_GLOBAL_PER_DAY}. "
                    "Новые запросы получают 429 до конца суток (UTC). "
                    "Поднять: RATE_LIMIT_AI_GLOBAL_PER_DAY.")
        elif not capped and used >= AI_GLOBAL_PER_DAY * _ALERT_SHARE and not _alert_state["warned"]:
            _alert_state["warned"] = True
            send = f"ИИ-вызовы за сутки: {used}/{AI_GLOBAL_PER_DAY} (80 %). Проверьте, нет ли злоупотребления."
    if send:
        _alert_owner_async(send)


def enforce_global() -> None:
    """Общий потолок сервиса. Проверяется ПОСЛЕ лимитов пользователя, чтобы
    одиночный злоупотребитель получал свой 429, не расходуя общий счётчик."""
    ok, why = check(_GLOBAL_KEY, AI_GLOBAL_PER_MIN, AI_GLOBAL_PER_DAY)
    used = global_hits_today()
    _maybe_alert(used, capped=(not ok and why == "day"))
    if not ok:
        _raise_429(busy=True)


def enforce_ai(telegram_id: int) -> None:
    """Общий лимит на любые AI-вызовы пользователя. Бросает HTTPException 429."""
    ok, _why = check(f"ai:{telegram_id}", AI_PER_MIN, AI_PER_DAY)
    if not ok:
        _raise_429()
    enforce_global()


def enforce_calc(telegram_id: int, is_premium: bool) -> None:
    """Лимит для бесплатного расчёта КБЖУ по тексту (/food/calculate)."""
    # Премиуму даём общий AI-лимит; free — более узкий дневной кап.
    if is_premium:
        enforce_ai(telegram_id)
        return
    ok, _why = check(f"calc:{telegram_id}", CALC_PER_MIN, CALC_PER_DAY_FREE)
    if not ok:
        _raise_429()
    enforce_global()


def enforce_heavy(telegram_id: int) -> None:
    """Лимит для тяжёлых генераций (план меню на неделю и т.п.)."""
    ok, _why = check(f"heavy:{telegram_id}", HEAVY_PER_MIN, HEAVY_PER_DAY)
    if not ok:
        _raise_429()
    enforce_global()


def enforce_payment(telegram_id: int) -> None:
    """Лимит на создание платежей у провайдера (ЮKassa и т.п.).

    Отдельная корзина, а не общая «тяжёлая»: генерация меню и попытка оплатить
    не должны съедать лимит друг у друга.
    """
    ok, _why = check(f"pay:{telegram_id}", PAYMENT_PER_MIN, PAYMENT_PER_DAY)
    if not ok:
        _raise_429()


def enforce_search(telegram_id: int) -> None:
    """Лимит для поиска блюд во внешней базе продуктов."""
    ok, _why = check(f"search:{telegram_id}", SEARCH_PER_MIN, SEARCH_PER_DAY)
    if not ok:
        _raise_429()


def _raise_429(busy: bool = False) -> None:
    from fastapi import HTTPException

    if busy:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "service_busy",
                "message": "Сервис перегружен. Попробуйте через несколько минут.",
            },
        )
    raise HTTPException(
        status_code=429,
        detail={
            "error": "rate_limited",
            "message": "Слишком много запросов подряд. Немного подождите и попробуйте снова.",
        },
    )
