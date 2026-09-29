"""Еда в чате с ботом: текст, фото, голос, карточка с кнопками.

Проверяем то, что видит человек: сказал всё — добавлено сразу; не сказал
количество — карточка с вопросом и «Добавить»; ответы и кнопки меняют
черновик; «Добавить» не записывает дважды; «Отменить» убирает записи;
доступ как в приложении; вебхук отвечает сразу и не обрабатывает дубли.
"""
import json, os, pathlib, sys, tempfile
from datetime import datetime
from unittest.mock import patch

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "botfood.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"; os.environ["OWNER_ID"] = "999"
os.environ["MINI_APP_URL"] = "https://app.example.com"
os.environ["FREE_SCAN_LIMIT"] = "2"
os.environ.pop("RAILWAY_ENVIRONMENT", None); os.environ.pop("RAILWAY_PROJECT_ID", None)
os.environ.pop("TELEGRAM_WEBHOOK_SECRET", None)

from fastapi.testclient import TestClient
from backend import ai_service, bot_food, config, legal, models as M, payment_providers, telegram_bot
from backend.database import init_db, SessionLocal
from backend.main import app

init_db()
client = TestClient(app)
fails = []


def chk(name, cond, extra=""):
    if not cond:
        fails.append(name + ("  " + str(extra) if extra else ""))


# --------------------------------------------------------------------------- #
#  Подмены: Bot API и ИИ
# --------------------------------------------------------------------------- #
calls = []
_msg_id = [100]


def fake_api(method, payload):
    calls.append((method, payload))
    if method == "sendMessage":
        _msg_id[0] += 1
        return {"message_id": _msg_id[0]}
    if method == "getFile":
        return {"file_path": "photos/x.jpg"}
    return True


PARSED = {}


def fake_parse(text, lang="ru"):
    if text not in PARSED:
        raise ai_service.AIError("нет еды")
    return PARSED[text]


def item(name, qty, unit, kcal, p, f, c, stated=True, fat=0.0):
    return {"dish_name": name, "quantity": qty, "unit": unit, "calories": kcal, "proteins": p,
            "fats": f, "carbs": c, "amount_stated": stated, "added_fat_g": fat}


PARSED["в обед съел 200 г жареной картошки и 200 г куриной грудки"] = {
    "meal_type": "lunch",
    "items": [item("картошка жареная", 200, "g", 380, 5, 20, 45, True, 15),
              item("куриная грудка", 200, "g", 330, 62, 7, 0, True)],
}
PARSED["гречка с котлетой"] = {
    "meal_type": None,
    "items": [item("гречка", 200, "g", 220, 8, 2, 40, stated=False),
              item("котлета жареная", 1, "pcs", 250, 15, 18, 8, stated=True, fat=10)],
}
PARSED["и чай с сахаром"] = {"meal_type": None, "items": [item("чай с сахаром", 1, "serving", 40, 0, 0, 10)]}
PARSED["съел 3 яйца"] = {"meal_type": None, "items": [item("яйцо варёное", 3, "pcs", 234, 19, 16, 1)]}

PHOTO = {"dish_name": "Паста карбонара", "weight_grams": 300, "calories": 600, "proteins": 24,
         "fats": 28, "carbs": 62, "confidence": "medium", "note": ""}

patches = [
    patch.object(telegram_bot, "_bot_api", fake_api),
    patch.object(telegram_bot, "_download_file", lambda path: b"\xff\xd8fakejpeg"),
    patch.object(ai_service, "parse_food_text", fake_parse),
    patch.object(ai_service, "analyze_food_image", lambda b, m, lang="ru": dict(PHOTO)),
    patch.object(ai_service, "transcribe_audio", lambda b, n, lang="ru": "гречка с котлетой"),
    patch.object(bot_food, "local_now", lambda: datetime(2026, 9, 29, 13, 30)),
]
for p in patches:
    p.start()

# Тот же id, что у dev-пользователя тестового клиента: так можно проверить
# удаление аккаунта через настоящий маршрут приложения.
TID = 1


def msg(text=None, tid=TID, **extra):
    m = {"message_id": 1, "from": {"id": tid, "first_name": "Аня", "language_code": "ru"},
         "chat": {"id": tid, "type": "private"}}
    if text is not None:
        m["text"] = text
    m.update(extra)
    return m


def send(update):
    with SessionLocal() as db:
        telegram_bot.handle_update(db, update)


def upd(text=None, **extra):
    return {"message": msg(text, **extra)}


def press(data, tid=TID):
    calls.clear()
    send({"callback_query": {"id": "cb1", "from": {"id": tid}, "data": data,
                             "message": {"message_id": 1, "chat": {"id": tid}}}})


def entries(tid=TID):
    with SessionLocal() as db:
        return db.query(M.DiaryEntry).filter(M.DiaryEntry.telegram_id == tid).all()


def last_draft(tid=TID):
    with SessionLocal() as db:
        d = db.query(M.BotMealDraft).filter(M.BotMealDraft.telegram_id == tid).order_by(M.BotMealDraft.id.desc()).first()
        if d is not None:
            db.expunge(d)
        return d


def sent_texts():
    return [p.get("text", "") for m, p in calls if m in ("sendMessage", "editMessageText")]


def buttons():
    for m, p in reversed(calls):
        kb = (p.get("reply_markup") or {}).get("inline_keyboard")
        if m in ("sendMessage", "editMessageText") and kb is not None:
            return [b.get("callback_data") for row in kb for b in row if b.get("callback_data")]
    return []


# ---------- 0. Без согласий — просим открыть приложение, ИИ не зовём ---------- #
calls.clear()
send(upd("гречка с котлетой"))
chk("без согласий: просим открыть приложение",
    any("откройте приложение" in t for t in sent_texts()), sent_texts())
chk("без согласий: черновика нет", last_draft() is None)

with SessionLocal() as db:
    legal.save(db, TID, {"pd": True, "terms": True, "health": True})
calls.clear()
send(upd("гречка с котлетой"))
chk("без согласия на ИИ: объясняем, где включить",
    any("Профиль" in t for t in sent_texts()), sent_texts())
with SessionLocal() as db:
    legal.save(db, TID, {"cross_border": True})

# ---------- 1. Всё сказано — добавлено сразу, без вопросов ---------- #
calls.clear()
send(upd("в обед съел 200 г жареной картошки и 200 г куриной грудки"))
es = entries()
chk("полная фраза: 2 записи сразу", len(es) == 2, len(es))
chk("полная фраза: в обед", all(e.meal_type == "lunch" for e in es), [e.meal_type for e in es])
chk("полная фраза: местная дата", all(e.date == "2026-09-29" for e in es), [e.date for e in es])
chk("полная фраза: количество сохранено", es and es[0].quantity == 200 and es[0].unit == "g")
chk("полная фраза: карточка «Добавлено»", any("Добавлено" in t for t in sent_texts()), sent_texts())
chk("полная фраза: есть «Отменить»", any(b.endswith(":u") for b in buttons()), buttons())
chk("полная фраза: вопросов нет", not any(":q:" in b or ":o:" in b for b in buttons()), buttons())
d_full = last_draft()

# ---------- 2. «Отменить» убирает записи ---------- #
press(f"f:{d_full.id}:u")
chk("отмена: записи удалены", len(entries()) == 0, len(entries()))
chk("отмена: статус undone", last_draft().status == "undone")
chk("отмена: ответ на нажатие", any(m == "answerCallbackQuery" for m, _ in calls))

# ---------- 3. Количество не названо — карточка с вопросом ---------- #
calls.clear()
send(upd("гречка с котлетой"))
d = last_draft()
chk("неполная фраза: в дневник не пишем", len(entries()) == 0)
chk("неполная фраза: черновик открыт", d.status == "open" and len(json.loads(d.items_json)) == 2)
chk("неполная фраза: вопрос про гречку", any(f"f:{d.id}:q:0:" in b for b in buttons()), buttons())
chk("неполная фраза: «Добавить» есть всегда", f"f:{d.id}:add" in buttons(), buttons())
chk("неполная фраза: приём по времени — обед", any("Обед" in t for t in sent_texts()), sent_texts())
chk("неполная фраза: оценка помечена ≈", any("≈200" in t for t in sent_texts()), sent_texts())

# Ответ числом — применяется к вопросу, ИИ не зовётся.
calls.clear()
with patch.object(ai_service, "parse_food_text", side_effect=AssertionError("ИИ не нужен")):
    send(upd("150"))
it = json.loads(last_draft().items_json)[0]
chk("ответ «150»: количество", it["qty"] == 150 and it["qty_done"], it)
chk("ответ «150»: калории пересчитаны", it["kcal"] == 165, it["kcal"])
chk("ответ «150»: карточка перенесена вниз",
    any(m == "deleteMessage" for m, _ in calls) and any(m == "sendMessage" for m, _ in calls))

# Следующий вопрос — масло для котлеты.
chk("затем вопрос про масло", any(f"f:{d.id}:o:1:none" == b for b in buttons()), buttons())
press(f"f:{d.id}:o:1:none")
it1 = json.loads(last_draft().items_json)[1]
chk("без масла: минус 10 г жира", it1["kcal"] == 160 and it1["f"] == 8.0, it1)
chk("кнопка правит карточку на месте", any(m == "editMessageText" for m, _ in calls))

# Ещё сообщение — дописывается в тот же приём.
calls.clear()
send(upd("и чай с сахаром"))
chk("дописали в тот же черновик", last_draft().id == d.id and len(json.loads(last_draft().items_json)) == 3)

# Смена приёма кнопкой.
press(f"f:{d.id}:m:dinner")
chk("приём сменён на ужин", last_draft().meal_type == "dinner")

# Добавить — и повторное нажатие ничего не дублирует.
press(f"f:{d.id}:add")
es = entries()
chk("добавлено 3 записи в ужин", len(es) == 3 and all(e.meal_type == "dinner" for e in es),
    [(e.dish_name, e.meal_type) for e in es])
press(f"f:{d.id}:add")
chk("второе нажатие не дублирует", len(entries()) == 3, len(entries()))
chk("второе нажатие: «Уже сохранено»",
    any(p.get("text") == "Уже сохранено." for m, p in calls if m == "answerCallbackQuery"), calls)

# Чужая кнопка не работает.
press(f"f:{d.id}:u", tid=7777)
chk("чужой не может отменить", len(entries()) == 3)

# ---------- 4. Количество названо, приём — нет: одна кнопка ---------- #
calls.clear()
send(upd("съел 3 яйца"))
d2 = last_draft()
chk("без приёма: черновик, не сразу", d2.status == "open" and len(entries()) == 3)
chk("без приёма: вопросов нет", not any(":q:" in b for b in buttons()), buttons())
press(f"f:{d2.id}:x")
chk("отмена черновика", last_draft().status == "cancelled" and len(entries()) == 3)

# ---------- 5. Фото: распознано, порцию можно уточнить; лимит бесплатных ---------- #
calls.clear()
send(upd(photo=[{"file_id": "s"}, {"file_id": "big"}]))
d3 = last_draft()
items3 = json.loads(d3.items_json)
chk("фото: блюдо в черновике", items3 and items3[0]["name"] == "Паста карбонара", items3)
chk("фото: взят самый крупный размер", any(p.get("file_id") == "big" for m, p in calls if m == "getFile"))
chk("фото: вопрос про порцию", any(f"f:{d3.id}:q:0:" in b for b in buttons()), buttons())
with SessionLocal() as db:
    u = db.query(M.User).filter(M.User.telegram_id == TID).first()
    chk("фото: скан засчитан", (u.daily_scans_used or 0) == 1, u.daily_scans_used)

calls.clear()
send(upd(photo=[{"file_id": "p2"}], caption="ужин, 150 г"))
it = json.loads(last_draft().items_json)[-1]
chk("подпись «150 г» учтена", it["qty"] == 150 and it["qty_done"] and it["kcal"] == 300, it)
chk("подпись «ужин, 150 г» — всё ясно, записано сразу",
    last_draft().status == "added" and last_draft().meal_type == "dinner", last_draft().status)

calls.clear()
send(upd(photo=[{"file_id": "p3"}]))
chk("лимит фото: честный отказ", any("закончились" in t for t in sent_texts()), sent_texts())

# ---------- 6. Голос: только по подписке ---------- #
calls.clear()
send(upd(voice={"file_id": "v1"}))
chk("голос без подписки: предложение", any("по подписке" in t for t in sent_texts()), sent_texts())
with SessionLocal() as db:
    payment_providers.activate_premium(db, TID, "monthly", "yookassa", 699.0, "RUB", charge_id="yk:bf")
calls.clear()
send(upd(voice={"file_id": "v1"}))
chk("голос: расшифровка показана", any("гречка с котлетой" in t for t in sent_texts()), sent_texts())
# Фото с подписью «ужин, 150 г» было полным — оно записалось сразу отдельным
# приёмом, а голосовое дописалось в открытый черновик с первым фото.
with SessionLocal() as db:
    open_d = db.query(M.BotMealDraft).filter(M.BotMealDraft.telegram_id == TID,
                                              M.BotMealDraft.status == "open").order_by(M.BotMealDraft.id.desc()).first()
    chk("голос: блюда в открытом черновике", open_d is not None and "котлета" in open_d.items_json,
        open_d.items_json[:200] if open_d else None)

# ---------- 7. Что бот игнорирует ---------- #
before = last_draft().id
calls.clear()
send({"edited_message": msg("съел 3 яйца")})
chk("правка сообщения не добавляет еду", last_draft().id == before and not calls, calls)
send({"message": dict(msg("съел 3 яйца"), chat={"id": -100, "type": "group"})})
chk("группа: еду не пишем", last_draft().id == before)
calls.clear()
send(upd("/unknown"))
chk("неизвестная команда молчит", not calls, calls)
calls.clear()
send(upd("привет, как дела"))
chk("не еда: подсказка", any("Не нашёл" in t for t in sent_texts()), sent_texts())

# ---------- 8. Вебхук: ответ сразу, дубли не обрабатываются ---------- #
chk("еда — в фоне", telegram_bot.is_background_update(upd("гречка с котлетой")))
chk("оплата — синхронно", not telegram_bot.is_background_update(
    {"message": dict(msg(), successful_payment={"invoice_payload": "x"})}))
chk("команда — синхронно", not telegram_bot.is_background_update(upd("/start")))

n_before = len(entries())
u1 = {"update_id": 424242, "message": msg("в обед съел 200 г жареной картошки и 200 г куриной грудки")}
r = client.post("/telegram/webhook", json=u1)
chk("вебхук отвечает 200", r.status_code == 200, r.status_code)
chk("вебхук: фоновая обработка сработала", len(entries()) == n_before + 2, len(entries()))
client.post("/telegram/webhook", json=u1)
chk("повтор того же update_id не дублирует", len(entries()) == n_before + 2, len(entries()))

# ---------- 9. Самопочинка allowed_updates ---------- #
hook = []


def hook_api(method, payload):
    hook.append((method, payload))
    if method == "getWebhookInfo":
        return {"url": "https://app.example.com/telegram/webhook",
                "allowed_updates": ["message", "pre_checkout_query"]}
    return True


with patch.object(telegram_bot, "_bot_api", hook_api), patch.object(telegram_bot, "BOT_TOKEN", "t"), \
        patch.object(config, "TELEGRAM_WEBHOOK_SECRET", "sec"):
    telegram_bot.ensure_webhook_updates()
sw = [p for m, p in hook if m == "setWebhook"]
chk("setWebhook с callback_query", sw and "callback_query" in sw[0]["allowed_updates"], hook)
chk("секрет сохранён", sw and sw[0]["secret_token"] == "sec")

hook.clear()


def hook_ok(method, payload):
    hook.append((method, payload))
    return {"url": "https://x", "allowed_updates": ["message", "callback_query"]}


with patch.object(telegram_bot, "_bot_api", hook_ok), patch.object(telegram_bot, "BOT_TOKEN", "t"), \
        patch.object(config, "TELEGRAM_WEBHOOK_SECRET", "sec"):
    telegram_bot.ensure_webhook_updates()
chk("уже настроено — не трогаем", not any(m == "setWebhook" for m, _ in hook), hook)

# ---------- 10. Время: границы приёмов пищи ---------- #
chk("11:59 — завтрак", bot_food.meal_by_time(datetime(2026, 1, 1, 11, 59)) == "breakfast")
chk("16:59 — обед", bot_food.meal_by_time(datetime(2026, 1, 1, 16, 59)) == "lunch")
chk("21:00 — ужин", bot_food.meal_by_time(datetime(2026, 1, 1, 21, 0)) == "dinner")
chk("23:30 — перекус", bot_food.meal_by_time(datetime(2026, 1, 1, 23, 30)) == "snack")

# ---------- 11. Черновики — данные человека: выгрузка и удаление ---------- #
from backend import main as main_mod

with SessionLocal() as db:
    data = main_mod._collect_export(db, TID)
chk("выгрузка содержит черновики", len(data.get("bot_meal_drafts") or []) > 0)
chk("удаление аккаунта 200", client.delete("/account/data").status_code == 200)
chk("черновики удалены вместе с аккаунтом", last_draft() is None)

for p in patches:
    p.stop()

if fails:
    print("FAIL:\n  " + "\n  ".join(fails)); sys.exit(1)
print("OK: полная фраза — сразу в обед; неполная — карточка с вопросом; ответ числом без ИИ;\n"
      "    масло, смена приёма, дописывание, «Добавить» без дублей, «Отменить»; фото с подписью\n"
      "    и лимитом; голос по подписке; правки/группы/команды игнорируются; вебхук в фоне без\n"
      "    дублей; самопочинка allowed_updates; границы по времени; черновики в выгрузке")
