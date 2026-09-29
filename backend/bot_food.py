"""
Еда прямо в чате с ботом: текст, голос и фото — без открытия приложения.

КАК ЭТО ВЫГЛЯДИТ ДЛЯ ЧЕЛОВЕКА
  1. Пишет «гречка с котлетой», наговаривает голосовое или присылает фото.
  2. Бот отвечает КАРТОЧКОЙ: что понял, сколько калорий, и кнопки —
     «✅ Добавить в обед» (приём подобран по времени), выбор другого приёма
     и «Отменить». Если что-то неясно — один вопрос с готовыми ответами
     («Сколько было гречки?» 100 г · 150 г · 200 г · 300 г, «Жарили на масле?»).
     Отвечать не обязательно: «Добавить» работает всегда, с оценкой ИИ.
  3. Пока карточка не добавлена, всё новое (ещё фото, «и чай с сахаром»)
     дописывается в тот же приём пищи.
  4. После «Добавить» карточка превращается в «✅ Добавлено» с кнопкой
     «Отменить» — ошибочное нажатие исправляется одним тапом.

  Если человек сразу сказал всё — и сколько, и какой приём («в обед съел
  200 г картошки и 200 г грудки») — вопросов нет, запись добавляется сразу.

ПОЧЕМУ ЧЕРНОВИК В БАЗЕ (bot_meal_drafts), А НЕ В ПАМЯТИ
  Вебхук не хранит состояние между сообщениями, а перезапуск сервера при
  деплое не должен терять начатый приём пищи. Старый незавершённый черновик
  не пропадает: его карточка со своими кнопками остаётся в чате.

ДОСТУП — как в приложении
  * нужны обязательные согласия и согласие на передачу данных ИИ-сервису;
  * текст — бесплатно (в пределах дневного лимита расчётов);
  * фото — тратит бесплатное сканирование, как камера в приложении;
  * голос — по подписке, как в приложении.
"""

from __future__ import annotations

import html
import json
import logging
import math
import os
import re
from datetime import datetime, timedelta

from fastapi import HTTPException

from backend import ai_service, analytics, barcode, config, legal, products, ratelimit, subscription
from backend.models import BotMealDraft, DiaryEntry, User

logger = logging.getLogger("bot_food")

MEALS = ("breakfast", "lunch", "dinner", "snack")
_MEAL_RU = {"breakfast": "завтрак", "lunch": "обед", "dinner": "ужин", "snack": "перекус"}
_MEAL_EN = {"breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner", "snack": "snack"}
_MEAL_BTN_RU = {"breakfast": "Завтрак", "lunch": "Обед", "dinner": "Ужин", "snack": "Перекус"}
_MEAL_BTN_EN = {"breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Dinner", "snack": "Snack"}
_MONTHS_RU = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря")

# Черновик, который не трогали дольше этого, считается брошенным: новое
# сообщение начнёт новый приём пищи (карточка старого остаётся в чате).
DRAFT_FRESH_MIN = int(os.getenv("BOT_DRAFT_FRESH_MIN", "120"))
# Больше вопросов на один приём пищи не задаём: остальное — по оценке ИИ.
MAX_QUESTIONS = 3
# «Много масла» — сколько граммов добавляем к заложенному (≈ 1 ст. ложка).
EXTRA_FAT_G = 10.0

try:
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo(os.getenv("APP_TZ", "Europe/Moscow"))
except Exception:  # noqa: BLE001 — без базы поясов считаем по часам сервера
    _TZ = None


# --------------------------------------------------------------------------- #
#  Время, язык, Bot API
# --------------------------------------------------------------------------- #
def local_now() -> datetime:
    """Текущее время в часовом поясе приложения (без tzinfo)."""
    now = datetime.now(_TZ) if _TZ else datetime.now()
    return now.replace(tzinfo=None)


def meal_by_time(now: datetime | None = None) -> str:
    """Приём пищи по часам: до 12 — завтрак, до 17 — обед, до 22 — ужин, позже — перекус.

    Те же границы, что у мини-приложения (mealByHour в page-diary.js).
    """
    h = (now or local_now()).hour
    if h < 12:
        return "breakfast"
    if h < 17:
        return "lunch"
    if h < 22:
        return "dinner"
    return "snack"


def _t(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru


def _api(method: str, payload: dict):
    # Через модуль, а не прямым импортом: тесты подменяют telegram_bot._bot_api.
    from backend import telegram_bot

    return telegram_bot._bot_api(method, payload)


def _send(chat_id, text: str, keyboard=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    return _api("sendMessage", payload)


def _app_button(lang: str, text_ru="Открыть приложение", text_en="Open the app"):
    if not config.MINI_APP_URL:
        return None
    return {"text": _t(lang, text_ru, text_en), "web_app": {"url": config.MINI_APP_URL}}


def _lang_for(user: User | None, message: dict | None = None) -> str:
    if user is not None and getattr(user, "language", None):
        return "en" if str(user.language).lower().startswith("en") else "ru"
    code = ""
    try:
        code = (message or {}).get("from", {}).get("language_code", "") or ""
    except Exception:  # noqa: BLE001
        code = ""
    return "en" if str(code).lower().startswith("en") else "ru"


def _fmt_num(v: float) -> str:
    v = round(float(v or 0), 1)
    return str(int(v)) if v == int(v) else str(v).replace(".", ",")


def _fmt_qty(item: dict, lang: str) -> str:
    q, unit = item.get("qty"), item.get("unit")
    if not q:
        return ""
    units_ru = {"g": "г", "ml": "мл", "pcs": "шт", "serving": "порц."}
    units_en = {"g": "g", "ml": "ml", "pcs": "pcs", "serving": "serv."}
    u = (units_en if lang == "en" else units_ru).get(unit or "serving", "")
    approx = "" if item.get("qty_done") else "≈"
    return f"{approx}{_fmt_num(q)} {u}".strip()


def _date_label(iso: str, lang: str) -> str:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
    except (TypeError, ValueError):
        return iso or ""
    today = local_now().date()
    if d.date() == today:
        return _t(lang, "сегодня", "today")
    if d.date() == today - timedelta(days=1):
        return _t(lang, "вчера", "yesterday")
    if lang == "en":
        return d.strftime("%b %d")
    return f"{d.day} {_MONTHS_RU[d.month - 1]}"


# --------------------------------------------------------------------------- #
#  Блюдо черновика
# --------------------------------------------------------------------------- #
def _item(name, qty, unit, kcal, p, f, c, *, stated, fat_g=0.0, src="text", per100=None) -> dict:
    """Блюдо черновика. base — исходная оценка, от неё считаются все пересчёты."""
    qty = float(qty) if qty else None
    it = {
        "name": str(name or "").strip()[:120] or "Блюдо",
        "qty": qty,
        "unit": unit if unit in ("g", "ml", "pcs", "serving") else ("serving" if qty else None),
        "base": {
            "qty": qty,
            "kcal": float(kcal or 0), "p": float(p or 0), "f": float(f or 0), "c": float(c or 0),
            "fat_g": float(fat_g or 0),
        },
        "fat_mode": "usual",
        # Вопрос про количество закрыт, если количество назвал сам человек.
        "qty_done": bool(stated),
        # Про масло спрашиваем, только если ИИ заложил его в оценку.
        "fat_done": not (fat_g and float(fat_g) > 0),
        "src": src,
        "per100": per100,
    }
    _recalc(it)
    return it


def _recalc(it: dict) -> None:
    """Пересчитать КБЖУ блюда из исходной оценки: количество, затем масло."""
    b = it["base"]
    k = 1.0
    if b.get("qty") and it.get("qty"):
        k = float(it["qty"]) / float(b["qty"])
    kcal, p, f, c = b["kcal"] * k, b["p"] * k, b["f"] * k, b["c"] * k
    fat = b.get("fat_g", 0.0) * k
    mode = it.get("fat_mode") or "usual"
    if mode == "none":
        kcal, f = kcal - fat * 9, f - fat
    elif mode == "more":
        extra = max(fat, EXTRA_FAT_G)
        kcal, f = kcal + extra * 9, f + extra
    it["kcal"] = max(0, int(round(kcal)))
    it["p"] = max(0.0, round(p, 1))
    it["f"] = max(0.0, round(f, 1))
    it["c"] = max(0.0, round(c, 1))


def _items(draft: BotMealDraft) -> list:
    try:
        data = json.loads(draft.items_json or "[]")
        return data if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


def _save_items(draft: BotMealDraft, items: list) -> None:
    draft.items_json = json.dumps(items, ensure_ascii=False)
    draft.updated_at = datetime.utcnow()


def _pending(items: list):
    """Текущий вопрос: сначала количество, потом масло. (вид, индекс) или None."""
    for i, it in enumerate(items):
        if not it.get("qty_done"):
            return ("qty", i)
    for i, it in enumerate(items):
        if not it.get("fat_done"):
            return ("fat", i)
    return None


def _limit_questions(items: list, start: int) -> None:
    """Не больше MAX_QUESTIONS вопросов на приём: остальное — по оценке ИИ."""
    asked = 0
    for it in items[:start]:
        asked += (not it.get("qty_done")) + (not it.get("fat_done"))
    for it in items[start:]:
        for key in ("qty_done", "fat_done"):
            if not it.get(key):
                if asked >= MAX_QUESTIONS:
                    it[key] = True
                else:
                    asked += 1


def _nice(x: float) -> float:
    """Округлить до «человеческого» числа: 80, 150, 250…"""
    if x >= 100:
        return float(math.floor(x / 50 + 0.5) * 50)
    return float(max(10, math.floor(x / 10 + 0.5) * 10))


def _qty_options(it: dict) -> list:
    unit = it.get("unit") or "serving"
    cur = float(it.get("qty") or 0)
    if it.get("src") in ("label", "barcode"):
        # Продукт с упаковки: порции от половины сотни до целой пачки.
        opts = {50.0, 100.0, 150.0, 200.0}
        pack = float(it.get("pack_g") or 0)
        if 200 < pack <= 1000:
            opts = {50.0, 100.0, 200.0, pack}
        return sorted(opts)
    if unit == "g":
        base = cur or 150
        opts = {_nice(base * 0.5), _nice(base), _nice(base * 1.5), _nice(base * 2)}
    elif unit == "ml":
        opts = {150.0, 250.0, 330.0, 500.0}
    elif unit == "pcs":
        opts = {1.0, 2.0, 3.0, 4.0}
        if cur > 4:
            opts = {1.0, 2.0, 3.0, float(round(cur))}
    else:
        opts = {0.5, 1.0, 1.5, 2.0}
    return sorted(opts)[:4]


# --------------------------------------------------------------------------- #
#  Карточка
# --------------------------------------------------------------------------- #
def _totals(items: list) -> dict:
    return {
        "kcal": sum(int(it.get("kcal") or 0) for it in items),
        "p": round(sum(float(it.get("p") or 0) for it in items), 1),
        "f": round(sum(float(it.get("f") or 0) for it in items), 1),
        "c": round(sum(float(it.get("c") or 0) for it in items), 1),
    }


def _meal_of(draft: BotMealDraft) -> str:
    return draft.meal_type if draft.meal_type in MEALS else meal_by_time()


def _items_block(items: list, lang: str) -> str:
    lines = []
    for n, it in enumerate(items, 1):
        qty = _fmt_qty(it, lang)
        mid = f" — {qty}" if qty else ""
        lines.append(f"{n}. {html.escape(it['name'])}{mid} — {it['kcal']} {_t(lang, 'ккал', 'kcal')}")
    t = _totals(items)
    lines.append("")
    lines.append(
        f"<b>{_t(lang, 'Итого', 'Total')}: {t['kcal']} {_t(lang, 'ккал', 'kcal')}</b> · "
        f"{_t(lang, 'Б', 'P')} {_fmt_num(t['p'])} · {_t(lang, 'Ж', 'F')} {_fmt_num(t['f'])} · "
        f"{_t(lang, 'У', 'C')} {_fmt_num(t['c'])}"
    )
    return "\n".join(lines)


def _card(draft: BotMealDraft, lang: str):
    """Текст и кнопки карточки в текущем состоянии черновика."""
    items = _items(draft)
    meal = _meal_of(draft)
    meal_name = (_MEAL_EN if lang == "en" else _MEAL_RU)[meal]
    date = _date_label(draft.date, lang)
    did = draft.id

    if draft.status == "added":
        text = (
            f"✅ <b>{_t(lang, 'Добавлено', 'Added')}: {meal_name}, {date}</b>\n\n"
            + _items_block(items, lang)
        )
        kb = [[{"text": _t(lang, "↩️ Отменить", "↩️ Undo"), "callback_data": f"f:{did}:u"}]]
        app_btn = _app_button(lang, "📱 Дневник", "📱 Diary")
        if app_btn:
            kb[0].append(app_btn)
        return text, kb
    if draft.status == "undone":
        return _t(lang, "↩️ Отменено — записи убраны из дневника.",
                  "↩️ Undone — the entries were removed from the diary."), None
    if draft.status == "cancelled":
        return _t(lang, "✖ Отменено, в дневник ничего не добавлено.",
                  "✖ Cancelled, nothing was added to the diary."), None

    # Открытый черновик.
    auto = "" if draft.meal_type in MEALS else _t(lang, " (по времени)", " (by time)")
    text = (
        f"🍽 <b>{meal_name.capitalize()}{auto}, {date}</b>\n\n" + _items_block(items, lang)
    )
    kb = []
    q = _pending(items)
    if q:
        kind, idx = q
        it = items[idx]
        name = html.escape(it["name"])
        if kind == "qty" and it.get("src") in ("label", "barcode"):
            per = it.get("per100") or {}
            if it.get("src") == "barcode":
                text += "\n\n🔎 " + _t(lang, f"Найдено по штрихкоду {it.get('barcode') or ''}.",
                                         f"Found by barcode {it.get('barcode') or ''}.")
            text += "\n\n🏷 " + _t(
                lang,
                f"КБЖУ с упаковки, на 100 г: {_fmt_num(per.get('calories'))} ккал · "
                f"Б {_fmt_num(per.get('proteins'))} · Ж {_fmt_num(per.get('fats'))} · "
                f"У {_fmt_num(per.get('carbs'))}. Продукт сохранён в «Мои продукты».\n"
                f"❓ Сколько граммов съели? Выберите или напишите число.",
                f"Label values per 100 g: {_fmt_num(per.get('calories'))} kcal · "
                f"P {_fmt_num(per.get('proteins'))} · F {_fmt_num(per.get('fats'))} · "
                f"C {_fmt_num(per.get('carbs'))}. Saved to My products.\n"
                f"❓ How many grams did you eat? Pick or type a number.",
            )
        elif kind == "qty":
            text += "\n\n❓ " + _t(
                lang,
                f"Сколько было: «{name}»? Выберите или напишите число.",
                f"How much «{name}» was there? Pick or type a number.",
            )
        if kind == "qty":
            row = []
            unit = it.get("unit") or "serving"
            for v in _qty_options(it):
                label = _fmt_num(v)
                if unit == "g":
                    label += _t(lang, " г", " g")
                elif unit == "ml":
                    label += _t(lang, " мл", " ml")
                elif unit == "pcs":
                    label += _t(lang, " шт", " pcs")
                else:
                    label = {0.5: "½", 1.5: "1½"}.get(v, label) + _t(lang, " порц.", " serv.")
                row.append({"text": label, "callback_data": f"f:{did}:q:{idx}:{_fmt_num(v).replace(',', '.')}"})
            kb.append(row)
        else:
            text += "\n\n🧈 " + _t(
                lang,
                f"«{name}»: жарили на масле?",
                f"«{name}»: was it cooked with oil?",
            )
            kb.append([
                {"text": _t(lang, "Без масла", "No oil"), "callback_data": f"f:{did}:o:{idx}:none"},
                {"text": _t(lang, "Как обычно", "Usual"), "callback_data": f"f:{did}:o:{idx}:usual"},
                {"text": _t(lang, "Много масла", "A lot"), "callback_data": f"f:{did}:o:{idx}:more"},
            ])
    text += "\n\n" + _t(
        lang,
        "Что-то ещё? Пришлите фото, текст или голосовое.",
        "Anything else? Send a photo, text or voice message.",
    )
    kb.append([{
        "text": _t(lang, f"✅ Добавить: {meal_name}", f"✅ Add to {meal_name}"),
        "callback_data": f"f:{did}:add",
    }])
    names = _MEAL_BTN_EN if lang == "en" else _MEAL_BTN_RU
    kb.append([
        {"text": ("✓ " if m == meal else "") + names[m], "callback_data": f"f:{did}:m:{m}"}
        for m in MEALS
    ])
    kb.append([{"text": _t(lang, "✖ Отменить", "✖ Cancel"), "callback_data": f"f:{did}:x"}])
    return text, kb


def _post_card(db, draft: BotMealDraft, lang: str) -> None:
    """Показать карточку НОВЫМ сообщением внизу чата; старую убрать.

    Используется, когда черновик изменился из-за сообщения человека: карточка
    должна оказаться под его сообщением, а не остаться где-то выше.
    """
    old_id = draft.card_message_id
    text, kb = _card(draft, lang)
    res = _send(draft.chat_id, text, kb)
    if isinstance(res, dict) and res.get("message_id"):
        draft.card_message_id = int(res["message_id"])
        db.commit()
    if old_id and old_id != draft.card_message_id:
        # Не удалось удалить (старше 48 часов) — хотя бы снимаем кнопки.
        if not _api("deleteMessage", {"chat_id": draft.chat_id, "message_id": old_id}):
            _api("editMessageReplyMarkup", {"chat_id": draft.chat_id, "message_id": old_id,
                                            "reply_markup": {"inline_keyboard": []}})


def _edit_card(draft: BotMealDraft, lang: str) -> None:
    """Обновить карточку на месте (после нажатия кнопки)."""
    if not draft.card_message_id:
        return
    text, kb = _card(draft, lang)
    payload = {"chat_id": draft.chat_id, "message_id": draft.card_message_id, "text": text,
               "parse_mode": "HTML", "disable_web_page_preview": True,
               "reply_markup": {"inline_keyboard": kb or []}}
    _api("editMessageText", payload)


# --------------------------------------------------------------------------- #
#  Черновики
# --------------------------------------------------------------------------- #
def _fresh_draft(db, tid: int):
    border = datetime.utcnow() - timedelta(minutes=DRAFT_FRESH_MIN)
    return (
        db.query(BotMealDraft)
        .filter(BotMealDraft.telegram_id == tid, BotMealDraft.status == "open",
                BotMealDraft.updated_at >= border)
        .order_by(BotMealDraft.id.desc())
        .first()
    )


def _new_draft(db, tid: int, chat_id, meal_type=None) -> BotMealDraft:
    draft = BotMealDraft(
        telegram_id=tid, chat_id=chat_id, status="open",
        date=local_now().date().isoformat(),
        meal_type=meal_type if meal_type in MEALS else None,
        items_json="[]",
    )
    db.add(draft)
    db.flush()
    return draft


def commit_draft(db, draft: BotMealDraft) -> list:
    """Записать блюда черновика в дневник. Возвращает id созданных записей."""
    meal = _meal_of(draft)
    ids = []
    for it in _items(draft):
        entry = DiaryEntry(
            telegram_id=draft.telegram_id, date=draft.date, meal_type=meal,
            dish_name=it["name"], calories=int(it.get("kcal") or 0),
            proteins=float(it.get("p") or 0), fats=float(it.get("f") or 0),
            carbs=float(it.get("c") or 0), quantity=it.get("qty"), unit=it.get("unit"),
        )
        db.add(entry)
        db.flush()
        ids.append(entry.id)
    draft.status = "added"
    draft.meal_type = meal
    draft.entry_ids_json = json.dumps(ids)
    draft.updated_at = datetime.utcnow()
    db.commit()
    analytics.track(draft.telegram_id, "bot_meal_added")
    return ids


def _undo(db, draft: BotMealDraft) -> None:
    try:
        ids = [int(x) for x in json.loads(draft.entry_ids_json or "[]")]
    except (TypeError, ValueError):
        ids = []
    if ids:
        db.query(DiaryEntry).filter(
            DiaryEntry.telegram_id == draft.telegram_id, DiaryEntry.id.in_(ids)
        ).delete(synchronize_session=False)
    draft.status = "undone"
    draft.updated_at = datetime.utcnow()
    db.commit()


# --------------------------------------------------------------------------- #
#  Проверки доступа
# --------------------------------------------------------------------------- #
def _gate(db, message: dict, need: str):
    """Общие проверки для текста/фото/голоса. Возвращает (user, lang) или None.

    need: "text" | "photo" | "voice" — от него зависят лимиты.
    """
    chat_id = message.get("chat", {}).get("id")
    tid = message.get("from", {}).get("id")
    user = db.query(User).filter(User.telegram_id == tid).first() if tid else None
    lang = _lang_for(user, message)

    if user is None or legal.needs_gate(db, tid):
        btn = _app_button(lang)
        _send(chat_id, _t(
            lang,
            "Чтобы я записывал еду в дневник, откройте приложение один раз и примите условия.",
            "To log food to your diary, open the app once and accept the terms.",
        ), [[btn]] if btn else None)
        return None
    if not legal.granted(db, tid, "cross_border"):
        _send(chat_id, _t(
            lang,
            "Распознавание еды работает на серверах ИИ-сервиса в США. Включите согласие на "
            "передачу данных в приложении: «Профиль» → «Данные».",
            "Food recognition runs on the AI service's servers in the USA. Allow data "
            "transfer in the app: Profile → Data.",
        ))
        return None

    try:
        if need == "voice":
            if not subscription.is_premium(user):
                btn = _app_button(lang, "Оформить подписку", "Subscribe")
                _send(chat_id, _t(
                    lang,
                    "Голосовой ввод доступен по подписке. Можно написать текстом — это бесплатно.",
                    "Voice input is a premium feature. You can type it instead — that's free.",
                ), [[btn]] if btn else None)
                return None
            ratelimit.enforce_ai(tid)
        elif need == "photo":
            subscription.assert_scan_available(db, user)
            ratelimit.enforce_ai(tid)
        elif need == "barcode":
            # Фото ещё не разобрано: если на нём штрихкод, ИИ не понадобится,
            # и скан не тратится. Здесь — только согласия; лимит поиска
            # спишем, если код найдётся, лимит скана — перед ИИ (_charge_scan).
            pass
        else:
            ratelimit.enforce_calc(tid, subscription.is_premium(user))
    except HTTPException as exc:
        if exc.status_code == 402:
            btn = _app_button(lang, "Оформить подписку", "Subscribe")
            _send(chat_id, _t(
                lang,
                f"Бесплатные распознавания фото на сегодня закончились ({config.FREE_SCAN_LIMIT} в день). "
                "Напишите, что съели, текстом — это бесплатно, или оформите подписку.",
                f"Today's free photo scans are used up ({config.FREE_SCAN_LIMIT} a day). "
                "Type what you ate instead — that's free — or subscribe.",
            ), [[btn]] if btn else None)
        else:
            _send(chat_id, _t(
                lang, "Слишком много запросов подряд. Попробуйте через минуту.",
                "Too many requests in a row. Try again in a minute.",
            ))
        return None
    return user, lang


# --------------------------------------------------------------------------- #
#  Входящие: текст, голос, фото
# --------------------------------------------------------------------------- #
_ANSWER_RE = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*(г|гр|грамм\w*|g|gr|grams?|шт\w*|pcs|pieces?|мл|ml|порц\w*|serv\w*)?\.?\s*$",
    re.I,
)
_OIL_RE = re.compile(r"масл|жир|сало|oil|butter|lard|fat", re.I)
_MEAL_WORDS = (
    ("breakfast", r"завтрак|breakfast"),
    ("lunch", r"обед|lunch"),
    ("dinner", r"ужин|dinner|supper"),
    ("snack", r"перекус|snack"),
)
_GRAMS_RE = re.compile(r"(\d{1,4})\s*(г|гр|грамм\w*|g)\b", re.I)


def _meal_from_words(text: str):
    low = (text or "").lower()
    for meal, pattern in _MEAL_WORDS:
        if re.search(pattern, low):
            return meal
    return None


def _answer_pending(db, draft: BotMealDraft, text: str, lang: str) -> bool:
    """Число в ответ на вопрос «Сколько было?» — применить и показать карточку."""
    m = _ANSWER_RE.match(text or "")
    if not m:
        return False
    items = _items(draft)
    q = _pending(items)
    if not q or q[0] != "qty":
        return False
    value = float(m.group(1).replace(",", "."))
    if value <= 0 or value > 5000:
        return False
    it = items[q[1]]
    it["qty"] = value
    it["qty_done"] = True
    _recalc(it)
    _save_items(draft, items)
    db.commit()
    _post_card(db, draft, lang)
    return True


def _add_items(db, message: dict, lang: str, new_items: list, meal_explicit, source_text: str) -> None:
    """Добавить блюда: сразу в дневник (если всё ясно) или в черновик с карточкой."""
    tid = message["from"]["id"]
    chat_id = message["chat"]["id"]

    # Про масло не спрашиваем, если человек сам о нём сказал.
    if _OIL_RE.search(source_text or ""):
        for it in new_items:
            it["fat_done"] = True

    # Всё названо — и количество, и приём пищи: добавляем без вопросов.
    complete = meal_explicit in MEALS and all(it.get("qty_done") for it in new_items)
    if complete:
        for it in new_items:
            it["fat_done"] = True
        draft = _new_draft(db, tid, chat_id, meal_explicit)
        _save_items(draft, new_items)
        commit_draft(db, draft)
        _post_card(db, draft, lang)
        return

    draft = _fresh_draft(db, tid)
    if draft is None:
        draft = _new_draft(db, tid, chat_id, meal_explicit)
    elif meal_explicit in MEALS:
        draft.meal_type = meal_explicit
    items = _items(draft)
    start = len(items)
    items.extend(new_items)
    _limit_questions(items, start)
    _save_items(draft, items)
    db.commit()
    _post_card(db, draft, lang)


def _items_from_parsed(parsed: dict, src: str) -> list:
    out = []
    for it in parsed.get("items") or []:
        out.append(_item(
            it.get("dish_name"), it.get("quantity"), it.get("unit"),
            it.get("calories"), it.get("proteins"), it.get("fats"), it.get("carbs"),
            stated=bool(it.get("amount_stated")), fat_g=it.get("added_fat_g") or 0, src=src,
        ))
    return out


def _typing(chat_id, action: str = "typing") -> None:
    _api("sendChatAction", {"chat_id": chat_id, "action": action})


def _food_from_text(db, message: dict, text: str, lang: str, src: str) -> None:
    chat_id = message["chat"]["id"]
    try:
        parsed = ai_service.parse_food_text(text, lang=lang)
    except ai_service.AIError:
        _send(chat_id, _t(
            lang,
            "Не нашёл в сообщении еды. Напишите, что съели, например: «гречка 200 г и котлета», "
            "или пришлите фото.",
            "I couldn't find any food in the message. Write what you ate, e.g. "
            "“200 g buckwheat and a cutlet”, or send a photo.",
        ))
        return
    items = _items_from_parsed(parsed, src)
    if not items:
        return
    _add_items(db, message, lang, items, parsed.get("meal_type"), text)


def handle_text(db, message: dict) -> None:
    """Текст, который не команда: ответ на вопрос карточки или описание еды."""
    text = (message.get("text") or "").strip()
    if not text:
        return
    tid = message["from"]["id"]

    # Сначала — не ответ ли это на вопрос открытой карточки («150», «200 г»).
    # Такой ответ не тратит ИИ и лимиты, поэтому проверяем до _gate.
    draft = _fresh_draft(db, tid)
    if draft is not None:
        user = db.query(User).filter(User.telegram_id == tid).first()
        if _answer_pending(db, draft, text, _lang_for(user, message)):
            return

    gate = _gate(db, message, "text")
    if gate is None:
        return
    user, lang = gate
    _typing(message["chat"]["id"])
    analytics.track(tid, "bot_text")
    _food_from_text(db, message, text, lang, "text")


def handle_voice(db, message: dict) -> None:
    """Голосовое или аудио: расшифровать и дальше — как текст."""
    from backend import telegram_bot

    gate = _gate(db, message, "voice")
    if gate is None:
        return
    user, lang = gate
    chat_id = message["chat"]["id"]
    media = message.get("voice") if isinstance(message.get("voice"), dict) else message.get("audio")
    file_id = (media or {}).get("file_id")
    audio = _download(file_id)
    if not audio:
        _send(chat_id, telegram_bot._voice_error_text(lang))
        return
    _typing(chat_id)
    try:
        text = ai_service.transcribe_audio(audio, "voice.ogg", lang=lang)
    except (ai_service.AIError, RuntimeError) as exc:
        logger.warning("bot voice: расшифровка не удалась (tid=%s): %s", user.telegram_id, exc)
        _send(chat_id, telegram_bot._voice_error_text(lang))
        return
    analytics.track(user.telegram_id, "bot_voice")
    if text and text.strip():
        _send(chat_id, "🎙 <i>" + html.escape(text.strip()[:500]) + "</i>")
    _food_from_text(db, message, text or "", lang, "voice")


def _download(file_id) -> bytes | None:
    from backend import telegram_bot

    if not file_id:
        return None
    info = _api("getFile", {"file_id": file_id})
    path = info.get("file_path") if isinstance(info, dict) else None
    return telegram_bot._download_file(path) if path else None


def _photo_file_id(message: dict):
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        # Telegram присылает несколько размеров — последний самый крупный.
        return photos[-1].get("file_id"), "image/jpeg"
    doc = message.get("document")
    if isinstance(doc, dict) and str(doc.get("mime_type") or "").startswith("image/"):
        return doc.get("file_id"), doc.get("mime_type")
    return None, None


def is_photo_message(message: dict) -> bool:
    return _photo_file_id(message)[0] is not None


# Штрихкод, который не нашёлся: ждём фото этикетки, чтобы записать товар
# в общий каталог под этим кодом. В памяти и ненадолго — после перезапуска
# продукт просто сохранится без привязки к коду.
_PENDING_CODES: dict = {}
PENDING_CODE_SEC = 15 * 60


def _pending_code(tid: int):
    item = _PENDING_CODES.get(tid)
    if item and item[1] > datetime.utcnow().timestamp():
        return item[0]
    _PENDING_CODES.pop(tid, None)
    return None


def _charge_scan(db, message: dict, user: User, lang: str) -> bool:
    """Проверить бесплатный лимит фото перед ИИ. False — отказ уже отправлен."""
    chat_id = message["chat"]["id"]
    try:
        subscription.assert_scan_available(db, user)
        ratelimit.enforce_ai(user.telegram_id)
    except HTTPException as exc:
        btn = _app_button(lang, "Оформить подписку", "Subscribe")
        if exc.status_code == 402:
            _send(chat_id, _t(
                lang,
                f"Бесплатные распознавания фото на сегодня закончились ({config.FREE_SCAN_LIMIT} в день). "
                "Напишите, что съели, текстом — это бесплатно, или оформите подписку. "
                "Штрихкоды известных продуктов читаются без лимита.",
                f"Today's free photo scans are used up ({config.FREE_SCAN_LIMIT} a day). "
                "Type what you ate instead — that's free — or subscribe. "
                "Barcodes of known products are read without a limit.",
            ), [[btn]] if btn else None)
        else:
            _send(chat_id, _t(lang, "Слишком много запросов подряд. Попробуйте через минуту.",
                              "Too many requests in a row. Try again in a minute."))
        return False
    return True


def handle_photo(db, message: dict) -> None:
    """Фото: штрихкод → продукт из каталога; иначе ИИ (блюдо или этикетка)."""
    gate = _gate(db, message, "barcode")
    if gate is None:
        return
    user, lang = gate
    chat_id = message["chat"]["id"]
    caption = (message.get("caption") or "").strip()
    file_id, mime = _photo_file_id(message)
    image = _download(file_id)
    if not image:
        _send(chat_id, _t(lang, "Не получилось скачать фото, пришлите ещё раз.",
                          "Couldn't download the photo, please send it again."))
        return
    if len(image) > 12 * 1024 * 1024:
        _send(chat_id, _t(lang, "Фото слишком большое.", "The photo is too large."))
        return

    _typing(chat_id, "typing")
    code = barcode.decode(image)
    if code:
        try:
            ratelimit.enforce_search(user.telegram_id)
        except HTTPException:
            _send(chat_id, _t(lang, "Слишком много запросов подряд. Попробуйте через минуту.",
                              "Too many requests in a row. Try again in a minute."))
            return
        found = barcode.lookup(db, code, lang)
        if found:
            per = {k: float(found[k]) for k in ("calories", "proteins", "fats", "carbs")}
            products.upsert_personal(db, user.telegram_id, found["name"], per,
                                     brand=found.get("brand") or "", barcode=code, source="barcode")
            analytics.track(user.telegram_id, "scan_barcode")
            it = _item(found["name"], 100, "g", per["calories"], per["proteins"], per["fats"],
                       per["carbs"], stated=False, src="barcode", per100=per)
            it["barcode"] = code
            m = _GRAMS_RE.search(caption)
            if m:
                it["qty"] = float(m.group(1))
                it["qty_done"] = True
                _recalc(it)
            _add_items(db, message, lang, [it], _meal_from_words(caption), caption)
            return

    if not _charge_scan(db, message, user, lang):
        return
    try:
        res = ai_service.analyze_food_image(image, mime or "image/jpeg", lang)
    except (ai_service.AIError, RuntimeError) as exc:
        logger.warning("bot photo: распознавание не удалось (tid=%s): %s", user.telegram_id, exc)
        _send(chat_id, _t(lang, "Не получилось распознать фото. Попробуйте кадр чётче или опишите текстом.",
                          "Couldn't recognize the photo. Try a clearer shot or describe it in text."))
        return

    name = res.get("dish_name") or ""
    no_food = name in (ai_service.NO_FOOD_NAME, ai_service.NO_FOOD_NAME_EN) or not res.get("calories")
    # Снимок штрихкода, которого нет в базе, — не распознавание еды: скан не
    # списываем, иначе человек платил бы двумя сканами (код + этикетка) за то,
    # о чём мы сами его попросили.
    if not (code and no_food):
        subscription.record_scan(db, user)
    analytics.track(user.telegram_id, "bot_photo")

    if no_food:
        if code:
            # Штрихкод прочитан, но товара нет ни у нас, ни в открытой базе.
            _PENDING_CODES[user.telegram_id] = (code, datetime.utcnow().timestamp() + PENDING_CODE_SEC)
            _send(chat_id, _t(
                lang,
                f"Штрихкод {code} прочитал, но такого товара пока нет в базе. Сфотографируйте "
                "таблицу КБЖУ на упаковке — запомню продукт, и в следующий раз он найдётся по штрихкоду.",
                f"I read barcode {code}, but this product isn't in the database yet. Take a photo of the "
                "nutrition table on the package — I'll remember it, and next time the barcode will work.",
            ))
            return
        _send(chat_id, _t(lang, "Не вижу на фото еды. Пришлите снимок блюда или напишите, что съели.",
                          "I don't see food in the photo. Send a photo of the dish or type what you ate."))
        return

    per = res.get("per_100g") if res.get("kind") == "label" else None
    if per:
        # Этикетка: цифры переписаны с упаковки. Сохраняем продукт в личный
        # список — в следующий раз он найдётся поиском по названию.
        # Код с этого же фото или с предыдущего, не найденного: товар — в общий
        # каталог, чтобы следующий скан этого штрихкода сработал сразу.
        link_code = code or _pending_code(user.telegram_id)
        products.upsert_personal(db, user.telegram_id, name, per, barcode=link_code, source="label")
        if link_code:
            products.save_shared(db, link_code, name, per, source="label")
            barcode.forget_missing(link_code)
            _PENDING_CODES.pop(user.telegram_id, None)
        it = _item(name, 100, "g", per.get("calories"), per.get("proteins"), per.get("fats"),
                   per.get("carbs"), stated=False, src="label", per100=per)
        it["pack_g"] = res.get("package_grams")
    else:
        grams = float(res.get("weight_grams") or 0) or None
        it = _item(name, grams, "g" if grams else "serving", res.get("calories"), res.get("proteins"),
                   res.get("fats"), res.get("carbs"), stated=False, src="photo")
    grams = it.get("qty")
    # Подпись к фото: «обед», «250 г» — учитываем, чтобы не переспрашивать.
    m = _GRAMS_RE.search(caption)
    if m and grams:
        it["qty"] = float(m.group(1))
        it["qty_done"] = True
        _recalc(it)
    _add_items(db, message, lang, [it], _meal_from_words(caption), caption)


# --------------------------------------------------------------------------- #
#  Кнопки карточки
# --------------------------------------------------------------------------- #
def handle_callback(db, cq: dict) -> None:
    """Нажатие кнопки на карточке: f:<draft>:<действие>[:аргументы]."""
    cq_id = cq.get("id")
    data = str(cq.get("data") or "")
    tid = (cq.get("from") or {}).get("id")
    notice = ""
    try:
        parts = data.split(":")
        if len(parts) < 3 or parts[0] != "f" or not tid:
            return
        draft = db.query(BotMealDraft).filter(
            BotMealDraft.id == int(parts[1]), BotMealDraft.telegram_id == int(tid)
        ).first()
        user = db.query(User).filter(User.telegram_id == int(tid)).first()
        lang = _lang_for(user, {"from": cq.get("from") or {}})
        if draft is None:
            notice = _t(lang, "Эта карточка устарела.", "This card is outdated.")
            return
        action = parts[2]
        items = _items(draft)

        if action == "u":
            if draft.status == "added":
                _undo(db, draft)
                notice = _t(lang, "Отменено", "Undone")
            _edit_card(draft, lang)
            return
        if draft.status != "open":
            # Двойное нажатие «Добавить» или кнопка со старой карточки.
            notice = _t(lang, "Уже сохранено.", "Already saved.")
            _edit_card(draft, lang)
            return

        if action == "add":
            if not items:
                notice = _t(lang, "Добавлять нечего.", "Nothing to add.")
                return
            commit_draft(db, draft)
            notice = _t(lang, "Добавлено в дневник", "Added to the diary")
        elif action == "x":
            draft.status = "cancelled"
            db.commit()
        elif action == "m" and len(parts) >= 4 and parts[3] in MEALS:
            draft.meal_type = parts[3]
            db.commit()
        elif action == "q" and len(parts) >= 5:
            idx, value = int(parts[3]), float(parts[4])
            if 0 <= idx < len(items) and 0 < value <= 5000:
                items[idx]["qty"] = value
                items[idx]["qty_done"] = True
                _recalc(items[idx])
                _save_items(draft, items)
                db.commit()
        elif action == "o" and len(parts) >= 5 and parts[4] in ("none", "usual", "more"):
            idx = int(parts[3])
            if 0 <= idx < len(items):
                items[idx]["fat_mode"] = parts[4]
                items[idx]["fat_done"] = True
                _recalc(items[idx])
                _save_items(draft, items)
                db.commit()
        _edit_card(draft, lang)
    except (ValueError, TypeError) as exc:
        logger.info("bot callback: битые данные %r: %s", data, exc)
    finally:
        payload = {"callback_query_id": cq_id}
        if notice:
            payload["text"] = notice
        if cq_id:
            _api("answerCallbackQuery", payload)
