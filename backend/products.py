"""
«Мои продукты»: КБЖУ на 100 г, которые человек однажды получил — с этикетки,
по штрихкоду или из базы — и больше не хочет вбивать руками.

Два вида строк в таблице food_products:
  * личные (telegram_id = человек) — его список, виден только ему, удаляется
    вместе с аккаунтом;
  * общие (telegram_id = NULL) — каталог по штрихкоду: что один человек
    сфотографировал с этикетки, следующий получит по сканеру сразу. В общих
    строках нет ничего личного — только продукт и цифры с упаковки.

Поиск по названию идёт по name_key (название в нижнем регистре, посчитанное
в Python): SQLite умеет LOWER/LIKE без учёта регистра только для латиницы,
а названия у нас в основном русские.
"""

from __future__ import annotations

import re
from datetime import datetime

from backend.models import FoodProduct

# Сколько продуктов храним на человека: старые по последнему использованию
# вытесняются, чтобы список не рос бесконечно.
MAX_PER_USER = 500


def _key(name: str) -> str:
    return " ".join(str(name or "").casefold().split())[:120]


def clean_per100(values: dict) -> dict | None:
    """Проверить КБЖУ на 100 г. None — если цифры невозможные."""
    try:
        kcal = float(values.get("calories"))
        p = float(values.get("proteins") or 0)
        f = float(values.get("fats") or 0)
        c = float(values.get("carbs") or 0)
    except (TypeError, ValueError, AttributeError):
        return None
    if not (0 < kcal <= 950) or min(p, f, c) < 0 or p + f + c > 105:
        return None
    return {"calories": round(kcal, 1), "proteins": round(p, 1), "fats": round(f, 1), "carbs": round(c, 1)}


def clean_barcode(code) -> str | None:
    """Штрихкод: только цифры, 8–14 знаков (EAN-8, EAN-13, UPC, GTIN-14)."""
    digits = re.sub(r"\D", "", str(code or ""))
    return digits if 8 <= len(digits) <= 14 else None


def to_item(p: FoodProduct) -> dict:
    """Строка в формате результата поиска (FoodSearchItem)."""
    return {
        "id": p.id,
        "code": p.barcode or "",
        "name": p.name,
        "brand": p.brand or "",
        "calories": int(round(p.kcal_100 or 0)),
        "proteins": round(p.p_100 or 0, 1),
        "fats": round(p.f_100 or 0, 1),
        "carbs": round(p.c_100 or 0, 1),
        "mine": p.telegram_id is not None,
        "source": p.source or "",
    }


def upsert_personal(db, tid: int, name: str, per100: dict, *, brand: str = "",
                    barcode: str | None = None, source: str = "manual") -> FoodProduct | None:
    """Сохранить продукт в личный список (или обновить уже сохранённый).

    Совпадением считается тот же штрихкод или то же название. Коммит — здесь.
    """
    per = clean_per100(per100)
    name = " ".join(str(name or "").split())[:120]
    if per is None or not name:
        return None
    barcode = clean_barcode(barcode)
    q = db.query(FoodProduct).filter(FoodProduct.telegram_id == tid)
    found = None
    if barcode:
        found = q.filter(FoodProduct.barcode == barcode).first()
    if found is None:
        found = q.filter(FoodProduct.name_key == _key(name)).first()

    now = datetime.utcnow()
    if found is None:
        found = FoodProduct(telegram_id=tid, created_at=now, uses=0)
        db.add(found)
    found.name = name
    found.name_key = _key(name)
    found.brand = (brand or found.brand or "")[:60]
    found.barcode = barcode or found.barcode
    found.kcal_100, found.p_100 = per["calories"], per["proteins"]
    found.f_100, found.c_100 = per["fats"], per["carbs"]
    found.source = source or found.source or "manual"
    found.uses = (found.uses or 0) + 1
    found.last_used_at = now
    found.updated_at = now
    db.commit()

    # Вытесняем самые давно не использованные сверх лимита.
    total = q.count()
    if total > MAX_PER_USER:
        old = (q.order_by(FoodProduct.last_used_at.asc()).limit(total - MAX_PER_USER).all())
        for row in old:
            db.delete(row)
        db.commit()
    return found


def list_personal(db, tid: int, limit: int = 30) -> list:
    rows = (
        db.query(FoodProduct)
        .filter(FoodProduct.telegram_id == tid)
        .order_by(FoodProduct.last_used_at.desc(), FoodProduct.id.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    return [to_item(r) for r in rows]


def search_personal(db, tid: int, query: str, limit: int = 6) -> list:
    key = _key(query)
    if len(key) < 2:
        return []
    like = "%" + key.replace("%", "").replace("_", "") + "%"
    rows = (
        db.query(FoodProduct)
        .filter(FoodProduct.telegram_id == tid, FoodProduct.name_key.like(like))
        .order_by(FoodProduct.uses.desc(), FoodProduct.last_used_at.desc())
        .limit(limit)
        .all()
    )
    return [to_item(r) for r in rows]


def shared_by_barcode(db, barcode: str) -> FoodProduct | None:
    code = clean_barcode(barcode)
    if not code:
        return None
    return (
        db.query(FoodProduct)
        .filter(FoodProduct.telegram_id.is_(None), FoodProduct.barcode == code)
        .order_by(FoodProduct.updated_at.desc())
        .first()
    )


def save_shared(db, barcode: str, name: str, per100: dict, *, brand: str = "",
                source: str = "off") -> FoodProduct | None:
    """Положить продукт в общий каталог по штрихкоду (без чьих-либо данных)."""
    code = clean_barcode(barcode)
    per = clean_per100(per100)
    name = " ".join(str(name or "").split())[:120]
    if not code or per is None or not name:
        return None
    row = shared_by_barcode(db, code)
    now = datetime.utcnow()
    if row is None:
        row = FoodProduct(telegram_id=None, barcode=code, created_at=now, uses=0)
        db.add(row)
    elif row.source == "off" and source == "label":
        # Цифры с настоящей упаковки точнее записи из открытой базы.
        pass
    elif row.source == "label" and source == "off":
        return row
    row.name, row.name_key = name, _key(name)
    row.brand = (brand or row.brand or "")[:60]
    row.kcal_100, row.p_100 = per["calories"], per["proteins"]
    row.f_100, row.c_100 = per["fats"], per["carbs"]
    row.source = source
    row.updated_at = now
    row.last_used_at = now
    db.commit()
    return row
