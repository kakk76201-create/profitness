/*
 * page-payment.js — страница «Оплата».
 *
 * Регистрирует контроллер через App.registerPage("payment", {...}).
 * Публичная ссылка — window.PagePayment.
 *
 * Страница НЕ входит в нижнюю навигацию (#tabbar): её открывает страница
 * «Подписка» кнопкой «Выбрать» у тарифа через App.goPayment(tariff), которая
 * кладёт ключ тарифа в App.state.paymentTariff и переходит сюда.
 *
 * Что показывает (сверху вниз):
 *   1. Шапка с кнопкой «Назад» -> App.navigate("subscription").
 *   2. Карточку выбранного тарифа: название, срок, цена в рублях,
 *      для тарифов длиннее месяца («3 месяца», год) — «≈ N ₽/мес · экономия
 *      M%» против месячной цены, честная строка про разовый платёж без
 *      автопродления и заметку для действующего премиума.
 *   3. Что входит в подписку — ЕДИНЫЙ список выгод (window.PageSubscription
 *      .benefits()), тот же, что на витрине подписки: раньше здесь были свои
 *      пять пунктов, а на витрине — другие восемь, и среди них не было
 *      AI-тренера.
 *   4. Способ оплаты — банковская карта; текст зависит от провайдера
 *      (cloudpayments / yookassa / none).
 *   5. Итог и кнопку «Оплатить N ₽» -> App.payCard(tariff).
 *   6. Юридический подвал: продавец, ИНН, поддержка, оферта и политика.
 *
 * Оплату проводит App.payCard: он сам выбирает провайдера по
 * App.subscription.card_provider. Доступ активирует ВЕБХУК на бэкенде —
 * фронт лишь показывает статус и опрашивает его (App._pollPremium внутри
 * App.payCard). Единственная валюта — рубли; оплата звёздами убрана.
 *
 * Данные берём из App.subscription (кэш GET /subscription/status):
 *   tariffs[key] = {days, price, currency}, card_prices, card_provider,
 *   card_currency, is_premium, is_owner, subscription_type,
 *   subscription_until, legal{seller, inn, contact, offer_url, privacy_url}.
 * При показе рисуем по кэшу (мгновенный отклик), затем обновляем статус
 * через App.refreshSubscription() и перерисовываем.
 *
 * Локализация RU/EN: все пользовательские строки — через App.pick(ru, en),
 * вычисляются НА МОМЕНТ РЕНДЕРА. Классы — с префиксом pay-.
 */
(function () {
  "use strict";

  /* =====================================================================
   *  УТИЛИТЫ
   * ===================================================================== */

  // Локализация: строка на текущем языке. Фолбэк — русский (если app.js
  // почему-то ещё не задал App.pick).
  function pick(ru, en) {
    if (window.App && typeof App.pick === "function") return App.pick(ru, en);
    return ru;
  }

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

  function toast(msg) {
    if (App && typeof App.toast === "function") App.toast(msg);
  }

  // Ключи тарифов, которые умеет показывать страница (порядок — как в подписке).
  // Тариф не из списка страница не откроет (вернёт к витрине), поэтому новый
  // тариф с бэкенда нужно добавить и сюда, и в TARIFF_META.
  var TARIFF_KEYS = ["monthly", "quarterly", "yearly", "lifetime"];

  // Оформление тарифов: название и срок «по умолчанию» (если сервер
  // не прислал days). Тексты — парами [ru, en], перевод при рендере.
  var TARIFF_META = {
    monthly: {
      title: ["Месячный", "Monthly"],
      term: ["30 дней доступа", "30 days of access"]
    },
    quarterly: {
      title: ["3 месяца", "3 months"],
      term: ["90 дней доступа", "90 days of access"]
    },
    yearly: {
      title: ["Годовой", "Yearly"],
      term: ["365 дней доступа", "365 days of access"]
    },
    lifetime: {
      title: ["Вечный", "Lifetime"],
      term: ["Навсегда", "Forever"]
    }
  };

  // Что входит в подписку. Единственный источник — страница подписки
  // (window.PageSubscription.benefits): список выгод должен быть ОДИН, иначе
  // человек видит на витрине одно, а на оплате другое и не понимает, за что
  // платит. Локальный список — аварийный фолбэк, если скрипт витрины почему-то
  // не загрузился; AI-тренер и в нём стоит первым.
  var INCLUDES_FALLBACK = [
    [
      "AI-тренер: программа под вас и разбор каждой тренировки",
      "AI trainer: a program built for you and a review of every workout"
    ],
    ["Фото и голос без лимита", "Unlimited photo and voice input"],
    ["Добавки и напоминания", "Supplements and reminders"],
    ["Планировщик меню и «Что съесть?»", "Meal planner and “What to eat?”"],
    ["Недельный отчёт и прогресс", "Weekly report and progress"]
  ];

  /**
   * Список выгод подписки на текущем языке (единый для всего приложения).
   * @returns {string[]}
   */
  function includesList() {
    var PS = window.PageSubscription;
    if (PS && typeof PS.benefits === "function") {
      var list = PS.benefits();
      if (list && list.length) return list;
    }
    return INCLUDES_FALLBACK.map(function (it) {
      return pick(it[0], it[1]);
    });
  }

  // Сколько миллисекунд после нажатия «Оплатить» страница готова показать
  // экран «Подписка активна». Окно нужно, потому что доступ выдаёт ВЕБХУК:
  // App.payCard резолвится раньше активации, а App._pollPremium дожидается её
  // и переоткрывает текущую страницу (onHide + onShow). Поэтому факт оплаты
  // определяем НЕ флагом в момент резолва, а сравнением статуса с тем, что был
  // до нажатия — и делаем это при каждом рендере, пока окно не истекло.
  var PAID_WINDOW_MS = 60000;

  // Внутреннее состояние контроллера (живёт между методами через замыкание).
  var state = {
    viewEl: null, // корневой элемент страницы (#view)
    tariff: null, // ключ оплачиваемого тарифа
    loading: false, // идёт обновление статуса (защита от гонок)
    submitting: false, // идёт оплата (защита от двойного нажатия)
    payAt: 0, // время нажатия «Оплатить» (мс), 0 — попытки не было
    payTariff: null, // какой тариф оплачивали
    payBefore: null // снимок статуса подписки до оплаты (для сравнения)
  };

  /** Забывает попытку оплаты (уход со страницы по кнопке, новый тариф). */
  function clearPayAttempt() {
    state.payAt = 0;
    state.payTariff = null;
    state.payBefore = null;
  }

  /**
   * Текущий статус подписки с безопасными значениями по умолчанию
   * (fail-safe: без данных считаем пользователя free, оплату — неподключённой).
   */
  function sub() {
    var s = (window.App && App.subscription) || {};
    var legal = s.legal || {};
    return {
      subscription_type: s.subscription_type || "free",
      subscription_until: s.subscription_until || null,
      is_premium: !!s.is_premium,
      is_owner: !!s.is_owner,
      tariffs: s.tariffs || {},
      card_enabled: !!s.card_enabled,
      card_currency: s.card_currency || "RUB",
      card_prices: s.card_prices || {},
      card_provider: s.card_provider || "none",
      // Чек по 54-ФЗ: нужен ли e-mail и что уже сохранено в профиле.
      receipt_email_required: !!s.receipt_email_required,
      email: s.email || "",
      legal: {
        seller: legal.seller || null,
        inn: legal.inn || null,
        contact: legal.contact || null,
        offer_url: legal.offer_url || null,
        privacy_url: legal.privacy_url || null
      }
    };
  }

  /**
   * Преобразует дату от сервера в человекочитаемый формат: в русском —
   * «ДД.ММ.ГГГГ», в английском — «Mon D, YYYY». При неудаче возвращает
   * исходную строку. (Тот же формат, что на странице подписки.)
   */
  function formatUntil(raw) {
    if (!raw) return "";
    var str = String(raw);
    var enLang = window.App && App.lang === "en";
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
    if (parts.length === 3) return parts[2] + "." + parts[1] + "." + parts[0];
    return str;
  }

  /**
   * Цена тарифа в рублях: сначала из tariffs[key].price, затем из
   * card_prices[key]. Возвращает число > 0 или null, если цены нет.
   */
  function tariffPrice(s, key) {
    var t = s.tariffs && s.tariffs[key];
    var price = t ? Number(t.price) : NaN;
    if (!isFinite(price) || price <= 0) {
      price = Number(s.card_prices && s.card_prices[key]);
    }
    return isFinite(price) && price > 0 ? price : null;
  }

  /** Валюта тарифа (по контракту — всегда «RUB»). */
  function tariffCurrency(s, key) {
    var t = s.tariffs && s.tariffs[key];
    return (t && t.currency) || s.card_currency || "RUB";
  }

  /** Срок действия тарифа в днях (null — навсегда/не задан). */
  function tariffDays(s, key) {
    var t = s.tariffs && s.tariffs[key];
    var days = t ? Number(t.days) : NaN;
    return isFinite(days) && days > 0 ? days : null;
  }

  /**
   * Форматирует сумму: целые — без дробной части («499 ₽»), иначе две цифры.
   * Символ ₽ для RUB, для прочих валют — код валюты.
   */
  function formatPrice(value, currency) {
    if (value == null) return "";
    var num = Number(value);
    if (!isFinite(num)) return "";
    var shown = num % 1 === 0 ? String(num) : num.toFixed(2);
    var symbol = currency === "RUB" ? "₽" : String(currency || "");
    return symbol ? shown + " " + symbol : shown;
  }

  /**
   * «Экономика» ЛЮБОГО тарифа длиннее месяца относительно месячного — тот же
   * расчёт, что на витрине подписки, чтобы цифры на двух экранах совпадали:
   *   months   — сколько месяцев покрывает тариф (days / дней в месяце,
   *              округлено: 90 -> 3, 365 -> 12);
   *   perMonth — рублей в месяц по этому тарифу;
   *   savePct  — процент экономии против months × месячная цена.
   * null для месячного/бессрочного тарифа, без месячной цены и когда выгоды
   * нет — рекламную строку без основания не показываем.
   */
  function tariffEconomy(s, key) {
    if (key === "monthly") return null;
    var price = tariffPrice(s, key);
    var monthly = tariffPrice(s, "monthly");
    var days = tariffDays(s, key);
    if (price == null || monthly == null || !days) return null;

    // Месяц меряем сроком месячного тарифа с сервера (по умолчанию 30 дней).
    var monthDays = tariffDays(s, "monthly") || 30;
    var months = Math.round(days / monthDays);
    if (months < 2) return null;

    var savePct = Math.round((1 - price / (monthly * months)) * 100);
    if (!(savePct > 0)) return null;
    return { perMonth: Math.round(price / months), savePct: savePct };
  }

  /**
   * Пропускает только http(s)-ссылки (реквизиты приходят из env — не даём
   * подсунуть javascript: и подобные схемы). Возвращает URL или null.
   */
  function safeUrl(url) {
    if (!url) return null;
    var str = String(url).trim();
    return /^https?:\/\//i.test(str) ? str : null;
  }

  /**
   * Открывает внешнюю ссылку: через Telegram (openTelegramLink для t.me,
   * иначе openLink), при отсутствии Telegram — обычным window.open.
   */
  function openExternal(url) {
    var safe = safeUrl(url);
    if (!safe) return;
    haptic("light");
    try {
      var tg = window.App && App.tg;
      if (tg && /^https?:\/\/t\.me\//i.test(safe) && typeof tg.openTelegramLink === "function") {
        tg.openTelegramLink(safe);
        return;
      }
      if (tg && typeof tg.openLink === "function") {
        tg.openLink(safe);
        return;
      }
      if (typeof window.open === "function") {
        window.open(safe, "_blank");
        return;
      }
      toast(pick("Ссылка недоступна", "The link is unavailable"));
    } catch (err) {
      toast(pick("Не удалось открыть ссылку", "Could not open the link"));
    }
  }

  /** Ключ оплачиваемого тарифа из App.state (или null, если не задан). */
  function requestedTariff() {
    var key = window.App && App.state && App.state.paymentTariff;
    if (!key) return null;
    key = String(key);
    return TARIFF_KEYS.indexOf(key) >= 0 ? key : null;
  }

  /**
   * Показывать ли экран успеха: после недавнего нажатия «Оплатить» по этому же
   * тарифу статус подписки улучшился (появился премиум либо сдвинулась дата
   * окончания / сменился тариф). Сравниваем со снимком, снятым до оплаты, —
   * поэтому экран появится и тогда, когда доступ выдал вебхук уже после
   * резолва App.payCard (App._pollPremium перерисует страницу).
   * @param {object} s текущий статус (результат sub())
   */
  function isJustPaid(s) {
    var before = state.payBefore;
    if (!before || !state.payAt) return false;
    if (Date.now() - state.payAt >= PAID_WINDOW_MS) return false;
    if (state.payTariff !== state.tariff) return false;
    if (!s.is_premium) return false;
    return (
      !before.is_premium ||
      s.subscription_until !== before.subscription_until ||
      s.subscription_type !== before.subscription_type
    );
  }

  /** Есть ли у пользователя бессрочный доступ (владелец или тариф lifetime). */
  function hasLifetime(s) {
    return s.is_owner || s.subscription_type === "lifetime";
  }

  /* =====================================================================
   *  РАЗМЕТКА
   * ===================================================================== */

  /**
   * Каркас страницы: шапка + контейнер #payBody, который перерисовывается
   * при каждом обновлении статуса.
   */
  function template() {
    var backLabel = pick("Назад", "Back");
    return (
      '<section class="page sub-page pay-page">' +
      '<header class="sub-head pay-head">' +
      '<button type="button" class="sub-back" id="payBack" aria-label="' +
      esc(backLabel) +
      '">' +
      '<span class="sub-back__arrow" aria-hidden="true">' +
      icon("arrow", { size: 18, rotate: 180 }) +
      "</span>" +
      "<span>" +
      esc(backLabel) +
      "</span>" +
      "</button>" +
      '<h1 class="page-title sub-title">' +
      esc(pick("Оплата", "Payment")) +
      "</h1>" +
      '<p class="page-subtitle sub-subtitle">' +
      // Название продукта то же, что в описании платежа у провайдера
      // (config.PAYMENT_PRODUCT_NAME): модератор сверяет витрину с платежом.
      esc(pick("Подписка Fitness Up", "Fitness Up subscription")) +
      "</p>" +
      "</header>" +
      '<div class="pay-body" id="payBody">' +
      '<div class="skeleton skeleton--block"></div>' +
      "</div>" +
      "</section>"
    );
  }

  /**
   * Карточка выбранного плана: иконка, название, срок, цена, заметки.
   */
  function planHtml(s, key) {
    var meta = TARIFF_META[key];
    var price = tariffPrice(s, key);
    var currency = tariffCurrency(s, key);
    var days = tariffDays(s, key);

    // Срок: берём дни с сервера (со склонением), иначе — подпись из метаданных.
    var termText = days
      ? pick(days + " " + daysWordRu(days) + " доступа", days + (days === 1 ? " day" : " days") + " of access")
      : pick(meta.term[0], meta.term[1]);

    // Для тарифов длиннее месяца — «≈ N ₽/мес · экономия M%» (только если
    // выгода против месячной цены действительно есть).
    var econHtml = "";
    var econ = tariffEconomy(s, key);
    if (econ) {
      var perMonthShown = formatPrice(econ.perMonth, currency);
      var econText =
        pick("≈ " + perMonthShown + "/мес", "≈ " + perMonthShown + "/mo") +
        " · " +
        pick("экономия " + econ.savePct + "%", "save " + econ.savePct + "%");
      econHtml = '<div class="pay-plan__econ">' + esc(econText) + "</div>";
    }

    // Заметка о том, как оплата ляжет на текущий доступ.
    var noteHtml = "";
    if (hasLifetime(s)) {
      noteHtml =
        '<div class="pay-plan__note">' +
        esc(pick("У вас уже вечный доступ", "You already have lifetime access")) +
        "</div>";
    } else if (s.is_premium) {
      if (key === "lifetime") {
        noteHtml =
          '<div class="pay-plan__note">' +
          esc(pick("Доступ станет бессрочным", "Your access becomes lifetime")) +
          "</div>";
      } else if (s.subscription_until) {
        noteHtml =
          '<div class="pay-plan__note">' +
          esc(
            pick(
              "Дни добавятся к текущей подписке (до " +
                formatUntil(s.subscription_until) +
                ")",
              "Days will be added to your current subscription (until " +
                formatUntil(s.subscription_until) +
                ")"
            )
          ) +
          "</div>";
      }
    }

    // Итог заказа: надзаголовок, тариф крупно, цена ещё крупнее — как на
    // чеке. Иконки тарифа здесь больше нет: она дублировала название.
    return (
      '<article class="card pay-plan">' +
      '<span class="eyebrow pay-plan__eyebrow">' +
      esc(pick("Ваш заказ", "Your order")) +
      "</span>" +
      '<div class="pay-plan__head">' +
      '<div class="pay-plan__info">' +
      '<div class="pay-plan__title">' +
      esc(pick(meta.title[0], meta.title[1])) +
      "</div>" +
      '<div class="pay-plan__term">' +
      esc(termText) +
      "</div>" +
      "</div>" +
      "</div>" +
      '<div class="num pay-plan__price">' +
      esc(formatPrice(price, currency)) +
      "</div>" +
      econHtml +
      '<div class="pay-plan__once">' +
      esc(
        pick(
          "Разовый платёж, без автопродления",
          "One-time payment, no auto-renewal"
        )
      ) +
      "</div>" +
      noteHtml +
      "</article>"
    );
  }

  /**
   * Блок «Что входит» — короткий список с галочками.
   */
  function includesHtml() {
    var items = includesList()
      .map(function (text) {
        return (
          '<li class="pay-include">' +
          '<span class="pay-include__check" aria-hidden="true">' +
          icon("check", { size: 18 }) +
          "</span>" +
          '<span class="pay-include__text">' +
          esc(text) +
          "</span>" +
          "</li>"
        );
      })
      .join("");

    return (
      '<section class="card pay-includes">' +
      '<span class="eyebrow pay-section-title">' +
      esc(pick("Что входит", "What is included")) +
      "</span>" +
      '<ul class="pay-includes__list">' +
      items +
      "</ul>" +
      "</section>"
    );
  }

  /**
   * Блок «Способ оплаты»: одна опция — банковская карта, выбрана по умолчанию.
   * Подпись зависит от провайдера; для «none» честно пишем, что приём карт
   * ещё подключается (витрина с ценой нужна для модерации платёжного сервиса).
   */
  function methodHtml(s) {
    var providerNote = "";
    if (s.card_provider === "cloudpayments") {
      providerNote = pick(
        "Безопасная оплата через CloudPayments",
        "Secure payment via CloudPayments"
      );
    } else if (s.card_provider === "yookassa") {
      providerNote = pick(
        "Безопасная оплата через ЮKassa",
        "Secure payment via YooKassa"
      );
    }

    var noticeHtml = "";
    if (s.card_provider !== "cloudpayments" && s.card_provider !== "yookassa") {
      noticeHtml =
        '<p class="pay-notice">' +
        esc(
          pick(
            "Приём карт подключается. Цена и тариф уже актуальны — оплата станет доступна в ближайшее время.",
            "Card payments are being connected. The price and plan are final — payment will be available shortly."
          )
        ) +
        "</p>";
    }

    return (
      '<section class="card pay-method">' +
      '<span class="eyebrow pay-section-title">' +
      esc(pick("Способ оплаты", "Payment method")) +
      "</span>" +
      '<div class="pay-method__option pay-method__option--active" role="radio" aria-checked="true" tabindex="-1">' +
      '<span class="pay-method__mark" aria-hidden="true"></span>' +
      '<span class="pay-method__icon" aria-hidden="true">' +
      icon("card", { size: 22 }) +
      "</span>" +
      '<span class="pay-method__body">' +
      '<span class="pay-method__title">' +
      esc(pick("Банковская карта", "Bank card")) +
      "</span>" +
      '<span class="pay-method__hint">' +
      esc(pick("Visa, Mastercard, МИР", "Visa, Mastercard, MIR")) +
      "</span>" +
      (providerNote
        ? '<span class="pay-method__provider">' + esc(providerNote) + "</span>"
        : "") +
      "</span>" +
      "</div>" +
      noticeHtml +
      "</section>"
    );
  }

  /**
   * Итог и кнопка оплаты. Для владельца вечного доступа кнопку оплаты не
   * показываем — вместо неё «Вернуться».
   */
  function totalHtml(s, key) {
    var price = tariffPrice(s, key);
    var currency = tariffCurrency(s, key);
    var shown = formatPrice(price, currency);

    if (hasLifetime(s)) {
      return (
        '<section class="card pay-total">' +
        '<p class="pay-total__lifetime">' +
        esc(
          pick(
            "У вас уже вечный доступ — оплата не нужна.",
            "You already have lifetime access — no payment needed."
          )
        ) +
        "</p>" +
        '<button type="button" class="btn btn--ghost btn-block pay-return">' +
        esc(pick("Вернуться", "Go back")) +
        "</button>" +
        "</section>"
      );
    }

    // Строка про оферту — только если ссылка на оферту задана.
    var offerHtml = safeUrl(s.legal.offer_url)
      ? '<p class="pay-total__offer">' +
        esc(
          pick(
            "Нажимая «Оплатить», вы принимаете условия оферты",
            "By tapping “Pay” you accept the terms of the offer"
          )
        ) +
        "</p>"
      : "";

    // Чек по 54-ФЗ уходит на e-mail: Telegram его не отдаёт, спрашиваем сами
    // (один раз — сервер запоминает в профиле).
    var emailHtml = "";
    if (s.receipt_email_required) {
      emailHtml =
        '<label class="field pay-email">' +
        '<span class="field__label">' +
        esc(pick("E-mail для чека", "E-mail for the receipt")) +
        "</span>" +
        '<input class="field__input" id="payEmail" type="email" inputmode="email" autocomplete="email" ' +
        'placeholder="name@example.com" value="' +
        esc(state.email || s.email || "") +
        '">' +
        "</label>";
    }
    return (
      '<section class="card pay-total">' +
      '<div class="pay-total__row">' +
      '<span class="pay-total__label">' +
      esc(pick("Итого", "Total")) +
      "</span>" +
      '<span class="num pay-total__sum">' +
      esc(shown) +
      "</span>" +
      "</div>" +
      emailHtml +
      offerHtml +
      '<button type="button" class="btn btn--cta btn-block pay-submit" id="paySubmit">' +
      esc(pick("Оплатить ", "Pay ")) +
      esc(shown) +
      "</button>" +
      "</section>"
    );
  }

  /**
   * Ссылка, которая открывается во внешнем браузере (через Telegram).
   * href="#" + data-url: сам переход делает обработчик openExternal.
   */
  function externalLinkHtml(url, label) {
    var safe = safeUrl(url);
    if (!safe) return "";
    return (
      '<a class="pay-legal__link" href="#" data-url="' +
      esc(safe) +
      '">' +
      esc(label) +
      "</a>"
    );
  }

  /**
   * Контакт поддержки: e-mail -> mailto:, @username -> t.me, иначе текст.
   */
  function contactHtml(contact) {
    var raw = String(contact || "").trim();
    if (!raw) return "";
    if (raw.charAt(0) === "@" && raw.length > 1) {
      return externalLinkHtml("https://t.me/" + raw.slice(1), raw);
    }
    if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(raw)) {
      return (
        '<a class="pay-legal__mail" href="mailto:' +
        esc(raw) +
        '">' +
        esc(raw) +
        "</a>"
      );
    }
    return esc(raw);
  }

  /**
   * Юридический подвал: реквизиты продавца и ссылки на документы.
   * Показывается только при наличии данных; строка про возврат — всегда
   * (её рендерит renderBody отдельно).
   */
  function legalHtml(s) {
    var legal = s.legal;
    var lines = [];

    if (legal.seller) {
      lines.push(
        '<p class="pay-legal__line">' +
          esc(pick("Продавец: ", "Seller: ")) +
          esc(legal.seller) +
          "</p>"
      );
    }
    if (legal.inn) {
      lines.push(
        '<p class="pay-legal__line">' +
          esc(pick("ИНН: ", "Tax ID: ")) +
          esc(legal.inn) +
          "</p>"
      );
    }
    var contact = contactHtml(legal.contact);
    if (contact) {
      lines.push(
        '<p class="pay-legal__line">' +
          esc(pick("Поддержка: ", "Support: ")) +
          contact +
          "</p>"
      );
    }

    var links = [];
    var offer = externalLinkHtml(legal.offer_url, pick("Оферта", "Offer"));
    if (offer) links.push(offer);
    var privacy = externalLinkHtml(
      legal.privacy_url,
      pick("Политика конфиденциальности", "Privacy policy")
    );
    if (privacy) links.push(privacy);
    if (links.length) {
      lines.push(
        '<p class="pay-legal__links">' +
          links.join('<span class="pay-legal__sep" aria-hidden="true">·</span>') +
          "</p>"
      );
    }

    if (!lines.length) return "";
    return '<footer class="pay-legal">' + lines.join("") + "</footer>";
  }

  /** Строка про возврат — показывается всегда (короткая и честная). */
  function refundHtml() {
    return (
      '<p class="pay-refund">' +
      esc(
        pick(
          "Возврат — по обращению в поддержку в течение 14 дней, если доступом не пользовались.",
          "Refund — contact support within 14 days if you have not used the access."
        )
      ) +
      "</p>"
    );
  }

  /**
   * Экран успеха: показывается сразу после оплаты, когда премиум активен.
   */
  function successHtml(s) {
    var forever = hasLifetime(s) || !s.subscription_until;
    var untilText = forever
      ? pick("Навсегда", "Forever")
      : pick("до ", "until ") + formatUntil(s.subscription_until);

    return (
      '<section class="card pay-success">' +
      '<div class="pay-success__icon" aria-hidden="true">' +
      icon("check", { size: 28 }) +
      "</div>" +
      '<h2 class="pay-success__title">' +
      esc(pick("Подписка активна", "Subscription active")) +
      "</h2>" +
      '<div class="pay-success__until">' +
      esc(untilText) +
      "</div>" +
      '<button type="button" class="btn btn--cta btn-block pay-return">' +
      esc(pick("Вернуться", "Go back")) +
      "</button>" +
      "</section>"
    );
  }

  /**
   * Состояние «нет цены»: тариф есть, но рублёвой цены сервер не прислал.
   */
  function noPriceHtml() {
    return (
      '<section class="card pay-empty">' +
      "<p>" +
      esc(
        pick(
          "Не удалось загрузить цену тарифа.",
          "Could not load the plan price."
        )
      ) +
      "</p>" +
      '<button type="button" class="btn btn--ghost btn-block pay-retry">' +
      esc(pick("Повторить", "Retry")) +
      "</button>" +
      '<button type="button" class="btn btn--ghost btn-block pay-back-plans">' +
      esc(pick("К тарифам", "Back to plans")) +
      "</button>" +
      "</section>"
    );
  }

  /* =====================================================================
   *  РЕНДЕР
   * ===================================================================== */

  /**
   * Перерисовывает тело страницы по текущему App.subscription и навешивает
   * обработчики на свежие элементы.
   */
  function renderBody() {
    var box = state.viewEl && state.viewEl.querySelector("#payBody");
    if (!box) return;

    var s = sub();
    var key = state.tariff;
    if (!key || !TARIFF_META[key]) return;

    if (isJustPaid(s)) {
      // Оплата только что прошла — показываем результат вместо витрины.
      box.innerHTML = successHtml(s);
    } else if (tariffPrice(s, key) == null) {
      box.innerHTML = noPriceHtml();
    } else {
      // Владельцу вечного доступа способ оплаты не показываем: платить нечего,
      // вместо кнопки оплаты в итоге стоит «Вернуться».
      box.innerHTML =
        planHtml(s, key) +
        includesHtml() +
        (hasLifetime(s) ? "" : methodHtml(s)) +
        totalHtml(s, key) +
        legalHtml(s) +
        refundHtml();
    }

    bindBody(box);
  }

  /**
   * Навешивает обработчики на элементы внутри #payBody.
   */
  function bindBody(box) {
    var submit = box.querySelector("#paySubmit");
    if (submit) {
      submit.disabled = !!state.submitting;
      submit.addEventListener("click", onSubmit);
    }

    var returns = box.querySelectorAll(".pay-return");
    for (var i = 0; i < returns.length; i++) {
      returns[i].addEventListener("click", onReturn);
    }

    var retry = box.querySelector(".pay-retry");
    if (retry) {
      retry.addEventListener("click", function () {
        haptic("light");
        refreshStatus();
      });
    }

    var backPlans = box.querySelector(".pay-back-plans");
    if (backPlans) {
      backPlans.addEventListener("click", onBack);
    }

    var links = box.querySelectorAll(".pay-legal__link");
    for (var j = 0; j < links.length; j++) {
      links[j].addEventListener("click", onLegalLink);
    }
  }

  /* =====================================================================
   *  ДЕЙСТВИЯ
   * ===================================================================== */

  /** «Назад» — всегда к списку тарифов. */
  function onBack() {
    haptic("light");
    clearPayAttempt();
    App.navigate("subscription");
  }

  /** «Вернуться» — туда, откуда пришли в подписку (по умолчанию «Аккаунт»). */
  function onReturn() {
    haptic("light");
    clearPayAttempt();
    var origin = (window.App && App.state && App.state.subOrigin) || "account";
    App.navigate(origin);
  }

  /** Клик по юридической ссылке (оферта, политика, телеграм-контакт). */
  function onLegalLink(e) {
    if (e && typeof e.preventDefault === "function") e.preventDefault();
    var el = e && e.currentTarget;
    var url = el && el.getAttribute("data-url");
    openExternal(url);
  }

  /**
   * Склонение «день/дня/дней» по числу — для срока тарифа («90 дней
   * доступа»): срок приходит с сервера из env, поэтому число может быть любым.
   */
  function daysWordRu(n) {
    var abs = Math.abs(Math.round(Number(n) || 0));
    var mod10 = abs % 10;
    var mod100 = abs % 100;
    if (mod10 === 1 && mod100 !== 11) return "день";
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return "дня";
    return "дней";
  }

  /**
   * Кнопка «Оплатить N ₽» — отдаёт управление App.payCard, который сам
   * выбирает провайдера (виджет CloudPayments, ссылка ЮKassa или честный
   * тост «оплата подключается»). Доступ активирует вебхук.
   */
  function onSubmit(e) {
    var btn = e && e.currentTarget;
    var key = state.tariff;
    if (!key) return;

    haptic("light");

    if (!(window.App && typeof App.payCard === "function")) {
      toast(pick("Оплата картой недоступна", "Card payment unavailable"));
      return;
    }
    if (state.submitting) return;
    // E-mail для чека — обязателен, когда сервер его требует.
    var email = "";
    var emailEl = state.viewEl && state.viewEl.querySelector("#payEmail");
    if (emailEl) {
      email = String(emailEl.value || "").trim();
      state.email = email;
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(email)) {
        haptic("error");
        toast(pick("Укажите e-mail — на него придёт чек", "Enter an e-mail — the receipt will be sent there"));
        emailEl.focus();
        return;
      }
    }

    // Запоминаем статус ДО оплаты — с ним renderBody сравнивает текущий, чтобы
    // отличить «стал премиумом» и «продлил подписку» от «ничего не изменилось»
    // (отказ, закрытие окна). Сравнение живёт PAID_WINDOW_MS: доступ выдаёт
    // вебхук уже после того, как App.payCard резолвится.
    var before = sub();
    state.payAt = Date.now();
    state.payTariff = key;
    state.payBefore = {
      is_premium: before.is_premium,
      subscription_until: before.subscription_until,
      subscription_type: before.subscription_type
    };

    state.submitting = true;
    if (btn) btn.disabled = true;
    App.showLoading();

    Promise.resolve(App.payCard(key, email))
      .then(function () {
        renderBody();
      })
      .catch(function (err) {
        haptic("error");
        toast(
          pick("Не удалось начать оплату: ", "Could not start payment: ") +
            (err && err.message ? err.message : pick("ошибка", "error"))
        );
      })
      .finally(function () {
        state.submitting = false;
        App.hideLoading();
        // Кнопка могла быть заменена перерисовкой — снимаем блокировку и с
        // прежнего узла (безвредно), и с актуального.
        if (btn) btn.disabled = false;
        var fresh =
          state.viewEl && state.viewEl.querySelector("#paySubmit");
        if (fresh) fresh.disabled = false;
      });
  }

  /**
   * Обновляет статус подписки с сервера и перерисовывает страницу.
   * Best-effort: при ошибке остаются данные из кэша.
   */
  function refreshStatus() {
    if (state.loading) return;
    state.loading = true;

    var done = function () {
      state.loading = false;
      // Страница могла смениться, пока шёл запрос — проверяем актуальность.
      if (state.viewEl && document.body.contains(state.viewEl)) {
        renderBody();
      }
    };

    if (window.App && typeof App.refreshSubscription === "function") {
      Promise.resolve(App.refreshSubscription()).then(done, done);
    } else {
      done();
    }
  }

  /* =====================================================================
   *  КОНТРОЛЛЕР СТРАНИЦЫ
   * ===================================================================== */

  var controller = {
    /**
     * Показ страницы: проверяет выбранный тариф, строит разметку, рисует
     * по кэшу и обновляет статус с сервера.
     */
    onShow: function (viewEl) {
      state.viewEl = viewEl;
      state.submitting = false;

      var key = requestedTariff();
      if (!key) {
        // Тариф не выбран (или неизвестен) — возвращаемся к списку тарифов.
        // Навигацию откладываем, чтобы не вызывать navigate внутри navigate.
        viewEl.innerHTML =
          '<section class="page sub-page pay-page">' +
          '<div class="card pay-empty"><p>' +
          esc(pick("Тариф не выбран.", "No plan selected.")) +
          "</p></div></section>";
        toast(pick("Выберите тариф", "Choose a plan"));
        setTimeout(function () {
          App.navigate("subscription");
        }, 0);
        return;
      }

      state.tariff = key;
      viewEl.innerHTML = template();

      App.scrollTop();

      var back = viewEl.querySelector("#payBack");
      if (back) back.addEventListener("click", onBack);

      // Сначала по кэшу (мгновенно), затем обновляем статус с сервера.
      renderBody();
      refreshStatus();
    },

    /**
     * Уход со страницы — освобождаем ссылки. Снимок статуса до оплаты НЕ
     * сбрасываем: App._pollPremium переоткрывает страницу после активации
     * доступа, и экран успеха должен пережить такую перерисовку (устаревание —
     * по времени, PAID_WINDOW_MS; кнопки «Назад»/«Вернуться» сбрасывают сами).
     */
    onHide: function () {
      state.viewEl = null;
      state.loading = false;
      state.submitting = false;
      // Уход со страницы посреди оплаты не должен оставить экран под оверлеем.
      if (window.App && typeof App.hideLoading === "function") App.hideLoading();
    }
  };

  // Регистрируем страницу и публикуем контроллер (для отладки/повторного входа).
  window.PagePayment = controller;
  App.registerPage("payment", controller);
})();
