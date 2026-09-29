"""Штрихкоды: код читается с настоящего JPEG, продукт ищется сначала в своём
каталоге, потом в Open Food Facts (с кэшем и лимитом), найденный по коду
продукт не тратит бесплатный скан и не зовёт ИИ, этикетка после ненайденного
кода пополняет общий каталог — в приложении и в боте."""
import io, json, os, pathlib, sys, tempfile
from unittest.mock import patch

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "bc.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"; os.environ["OWNER_ID"] = "999"
os.environ["MINI_APP_URL"] = "https://app.example.com"; os.environ["FREE_SCAN_LIMIT"] = "10"
os.environ.pop("RAILWAY_ENVIRONMENT", None); os.environ.pop("RAILWAY_PROJECT_ID", None)

import zxingcpp
from PIL import Image, ImageFilter
from fastapi.testclient import TestClient
from backend import ai_service, barcode, models as M, products, telegram_bot
from backend.database import init_db, SessionLocal
from backend.main import app

init_db()
c = TestClient(app)
fails = []


def chk(name, cond, extra=""):
    if not cond:
        fails.append(name + ("  " + str(extra) if extra else ""))


def barcode_jpeg(code: str) -> bytes:
    """«Фото» штрихкода: код на фоне, лёгкий поворот и размытие, JPEG."""
    bc = zxingcpp.create_barcode(code, zxingcpp.BarcodeFormat.EAN13)
    mv = memoryview(zxingcpp.write_barcode_to_image(bc, scale=3))
    pic = Image.frombytes("L", (mv.shape[1], mv.shape[0]), mv.tobytes())
    canvas = Image.new("L", (1280, 960), 205)
    canvas.paste(pic, (330, 360))
    photo = canvas.rotate(6, fillcolor=205).filter(ImageFilter.GaussianBlur(1.0))
    buf = io.BytesIO()
    photo.convert("RGB").save(buf, "JPEG", quality=75)
    return buf.getvalue()


c.post("/consent", json={"pd": True, "terms": True, "health": True, "cross_border": True})
KEFIR = "4607001771234"  # контрольная цифра не важна для теста поиска
KEFIR = KEFIR[:12] + str((10 - sum((3 if i % 2 else 1) * int(d) for i, d in enumerate(KEFIR[:12])) % 10) % 10)
MISSING = "4600000000017"
MISSING = MISSING[:12] + str((10 - sum((3 if i % 2 else 1) * int(d) for i, d in enumerate(MISSING[:12])) % 10) % 10)

# ---------- 1. Чтение кода ---------- #
chk("код читается с JPEG", barcode.decode(barcode_jpeg(KEFIR)) == KEFIR, barcode.decode(barcode_jpeg(KEFIR)))
chk("мусор — нет кода, без исключения", barcode.decode(b"not an image") is None)
plain = io.BytesIO(); Image.new("RGB", (640, 480), (120, 80, 40)).save(plain, "JPEG")
chk("фото без кода — None", barcode.decode(plain.getvalue()) is None)

# ---------- 2. Поиск: Open Food Facts, каталог, кэш «нет» ---------- #
off_calls = []


class Resp:
    def __init__(self, status, data):
        self.status_code, self._d = status, data

    def json(self):
        return self._d


def fake_get(url, params=None, timeout=None, headers=None):
    off_calls.append(url)
    if KEFIR in url:
        return Resp(200, {"status": 1, "product": {
            "product_name": "Kefir", "product_name_ru": "Кефир 1%", "brands": "Простоквашино",
            "nutriments": {"energy-kj_100g": 167.4, "proteins_100g": 3, "fat_100g": 1, "carbohydrates_100g": 4}}})
    return Resp(404, {"status": 0})


with patch.object(barcode.httpx, "get", fake_get):
    with SessionLocal() as db:
        found = barcode.lookup(db, KEFIR, "ru")
        chk("OFF: найден, русское название", found and found["name"] == "Кефир 1%", found)
        chk("OFF: кДж переведены в ккал", found and found["calories"] == 40, found)
        chk("OFF: записан в общий каталог", products.shared_by_barcode(db, KEFIR) is not None)
        n = len(off_calls)
        barcode.lookup(db, KEFIR, "ru")
        chk("повтор — из каталога, без OFF", len(off_calls) == n, off_calls)
        chk("не найден — None", barcode.lookup(db, MISSING, "ru") is None)
        n = len(off_calls)
        barcode.lookup(db, MISSING, "ru")
        chk("«не найдено» помним — OFF не дёргаем", len(off_calls) == n, off_calls)

# Лимит запросов к OFF на весь сервер.
barcode._lookup_times.clear()
with patch.object(barcode, "MAX_LOOKUPS_PER_MIN", 2):
    chk("лимит: 1", barcode._allow_lookup())
    chk("лимит: 2", barcode._allow_lookup())
    chk("лимит: 3-й отклонён", not barcode._allow_lookup())
barcode._lookup_times.clear()

# Сбой сети не записывает «не найдено».
with patch.object(barcode, "_fetch_off", lambda code, lang: None):
    with SessionLocal() as db:
        chk("сбой сети — None", barcode.lookup(db, "4000000000000", "ru") is None)
chk("сбой сети не запомнен как «нет»", not barcode._known_missing("4000000000000"))

# ---------- 3. /food/analyze: штрихкод без ИИ и без скана ---------- #
def scans_used():
    with SessionLocal() as db:
        u = db.query(M.User).filter(M.User.telegram_id == 1).first()
        return u.daily_scans_used or 0


before = scans_used()
with patch.object(ai_service, "analyze_food_image", side_effect=AssertionError("ИИ не нужен")), \
        patch("backend.main.analyze_food_image", side_effect=AssertionError("ИИ не нужен")):
    r = c.post("/food/analyze", files={"file": ("b.jpg", barcode_jpeg(KEFIR), "image/jpeg")})
chk("штрихкод: 200", r.status_code == 200, r.text[:300])
body = r.json()
chk("штрихкод: kind=barcode", body.get("kind") == "barcode" and body.get("barcode") == KEFIR, body)
chk("штрихкод: на 100 г", body.get("per_100g", {}).get("calories") == 40, body)
chk("штрихкод: скан не потрачен", scans_used() == before, (before, scans_used()))

LABEL = {"kind": "label", "dish_name": "Сырок глазированный", "weight_grams": 100, "calories": 410,
         "proteins": 8, "fats": 26, "carbs": 36, "confidence": "high", "note": "",
         "per_100g": {"calories": 410, "proteins": 8, "fats": 26, "carbs": 36}, "package_grams": 40}
NOFOOD = {"kind": "dish", "dish_name": ai_service.NO_FOOD_NAME, "weight_grams": 0, "calories": 0,
          "proteins": 0, "fats": 0, "carbs": 0, "confidence": "high", "note": ""}

before = scans_used()
with patch("backend.main.analyze_food_image", lambda *a, **k: dict(NOFOOD)):
    r = c.post("/food/analyze", files={"file": ("m.jpg", barcode_jpeg(MISSING), "image/jpeg")})
chk("ненайденный код: скан не списан", scans_used() == before, (before, scans_used()))
chk("не найден: код в ответе для фронта", r.json().get("barcode") == MISSING and r.json().get("kind") == "dish", r.json())

# Этикетка после ненайденного кода: фронт присылает продукт с кодом.
r = c.post("/products", json={"name": "Сырок глазированный", "barcode": MISSING, "calories": 410,
                              "proteins": 8, "fats": 26, "carbs": 36, "source": "label"})
chk("этикетка с кодом сохранена", r.status_code == 200 and r.json()["code"] == MISSING, r.text[:200])
with SessionLocal() as db:
    chk("этикетка пополнила общий каталог", products.shared_by_barcode(db, MISSING) is not None)
chk("код больше не «ненайденный»", not barcode._known_missing(MISSING))
r = c.get(f"/food/barcode/{MISSING}")
chk("теперь находится по коду", r.json().get("found") is True and r.json()["product"]["calories"] == 410, r.json())
chk("неверный код — 400", c.get("/food/barcode/12ab").status_code == 400)

# Код и этикетка на одном фото — каталог пополняется сразу.
THIRD = "4600000000024"
THIRD = THIRD[:12] + str((10 - sum((3 if i % 2 else 1) * int(d) for i, d in enumerate(THIRD[:12])) % 10) % 10)
with patch.object(barcode, "_fetch_off", lambda code, lang: {}), \
        patch("backend.main.analyze_food_image", lambda *a, **k: dict(LABEL, dish_name="Творожок")):
    r = c.post("/food/analyze", files={"file": ("t.jpg", barcode_jpeg(THIRD), "image/jpeg")})
chk("код+этикетка: kind=label", r.json().get("kind") == "label", r.json())
with SessionLocal() as db:
    chk("код+этикетка: в каталоге", products.shared_by_barcode(db, THIRD) is not None)

# ---------- 4. Бот ---------- #
calls = []
IMAGES = {}


def fake_api(method, payload):
    calls.append((method, payload))
    if method == "sendMessage":
        return {"message_id": len(calls)}
    if method == "getFile":
        return {"file_path": payload["file_id"]}
    return True


def photo(file_id):
    with SessionLocal() as db:
        telegram_bot.handle_update(db, {"message": {
            "message_id": 9, "from": {"id": 1, "language_code": "ru"},
            "chat": {"id": 1, "type": "private"}, "photo": [{"file_id": file_id}]}})


FOURTH = "4600000000031"
FOURTH = FOURTH[:12] + str((10 - sum((3 if i % 2 else 1) * int(d) for i, d in enumerate(FOURTH[:12])) % 10) % 10)
IMAGES.update({"kefir": barcode_jpeg(KEFIR), "unknown": barcode_jpeg(FOURTH), "label": plain.getvalue()})
ai_calls = []


def fake_ai(image, mime, lang="ru"):
    ai_calls.append(1)
    return dict(LABEL, dish_name="Йогурт питьевой") if image == IMAGES["label"] else dict(NOFOOD)


with patch.object(telegram_bot, "_bot_api", fake_api), \
        patch.object(telegram_bot, "_download_file", lambda p: IMAGES[p]), \
        patch.object(ai_service, "analyze_food_image", fake_ai), \
        patch.object(barcode, "_fetch_off", lambda code, lang: {}):
    photo("kefir")
    texts = [p.get("text", "") for m, p in calls if m == "sendMessage"]
    chk("бот: найдено по штрихкоду", any("штрихкоду" in t or "barcode" in t for t in texts), texts)
    chk("бот: ИИ не звали", not ai_calls)

    calls.clear()
    scans_before_unknown = scans_used()
    photo("unknown")
    chk("бот: ненайденный код не списывает скан", scans_used() == scans_before_unknown,
        (scans_before_unknown, scans_used()))
    texts = [p.get("text", "") for m, p in calls if m == "sendMessage"]
    chk("бот: код не найден — просим этикетку", any(FOURTH in t for t in texts), texts)

    calls.clear()
    photo("label")
with SessionLocal() as db:
    chk("бот: этикетка привязана к коду в каталоге", products.shared_by_barcode(db, FOURTH) is not None)
    mine = db.query(M.FoodProduct).filter(M.FoodProduct.telegram_id == 1,
                                          M.FoodProduct.barcode == FOURTH).first()
    chk("бот: и в моих продуктах с кодом", mine is not None)

if fails:
    print("FAIL:\n  " + "\n  ".join(fails)); sys.exit(1)
print("OK: код читается с размытого JPEG; OFF → каталог, повтор без OFF, «нет» помним, сбой — нет;\n"
      "    лимит OFF; найденный по коду продукт без ИИ и без скана; этикетка после ненайденного\n"
      "    кода и код+этикетка на одном фото пополняют каталог; то же в боте")
