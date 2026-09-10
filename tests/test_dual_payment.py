"""Оплата подписки: только рубли, только карта (Stars полностью убраны).

Проверяем контракт статуса подписки, на который опирается фронт:
  * в тарифах НЕТ цен в звёздах — только срок и рублёвая цена;
  * витрина (card_enabled/card_prices) показывается по наличию ЦЕНЫ, а не по
    подключённой платёжной системе (так требует модерация платёжного сервиса);
  * card_provider выбирается по ключам, а явный PAYMENT_PROVIDER всё перебивает;
  * реквизиты продавца (legal) приезжают из переменных окружения;
  * маршрут оплаты звёздами удалён из приложения (POST по нему -> 4xx);
  * оплата картой по-прежнему включает премиум.

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
    "PRICE_MONTHLY_RUB", "PRICE_YEARLY_RUB", "PRICE_LIFETIME_RUB",
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
    # По умолчанию продаются только месяц и год: у вечного тарифа цена 0,
    # он остаётся лишь для ручной выдачи владельцем.
    check("по умолчанию два тарифа: месяц и год", set(tariffs) == {"monthly", "yearly"},
          sorted(tariffs))
    check("каталог месячного тарифа по контракту",
          tariffs.get("monthly") == {"days": 30, "price": 699.0, "currency": "RUB"},
          tariffs.get("monthly"))
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
                       PRICE_MONTHLY_RUB="499", PRICE_YEARLY_RUB="3990",
                       PRICE_LIFETIME_RUB="0")  # вечный картой не продаётся
    status = client.get("/subscription/status").json()

    check("card_enabled=True", status.get("card_enabled") is True, status.get("card_enabled"))
    prices = status.get("card_prices") or {}
    check("цена месячного", prices.get("monthly") == 499, prices)
    check("цена годового", prices.get("yearly") == 3990, prices)
    check("тариф без рублёвой цены не предлагается", "lifetime" not in prices, prices)
    check("тариф без цены исчез и из каталога",
          set(status.get("tariffs") or {}) == {"monthly", "yearly"},
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

    if problems:
        print("FAIL:")
        for p in problems:
            print("  -", p)
        return 1

    print("OK: цены только в рублях (поля stars нет нигде), каталог тарифов по контракту;")
    print("    витрина работает без ключей, провайдер выбирается по ключам, а явный")
    print("    PAYMENT_PROVIDER их перебивает; реквизиты продавца из env; маршрута")
    print("    оплаты звёздами больше нет; оплата картой включает премиум")
    return 0


if __name__ == "__main__":
    sys.exit(main())
