"""Свои рецепты, правка своих продуктов и история съеденного.

Рецепт: КБЖУ считаются из ингредиентов на вес ГОТОВОГО блюда, порция — вес
блюда на число порций; посчитанное блюдо — личный продукт (source=recipe),
который не затирается продуктом с тем же названием из поиска и не вытесняется
лимитом списка. История берётся из записей дневника с порциями: «Недавние» без
повторов (кириллица без учёта регистра), «Частые» — за 90 дней. Поиск находит
то, что человек уже ел. Рецепты попадают в выгрузку и удаляются с аккаунтом.
"""
import os, pathlib, sqlite3, sys, tempfile
from datetime import date, timedelta
from unittest.mock import patch

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "rec.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"; os.environ["OWNER_ID"] = "999"
os.environ["MINI_APP_URL"] = "https://app.example.com"
os.environ.pop("RAILWAY_ENVIRONMENT", None); os.environ.pop("RAILWAY_PROJECT_ID", None)

from fastapi.testclient import TestClient
from backend import food_search, models as M, products, recipes
from backend.database import init_db, run_migrations, SessionLocal, engine
from backend.main import app, _collect_export

init_db()
c = TestClient(app)
fails = []


def chk(name, cond, extra=""):
    if not cond:
        fails.append(name + ("  " + str(extra) if extra else ""))


c.post("/consent", json={"pd": True, "terms": True, "health": True, "cross_border": True})

# ---------- 1. Расчёт рецепта ---------- #
ING = [
    {"name": "Гречка", "grams": 200, "calories": 343, "proteins": 13, "fats": 3.4, "carbs": 62},
    {"name": "Масло сливочное", "grams": 20, "calories": 748, "proteins": 0.5, "fats": 82, "carbs": 0.8},
    {"name": "Вода", "grams": 400, "calories": 0},
]
calc = recipes.compute(ING)
raw_kcal = 343 * 2 + 748 * 0.2
chk("сырой вес = сумма", calc["raw_weight_g"] == 620, calc["raw_weight_g"])
chk("итого ккал", abs(calc["totals"]["calories"] - raw_kcal) < 0.5, calc["totals"])
chk("без веса готового — на 100 г по сумме",
    abs(calc["per100"]["calories"] - raw_kcal / 6.2) < 0.2, calc["per100"])
chk("вода 0 ккал — допустима", len(calc["ingredients"]) == 3)

calc = recipes.compute(ING, cooked_weight_g=500, servings=4)
chk("вес готового меняет 100 г", abs(calc["per100"]["calories"] - raw_kcal / 5) < 0.2, calc["per100"])
chk("порция = вес / порции", calc["serving_g"] == 125, calc["serving_g"])


def err(*a, **k):
    try:
        recipes.compute(*a, **k)
    except recipes.RecipeError as exc:
        return str(exc)
    return None


chk("без ингредиентов — ошибка", err([]) is not None)
chk("только вода — нет калорий", "калорий" in (err([ING[2]]) or ""), err([ING[2]]))
chk("крошечный вес готового — ошибка", err(ING, cooked_weight_g=5) is not None)
chk("порций 0 — ошибка", err(ING, servings=0) is not None)
chk("кривой ингредиент отброшен",
    len(recipes.compute(ING + [{"name": "x", "grams": -5, "calories": 10}])["ingredients"]) == 3)

# ---------- 2. API рецептов ---------- #
r = c.post("/recipes", json={"name": "Гречка с маслом", "ingredients": ING,
                             "cooked_weight_g": 500, "servings": 4})
chk("создание 200", r.status_code == 200, r.text[:300])
rec = r.json()
prod = rec.get("product") or {}
chk("продукт рецепта", prod.get("kind") == "recipe" and prod.get("serving_g") == 125, prod)
chk("КБЖУ на 100 г посчитаны сервером", abs(prod.get("calories", 0) - raw_kcal / 5) < 1, prod)
chk("пустое название — 400",
    c.post("/recipes", json={"name": " ", "ingredients": ING}).status_code == 400)
r = c.post("/recipes", json={"name": "Вода", "ingredients": [ING[2]]})
chk("ошибка расчёта — 400 с текстом", r.status_code == 400 and "калорий" in r.text, r.text[:200])

lst = c.get("/recipes").json()["items"]
chk("список рецептов", len(lst) == 1 and len(lst[0]["ingredients"]) == 3, lst)
plist = c.get("/products").json()["items"]
chk("рецепт есть в «Моих продуктах» с видом recipe",
    any(p["kind"] == "recipe" and p["id"] == prod["id"] for p in plist), plist)

with patch.object(food_search, "search_products", lambda *a, **k: [
        {"code": "5", "name": "Гречка с маслом", "brand": "", "calories": 150,
         "proteins": 5, "fats": 4, "carbs": 25}]):
    items = c.get("/food/search", params={"q": "ГРЕЧКА"}).json()["items"]
chk("поиск: свой рецепт первым", items and items[0].get("kind") == "recipe", items[:2])

# Продукт с тем же названием из поиска не затирает рецепт.
r = c.post("/products", json={"name": "гречка с маслом", "calories": 150, "proteins": 5,
                              "fats": 4, "carbs": 25, "source": "search"})
chk("одноимённый продукт — отдельная строка", r.status_code == 200 and r.json()["id"] != prod["id"], r.json())
with SessionLocal() as db:
    row = db.get(M.FoodProduct, prod["id"])
    chk("рецепт не затёрт", row.source == "recipe" and abs(row.kcal_100 - raw_kcal / 5) < 1,
        (row.source, row.kcal_100))
chk("рецепт нельзя править как продукт",
    c.put(f"/products/{prod['id']}", json={"name": "x", "calories": 100}).status_code == 400)

# Переименование — тот же продукт, без сироты.
r = c.put(f"/recipes/{rec['id']}", json={"name": "Гречка по-домашнему", "ingredients": ING[:2],
                                          "servings": 2})
chk("правка 200", r.status_code == 200, r.text[:300])
chk("правка: тот же продукт", r.json()["product"]["id"] == prod["id"], r.json()["product"])
chk("правка: новое название и порция",
    r.json()["product"]["name"] == "Гречка по-домашнему" and r.json()["product"]["serving_g"] == 110,
    r.json()["product"])
with SessionLocal() as db:
    n = db.query(M.FoodProduct).filter(M.FoodProduct.source == "recipe").count()
chk("сирот нет", n == 1, n)

# Лимит «Моих продуктов» рецепт не вытесняет.
with patch.object(products, "MAX_PER_USER", 2):
    for n in range(4):
        c.post("/products", json={"name": f"Вытесняемый {n}", "calories": 100 + n})
with SessionLocal() as db:
    chk("рецепт пережил лимит", db.get(M.FoodProduct, prod["id"]) is not None)

# Чужой рецепт не виден.
with SessionLocal() as db:
    other, _ = recipes.save(db, 777, "Чужой суп", ING)
    other_id, other_pid = other.id, other.product_id
chk("чужой рецепт — 404 на правку",
    c.put(f"/recipes/{other_id}", json={"name": "x", "ingredients": ING}).status_code == 404)
chk("чужой рецепт — 404 на удаление", c.delete(f"/recipes/{other_id}").status_code == 404)
chk("чужой рецепт не в списке", all(x["id"] != other_id for x in c.get("/recipes").json()["items"]))
chk("чужой продукт — 404 на «съеден»", c.post(f"/products/{other_pid}/used").status_code == 404)

# Удаление продукта-рецепта удаляет рецепт; удаление рецепта — продукт.
r2 = c.post("/recipes", json={"name": "Омлет", "ingredients": [
    {"name": "Яйцо", "grams": 120, "calories": 157, "proteins": 12.7, "fats": 11.5, "carbs": 0.7}]}).json()
chk("удаление продукта-рецепта 200", c.delete(f"/products/{r2['product']['id']}").status_code == 200)
chk("рецепт ушёл вместе с продуктом", all(x["id"] != r2["id"] for x in c.get("/recipes").json()["items"]))
chk("удаление рецепта 200", c.delete(f"/recipes/{rec['id']}").status_code == 200)
with SessionLocal() as db:
    chk("продукт ушёл вместе с рецептом", db.get(M.FoodProduct, prod["id"]) is None)

# ---------- 3. Свой продукт: правка и порция ---------- #
r = c.post("/products", json={"name": "Сырник", "calories": 220, "proteins": 15, "fats": 9,
                              "carbs": 20, "serving_g": 60, "source": "manual"})
chk("продукт с порцией", r.status_code == 200 and r.json()["serving_g"] == 60, r.json())
pid = r.json()["id"]
r = c.put(f"/products/{pid}", json={"name": "Сырник домашний", "calories": 230, "proteins": 15,
                                    "fats": 10, "carbs": 20, "serving_g": None})
chk("правка продукта", r.status_code == 200 and r.json()["name"] == "Сырник домашний"
    and r.json()["serving_g"] is None, r.json())
chk("правка: невозможные цифры — 400",
    c.put(f"/products/{pid}", json={"name": "x", "calories": 5000}).status_code == 400)
chk("правка чужого — 404", c.put(f"/products/{other_pid}", json={"name": "x", "calories": 100}).status_code == 404)
with SessionLocal() as db:
    before = db.get(M.FoodProduct, pid).uses
chk("«съеден» 200", c.post(f"/products/{pid}/used").status_code == 200)
with SessionLocal() as db:
    chk("«съеден» поднимает счётчик", db.get(M.FoodProduct, pid).uses == before + 1)

# ---------- 4. История ---------- #
today = date.today()


def eat(name, days_ago, qty, unit, kcal, meal="lunch"):
    r = c.post("/food/manual", json={
        "date": (today - timedelta(days=days_ago)).isoformat(), "meal_type": meal,
        "dish_name": name, "calories": kcal, "proteins": 5, "fats": 3, "carbs": 20,
        "quantity": qty, "unit": unit})
    assert r.status_code == 200, r.text


eat("Овсянка на молоке", 120, 200, "g", 250)   # старше 90 дней — в «Частые» не идёт
eat("Яблоко", 100, 1, "pcs", 50)
eat("Яблоко", 95, 1, "pcs", 50)
eat("Овсянка на молоке", 5, 200, "g", 250, "breakfast")
eat("ОВСЯНКА НА МОЛОКЕ", 2, 250, "g", 312, "breakfast")
eat("Кофе с молоком", 1, 1, "serving", 60)
eat("Кофе с молоком", 0, 1, "serving", 60)
eat("Кофе с молоком", 0, 2, "serving", 120)

h = c.get("/food/history").json()
recent = h["recent"]
names = [i["dish_name"].casefold() for i in recent]
chk("недавние без повторов (кириллица)", len(names) == len(set(names)), names)
chk("недавние: последнее съеденное первым", recent and recent[0]["dish_name"] == "Кофе с молоком"
    and recent[0]["quantity"] == 2, recent[:1])
oat = next((i for i in recent if i["dish_name"].casefold() == "овсянка на молоке"), {})
chk("недавние: последняя порция", oat.get("quantity") == 250 and oat.get("unit") == "g", oat)

freq = h["frequent"]
chk("частые: кофе первым (3 раза)", freq and freq[0]["dish_name"] == "Кофе с молоком"
    and freq[0]["count"] == 3, freq[:1])
oat_f = next((i for i in freq if i["dish_name"].casefold() == "овсянка на молоке"), {})
chk("частые: старше 90 дней не считается", oat_f.get("count") == 2, oat_f)
chk("частые: яблоко за 90 дней — один раз, не попадает",
    all(i["dish_name"] != "Яблоко" for i in freq), [i["dish_name"] for i in freq])

with patch.object(food_search, "search_products", lambda *a, **k: []):
    items = c.get("/food/search", params={"q": "овсян"}).json()["items"]
hist = [i for i in items if i.get("kind") == "history"]
chk("поиск: ели раньше — с порцией", hist and hist[0]["quantity"] == 250 and hist[0]["unit"] == "g", items)

# ---------- 5. Выгрузка и удаление аккаунта ---------- #
c.post("/recipes", json={"name": "Салат", "ingredients": [
    {"name": "Огурец", "grams": 150, "calories": 15, "proteins": 0.8, "fats": 0.1, "carbs": 2.8}]})
with SessionLocal() as db:
    exp = _collect_export(db, 1)
chk("рецепты в выгрузке", len(exp.get("recipes") or []) == 1, exp.get("recipes"))
chk("удаление аккаунта 200", c.delete("/account/data").status_code == 200)
with SessionLocal() as db:
    chk("рецепты удалены", db.query(M.Recipe).filter(M.Recipe.telegram_id == 1).count() == 0)
    chk("чужие рецепты на месте", db.query(M.Recipe).filter(M.Recipe.telegram_id == 777).count() == 1)

# ---------- 6. Миграция старой таблицы food_products ---------- #
with engine.begin() as conn:
    conn.exec_driver_sql("DROP TABLE food_products")
    conn.exec_driver_sql(
        "CREATE TABLE food_products (id INTEGER PRIMARY KEY, telegram_id BIGINT, barcode VARCHAR, "
        "name VARCHAR, name_key VARCHAR, brand VARCHAR, kcal_100 FLOAT, p_100 FLOAT, f_100 FLOAT, "
        "c_100 FLOAT, source VARCHAR, uses INTEGER, last_used_at DATETIME, created_at DATETIME, "
        "updated_at DATETIME)")
    conn.exec_driver_sql(
        "INSERT INTO food_products (telegram_id, name, name_key, kcal_100, source) "
        "VALUES (5, 'Старый', 'старый', 100, 'manual')")
run_migrations()
run_migrations()
db_path = os.environ["DATABASE_URL"].replace("sqlite:///", "")
cols = [r[1] for r in sqlite3.connect(db_path).execute("PRAGMA table_info(food_products)")]
chk("миграция: serving_g добавлена", "serving_g" in cols, cols)
chk("миграция: данные целы",
    sqlite3.connect(db_path).execute("SELECT name FROM food_products").fetchall() == [("Старый",)])

if fails:
    print("FAIL:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("OK: рецепты, свои продукты и история")
