"""
Юридический слой: документы и согласия пользователя.

Тексты документов лежат в frontend/legal/*.html как ФРАГМЕНТЫ (только
содержимое: заголовки и абзацы). Оболочку страницы, стили и реквизиты
продавца подставляет render() — благодаря этому реквизиты живут в
переменных окружения, а не размазаны по четырём файлам, и правка текста
не требует правки кода.

Согласия хранятся ЖУРНАЛОМ (таблица consents, только добавление): каждое
«дал» и каждое «отозвал» — отдельная строка с датой и редакцией документов.
Текущее состояние — последняя строка по каждому виду. Журнал нужен, чтобы при
проверке можно было показать, КОГДА и НА КАКУЮ редакцию человек согласился.

Виды согласий (152-ФЗ):
  pd           — обработка персональных данных (ст. 6 ч. 1 п. 1). Обязательно.
  terms        — принятие соглашения и оферты, подтверждение 18+. Обязательно.
  health       — данные о состоянии здоровья (ст. 10 ч. 2 п. 1). Добровольно:
                 без него не работают тренер, трекер цикла и фото-прогресса.
  cross_border — трансграничная передача (ст. 12). Добровольно: без него не
                 работают функции с ИИ (фото еды, голос, рекомендации).

Обязательные согласия спрашиваем при первом запуске; необязательные человек
может включить и выключить в любой момент в «Профиль → Данные».
"""

from __future__ import annotations

import html as _html
import logging
import re
from datetime import datetime
from pathlib import Path

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from backend import config
from backend.auth import get_current_user
from backend.database import get_db
from backend.models import Consent, User

logger = logging.getLogger(__name__)

# Документы, которые отдаёт /legal/{name}.html. Имя проверяется по этому
# списку — чтение произвольного файла с диска по пути из адреса исключено.
DOCS = ("terms", "privacy", "offer", "consent")

KINDS = ("pd", "terms", "health", "cross_border")
REQUIRED = ("pd", "terms")
OPTIONAL = ("health", "cross_border")

# Что именно перестаёт работать без необязательного согласия — показываем
# человеку в интерфейсе и возвращаем в ошибке 403.
BLOCKS = {
    "health": "тренер, трекер цикла и фото-прогресса",
    "cross_border": "распознавание еды по фото и голосу, советы ИИ",
}

_LEGAL_DIR = Path(__file__).resolve().parent.parent / "frontend" / "legal"
_RENDER_CACHE: dict = {}


# --------------------------------------------------------------------------- #
#  Документы
# --------------------------------------------------------------------------- #
def _requisites() -> dict:
    """Реквизиты для подстановки в документы (все — из переменных окружения)."""
    bot = f"@{config.BOT_USERNAME}" if config.BOT_USERNAME else "—"
    contact = config.SUPPORT_CONTACT or bot
    inn = f", ИНН {config.LEGAL_INN}" if config.LEGAL_INN else ""
    return {
        "SELLER": config.LEGAL_SELLER or "Владелец приложения «Fitness Up»",
        "INN_SUFFIX": inn,
        "CONTACT": contact,
        "BOT": bot,
        "APP": config.MINI_APP_URL or bot,
        "VERSION": config.LEGAL_VERSION,
        "DATE": config.LEGAL_DATE,
    }


def requisites_ready() -> bool:
    """Заполнены ли реквизиты продавца (без них документы юридически пустые)."""
    return bool(config.LEGAL_SELLER and config.LEGAL_INN and config.SUPPORT_CONTACT)


_SHELL = """<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Fitness Up</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0 auto; padding: 24px 18px 64px; max-width: 760px;
    font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    color: #14161a; background: #fff; overflow-wrap: break-word; }
  h1 { font-size: 24px; line-height: 1.25; margin: 0 0 4px; }
  h2 { font-size: 18px; line-height: 1.3; margin: 28px 0 8px; }
  p { margin: 0 0 12px; }
  a { color: #d24a17; }
  .meta { color: #6b7280; font-size: 14px; margin-bottom: 20px; }
  .warn { border: 1px solid #d24a17; border-radius: 10px; padding: 12px 14px;
    margin-bottom: 20px; color: #d24a17; font-size: 14px; }
  .nav { margin-top: 40px; padding-top: 16px; border-top: 1px solid #e5e7eb;
    font-size: 14px; }
  .nav a { margin-right: 14px; display: inline-block; }
  @media (prefers-color-scheme: dark) {
    body { color: #e8eaed; background: #14161a; }
    .meta { color: #9aa0a6; }
    a { color: #ff7a3d; }
    .warn { border-color: #ff7a3d; color: #ff7a3d; }
    .nav { border-top-color: #2a2d33; }
  }
</style>
</head><body>
{warn}{body}
<div class="nav">
  <a href="/legal/terms.html">Соглашение</a>
  <a href="/legal/privacy.html">Политика</a>
  <a href="/legal/offer.html">Оферта</a>
  <a href="/legal/consent.html">Согласия</a>
</div>
</body></html>
"""

_TITLES = {
    "terms": "Пользовательское соглашение",
    "privacy": "Политика обработки персональных данных",
    "offer": "Публичная оферта",
    "consent": "Согласие на обработку персональных данных",
}


def render(name: str) -> str:
    """Собрать готовую HTML-страницу документа. Имя должно быть из DOCS."""
    if name in _RENDER_CACHE:
        return _RENDER_CACHE[name]
    if name not in DOCS:
        raise KeyError(name)

    body = (_LEGAL_DIR / f"{name}.html").read_text(encoding="utf-8")
    # Заметка для разработчика в начале файла — не часть документа.
    body = re.sub(r"\A\s*<!--.*?-->\s*", "", body, flags=re.S)
    for key, value in _requisites().items():
        body = body.replace("{{" + key + "}}", _html.escape(str(value)))

    warn = ""
    if not requisites_ready():
        warn = (
            '<div class="warn">Документ опубликован без реквизитов продавца. '
            "Задайте LEGAL_SELLER, LEGAL_INN и SUPPORT_CONTACT.</div>\n"
        )
    # Оболочка — обычная строка с одинарными фигурными скобками в CSS,
    # поэтому подставляем через replace, а не через format.
    page = _SHELL.replace("{title}", _html.escape(_TITLES[name]))
    page = page.replace("{warn}", warn).replace("{body}", body)
    _RENDER_CACHE[name] = page
    return page


def doc_urls() -> dict:
    """Ссылки на документы для фронтенда и платёжной страницы."""
    base = (config.MINI_APP_URL or "").rstrip("/")
    return {name: f"{base}/legal/{name}.html" for name in DOCS}


# --------------------------------------------------------------------------- #
#  Согласия
# --------------------------------------------------------------------------- #
def state(db: Session, telegram_id: int) -> dict:
    """Текущее состояние согласий: {вид: {granted, version, date}}.

    Берём последнюю запись журнала по каждому виду (по id — он монотонный).
    """
    out = {k: {"granted": False, "version": "", "date": ""} for k in KINDS}
    rows = (
        db.query(Consent)
        .filter(Consent.telegram_id == telegram_id)
        .order_by(Consent.id.asc())
        .all()
    )
    for row in rows:  # более поздние записи затирают более ранние
        if row.kind in out:
            out[row.kind] = {
                "granted": bool(row.granted),
                "version": row.version or "",
                "date": row.created_at.isoformat(timespec="seconds") if row.created_at else "",
            }
    return out


def granted(db: Session, telegram_id: int, kind: str) -> bool:
    """Действует ли согласие данного вида прямо сейчас."""
    row = (
        db.query(Consent)
        .filter(Consent.telegram_id == telegram_id, Consent.kind == kind)
        .order_by(Consent.id.desc())
        .first()
    )
    return bool(row and row.granted)


def needs_gate(db: Session, telegram_id: int) -> bool:
    """Нужно ли показать экран согласий.

    Да, если какое-то обязательное согласие не дано ИЛИ дано на старую
    редакцию документов: смена LEGAL_VERSION — осознанное решение владельца
    переспросить всех.
    """
    st = state(db, telegram_id)
    for kind in REQUIRED:
        cur = st[kind]
        if not cur["granted"] or cur["version"] != config.LEGAL_VERSION:
            return True
    return False


def save(db: Session, telegram_id: int, values: dict, source: str = "app") -> dict:
    """Записать решения человека. Пишем только изменения — журнал не засоряем."""
    st = state(db, telegram_id)
    changed = []
    for kind in KINDS:
        if kind not in values:
            continue
        want = bool(values[kind])
        cur = st[kind]
        # Перезаписываем и когда значение то же, но редакция устарела:
        # согласие на новую редакцию — новый факт, его нужно зафиксировать.
        if cur["granted"] == want and cur["version"] == config.LEGAL_VERSION:
            continue
        db.add(
            Consent(
                telegram_id=telegram_id,
                kind=kind,
                granted=want,
                version=config.LEGAL_VERSION,
                source=source,
                created_at=datetime.utcnow(),
            )
        )
        changed.append(kind)
    if changed:
        db.commit()
        logger.info("consent: %s -> %s (%s)", telegram_id, changed, config.LEGAL_VERSION)
    return state(db, telegram_id)


def revoke_all(db: Session, telegram_id: int, reason: str = "account_deleted") -> None:
    """Зафиксировать отзыв всех согласий (при удалении профиля).

    Журнал при удалении аккаунта НЕ стираем: он подтверждает, что обработка
    велась с согласия и что оно отозвано тогда-то. Персональных данных в нём
    нет, кроме идентификатора Telegram. Коммит — на стороне вызывающего кода.
    """
    st = state(db, telegram_id)
    for kind in KINDS:
        if st[kind]["granted"]:
            db.add(
                Consent(
                    telegram_id=telegram_id,
                    kind=kind,
                    granted=False,
                    version=config.LEGAL_VERSION,
                    source=reason,
                    created_at=datetime.utcnow(),
                )
            )


# --------------------------------------------------------------------------- #
#  Зависимости FastAPI
# --------------------------------------------------------------------------- #
def _guard(kind: str):
    """Собрать зависимость, пропускающую запрос только при наличии согласия."""

    def dependency(
        user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ) -> User:
        if not granted(db, user.telegram_id, kind):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "consent_required",
                    "kind": kind,
                    "message": "Нужно согласие: " + BLOCKS.get(kind, kind),
                },
            )
        return user

    return dependency


# Готовые зависимости — их вешают на маршруты через dependencies=[...],
# чтобы не менять сигнатуры обработчиков.
require_health_consent = _guard("health")
require_ai_consent = _guard("cross_border")
