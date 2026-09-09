"""Рублёвая витрина: цена и кнопка оплаты в рублях показываются ДО подключения
платёжной системы.

Так и задумано: цену в рублях и кнопку оплаты требует модерация платёжного
сервиса (ЮKassa и т.п.), которую проходят ДО получения ключей. При этом пока
провайдер не подключён, нажатие не должно ничего активировать.

Запуск:  .venv/Scripts/python.exe tests/test_rub_showcase.py
"""

import importlib
import os
import sys
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp, "rub.db").replace("\\", "/")
os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["OWNER_ID"] = "0"
os.environ["ALLOW_INSECURE_AUTH"] = "1"


def build_app(**env):
    """Пересобрать приложение с заданным окружением."""
    for key in ("CLOUDPAYMENTS_PUBLIC_ID", "CLOUDPAYMENTS_API_SECRET",
                "YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY",
                "PRICE_MONTHLY_RUB", "PRICE_YEARLY_RUB", "PRICE_LIFETIME_RUB",
                "PAYMENT_PROVIDER"):
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

    # --- 1. Ничего не настроено: витрина всё равно есть --------------------
    client = build_app()
    status = client.get("/subscription/status").json()

    check("витрина включена без платёжки", status.get("card_enabled") is True,
          status.get("card_enabled"))
    check("провайдер = none", status.get("card_provider") == "none",
          status.get("card_provider"))
    check("валюта рубли", status.get("card_currency") == "RUB", status.get("card_currency"))

    prices = status.get("card_prices") or {}
    check("цена месячного есть", prices.get("monthly", 0) > 0, prices)
    check("цена годового есть", prices.get("yearly", 0) > 0, prices)
    check("цена вечного есть", prices.get("lifetime", 0) > 0, prices)
    check("все три тарифа в каталоге",
          set(status.get("tariffs", {})) == {"monthly", "yearly", "lifetime"},
          sorted(status.get("tariffs", {})))
    check("в каталоге только рубли (цен в звёздах нет)",
          all(set(cfg) == {"days", "price", "currency"} and cfg["currency"] == "RUB"
              for cfg in status.get("tariffs", {}).values()),
          status.get("tariffs"))

    # Оплатить картой пока нельзя — ключей нет.
    resp = client.get("/payment/cloudpayments/config", params={"tariff": "monthly"})
    check("конфиг карты недоступен без ключей", resp.status_code == 503, resp.status_code)

    # --- 2. Цены переопределяются переменными окружения --------------------
    client = build_app(PRICE_MONTHLY_RUB="349", PRICE_YEARLY_RUB="2990",
                       PRICE_LIFETIME_RUB="0")
    prices = client.get("/subscription/status").json().get("card_prices") or {}
    check("своя цена месячного", prices.get("monthly") == 349, prices)
    check("своя цена годового", prices.get("yearly") == 2990, prices)
    check("нулевая цена скрывает тариф из витрины", "lifetime" not in prices, prices)
    check("тариф без цены исчезает и из каталога",
          "lifetime" not in (client.get("/subscription/status").json().get("tariffs") or {}),
          client.get("/subscription/status").json().get("tariffs"))

    # --- 3. Подключение CloudPayments переключает провайдера ---------------
    client = build_app(CLOUDPAYMENTS_PUBLIC_ID="pk", CLOUDPAYMENTS_API_SECRET="sec")
    status = client.get("/subscription/status").json()
    check("провайдер = cloudpayments", status.get("card_provider") == "cloudpayments",
          status.get("card_provider"))
    check("витрина осталась", status.get("card_enabled") is True, status.get("card_enabled"))

    # --- 4. Ключи ЮKassa переключают провайдера ----------------------------
    client = build_app(YOOKASSA_SHOP_ID="123456", YOOKASSA_SECRET_KEY="live_secret")
    status = client.get("/subscription/status").json()
    check("провайдер = yookassa по ключам", status.get("card_provider") == "yookassa",
          status.get("card_provider"))

    # Провайдера можно задать и явно — это перебивает автоопределение.
    client = build_app(PAYMENT_PROVIDER="yookassa")
    status = client.get("/subscription/status").json()
    check("явный провайдер уважается", status.get("card_provider") == "yookassa",
          status.get("card_provider"))

    # Явное отключение приёма карт при сохранённой витрине.
    client = build_app(PAYMENT_PROVIDER="none",
                       CLOUDPAYMENTS_PUBLIC_ID="pk", CLOUDPAYMENTS_API_SECRET="sec")
    status = client.get("/subscription/status").json()
    check("явный none перебивает ключи", status.get("card_provider") == "none",
          status.get("card_provider"))

    # --- 5. Витрина не выдаёт доступ сама по себе -------------------------
    client = build_app()
    status = client.get("/subscription/status").json()
    check("премиум не выдан витриной", status.get("is_premium") is False, status)

    if problems:
        print("FAIL:")
        for p in problems:
            print("  -", p)
        return 1

    print("OK: рублёвая цена и кнопка показываются без подключённой платёжки;")
    print("    цены переопределяются через PRICE_*_RUB, нулевая цена убирает тариф;")
    print("    провайдер определяется автоматически и задаётся явно (yookassa/none);")
    print("    сама витрина премиум не выдаёт")
    return 0


if __name__ == "__main__":
    sys.exit(main())
