"""
История съеденного — из самого дневника (diary_entries), с порциями.

«Недавние» — последние разные блюда; «Частые» — что человек ест чаще всего
за последние 90 дней. У каждого блюда — последняя порция (количество, единица)
и её КБЖУ: приложение подставляет их и пересчитывает, если поменять граммы.

Группируем по названию в Python (casefold), а не GROUP BY LOWER(...):
SQLite не приводит кириллицу к нижнему регистру.
"""

from __future__ import annotations

from datetime import date as date_cls, timedelta

from backend.models import DiaryEntry
from backend.products import _key

RECENT_SCAN = 400
FREQUENT_SCAN = 1500
SEARCH_SCAN = 600


def _item(r: DiaryEntry, count: int = 1) -> dict:
    return {
        "dish_name": r.dish_name or "",
        "quantity": r.quantity,
        "unit": r.unit,
        "calories": int(r.calories or 0),
        "proteins": round(r.proteins or 0.0, 1),
        "fats": round(r.fats or 0.0, 1),
        "carbs": round(r.carbs or 0.0, 1),
        "meal_type": r.meal_type,
        "count": count,
        "last_date": r.date,
    }


def _latest(db, tid: int, limit: int, since: str | None = None):
    q = db.query(DiaryEntry).filter(DiaryEntry.telegram_id == tid)
    if since:
        q = q.filter(DiaryEntry.date >= since)
    return (
        q.order_by(DiaryEntry.created_at.desc(), DiaryEntry.id.desc())
        .limit(limit)
        .all()
    )


def recent(db, tid: int, limit: int = 25) -> list:
    """Последние разные блюда — с той порцией, что была в последний раз."""
    seen: set[str] = set()
    out = []
    for r in _latest(db, tid, RECENT_SCAN):
        key = _key(r.dish_name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(_item(r))
        if len(out) >= limit:
            break
    return out


def frequent(db, tid: int, today: date_cls | None = None, days: int = 90,
             limit: int = 25, min_count: int = 2) -> list:
    """Что ест чаще всего: по числу записей за `days` дней, порция — последняя."""
    since = ((today or date_cls.today()) - timedelta(days=days)).isoformat()
    groups: dict[str, list] = {}
    order: list[str] = []
    for r in _latest(db, tid, FREQUENT_SCAN, since):
        key = _key(r.dish_name)
        if not key:
            continue
        if key not in groups:
            groups[key] = [r, 0]
            order.append(key)
        groups[key][1] += 1
    ranked = [k for k in order if groups[k][1] >= min_count]
    # sorted устойчив: при равном счёте раньше остаётся съеденное позже.
    ranked = sorted(ranked, key=lambda k: -groups[k][1])
    return [_item(groups[k][0], groups[k][1]) for k in ranked[:limit]]


def search(db, tid: int, query: str, limit: int = 4, exclude: set | None = None) -> list:
    """Блюда из своей истории, в названии которых есть запрос."""
    q = _key(query)
    if len(q) < 2:
        return []
    exclude = exclude or set()
    seen: set[str] = set()
    out = []
    for r in _latest(db, tid, SEARCH_SCAN):
        key = _key(r.dish_name)
        if not key or key in seen or key in exclude or q not in key:
            continue
        seen.add(key)
        out.append(_item(r))
        if len(out) >= limit:
            break
    return out
