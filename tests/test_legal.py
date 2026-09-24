"""Документы и согласия (152-ФЗ): страницы публикуются с реквизитами, экран
согласий показывается новым и «старым» пользователям, функции без согласия
закрыты, выгрузка данных уходит файлом, журнал согласий переживает удаление."""
import json, os, pathlib, sys, tempfile
from unittest.mock import patch

ROOT = pathlib.Path(r"C:\Games\MiniApp"); sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmp, "legal.db").replace("\\", "/")
os.environ["ALLOW_INSECURE_AUTH"] = "1"; os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["OPENAI_API_KEY"] = "dummy"; os.environ["OWNER_ID"] = "999"
os.environ["LEGAL_SELLER"] = "ИП Иванов Иван Иванович"
os.environ["LEGAL_INN"] = "123456789012"
os.environ["SUPPORT_CONTACT"] = "@fitness_up_support"
os.environ["BOT_USERNAME"] = "fitness_up_bot"
os.environ["MINI_APP_URL"] = "https://app.example.com"
os.environ.pop("RAILWAY_ENVIRONMENT", None); os.environ.pop("RAILWAY_PROJECT_ID", None)

from fastapi.testclient import TestClient
from backend import config, legal, models as M, payment_providers, ratelimit, telegram_bot
from backend.database import init_db, SessionLocal
from backend.main import app

init_db()
c = TestClient(app)
fails = []

# Трекер цикла — платная функция: выдаём доступ, иначе премиум-проверка
# ответит 402 раньше, чем мы дойдём до проверки согласия.
with SessionLocal() as db:
    payment_providers.activate_premium(db, 1, "yearly", "yookassa", 5590.0, "RUB", charge_id="yk:legal")


def chk(name, cond, extra=""):
    if not cond:
        fails.append(name + ("  " + str(extra) if extra else ""))


def consents(tid=1):
    with SessionLocal() as db:
        return db.query(M.Consent).filter(M.Consent.telegram_id == tid).count()


# ---------- 1. Страницы документов ----------
for name in ("terms", "privacy", "offer", "consent"):
    r = c.get(f"/legal/{name}.html")
    chk(f"{name}: страница отдаётся", r.status_code == 200, r.status_code)
    body = r.text
    chk(f"{name}: реквизиты подставлены", "ИП Иванов Иван Иванович" in body and "ИНН 123456789012" in body)
    chk(f"{name}: контакт подставлен", "@fitness_up_support" in body, name)
    chk(f"{name}: не осталось плейсхолдеров", "{{" not in body, body[:200])
    chk(f"{name}: это целая страница", body.lstrip().startswith("<!DOCTYPE html>"))
    chk(f"{name}: нет предупреждения о реквизитах", "без реквизитов продавца" not in body)

chk("оферта: цена и возврат описаны", "возврат" in c.get("/legal/offer.html").text.lower())
chk("политика: трансграничная передача", "рансгранич" in c.get("/legal/privacy.html").text)
chk("соглашение: не медицинская услуга", "не оказывает медицинских услуг" in c.get("/legal/terms.html").text)
chk("чужой документ — 404", c.get("/legal/config.html").status_code == 404)
chk("путь наружу — не 200", c.get("/legal/..%2F..%2Fbackend%2Fconfig.html").status_code != 200)
chk("документы открыты без авторизации", "x-telegram-init-data" not in c.get("/legal/terms.html").request.headers)

# ---------- 2. Экран согласий ----------
r = c.get("/consent")
chk("статус согласий 200", r.status_code == 200, r.status_code)
data = r.json()
chk("новому нужен экран согласий", data["needs_consent"] is True, data)
chk("обязательные названы", data["required"] == ["pd", "terms"], data["required"])
chk("ссылки на документы абсолютные",
    data["docs"]["privacy"] == "https://app.example.com/legal/privacy.html", data["docs"])

# ---------- 3. Функции закрыты без согласия ----------
chk("фото еды без согласия — 403", c.post("/food/analyze", files={"file": ("a.jpg", b"x", "image/jpeg")}).status_code == 403)
r = c.post("/cycle/log", json={"cycle_start_date": "2026-09-01"})
chk("цикл без согласия — 403", r.status_code == 403, r.status_code)
chk("в ошибке указан вид согласия", r.json()["detail"]["kind"] == "health", r.json())
chk("код ошибки машиночитаем", r.json()["detail"]["error"] == "consent_required")

# ---------- 4. Согласие даётся и записывается ----------
r = c.post("/consent", json={"pd": True, "terms": True, "cross_border": True})
chk("сохранение 200", r.status_code == 200, r.status_code)
st = r.json()
chk("экран больше не нужен", st["needs_consent"] is False, st)
chk("редакция зафиксирована", st["state"]["pd"]["version"] == config.LEGAL_VERSION, st["state"]["pd"])
chk("дата зафиксирована", bool(st["state"]["pd"]["date"]))
chk("здоровье не включалось само", st["state"]["health"]["granted"] is False)
chk("журнал: три записи", consents() == 3, consents())

# Повторное согласие на ту же редакцию журнал не засоряет.
c.post("/consent", json={"pd": True, "terms": True})
chk("повтор не пишется в журнал", consents() == 3, consents())

chk("цикл всё ещё закрыт", c.post("/cycle/log", json={"cycle_start_date": "2026-09-01"}).status_code == 403)
c.post("/consent", json={"health": True})
chk("цикл открылся", c.post("/cycle/log", json={"cycle_start_date": "2026-09-01"}).status_code == 200)

# ---------- 5. Отзыв согласия закрывает функцию обратно ----------
c.post("/consent", json={"cross_border": False})
chk("после отзыва фото снова 403",
    c.post("/food/analyze", files={"file": ("a.jpg", b"x", "image/jpeg")}).status_code == 403)
chk("отзыв записан отдельной строкой", consents() == 5, consents())
with SessionLocal() as db:
    chk("данные цикла при отзыве не удалены", db.query(M.CycleLog).count() == 1)

# ---------- 5.1. Голосовое боту тоже уважает согласие ----------
# Тот же путь распознавания, что и в приложении, только мимо его проверок:
# если он не смотрит на согласие, весь экран согласий бессмыслен.
said = []
with patch.object(telegram_bot, "_bot_api", lambda m, pl: said.append((m, pl))), \
        patch.object(telegram_bot.ai_service, "transcribe_audio",
                     lambda *a, **k: (_ for _ in ()).throw(AssertionError("ИИ вызван без согласия"))):
    with SessionLocal() as db:
        telegram_bot.handle_update(db, {"message": {"message_id": 7, "from": {"id": 1}, "chat": {"id": 1},
                                                    "voice": {"file_id": "vf1"}}})
chk("голосовое без согласия: ответ вместо распознавания", len(said) == 1, said)
txt = said[0][1].get("text", "") if said else ""
chk("в ответе объяснено, что включить",
    ("Профиль" in txt) or ("Profile" in txt), txt)

c.post("/consent", json={"cross_border": True})
said2 = []
with patch.object(telegram_bot, "_bot_api", lambda m, pl: said2.append((m, pl))):
    with SessionLocal() as db:
        telegram_bot.handle_update(db, {"message": {"message_id": 8, "from": {"id": 1}, "chat": {"id": 1},
                                                    "voice": {"file_id": "vf1"}}})
chk("с согласием идём дальше (getFile)", any(m == "getFile" for m, _ in said2), said2)
c.post("/consent", json={"cross_border": False})

# ---------- 6. Новая редакция документов спрашивается заново ----------
with patch.object(config, "LEGAL_VERSION", "2.0"):
    chk("смена редакции возвращает экран", c.get("/consent").json()["needs_consent"] is True)
    c.post("/consent", json={"pd": True, "terms": True})
    chk("согласие на новую редакцию принято", c.get("/consent").json()["needs_consent"] is False)

# ---------- 7. Выгрузка своих данных ----------
sent = {}


def fake_send(chat_id, filename, content, caption=""):
    sent["chat_id"] = chat_id; sent["filename"] = filename; sent["content"] = content
    return True


with patch.object(telegram_bot, "send_document", fake_send):
    r = c.post("/account/export")
chk("выгрузка 200", r.status_code == 200, r.status_code)
chk("файл ушёл владельцу данных", sent.get("chat_id") == 1, sent.get("chat_id"))
payload = json.loads(sent["content"].decode("utf-8"))
chk("в выгрузке профиль", payload["profile"] and payload["profile"]["telegram_id"] == 1)
chk("в выгрузке журнал согласий", len(payload["consents"]) >= 5, len(payload.get("consents", [])))
chk("в выгрузке данные цикла", len(payload["cycle"]) == 1)
chk("выгрузка сериализуема", "exported_at" in payload)

with patch.object(telegram_bot, "send_document", lambda *a, **k: False),         patch.object(ratelimit, "check", lambda *a, **k: (True, None)):
    chk("сбой отправки — честная ошибка", c.post("/account/export").status_code == 503)

with patch.object(telegram_bot, "send_document", fake_send):
    chk("выгрузка не чаще раза в минуту", c.post("/account/export").status_code == 429)

# ---------- 8. Удаление аккаунта: журнал согласий остаётся, отзыв фиксируется ----------
before = consents()
chk("удаление 200", c.delete("/account/data").status_code == 200)
chk("журнал согласий не стёрт", consents() > before, (before, consents()))
with SessionLocal() as db:
    last = db.query(M.Consent).filter(M.Consent.telegram_id == 1).order_by(M.Consent.id.desc()).first()
    chk("последняя запись — отзыв", last.granted is False and last.source == "account_deleted", last.source)
    chk("данные пользователя удалены", db.query(M.CycleLog).count() == 0)
chk("после удаления снова спросим согласие", c.get("/consent").json()["needs_consent"] is True)

# ---------- 9. Без реквизитов документ честно предупреждает ----------
legal._RENDER_CACHE.clear()
with patch.object(config, "LEGAL_SELLER", ""), patch.object(config, "LEGAL_INN", ""):
    page = legal.render("offer")
chk("предупреждение о пустых реквизитах", "без реквизитов продавца" in page)
legal._RENDER_CACHE.clear()

if fails:
    print("FAIL:\n  " + "\n  ".join(fails)); sys.exit(1)
print("OK: документы с реквизитами и без плейсхолдеров, экран согласий для новых и при смене\n"
      "    редакции, 403 без согласия, отзыв не трогает данные, выгрузка файлом, журнал переживает\n"
      "    удаление аккаунта")
