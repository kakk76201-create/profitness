/*
 * page-subscription.js — страница «Подписка» (💎).
 *
 * Регистрирует контроллер через App.registerPage("subscription", {...}).
 * Публичная ссылка — window.PageSubscription.
 *
 * Страница НЕ входит в нижнюю навигацию (#tabbar) — она открывается кнопкой
 * «💎 Подписка» со страницы «Аккаунт». Возврат — кнопкой «Назад» в account.
 *
 * Что показывает:
 *   1. ТЕКУЩИЙ СТАТУС подписки (App.subscription):
 *        - премиум  -> «Подписка активна» + «до <дата>» (или «Навсегда»
 *          для lifetime/owner);
 *        - free     -> «Бесплатный доступ».
 *   2. КАРТОЧКИ ТАРИФОВ из App.subscription.tariffs ({days, price, currency}):
 *        Месячный (monthly), Годовой (yearly), Вечный (lifetime).
 *        У каждого — цена в рублях и кнопка «Выбрать» -> App.goPayment(tariff),
 *        которая открывает отдельную страницу оплаты ("payment"). Сама оплата
 *        здесь НЕ запускается: страница подписки — это витрина.
 *   3. Если задан App.subscription.tribute_url — кнопка «Оплатить через Tribute»
 *        -> (App.tg.openLink || window.open)(tribute_url).
 *
 * Контроль доступа — на сервере (платные роуты отдают 402). Эта страница лишь
 * показывает тарифы и текущий статус. При показе и после оплаты статус
 * обновляется через App.refreshSubscription().
 *
 * Локализация RU/EN: все пользовательские строки обёрнуты в App.pick(ru, en)
 * и вычисляются НА МОМЕНТ РЕНДЕРА, чтобы смена языка давала корректный текст.
 * Классы — с префиксом sub-.
 */
(function () {
  "use strict";

  // Локализация: возвращает строку на текущем языке. Хелпер App.pick задаётся
  // в app.js; здесь — безопасный фолбэк (русский), если он ещё не определён.
  function pick(ru, en) {
    if (App && typeof App.pick === "function") return App.pick(ru, en);
    return ru;
  }

  // Описание тарифов: ключ для бэкенда -> метаданные для отображения.
  // Порядок задаёт расположение карточек на странице.
  // Тексты заданы парами [ru, en] и переводятся через pick() в момент рендера.
  var TARIFF_META = [
    {
      key: "monthly",
      title: ["Месячный", "Monthly"],
      icon: "📅",
      note: ["Доступ на 30 дней", "Access for 30 days"]
    },
    {
      key: "yearly",
      title: ["Годовой", "Yearly"],
      icon: "🗓️",
      note: ["Выгоднее на длинной дистанции", "Better value over time"]
      // Бейдж «Выгодно»/«Best value» и подсветка sub-card--best добавляются
      // динамически в renderTariffs (годовой всегда самый выгодный вариант).
    },
    {
      key: "lifetime",
      title: ["Вечный", "Lifetime"],
      icon: "♾️",
      note: ["Один раз — и навсегда", "Pay once — keep forever"],
      badge: ["Навсегда", "Forever"]
    }
  ];

  // Преимущества подписки — общий список ценности (показываем на странице).
  // Каждый пункт — пара [ru, en]; перевод выполняется при рендере.
  var BENEFITS = [
    ["Журнал тренировок и расход калорий", "Workout log and calorie burn"],
    [
      "Учёт добавок, напоминания и AI-советы",
      "Supplement tracking, reminders and AI tips"
    ],
    [
      "Распознавание еды по фото и голосу без лимита",
      "Unlimited food recognition by photo and voice"
    ],
    [
      "Вес и адаптивные калории под ваш прогресс",
      "Weight tracking and adaptive calories for your progress"
    ],
    [
      "Планировщик меню и AI «Что съесть?»",
      "Meal planner and AI “What to eat?”"
    ],
    ["Недельный отчёт о прогрессе", "Weekly progress report"],
    ["Трекер цикла", "Cycle tracker"],
    ["Фото-прогресс", "Photo progress"]
  ];

  // Внутреннее состояние контроллера (живёт между методами через замыкание).
  var state = {
    viewEl: null, // корневой элемент страницы (#view)
    loading: false // флаг обновления статуса (защита от гонок)
  };

  /* =====================================================================
   *  УТИЛИТЫ
   * ===================================================================== */

  function esc(s) {
    return App.escapeHtml(s == null ? "" : String(s));
  }

  function haptic(kind) {
    if (App && typeof App.haptic === "function") App.haptic(kind);
  }

  function toast(msg) {
    if (App && typeof App.toast === "function") App.toast(msg);
  }

  /**
   * Возвращает текущий объект статуса подписки с безопасными значениями
   * по умолчанию (fail-safe: при отсутствии данных считаем пользователя free).
   */
  function sub() {
    var s = App.subscription || {};
    return {
      subscription_type: s.subscription_type || "free",
      subscription_until: s.subscription_until || null,
      is_premium: !!s.is_premium,
      is_owner: !!s.is_owner,
      tariffs: s.tariffs || {},
      tribute_url: s.tribute_url || null,
      is_trial_available: !!s.is_trial_available,
      trial_days: Number(s.trial_days) || 0,
      is_expired: !!s.is_expired,
      // Оплата картой в рублях — единственный способ оплаты в приложении.
      card_enabled: !!s.card_enabled,
      card_currency: s.card_currency || "RUB",
      card_prices: s.card_prices || {},
      card_provider: s.card_provider || "none"
    };
  }

  /**
   * Склонение слова «день» по числу (для русского текста):
   * 1 день, 2–4 дня, 5–20 дней, 21 день и т. д.
   * @param {number} n
   * @returns {string}
   */
  function daysWordRu(n) {
    var num = Math.abs(Number(n)) || 0;
    var tail100 = num % 100;
    if (tail100 >= 11 && tail100 <= 14) return "дней";
    var tail10 = num % 10;
    if (tail10 === 1) return "день";
    if (tail10 >= 2 && tail10 <= 4) return "дня";
    return "дней";
  }

  /**
   * Преобразует дату от сервера в человекочитаемый формат. В русском —
   * «ДД.ММ.ГГГГ», в английском — локальный формат «Mon D, YYYY».
   * Принимает ISO-строку или «YYYY-MM-DD …»; при неудаче возвращает исходную
   * строку как есть.
   */
  function formatUntil(raw) {
    if (!raw) return "";
    var str = String(raw);
    var enLang = App && App.lang === "en";
    // Пытаемся распарсить как полноценную дату.
    var d = new Date(str);
    if (!isNaN(d.getTime())) {
      if (enLang) {
        try {
          return d.toLocaleDateString("en-US", {
            year: "numeric",
            month: "short",
            day: "numeric"
          });
        } catch (e) {
          // Фолбэк ниже на ручной разбор.
        }
      }
      var day = d.getDate();
      var mon = d.getMonth() + 1;
      var year = d.getFullYear();
      return (
        (day < 10 ? "0" + day : "" + day) +
        "." +
        (mon < 10 ? "0" + mon : "" + mon) +
        "." +
        year
      );
    }
    // Фолбэк: «YYYY-MM-DD…» -> «DD.MM.YYYY».
    var datePart = str.split("T")[0].split(" ")[0];
    var parts = datePart.split("-");
    if (parts.length === 3) {
      return parts[2] + "." + parts[1] + "." + parts[0];
    }
    return str;
  }

  /**
   * Безопасно достаёт цену тарифа: {price:number, currency:string} или null,
   * если рублёвой цены нет (такой тариф на витрине не показываем).
   * Основной источник — s.tariffs[key] ({days, price, currency}); фолбэк —
   * s.card_prices[key] (та же рублёвая витрина в плоском виде).
   * @param {object} s результат sub()
   * @param {string} key "monthly" | "yearly" | "lifetime"
   */
  function tariffPrice(s, key) {
    var t = s.tariffs && s.tariffs[key];
    var price = t ? Number(t.price) : NaN;
    var currency = (t && t.currency) || s.card_currency || "RUB";

    if (!isFinite(price) || price <= 0) {
      price = Number(s.card_prices && s.card_prices[key]);
      currency = s.card_currency || "RUB";
    }
    if (!isFinite(price) || price <= 0) return null;
    return { price: price, currency: currency };
  }

  /**
   * Форматирует сумму для показа: целые — без дробной части («499 ₽»),
   * иначе два знака («499.50 ₽»). Для RUB — символ ₽, иначе код валюты.
   * @param {number} price
   * @param {string} currency
   * @returns {string}
   */
  function formatPrice(price, currency) {
    var n = Number(price);
    if (!isFinite(n)) return "";
    var shown = n % 1 === 0 ? String(n) : n.toFixed(2);
    var cur = currency || "RUB";
    return shown + " " + (cur === "RUB" ? "₽" : cur);
  }

  /**
   * Считает «экономику» годового тарифа относительно месячного:
   *   - perMonth — во сколько за месяц обходится годовой (yearly.price / 12);
   *   - savePct  — процент экономии против 12× месячных.
   * Возвращает null, если данных недостаточно или экономии нет
   * (тогда никакой рекламной подписи не показываем — честно).
   * @param {object} s результат sub()
   */
  function yearlyEconomy(s) {
    var yearly = tariffPrice(s, "yearly");
    var monthly = tariffPrice(s, "monthly");
    if (!yearly) return null;

    var perMonth = Math.round(yearly.price / 12);
    var savePct = null;
    if (monthly) {
      var fullYear = monthly.price * 12;
      if (fullYear > 0) {
        savePct = Math.round((1 - yearly.price / fullYear) * 100);
        if (savePct <= 0) savePct = null; // экономии нет — не завышаем
      }
    }
    return { perMonth: perMonth, currency: yearly.currency, savePct: savePct };
  }

  /* =====================================================================
   *  РАЗМЕТКА
   * ===================================================================== */

  /**
   * Базовый каркас страницы. Внутренние блоки (статус, тарифы) рендерятся
   * отдельно и перерисовываются при обновлении статуса.
   */
  function template() {
    var benefitsHtml = BENEFITS.map(function (b) {
      return (
        '<li class="sub-benefit">' +
        '<span class="sub-benefit__check" aria-hidden="true">✓</span>' +
        '<span class="sub-benefit__text">' +
        esc(pick(b[0], b[1])) +
        "</span>" +
        "</li>"
      );
    }).join("");

    var backLabel = pick("Назад", "Back");

    return (
      '<section class="page sub-page">' +
      // ---- Шапка с кнопкой «Назад» ----
      '<header class="sub-head">' +
      '<button type="button" class="sub-back" id="subBack" aria-label="' +
      esc(backLabel) +
      '">' +
      '<span class="sub-back__arrow" aria-hidden="true">←</span>' +
      "<span>" +
      esc(backLabel) +
      "</span>" +
      "</button>" +
      '<h1 class="page-title sub-title">💎 ' +
      esc(pick("Подписка", "Subscription")) +
      "</h1>" +
      '<p class="page-subtitle sub-subtitle">' +
      esc(
        pick(
          "Премиум-доступ ко всем возможностям трекера.",
          "Premium access to every feature of the tracker."
        )
      ) +
      "</p>" +
      "</header>" +

      // ---- Карточка текущего статуса (заполняется renderStatus) ----
      '<div class="card sub-status" id="subStatus">' +
      '<div class="skeleton skeleton--block"></div>' +
      "</div>" +

      // ---- Список преимуществ ----
      '<section class="card sub-benefits">' +
      '<h2 class="sub-section-title">' +
      esc(pick("Что входит в подписку", "What is included")) +
      "</h2>" +
      '<ul class="sub-benefits__list">' +
      benefitsHtml +
      "</ul>" +
      "</section>" +

      // ---- Тарифы (заполняется renderTariffs) ----
      '<section class="sub-tariffs" id="subTariffs">' +
      '<div class="skeleton skeleton--block"></div>' +
      "</section>" +

      // ---- Оплата через Tribute (показывается при наличии ссылки) ----
      '<div class="sub-tribute" id="subTribute" hidden></div>' +

      '<p class="sub-foot">' +
      esc(
        pick(
          "Оплата банковской картой в рублях. Разовый платёж без автопродления, доступ открывается сразу после оплаты.",
          "Payment by bank card in rubles. One-time payment without auto-renewal, access opens right after payment."
        )
      ) +
      "</p>" +
      "</section>"
    );
  }

  /**
   * Отрисовывает карточку текущего статуса подписки.
   */
  function renderStatus() {
    var box = state.viewEl && state.viewEl.querySelector("#subStatus");
    if (!box) return;

    var s = sub();

    if (s.is_premium) {
      // Премиум активен. Для owner / lifetime — «Навсегда», иначе «до <дата>».
      var forever =
        s.is_owner || s.subscription_type === "lifetime" || !s.subscription_until;
      var untilLine;
      if (forever) {
        untilLine =
          '<div class="sub-status__until">' +
          esc(pick("Навсегда", "Forever")) +
          "</div>";
      } else {
        untilLine =
          '<div class="sub-status__until">' +
          esc(pick("до ", "until ")) +
          esc(formatUntil(s.subscription_until)) +
          "</div>";
      }
      box.className = "card sub-status sub-status--premium";
      box.innerHTML =
        '<div class="sub-status__icon" aria-hidden="true">✅</div>' +
        '<div class="sub-status__body">' +
        '<div class="sub-status__title">' +
        esc(pick("Подписка активна", "Subscription active")) +
        "</div>" +
        untilLine +
        "</div>";
    } else {
      // Не премиум: «истекла» (была платная) или обычный free.
      var expired = s.is_expired;
      var icon = expired ? "⏳" : "🔓";
      var title = expired
        ? pick("Подписка истекла", "Subscription expired")
        : pick("Бесплатный доступ", "Free access");
      var subtitle = expired
        ? pick("Продлите, чтобы вернуть премиум-доступ.", "Renew to get your premium access back.")
        : pick("Оформите подписку, чтобы открыть все возможности", "Subscribe to unlock every feature");

      // Кнопка пробного периода — если доступен (одноразово).
      var trialHtml = "";
      if (s.is_trial_available && s.trial_days > 0) {
        trialHtml =
          '<button type="button" class="btn btn--cta btn-block sub-trial" id="subTrial">' +
          esc(pick(
            "🎁 Попробовать " + s.trial_days + " " + daysWordRu(s.trial_days) + " бесплатно",
            "🎁 Try " + s.trial_days + (s.trial_days === 1 ? " day" : " days") + " free"
          )) +
          "</button>";
      }

      box.className = "card sub-status " + (expired ? "sub-status--expired" : "sub-status--free");
      box.innerHTML =
        '<div class="sub-status__row">' +
        '<div class="sub-status__icon" aria-hidden="true">' + icon + "</div>" +
        '<div class="sub-status__body">' +
        '<div class="sub-status__title">' + esc(title) + "</div>" +
        '<div class="sub-status__until">' + esc(subtitle) + "</div>" +
        "</div>" +
        "</div>" +
        trialHtml;

      var trialBtn = box.querySelector("#subTrial");
      if (trialBtn) {
        trialBtn.addEventListener("click", onTrial);
      }
    }
  }

  /**
   * Активирует одноразовый бесплатный пробный период.
   */
  function onTrial(e) {
    var btn = e && e.currentTarget;
    haptic("light");
    if (!(App.api && App.api.startTrial)) return;
    if (btn) btn.disabled = true;
    App.showLoading();
    App.api
      .startTrial()
      .then(function (status) {
        if (status && typeof status === "object") {
          App.subscription = status;
        }
        haptic("success");
        toast(pick("Пробный период активирован!", "Free trial activated!"));
        renderAll();
      })
      .catch(function (err) {
        haptic("error");
        toast((err && err.message) ? err.message : pick("Не удалось активировать пробный период", "Could not activate trial"));
        if (btn) btn.disabled = false;
      })
      .finally(function () {
        App.hideLoading();
      });
  }

  /**
   * Отрисовывает карточки тарифов из App.subscription.tariffs.
   */
  function renderTariffs() {
    var box = state.viewEl && state.viewEl.querySelector("#subTariffs");
    if (!box) return;

    var s = sub();

    // «Экономика» годового тарифа (₽/мес и процент экономии) — для рекламных
    // подписей и подсветки самой выгодной карточки.
    var economy = yearlyEconomy(s);

    // Собираем только те тарифы, для которых сервер вернул рублёвую цену.
    var cards = [];
    TARIFF_META.forEach(function (meta) {
      var priceInfo = tariffPrice(s, meta.key);
      if (!priceInfo) return; // цены нет — тариф не показываем

      var isYearly = meta.key === "yearly";
      // Подсвечиваем годовой как «самый выгодный» вариант.
      var best = isYearly;

      // Бейдж: для годового — «Выгодно» (best value), иначе — из метаданных.
      var badgeText = best
        ? pick("Выгодно", "Best value")
        : meta.badge
        ? pick(meta.badge[0], meta.badge[1])
        : null;
      var badgeHtml = badgeText
        ? '<span class="sub-tariff__badge">' + esc(badgeText) + "</span>"
        : "";

      // Для годового тарифа — подпись «≈ N ₽/мес» и «экономия M%».
      var econHtml = "";
      if (isYearly && economy) {
        var perMonthShown = formatPrice(economy.perMonth, economy.currency);
        var perMonthLine =
          '<span class="sub-tariff__permonth">' +
          esc(
            pick("≈ " + perMonthShown + "/мес", "≈ " + perMonthShown + "/mo")
          ) +
          "</span>";
        var saveLine =
          economy.savePct != null
            ? '<span class="sub-tariff__save">' +
              esc(
                pick(
                  "экономия " + economy.savePct + "%",
                  "save " + economy.savePct + "%"
                )
              ) +
              "</span>"
            : "";
        econHtml =
          '<div class="sub-tariff__econ">' + perMonthLine + saveLine + "</div>";
      }

      cards.push(
        '<article class="card sub-tariff' +
          (best ? " sub-card--best" : "") +
          '" data-tariff="' +
          esc(meta.key) +
          '">' +
          '<div class="sub-tariff__head">' +
          '<span class="sub-tariff__icon" aria-hidden="true">' +
          esc(meta.icon) +
          "</span>" +
          '<div class="sub-tariff__info">' +
          '<div class="sub-tariff__title">' +
          esc(pick(meta.title[0], meta.title[1])) +
          badgeHtml +
          "</div>" +
          '<div class="sub-tariff__note">' +
          esc(pick(meta.note[0], meta.note[1])) +
          "</div>" +
          econHtml +
          "</div>" +
          '<div class="sub-tariff__price">' +
          esc(formatPrice(priceInfo.price, priceInfo.currency)) +
          "</div>" +
          "</div>" +
          '<button type="button" class="btn btn--cta sub-tariff__pay" data-tariff="' +
          esc(meta.key) +
          '">' +
          esc(pick("Выбрать", "Choose")) +
          "</button>" +
          "</article>"
      );
    });

    if (!cards.length) {
      // Тарифы не пришли — мягко сообщаем и предлагаем повторить.
      box.innerHTML =
        '<div class="card sub-tariffs__empty">' +
        "<p>" +
        esc(pick("Не удалось загрузить тарифы.", "Could not load plans.")) +
        "</p>" +
        '<button type="button" class="btn btn--ghost" id="subTariffsRetry">' +
        esc(pick("Повторить", "Retry")) +
        "</button>" +
        "</div>";
      var retry = box.querySelector("#subTariffsRetry");
      if (retry) {
        retry.addEventListener("click", function () {
          refreshStatus();
        });
      }
      return;
    }

    // Для премиум-пользователя это уже не «выбор», а продление: дни складываются.
    var sectionTitle = s.is_premium
      ? pick("Продлить", "Extend")
      : pick("Тарифы", "Plans");

    box.innerHTML =
      '<h2 class="sub-section-title">' +
      esc(sectionTitle) +
      "</h2>" +
      cards.join("");

    // Кнопка «Выбрать» ведёт на отдельную страницу оплаты.
    var payBtns = box.querySelectorAll(".sub-tariff__pay");
    for (var i = 0; i < payBtns.length; i++) {
      payBtns[i].addEventListener("click", onChoose);
    }
  }

  /**
   * Показывает/прячет блок оплаты через Tribute в зависимости от tribute_url.
   */
  function renderTribute() {
    var box = state.viewEl && state.viewEl.querySelector("#subTribute");
    if (!box) return;

    var s = sub();
    if (!s.tribute_url) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }

    box.hidden = false;
    box.innerHTML =
      '<button type="button" class="btn btn--ghost sub-tribute__btn" id="subTributeBtn">' +
      esc(pick("Оплатить через Tribute", "Pay via Tribute")) +
      "</button>" +
      '<p class="sub-tribute__hint">' +
      esc(
        pick(
          "Альтернативный способ оплаты во внешнем сервисе.",
          "Alternative payment via an external service."
        )
      ) +
      "</p>";

    var btn = box.querySelector("#subTributeBtn");
    if (btn) {
      btn.addEventListener("click", function () {
        onTribute(s.tribute_url);
      });
    }
  }

  /**
   * Перерисовывает все динамические блоки страницы по текущему App.subscription.
   */
  function renderAll() {
    renderStatus();
    renderTariffs();
    renderTribute();
  }

  /* =====================================================================
   *  ДЕЙСТВИЯ
   * ===================================================================== */

  /**
   * Обработчик кнопки «Выбрать» — открывает отдельную страницу оплаты
   * ("payment") для выбранного тарифа. Сама оплата запускается уже там.
   */
  function onChoose(e) {
    var btn = e && e.currentTarget;
    var tariff = btn && btn.getAttribute("data-tariff");
    if (!tariff) return;

    haptic("light");

    if (typeof App.goPayment !== "function") {
      // Контракт гарантирует наличие App.goPayment; на всякий случай — фолбэк.
      toast(pick("Оплата временно недоступна", "Payment is temporarily unavailable"));
      return;
    }
    App.goPayment(tariff);
  }

  /**
   * Открывает ссылку оплаты Tribute во внешнем браузере (через Telegram, если
   * доступно, иначе обычным window.open).
   */
  function onTribute(url) {
    if (!url) return;
    haptic("light");
    try {
      if (App.tg && typeof App.tg.openLink === "function") {
        App.tg.openLink(url);
      } else if (typeof window.open === "function") {
        window.open(url, "_blank");
      } else {
        toast(pick("Ссылка для оплаты недоступна", "Payment link is unavailable"));
      }
    } catch (err) {
      toast(pick("Не удалось открыть оплату", "Could not open payment"));
    }
  }

  /**
   * Обновляет статус подписки с сервера и перерисовывает страницу.
   * Best-effort: при ошибке оставляем текущие данные и показываем их.
   */
  function refreshStatus() {
    if (state.loading) return;
    state.loading = true;

    var done = function () {
      state.loading = false;
      // Страница могла смениться, пока шёл запрос — проверяем актуальность.
      if (state.viewEl && document.body.contains(state.viewEl)) {
        renderAll();
      }
    };

    if (App.refreshSubscription) {
      Promise.resolve(App.refreshSubscription()).then(done, done);
    } else {
      // Контракт гарантирует App.refreshSubscription; фолбэк — просто рендерим.
      done();
    }
  }

  /* =====================================================================
   *  КОНТРОЛЛЕР СТРАНИЦЫ
   * ===================================================================== */

  var controller = {
    /**
     * Показ страницы: строит разметку, вешает обработчики, рисует текущий
     * статус из кэша и обновляет его с сервера.
     */
    onShow: function (viewEl) {
      state.viewEl = viewEl;
      viewEl.innerHTML = template();

      App.scrollTop();

      // Кнопка «Назад» -> возврат на страницу-источник (App.state.subOrigin),
      // откуда открыли подписку/пейвол. Фолбэк — «account». Это позволяет,
      // например, вернуться в «Тренировки», если пейвол открыли оттуда.
      var back = viewEl.querySelector("#subBack");
      if (back) {
        back.addEventListener("click", function () {
          haptic("light");
          var origin =
            (App.state && App.state.subOrigin) || "account";
          App.navigate(origin);
        });
      }

      // Сначала рисуем по кэшу (мгновенный отклик), затем обновляем с сервера.
      renderAll();
      refreshStatus();
    },

    /**
     * Уход со страницы — освобождаем ссылки.
     */
    onHide: function () {
      state.viewEl = null;
      state.loading = false;
    }
  };

  // Регистрируем страницу и публикуем контроллер (для отладки/повторного входа).
  window.PageSubscription = controller;
  App.registerPage("subscription", controller);
})();
