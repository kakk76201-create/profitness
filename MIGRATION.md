# Переезд проекта на другой хостинг

Инструкция подходит для любого назначения: новый аккаунт Railway, Render, Koyeb,
свой VPS. Главное правило проекта — **не потерять пользователей**, поэтому база
переносится первым шагом, а не последним.

---

## 0. СНАЧАЛА — снять копию базы

В боевой базе лежат живые люди с оплаченными подписками. Пока старый проект ещё
существует, скопируйте данные. Даже если переезд отложится — копия будет.

Возьмите старый `DATABASE_URL` (Railway → сервис Postgres → Variables →
`DATABASE_PUBLIC_URL`, именно **публичный**, внутренний `postgres.railway.internal`
снаружи не работает).

Сначала посмотрите, что будет скопировано (ничего не пишется):

```
.venv\Scripts\python.exe tools\db_copy.py --source "<СТАРЫЙ_DATABASE_URL>" --target "sqlite:///./backup_prod.db"
```

Затем реальное сохранение в локальный файл:

```
.venv\Scripts\python.exe tools\db_copy.py --source "<СТАРЫЙ_DATABASE_URL>" --target "sqlite:///./backup_prod.db" --apply
```

Файл `backup_prod.db` — ваша страховка. Он в `.gitignore`, в репозиторий не попадёт.

> **Фото прогресса не переносятся.** Они лежат файлами на диске контейнера
> (`PROGRESS_PHOTOS_DIR`), а том не смонтирован — то есть они и так пропадают при
> каждом редеплое. Это отдельная задача (том или облачное хранилище).

---

## 1. Репозиторий на новый аккаунт GitHub

На новом аккаунте создайте **пустой** приватный репозиторий (без README).

```
git remote rename origin old-origin
git remote add origin https://github.com/<НОВЫЙ_АККАУНТ>/<РЕПО>.git
git push -u origin main
```

Проверка: `git remote -v` и что коммиты видны на GitHub.

---

## 2. База данных

Создайте Postgres у выбранного провайдера и получите новый `DATABASE_URL`
(строка вида `postgresql://user:pass@host/db`).

Перенос данных — тем же инструментом (сначала без `--apply`, чтобы увидеть план):

```
.venv\Scripts\python.exe tools\db_copy.py --source "sqlite:///./backup_prod.db" --target "<НОВЫЙ_DATABASE_URL>" --apply
```

Скрипт сам создаст таблицы нужных типов и перенесёт строки. Повторный запуск
безопасен — существующие записи пропускаются.

---

## 3. Приложение

Подключите новый репозиторий к хостингу. Сборка описана в `railway.toml`
(nixpacks + `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`). На других
платформах укажите ту же команду запуска вручную.

### Переменные окружения

**Обязательные:**

| Переменная | Значение |
|---|---|
| `DATABASE_URL` | строка подключения к новой базе |
| `BOT_TOKEN` | токен бота из @BotFather |
| `OPENAI_API_KEY` | ключ OpenAI |
| `OWNER_ID` | ваш числовой Telegram ID |
| `TELEGRAM_WEBHOOK_SECRET` | случайная строка (та же уйдёт в setWebhook) |
| `MINI_APP_URL` | `https://<новый-домен>` — без неё в `/start` нет кнопки входа |
| `BOT_USERNAME` | имя бота без `@` |
| `ALLOW_INSECURE_AUTH` | **`0`** — при `1` авторизация обходится без подписи Telegram |

**Важные (иначе поведение изменится):**

`APP_TZ=Europe/Moscow`, `ENABLE_SCHEDULER=1`, `FREE_SCAN_LIMIT=3`, `TRIAL_DAYS=7`,
`SUBSCRIPTION_MONTHLY_DAYS=30`, `SUBSCRIPTION_YEARLY_DAYS=365`,
`PRICE_MONTHLY_RUB=699`, `PRICE_YEARLY_RUB=5590`, `PRICE_LIFETIME_RUB=0` (0 — вечный тариф не продаётся).

**Оплата картой** (единственный способ оплаты; цены только в рублях):
`PAYMENT_PROVIDER` (`auto` / `cloudpayments` / `yookassa` / `none`),
`CLOUDPAYMENTS_PUBLIC_ID`, `CLOUDPAYMENTS_API_SECRET`, `CLOUDPAYMENTS_CURRENCY`,
`YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`, `YOOKASSA_WEBHOOK_SECRET`, `YOOKASSA_RETURN_URL`.
Адреса вебхуков в кабинетах после переезда меняются на новый домен:
`/payment/cloudpayments/webhook` и `/payment/yookassa/webhook`.

**Реквизиты продавца** (показываются на странице оплаты, нужны для модерации):
`LEGAL_SELLER`, `LEGAL_INN`, `SUPPORT_CONTACT`, `OFFER_URL`, `PRIVACY_URL`.

**По желанию:** `DEBUG_AI=0`, `MARKET_CLID`, `TRIBUTE_*`, `OPENAI_*`,
`RATE_LIMIT_*`, `OFF_*`.

---

## 4. Переключение бота на новый домен

Домен сменился — без этих трёх шагов бот молча перестанет работать.

**Вебхук:**

```
curl "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=https://<новый-домен>/telegram/webhook&secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

Проверить: `https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo` — в ответе
должен быть новый адрес и `"pending_update_count": 0`.

**Кнопка меню бота:** @BotFather → `/mybots` → бот → Bot Settings → Menu Button →
указать новый URL.

**`MINI_APP_URL`** в переменных окружения (см. выше).

---

## 5. Проверка

1. `https://<новый-домен>/api/health` → `{"status":"ok","db":"postgresql"}`.
   Если в ответе есть `warning` про эфемерный SQLite — `DATABASE_URL` не подхватился.
2. В боте `/start` → приходит приветствие с кнопкой «Открыть приложение».
3. В боте `/users` → те же пользователи, что были раньше (проверка переноса базы).
4. Открыть мини-апп: дневник, тренировки, экран подписки с ценами.
5. Старый проект не удалять минимум неделю — на случай отката.

---

## Откат

Ничего необратимого не происходит: старый репозиторий остаётся под именем
`old-origin`, старая база не изменяется (скрипт только читает из источника).
Чтобы вернуться — верните прежний вебхук и `MINI_APP_URL`.
