"""
Свои рецепты: домашнее блюдо из продуктов с граммами.

Человек собирает ингредиенты (из поиска, своих продуктов или вручную) с
весом, указывает вес готового блюда и число порций — получает КБЖУ на 100 г
и на порцию. Посчитанное блюдо сохраняется личным продуктом
(food_products, source="recipe"), поэтому дальше работает как любой «мой
продукт»: первым в поиске, пересчёт по граммам, запись порциями.

Вес готового блюда важен: при варке вода уходит или впитывается, и 100 г
готового супа — не 100 г сырых продуктов. Если вес не указан, считаем по
сумме ингредиентов.
"""

from __future__ import annotations

import json
from datetime import datetime

from backend import products
from backend.models import FoodProduct, Recipe

MAX_PER_USER = 200
MAX_INGREDIENTS = 40
MAX_SERVINGS = 50


class RecipeError(ValueError):
    """Ошибка в рецепте: текст можно показать человеку."""


def _num(value, lo: float, hi: float):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


def clean_ingredient(item) -> dict | None:
    """Ингредиент: название, граммы и КБЖУ на 100 г. 0 ккал — можно (вода, соль)."""
    if not isinstance(item, dict):
        return None
    name = " ".join(str(item.get("name") or "").split())[:120]
    grams = _num(item.get("grams"), 0.1, 10000)
    kcal = _num(item.get("calories"), 0, 950)
    p = _num(item.get("proteins") or 0, 0, 100)
    f = _num(item.get("fats") or 0, 0, 100)
    c = _num(item.get("carbs") or 0, 0, 100)
    if not name or None in (grams, kcal, p, f, c) or p + f + c > 105:
        return None
    return {
        "name": name, "grams": round(grams, 1), "calories": round(kcal, 1),
        "proteins": round(p, 1), "fats": round(f, 1), "carbs": round(c, 1),
    }


def compute(ingredients: list, cooked_weight_g=None, servings=1) -> dict:
    """Посчитать блюдо. RecipeError — если считать не из чего или цифры невозможные."""
    items = [i for i in (clean_ingredient(x) for x in (ingredients or [])) if i]
    if not items:
        raise RecipeError("Добавьте хотя бы один ингредиент с весом")
    if len(items) > MAX_INGREDIENTS:
        raise RecipeError(f"В рецепте может быть не больше {MAX_INGREDIENTS} ингредиентов")

    raw_g = sum(i["grams"] for i in items)
    totals = {k: sum(i[k] * i["grams"] / 100 for i in items)
              for k in ("calories", "proteins", "fats", "carbs")}

    if cooked_weight_g in (None, "", 0):
        weight = raw_g
        cooked = None
    else:
        cooked = _num(cooked_weight_g, 1, 50000)
        if cooked is None:
            raise RecipeError("Вес готового блюда — от 1 г до 50 кг")
        weight = cooked

    try:
        n = 1 if servings in (None, "") else int(servings)
    except (TypeError, ValueError):
        n = 0
    if not 1 <= n <= MAX_SERVINGS:
        raise RecipeError(f"Порций — от 1 до {MAX_SERVINGS}")

    per100 = {k: v * 100 / weight for k, v in totals.items()}
    clean = products.clean_per100(per100)
    if clean is None:
        if totals["calories"] <= 0:
            raise RecipeError("В рецепте нет калорий — проверьте ингредиенты")
        raise RecipeError("Слишком маленький вес готового блюда для этих продуктов")

    serving_g = round(weight / n, 1)
    return {
        "ingredients": items,
        "raw_weight_g": round(raw_g, 1),
        "cooked_weight_g": round(cooked, 1) if cooked else None,
        "weight_g": round(weight, 1),
        "servings": n,
        "serving_g": serving_g,
        "totals": {k: round(v, 1) for k, v in totals.items()},
        "per100": clean,
    }


def _ingredients(row: Recipe) -> list:
    try:
        data = json.loads(row.ingredients_json or "[]")
    except (TypeError, ValueError):
        return []
    return [i for i in (clean_ingredient(x) for x in data) if i]


def to_out(row: Recipe, product: FoodProduct | None) -> dict:
    """Рецепт для приложения: как сохранён + посчитанный продукт."""
    item = products.to_item(product) if product is not None else None
    return {
        "id": row.id,
        "name": row.name,
        "ingredients": _ingredients(row),
        "cooked_weight_g": row.cooked_weight_g,
        "servings": row.servings or 1,
        "product": item,
    }


def _product(db, tid: int, row: Recipe) -> FoodProduct | None:
    if row.product_id is None:
        return None
    return products.get_personal(db, tid, row.product_id)


def get(db, tid: int, recipe_id: int) -> Recipe | None:
    return (
        db.query(Recipe)
        .filter(Recipe.id == recipe_id, Recipe.telegram_id == tid)
        .first()
    )


def list_out(db, tid: int) -> list:
    """Рецепты человека: недавно съеденные — первыми."""
    rows = db.query(Recipe).filter(Recipe.telegram_id == tid).all()
    ids = [r.product_id for r in rows if r.product_id is not None]
    by_id = {}
    if ids:
        for p in db.query(FoodProduct).filter(
            FoodProduct.telegram_id == tid, FoodProduct.id.in_(ids)
        ):
            by_id[p.id] = p

    def order(r):
        p = by_id.get(r.product_id)
        used = (p.last_used_at if p is not None else None) or r.updated_at or datetime.min
        return used
    rows.sort(key=order, reverse=True)
    return [to_out(r, by_id.get(r.product_id)) for r in rows]


def save(db, tid: int, name: str, ingredients: list, cooked_weight_g=None,
         servings=1, row: Recipe | None = None) -> tuple[Recipe, FoodProduct]:
    """Создать или поправить рецепт и его продукт. Коммит — здесь."""
    name = " ".join(str(name or "").split())[:120]
    if not name:
        raise RecipeError("Назовите рецепт")
    calc = compute(ingredients, cooked_weight_g, servings)

    if row is None:
        count = db.query(Recipe).filter(Recipe.telegram_id == tid).count()
        if count >= MAX_PER_USER:
            raise RecipeError(f"Рецептов может быть не больше {MAX_PER_USER}")

    now = datetime.utcnow()
    product = _product(db, tid, row) if row is not None else None
    if product is None:
        # Новый рецепт или его продукт удалили — заводим строку заново.
        product = FoodProduct(telegram_id=tid, source=products.RECIPE,
                              created_at=now, uses=0, last_used_at=now)
        db.add(product)
    per = calc["per100"]
    product.name, product.name_key = name, products._key(name)
    product.brand = ""
    product.barcode = None
    product.source = products.RECIPE
    product.kcal_100, product.p_100 = per["calories"], per["proteins"]
    product.f_100, product.c_100 = per["fats"], per["carbs"]
    product.serving_g = calc["serving_g"]
    product.updated_at = now
    db.flush()

    if row is None:
        row = Recipe(telegram_id=tid, created_at=now)
        db.add(row)
    row.name = name
    row.ingredients_json = json.dumps(calc["ingredients"], ensure_ascii=False)
    row.cooked_weight_g = calc["cooked_weight_g"]
    row.servings = calc["servings"]
    row.product_id = product.id
    row.updated_at = now
    db.commit()
    return row, product


def delete(db, tid: int, row: Recipe) -> None:
    """Удалить рецепт вместе с его продуктом."""
    if row.product_id is not None:
        db.query(FoodProduct).filter(
            FoodProduct.id == row.product_id, FoodProduct.telegram_id == tid
        ).delete(synchronize_session=False)
    db.delete(row)
    db.commit()


def forget_product(db, tid: int, product_id: int) -> None:
    """Продукт-рецепт удалили из списка — рецепт без него не нужен.

    Коммит — у вызывающего (вместе с удалением продукта).
    """
    db.query(Recipe).filter(
        Recipe.telegram_id == tid, Recipe.product_id == product_id
    ).delete(synchronize_session=False)
