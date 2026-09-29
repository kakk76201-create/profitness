"""«Мои продукты» и фото этикетки.

Этикетка: модель переписывает КБЖУ на 100 г, неправдоподобные цифры не
принимаются; продукт сохраняется в личный список; поиск по названию находит
свои продукты первыми (кириллица без учёта регистра — в том числе в SQLite);
общий каталог по штрихкоду не содержит личных данных и переживает удаление
аккаунта, а личный список — нет.
"""
import json, os, pathlib, sys, tempfile
from unittest.mock import patch

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "prod.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"; os.environ["OWNER_ID"] = "999"
os.environ["MINI_APP_URL"] = "https://app.example.com"
os.environ.pop("RAILWAY_ENVIRONMENT", None); os.environ.pop("RAILWAY_PROJECT_ID", None)

from fastapi.testclient import TestClient
from backend import ai_service, bot_food, food_search, legal, models as M, products, telegram_bot
from backend.database import init_db, SessionLocal
from backend.main import app

init_db()
c = TestClient(app)
fails = []


def chk(name, cond, extra=""):
    if not cond:
        fails.append(name + ("  " + str(extra) if extra else ""))


c.post("/consent", json={"pd": True, "terms": True, "health": True, "cross_border": True})

# ---------- 1. Разбор ответа модели: этикетка ---------- #
LABEL = {"kind": "label", "dish_name": "Творог Простоквашино 5%", "weight_grams": 100,
         "calories": 121, "proteins": 17, "fats": 5, "carbs": 3,
         "per_100g": {"calories": 121, "proteins": 17, "fats": 5, "carbs": 3},
         "package_grams": 350, "confidence": "high", "note": ""}


def model_returns(payload):
    return patch.object(ai_service, "_call_model", lambda *a, **k: (json.dumps(payload), "stop", None))


with patch.object(ai_service, "_get_vision_client", lambda: None), model_returns(LABEL):
    res = ai_service.analyze_food_image(b"img", "image/jpeg", "ru")
chk("этикетка распознана", res["kind"] == "label" and res["per_100g"]["calories"] == 121, res)
chk("масса упаковки", res["package_grams"] == 350, res.get("package_grams"))

bad = dict(LABEL, per_100g={"calories": 2000, "proteins": 17, "fats": 5, "carbs": 3})
with patch.object(ai_service, "_get_vision_client", lambda: None), model_returns(bad):
    res = ai_service.analyze_food_image(b"img", "image/jpeg", "ru")
chk("невозможные цифры — не этикетка", res["kind"] == "dish", res["kind"])

DISH = {"kind": "dish", "dish_name": "Плов", "weight_grams": 300, "calories": 520, "proteins": 18,
        "fats": 20, "carbs": 65, "confidence": "medium", "note": ""}
with patch.object(ai_service, "_get_vision_client", lambda: None), model_returns(DISH):
    res = ai_service.analyze_food_image(b"img", "image/jpeg", "ru")
chk("обычное блюдо — dish", res["kind"] == "dish" and res.get("per_100g") is None)

# ---------- 2. /food/analyze отдаёт вид и значения на 100 г ---------- #
with patch("backend.main.analyze_food_image", lambda *a, **k: dict(LABEL, _debug={})):
    r = c.post("/food/analyze", files={"file": ("l.jpg", b"\xff\xd8x", "image/jpeg")})
chk("analyze 200", r.status_code == 200, r.text[:200])
chk("analyze: kind=label", r.json().get("kind") == "label" and r.json()["per_100g"]["proteins"] == 17, r.json())

# ---------- 3. Мои продукты: сохранить, список, поиск ---------- #
r = c.post("/products", json={"name": "Творог Простоквашино 5%", "calories": 121, "proteins": 17,
                              "fats": 5, "carbs": 3, "source": "label"})
chk("сохранение 200", r.status_code == 200, r.text[:200])
pid = r.json().get("id")
chk("сохранённый — мой", r.json().get("mine") is True)
c.post("/products", json={"name": "творог простоквашино 5%", "calories": 120, "proteins": 17,
                          "fats": 5, "carbs": 3})
lst = c.get("/products").json()["items"]
chk("то же название — без дубля", len(lst) == 1 and lst[0]["calories"] == 120, lst)
chk("невозможные цифры — 400",
    c.post("/products", json={"name": "x", "calories": 5000}).status_code == 400)
chk("пустое название — 400",
    c.post("/products", json={"name": "  ", "calories": 100}).status_code == 400)

OFF = [{"code": "1", "name": "Творог Простоквашино 5%", "brand": "", "calories": 121, "proteins": 17,
        "fats": 5, "carbs": 3},
       {"code": "2", "name": "Творог зернёный", "brand": "Савушкин", "calories": 101, "proteins": 12,
        "fats": 5, "carbs": 2}]
with patch.object(food_search, "search_products", lambda *a, **k: list(OFF)):
    items = c.get("/food/search", params={"q": "ТВОРОГ"}).json()["items"]
chk("поиск: мой первым (кириллица, другой регистр)", items and items[0].get("mine") is True, items[:1])
chk("поиск: дубль из базы убран", sum(1 for i in items if i["name"].lower() == "творог простоквашино 5%") == 1,
    [i["name"] for i in items])
chk("поиск: остальное из базы", any(i["name"] == "Творог зернёный" for i in items))

# ---------- 4. Лимит списка ---------- #
with patch.object(products, "MAX_PER_USER", 3):
    for n in range(5):
        c.post("/products", json={"name": f"Продукт {n}", "calories": 100 + n})
    total = len(c.get("/products").json()["items"])
chk("лимит списка соблюдается", total == 3, total)

# ---------- 5. Удаление из списка ---------- #
lst = c.get("/products").json()["items"]
victim = lst[-1]["id"]
chk("удаление 200", c.delete(f"/products/{victim}").status_code == 200)
chk("чужое/несуществующее — 404", c.delete("/products/999999").status_code == 404)

# ---------- 6. Бот: фото этикетки ---------- #
calls = []


def fake_api(method, payload):
    calls.append((method, payload))
    if method == "sendMessage":
        return {"message_id": len(calls)}
    if method == "getFile":
        return {"file_path": "p.jpg"}
    return True


with patch.object(telegram_bot, "_bot_api", fake_api), \
        patch.object(telegram_bot, "_download_file", lambda p: b"\xff\xd8x"), \
        patch.object(ai_service, "analyze_food_image", lambda *a, **k: dict(LABEL)):
    with SessionLocal() as db:
        telegram_bot.handle_update(db, {"message": {
            "message_id": 5, "from": {"id": 1, "language_code": "ru"},
            "chat": {"id": 1, "type": "private"}, "photo": [{"file_id": "lbl"}]}})
texts = [p.get("text", "") for m, p in calls if m == "sendMessage"]
chk("бот: КБЖУ с упаковки показаны", any(("с упаковки" in t) or ("per 100 g" in t) for t in texts), texts)
kb = [b.get("callback_data") for m, p in calls if m == "sendMessage"
      for row in (p.get("reply_markup") or {}).get("inline_keyboard", []) for b in row if b.get("callback_data")]
chk("бот: варианты граммов, включая пачку 350", any(x.endswith(":350") for x in kb), kb)
with SessionLocal() as db:
    d = db.query(M.BotMealDraft).order_by(M.BotMealDraft.id.desc()).first()
    it = json.loads(d.items_json)[0]
chk("бот: по умолчанию ≈100 г", it["qty"] == 100 and not it["qty_done"] and it["kcal"] == 121, it)

# ---------- 7. Общий каталог и удаление аккаунта ---------- #
with SessionLocal() as db:
    products.save_shared(db, "4601234567890", "Кефир 1%", {"calories": 40, "proteins": 3, "fats": 1, "carbs": 4})
    chk("общий: найден по штрихкоду", products.shared_by_barcode(db, "4601234567890") is not None)
    chk("общий: без владельца", products.shared_by_barcode(db, "4601234567890").telegram_id is None)
    # Запись из открытой базы не перетирает цифры, переписанные с упаковки.
    products.save_shared(db, "4601234567890", "Кефир 1%", {"calories": 40, "proteins": 3, "fats": 1, "carbs": 4},
                         source="label")
    products.save_shared(db, "4601234567890", "Кефир", {"calories": 99, "proteins": 3, "fats": 1, "carbs": 4},
                         source="off")
    chk("этикетка важнее открытой базы", products.shared_by_barcode(db, "4601234567890").kcal_100 == 40)

from backend import main as main_mod

with SessionLocal() as db:
    exp = main_mod._collect_export(db, 1)
chk("выгрузка: мои продукты", len(exp.get("my_products") or []) >= 1)
chk("удаление аккаунта 200", c.delete("/account/data").status_code == 200)
with SessionLocal() as db:
    mine_left = db.query(M.FoodProduct).filter(M.FoodProduct.telegram_id == 1).count()
    shared_left = db.query(M.FoodProduct).filter(M.FoodProduct.telegram_id.is_(None)).count()
chk("личные продукты удалены", mine_left == 0, mine_left)
chk("общий каталог остался", shared_left == 1, shared_left)

if fails:
    print("FAIL:\n  " + "\n  ".join(fails)); sys.exit(1)
print("OK: этикетка (и отказ от невозможных цифр), analyze отдаёт kind, мои продукты без дублей,\n"
      "    поиск — свои первыми без учёта регистра, лимит, удаление, бот спрашивает граммы,\n"
      "    общий каталог по штрихкоду без владельца, личное уходит с аккаунтом")
