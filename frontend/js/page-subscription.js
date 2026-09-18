/*
 * page-subscription.js — страница «Подписка».
 *
 * Регистрирует контроллер через App.registerPage("subscription", {...}).
 * Публичная ссылка — window.PageSubscription.
 *
 * Страница НЕ входит в нижнюю навигацию (#tabbar) — она открывается строкой
 * «Подписка» со страницы «Профиль». Возврат — кнопкой «Назад».
 *
 * ЕДИНЫЙ СПИСОК ВЫГОД. Здесь живёт единственный на всё приложение список
 * того, что даёт подписка (BENEFITS), и он же отдаётся наружу методом
 * controller.benefits(). Страница оплаты и paywall добавок берут его
 * отсюда, а не составляют свой: раньше на витрине было
 * восемь пунктов БЕЗ AI-тренера, а на оплате — другие пять, и человек не мог
 * понять, за что платит. AI-тренер стоит ПЕРВЫМ: это главная функция продукта.
 *
 * Что показывает:
 *   1. ТЕКУЩИЙ СТАТУС подписки (App.subscription):
 *        - премиум  -> «Подписка активна» + «до <дата>» (или «Навсегда»
 *          для lifetime/owner);
 *        - free     -> «Бесплатный доступ».
 *   2. КАРТОЧКИ ТАРИФОВ из App.subscription.tariffs ({days, price, currency}):
 *        Месячный (monthly), 3 месяца (quarterly), Годовой (yearly),
 *        Вечный (lifetime). У тарифов длиннее месяца — «≈ N ₽/мес · экономия
 *        M%» относительно месячной цены (только если выгода действительно есть).
 *        Карточки работают как переключатель (выбран по умолчанию годовой),
 *        а под ними одна кнопка «Оформить за N ₽» -> App.goPayment(tariff),
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
  // Порядок задаёт расположение карточек на странице: от короткого срока к
  // длинному (как и каталог на бэкенде), чтобы «лесенка» цен читалась сверху
  // вниз — каждая следующая ступень дешевле в пересчёте на месяц.
  // Тексты заданы парами [ru, en] и переводятся через pick() в момент рендера.
  // termNote: true — подпись срока строится из days, пришедших с сервера
  // («Доступ на 90 дней»): срок задаётся env и может отличаться от
  // значения по умолчанию, а note — лишь запасной текст, если days нет.
  var TARIFF_META = [
    {
      key: "monthly",
      title: ["Месячный", "Monthly"],
      termNote: true,
      note: ["Доступ на 30 дней", "Access for 30 days"]
    },
    {
      key: "quarterly",
      title: ["3 месяца", "3 months"],
      termNote: true,
      note: ["Доступ на 90 дней", "Access for 90 days"]
    },
    {
      key: "yearly",
      title: ["Годовой", "Yearly"],
      // Срок — как у остальных: о выгоде уже говорят бейдж и «экономия N%»,
      // отдельная рекламная строка только удлиняла карточку.
      termNote: true,
      note: ["Доступ на 365 дней", "Access for 365 days"]
    },
    {
      key: "lifetime",
      title: ["Вечный", "Lifetime"],
      note: ["Один раз — и навсегда", "Pay once — keep forever"],
      badge: ["Навсегда", "Forever"]
    }
  ];

  // ЕДИНЫЙ список того, что даёт подписка. Один и тот же на витрине, на
  // экране оплаты и в paywall — см. комментарий в шапке файла.
  // Каждый пункт — пара [ru, en]; перевод выполняется при рендере.
  var BENEFITS = [
    [
      "AI-тренер: программа под вас и разбор каждой тренировки",
      "AI trainer: a program built for you and a review of every workout"
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
    [
      "Добавки: учёт, напоминания и AI-советы",
      "Supplements: tracking, reminders and AI tips"
    ],
    ["Недельный отчёт о прогрессе", "Weekly progress report"],
    ["Фото-прогресс и трекер цикла", "Photo progress and cycle tracker"]
  ];

  // Три коротких обещания на тёмном блоке вверху экрана. Полный список
  // BENEFITS остаётся ниже, в «Что входит»; здесь — только суть, потому что
  // поверх фотографии длинные строки не читаются.
  var HERO_POINTS = [
    ["AI-тренер и программа под вас", "AI trainer and a program built for you"],
    ["Питание по фото и голосу", "Food by photo and voice"],
    ["Добавки и напоминания", "Supplements and reminders"]
  ];

  // Подпись срока под ценой: «в месяц», «за 3 месяца», «в год».
  var TARIFF_PERIOD = {
    monthly: ["в месяц", "per month"],
    quarterly: ["за 3 месяца", "per 3 months"],
    yearly: ["в год", "per year"],
    lifetime: ["навсегда", "forever"]
  };

  // Внутреннее состояние контроллера (живёт между методами через замыкание).
  var state = {
    viewEl: null, // корневой элемент страницы (#view)
    loading: false, // флаг обновления статуса (защита от гонок)
    // Выбранный тариф: карточки работают как переключатель, а оплата
    // запускается одной кнопкой под ними. Пока человек ничего не выбрал —
    // выделен годовой: он и помечен «Выгодно».
    selected: null
  };

  /* =====================================================================
   *  УТИЛИТЫ
   * ===================================================================== */

  function esc(s) {
    return App.escapeHtml(s == null ? "" : String(s));
  }

  /** Иконка из общего набора (js/icons.js). Возвращает строку <svg …>. */
  function icon(name, opts) {
    return App.icon ? App.icon(name, opts) : "";
  }

  function haptic(kind) {
    if (App && typeof App.haptic === "function") App.haptic(kind);
  }

  /**
   * Значение переменной --hero-img для фото геройского блока.
   * Путь делаем абсолютным: относительный url() внутри custom property Chrome
   * разрешает от адреса style.css, где переменная подставляется, а не от
   * страницы — картинка запрашивалась как css/img/… и уходила в 404.
   * @param {string} file имя файла в frontend/img
   */
  function heroImg(file) {
    return App.heroImg(file);
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
      card_provider: s.card_provider || "none",
      test_payment_price: Number(s.test_payment_price) || 0
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
   * @param {string} key "monthly" | "quarterly" | "yearly" | "lifetime"
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

  /** Срок тарифа в днях с сервера (null — бессрочный или не пришёл). */
  function tariffDays(s, key) {
    var t = s.tariffs && s.tariffs[key];
    var days = t ? Number(t.days) : NaN;
    return isFinite(days) && days > 0 ? days : null;
  }

  /**
   * Считает «экономику» ЛЮБОГО тарифа длиннее месяца относительно месячного:
   *   - months   — сколько месяцев покрывает тариф (days / дней в месяце,
   *                округлено до целых: 90 -> 3, 365 -> 12);
   *   - perMonth — во сколько обходится месяц (price / months);
   *   - savePct  — процент экономии против months × месячная цена.
   * Возвращает null для месячного/бессрочного тарифа, без месячной цены и
   * когда выгоды нет: рекламную строку «экономия» без основания не показываем.
   * Одна функция на все тарифы, чтобы «3 месяца» и год считались одинаково и
   * новый срок не требовал своей копии расчёта.
   * @param {object} s результат sub()
   * @param {string} key ключ тарифа
   */
  function tariffEconomy(s, key) {
    if (key === "monthly") return null;
    var plan = tariffPrice(s, key);
    var monthly = tariffPrice(s, "monthly");
    var days = tariffDays(s, key);
    if (!plan || !monthly || !days) return null;

    // Месяц меряем сроком месячного тарифа с сервера (по умолчанию 30 дней),
    // чтобы расчёт не разошёлся с тем, что реально продаётся как «месяц».
    var monthDays = tariffDays(s, "monthly") || 30;
    var months = Math.round(days / monthDays);
    if (months < 2) return null; // не длиннее месяца — сравнивать не с чем

    var savePct = Math.round((1 - plan.price / (monthly.price * months)) * 100);
    if (!(savePct > 0)) return null; // выгоды нет — не завышаем

    return {
      perMonth: Math.round(plan.price / months),
      currency: plan.currency,
      savePct: savePct
    };
  }

  /**
   * Подпись срока карточки: «Доступ на 90 дней» по days с сервера, иначе —
   * запасной текст из метаданных.
   */
  function tariffNote(s, meta) {
    var days = meta.termNote ? tariffDays(s, meta.key) : null;
    if (!days) return pick(meta.note[0], meta.note[1]);
    return pick(
      "Доступ на " + days + " " + daysWordRu(days),
      "Access for " + days + (days === 1 ? " day" : " days")
    );
  }

  /* =====================================================================
   *  ЕДИНЫЙ СПИСОК ВЫГОД — ОБЩИЙ ДЛЯ ВСЕГО ПРИЛОЖЕНИЯ
   *  Публикуется через window.PageSubscription, чтобы экран оплаты и paywall
   *  показывали ТОТ ЖЕ список, а не составляли собственный.
   * ===================================================================== */

  /**
   * Список выгод подписки на текущем языке (AI-тренер первым).
   * @returns {string[]}
   */
  function benefitsList() {
    return BENEFITS.map(function (b) {
      return pick(b[0], b[1]);
    });
  }

  /* =====================================================================
   *  РАЗМЕТКА
   * ===================================================================== */

  /**
   * Базовый каркас страницы. Внутренние блоки (статус, тарифы) рендерятся
   * отдельно и перерисовываются при обновлении статуса.
   */
  function template() {
    var benefitsHtml = benefitsList()
      .map(function (text) {
        return (
          '<li class="sub-benefit">' +
          '<span class="sub-benefit__check" aria-hidden="true">' +
          icon("check", { size: 18 }) +
          "</span>" +
          '<span class="sub-benefit__text">' +
          esc(text) +
          "</span>" +
          "</li>"
        );
      })
      .join("");

    var heroPoints = HERO_POINTS.map(function (p) {
      return (
        '<li class="sub-hero__point">' +
        icon("check", { size: 18 }) +
        "<span>" + esc(pick(p[0], p[1])) + "</span>" +
        "</li>"
      );
    }).join("");

    var backLabel = pick("Назад", "Back");

    return (
      '<section class="page sub-page">' +
      // ---- Кнопка «Назад» ----
      '<header class="sub-head">' +
      '<button type="button" class="sub-back" id="subBack" aria-label="' +
      esc(backLabel) +
      '">' +
      '<span class="sub-back__arrow" aria-hidden="true">' +
      icon("arrow", { size: 18, rotate: 180 }) +
      "</span>" +
      "<span>" +
      esc(backLabel) +
      "</span>" +
      "</button>" +
      "</header>" +

      // ---- Тёмный блок с фотографией: что покупает человек ----
      '<section class="hero hero--img sub-hero" style="' + heroImg("hero-premium.jpg") + '">' +
      '<span class="eyebrow">' + esc(pick("Fitness Up Premium", "Fitness Up Premium")) + "</span>" +
      '<h1 class="hero__title sub-hero__title">' +
      esc(pick("Тренер, питание, добавки", "Coach, nutrition, supplements")) +
      "</h1>" +
      '<ul class="sub-hero__points">' + heroPoints + "</ul>" +
      "</section>" +

      // ---- Текущий статус (заполняется renderStatus) ----
      '<div class="card sub-status" id="subStatus">' +
      '<div class="skeleton skeleton--block"></div>' +
      "</div>" +

      // ---- Тестовый платёж владельца (заполняется renderTest) ----
      '<section class="card sub-test" id="subTest" hidden></section>' +

      // ---- Тарифы и кнопка оплаты (заполняется renderTariffs) ----
      '<section class="sub-tariffs" id="subTariffs">' +
      '<div class="skeleton skeleton--block"></div>' +
      "</section>" +

      // ---- Оплата через Tribute (показывается при наличии ссылки) ----
      '<div class="sub-tribute" id="subTribute" hidden></div>' +

      // ---- Полный список того, что входит ----
      '<section class="card sub-benefits">' +
      '<span class="eyebrow sub-benefits__eyebrow">' +
      esc(pick("Что входит", "What is included")) +
      "</span>" +
      '<ul class="sub-benefits__list">' +
      benefitsHtml +
      "</ul>" +
      "</section>" +

      '<p class="sub-foot">' +
      esc(
        pick(
          "Оплата банковской картой в рублях. Разовый платёж без автопродления, доступ открывается сразу после оплаты.",
          "Payment by bank card in rubles. One-time payment without auto-renewal, access opens right after payment."
        )
      ) +
      "</p>" +
      // Оферта и политика — те же ссылки, что на экране оплаты; блок пуст,
      // пока сервер не прислал адреса (заполняется renderLegal).
      '<p class="sub-legal" id="subLegal" hidden></p>' +
      "</section>"
    );
  }

  /** Адрес документа, который безопасно открыть снаружи (только http/https). */
  function safeUrl(url) {
    var s = String(url || "").trim();
    return /^https?:\/\//i.test(s) ? s : "";
  }

  /**
   * Ссылки на оферту и политику под витриной. Открываются внешним браузером
   * через Telegram, как на экране оплаты.
   */
  function renderLegal() {
    var box = state.viewEl && state.viewEl.querySelector("#subLegal");
    if (!box) return;
    var legal = (App.subscription && App.subscription.legal) || {};
    var links = [];
    var offer = safeUrl(legal.offer_url);
    var privacy = safeUrl(legal.privacy_url);
    if (offer) {
      links.push(
        '<a class="sub-legal__link" href="#" data-url="' + esc(offer) + '">' +
        esc(pick("Оферта", "Offer")) + "</a>"
      );
    }
    if (privacy) {
      links.push(
        '<a class="sub-legal__link" href="#" data-url="' + esc(privacy) + '">' +
        esc(pick("Политика конфиденциальности", "Privacy policy")) + "</a>"
      );
    }
    if (!links.length) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    box.hidden = false;
    box.innerHTML = links.join('<span class="sub-legal__sep" aria-hidden="true">·</span>');
    var anchors = box.querySelectorAll(".sub-legal__link");
    for (var i = 0; i < anchors.length; i++) {
      anchors[i].addEventListener("click", function (e) {
        e.preventDefault();
        onTribute(this.getAttribute("data-url"));
      });
    }
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
      box.hidden = false;
      box.className = "card sub-status sub-status--premium";
      box.innerHTML =
        '<div class="sub-status__row">' +
        '<div class="sub-status__icon" aria-hidden="true">' +
        icon("check", { size: 24 }) +
        "</div>" +
        '<div class="sub-status__body">' +
        '<div class="sub-status__title">' +
        esc(pick("Подписка активна", "Subscription active")) +
        "</div>" +
        untilLine +
        "</div>" +
        "</div>";
    } else {
      // Не премиум: «истекла» (была платная) или обычный free.
      var expired = s.is_expired;
      var trialOn = s.is_trial_available && s.trial_days > 0;

      // Обычному free без пробного периода карточка статуса не нужна:
      // тёмный блок выше уже говорит, что подписки нет, а повторять
      // «бесплатный доступ» отдельной плашкой — лишний шум перед тарифами.
      if (!expired && !trialOn) {
        box.hidden = true;
        box.innerHTML = "";
        return;
      }

      // Истёкшая подписка — часы (время вышло), пробный период — подарок.
      var statusIcon = icon(expired ? "clock" : "gift", { size: 24 });
      var title = expired
        ? pick("Подписка истекла", "Subscription expired")
        : pick("Пробный период", "Free trial");
      var subtitle = expired
        ? pick("Продлите, чтобы вернуть премиум-доступ.", "Renew to get your premium access back.")
        : pick(
            "Первые " + s.trial_days + " " + daysWordRu(s.trial_days) + " — бесплатно, без карты",
            "First " + s.trial_days + (s.trial_days === 1 ? " day" : " days") + " free, no card needed"
          );

      // Кнопка пробного периода — если доступен (одноразово). Срок уже
      // назван строкой выше, поэтому подпись короткая и помещается в строку.
      var trialHtml = "";
      if (trialOn) {
        trialHtml =
          '<button type="button" class="btn btn--cta btn-block sub-trial" id="subTrial">' +
          esc(pick("Попробовать бесплатно", "Start free trial")) +
          "</button>";
      }

      box.hidden = false;
      box.className = "card sub-status " + (expired ? "sub-status--expired" : "sub-status--trial");
      box.innerHTML =
        '<div class="sub-status__row">' +
        '<div class="sub-status__icon" aria-hidden="true">' + statusIcon + "</div>" +
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

    // Собираем только те тарифы, для которых сервер вернул рублёвую цену.
    var cards = [];
    var available = [];
    TARIFF_META.forEach(function (meta) {
      var priceInfo = tariffPrice(s, meta.key);
      if (!priceInfo) return; // цены нет — тариф не показываем
      available.push(meta.key);
    });

    // Выбранный тариф должен существовать на витрине: если цены поменялись
    // и прежний выбор исчез — берём годовой («Выгодно»), иначе первый.
    if (available.indexOf(state.selected) === -1) {
      state.selected = available.indexOf("yearly") !== -1 ? "yearly" : available[0] || null;
    }

    TARIFF_META.forEach(function (meta) {
      var priceInfo = tariffPrice(s, meta.key);
      if (!priceInfo) return;

      var isYearly = meta.key === "yearly";
      // Подсвечиваем годовой как «самый выгодный» вариант.
      var best = isYearly;
      var on = meta.key === state.selected;

      // Бейдж: для годового — «Выгодно» (best value), иначе — из метаданных.
      var badgeText = best
        ? pick("Выгодно", "Best value")
        : meta.badge
        ? pick(meta.badge[0], meta.badge[1])
        : null;
      var badgeHtml = badgeText
        ? '<span class="sub-tariff__badge">' + esc(badgeText) + "</span>"
        : "";

      // Для тарифов длиннее месяца — «≈ N ₽/мес» и «экономия M%» против
      // месячной цены (только когда выгода есть — см. tariffEconomy).
      var econHtml = "";
      var economy = tariffEconomy(s, meta.key);
      if (economy) {
        var perMonthShown = formatPrice(economy.perMonth, economy.currency);
        econHtml =
          '<div class="sub-tariff__econ">' +
          '<span class="sub-tariff__permonth">' +
          esc(pick("≈ " + perMonthShown + "/мес", "≈ " + perMonthShown + "/mo")) +
          "</span>" +
          '<span class="sub-tariff__save">' +
          esc(pick("экономия " + economy.savePct + "%", "save " + economy.savePct + "%")) +
          "</span>" +
          "</div>";
      }

      var period = TARIFF_PERIOD[meta.key] || TARIFF_PERIOD.monthly;

      // Карточка — переключатель (radio): цена крупно, срок под ней, метка
      // выбора справа. Платёж запускает одна кнопка под всеми карточками.
      cards.push(
        '<button type="button" class="card sub-tariff' +
          (best ? " sub-card--best" : "") +
          (on ? " sub-tariff--on" : "") +
          '" data-tariff="' +
          esc(meta.key) +
          '" role="radio" aria-checked="' + (on ? "true" : "false") + '">' +
          '<span class="sub-tariff__info">' +
          '<span class="sub-tariff__title">' +
          esc(pick(meta.title[0], meta.title[1])) +
          badgeHtml +
          "</span>" +
          '<span class="sub-tariff__note">' +
          esc(tariffNote(s, meta)) +
          "</span>" +
          econHtml +
          "</span>" +
          '<span class="sub-tariff__pricing">' +
          '<span class="num sub-tariff__price">' +
          esc(formatPrice(priceInfo.price, priceInfo.currency)) +
          "</span>" +
          '<span class="sub-tariff__period">' + esc(pick(period[0], period[1])) + "</span>" +
          "</span>" +
          '<span class="sub-tariff__mark" aria-hidden="true">' +
          icon("check", { size: 16 }) +
          "</span>" +
          "</button>"
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

    var chosen = tariffPrice(s, state.selected);
    box.innerHTML =
      '<span class="eyebrow sub-tariffs__eyebrow">' +
      esc(sectionTitle) +
      "</span>" +
      '<div class="sub-tariffs__list" role="radiogroup" aria-label="' + esc(sectionTitle) + '">' +
      cards.join("") +
      "</div>" +
      // Единственная кнопка оплаты: подпись содержит сумму выбранного тарифа,
      // чтобы человек видел, за что платит, ещё до экрана оплаты.
      '<button type="button" class="btn btn--cta btn-block sub-tariff__pay" id="subPay" data-tariff="' +
      esc(state.selected || "") +
      '">' +
      esc(s.is_premium
        ? pick("Продлить за ", "Extend for ")
        : pick("Оформить за ", "Subscribe for ")) +
      esc(chosen ? formatPrice(chosen.price, chosen.currency) : "") +
      "</button>";

    // Карточка — переключатель; кнопка ведёт на отдельную страницу оплаты.
    var cardBtns = box.querySelectorAll(".sub-tariff");
    for (var c = 0; c < cardBtns.length; c++) {
      cardBtns[c].addEventListener("click", onSelectTariff);
    }
    var payBtn = box.querySelector(".sub-tariff__pay");
    if (payBtn) payBtn.addEventListener("click", onChoose);
  }

  /** Выбор карточки тарифа: запоминаем и перерисовываем витрину. */
  function onSelectTariff(e) {
    var key = e && e.currentTarget && e.currentTarget.getAttribute("data-tariff");
    if (!key || key === state.selected) return;
    haptic("selection");
    state.selected = key;
    renderTariffs();
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
  /**
   * Блок «Проверка оплаты» — только владельцу, когда сервер прислал сумму.
   * Ведёт на обычную страницу оплаты с тарифом "test".
   */
  function renderTest() {
    var box = state.viewEl && state.viewEl.querySelector("#subTest");
    if (!box) return;
    var s = sub();
    if (!s.test_payment_price || s.card_provider === "none") {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    var price = formatPrice(s.test_payment_price, s.card_currency);
    box.hidden = false;
    box.innerHTML =
      '<span class="eyebrow">' + esc(pick("Проверка оплаты · видно только вам", "Payment check · only you see this")) + "</span>" +
      '<p class="sub-test__text">' +
      esc(pick(
        "Настоящий платёж на " + price + " через ЮKassa: проверяет оплату, возврат в приложение и уведомление. Подписка не изменится.",
        "A real " + price + " payment via YooKassa: checks payment, return to the app and the notification. Your subscription stays the same."
      )) +
      "</p>" +
      '<button type="button" class="btn btn--ghost btn-block" id="subTestBtn">' +
      esc(pick("Оплатить ", "Pay ")) + esc(price) +
      "</button>";
    var btn = box.querySelector("#subTestBtn");
    if (btn) {
      btn.addEventListener("click", function () {
        haptic("light");
        App.goPayment("test");
      });
    }
  }

  function renderAll() {
    renderStatus();
    renderTest();
    renderTariffs();
    renderTribute();
    renderLegal();
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
      state.selected = null;
    },

    // ---- Общее достояние: единый список выгод ----
    // Его берут экран оплаты (page-payment.js) и paywall добавок
    // (page-supplements.js). Функция, а не массив: язык меняется,
    // и значение должно считаться на момент вызова.
    benefits: benefitsList
  };

  // Регистрируем страницу и публикуем контроллер (для отладки/повторного входа).
  window.PageSubscription = controller;
  App.registerPage("subscription", controller);
})();
