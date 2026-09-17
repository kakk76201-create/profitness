"""Оплата подписки: только рубли, только карта (Stars полностью убраны).

Проверяем контракт статуса подписки, на который опирается фронт:
  * в тарифах НЕТ цен в звёздах — только срок и рублёвая цена;
  * витрина (card_enabled/card_prices) показывается по наличию ЦЕНЫ, а не по
    подключённой платёжной системе (так требует модерация платёжного сервиса);
  * card_provider выбирается по ключам, а явный PAYMENT_PROVIDER всё перебивает;
  * реквизиты продавца (legal) приезжают из переменных окружения;
  * маршрут оплаты звёздами удалён из приложения (POST по нему -> 4xx);
  * оплата картой по-прежнему включает премиум;
  * тариф «3 месяца» (quarterly): 90 дней за 1790 ₽ стоит в каталоге между
    месяцем и годом, оплата на эту сумму даёт +90 дней и складывается с
    остатком, оплата по цене месяца его НЕ открывает, описание платежа —
    по-русски с названием продукта, а напоминание об окончании получают
    подписчики каждого срочного тарифа.

Запуск:  .venv/Scripts/python.exe tests/test_dual_payment.py
"""

import base64
import hashlib
import hmac
import importlib
import os
import sys
import pathlib
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import urlencode

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp()
SECRET = "dual_secret"

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp, "dual.db").replace("\\", "/")
os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["OWNER_ID"] = "0"
os.environ["ALLOW_INSECURE_AUTH"] = "1"

# Все переменные, которые тест переключает между сборками приложения.
TOGGLED = (
    "CLOUDPAYMENTS_PUBLIC_ID", "CLOUDPAYMENTS_API_SECRET",
    "YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY",
    "PAYMENT_PROVIDER",
    "PRICE_MONTHLY_RUB", "PRICE_QUARTERLY_RUB", "PRICE_YEARLY_RUB", "PRICE_LIFETIME_RUB",
    "LEGAL_SELLER", "LEGAL_INN", "SUPPORT_CONTACT", "OFFER_URL", "PRIVACY_URL",
)


def build_app(**env):
    """Пересобрать приложение с заданным окружением (остальное — по умолчанию)."""
    for key in TOGGLED:
        os.environ.pop(key, None)
    os.environ.update(env)

    import backend.config
    importlib.reload(backend.config)
    import backend.cloudpayments
    importlib.reload(backend.cloudpayments)
    import backend.yookassa
    importlib.reload(backend.yookassa)
    import backend.main
    importlib.reload(backend.main)

    from fastapi.testclient import TestClient
    from backend.database import init_db
    init_db()
    return TestClient(backend.main.app)


def sign(body: bytes) -> str:
    """Подпись CloudPayments: base64(HMAC-SHA256(сырое тело, API Secret))."""
    return base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()


def post_cp_webhook(client, fields: dict):
    """Отправить подписанное уведомление CloudPayments."""
    body = urlencode(fields).encode()
    return client.post(
        "/payment/cloudpayments/webhook",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Content-HMAC": sign(body)},
    )


def get_user(tid):
    from backend.database import SessionLocal
    from backend import models as M
    db = SessionLocal()
    try:
        return db.query(M.User).filter(M.User.telegram_id == tid).first()
    finally:
        db.close()


def main():
    problems = []

    def check(name, condition, extra=""):
        if not condition:
            problems.append(f"{name}  {extra}")

    # --- 1. Карты НЕ настроены: витрина есть, но принять деньги нечем -------
    # Рублёвая цена и кнопка показываются всегда (их требует модерация
    # платёжного сервиса, которую проходят ДО получения ключей).
    client = build_app()
    status = client.get("/subscription/status").json()

    check("статус отдаётся", "tariffs" in status, status)
    check("витрина показывается", status.get("card_enabled") is True,
          status.get("card_enabled"))
    check("провайдер = none без ключей", status.get("card_provider") == "none",
          status.get("card_provider"))
    check("валюта рубли", status.get("card_currency") == "RUB", status.get("card_currency"))
    check("рублёвые цены есть", bool(status.get("card_prices")), status.get("card_prices"))

    tariffs = status.get("tariffs") or {}
    # По умолчанию продаются месяц, 3 месяца и год: у вечного тарифа цена 0,
    # он остаётся лишь для ручной выдачи владельцем.
    check("по умолчанию три тарифа: месяц, 3 месяца и год",
          set(tariffs) == {"monthly", "quarterly", "yearly"}, sorted(tariffs))
    # Порядок ключей — это порядок карточек на витрине (от короткого к длинному).
    check("порядок тарифов в каталоге: месяц, 3 месяца, год",
          list(tariffs) == ["monthly", "quarterly", "yearly"], list(tariffs))
    check("каталог месячного тарифа по контракту",
          tariffs.get("monthly") == {"days": 30, "price": 699.0, "currency": "RUB"},
          tariffs.get("monthly"))
    check("каталог тарифа «3 месяца» по контракту",
          tariffs.get("quarterly") == {"days": 90, "price": 1790.0, "currency": "RUB"},
          tariffs.get("quarterly"))
    # Лесенка честная: чем дольше срок, тем дешевле обходится месяц.
    per_month = {k: v["price"] / (v["days"] / 30) for k, v in tariffs.items()}
    check("лесенка цен: месяц дороже всего, год дешевле всего",
          per_month.get("monthly", 0) > per_month.get("quarterly", 0) > per_month.get("yearly", 0),
          per_month)
    check("годовой по умолчанию 5590 ₽",
          (tariffs.get("yearly") or {}).get("price") == 5590.0, tariffs.get("yearly"))
    check("вечный тариф не продаётся", "lifetime" not in tariffs, sorted(tariffs))
    for name, cfg in tariffs.items():
        check(f"у тарифа {name} нет поля stars", "stars" not in (cfg or {}), cfg)
        check(f"у тарифа {name} валюта RUB", (cfg or {}).get("currency") == "RUB", cfg)
    check("в JSON статуса вообще нет слова stars",
          "stars" not in client.get("/subscription/status").text.lower(),
          client.get("/subscription/status").text[:300])

    resp = client.get("/payment/cloudpayments/config", params={"tariff": "monthly"})
    check("конфиг карты -> 503, когда выключено", resp.status_code == 503, resp.status_code)
    resp = client.post("/payment/yookassa/create", json={"tariff": "monthly"})
    check("ЮKassa -> 503, когда ключей нет", resp.status_code == 503, resp.status_code)

    # --- 2. Маршрут оплаты звёздами удалён ---------------------------------
    # Самого маршрута в приложении больше нет. HTTP-код при этом 405, а не 404:
    # на "/" смонтирована статика SPA, и она отвечает «метод не поддерживается»
    # на POST по любому неизвестному пути. Главное — оплату он не создаёт.
    import backend.main as _main_module
    check("маршрута /payment/stars/invoice нет в приложении",
          not any(getattr(r, "path", "") == "/payment/stars/invoice"
                  for r in _main_module.app.routes),
          [getattr(r, "path", "") for r in _main_module.app.routes if "stars" in getattr(r, "path", "")])
    resp = client.post("/payment/stars/invoice", json={"tariff": "monthly"})
    check("оплата звёздами не создаётся (4xx)", 400 <= resp.status_code < 500,
          resp.status_code)

    # --- 3. Выбор провайдера по ключам и явной настройке --------------------
    client = build_app(CLOUDPAYMENTS_PUBLIC_ID="pk_dual", CLOUDPAYMENTS_API_SECRET=SECRET)
    check("ключи CloudPayments -> cloudpayments",
          client.get("/subscription/status").json().get("card_provider") == "cloudpayments",
          client.get("/subscription/status").json().get("card_provider"))

    client = build_app(YOOKASSA_SHOP_ID="123", YOOKASSA_SECRET_KEY="live_secret")
    check("ключи ЮKassa -> yookassa",
          client.get("/subscription/status").json().get("card_provider") == "yookassa",
          client.get("/subscription/status").json().get("card_provider"))

    # Заданы обе платёжки — приоритет у ЮKassa (её подключают после модерации).
    client = build_app(CLOUDPAYMENTS_PUBLIC_ID="pk", CLOUDPAYMENTS_API_SECRET="sec",
                       YOOKASSA_SHOP_ID="123", YOOKASSA_SECRET_KEY="live_secret")
    check("при обеих платёжках приоритет у ЮKassa",
          client.get("/subscription/status").json().get("card_provider") == "yookassa",
          client.get("/subscription/status").json().get("card_provider"))

    # Явное значение перебивает автоопределение в обе стороны.
    client = build_app(PAYMENT_PROVIDER="cloudpayments",
                       YOOKASSA_SHOP_ID="123", YOOKASSA_SECRET_KEY="live_secret")
    check("явный cloudpayments побеждает ключи ЮKassa",
          client.get("/subscription/status").json().get("card_provider") == "cloudpayments",
          client.get("/subscription/status").json().get("card_provider"))

    client = build_app(PAYMENT_PROVIDER="none",
                       CLOUDPAYMENTS_PUBLIC_ID="pk", CLOUDPAYMENTS_API_SECRET="sec")
    status = client.get("/subscription/status").json()
    check("явный none перебивает ключи", status.get("card_provider") == "none",
          status.get("card_provider"))
    check("витрина при этом осталась", status.get("card_enabled") is True,
          status.get("card_enabled"))

    # --- 4. Реквизиты продавца приезжают из окружения -----------------------
    client = build_app(LEGAL_SELLER="ИП Иванов И. И.", LEGAL_INN="123456789012",
                       SUPPORT_CONTACT="@support_bot",
                       OFFER_URL="https://example.com/offer",
                       PRIVACY_URL="https://example.com/privacy")
    legal = client.get("/subscription/status").json().get("legal") or {}
    check("продавец", legal.get("seller") == "ИП Иванов И. И.", legal)
    check("ИНН", legal.get("inn") == "123456789012", legal)
    check("контакт", legal.get("contact") == "@support_bot", legal)
    check("оферта", legal.get("offer_url") == "https://example.com/offer", legal)
    check("политика", legal.get("privacy_url") == "https://example.com/privacy", legal)

    legal_empty = build_app().get("/subscription/status").json().get("legal") or {}
    check("незаданные реквизиты -> None",
          set(legal_empty) == {"seller", "inn", "contact", "offer_url", "privacy_url"}
          and all(v is None for v in legal_empty.values()), legal_empty)

    # --- 5. Карты настроены: цены и конфиг виджета --------------------------
    client = build_app(CLOUDPAYMENTS_PUBLIC_ID="pk_dual", CLOUDPAYMENTS_API_SECRET=SECRET,
                       PRICE_MONTHLY_RUB="499", PRICE_QUARTERLY_RUB="1290",
                       PRICE_YEARLY_RUB="3990",
                       PRICE_LIFETIME_RUB="0")  # вечный картой не продаётся
    status = client.get("/subscription/status").json()

    check("card_enabled=True", status.get("card_enabled") is True, status.get("card_enabled"))
    prices = status.get("card_prices") or {}
    check("цена месячного", prices.get("monthly") == 499, prices)
    check("цена годового", prices.get("yearly") == 3990, prices)
    check("цена 3 месяцев из PRICE_QUARTERLY_RUB", prices.get("quarterly") == 1290, prices)
    check("тариф без рублёвой цены не предлагается", "lifetime" not in prices, prices)
    check("тариф без цены исчез и из каталога",
          set(status.get("tariffs") or {}) == {"monthly", "quarterly", "yearly"},
          sorted(status.get("tariffs") or {}))

    resp = client.get("/payment/cloudpayments/config", params={"tariff": "monthly"})
    check("конфиг карты 200", resp.status_code == 200, resp.text[:200])
    cfg = resp.json() if resp.status_code == 200 else {}
    check("public_id отдан", cfg.get("public_id") == "pk_dual", cfg.get("public_id"))
    check("секрет не утёк", SECRET not in resp.text, "секрет в ответе!")
    check("сумма в рублях", cfg.get("amount") == 499, cfg.get("amount"))
    check("плательщик — текущий пользователь", cfg.get("account_id") == "1",
          cfg.get("account_id"))

    resp = client.get("/payment/cloudpayments/config", params={"tariff": "lifetime"})
    check("вечный картой -> 400", resp.status_code == 400, resp.status_code)

    # --- 6. Оплата картой реально включает премиум --------------------------
    fields = {
        "OperationType": "Payment", "Status": "Completed", "TransactionId": "9001",
        "Amount": "499", "Currency": "RUB", "AccountId": "1",
        "InvoiceId": cfg.get("invoice_id", "monthly:1:x"), "TestMode": "0",
    }
    body = urlencode(fields).encode()
    signature = base64.b64encode(
        hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
    ).decode()
    resp = client.post(
        "/payment/cloudpayments/webhook",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Content-HMAC": signature},
    )
    check("вебхук -> {'code': 0}", resp.json() == {"code": 0}, resp.json())

    status = client.get("/subscription/status").json()
    check("после оплаты картой премиум активен", status.get("is_premium") is True, status)
    check("тип подписки monthly", status.get("subscription_type") == "monthly",
          status.get("subscription_type"))

    # --- 7. Тариф «3 месяца»: виджет и вебхук по ценам по умолчанию ----------
    client = build_app(CLOUDPAYMENTS_PUBLIC_ID="pk_dual", CLOUDPAYMENTS_API_SECRET=SECRET)
    import backend.config as cfg_module
    import backend.cloudpayments as cp_module

    resp = client.get("/payment/cloudpayments/config", params={"tariff": "quarterly"})
    check("конфиг виджета для 3 месяцев 200", resp.status_code == 200, resp.text[:200])
    q_cfg = resp.json() if resp.status_code == 200 else {}
    check("сумма 3 месяцев — 1790 ₽ из прайса сервера", q_cfg.get("amount") == 1790,
          q_cfg.get("amount"))
    check("номер заказа содержит тариф quarterly",
          str(q_cfg.get("invoice_id", "")).startswith("quarterly:"), q_cfg.get("invoice_id"))
    # Описание видят плательщик и модератор: продукт и тариф по-русски, без кода.
    q_desc = str(q_cfg.get("description") or "")
    check("описание CloudPayments: «Fitness Up» и «3 месяца»",
          "Fitness Up" in q_desc and "3 месяца" in q_desc, q_desc)
    check("описание CloudPayments без кода тарифа и старого названия",
          "quarterly" not in q_desc and "Калории" not in q_desc, q_desc)
    check("описание для каждого тарифа по-русски, без кода",
          all(name not in cfg_module.payment_description(name) for name in cfg_module.TARIFFS),
          [cfg_module.payment_description(name) for name in cfg_module.TARIFFS])

    base_fields = {"OperationType": "Payment", "Status": "Completed",
                   "Currency": "RUB", "TestMode": "0"}

    # Честная оплата 1790 ₽ открывает доступ на 90 дней.
    started = datetime.utcnow()
    resp = post_cp_webhook(client, dict(base_fields, TransactionId="9101", Amount="1790",
                                        AccountId="2001",
                                        InvoiceId="quarterly:2001:20260916120000"))
    check("вебхук 1790 ₽ за 3 месяца -> {'code': 0}", resp.json() == {"code": 0}, resp.json())
    q_user = get_user(2001)
    check("подписка «3 месяца» активирована",
          q_user is not None and q_user.subscription_type == "quarterly",
          q_user and q_user.subscription_type)
    if q_user is not None and q_user.subscription_until is not None:
        delta = q_user.subscription_until - started
        check("3 месяца = +90 дней",
              timedelta(days=90) <= delta < timedelta(days=90, minutes=5), delta)
    else:
        check("у подписки «3 месяца» есть дата окончания", False, q_user)

    # Цена МЕСЯЦА с номером заказа «3 месяца» — попытка взять 90 дней за 699 ₽.
    resp = post_cp_webhook(client, dict(base_fields, TransactionId="9102", Amount="699",
                                        AccountId="2002",
                                        InvoiceId="quarterly:2002:20260916120001"))
    check("699 ₽ за 3 месяца -> код неверной суммы",
          resp.json().get("code") == cp_module.CODE_INVALID_AMOUNT, resp.json())
    check("699 ₽ за 3 месяца -> доступ НЕ выдан", get_user(2002) is None, get_user(2002))

    # --- 8. Покупка «3 месяцев» складывается с действующей подпиской ---------
    from backend import payment_providers
    from backend.database import SessionLocal
    from backend import models as M

    remaining_until = datetime.utcnow() + timedelta(days=10)
    db = SessionLocal()
    db.add(M.User(telegram_id=2003, subscription_type="monthly",
                  subscription_until=remaining_until))
    db.commit()
    db.close()
    db = SessionLocal()
    try:
        payment_providers.activate_premium(db, 2003, "quarterly", "cloudpayments",
                                           1790, "RUB", charge_id="cp:stack-2003")
    finally:
        db.close()
    stacked = get_user(2003)
    check("3 месяца сложились с остатком: +90 дней к дате окончания",
          stacked is not None
          and stacked.subscription_until == remaining_until + timedelta(days=90),
          (remaining_until, stacked and stacked.subscription_until))
    check("после продления тип — quarterly",
          stacked is not None and stacked.subscription_type == "quarterly",
          stacked and stacked.subscription_type)

    # --- 9. Напоминание об окончании получают ВСЕ срочные тарифы -------------
    # Фильтр типов в напоминаниях перечислен явно; новый тариф без записи там
    # молча оставил бы своих подписчиков без предупреждения. Проверяем каждый
    # срочный тариф из конфига, а триал, наоборот, получать не должен.
    from backend import notifications

    now = datetime(2030, 1, 15, 13, 0)
    today = now.strftime("%Y-%m-%d")
    timed = [name for name, t in cfg_module.TARIFFS.items() if t.get("days")]
    check("срочные тарифы в конфиге: месяц, 3 месяца, год",
          timed == ["monthly", "quarterly", "yearly"], timed)
    tids = {}
    db = SessionLocal()
    for i, name in enumerate(timed + ["trial"]):
        tid = 2100 + i
        tids[tid] = name
        db.add(M.User(telegram_id=tid, subscription_type=name,
                      subscription_until=now + timedelta(days=3)))
    db.commit()
    db.close()

    sent = []

    def fake_send(chat_id, text):
        sent.append(chat_id)
        return True

    with patch.object(notifications, "send_telegram", side_effect=fake_send):
        db = SessionLocal()
        try:
            notifications._process_subscription_lifecycle(db, now, today)
        finally:
            db.close()
    reminded = {tids[t] for t in sent if t in tids}
    check("напоминание за 3 дня получили все срочные тарифы",
          reminded >= set(timed), sorted(reminded))
    check("триалу «продлите подписку» не шлём", "trial" not in reminded, sorted(reminded))

    if problems:
        print("FAIL:")
        for p in problems:
            print("  -", p)
        return 1

    print("OK: цены только в рублях (поля stars нет нигде), каталог тарифов по контракту;")
    print("    витрина работает без ключей, провайдер выбирается по ключам, а явный")
    print("    PAYMENT_PROVIDER их перебивает; реквизиты продавца из env; маршрута")
    print("    оплаты звёздами больше нет; оплата картой включает премиум;")
    print("    «3 месяца»: 90 дней за 1790 ₽ между месяцем и годом, складывается с")
    print("    остатком, по цене месяца не открывается, описание платежа по-русски,")
    print("    напоминание об окончании получают все срочные тарифы")
    return 0


if __name__ == "__main__":
    sys.exit(main())
