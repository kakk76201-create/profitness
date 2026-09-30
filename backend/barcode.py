"""
Штрихкоды: прочитать код с фото и найти продукт с КБЖУ.

ЧТЕНИЕ КОДА — на сервере (zxing-cpp). Так одинаково работает и на iPhone
(в браузере Telegram на iOS нет встроенного распознавания штрихкодов), и для
фото, присланного боту. Любое фото сначала проверяется на штрихкод — это
локально и занимает миллисекунды; ИИ зовётся, только если кода нет или
продукт не нашёлся.

ПОИСК ПРОДУКТА — в два шага:
  1. общий каталог в нашей базе (food_products без владельца): то, что уже
     находили раньше или что кто-то переписал с этикетки;
  2. Open Food Facts по коду. Лимит у них — 15 запросов в минуту на IP ВСЕГО
     сервера, поэтому ответы кладём в каталог (повторный скан того же товара
     в Open Food Facts уже не идёт), а «не найдено» помним сутки.

Не нашли — человек фотографирует таблицу КБЖУ на упаковке, и продукт
записывается в каталог под этим кодом: следующий, кто отсканирует тот же
товар, получит его сразу. У российских товаров в открытой базе заметные
пробелы — именно так каталог и наполняется.

Модуль fail-safe: без zxing-cpp, без сети или при любой ошибке — «кода нет»
или «не найдено», но не исключение.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time

from backend import food_search, products

try:
    import zxingcpp
except Exception:  # noqa: BLE001 — без библиотеки просто нет чтения кодов
    zxingcpp = None

try:
    from PIL import Image, ImageOps
except Exception:  # noqa: BLE001
    Image = None

try:
    import httpx
except Exception:  # noqa: BLE001
    httpx = None

logger = logging.getLogger("barcode")

PRODUCT_URL = os.getenv("OFF_PRODUCT_URL", "https://world.openfoodfacts.org/api/v2/product/{code}")
# Официальный лимит чтения продуктов — 15/мин на IP; держим запас.
MAX_LOOKUPS_PER_MIN = int(os.getenv("OFF_PRODUCT_RPM", "12"))
NOT_FOUND_TTL_SEC = 24 * 3600
MAX_SIDE = 1600

_lock = threading.Lock()
_lookup_times: list = []
_not_found: dict = {}


def available() -> bool:
    return zxingcpp is not None and Image is not None


def decode(image_bytes: bytes) -> str | None:
    """Найти на фото товарный штрихкод (EAN-13/8, UPC-A/E). None — кода нет."""
    if not available() or not image_bytes:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img).convert("L")
        if max(img.size) > MAX_SIDE:
            img.thumbnail((MAX_SIDE, MAX_SIDE))
        fmts = (zxingcpp.BarcodeFormat.EAN13 | zxingcpp.BarcodeFormat.EAN8
                | zxingcpp.BarcodeFormat.UPCA | zxingcpp.BarcodeFormat.UPCE)
        for res in zxingcpp.read_barcodes(img, formats=fmts):
            code = products.clean_barcode(res.text)
            if code:
                return code
    except Exception as exc:  # noqa: BLE001 — битое фото не должно ронять запрос
        logger.info("barcode.decode: %s", exc)
    return None


def _allow_lookup() -> bool:
    now = time.time()
    with _lock:
        while _lookup_times and now - _lookup_times[0] > 60:
            _lookup_times.pop(0)
        if len(_lookup_times) >= MAX_LOOKUPS_PER_MIN:
            return False
        _lookup_times.append(now)
        return True


def _remember_not_found(code: str) -> None:
    with _lock:
        _not_found[code] = time.time() + NOT_FOUND_TTL_SEC
        if len(_not_found) > 5000:
            for k in sorted(_not_found, key=_not_found.get)[:1000]:
                _not_found.pop(k, None)


def _known_missing(code: str) -> bool:
    with _lock:
        until = _not_found.get(code)
        if until and until > time.time():
            return True
        _not_found.pop(code, None)
        return False


def forget_missing(code: str) -> None:
    """Продукт появился (сняли этикетку) — больше не считать код ненайденным."""
    with _lock:
        _not_found.pop(code, None)


def _fetch_off(code: str, lang: str) -> dict | None:
    """Запросить продукт в Open Food Facts. None — нет, нет цифр или не удалось."""
    if httpx is None or not _allow_lookup():
        return None
    try:
        resp = httpx.get(
            PRODUCT_URL.format(code=code),
            params={"fields": "code,product_name,product_name_ru,product_name_en,brands,nutriments"},
            timeout=food_search.TIMEOUT_SEC,
            headers={"User-Agent": food_search.USER_AGENT, "Accept": "application/json"},
        )
        if resp.status_code == 404:
            return {}
        if resp.status_code != 200:
            logger.warning("barcode: Open Food Facts HTTP %s для %s", resp.status_code, code)
            return None
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("barcode: сбой запроса %s: %s", code, exc)
        return None
    prod = data.get("product") if isinstance(data, dict) else None
    if not isinstance(prod, dict) or data.get("status") in (0, "failure"):
        return {}
    name = prod.get(f"product_name_{lang}") or prod.get("product_name") or prod.get("product_name_en")
    hit = dict(prod, product_name=name, code=code)
    return food_search._normalize_hit(hit) or {}


def lookup(db, code: str, lang: str = "ru") -> dict | None:
    """Продукт по штрихкоду в формате FoodSearchItem, либо None."""
    code = products.clean_barcode(code)
    if not code:
        return None
    row = products.shared_by_barcode(db, code)
    if row is not None:
        return products.to_item(row)
    if _known_missing(code):
        return None
    found = _fetch_off(code, "en" if str(lang).startswith("en") else "ru")
    if found is None:
        # Сеть или лимит — не запоминаем как «нет», попробуем в другой раз.
        return None
    if not found:
        _remember_not_found(code)
        # Код товара — не личные данные. Строка нужна, чтобы проверить на
        # реальных промахах, закрывает ли их платная база (см. /stats).
        logger.info("barcode MISS %s", code)
        return None
    row = products.save_shared(
        db, code, found["name"],
        {"calories": found["calories"], "proteins": found["proteins"],
         "fats": found["fats"], "carbs": found["carbs"]},
        brand=found.get("brand") or "", source="off",
    )
    return products.to_item(row) if row is not None else None
