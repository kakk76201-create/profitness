"""Режимы съёмки: человек сам выбирает, что снимает.

«Еда» — только оценка блюда ИИ, штрихкод не ищется (упаковка рядом с тарелкой
не подменяет блюдо). «Штрихкод» — без ИИ и без траты скана; кода на фото нет —
понятная ошибка; товара нет — просьба снять этикетку. «КБЖУ» — ИИ только
переписывает этикетку; таблицы нет — ошибка без списания скана.
Без режима — прежнее автоматическое поведение (старые версии приложения, бот).
"""
import io, os, pathlib, sys, tempfile
from unittest.mock import patch

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "modes.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"; os.environ["OWNER_ID"] = "999"
os.environ["FREE_SCAN_LIMIT"] = "10"
os.environ.pop("RAILWAY_ENVIRONMENT", None); os.environ.pop("RAILWAY_PROJECT_ID", None)

import zxingcpp
from PIL import Image
from fastapi.testclient import TestClient
from backend import ai_service, barcode, models as M, products
from backend.database import init_db, SessionLocal
from backend.main import app

init_db()
c = TestClient(app)
fails = []


def chk(name, cond, extra=""):
    if not cond:
        fails.append(name + ("  " + str(extra) if extra else ""))


def ean(prefix12: str) -> str:
    return prefix12 + str((10 - sum((3 if i % 2 else 1) * int(d) for i, d in enumerate(prefix12)) % 10) % 10)


def barcode_jpeg(code: str) -> bytes:
    bc = zxingcpp.create_barcode(code, zxingcpp.BarcodeFormat.EAN13)
    mv = memoryview(zxingcpp.write_barcode_to_image(bc, scale=3))
    pic = Image.frombytes("L", (mv.shape[1], mv.shape[0]), mv.tobytes())
    canvas = Image.new("L", (1280, 960), 205)
    canvas.paste(pic, (330, 360))
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, "JPEG", quality=80)
    return buf.getvalue()


def plain_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (150, 90, 40)).save(buf, "JPEG")
    return buf.getvalue()


def scans_used():
    with SessionLocal() as db:
        u = db.query(M.User).filter(M.User.telegram_id == 1).first()
        return (u.daily_scans_used or 0) if u else 0


def post(img, mode=None):
    data = {"mode": mode} if mode else {}
    return c.post("/food/analyze", files={"file": ("x.jpg", img, "image/jpeg")}, data=data)


c.post("/consent", json={"pd": True, "terms": True, "health": True, "cross_border": True})
KNOWN, UNKNOWN = ean("460700177123"), ean("460000000009")
with SessionLocal() as db:
    products.save_shared(db, KNOWN, "Кефир 1%", {"calories": 40, "proteins": 3, "fats": 1, "carbs": 4})

DISH = {"kind": "dish", "dish_name": "Гречка с котлетой", "weight_grams": 300, "calories": 480,
        "proteins": 28, "fats": 18, "carbs": 50, "confidence": "medium", "note": ""}
LABEL = {"kind": "label", "dish_name": "Сырок", "weight_grams": 100, "calories": 410, "proteins": 8,
         "fats": 26, "carbs": 36, "confidence": "high", "note": "",
         "per_100g": {"calories": 410, "proteins": 8, "fats": 26, "carbs": 36}, "package_grams": 40}
NONE = {"kind": "none", "dish_name": "", "weight_grams": 0, "calories": 0, "proteins": 0,
        "fats": 0, "carbs": 0, "confidence": "low", "note": ""}

ai_modes = []


def fake_ai(result):
    def run(image, mime, lang="ru", mode="auto"):
        ai_modes.append(mode)
        return dict(result)
    return run


no_ai = patch("backend.main.analyze_food_image", side_effect=AssertionError("ИИ не нужен"))

# ---------- Штрихкод ---------- #
before = scans_used()
with no_ai:
    r = post(barcode_jpeg(KNOWN), "barcode")
chk("штрихкод: найден", r.status_code == 200 and r.json()["kind"] == "barcode", r.text[:200])
with no_ai:
    r = post(plain_jpeg(), "barcode")
chk("штрихкод: кода нет — 422 no_barcode", r.status_code == 422 and r.json()["detail"]["error"] == "no_barcode",
    r.text[:200])
with no_ai, patch.object(barcode, "_fetch_off", lambda code, lang: {}):
    r = post(barcode_jpeg(UNKNOWN), "barcode")
chk("штрихкод: товара нет — barcode_missing", r.status_code == 200 and r.json()["kind"] == "barcode_missing"
    and r.json()["barcode"] == UNKNOWN, r.text[:200])
chk("штрихкод: сканы не списаны", scans_used() == before, (before, scans_used()))

# ---------- Еда ---------- #
ai_modes.clear()
with patch("backend.main.analyze_food_image", fake_ai(DISH)):
    r = post(barcode_jpeg(KNOWN), "food")
chk("еда: блюдо, а не кефир со штрихкода", r.json()["kind"] == "dish" and r.json()["barcode"] is None, r.json())
chk("еда: ИИ в режиме dish", ai_modes == ["dish"], ai_modes)
chk("еда: скан списан", scans_used() == before + 1, scans_used())

# ---------- КБЖУ с упаковки ---------- #
before = scans_used()
ai_modes.clear()
with patch("backend.main.analyze_food_image", fake_ai(NONE)):
    r = post(plain_jpeg(), "label")
chk("КБЖУ: таблицы нет — 422 no_label", r.status_code == 422 and r.json()["detail"]["error"] == "no_label",
    r.text[:200])
chk("КБЖУ: без таблицы скан не списан", scans_used() == before, (before, scans_used()))
chk("КБЖУ: ИИ в режиме label", ai_modes == ["label"], ai_modes)
with patch("backend.main.analyze_food_image", fake_ai(LABEL)):
    r = post(plain_jpeg(), "label")
chk("КБЖУ: этикетка прочитана", r.json()["kind"] == "label" and r.json()["per_100g"]["calories"] == 410, r.json())
chk("КБЖУ: скан списан", scans_used() == before + 1, scans_used())

# ---------- Без режима — как раньше ---------- #
with no_ai:
    r = post(barcode_jpeg(KNOWN))
chk("авто: штрихкод находится сам", r.json()["kind"] == "barcode", r.json())
ai_modes.clear()
with patch("backend.main.analyze_food_image", fake_ai(DISH)):
    r = post(plain_jpeg(), "что-то странное")
chk("неизвестный режим = авто", r.status_code == 200 and ai_modes == ["auto"], (r.status_code, ai_modes))

# ---------- Промпты модели по режимам ---------- #
chk("промпт «еда» без этикетки", "ЭТИКЕТКА" not in ai_service.SYSTEM_PROMPT_DISH)
chk("промпт «авто» с этикеткой", "ЭТИКЕТКА" in ai_service.SYSTEM_PROMPT)
captured = {}


def fake_call(client, data_url, lang="ru", mode="auto"):
    captured["mode"] = mode
    return ('{"kind": "none", "dish_name": ""}', "stop", None)


with patch.object(ai_service, "_get_vision_client", lambda: None), patch.object(ai_service, "_call_model", fake_call):
    res = ai_service.analyze_food_image(plain_jpeg(), "image/jpeg", "ru", "label")
chk("ИИ: режим доходит до модели", captured.get("mode") == "label", captured)
chk("ИИ: этикетки нет — kind none", res["kind"] == "none", res)

if fails:
    print("FAIL:\n  " + "\n  ".join(fails)); sys.exit(1)
print("OK: штрихкод без ИИ и без скана (найден / нет кода / нет товара); «еда» не ищет код;\n"
      "    «КБЖУ» без таблицы — ошибка без скана; без режима — как раньше; режим доходит до модели")
