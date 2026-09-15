/*
 * app.js — ядро мини-приложения.
 * Определяет глобальный объект window.App: доступ к Telegram WebApp,
 * HTTP-клиент к бэкенду, простой роутер по страницам и набор хелперов.
 *
 * ВАЖНО: этот файл НЕ вызывает App.init() — инициализация запускается
 * отдельным inline-скриптом в конце index.html (после регистрации страниц).
 *
 * Локализация: App.lang ("ru"|"en"), App.pick(ru, en), App.setLang(lang).
 * Все пользовательские строки оборачиваются в App.pick("рус","eng") НА МОМЕНТ
 * рендера, чтобы смена языка с перерисовкой давала нужный текст.
 */
(function () {
  "use strict";

  // Ссылка на Telegram WebApp (может отсутствовать вне Telegram).
  var tg = (window.Telegram && window.Telegram.WebApp) || null;

  // Глобальный объект приложения. Страницы опираются на этот публичный контракт.
  var App = {
    // Telegram WebApp SDK (или null, если приложение открыто вне Telegram).
    tg: tg,

    // Telegram-пользователь из initDataUnsafe (или null).
    user: (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) || null,

    // Текущий язык интерфейса: "ru" | "en". Определяется в App.init
    // (профиль с сервера > язык Telegram > "ru") ДО первой навигации.
    lang: "ru",

    // Простой кэш состояния приложения.
    state: {
      profile: null, // последний загруженный профиль пользователя
      diaryByDate: {} // кэш дневника по датам: { "YYYY-MM-DD": DiaryDayOut }
    },

    // Статус подписки. По умолчанию — НЕ премиум (fail-safe: при сбое
    // загрузки показываем paywall, а не открываем платные фичи).
    // Заполняется в App.init после авторизации через App.api.getSubscription().
    subscription: {
      is_premium: false,
      is_owner: false,
      subscription_type: "free",
      subscription_until: null,
      tariffs: {},
      tribute_url: null,
      // Оплата картой в рублях: до загрузки статуса считаем, что приём карт
      // не подключён (fail-safe — кнопка честно скажет «оплата подключается»).
      card_enabled: false,
      card_currency: "RUB",
      card_prices: {},
      card_provider: "none",
      legal: null
    },

    // Реестр зарегистрированных страниц: { name: controller }.
    _pages: {},

    // Имя текущей активной страницы (или null до первой навигации).
    _current: null
  };

  /* =====================================================================
   *  ЛОКАЛИЗАЦИЯ
   *  App.lang — текущий язык. App.pick(ru, en) выбирает строку по языку.
   *  ВСЕ пользовательские строки оборачиваются в App.pick НА МОМЕНТ рендера.
   * ===================================================================== */

  /**
   * Возвращает английскую строку, если App.lang === "en", иначе русскую.
   * @param {string} ru русский вариант
   * @param {string} en английский вариант
   * @returns {string}
   */
  App.pick = function (ru, en) {
    return App.lang === "en" ? en : ru;
  };

  /**
   * Переустанавливает подписи вкладок нижней навигации по текущему языку.
   * Подписи в index.html заданы по-русски — здесь они заменяются по App.pick.
   * Центральная кнопка-камера подписи не имеет (пропускаем).
   * Вызывается в init и в setLang.
   */
  function applyTabLabels() {
    // Локализуем заголовок документа и атрибут lang (видны в части клиентов).
    try {
      document.title = "Fitness Up";
      document.documentElement.lang = App.lang;
    } catch (e) {
      /* не критично */
    }

    var map = {
      today: App.pick("Сегодня", "Today"),
      trainer: App.pick("Тренировка", "Workout"),
      diary: App.pick("Питание", "Nutrition"),
      account: App.pick("Профиль", "Profile")
    };
    var tabs = document.querySelectorAll("#tabbar .tab");
    for (var i = 0; i < tabs.length; i++) {
      var tab = tabs[i];
      var page = tab.getAttribute("data-page");
      if (!page || !map.hasOwnProperty(page)) {
        continue; // например, центральная кнопка-камера (scan) — без подписи
      }
      var labelEl = tab.querySelector(".tab-label");
      if (labelEl) {
        labelEl.textContent = map[page];
      }
    }
  }

  // Публикуем applyTabLabels для возможного использования страницами.
  App.applyTabLabels = applyTabLabels;

  /**
   * Меняет язык интерфейса: ставит App.lang, сохраняет на сервер,
   * переустанавливает подписи вкладок и перерисовывает текущую страницу.
   * @param {string} lang "ru" | "en"
   */
  App.setLang = function (lang) {
    var next = lang === "en" ? "en" : "ru";
    App.lang = next;

    // Синхронизируем язык в кэше профиля (best-effort).
    if (App.state.profile && typeof App.state.profile === "object") {
      App.state.profile.language = next;
    }

    // Сохраняем выбор на сервере (не блокируем UI, ошибки гасим).
    try {
      App.api.saveProfile({ language: next }).catch(function (err) {
        console.warn("Не удалось сохранить язык: " + (err && err.message));
      });
    } catch (e) {
      console.warn("Не удалось сохранить язык", e);
    }

    // Обновляем подписи вкладок и перерисовываем текущий экран.
    applyTabLabels();
    if (App._current) {
      App.navigate(App._current);
    }
  };

  /* =====================================================================
   *  HTTP-КЛИЕНТ
   *  Базовый URL — тот же origin (""), что и статика, отдаваемая бэкендом.
   * ===================================================================== */

  // Имя заголовка авторизации Telegram (должно совпадать с бэкендом).
  var INIT_HEADER = "X-Telegram-Init-Data";

  /**
   * Возвращает строку initData для авторизации.
   * Если приложение открыто вне Telegram — вернёт пустую строку
   * (бэкенд может разрешить dev-режим через ALLOW_INSECURE_AUTH).
   */
  function initData() {
    return (App.tg && App.tg.initData) || "";
  }

  /**
   * Универсальная обёртка над fetch.
   * - Всегда добавляет заголовок X-Telegram-Init-Data.
   * - Для JSON-тела ставит Content-Type: application/json и сериализует объект.
   * - Для FormData НЕ выставляет Content-Type вручную (его проставит браузер
   *   вместе с boundary).
   * - При ответе !res.ok бросает Error с текстом detail от сервера.
   *
   * @param {string} path  путь запроса (например, "/api/profile")
   * @param {object} [opts] { method, body, isForm }
   * @returns {Promise<any>} распарсенный JSON-ответ
   */
  function request(path, opts) {
    opts = opts || {};
    var method = opts.method || "GET";
    var headers = {};
    // Заголовок авторизации Telegram присутствует во всех запросах.
    headers[INIT_HEADER] = initData();

    var fetchOpts = { method: method, headers: headers };

    if (opts.isForm) {
      // FormData: тело передаём как есть, Content-Type не трогаем.
      fetchOpts.body = opts.body;
    } else if (opts.body !== undefined && opts.body !== null) {
      // JSON: проставляем заголовок и сериализуем тело.
      headers["Content-Type"] = "application/json";
      fetchOpts.body = JSON.stringify(opts.body);
    }

    return fetch(path, fetchOpts).then(function (res) {
      // Пытаемся разобрать тело ответа как JSON (даже при ошибке —
      // там может лежать detail с описанием проблемы).
      return res
        .text()
        .then(function (raw) {
          var data = null;
          if (raw) {
            try {
              data = JSON.parse(raw);
            } catch (e) {
              data = null; // тело не JSON — оставляем null
            }
          }

          if (!res.ok) {
            // Достаём осмысленное сообщение об ошибке от сервера.
            var detail = "";
            if (data && typeof data === "object") {
              if (typeof data.detail === "string") {
                detail = data.detail;
              } else if (data.detail && typeof data.detail === "object") {
                // detail может быть объектом {error, message} (например 402)
                // или массивом ошибок валидации pydantic.
                if (typeof data.detail.message === "string") {
                  detail = data.detail.message;
                } else {
                  try {
                    detail = JSON.stringify(data.detail);
                  } catch (e2) {
                    detail = String(data.detail);
                  }
                }
              } else if (data.detail) {
                detail = String(data.detail);
              } else if (typeof data.message === "string") {
                detail = data.message;
              }
            }
            if (!detail) {
              detail = raw || App.pick("Ошибка ", "Error ") + res.status;
            }
            var err = new Error(detail);
            // Прокидываем HTTP-статус и машиночитаемый код ошибки наверх,
            // чтобы страницы могли отличить paywall (402) от прочих сбоев.
            err.status = res.status;
            if (data && data.detail && typeof data.detail === "object" &&
                typeof data.detail.error === "string") {
              err.code = data.detail.error;
            }
            throw err;
          }

          return data;
        });
    }).catch(function (err) {
      // Отдельно обрабатываем сетевые сбои (бэкенд недоступен, нет интернета).
      if (err instanceof TypeError) {
        throw new Error(
          App.pick(
            "Нет соединения с сервером. Проверьте интернет.",
            "No connection to the server. Check your internet."
          )
        );
      }
      throw err;
    });
  }

  /**
   * Обёртка над fetch для ПРИВАТНЫХ бинарных ответов (например, фото прогресса).
   * Всегда добавляет заголовок авторизации Telegram и возвращает Blob. Так
   * приватные картинки грузятся авторизованно (в <img> напрямую заголовок не
   * подставить), а не через публичную статику.
   *
   * @param {string} path путь запроса
   * @returns {Promise<Blob>} бинарное тело ответа
   */
  function requestBlob(path) {
    var headers = {};
    headers[INIT_HEADER] = initData();
    return fetch(path, { method: "GET", headers: headers })
      .then(function (res) {
        if (!res.ok) {
          var err = new Error(
            App.pick("Не удалось загрузить изображение", "Failed to load image")
          );
          err.status = res.status;
          throw err;
        }
        return res.blob();
      })
      .catch(function (err) {
        if (err instanceof TypeError) {
          throw new Error(
            App.pick("Нет соединения с сервером.", "No connection to the server.")
          );
        }
        throw err;
      });
  }

  /**
   * Публичный API-клиент. Каждый метод возвращает Promise и
   * бросает Error(message) при неудаче.
   */
  App.api = {
    // Подтверждение авторизации и получение профиля (триггерит upsert на бэке).
    verify: function () {
      return request("/auth/verify", { method: "POST" });
    },

    // Анализ фото еды. Принимает File, отправляет multipart/form-data.
    analyzeFood: function (file) {
      var form = new FormData();
      form.append("file", file);
      return request("/food/analyze", {
        method: "POST",
        body: form,
        isForm: true
      });
    },

    // Голосовой ввод еды (Этап 2, ПРЕМИУМ). Принимает аудио File, отправляет
    // multipart/form-data (поле "file"). Для free бэкенд отдаёт 402 (paywall).
    // Ответ: {transcript, meal_type, items:[{dish_name,calories,proteins,fats,carbs}]}.
    analyzeVoice: function (file) {
      var form = new FormData();
      form.append("file", file);
      return request("/food/voice", {
        method: "POST",
        body: form,
        isForm: true
      });
    },

    // Добавление записи в дневник (тело — DiaryEntryIn).
    addDiary: function (entry) {
      return request("/diary/add", { method: "POST", body: entry });
    },

    // Получение дневника за конкретную дату ("YYYY-MM-DD").
    getDiary: function (dateStr) {
      return request("/diary/" + encodeURIComponent(dateStr));
    },

    // Удаление записи дневника по id.
    deleteEntry: function (id) {
      return request("/diary/" + encodeURIComponent(id), { method: "DELETE" });
    },

    // Частичное редактирование записи дневника. patch — только изменяемые поля
    // (dish_name, meal_type, calories, proteins, fats, carbs, quantity, unit).
    // Ответ: обновлённый DiaryEntryOut.
    updateEntry: function (id, patch) {
      return request("/diary/" + encodeURIComponent(id), {
        method: "PATCH",
        body: patch
      });
    },

    // Серия дней подряд с записями («стрик»). today — локальная дата клиента.
    // Ответ: {current, longest, logged_today}.
    getStreak: function (today) {
      return request(
        "/stats/streak" + (today ? "?today=" + encodeURIComponent(today) : "")
      );
    },

    // Получение профиля пользователя.
    getProfile: function () {
      return request("/profile");
    },

    // Сохранение профиля (тело — ProfileIn, все поля опциональны).
    saveProfile: function (data) {
      return request("/profile", { method: "POST", body: data });
    },

    // История за последние N дней (по умолчанию 30).
    getHistory: function (days) {
      var d = days || 30;
      return request("/history?days=" + encodeURIComponent(d));
    },

    /* -------------------------------------------------------------------
     *  ТРЕНИРОВКИ
     * ------------------------------------------------------------------- */

    // Добавление тренировки. Тело: {date, type, duration_min, calories_burned}.
    // Ответ: {id, date, type, duration_min, calories_burned}.
    addWorkout: function (w) {
      return request("/workout/add", { method: "POST", body: w });
    },

    // Тренировки за дату ("YYYY-MM-DD"). Ответ: {date, workouts:[...], total_burned}.
    getWorkouts: function (dateStr) {
      return request("/workout/" + encodeURIComponent(dateStr));
    },

    // Удаление тренировки по id. Ответ: {ok}.
    deleteWorkout: function (id) {
      return request("/workout/" + encodeURIComponent(id), { method: "DELETE" });
    },

    // Оценка сожжённых калорий. Тело: {type, duration_min}.
    // Ответ: {calories_burned, met}.
    estimateWorkout: function (payload) {
      return request("/workout/estimate", { method: "POST", body: payload });
    },

    // Совет по восстановлению (ПРЕМИУМ). Тело: {zone, complaint|null}.
    // Ответ: {zone, likely_cause, is_typical_soreness, today[], avoid[],
    //         training, red_flags[], disclaimer}.
    getRecoveryAdvice: function (payload) {
      return request("/recovery/advice", { method: "POST", body: payload });
    },

    /* -------------------------------------------------------------------
     *  ЕДА (ручной ввод, недавнее, рекомендации)
     * ------------------------------------------------------------------- */

    // Ручное добавление блюда в дневник.
    // Тело: {date, meal_type, dish_name, calories, proteins, fats, carbs}.
    // Ответ: DiaryEntryOut.
    addManualFood: function (entry) {
      return request("/food/manual", { method: "POST", body: entry });
    },

    // Недавно добавленные блюда.
    // Ответ: {items:[{dish_name, calories, proteins, fats, carbs}]}.
    getRecentFoods: function () {
      return request("/food/recent");
    },

    // Поиск блюд с готовыми КБЖУ во внешней базе продуктов (НЕ премиум).
    // Ответ: {query, per, items:[{code, name, brand, calories, proteins, fats, carbs}]}.
    // Значения — на 100 г / 100 мл. Пустой список — не ошибка (вводим вручную).
    searchFood: function (query) {
      return request("/food/search?q=" + encodeURIComponent(query));
    },

    // Расчёт КБЖУ блюда по названию/количеству/единице (НЕ премиум — базовый дневник).
    // Тело: {name, quantity:float|null, unit:str|null}.
    // Ответ: {dish_name, quantity, unit, calories, proteins, fats, carbs}.
    calculateFood: function (payload) {
      return request("/food/calculate", { method: "POST", body: payload });
    },

    // Записи «вчера» (для быстрого добавления). dateIso — "YYYY-MM-DD".
    // Ответ: {items:[{dish_name, quantity, unit, calories, proteins, fats, carbs, meal_type}]}.
    getYesterday: function (dateIso) {
      return request("/food/yesterday?date=" + encodeURIComponent(dateIso));
    },

    // Рекомендации блюд по остатку нормы.
    // Тело: {remaining_calories, remaining_proteins, remaining_fats,
    //        remaining_carbs, diet_goal?, time_of_day?}.
    // Ответ: {suggestions:[{dish_name, calories, proteins, fats, carbs, reason}]}.
    recommendFood: function (payload) {
      return request("/food/recommend", { method: "POST", body: payload });
    },

    /* -------------------------------------------------------------------
     *  ДОБАВКИ (БАДы, витамины и т.п.)
     * ------------------------------------------------------------------- */

    // Добавление добавки.
    // Тело: {name, type, dosage, intake_time?, reminder_enabled?}.
    // Ответ: {id, name, type, dosage, intake_time, reminder_enabled}.
    addSupplement: function (s) {
      return request("/supplement/add", { method: "POST", body: s });
    },

    // Список добавок. Ответ: {items:[...]}.
    getSupplements: function () {
      return request("/supplement/list");
    },

    // Удаление добавки по id. Ответ: {ok}.
    deleteSupplement: function (id) {
      return request("/supplement/" + encodeURIComponent(id), {
        method: "DELETE"
      });
    },

    // Подсказки по добавкам.
    // Ответ: {suggestions:[{name, dosage, note}], disclaimer}.
    suggestSupplements: function () {
      return request("/supplement/suggest");
    },

    /* -------------------------------------------------------------------
     *  ЦЕЛЬ ПО КАЛОРИЯМ
     * ------------------------------------------------------------------- */

    // Расчёт дневной нормы. Тело: {weight?, height?, age?, gender?,
    //   activity_level?, diet_goal?}.
    // Ответ: {daily_goal_kcal, target_proteins, target_fats, target_carbs,
    //   diet_goal, bmr, tdee}.
    // ВНИМАНИЕ: сервер сам сохраняет результат в профиль.
    calculateGoal: function (payload) {
      return request("/goal/calculate", { method: "POST", body: payload });
    },

    /* -------------------------------------------------------------------
     *  УВЕДОМЛЕНИЯ
     * ------------------------------------------------------------------- */

    // Текущие настройки уведомлений.
    // Ответ: {telegram_id, meal_reminder_enabled, breakfast_time, lunch_time,
    //   dinner_time, training_reminder_enabled, training_time,
    //   supplement_reminder_enabled, daily_summary_enabled, summary_time}.
    getNotificationSettings: function () {
      return request("/notifications/settings");
    },

    // Сохранение настроек уведомлений (частичный объект тех же полей).
    // Ответ: NotificationSettingsOut.
    saveNotificationSettings: function (payload) {
      return request("/notifications/settings", {
        method: "POST",
        body: payload
      });
    },

    /* -------------------------------------------------------------------
     *  НАПОМИНАНИЯ О ТРЕНИРОВКАХ
     *  weekdays: массив int (0=Пн,1=Вт,2=Ср,3=Чт,4=Пт,5=Сб,6=Вс).
     * ------------------------------------------------------------------- */

    // Список напоминаний о тренировке.
    // Ответ: {items:[{id, weekdays:[int], time, enabled}]}.
    getTrainingReminders: function () {
      return request("/reminders/training");
    },

    // Добавление напоминания о тренировке.
    // Тело: {weekdays:[int], time, enabled}.
    // Ответ: {id, weekdays, time, enabled}.
    addTrainingReminder: function (r) {
      return request("/reminders/training", { method: "POST", body: r });
    },

    // Удаление напоминания о тренировке по id. Ответ: {ok}.
    deleteTrainingReminder: function (id) {
      return request("/reminders/training/" + encodeURIComponent(id), {
        method: "DELETE"
      });
    },

    /* -------------------------------------------------------------------
     *  НАПОМИНАНИЯ О ПРИЁМЕ ДОБАВОК
     * ------------------------------------------------------------------- */

    // Список напоминаний о приёме добавок.
    // Ответ: {items:[{id, label, time, enabled, supplements:[{id, name}]}]}.
    getSupplementReminders: function () {
      return request("/reminders/supplement");
    },

    // Добавление напоминания о приёме добавок.
    // Тело: {label, time, enabled, supplement_ids:[int]}.
    // Ответ: {id, label, time, enabled, supplements:[{id, name}]}.
    addSupplementReminder: function (r) {
      return request("/reminders/supplement", { method: "POST", body: r });
    },

    // Удаление напоминания о приёме добавок по id. Ответ: {ok}.
    deleteSupplementReminder: function (id) {
      return request("/reminders/supplement/" + encodeURIComponent(id), {
        method: "DELETE"
      });
    },

    /* -------------------------------------------------------------------
     *  AI-РЕКОМЕНДАЦИИ ДОБАВОК ПО ЦЕЛИ УЛУЧШЕНИЯ
     * ------------------------------------------------------------------- */

    // Подбор добавок под цель улучшения.
    // Тело: {improvement_goal}.
    // Ответ: {suggestions:[{name, dosage, note}], disclaimer,
    //   training_count, improvement_goal}.
    recommendSupplements: function (payload) {
      return request("/supplement/recommend", {
        method: "POST",
        body: payload
      });
    },

    /* -------------------------------------------------------------------
     *  ПОДПИСКА И ОПЛАТА (Этап 1)
     * ------------------------------------------------------------------- */

    // Статус подписки пользователя.
    // Ответ: {subscription_type, subscription_until, is_premium, is_owner,
    //   tariffs:{monthly:{days,price,currency}, yearly:{...}, lifetime:{...}},
    //   card_enabled, card_currency, card_prices, card_provider,
    //   legal:{seller, inn, contact, offer_url, privacy_url}, tribute_url}.
    getSubscription: function () {
      return request("/subscription/status");
    },

    // Остаток бесплатных сканирований на сегодня.
    // Ответ: {used, limit, remaining (-1=безлимит), is_premium}.
    getScansRemaining: function () {
      return request("/scans/remaining");
    },

    // Активировать одноразовый пробный период. Ответ: тот же SubscriptionStatusOut.
    startTrial: function () {
      return request("/subscription/trial", { method: "POST" });
    },

    // Полное удаление всех данных пользователя и профиля (необратимо).
    // Ответ: {ok, deleted}. После успеха фронт перезапускает приложение.
    deleteAccountData: function () {
      return request("/account/data", { method: "DELETE" });
    },

    // Параметры виджета оплаты картой (CloudPayments) по выбранному тарифу.
    // Ответ: {public_id, amount, currency, description, account_id, invoice_id, tariff}.
    // API Secret на клиент НЕ передаётся — только публичные параметры.
    getCardPaymentConfig: function (tariff) {
      return request(
        "/payment/cloudpayments/config?tariff=" + encodeURIComponent(tariff)
      );
    },

    // Создание платежа ЮKassa по выбранному тарифу.
    // Тело: {tariff:"monthly"|"yearly"|"lifetime"}.
    // Ответ: {payment_id, confirmation_url} — страницу подтверждения открываем
    // во внешнем браузере; доступ активирует вебхук, а не фронт.
    createYookassaPayment: function (tariff) {
      return request("/payment/yookassa/create", {
        method: "POST",
        body: { tariff: tariff }
      });
    },

    /* -------------------------------------------------------------------
     *  ТРЕКИНГ ВЕСА И АДАПТИВНЫЕ КАЛОРИИ (Этап 3, ПРЕМИУМ)
     *  Все три роута платные: для free бэкенд отдаёт 402 (paywall).
     * ------------------------------------------------------------------- */

    // Добавление/обновление замера веса (upsert по дате).
    // Тело: {date:"YYYY-MM-DD", weight:float}.
    // Ответ: {id, date, weight}.
    addWeight: function (payload) {
      return request("/weight/add", { method: "POST", body: payload });
    },

    // История веса за N дней (по умолчанию 90).
    // Ответ: {logs:[{date, weight}], trend:[{date, weight}],
    //   latest:float|null, change_kg:float|null}.
    getWeightHistory: function (days) {
      var d = days || 90;
      return request("/weight/history?days=" + encodeURIComponent(d));
    },

    // Пересчёт адаптивной цели по реальной динамике веса.
    // Тело: {} (пустое). Ответ: {enough_data:bool, maintenance:int|null,
    //   new_goal:int|null, weekly_change_kg:float|null, avg_intake:int|null,
    //   days_used:int, explanation:str}.
    recalcAdaptive: function () {
      return request("/calories/recalculate-adaptive", {
        method: "POST",
        body: {}
      });
    },

    /* -------------------------------------------------------------------
     *  AI-ФУНКЦИИ (Этап 5, ПРЕМИУМ)
     *  Все роуты платные: для free бэкенд отдаёт 402 (paywall).
     * ------------------------------------------------------------------- */

    // Недельный AI-отчёт по дневнику/тренировкам/весу.
    // Ответ: {summary, insights:[str], focus:str|null, stats:{avg_calories,
    //   goal, calories_trend, avg_proteins, avg_fats, avg_carbs, days_logged,
    //   workouts_count, total_burned, weight_change_kg, avg_deficit}|null}.
    getWeeklyReport: function () {
      return request("/report/weekly");
    },

    // AI-планировщик меню. Тело: {scope:"day"|"week", preferences?, budget?}.
    // Ответ: {days:[{label, meals:{breakfast:[{dish_name,calories,proteins,
    //   fats,carbs}], lunch:[...], dinner:[...], snack:[...]}}], shopping_list:[str]}.
    generateMealPlan: function (payload) {
      return request("/meal-plan/generate", { method: "POST", body: payload });
    },

    // Замена одного блюда в плане меню.
    // Тело: {meal_type, around_calories?, preferences?}.
    // Ответ: {dish_name, calories, proteins, fats, carbs}.
    regenerateMealItem: function (payload) {
      return request("/meal-plan/regenerate-item", {
        method: "POST",
        body: payload
      });
    },

    // Умные предложения еды по остатку нормы / типу приёма / свободному тексту.
    // Тело: {meal_type?, free_text?, remaining_calories, remaining_proteins,
    //   remaining_fats, remaining_carbs}.
    // Ответ: {suggestions:[{dish_name, calories, proteins, fats, carbs, reason}]}.
    suggestFood: function (payload) {
      return request("/food/suggest", { method: "POST", body: payload });
    },

    // Готовый список полезных перекусов («вкусняшек»).
    // Ответ: {suggestions:[{dish_name, calories, proteins, fats, carbs, reason}]}.
    getHealthySnacks: function () {
      return request("/food/healthy-snacks");
    },

    // --- Трекинг цикла (Этап 6, ПРЕМИУМ) ---
    // Текущий статус цикла: фаза, день, прогнозы, фертильное окно.
    // Ответ: {has_data, phase, day_of_cycle, next_period_date, ...}.
    getCycleStatus: function () {
      return request("/cycle/status");
    },

    // Сохранить данные цикла и получить пересчитанный статус.
    // Тело: {cycle_start_date, cycle_length?, period_length?, notes?}.
    logCycle: function (payload) {
      return request("/cycle/log", { method: "POST", body: payload });
    },

    // Сбросить (удалить) все данные цикла пользователя.
    resetCycle: function () {
      return request("/cycle", { method: "DELETE" });
    },

    // --- Фото-прогресс (Этап 7, ПРЕМИУМ, приватно) ---
    // Загрузить фото прогресса (multipart). date/weight — необязательны.
    // Ответ: {id, date, weight, image_url, created_at}.
    uploadProgress: function (file, date, weight) {
      var form = new FormData();
      form.append("file", file);
      if (date) form.append("date", date);
      if (weight !== undefined && weight !== null && weight !== "") {
        form.append("weight", weight);
      }
      return request("/progress/upload", { method: "POST", body: form, isForm: true });
    },

    // Список фото прогресса (по возрастанию даты). Ответ: {items:[...]}.
    getProgressList: function () {
      return request("/progress/list");
    },

    // Загрузить приватное изображение как blob и вернуть object URL для <img>.
    // Вызывающий обязан освободить URL через URL.revokeObjectURL по завершении.
    getProgressImageUrl: function (id) {
      return requestBlob("/progress/" + encodeURIComponent(id) + "/image").then(
        function (blob) {
          return URL.createObjectURL(blob);
        }
      );
    },

    // Удалить фото прогресса по id.
    deleteProgress: function (id) {
      return request("/progress/" + encodeURIComponent(id), { method: "DELETE" });
    },

    /* -------------------------------------------------------------------
     *  AI-ТРЕНЕР (ПРЕМИУМ, префикс /trainer — backend/trainer.py, ТЗ §4)
     *  Все роуты платные: для free бэкенд отдаёт 402 (paywall).
     *  Ошибки ИИ → 502 с русским detail, лимиты → 429.
     * ------------------------------------------------------------------- */

    // Обзор для экрана «Сегодня»: {profile, program, today, streak, week[7],
    //   active_session_id, pending_review}.
    trainerOverview: function () {
      return request("/trainer/overview");
    },

    // Профиль тренера (404, если анкета не заполнялась). Ответ: TrainerProfileOut.
    trainerProfile: function () {
      return request("/trainer/profile");
    },

    // Сохранение анкеты (TrainerProfileIn: goal, level, equipment, equipment_extra,
    //   days_per_week, preferred_weekdays, session_minutes, program_weeks,
    //   limitations, limitations_text, focus, reminder_enabled, reminder_time).
    // Побочный эффект: создаёт/обновляет TrainingReminder. Ответ: TrainerProfileOut.
    trainerSaveProfile: function (data) {
      return request("/trainer/profile", { method: "POST", body: data });
    },

    // Генерация программы (ИИ, heavy-лимит). Тело: {regenerate_note?}.
    // Ответ: TrainerProgramOut (с раскрытыми днями). Предыдущая активная → archived.
    trainerGenerateProgram: function (payload) {
      return request("/trainer/program/generate", {
        method: "POST",
        body: payload || {}
      });
    },

    // Активная (или указанная) программа. Ответ: TrainerProgramOut; 404 если нет.
    trainerProgram: function (programId) {
      return request(
        "/trainer/program" +
          (programId != null ? "?program_id=" + encodeURIComponent(programId) : "")
      );
    },

    // Архивировать программу. Ответ: {ok}.
    trainerArchiveProgram: function (programId) {
      return request("/trainer/program/" + encodeURIComponent(programId) + "/archive", {
        method: "POST"
      });
    },

    // План на дату. Ответ: TrainerTodayOut {date, is_training_day, kind, day,
    //   next_date, active_session}.
    trainerToday: function (dateStr) {
      return request(
        "/trainer/today" + (dateStr ? "?date=" + encodeURIComponent(dateStr) : "")
      );
    },

    // Старт сессии. Тело: {program_day_id: int|null, date}. Ответ: TrainerSessionOut;
    // 409 — уже есть активная сессия.
    trainerStartSession: function (payload) {
      return request("/trainer/session/start", { method: "POST", body: payload });
    },

    // Активная сессия. Ответ: TrainerSessionOut | null.
    trainerActiveSession: function () {
      return request("/trainer/session/active");
    },

    // Сессия по id. Ответ: TrainerSessionOut.
    trainerSession: function (sessionId) {
      return request("/trainer/session/" + encodeURIComponent(sessionId));
    },

    // Сохранить подход (upsert по session_exercise_id + set_index).
    // Тело: {session_exercise_id, set_index, set_type, weight_kg, reps, time_sec,
    //   rpe, is_done}. Ответ: {set, prs[], rest_sec, exercise_status}.
    trainerSaveSet: function (sessionId, payload) {
      return request("/trainer/session/" + encodeURIComponent(sessionId) + "/set", {
        method: "POST",
        body: payload
      });
    },

    // Удалить подход. Ответ: {ok}.
    trainerDeleteSet: function (sessionId, setId) {
      return request(
        "/trainer/session/" + encodeURIComponent(sessionId) +
          "/set/" + encodeURIComponent(setId),
        { method: "DELETE" }
      );
    },

    // Альтернативы упражнению. reason: busy|no_equipment|pain|other.
    // Ответ: {items: [TrainerExerciseBriefOut], reason}.
    trainerAlternatives: function (exerciseId, reason, sessionId) {
      var q = "?reason=" + encodeURIComponent(reason || "other");
      if (sessionId != null) q += "&session_id=" + encodeURIComponent(sessionId);
      return request("/trainer/exercises/" + encodeURIComponent(exerciseId) + "/alternatives" + q);
    },

    // Заменить упражнение в сессии. Тело: {new_exercise_id, reason, remember}.
    // Ответ: TrainerSessionExerciseOut (новая строка).
    trainerReplaceExercise: function (sessionId, sexId, payload) {
      return request(
        "/trainer/session/" + encodeURIComponent(sessionId) +
          "/exercise/" + encodeURIComponent(sexId) + "/replace",
        { method: "POST", body: payload }
      );
    },

    // Пропустить упражнение. Ответ: {ok, status:"skipped"}.
    trainerSkipExercise: function (sessionId, sexId) {
      return request(
        "/trainer/session/" + encodeURIComponent(sessionId) +
          "/exercise/" + encodeURIComponent(sexId) + "/skip",
        { method: "POST" }
      );
    },

    // Добавить упражнение в сессию. Тело: {exercise_id, sets, reps_min, reps_max}.
    // Ответ: TrainerSessionExerciseOut.
    trainerAddExercise: function (sessionId, payload) {
      return request(
        "/trainer/session/" + encodeURIComponent(sessionId) + "/exercise/add",
        { method: "POST", body: payload }
      );
    },

    // Завершить сессию (создаёт Workout). Тело: {duration_min?, note?}.
    // Ответ: {session, summary, prs, workout_id}; 400 — нет ни одного сета.
    trainerFinishSession: function (sessionId, payload) {
      return request(
        "/trainer/session/" + encodeURIComponent(sessionId) + "/finish",
        { method: "POST", body: payload || {} }
      );
    },

    // Отзыв после тренировки. Тело: {feedback: easy|ok|hard, note?, rpe?}.
    // Ответ: TrainerAdaptationOut {changes, lines, message}.
    trainerFeedback: function (sessionId, payload) {
      return request(
        "/trainer/session/" + encodeURIComponent(sessionId) + "/feedback",
        { method: "POST", body: payload }
      );
    },

    // Отменить сессию (Workout не создаётся). Ответ: {ok}.
    trainerAbandonSession: function (sessionId) {
      return request(
        "/trainer/session/" + encodeURIComponent(sessionId) + "/abandon",
        { method: "POST" }
      );
    },

    // История сессий. Ответ: {items: [TrainerSessionBriefOut], total}.
    trainerSessions: function (limit, offset) {
      return request(
        "/trainer/sessions?limit=" + encodeURIComponent(limit || 20) +
          "&offset=" + encodeURIComponent(offset || 0)
      );
    },

    // Прогресс. params: {exercise_id?, period?: "4w"|"3m"|"all"}.
    // Ответ: TrainerProgressOut {streak, totals_4w, week_compare, muscle_volume_7d,
    //   records, top_exercises, chart}.
    trainerProgress: function (params) {
      params = params || {};
      var q = [];
      if (params.exercise_id != null) q.push("exercise_id=" + encodeURIComponent(params.exercise_id));
      if (params.period) q.push("period=" + encodeURIComponent(params.period));
      return request("/trainer/progress" + (q.length ? "?" + q.join("&") : ""));
    },

    // Библиотека упражнений. params: {muscle?, equipment?, q?, limit?}.
    // Ответ: {items: [Brief + difficulty, category, technique_status, excluded]}.
    trainerExercises: function (params) {
      params = params || {};
      var q = [];
      if (params.muscle) q.push("muscle=" + encodeURIComponent(params.muscle));
      if (params.equipment) q.push("equipment=" + encodeURIComponent(params.equipment));
      if (params.q) q.push("q=" + encodeURIComponent(params.q));
      if (params.limit) q.push("limit=" + encodeURIComponent(params.limit));
      return request("/trainer/exercises" + (q.length ? "?" + q.join("&") : ""));
    },

    // Карточка упражнения; withTechnique=true — с генерацией/кэшем техники (ИИ).
    // Ответ: TrainerExerciseOut {…, technique|null, technique_status, excluded}.
    trainerExercise: function (exerciseId, withTechnique) {
      return request(
        "/trainer/exercises/" + encodeURIComponent(exerciseId) +
          (withTechnique ? "?technique=1" : "")
      );
    },

    // История по упражнению. Ответ: {records, sessions, points}.
    trainerExerciseHistory: function (exerciseId) {
      return request("/trainer/exercises/" + encodeURIComponent(exerciseId) + "/history");
    },

    // Исключить/вернуть упражнение. Ответ: {ok, excluded}.
    trainerExcludeExercise: function (exerciseId, excluded) {
      return request(
        "/trainer/exercises/" + encodeURIComponent(exerciseId) + "/exclude",
        { method: "POST", body: { excluded: !!excluded } }
      );
    },

    // Недельный разбор (ИИ, heavy-лимит). Тело: {week_start?}.
    // Ответ: TrainerWeeklyReviewOut; 409 — за неделю 0 сессий.
    trainerWeeklyReview: function (payload) {
      return request("/trainer/review/weekly", { method: "POST", body: payload || {} });
    },

    // Последний сохранённый разбор. Ответ: TrainerWeeklyReviewOut | null.
    trainerLatestReview: function () {
      return request("/trainer/review/latest");
    },

    // Применить выбранные правки разбора к следующей неделе.
    // Ответ: {applied: [int], lines: [str]}.
    trainerApplyReview: function (reviewId, changeIds) {
      return request(
        "/trainer/review/" + encodeURIComponent(reviewId) + "/apply",
        { method: "POST", body: { change_ids: changeIds || [] } }
      );
    },

    // Совет по питанию на день (ИИ при промахе кэша). Ответ: TrainerNutritionTipOut.
    trainerNutritionToday: function (dateStr) {
      return request(
        "/trainer/nutrition/today" + (dateStr ? "?date=" + encodeURIComponent(dateStr) : "")
      );
    }
  };

  /* =====================================================================
   *  РОУТЕР ПО СТРАНИЦАМ
   * ===================================================================== */

  /**
   * Регистрирует страницу.
   * @param {string} name  одно из {scan, diary, account, workouts, supplements, subscription}
   * @param {object} controller { onShow(viewEl), onHide?() }
   */
  App.registerPage = function (name, controller) {
    App._pages[name] = controller;
  };

  /**
   * Переход на страницу по имени.
   * Вызывает onHide текущей страницы, очищает #view, подсвечивает таб
   * и вызывает onShow целевой страницы.
   * @param {string} name
   */
  /**
   * Экраны-задачи: пользователь занят одним делом, и нижняя навигация только
   * мешает. Главное — тренировка: с видимым таббаром из неё выходишь случайным
   * тапом, теряя незавершённую сессию без всякого подтверждения.
   * Возврат с таких экранов — только их собственной кнопкой «Назад»/«Закрыть».
   */
  var TASK_PAGES = {
    "trainer-session": true,
    "trainer-onboarding": true,
    onboarding: true,
    payment: true
  };

  /**
   * Вкладка, которую подсвечивать для экрана вне таббара. Без этого при
   * переходе вглубь раздела навигация «гасла» — ни один пункт не активен,
   * и пользователь терял понимание, где находится.
   */
  var TAB_OF_PAGE = {
    trainer: "trainer",
    "trainer-program": "trainer",
    "trainer-progress": "trainer",
    "trainer-exercise": "trainer",
    "trainer-session": "trainer",
    "trainer-onboarding": "trainer",
    supplements: "account",
    subscription: "account",
    payment: "account"
  };

  App.navigate = function (name) {
    var target = App._pages[name];
    if (!target) {
      // Запрошена незарегистрированная страница — игнорируем во избежание краша.
      return;
    }

    // Скрываем текущую страницу (если у неё есть обработчик onHide).
    if (App._current && App._pages[App._current]) {
      var prev = App._pages[App._current];
      if (typeof prev.onHide === "function") {
        try {
          prev.onHide();
        } catch (e) {
          // Ошибка в onHide не должна блокировать навигацию.
          console.error("Ошибка в onHide страницы " + App._current, e);
        }
      }
    }

    // Очищаем контейнер представления.
    var viewEl = document.getElementById("view");
    if (viewEl) {
      viewEl.innerHTML = "";
    }

    // Режим экрана-задачи: прячем нижнюю навигацию (класс на <body>).
    document.body.classList.toggle("is-task-screen", !!TASK_PAGES[name]);

    // Обновляем активный таб. Для экранов вне таббара подсвечиваем вкладку
    // раздела, которому экран принадлежит.
    var activeTab = TAB_OF_PAGE[name] || name;
    var tabs = document.querySelectorAll("#tabbar .tab");
    for (var i = 0; i < tabs.length; i++) {
      var t = tabs[i];
      if (t.getAttribute("data-page") === activeTab) {
        t.classList.add("active");
      } else {
        t.classList.remove("active");
      }
    }

    App._current = name;

    // Показываем целевую страницу.
    if (typeof target.onShow === "function") {
      target.onShow(viewEl);
    }

    // Сбрасываем прокрутку наверх: иначе после смены экрана (или короткого
    // экрана ошибки) можно «застрять» прокрученным вниз без возможности вернуться.
    App.scrollTop();
  };

  /* =====================================================================
   *  ПОДПИСКА: ЕДИНЫЙ PAYWALL И ОПЛАТА
   *  Контроль доступа — на бэкенде (платные роуты отдают 402). Фронт лишь
   *  ПОКАЗЫВАЕТ paywall и предлагает оформить подписку, не дублируя проверки
   *  как «безопасность».
   * ===================================================================== */

  /**
   * Премиум ли текущий пользователь (по кэшированному статусу).
   * @returns {boolean}
   */
  App.isPremium = function () {
    return !!(App.subscription && App.subscription.is_premium);
  };

  /**
   * Перезагружает статус подписки в App.subscription (best-effort).
   * При ошибке статус НЕ меняется (остаётся прежним).
   * @returns {Promise}
   */
  App.refreshSubscription = function () {
    return App.api
      .getSubscription()
      .then(function (status) {
        if (status && typeof status === "object") {
          App.subscription = status;
        }
        return App.subscription;
      })
      .catch(function (err) {
        // Не критично: оставляем прежний статус. Логируем для диагностики.
        console.warn("Не удалось обновить статус подписки: " + err.message);
        return App.subscription;
      });
  };

  /**
   * Рендерит экран-заглушку (paywall) заблокированной фичи в контейнер.
   * Тексты, передаваемые страницами (icon/title/desc/bullets), уже должны
   * идти через App.pick на стороне страниц. Собственные подписи paywall
   * («Недоступно — нужна подписка», «Оформить подписку») локализуются здесь.
   * @param {HTMLElement} viewEl  контейнер для вставки
   * @param {object} [opts] { icon, title, desc, bullets:[...] }
   */
  App.paywall = function (viewEl, opts) {
    if (!viewEl) {
      return;
    }
    opts = opts || {};
    var icon = opts.icon || "🔒";
    var title = opts.title || App.pick("Премиум-функция", "Premium feature");
    var desc =
      opts.desc ||
      App.pick(
        "Эта возможность доступна по подписке",
        "This feature is available with a subscription"
      );
    var bullets = Array.isArray(opts.bullets) ? opts.bullets : [];

    var bulletsHtml = "";
    if (bullets.length) {
      var items = "";
      for (var i = 0; i < bullets.length; i++) {
        items +=
          '<li class="paywall-bullet">' +
          '<span class="paywall-bullet-mark" aria-hidden="true">✓</span>' +
          '<span class="paywall-bullet-text">' +
          App.escapeHtml(bullets[i]) +
          "</span>" +
          "</li>";
      }
      bulletsHtml = '<ul class="paywall-bullets">' + items + "</ul>";
    }

    var html =
      '<section class="paywall">' +
      '<div class="card paywall-card">' +
      '<div class="paywall-icon" aria-hidden="true">' +
      App.escapeHtml(icon) +
      "</div>" +
      '<h2 class="paywall-title">' +
      App.escapeHtml(title) +
      "</h2>" +
      '<p class="paywall-desc">' +
      App.escapeHtml(desc) +
      "</p>" +
      bulletsHtml +
      "</div>" +
      '<div class="paywall-lock">' +
      '<span class="paywall-lock-icon" aria-hidden="true">🔒</span>' +
      '<span class="paywall-lock-text">' +
      App.escapeHtml(
        App.pick("Недоступно — нужна подписка", "Unavailable — subscription required")
      ) +
      "</span>" +
      "</div>" +
      '<button type="button" class="btn btn-cta btn-block paywall-cta" id="paywall-subscribe">' +
      App.escapeHtml(App.pick("Оформить подписку", "Get subscription")) +
      "</button>" +
      "</section>";

    viewEl.innerHTML = html;

    var btn = viewEl.querySelector("#paywall-subscribe");
    if (btn) {
      btn.addEventListener("click", function () {
        App.haptic("light");
        App.goSubscription();
      });
    }
  };

  /**
   * Открывает экран подписки, запомнив страницу, откуда пришли, в
   * App.state.subOrigin — чтобы «Назад» на экране подписки вернул обратно.
   * Если мы уже на подписке — origin не перезаписываем.
   */
  App.goSubscription = function () {
    if (App._current && App._current !== "subscription") {
      App.state.subOrigin = App._current;
    }
    App.navigate("subscription");
  };

  /**
   * Требует премиум для показа фичи. Если премиум есть — возвращает true.
   * Иначе рендерит paywall в контейнер и возвращает false.
   * Страницы используют как ранний выход:
   *   if (!App.requirePremium(viewEl, {...})) return;
   * @param {HTMLElement} viewEl
   * @param {object} [opts]
   * @returns {boolean}
   */
  App.requirePremium = function (viewEl, opts) {
    if (App.isPremium()) {
      return true;
    }
    App.paywall(viewEl, opts);
    return false;
  };

  // Тарифы, для которых существует страница оплаты (совпадают с config.TARIFFS).
  var PAYMENT_TARIFFS = ["monthly", "yearly", "lifetime"];

  /**
   * Открывает отдельную страницу оплаты для выбранного тарифа.
   * Тариф кладём в App.state.paymentTariff — страница "payment" читает его
   * при onShow (навигация в приложении без параметров в URL).
   * @param {string} tariff "monthly"|"yearly"|"lifetime"
   */
  App.goPayment = function (tariff) {
    if (typeof tariff !== "string" || PAYMENT_TARIFFS.indexOf(tariff) === -1) {
      App.toast(App.pick("Неизвестный тариф", "Unknown plan"));
      return;
    }
    if (!App._pages || !App._pages.payment) {
      // Страница оплаты не подключена (старая версия ассетов) — не роняем UI.
      App.toast(
        App.pick("Оплата временно недоступна", "Payment is temporarily unavailable")
      );
      return;
    }
    App.state.paymentTariff = tariff;
    App.navigate("payment");
  };

  /* =====================================================================
   *  ОПЛАТА КАРТОЙ В РУБЛЯХ — единственный способ оплаты в приложении.
   *
   *  Куда вести пользователя, решает бэкенд полем card_provider:
   *    "cloudpayments" — виджет CloudPayments прямо в мини-приложении;
   *    "yookassa"      — платёж создаётся на бэкенде, страница подтверждения
   *                      открывается во внешнем браузере;
   *    "none"          — приём карт ещё не подключён (витрина с ценой видна,
   *                      это нужно для модерации в платёжном сервисе).
   *
   *  Виджет CloudPayments подгружается ЛЕНИВО, только когда пользователь
   *  реально начал оплату: сторонний скрипт не должен тормозить запуск
   *  приложения у всех остальных.
   *
   *  ВАЖНО: доступ выдаётся ТОЛЬКО вебхуком с проверенной подписью. Колбэк
   *  onSuccess здесь используется исключительно для UI (показать «активируем…»
   *  и опросить статус) — доверять ему как факту оплаты нельзя.
   * ===================================================================== */

  // Адрес скрипта виджета CloudPayments.
  var CP_WIDGET_SRC = "https://widget.cloudpayments.ru/bundles/cloudpayments.js";
  var _cpLoading = null;

  /**
   * Лениво подгружает скрипт виджета CloudPayments (один раз).
   * @returns {Promise} резолвится, когда window.cp доступен
   */
  function loadCloudPaymentsWidget() {
    if (window.cp && typeof window.cp.CloudPayments === "function") {
      return Promise.resolve();
    }
    if (_cpLoading) return _cpLoading;

    _cpLoading = new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      script.src = CP_WIDGET_SRC;
      script.async = true;
      script.onload = function () {
        if (window.cp && typeof window.cp.CloudPayments === "function") {
          resolve();
        } else {
          reject(new Error("widget not available"));
        }
      };
      script.onerror = function () {
        _cpLoading = null; // разрешаем повторную попытку
        reject(new Error("widget failed to load"));
      };
      document.head.appendChild(script);
    });
    return _cpLoading;
  }

  /**
   * Оплата подписки банковской картой через виджет CloudPayments.
   * @param {string} tariff "monthly" | "yearly" | "lifetime"
   * @returns {Promise}
   */
  function payCardCloudPayments(tariff) {
    return App.api
      .getCardPaymentConfig(tariff)
      .then(function (cfg) {
        if (!cfg || !cfg.public_id) {
          throw new Error(App.pick("Оплата картой недоступна", "Card payment unavailable"));
        }
        return loadCloudPaymentsWidget().then(function () {
          return new Promise(function (resolve) {
            var widget = new window.cp.CloudPayments();
            widget.pay(
              "charge",
              {
                publicId: cfg.public_id,
                description: cfg.description,
                amount: cfg.amount,
                currency: cfg.currency,
                invoiceId: cfg.invoice_id,
                // accountId — по нему вебхук определит, кому начислять доступ.
                accountId: cfg.account_id,
                skin: "modern"
              },
              {
                onSuccess: function () {
                  // Доступ активирует вебхук — ждём и опрашиваем статус.
                  App.toast(App.pick(
                    "Оплата получена, активируем доступ…",
                    "Payment received, activating…"
                  ));
                  App._pollPremium(8, 1500);
                  resolve();
                },
                onFail: function () {
                  App.toast(App.pick("Оплата не прошла", "Payment failed"));
                  resolve();
                },
                onComplete: function () {
                  // Вызывается при любом закрытии виджета (в т.ч. крестиком):
                  // страховка, чтобы промис не завис и оверлей загрузки снялся.
                  resolve();
                }
              }
            );
          });
        });
      })
      .catch(function (err) {
        App.toast(
          err && err.message ? err.message : App.pick("Ошибка оплаты", "Payment error")
        );
      });
  }

  /**
   * Оплата подписки банковской картой через ЮKassa.
   * Бэкенд создаёт платёж и отдаёт confirmation_url — открываем его во внешнем
   * браузере (в WebView Telegram платёжная форма может не работать) и ждём
   * активации доступа вебхуком, опрашивая статус подписки.
   * @param {string} tariff "monthly" | "yearly" | "lifetime"
   * @returns {Promise}
   */
  function payCardYookassa(tariff) {
    return App.api
      .createYookassaPayment(tariff)
      .then(function (res) {
        var url = res && res.confirmation_url;
        if (!url) {
          throw new Error(
            App.pick("Не удалось создать платёж", "Failed to create payment")
          );
        }
        if (App.tg && typeof App.tg.openLink === "function") {
          App.tg.openLink(url);
        } else if (typeof window.open === "function") {
          window.open(url, "_blank");
        } else {
          throw new Error(
            App.pick("Ссылка для оплаты недоступна", "Payment link is unavailable")
          );
        }
        App.toast(
          App.pick(
            "Завершите оплату в браузере — доступ появится автоматически",
            "Finish the payment in your browser — access will appear automatically"
          )
        );
        // Оплата идёт во внешнем браузере: ждём дольше, чем при виджете.
        App._pollPremium(20, 3000);
      })
      .catch(function (err) {
        App.toast(
          err && err.message ? err.message : App.pick("Ошибка оплаты", "Payment error")
        );
      });
  }

  /**
   * Единая точка входа для оплаты картой: выбирает провайдера по
   * App.subscription.card_provider и передаёт ему управление.
   * Промис НИКОГДА не реджектится — об ошибках сообщаем тостом, чтобы вызывающая
   * страница могла спокойно разблокировать кнопку в .then/.finally.
   * @param {string} tariff "monthly" | "yearly" | "lifetime"
   * @returns {Promise}
   */
  App.payCard = function (tariff) {
    var provider =
      (App.subscription && App.subscription.card_provider) || "none";

    if (provider === "cloudpayments") {
      return payCardCloudPayments(tariff);
    }
    if (provider === "yookassa") {
      return payCardYookassa(tariff);
    }

    // Приём карт ещё не подключён: цену и кнопку показываем (витрина с ценой
    // нужна для модерации в платёжном сервисе), но честно об этом говорим.
    App.toast(
      App.pick(
        "Оплата картой подключается. Попробуйте позже.",
        "Card payment is being connected. Please try again later."
      )
    );
    return Promise.resolve();
  };

  /**
   * Опрашивает статус подписки, пока не появится премиум (после оплаты).
   * @param {number} attempts сколько попыток осталось
   * @param {number} delay задержка между попытками, мс
   */
  App._pollPremium = function (attempts, delay) {
    App.refreshSubscription().then(function () {
      if (App.isPremium()) {
        App.toast(App.pick("Подписка активна!", "Subscription is active!"));
        if (App._current) App.navigate(App._current);
        return;
      }
      if (attempts > 1) {
        setTimeout(function () {
          App._pollPremium(attempts - 1, delay);
        }, delay);
      } else {
        // Не дождались — вебхук активирует чуть позже; успокаиваем пользователя.
        App.toast(App.pick(
          "Доступ появится в течение минуты.",
          "Access will appear within a minute."
        ));
        if (App._current) App.navigate(App._current);
      }
    });
  };

  /* =====================================================================
   *  ИНИЦИАЛИЗАЦИЯ
   * ===================================================================== */

  /**
   * Прокидывает безопасные отступы Telegram в CSS-переменные:
   *   --tg-safe-top    — вырез экрана + плавающие кнопки Telegram сверху;
   *   --tg-safe-bottom — нижняя безопасная зона.
   * Критично при открытии из поиска/по ссылке (полноэкранный режим), иначе
   * контент уезжает под верхние элементы Telegram (Close/«…»).
   */
  function applySafeArea() {
    if (!App.tg) return;
    try {
      var sa = App.tg.safeAreaInset || {};
      var csa = App.tg.contentSafeAreaInset || {};
      var top = (Number(sa.top) || 0) + (Number(csa.top) || 0);
      var bottom = (Number(sa.bottom) || 0) + (Number(csa.bottom) || 0);
      var root = document.documentElement.style;
      root.setProperty("--tg-safe-top", top + "px");
      root.setProperty("--tg-safe-bottom", bottom + "px");
    } catch (e) {
      /* безопасные зоны — не критичны, игнорируем сбой */
    }
  }

  /**
   * Применяет тему Telegram к CSS-переменным (если данные доступны).
   * Делается мягко: при отсутствии данных просто используется дизайн по умолчанию.
   */
  function applyTheme() {
    // Светлая пастельная тема: фиксируем схему через <meta name="color-scheme">,
    // чтобы вебвью не переключал элементы формы в тёмный режим. Добавляем тег,
    // если его нет в разметке (index.html может не содержать его).
    try {
      var meta = document.querySelector('meta[name="color-scheme"]');
      if (!meta) {
        meta = document.createElement("meta");
        meta.setAttribute("name", "color-scheme");
        meta.setAttribute("content", "light");
        var head = document.head || document.getElementsByTagName("head")[0];
        if (head) {
          head.appendChild(meta);
        }
      }
    } catch (e) {
      /* color-scheme — не критично */
    }

    if (!App.tg) {
      return;
    }

    // Красим шапку и фон Telegram под пастельный --bg (#FAF7F2), чтобы
    // системная область над мини-приложением совпадала с дизайном.
    try {
      if (typeof App.tg.setHeaderColor === "function") {
        App.tg.setHeaderColor("#FAF7F2");
      }
      if (typeof App.tg.setBackgroundColor === "function") {
        App.tg.setBackgroundColor("#FAF7F2");
      }
    } catch (e) {
      /* цвета Telegram — не критично, игнорируем */
    }

    try {
      // Стабильная высота вьюпорта Telegram для корректной верстки.
      var stable = App.tg.viewportStableHeight;
      if (stable) {
        document.documentElement.style.setProperty(
          "--tg-viewport-stable-height",
          stable + "px"
        );
      }
      // Безопасные зоны Telegram (вырез + плавающие кнопки сверху).
      applySafeArea();

      // Подписка на изменение размеров вьюпорта (клавиатура, разворот) и зон.
      if (typeof App.tg.onEvent === "function") {
        App.tg.onEvent("viewportChanged", function () {
          var h = App.tg.viewportStableHeight;
          if (h) {
            document.documentElement.style.setProperty(
              "--tg-viewport-stable-height",
              h + "px"
            );
          }
          applySafeArea();
        });
        App.tg.onEvent("safeAreaChanged", applySafeArea);
        App.tg.onEvent("contentSafeAreaChanged", applySafeArea);
      }
    } catch (e) {
      // Тема — не критичный функционал, ошибки игнорируем.
      console.warn("Не удалось применить тему Telegram", e);
    }
  }

  /**
   * Определяет стартовый язык интерфейса по приоритету:
   *   1) язык из профиля (сервер) — App.state.profile.language;
   *   2) язык Telegram-пользователя (language_code: "ru*" -> ru, иначе en);
   *   3) "ru" по умолчанию.
   * Результат сохраняется в App.lang.
   */
  function detectLang() {
    // 1) Приоритет — язык из профиля на сервере.
    var profileLang =
      App.state.profile &&
      typeof App.state.profile.language === "string" &&
      App.state.profile.language;
    if (profileLang === "ru" || profileLang === "en") {
      App.lang = profileLang;
      return;
    }

    // 2) Язык Telegram-пользователя.
    var code =
      (App.tg &&
        App.tg.initDataUnsafe &&
        App.tg.initDataUnsafe.user &&
        App.tg.initDataUnsafe.user.language_code) ||
      "";
    if (typeof code === "string" && code) {
      App.lang = code.toLowerCase().indexOf("ru") === 0 ? "ru" : "en";
      return;
    }

    // 3) По умолчанию — русский.
    App.lang = "ru";
  }

  /**
   * Инициализация приложения. Вызывается ОДИН раз из index.html
   * после регистрации всех страниц.
   */
  App.init = function () {
    // Сообщаем Telegram о готовности и разворачиваем окно на весь экран.
    if (App.tg) {
      try {
        if (typeof App.tg.ready === "function") {
          App.tg.ready();
        }
        if (typeof App.tg.expand === "function") {
          App.tg.expand();
        }
        // Отключаем вертикальные свайпы Telegram (Bot API 7.7+): из-за них
        // контент «уезжает» вверх и прокрутка залипает после смены экрана.
        if (typeof App.tg.disableVerticalSwipes === "function") {
          App.tg.disableVerticalSwipes();
        }
      } catch (e) {
        console.warn("Ошибка инициализации Telegram WebApp", e);
      }
    }

    // Обновляем пользователя (на случай, если SDK подгрузился позже).
    App.user =
      (App.tg && App.tg.initDataUnsafe && App.tg.initDataUnsafe.user) || null;

    // Применяем тему/вьюпорт.
    applyTheme();

    // Предварительное определение языка по Telegram (профиля ещё нет).
    // Чтобы UI до загрузки профиля уже был на правильном языке.
    detectLang();
    applyTabLabels();

    // Навешиваем обработчики на кнопки нижней навигации.
    var tabs = document.querySelectorAll("#tabbar .tab");
    for (var i = 0; i < tabs.length; i++) {
      (function (tab) {
        tab.addEventListener("click", function () {
          var page = tab.getAttribute("data-page");
          if (!page) {
            return;
          }
          // Повторный тап по УЖЕ активной центральной кнопке-камере (мы уже
          // находимся на экране сканера) = СПУСК затвора, а не ре-навигация.
          // Первое нажатие открывает сканер (живую камеру), второе — снимает.
          if (page === "scan" && App._current === "scan") {
            App.haptic("medium");
            if (window.PageScan && typeof window.PageScan.capture === "function") {
              try {
                window.PageScan.capture();
              } catch (e) {
                // Сбой спуска не должен ломать навигацию — мягко игнорируем.
                console.error("Ошибка спуска камеры (PageScan.capture)", e);
              }
            }
            return;
          }
          // Обычная навигация: первое нажатие открывает целевую страницу.
          App.haptic("light");
          App.navigate(page);
        });
      })(tabs[i]);
    }

    // Best-effort авторизация: подтверждаем пользователя и кэшируем профиль,
    // ЗАТЕМ загружаем статус подписки и только после этого делаем первую
    // навигацию — gated-страницы должны знать статус подписки при показе.
    // Все шаги fail-safe: ошибки не блокируют запуск, статус подписки при
    // сбое остаётся дефолтным (НЕ премиум) — пользователю покажется paywall.
    App.api
      .verify()
      .then(function (profile) {
        App.state.profile = profile;
      })
      .catch(function (err) {
        console.warn("Авторизация не выполнена: " + err.message);
      })
      .then(function () {
        // Профиль загружен — определяем язык окончательно (профиль > telegram > ru)
        // и обновляем подписи вкладок ДО первой навигации.
        detectLang();
        applyTabLabels();
        // refreshSubscription сам гасит свои ошибки, статус остаётся дефолтным.
        return App.refreshSubscription();
      })
      .then(function () {
        // Стартовая страница — «Сегодня»: один экран отвечает на вопрос
        // «что у меня сейчас» (калории, тренировка дня, вес, серия) и ведёт
        // в нужный раздел. Первый запуск без цели по калориям — мастер
        // онбординга. Камеру на старте не открываем: иначе Telegram сразу
        // спрашивает разрешение, ещё до того как человек понял, что это.
        if (
          !App.state.profile ||
          App.state.profile.daily_goal_kcal == null
        ) {
          App.navigate("onboarding");
        } else {
          App.navigate("today");
        }
      });
  };

  /* =====================================================================
   *  ХЕЛПЕРЫ ДЛЯ СТРАНИЦ
   * ===================================================================== */

  // Таймер автоскрытия тоста.
  var _toastTimer = null;

  /**
   * Показывает короткое всплывающее уведомление (toast).
   * @param {string} msg
   */
  App.toast = function (msg) {
    var el = document.getElementById("toast");
    if (!el) {
      // Запасной вариант, если контейнера тоста нет в разметке.
      try {
        console.log("[toast] " + msg);
      } catch (e) {}
      return;
    }
    el.textContent = msg;
    el.classList.add("show");
    if (_toastTimer) {
      clearTimeout(_toastTimer);
    }
    _toastTimer = setTimeout(function () {
      el.classList.remove("show");
    }, 2800);
  };

  /** Показывает оверлей загрузки (#loading). */
  App.showLoading = function () {
    var el = document.getElementById("loading");
    if (el) {
      el.classList.add("show");
    }
  };

  /** Скрывает оверлей загрузки (#loading). */
  App.hideLoading = function () {
    var el = document.getElementById("loading");
    if (el) {
      el.classList.remove("show");
    }
  };

  /** Надёжно прокручивает страницу в самый верх (для разных вебвью/Telegram). */
  App.scrollTop = function () {
    try {
      window.scrollTo(0, 0);
    } catch (e) {}
    try {
      if (document.scrollingElement) document.scrollingElement.scrollTop = 0;
      if (document.documentElement) document.documentElement.scrollTop = 0;
      if (document.body) document.body.scrollTop = 0;
    } catch (e) {}
  };

  /**
   * Возвращает сегодняшнюю дату в формате "YYYY-MM-DD" по локальному времени.
   * @returns {string}
   */
  App.todayStr = function () {
    var d = new Date();
    var y = d.getFullYear();
    var m = d.getMonth() + 1;
    var day = d.getDate();
    return (
      y +
      "-" +
      (m < 10 ? "0" + m : "" + m) +
      "-" +
      (day < 10 ? "0" + day : "" + day)
    );
  };

  /**
   * Форматирует число: округляет до целого и возвращает строкой.
   * Нечисловые значения превращаются в "0".
   * @param {number} n
   * @returns {string}
   */
  App.fmt = function (n) {
    var num = Number(n);
    if (!isFinite(num)) {
      return "0";
    }
    return String(Math.round(num));
  };

  /**
   * Возвращает локализованную подпись для типа приёма пищи.
   * Локализуется НА МОМЕНТ вызова через App.pick (а не один раз при загрузке).
   * @param {string} type breakfast|lunch|dinner|snack
   * @returns {string}
   */
  App.mealLabel = function (type) {
    switch (type) {
      case "breakfast":
        return App.pick("Завтрак", "Breakfast");
      case "lunch":
        return App.pick("Обед", "Lunch");
      case "dinner":
        return App.pick("Ужин", "Dinner");
      case "snack":
        return App.pick("Перекус", "Snack");
      default:
        return type || "";
    }
  };

  /**
   * Вызывает тактильную отдачу (haptic feedback) через Telegram, если доступно.
   * @param {string} [type] "light"|"medium"|"heavy"|"selection"|"success"|"warning"|"error"
   */
  App.haptic = function (type) {
    if (!App.tg || !App.tg.HapticFeedback) {
      return;
    }
    try {
      var hf = App.tg.HapticFeedback;
      if (type === "success" || type === "warning" || type === "error") {
        hf.notificationOccurred(type);
      } else if (type === "selection") {
        // Тактильный «тик» при переключении (выбор даты, таба и т.п.).
        if (typeof hf.selectionChanged === "function") {
          hf.selectionChanged();
        }
      } else {
        // Лёгкая отдача по умолчанию.
        hf.impactOccurred(type || "light");
      }
    } catch (e) {
      // Тактильная отдача не критична — молча игнорируем.
    }
  };

  /* =====================================================================
   *  МИНИ-КАЛЕНДАРЬ (общий хелпер для дневника и тренировок)
   *
   *  Компактный попап-календарь, встраиваемый в переданный контейнер.
   *  Переиспользует существующие CSS-классы cal-* (не изобретаем новых).
   *  Сетка Пн-первая; будущие даты недоступны; месяц навигации хранится в
   *  containerEl.dataset.calMonth и меняется стрелками ‹ › БЕЗ вызова onPick.
   * ===================================================================== */

  // Названия месяцев для заголовка календаря (RU именительный падеж + EN).
  var CAL_MONTHS_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
  ];
  var CAL_MONTHS_EN = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
  ];
  // Заголовки дней недели (Пн-первый).
  var CAL_DOW_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  var CAL_DOW_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  /**
   * Возвращает "YYYY-MM" месяца для заданной ISO-даты.
   * @param {string} isoDate "YYYY-MM-DD"
   * @returns {string} "YYYY-MM"
   */
  function calMonthOf(isoDate) {
    var parts = String(isoDate).split("-");
    return parts[0] + "-" + parts[1];
  }

  /**
   * Рисует попап мини-календаря для месяца containerEl.dataset.calMonth.
   * @param {HTMLElement} containerEl контейнер попапа
   * @param {string} selectedIso выбранная дата "YYYY-MM-DD"
   * @param {Function} onPick колбэк выбора дня (iso)
   */
  function calRender(containerEl, selectedIso, onPick) {
    var parts = String(containerEl.dataset.calMonth).split("-");
    var year = parseInt(parts[0], 10);
    var month = parseInt(parts[1], 10) - 1; // 0-based
    if (isNaN(year) || isNaN(month)) {
      var now = new Date();
      year = now.getFullYear();
      month = now.getMonth();
      containerEl.dataset.calMonth =
        year + "-" + String(month + 1).padStart(2, "0");
    }

    var todayStr = App.todayStr();
    var todayMonth = calMonthOf(todayStr);
    var viewMonth = containerEl.dataset.calMonth;

    // Заголовок «Месяц ГОД» (RU именительный падеж).
    var title = App.pick(CAL_MONTHS_RU[month], CAL_MONTHS_EN[month]) + " " + year;

    // Следующий месяц целиком в будущем? -> отключаем стрелку «›».
    var nextDisabled = viewMonth >= todayMonth;

    // Заголовки дней недели (Пн-первый).
    var dowHtml = "";
    for (var w = 0; w < 7; w++) {
      dowHtml +=
        '<span class="cal-dow">' +
        App.escapeHtml(App.pick(CAL_DOW_RU[w], CAL_DOW_EN[w])) +
        "</span>";
    }

    // Первый день месяца и число дней в месяце (полдень — защита от сдвига суток).
    var first = new Date(year, month, 1, 12, 0, 0, 0);
    // getDay(): 0=Вс..6=Сб. Приводим к Пн-первому: (getDay()+6)%7.
    var lead = (first.getDay() + 6) % 7;
    var daysInMonth = new Date(year, month + 1, 0, 12, 0, 0, 0).getDate();

    var cells = "";
    // Ведущие пустые ячейки.
    for (var e = 0; e < lead; e++) {
      cells += '<span class="cal-cell cal-cell--empty"></span>';
    }
    // Дни месяца.
    for (var d = 1; d <= daysInMonth; d++) {
      var iso =
        year + "-" +
        String(month + 1).padStart(2, "0") + "-" +
        String(d).padStart(2, "0");
      var cls = "cal-cell";
      var future = iso > todayStr;
      if (future) cls += " cal-cell--disabled";
      if (iso === todayStr) cls += " cal-cell--today";
      if (iso === selectedIso) cls += " cal-cell--selected";
      if (future) {
        cells += '<span class="' + cls + '">' + d + "</span>";
      } else {
        cells +=
          '<button type="button" class="' + cls + '" data-cal-day="' + iso + '">' + d + "</button>";
      }
    }

    containerEl.innerHTML =
      '<div class="cal-pop">' +
      '<div class="cal-head">' +
      '<button type="button" class="cal-nav" data-cal-nav="prev" ' +
      'aria-label="' + App.escapeHtml(App.pick("Предыдущий месяц", "Previous month")) + '">‹</button>' +
      '<span class="cal-title">' + App.escapeHtml(title) + "</span>" +
      '<button type="button" class="cal-nav" data-cal-nav="next"' +
      (nextDisabled ? " disabled" : "") + " " +
      'aria-label="' + App.escapeHtml(App.pick("Следующий месяц", "Next month")) + '">›</button>' +
      "</div>" +
      '<div class="cal-grid">' + dowHtml + cells + "</div>" +
      "</div>";

    // Навигация по месяцам (меняет ТОЛЬКО просматриваемый месяц, не onPick).
    var navs = containerEl.querySelectorAll(".cal-nav");
    for (var n = 0; n < navs.length; n++) {
      navs[n].addEventListener("click", function (ev) {
        var btn = ev.currentTarget;
        if (btn.disabled) return;
        var dir = btn.getAttribute("data-cal-nav");
        App.haptic && App.haptic("selection");
        var mp = String(containerEl.dataset.calMonth).split("-");
        var my = parseInt(mp[0], 10);
        var mm = parseInt(mp[1], 10) - 1;
        var dt = new Date(my, mm + (dir === "next" ? 1 : -1), 1, 12, 0, 0, 0);
        containerEl.dataset.calMonth =
          dt.getFullYear() + "-" + String(dt.getMonth() + 1).padStart(2, "0");
        calRender(containerEl, selectedIso, onPick);
      });
    }

    // Выбор дня: очищаем контейнер и вызываем onPick(iso).
    var grid = containerEl.querySelector(".cal-grid");
    if (grid) {
      grid.addEventListener("click", function (ev) {
        var cell = ev.target.closest(".cal-cell[data-cal-day]");
        if (!cell) return;
        var iso = cell.getAttribute("data-cal-day");
        if (!iso) return;
        App.miniCalendarClose(containerEl);
        if (typeof onPick === "function") {
          onPick(iso);
        }
      });
    }
  }

  /**
   * Переключает мини-календарь в контейнере: если попап уже открыт — закрывает,
   * иначе рисует его для месяца выбранной даты.
   * @param {HTMLElement} containerEl контейнер попапа
   * @param {string} selectedIso выбранная дата "YYYY-MM-DD"
   * @param {Function} onPick колбэк выбора (не-будущего) дня: onPick(iso)
   */
  App.miniCalendarToggle = function (containerEl, selectedIso, onPick) {
    if (!containerEl) return;
    if (containerEl.innerHTML.trim() !== "") {
      App.miniCalendarClose(containerEl);
      return;
    }
    containerEl.dataset.calMonth = calMonthOf(selectedIso);
    calRender(containerEl, selectedIso, onPick);
  };

  /**
   * Закрывает мини-календарь (очищает контейнер).
   * @param {HTMLElement} containerEl
   */
  App.miniCalendarClose = function (containerEl) {
    if (containerEl) containerEl.innerHTML = "";
  };

  /**
   * Экранирует HTML-спецсимволы для безопасной вставки текста в разметку.
   * @param {string} s
   * @returns {string}
   */
  App.escapeHtml = function (s) {
    if (s === null || s === undefined) {
      return "";
    }
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  };

  // Публикуем объект приложения в глобальной области.
  window.App = App;
})();
