/*
 * page-account.js — страница «Профиль».
 *
 * Регистрирует контроллер через App.registerPage("account", {...}).
 *
 * УСТРОЙСТВО ЭКРАНА (после редизайна):
 *   - Шапка: аватар (App.user.photo_url) и имя.
 *   - Строка-карточка «Подписка» -> экран подписки.
 *   - Дальше всё содержимое разложено по ГРУППАМ односточных разделов
 *     (.acc-sec) с шевроном: раздел раскрывается по нажатию, а не показывает
 *     своё содержимое сразу. Причина: прежняя версия рисовала семь секций плюс
 *     лист настроек с пятью группами одновременно — экран было невозможно
 *     окинуть взглядом, а половина блоков грузила данные впустую.
 *
 *       ПРОФИЛЬ      — Мои параметры и цель.
 *       ПРОГРЕСС     — Вес, История, Фото-прогресс, Трекинг цикла.
 *       УМНЫЕ ФУНКЦИИ— Адаптивные калории, Недельный AI-отчёт.
 *       НАСТРОЙКИ    — Уведомления, Язык, Данные.
 *
 *     Раздел может быть и СТРОКОЙ-ССЫЛКОЙ (поле link): такая строка не
 *     раскрывается, а уводит на отдельный экран. Строки «Добавки» здесь
 *     больше нет: добавки стали вкладкой таббара, и вход из профиля лишь
 *     дублировал её.
 *
 * ДАННЫЕ ГРУЗЯТСЯ ЛЕНИВО: содержимое раздела запрашивается при ПЕРВОМ
 * раскрытии и кэшируется на время показа страницы. Раньше renderPremiumSections
 * вызывался трижды за открытие экрана, и график веса, цикл и фото-прогресс
 * запрашивались по три раза. Теперь при входе идёт ровно два запроса —
 * профиль и статус подписки, — и один общий проход по разделам после того,
 * как оба ответа получены.
 *
 * ЗАКРЫТЫЕ РАЗДЕЛЫ: для бесплатного пользователя платный раздел — это ОДНА
 * компактная строка с иконкой замка; нажатие ведёт на единственный полный
 * экран подписки. Прежде на этой странице рисовалось пять заглушек paywall
 * с пятнадцатью буллетами подряд.
 *
 * Локализация: весь видимый текст через App.pick(ru, en) НА МОМЕНТ РЕНДЕРА.
 * Иконки — только App.icon(...), эмодзи в интерфейсе нет.
 */
(function () {
  "use strict";

  // Локальный хелпер локализации — короткий псевдоним App.pick(ru, en).
  // Все видимые строки этой страницы проходят через L на момент рендера.
  function L(ru, en) {
    return App.pick(ru, en);
  }

  /** Экранирование текста перед вставкой в разметку. */
  function esc(s) {
    return App.escapeHtml(s == null ? "" : String(s));
  }

  /** Иконка из общего набора (js/icons.js). Возвращает строку <svg …>. */
  function icon(name, opts) {
    return App.icon ? App.icon(name, opts) : "";
  }

  // Варианты уровня активности для выпадающего списка.
  // value — коэффициент TDEE, label() — локализованное описание (RU/EN),
  // вычисляется на момент рендера через App.pick.
  var ACTIVITY_OPTIONS = [
    {
      value: 1.2,
      label: function () {
        return L(
          "Минимальная (сидячий образ жизни)",
          "Minimal (sedentary lifestyle)"
        );
      }
    },
    {
      value: 1.375,
      label: function () {
        return L(
          "Лёгкая (1-3 тренировки в неделю)",
          "Light (1-3 workouts per week)"
        );
      }
    },
    {
      value: 1.55,
      label: function () {
        return L(
          "Средняя (3-5 тренировок в неделю)",
          "Moderate (3-5 workouts per week)"
        );
      }
    },
    {
      value: 1.725,
      label: function () {
        return L(
          "Высокая (6-7 тренировок в неделю)",
          "High (6-7 workouts per week)"
        );
      }
    },
    {
      value: 1.9,
      label: function () {
        return L(
          "Очень высокая (тяжёлый физический труд)",
          "Very high (hard physical labor)"
        );
      }
    }
  ];

  // Варианты цели питания (diet_goal). value — то, что уходит на сервер.
  // label() локализуется на момент рендера.
  var DIET_GOAL_OPTIONS = [
    {
      value: "loss",
      label: function () {
        return L("Похудение", "Weight loss");
      }
    },
    {
      value: "maintain",
      label: function () {
        return L("Поддержание", "Maintenance");
      }
    },
    {
      value: "gain",
      label: function () {
        return L("Набор массы", "Muscle gain");
      }
    }
  ];

  // Значение времени по умолчанию для вечерней сводки, если сервер не вернул своё.
  var DEFAULT_SUMMARY_TIME = "21:00";

  // Времена приёмов пищи по умолчанию, когда напоминания включают впервые.
  var DEFAULT_MEAL_TIMES = ["09:00", "13:00", "19:00"];

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

  // Названия месяцев в родительном падеже: «до 12 октября». Числовая дата
  // «12.10.2026» на тёмной карточке читается как строка из документа, а не
  // как обещание — статус подписки должен звучать по-человечески.
  var MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"
  ];
  var MONTHS_EN = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
  ];

  /**
   * Преобразует ISO-дату подписки (например, "2026-12-31" или
   * "2026-12-31T10:00:00") в короткую словесную форму: «12 октября»
   * (год добавляется только если он не текущий). Возвращает пустую
   * строку, если дату распознать не удалось.
   */
  function formatSubDate(value) {
    if (!value) return "";
    var s = String(value).trim();
    // Берём только дату, если пришла дата-время.
    var datePart = s.split("T")[0].split(" ")[0];
    var parts = datePart.split("-");
    if (parts.length !== 3) return datePart;
    var year = Number(parts[0]);
    var mon = Number(parts[1]) - 1;
    var day = Number(parts[2]);
    if (!(mon >= 0 && mon < 12) || !(day > 0)) return datePart;
    var sameYear = year === new Date().getFullYear();
    var en = App.lang === "en";
    var text = en
      ? MONTHS_EN[mon] + " " + day
      : day + " " + MONTHS_RU[mon];
    return sameYear ? text : text + (en ? ", " : " ") + year;
  }

  /**
   * Формирует краткий статус подписки из App.subscription.
   * Текст локализуется на момент вызова (RU/EN).
   * @returns {{ text:string, premium:boolean, action:string }}
   *   premium=true — активная подписка (для подсветки карточки);
   *   action — подпись кнопки на карточке («Оформить» / «Управлять»).
   */
  function subscriptionStatus() {
    var sub = (App && App.subscription) || {};
    var type = sub.subscription_type || "free";
    var premium = !!sub.is_premium;
    var manage = L("Управлять", "Manage");

    // Владелец и вечная подписка — доступ навсегда.
    if (sub.is_owner || type === "lifetime") {
      return {
        text: L("Навсегда", "Forever"),
        premium: true,
        action: manage
      };
    }

    if (premium) {
      var until = formatSubDate(sub.subscription_until);
      return {
        text: until
          ? L("Активна до " + until, "Active until " + until)
          : L("Активна", "Active"),
        premium: true,
        action: manage
      };
    }

    return {
      text: sub.is_expired
        ? L("Истекла", "Expired")
        : L("Не оформлена", "Not active"),
      premium: false,
      action: sub.is_expired ? L("Продлить", "Renew") : L("Оформить", "Subscribe")
    };
  }

  // Ссылки на корневой элемент представления и элементы формы.
  // Хранятся в замыкании контроллера, чтобы переиспользовать между методами.
  var els = null;

  /* =====================================================================
   *  РАЗДЕЛЫ-СТРОКИ (.acc-sec)
   *
   *  Раздел — это строка с иконкой, названием и шевроном; содержимое лежит
   *  в скрытом теле и появляется по нажатию. Платный раздел без подписки
   *  не раскрывается вовсе: у него замок вместо шеврона и нажатие ведёт на
   *  единственный экран подписки. Так пять прежних paywall-заглушек на одной
   *  странице превращаются в пять обычных строк.
   * ===================================================================== */

  // Ключи разделов, которые доступны только по подписке.
  var PREMIUM_SECTIONS = ["weight", "progress", "cycle", "adapt", "report"];

  /** Требуется ли подписка для раздела. */
  function isPremiumSection(key) {
    return PREMIUM_SECTIONS.indexOf(key) !== -1;
  }

  /**
   * Разметка одного раздела.
   * @param {object} o {key, icon, title, hint, body, link}
   *   body — готовый HTML тела (для статичных разделов) либо "" (ленивая
   *   загрузка при первом раскрытии);
   *   link — имя страницы: такая строка не раскрывается, а уводит на неё
   *   (у отдельного экрана своё содержимое, в теле раздела ему тесно).
   */
  function sectionHtml(o) {
    var locked = isPremiumSection(o.key) && !App.isPremium();
    var hint = locked ? L("По подписке", "With subscription") : o.hint || "";

    return (
      '<div class="acc-sec' +
      (locked ? " acc-sec--locked" : "") +
      '" data-sec="' +
      esc(o.key) +
      '"' +
      // Собственную подпись храним в атрибуте: при снятии замка
      // applySectionsState возвращает её на место вместо «По подписке».
      (o.hint ? ' data-hint="' + esc(o.hint) + '"' : "") +
      (o.link ? ' data-link="' + esc(o.link) + '"' : "") +
      ' id="accSec-' +
      esc(o.key) +
      '">' +
      '<button type="button" class="acc-sec__head" aria-expanded="false">' +
      '<span class="acc-sec__icon" aria-hidden="true">' +
      icon(o.icon) +
      "</span>" +
      '<span class="acc-sec__text">' +
      '<span class="acc-sec__title">' +
      esc(o.title) +
      "</span>" +
      '<span class="acc-sec__hint">' +
      esc(hint) +
      "</span>" +
      "</span>" +
      '<span class="acc-sec__mark" aria-hidden="true">' +
      (locked ? icon("lock", { size: 18 }) : icon("chevron", { size: 18 })) +
      "</span>" +
      "</button>" +
      // У строки-ссылки тела нет вовсе: её содержимое — отдельный экран.
      (o.link
        ? ""
        : '<div class="acc-sec__body" hidden>' + (o.body || "") + "</div>") +
      "</div>"
    );
  }

  /**
   * Группа разделов: надзаголовок + карточка со строками.
   * @param {string} title надзаголовок группы (локализованный)
   * @param {string} rows HTML строк-разделов
   */
  function groupHtml(title, rows) {
    return (
      '<div class="acc-group">' +
      '<span class="eyebrow acc-group__title">' +
      esc(title) +
      "</span>" +
      '<section class="card acc-list">' +
      rows +
      "</section>" +
      "</div>"
    );
  }

  /** Находит DOM-узел раздела по ключу. */
  function secEl(key) {
    if (!els || !els.viewEl) return null;
    return els.viewEl.querySelector('.acc-sec[data-sec="' + key + '"]');
  }

  /** Тело раздела по ключу. */
  function secBody(key) {
    var sec = secEl(key);
    return sec ? sec.querySelector(".acc-sec__body") : null;
  }

  /** Раскрыт ли раздел сейчас. */
  function secIsOpen(key) {
    var sec = secEl(key);
    return !!(sec && sec.classList.contains("acc-sec--open"));
  }

  /**
   * Ленивая загрузка содержимого раздела: вызывается при первом раскрытии
   * и повторно — когда пришли свежие данные (профиль/подписка) для уже
   * раскрытого раздела.
   */
  function loadSection(key) {
    switch (key) {
      case "weight":
        renderWeight();
        break;
      case "history":
        loadHistory();
        break;
      case "cycle":
        renderCycle();
        break;
      case "progress":
        renderProgress();
        break;
      case "adapt":
        renderAdaptive();
        break;
      case "report":
        renderReport();
        break;
      case "notify":
        loadNotifications();
        break;
      default:
        break;
    }
  }

  /**
   * Раскрывает/сворачивает раздел. Для закрытого платного раздела вместо
   * раскрытия уводит на экран подписки (единственный на всё приложение).
   */
  function toggleSection(key) {
    var sec = secEl(key);
    if (!sec) return;

    if (sec.classList.contains("acc-sec--locked")) {
      App.haptic("light");
      App.goSubscription();
      return;
    }

    // Строка-ссылка: уводит на отдельный экран, а не раскрывается.
    var link = sec.getAttribute("data-link");
    if (link) {
      App.haptic("selection");
      App.navigate(link);
      return;
    }

    var body = sec.querySelector(".acc-sec__body");
    var head = sec.querySelector(".acc-sec__head");
    var open = sec.classList.toggle("acc-sec--open");
    if (body) body.hidden = !open;
    if (head) head.setAttribute("aria-expanded", open ? "true" : "false");
    App.haptic("selection");

    // Данные подгружаем только при первом раскрытии: повторное открытие
    // показывает уже загруженное содержимое без запроса.
    if (open && !sec.dataset.loaded) {
      sec.dataset.loaded = "1";
      loadSection(key);
    }
  }

  /**
   * Одна делегированная навеска на все разделы страницы.
   *
   * Слушатель вешаем на КОРЕНЬ СТРАНИЦЫ (.page-account), который создаётся
   * заново при каждом onShow, а НЕ на #view: этот контейнер живёт всё время
   * работы приложения, роутер лишь чистит его innerHTML. Обработчик на нём
   * копился бы с каждым входом на экран — со второго раза один тап
   * срабатывал дважды, и ни один раздел больше не открывался.
   */
  function bindSections(viewEl) {
    if (!viewEl) return;
    var root = viewEl.querySelector(".page-account");
    if (!root) return;
    root.addEventListener("click", function (e) {
      var head = e.target.closest(".acc-sec__head");
      if (!head) return;
      var sec = head.closest(".acc-sec");
      if (!sec) return;
      var key = sec.getAttribute("data-sec");
      if (key) toggleSection(key);
    });
  }

  /**
   * Приводит платные разделы к текущему статусу подписки: замок/шеврон,
   * подпись и — если доступ пропал — сворачивание уже раскрытого раздела.
   * Вызывается ОДИН раз после того, как получены профиль и статус подписки.
   */
  function applySectionsState() {
    if (!els || !els.viewEl) return;

    var premium = App.isPremium();

    for (var i = 0; i < PREMIUM_SECTIONS.length; i++) {
      var key = PREMIUM_SECTIONS[i];
      var sec = secEl(key);
      if (!sec) continue;

      var locked = !premium;
      sec.classList.toggle("acc-sec--locked", locked);

      var mark = sec.querySelector(".acc-sec__mark");
      if (mark) {
        mark.innerHTML = locked
          ? icon("lock", { size: 18 })
          : icon("chevron", { size: 18 });
      }
      var hint = sec.querySelector(".acc-sec__hint");
      if (hint) {
        hint.textContent = locked
          ? L("По подписке", "With subscription")
          : sec.getAttribute("data-hint") || "";
      }

      if (locked) {
        // Доступ пропал (или не подтвердился) — закрываем и чистим содержимое,
        // чтобы платные данные не оставались на экране.
        sec.classList.remove("acc-sec--open");
        delete sec.dataset.loaded;
        var body = sec.querySelector(".acc-sec__body");
        if (body) {
          body.hidden = true;
          body.innerHTML = "";
        }
        var head = sec.querySelector(".acc-sec__head");
        if (head) head.setAttribute("aria-expanded", "false");
      } else if (secIsOpen(key)) {
        // Раздел уже раскрыт, а данные (профиль/подписка) только что пришли —
        // перерисовываем его один раз актуальными данными.
        loadSection(key);
      }
    }

    applyCycleVisibility();
  }

  /**
   * Женская фича: для явно мужского профиля раздел цикла не показываем вовсе.
   * Вызывается и по кэшу профиля (сразу при показе), и после его загрузки.
   */
  function applyCycleVisibility() {
    var gender = App.state.profile && App.state.profile.gender;
    var cycleSec = secEl("cycle");
    if (cycleSec) {
      cycleSec.hidden = gender === "male";
    }
  }

  /* =====================================================================
   *  РАЗМЕТКА СТРАНИЦЫ
   * ===================================================================== */

  /** Тело раздела «Мои параметры и цель» — форма профиля. */
  function profileFormHtml() {
    // Опции уровня активности (локализованные подписи).
    var activityOptionsHtml = ACTIVITY_OPTIONS.map(function (o) {
      return (
        '<option value="' + o.value + '">' + esc(o.label()) + "</option>"
      );
    }).join("");

    // Опции цели питания (локализованные подписи).
    var dietGoalOptionsHtml = DIET_GOAL_OPTIONS.map(function (o) {
      return (
        '<option value="' + esc(o.value) + '">' + esc(o.label()) + "</option>"
      );
    }).join("");

    return (
      '<form class="acc-form" id="accForm" novalidate>' +
      '<div class="acc-grid">' +
      '<label class="field">' +
      '<span class="field__label">' +
      esc(L("Вес, кг", "Weight, kg")) +
      "</span>" +
      '<input class="field__input" id="accWeight" type="number" inputmode="decimal" min="0" step="0.1" placeholder="70">' +
      "</label>" +

      '<label class="field">' +
      '<span class="field__label">' +
      esc(L("Рост, см", "Height, cm")) +
      "</span>" +
      '<input class="field__input" id="accHeight" type="number" inputmode="decimal" min="0" step="0.1" placeholder="175">' +
      "</label>" +

      '<label class="field">' +
      '<span class="field__label">' +
      esc(L("Возраст, лет", "Age, years")) +
      "</span>" +
      '<input class="field__input" id="accAge" type="number" inputmode="numeric" min="0" step="1" placeholder="30">' +
      "</label>" +

      '<label class="field">' +
      '<span class="field__label">' +
      esc(L("Пол", "Gender")) +
      "</span>" +
      '<select class="field__input" id="accGender">' +
      // Пустой вариант по умолчанию: пол не выбран, пока пользователь не укажет
      // явно (бэкенд оставляет gender NULL, чтобы не искажать расчёт калорий).
      '<option value="">' +
      esc(L("— выберите —", "— select —")) +
      "</option>" +
      '<option value="male">' +
      esc(L("Мужской", "Male")) +
      "</option>" +
      '<option value="female">' +
      esc(L("Женский", "Female")) +
      "</option>" +
      "</select>" +
      "</label>" +
      "</div>" +

      '<label class="field">' +
      '<span class="field__label">' +
      esc(L("Уровень активности", "Activity level")) +
      "</span>" +
      '<select class="field__input" id="accActivity">' +
      activityOptionsHtml +
      "</select>" +
      "</label>" +

      // ---- Цель питания (diet_goal) ----
      '<label class="field goal-field">' +
      '<span class="field__label">' +
      esc(L("Цель питания", "Nutrition goal")) +
      "</span>" +
      '<select class="field__input" id="accDietGoal">' +
      dietGoalOptionsHtml +
      "</select>" +
      "</label>" +

      '<label class="field">' +
      '<span class="field__label">' +
      esc(L("Цель по калориям, ккал/день", "Calorie goal, kcal/day")) +
      "</span>" +
      '<input class="field__input" id="accGoal" type="number" inputmode="numeric" min="0" step="1" placeholder="2000">' +
      "</label>" +

      // ---- Блок целевых БЖУ (скрыт, пока нет данных) ----
      '<div class="goal-macros" id="accGoalMacros" hidden>' +
      '<div class="goal-macros__title">' +
      esc(L("Целевые БЖУ в день", "Daily target P/F/C")) +
      "</div>" +
      '<div class="goal-macros__grid">' +
      '<div class="goal-macro goal-macro--prot">' +
      '<span class="goal-macro__value" id="accTargetProt">—</span>' +
      '<span class="goal-macro__label">' +
      esc(L("Белки, г", "Protein, g")) +
      "</span>" +
      "</div>" +
      '<div class="goal-macro goal-macro--fat">' +
      '<span class="goal-macro__value" id="accTargetFat">—</span>' +
      '<span class="goal-macro__label">' +
      esc(L("Жиры, г", "Fat, g")) +
      "</span>" +
      "</div>" +
      '<div class="goal-macro goal-macro--carb">' +
      '<span class="goal-macro__value" id="accTargetCarb">—</span>' +
      '<span class="goal-macro__label">' +
      esc(L("Углеводы, г", "Carbs, g")) +
      "</span>" +
      "</div>" +
      "</div>" +
      "</div>" +

      '<button type="button" class="btn btn--ghost" id="accCalcBtn">' +
      icon("settings") +
      "<span>" +
      esc(L("Рассчитать автоматически", "Calculate automatically")) +
      "</span>" +
      "</button>" +
      '<button type="submit" class="btn btn--cta" id="accSaveBtn">' +
      esc(L("Сохранить", "Save")) +
      "</button>" +

      '<p class="acc-hint">' +
      esc(
        L(
          "Автоматический расчёт учитывает ваши параметры, уровень активности и цель питания.",
          "Automatic calculation takes into account your parameters, activity level and nutrition goal."
        )
      ) +
      "</p>" +
      "</form>"
    );
  }

  /** Тело раздела «Язык» — сегментированный переключатель RU/EN. */
  function langBodyHtml() {
    var curLang = App.lang === "en" ? "en" : "ru";
    return (
      '<div class="acc-lang__switch" role="group" aria-label="' +
      esc(L("Выбор языка", "Language selection")) +
      '">' +
      '<button type="button" class="acc-lang__btn' +
      (curLang === "ru" ? " acc-lang__btn--active" : "") +
      '" id="accLangRu" data-lang="ru" aria-pressed="' +
      (curLang === "ru" ? "true" : "false") +
      '">Русский</button>' +
      '<button type="button" class="acc-lang__btn' +
      (curLang === "en" ? " acc-lang__btn--active" : "") +
      '" id="accLangEn" data-lang="en" aria-pressed="' +
      (curLang === "en" ? "true" : "false") +
      '">English</button>' +
      "</div>"
    );
  }

  /**
   * Тело раздела «Тема» — сегмент «Авто / Светлая / Тёмная».
   * «Авто» означает: как в Telegram, а вне его — как в системе. Это режим
   * по умолчанию, поэтому он стоит первым.
   */
  function themeBodyHtml() {
    var mode = App.theme ? App.theme.mode() : "auto";
    var opts = [
      { key: "auto", label: L("Авто", "Auto") },
      { key: "light", label: L("Светлая", "Light") },
      { key: "dark", label: L("Тёмная", "Dark") }
    ];
    var html =
      '<div class="acc-lang__switch" role="group" aria-label="' +
      esc(L("Выбор темы", "Theme selection")) +
      '">';
    for (var i = 0; i < opts.length; i++) {
      var on = opts[i].key === mode;
      html +=
        '<button type="button" class="acc-lang__btn' +
        (on ? " acc-lang__btn--active" : "") +
        '" data-theme-mode="' +
        opts[i].key +
        '" aria-pressed="' +
        (on ? "true" : "false") +
        '">' +
        esc(opts[i].label) +
        "</button>";
    }
    return html + "</div>";
  }

  /** Тело раздела «Данные» — необратимое удаление аккаунта. */
  function dataBodyHtml() {
    return (
      '<p class="acc-danger-hint">' +
      esc(
        L(
          "Полностью удалить все ваши данные (дневник, тренировки, вес, напоминания, фото) и профиль. Действие необратимо; активная подписка прекратится.",
          "Permanently delete all your data (diary, workouts, weight, reminders, photos) and profile. This cannot be undone; any active subscription will end."
        )
      ) +
      "</p>" +
      '<button type="button" class="btn btn--danger btn-block" id="accDeleteData">' +
      icon("trash") +
      "<span>" +
      esc(L("Удалить мои данные", "Delete my data")) +
      "</span>" +
      "</button>"
    );
  }

  /**
   * Возвращает HTML-разметку всей страницы аккаунта.
   * Все видимые строки локализуются здесь через App.pick на момент рендера.
   */
  function template() {
    // Имя пользователя для шапки (берём из Telegram-объекта, если есть).
    var u = App.user || {};
    var displayName =
      (u.first_name ? u.first_name : "") +
      (u.last_name ? " " + u.last_name : "");
    if (!displayName.trim()) {
      displayName = u.username ? "@" + u.username : L("Пользователь", "User");
    }

    // Аватар: либо картинка из Telegram, либо заглушка с иконкой.
    var avatarHtml = u.photo_url
      ? '<img class="acc-avatar" id="accAvatar" alt="' +
        esc(L("Аватар", "Avatar")) +
        '" src="' +
        esc(u.photo_url) +
        '">'
      : '<div class="acc-avatar acc-avatar--empty" id="accAvatar" aria-hidden="true">' +
        icon("user", { size: 30 }) +
        "</div>";

    // Краткий статус подписки для карточки в начале страницы.
    var sub = subscriptionStatus();

    return (
      '<section class="page page-account">' +
      // ---- Компактная шапка профиля ----
      '<header class="acc-header acc-header--compact">' +
      avatarHtml +
      '<div class="acc-header__info">' +
      '<h1 class="acc-name">' +
      esc(displayName) +
      "</h1>" +
      (u.username
        ? '<div class="acc-username">@' + esc(u.username) + "</div>"
        : "") +
      "</div>" +
      "</header>" +

      // ---- Карточка-кнопка «Подписка» ----
      // Тёмный блок с фотографией — тот же приём, что у тренировки дня на
      // «Сегодня»: единственный такой на экране, поэтому подписка находится
      // взглядом сразу. Вся карточка — одна кнопка; «Оформить» внутри —
      // лишь визуальная подпись действия (span, а не вложенная кнопка).
      '<button type="button" class="acc-sub-card hero hero--img' +
      (sub.premium ? " acc-sub-card--premium" : "") +
      '" id="accSubCard" style="' + heroImg("hero-premium.jpg") + '">' +
      '<span class="eyebrow acc-sub-card__title">' +
      esc(L("Подписка", "Subscription")) +
      "</span>" +
      '<span class="acc-sub-card__status" id="accSubStatus">' +
      esc(sub.text) +
      "</span>" +
      '<span class="btn acc-sub-card__action' +
      (sub.premium ? " acc-sub-card__action--ghost" : " btn--cta") +
      '" id="accSubAction">' +
      esc(sub.action) +
      "</span>" +
      "</button>" +

      // ---- ПРОФИЛЬ ----
      groupHtml(
        L("Профиль", "Profile"),
        sectionHtml({
          key: "profile",
          icon: "user",
          title: L("Мои параметры и цель", "My parameters & goal"),
          body: profileFormHtml()
        })
      ) +

      // ---- ПРОГРЕСС ----
      groupHtml(
        L("Прогресс", "Progress"),
        sectionHtml({
          key: "weight",
          icon: "scale",
          title: L("Вес", "Weight")
        }) +
          sectionHtml({
            key: "history",
            icon: "calendar",
            title: L("История по дням", "Daily history")
          }) +
          sectionHtml({
            key: "progress",
            icon: "camera",
            title: L("Фото-прогресс", "Progress photos")
          }) +
          sectionHtml({
            key: "cycle",
            icon: "droplet",
            title: L("Трекинг цикла", "Cycle tracking")
          })
      ) +

      // ---- УМНЫЕ ФУНКЦИИ ----
      groupHtml(
        L("Умные функции", "Smart features"),
        sectionHtml({
          key: "adapt",
          icon: "target",
          title: L("Адаптивные калории", "Adaptive calories")
        }) +
          sectionHtml({
            key: "report",
            icon: "chartLine",
            title: L("Недельный AI-отчёт", "Weekly AI report")
          })
          // Строки «Добавки» здесь больше нет: добавки — вкладка таббара.
      ) +

      // ---- НАСТРОЙКИ ----
      groupHtml(
        L("Настройки", "Settings"),
        sectionHtml({
          key: "notify",
          icon: "bell",
          title: L("Уведомления", "Notifications")
        }) +
          sectionHtml({
            key: "theme",
            icon: "moon",
            title: L("Тема оформления", "Appearance"),
            body: themeBodyHtml()
          }) +
          sectionHtml({
            key: "lang",
            icon: "list",
            title: L("Язык", "Language"),
            body: langBodyHtml()
          }) +
          sectionHtml({
            key: "data",
            icon: "trash",
            title: L("Данные", "Data"),
            body: dataBodyHtml()
          })
      ) +
      "</section>"
    );
  }

  /**
   * Диалог подтверждения опасного действия. Использует нативный Telegram
   * showConfirm, если доступен, иначе — window.confirm. cb вызывается при «да».
   * @param {string} message
   * @param {Function} cb
   */
  function confirmDanger(message, cb) {
    if (App.tg && typeof App.tg.showConfirm === "function") {
      try {
        App.tg.showConfirm(message, function (ok) {
          if (ok) cb();
        });
        return;
      } catch (e) {
        /* упадём в фолбэк ниже */
      }
    }
    if (window.confirm(message)) cb();
  }

  /**
   * Обработчик кнопки «Удалить мои данные». Спрашивает подтверждение, затем
   * зовёт бэкенд-удаление и перезапускает приложение (чистый онбординг с нуля).
   * @param {HTMLElement} btn
   */
  function onDeleteData(btn) {
    var msg = L(
      "Удалить все ваши данные и профиль? Это действие необратимо, а активная подписка прекратится.",
      "Delete all your data and profile? This cannot be undone and any active subscription will end."
    );
    confirmDanger(msg, function () {
      if (btn) btn.disabled = true;
      App.showLoading();
      App.api
        .deleteAccountData()
        .then(function () {
          App.haptic("success");
          App.toast(L("Данные удалены", "Data deleted"));
          // Перезапуск приложения: сбросить кэш/состояние и пройти онбординг заново.
          setTimeout(function () {
            window.location.reload();
          }, 600);
        })
        .catch(function (err) {
          App.haptic("error");
          var reason = err && err.message ? err.message : L("ошибка", "error");
          App.toast(
            L("Не удалось удалить: " + reason, "Failed to delete: " + reason)
          );
          if (btn) btn.disabled = false;
          App.hideLoading();
        });
    });
  }

  /* =====================================================================
   *  ФОРМА ПРОФИЛЯ
   * ===================================================================== */

  /**
   * Заполняет форму данными профиля, полученными с сервера.
   * @param {Object} p — объект ProfileOut.
   */
  function fillForm(p) {
    if (!p || !els || !els.weight) return;
    if (p.weight != null) els.weight.value = p.weight;
    if (p.height != null) els.height.value = p.height;
    if (p.age != null) els.age.value = p.age;
    // Пол заполняем только если он задан в профиле; иначе оставляем пустой
    // вариант «— выберите —» (не подставляем «Мужской» по умолчанию).
    if (p.gender === "female" || p.gender === "male") els.gender.value = p.gender;

    // Уровень активности: выбираем ближайшее доступное значение из списка.
    if (p.activity_level != null) {
      var target = Number(p.activity_level);
      var best = ACTIVITY_OPTIONS[0].value;
      var bestDiff = Infinity;
      ACTIVITY_OPTIONS.forEach(function (o) {
        var d = Math.abs(o.value - target);
        if (d < bestDiff) {
          bestDiff = d;
          best = o.value;
        }
      });
      els.activity.value = String(best);
    }

    // Цель питания (diet_goal). По умолчанию — «Поддержание».
    if (p.diet_goal && isKnownDietGoal(p.diet_goal)) {
      els.dietGoal.value = p.diet_goal;
    }

    if (p.daily_goal_kcal != null) els.goal.value = p.daily_goal_kcal;

    // Целевые БЖУ — показываем блок, если хотя бы одно значение задано.
    showTargetMacros(p.target_proteins, p.target_fats, p.target_carbs);
  }

  /**
   * Проверяет, что переданная цель питания есть в списке известных вариантов.
   */
  function isKnownDietGoal(v) {
    for (var i = 0; i < DIET_GOAL_OPTIONS.length; i++) {
      if (DIET_GOAL_OPTIONS[i].value === v) return true;
    }
    return false;
  }

  /**
   * Показывает или скрывает блок целевых БЖУ.
   * Если все значения пусты — блок прячется.
   */
  function showTargetMacros(prot, fat, carb) {
    if (!els || !els.goalMacros) return;
    var has =
      (prot != null && prot !== "") ||
      (fat != null && fat !== "") ||
      (carb != null && carb !== "");
    if (!has) {
      els.goalMacros.hidden = true;
      return;
    }
    els.targetProt.textContent = prot != null ? App.fmt(prot) : "—";
    els.targetFat.textContent = fat != null ? App.fmt(fat) : "—";
    els.targetCarb.textContent = carb != null ? App.fmt(carb) : "—";
    els.goalMacros.hidden = false;
  }

  /**
   * Считывает числовое значение из поля ввода.
   * @returns {number|null} число либо null, если поле пустое/некорректное.
   */
  function readNum(input) {
    if (!input) return null;
    var raw = (input.value || "").trim().replace(",", ".");
    if (raw === "") return null;
    var n = Number(raw);
    return isFinite(n) ? n : null;
  }

  /**
   * Обработчик кнопки «Рассчитать автоматически».
   * Вызывает серверный расчёт App.api.calculateGoal — сервер сам сохраняет
   * результат в профиль. В ответ приходит дневная норма и целевые БЖУ.
   */
  function onCalc() {
    var weight = readNum(els.weight);
    var height = readNum(els.height);
    var age = readNum(els.age);

    if (weight == null || height == null || age == null) {
      App.toast(
        L(
          "Заполните вес, рост и возраст для расчёта",
          "Fill in weight, height and age to calculate"
        )
      );
      App.haptic("error");
      return;
    }

    var payload = {
      weight: weight,
      height: height,
      age: Math.round(age),
      gender: els.gender.value,
      activity_level: Number(els.activity.value) || 1.375,
      diet_goal: els.dietGoal.value
    };

    els.calcBtn.disabled = true;
    App.showLoading();

    App.api
      .calculateGoal(payload)
      .then(function (res) {
        if (!res) {
          throw new Error(L("Пустой ответ сервера", "Empty server response"));
        }
        // Подставляем дневную норму в поле цели.
        if (res.daily_goal_kcal != null) {
          els.goal.value = Math.round(res.daily_goal_kcal);
        }
        // Показываем целевые БЖУ.
        showTargetMacros(res.target_proteins, res.target_fats, res.target_carbs);
        // Если сервер вернул нормализованную цель питания — отражаем её.
        if (res.diet_goal && isKnownDietGoal(res.diet_goal)) {
          els.dietGoal.value = res.diet_goal;
        }
        // Сервер сохранил расчёт в профиль — синхронизируем кэш.
        if (App.state.profile) {
          App.state.profile.daily_goal_kcal = res.daily_goal_kcal;
          App.state.profile.target_proteins = res.target_proteins;
          App.state.profile.target_fats = res.target_fats;
          App.state.profile.target_carbs = res.target_carbs;
          App.state.profile.diet_goal = res.diet_goal || els.dietGoal.value;
        }
        App.haptic("success");
        App.toast(
          L(
            "Норма рассчитана: " + App.fmt(res.daily_goal_kcal) + " ккал",
            "Goal calculated: " + App.fmt(res.daily_goal_kcal) + " kcal"
          )
        );
      })
      .catch(function (err) {
        App.haptic("error");
        var reason = err && err.message ? err.message : L("ошибка", "error");
        App.toast(
          L("Не удалось рассчитать: " + reason, "Failed to calculate: " + reason)
        );
      })
      .finally(function () {
        els.calcBtn.disabled = false;
        App.hideLoading();
      });
  }

  /**
   * Обработчик отправки формы — сохранение профиля на сервере.
   */
  function onSave(e) {
    if (e) e.preventDefault();

    // Собираем только заполненные поля (ProfileIn — все поля опциональны).
    var data = {};
    var weight = readNum(els.weight);
    var height = readNum(els.height);
    var age = readNum(els.age);
    var goal = readNum(els.goal);

    if (weight != null) data.weight = weight;
    if (height != null) data.height = height;
    if (age != null) data.age = Math.round(age);
    data.gender = els.gender.value;
    data.activity_level = Number(els.activity.value) || 1.375;
    data.diet_goal = els.dietGoal.value;
    if (goal != null) data.daily_goal_kcal = Math.round(goal);

    els.saveBtn.disabled = true;
    App.showLoading();

    App.api
      .saveProfile(data)
      .then(function (profile) {
        // Обновляем кэш профиля и переотрисовываем поля.
        App.state.profile = profile;
        fillForm(profile);
        App.haptic("success");
        App.toast(L("Профиль сохранён", "Profile saved"));
      })
      .catch(function (err) {
        App.haptic("error");
        var reason = err && err.message ? err.message : L("ошибка", "error");
        App.toast(
          L("Не удалось сохранить: " + reason, "Failed to save: " + reason)
        );
      })
      .finally(function () {
        els.saveBtn.disabled = false;
        App.hideLoading();
      });
  }

  /* =====================================================================
   *  ЯЗЫК / LANGUAGE
   *  Переключатель RU/EN. По выбору вызывает App.setLang(...),
   *  который сохраняет язык на сервере и перерисовывает текущую страницу.
   * ===================================================================== */

  /**
   * Обработчик нажатия на кнопку выбора языка.
   * Если выбран тот же язык — ничего не делаем (без лишней перерисовки).
   */
  function onPickLang(lang) {
    var cur = App.lang === "en" ? "en" : "ru";
    if (lang === cur) {
      App.haptic("selection");
      return;
    }
    App.haptic("selection");
    if (App.setLang) {
      // setLang сам перерисует страницу аккаунта — подсветка обновится в template().
      App.setLang(lang);
    }
  }

  /* =====================================================================
   *  ПРЕМИУМ: ВЕС / WEIGHT
   *  График динамики (замеры + тренд) И ввод замера в одном месте: раньше
   *  график ничего не предлагал, а вес вводился единственным полем внутри
   *  свёрнутой формы «Мои параметры» — связь между ними была неочевидна.
   * ===================================================================== */

  /**
   * Округляет число до одного знака после запятой и возвращает строкой.
   * Нечисловые значения превращаются в "—".
   */
  function fmt1(n) {
    var num = Number(n);
    if (!isFinite(num)) return "—";
    return String(Math.round(num * 10) / 10);
  }

  /**
   * Изменение веса со знаком: "+1.2" / "-0.8" / "0".
   */
  function fmtChange(n) {
    var num = Number(n);
    if (!isFinite(num)) return "0";
    var rounded = Math.round(num * 10) / 10;
    return (rounded > 0 ? "+" : "") + rounded;
  }

  /**
   * Строит содержимое раздела «Вес»: кнопка записи замера, скрытая форма
   * ввода и контейнер графика.
   */
  function renderWeight() {
    var box = secBody("weight");
    if (!box) return;

    box.innerHTML =
      '<div class="acc-wt">' +
      '<button type="button" class="btn btn--ghost acc-wt__add" id="accWtAdd">' +
      icon("plus") +
      "<span>" +
      esc(L("Записать вес", "Log weight")) +
      "</span>" +
      "</button>" +
      '<div class="acc-wt__form" id="accWtForm" hidden>' +
      '<label class="field acc-wt__field">' +
      '<span class="field__label">' +
      esc(L("Вес сегодня, кг", "Weight today, kg")) +
      "</span>" +
      '<input class="field__input" id="accWtInput" type="number" inputmode="decimal" min="0" step="0.1" placeholder="70">' +
      "</label>" +
      '<button type="button" class="btn btn--cta acc-wt__save" id="accWtSave">' +
      esc(L("Сохранить", "Save")) +
      "</button>" +
      "</div>" +
      '<div class="wt-chart-wrap" id="accWeightChart">' +
      '<div class="skeleton skeleton--block"></div>' +
      "</div>" +
      "</div>";

    bindWeightInput(box);
    loadWeight(box.querySelector("#accWeightChart"));
  }

  /**
   * Навешивает обработчики компактного ввода замера веса.
   * Форма скрыта до нажатия «Записать вес»: в свёрнутом виде раздел остаётся
   * графиком, а не формой.
   */
  function bindWeightInput(box) {
    var addBtn = box.querySelector("#accWtAdd");
    var form = box.querySelector("#accWtForm");
    var input = box.querySelector("#accWtInput");
    var saveBtn = box.querySelector("#accWtSave");
    if (!addBtn || !form || !input || !saveBtn) return;

    // Предзаполняем последним известным весом из профиля — чаще всего правка
    // идёт в пределах пары сотен граммов.
    var p = App.state.profile || {};
    if (p.weight != null && input.value === "") input.value = p.weight;

    addBtn.addEventListener("click", function () {
      App.haptic("selection");
      var show = form.hidden;
      form.hidden = !show;
      addBtn.hidden = show;
      if (show) input.focus();
    });

    saveBtn.addEventListener("click", function () {
      onSaveWeight(box, input, saveBtn);
    });
  }

  /**
   * Сохраняет замер веса. Пишем через saveProfile: бэкенд одним запросом
   * обновляет и профиль, и замер за сегодня (upsert по дате), поэтому тренд
   * и адаптивный расчёт питаются тем же единственным вводом.
   */
  function onSaveWeight(box, input, btn) {
    var value = readNum(input);
    if (value == null || value <= 0) {
      App.toast(L("Укажите вес", "Enter your weight"));
      App.haptic("error");
      input.focus();
      return;
    }

    btn.disabled = true;
    App.showLoading();

    App.api
      .saveProfile({ weight: value })
      .then(function (profile) {
        if (profile && typeof profile === "object") {
          App.state.profile = profile;
        } else if (App.state.profile) {
          App.state.profile.weight = value;
        }
        // Поле веса в форме параметров должно показывать то же значение.
        if (els && els.weight) els.weight.value = value;

        App.haptic("success");
        App.toast(L("Вес записан", "Weight logged"));

        // Прячем форму и перерисовываем график свежими данными.
        var form = box.querySelector("#accWtForm");
        var addBtn = box.querySelector("#accWtAdd");
        if (form) form.hidden = true;
        if (addBtn) addBtn.hidden = false;
        loadWeight(box.querySelector("#accWeightChart"));
      })
      .catch(function (err) {
        App.haptic("error");
        var reason = err && err.message ? err.message : L("ошибка", "error");
        App.toast(
          L("Не удалось сохранить: " + reason, "Failed to save: " + reason)
        );
      })
      .finally(function () {
        btn.disabled = false;
        App.hideLoading();
      });
  }

  /**
   * Открывает форму ввода веса (используется пустым состоянием графика).
   */
  function openWeightForm() {
    var box = secBody("weight");
    if (!box) return;
    var form = box.querySelector("#accWtForm");
    var addBtn = box.querySelector("#accWtAdd");
    var input = box.querySelector("#accWtInput");
    if (!form) return;
    form.hidden = false;
    if (addBtn) addBtn.hidden = true;
    if (input) input.focus();
  }

  /**
   * Загружает историю веса за 90 дней и отрисовывает SVG-график.
   */
  function loadWeight(chart) {
    if (!chart) return;
    chart.innerHTML = '<div class="skeleton skeleton--block"></div>';

    App.api
      .getWeightHistory(90)
      .then(function (res) {
        renderWeightChart(chart, res || {});
      })
      .catch(function (err) {
        chart.innerHTML =
          '<div class="acc-error">' +
          "<p>" +
          esc(
            L("Не удалось загрузить график веса.", "Failed to load weight chart.")
          ) +
          "</p>" +
          '<p class="acc-error__msg">' +
          esc(err && err.message ? err.message : L("Ошибка сети", "Network error")) +
          "</p>" +
          '<button type="button" class="btn btn--ghost" id="accWeightRetry">' +
          esc(L("Повторить", "Retry")) +
          "</button>" +
          "</div>";
        var retry = chart.querySelector("#accWeightRetry");
        if (retry) {
          retry.addEventListener("click", function () {
            loadWeight(chart);
          });
        }
      });
  }

  /**
   * Отрисовывает SVG-график динамики веса.
   * @param {HTMLElement} chart — контейнер графика.
   * @param {Object} res — {logs:[{date,weight}], trend:[{date,weight}],
   *   latest:float|null, change_kg:float|null}.
   */
  function renderWeightChart(chart, res) {
    var logs = Array.isArray(res.logs) ? res.logs : [];
    var trend = Array.isArray(res.trend) ? res.trend : [];

    // Пустое состояние — КНОПКА, а не инструкция: раньше здесь был текст
    // «укажите вес в разделе…», который некуда было нажать.
    if (!logs.length) {
      chart.innerHTML =
        '<div class="wt-empty">' +
        '<div class="wt-empty__icon" aria-hidden="true">' +
        icon("scale", { size: 24 }) +
        "</div>" +
        '<div class="wt-empty__text">' +
        esc(
          L(
            "Пока нет ни одного замера — динамика появится после первого.",
            "No measurements yet — the trend appears after the first one."
          )
        ) +
        "</div>" +
        '<button type="button" class="btn btn--cta wt-empty__btn" id="accWtEmptyAdd">' +
        esc(L("Записать вес", "Log weight")) +
        "</button>" +
        "</div>";
      var emptyBtn = chart.querySelector("#accWtEmptyAdd");
      if (emptyBtn) {
        emptyBtn.addEventListener("click", function () {
          App.haptic("selection");
          openWeightForm();
        });
      }
      return;
    }

    // ---- Геометрия SVG (адаптивная через viewBox + width:100%) ----
    var W = 320;
    var H = 180;
    var padL = 34; // место под подписи веса слева
    var padR = 12;
    var padT = 12;
    var padB = 22; // место под подписи дат снизу
    var plotW = W - padL - padR;
    var plotH = H - padT - padB;

    // Собираем все точки (даты по порядку) — за основу берём logs.
    // Ось X — индекс по отсортированным датам логов (равномерно).
    var dates = logs.map(function (p) {
      return p.date;
    });
    var indexByDate = {};
    dates.forEach(function (d, i) {
      indexByDate[d] = i;
    });
    var n = dates.length;

    // Диапазон значений Y: учитываем и замеры, и тренд, с небольшим запасом.
    var allVals = [];
    logs.forEach(function (p) {
      if (p.weight != null && isFinite(Number(p.weight))) {
        allVals.push(Number(p.weight));
      }
    });
    trend.forEach(function (p) {
      if (p.weight != null && isFinite(Number(p.weight))) {
        allVals.push(Number(p.weight));
      }
    });
    var minV = Math.min.apply(null, allVals);
    var maxV = Math.max.apply(null, allVals);
    if (!isFinite(minV) || !isFinite(maxV)) {
      minV = 0;
      maxV = 1;
    }
    if (minV === maxV) {
      // Единственное значение — даём симметричный запас в 1 кг.
      minV -= 1;
      maxV += 1;
    } else {
      var pad = (maxV - minV) * 0.12;
      minV -= pad;
      maxV += pad;
    }
    var spanV = maxV - minV || 1;

    // Хелперы перевода данных в координаты SVG.
    function xAt(i) {
      if (n <= 1) return padL + plotW / 2;
      return padL + (plotW * i) / (n - 1);
    }
    function yAt(v) {
      return padT + plotH - ((Number(v) - minV) / spanV) * plotH;
    }

    // ---- Сетка: горизонтальные линии min / середина / max ----
    var gridLines = "";
    var gridVals = [maxV, (maxV + minV) / 2, minV];
    gridVals.forEach(function (gv) {
      var y = yAt(gv);
      gridLines +=
        '<line class="wt-grid-line" x1="' +
        padL +
        '" y1="' +
        y.toFixed(1) +
        '" x2="' +
        (W - padR) +
        '" y2="' +
        y.toFixed(1) +
        '"></line>';
    });

    // Подписи по оси Y (округлённый вес).
    var yLabels = "";
    [maxV, minV].forEach(function (gv) {
      var y = yAt(gv);
      yLabels +=
        '<text class="wt-axis-label" x="' +
        (padL - 4) +
        '" y="' +
        (y + 3).toFixed(1) +
        '" text-anchor="end">' +
        esc(fmt1(gv)) +
        "</text>";
    });

    // ---- Линия замеров (приглушённая) + точки ----
    var logPointsStr = logs
      .map(function (p) {
        return xAt(indexByDate[p.date]).toFixed(1) + "," + yAt(p.weight).toFixed(1);
      })
      .join(" ");
    var logLine =
      n > 1
        ? '<polyline class="wt-line-logs" points="' + logPointsStr + '"></polyline>'
        : "";
    var logDots = logs
      .map(function (p) {
        return (
          '<circle class="wt-dot" cx="' +
          xAt(indexByDate[p.date]).toFixed(1) +
          '" cy="' +
          yAt(p.weight).toFixed(1) +
          '" r="2.6"></circle>'
        );
      })
      .join("");

    // ---- Линия тренда (выделенная, цвет CTA/зелёный) ----
    var trendLine = "";
    if (trend.length > 1) {
      // Тренд может приходить по своим датам — мапим на ось X логов, где
      // возможно, иначе равномерно распределяем по индексу самого тренда.
      var trendPts = trend
        .map(function (p, i) {
          var xi;
          if (indexByDate.hasOwnProperty(p.date)) {
            xi = xAt(indexByDate[p.date]);
          } else if (trend.length > 1) {
            xi = padL + (plotW * i) / (trend.length - 1);
          } else {
            xi = padL + plotW / 2;
          }
          return xi.toFixed(1) + "," + yAt(p.weight).toFixed(1);
        })
        .join(" ");
      trendLine =
        '<polyline class="wt-line-trend" points="' + trendPts + '"></polyline>';
    }

    // ---- Подписи дат по оси X (первая и последняя) ----
    var xLabels = "";
    if (n >= 1) {
      xLabels +=
        '<text class="wt-axis-label" x="' +
        xAt(0).toFixed(1) +
        '" y="' +
        (H - 6) +
        '" text-anchor="start">' +
        esc(formatDate(dates[0])) +
        "</text>";
    }
    if (n >= 2) {
      xLabels +=
        '<text class="wt-axis-label" x="' +
        xAt(n - 1).toFixed(1) +
        '" y="' +
        (H - 6) +
        '" text-anchor="end">' +
        esc(formatDate(dates[n - 1])) +
        "</text>";
    }

    var svg =
      '<svg class="wt-svg" viewBox="0 0 ' +
      W +
      " " +
      H +
      '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="' +
      esc(L("График динамики веса", "Weight dynamics chart")) +
      '">' +
      gridLines +
      yLabels +
      logLine +
      trendLine +
      logDots +
      xLabels +
      "</svg>";

    // ---- Сводка: текущий вес и изменение за период ----
    var kg = L("кг", "kg");
    var latest = res.latest != null ? res.latest : logs[n - 1] && logs[n - 1].weight;
    var change = res.change_kg;

    var changeHtml = "";
    if (change != null && isFinite(Number(change))) {
      var num = Number(change);
      var cls = num > 0 ? " wt-change--up" : num < 0 ? " wt-change--down" : "";
      changeHtml =
        '<div class="wt-stat">' +
        '<span class="wt-stat__value' +
        cls +
        '">' +
        esc(fmtChange(change) + " " + kg) +
        "</span>" +
        '<span class="wt-stat__label">' +
        esc(L("за период", "over the period")) +
        "</span>" +
        "</div>";
    }

    var latestHtml =
      '<div class="wt-stat">' +
      '<span class="wt-stat__value">' +
      esc(fmt1(latest) + " " + kg) +
      "</span>" +
      '<span class="wt-stat__label">' +
      esc(L("текущий вес", "current weight")) +
      "</span>" +
      "</div>";

    // ---- Легенда (замеры / тренд) ----
    var legend =
      '<div class="wt-legend">' +
      '<span class="wt-legend__item">' +
      '<span class="wt-legend__swatch wt-legend__swatch--logs" aria-hidden="true"></span>' +
      esc(L("Замеры", "Measurements")) +
      "</span>" +
      '<span class="wt-legend__item">' +
      '<span class="wt-legend__swatch wt-legend__swatch--trend" aria-hidden="true"></span>' +
      esc(L("Тренд", "Trend")) +
      "</span>" +
      "</div>";

    chart.innerHTML =
      '<div class="wt-stats">' + latestHtml + changeHtml + "</div>" + svg + legend;
  }

  /* =====================================================================
   *  ПРЕМИУМ: АДАПТИВНЫЕ КАЛОРИИ / ADAPTIVE CALORIES
   *  Тумблер adaptive_enabled + кнопка пересчёта дневной цели по реальной
   *  динамике веса. Платный роут (для free — 402).
   * ===================================================================== */

  /**
   * Строит содержимое раздела «Адаптивные калории».
   */
  function renderAdaptive() {
    var card = secBody("adapt");
    if (!card) return;

    var p = App.state.profile || {};
    var enabled = !!p.adaptive_enabled;
    var maintenance = p.calculated_maintenance;

    var maintHtml = "";
    if (maintenance != null && isFinite(Number(maintenance))) {
      maintHtml =
        '<div class="adapt-maint" id="accAdaptMaint">' +
        esc(
          L("Фактическое поддержание ≈ ", "Real maintenance ≈ ") +
            App.fmt(maintenance) +
            L(" ккал", " kcal")
        ) +
        "</div>";
    }

    // Вторичное действие «Пересчитать по динамике» доступно только когда включено.
    var recalcHtml = enabled
      ? '<button type="button" class="btn btn--ghost adapt-recalc-btn" id="accAdaptRecalc">' +
        esc(L("Пересчитать по динамике", "Recalculate now")) +
        "</button>"
      : "";

    card.innerHTML =
      '<p class="adapt-hint">' +
      esc(
        L(
          "Корректирует дневную цель по реальной динамике веса.",
          "Adjusts your daily goal from real weight dynamics."
        )
      ) +
      "</p>" +
      maintHtml +
      // Тумблер-кнопка (Активировать / Деактивировать) вместо чекбокса.
      '<button type="button" class="tgl-btn' +
      (enabled ? " tgl-btn--on" : "") +
      '" id="accAdaptToggle">' +
      esc(
        enabled ? L("Деактивировать", "Deactivate") : L("Активировать", "Activate")
      ) +
      "</button>" +
      recalcHtml +
      '<div class="adapt-result" id="accAdaptResult" hidden></div>';

    var toggle = card.querySelector("#accAdaptToggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        onToggleAdaptive(toggle);
      });
    }

    var recalcBtn = card.querySelector("#accAdaptRecalc");
    if (recalcBtn) {
      recalcBtn.addEventListener("click", function () {
        onRecalcAdaptive(recalcBtn, card.querySelector("#accAdaptResult"));
      });
    }
  }

  /**
   * Переключение adaptive_enabled по кнопке-тумблеру .tgl-btn — сохраняем в
   * профиль на сервере. При включении дополнительно запускаем пересчёт по
   * динамике и показываем его результат.
   */
  function onToggleAdaptive(btn) {
    // Текущее состояние определяем по классу-модификатору кнопки.
    var wasOn = btn.classList.contains("tgl-btn--on");
    var enabled = !wasOn;
    App.haptic("selection");
    btn.disabled = true;

    App.api
      .saveProfile({ adaptive_enabled: enabled })
      .then(function (profile) {
        // Обновляем кэш профиля актуальными данными от сервера.
        if (profile && typeof profile === "object") {
          App.state.profile = profile;
        } else if (App.state.profile) {
          App.state.profile.adaptive_enabled = enabled;
        }
        App.toast(
          enabled
            ? L("Адаптивные калории включены", "Adaptive calories enabled")
            : L("Адаптивные калории выключены", "Adaptive calories disabled")
        );
        // Перерисовываем раздел в новое состояние (вид/подпись кнопки + recalc).
        renderAdaptive();
        // При включении сразу считаем цель по реальной динамике веса.
        var card = secBody("adapt");
        if (enabled && card) {
          onRecalcAdaptive(
            card.querySelector("#accAdaptRecalc"),
            card.querySelector("#accAdaptResult")
          );
        }
      })
      .catch(function (err) {
        btn.disabled = false;
        App.haptic("error");
        var reason = err && err.message ? err.message : L("ошибка", "error");
        App.toast(
          L("Не удалось сохранить: " + reason, "Failed to save: " + reason)
        );
      });
  }

  /**
   * Пересчёт адаптивной цели по реальной динамике веса.
   * При enough_data — показываем поддержание, новую цель и недельное изменение,
   * обновляем поле цели в форме и кэш профиля. Иначе — показываем пояснение.
   */
  function onRecalcAdaptive(btn, resultBox) {
    if (btn) btn.disabled = true;
    App.showLoading();

    App.api
      .recalcAdaptive()
      .then(function (res) {
        res = res || {};
        renderAdaptiveResult(resultBox, res);

        if (res.enough_data && res.new_goal != null) {
          // Обновляем поле цели в форме профиля и кэш.
          if (els && els.goal) {
            els.goal.value = Math.round(res.new_goal);
          }
          if (App.state.profile) {
            App.state.profile.daily_goal_kcal = res.new_goal;
            if (res.maintenance != null) {
              App.state.profile.calculated_maintenance = res.maintenance;
            }
          }
          // Обновляем подпись поддержания в разделе, если она есть.
          updateMaintenanceLabel(res.maintenance);
          App.haptic("success");
          App.toast(
            L(
              "Новая цель: " + App.fmt(res.new_goal) + " ккал",
              "New goal: " + App.fmt(res.new_goal) + " kcal"
            )
          );
        } else {
          App.haptic("warning");
          App.toast(
            L("Недостаточно данных для пересчёта", "Not enough data to recalculate")
          );
        }
      })
      .catch(function (err) {
        App.haptic("error");
        var reason = err && err.message ? err.message : L("ошибка", "error");
        App.toast(
          L("Не удалось пересчитать: " + reason, "Failed to recalculate: " + reason)
        );
      })
      .finally(function () {
        if (btn) btn.disabled = false;
        App.hideLoading();
      });
  }

  /**
   * Обновляет (или создаёт) строку «Фактическое поддержание ≈ N ккал».
   */
  function updateMaintenanceLabel(maintenance) {
    var card = secBody("adapt");
    if (!card) return;
    if (maintenance == null || !isFinite(Number(maintenance))) return;
    var line = card.querySelector("#accAdaptMaint");
    var text =
      L("Фактическое поддержание ≈ ", "Real maintenance ≈ ") +
      App.fmt(maintenance) +
      L(" ккал", " kcal");
    if (line) {
      line.textContent = text;
    }
  }

  /**
   * Отрисовывает результат пересчёта адаптивной цели.
   * @param {HTMLElement} box — контейнер результата.
   * @param {Object} res — ответ /calories/recalculate-adaptive.
   */
  function renderAdaptiveResult(box, res) {
    if (!box) return;

    var explanation = res.explanation || "";

    if (!res.enough_data) {
      // Мало данных — показываем только пояснение.
      box.innerHTML =
        '<div class="adapt-note adapt-note--warn">' +
        esc(
          explanation ||
            L(
              "Недостаточно данных. Добавляйте вес и приёмы пищи регулярно.",
              "Not enough data. Log your weight and meals regularly."
            )
        ) +
        "</div>";
      box.hidden = false;
      return;
    }

    var rows = "";
    if (res.maintenance != null) {
      rows +=
        '<div class="adapt-stat">' +
        '<span class="adapt-stat__label">' +
        esc(L("Поддержание", "Maintenance")) +
        "</span>" +
        '<span class="adapt-stat__value">≈ ' +
        esc(App.fmt(res.maintenance)) +
        " " +
        esc(L("ккал", "kcal")) +
        "</span>" +
        "</div>";
    }
    if (res.new_goal != null) {
      rows +=
        '<div class="adapt-stat adapt-stat--accent">' +
        '<span class="adapt-stat__label">' +
        esc(L("Новая цель", "New goal")) +
        "</span>" +
        '<span class="adapt-stat__value">' +
        esc(App.fmt(res.new_goal)) +
        " " +
        esc(L("ккал", "kcal")) +
        "</span>" +
        "</div>";
    }
    if (res.weekly_change_kg != null && isFinite(Number(res.weekly_change_kg))) {
      rows +=
        '<div class="adapt-stat">' +
        '<span class="adapt-stat__label">' +
        esc(L("Изменение веса", "Weight change")) +
        "</span>" +
        '<span class="adapt-stat__value">' +
        esc(fmtChange(res.weekly_change_kg) + " " + L("кг/нед", "kg/week")) +
        "</span>" +
        "</div>";
    }

    var explHtml = explanation
      ? '<p class="adapt-expl">' + esc(explanation) + "</p>"
      : "";

    box.innerHTML = explHtml + '<div class="adapt-stats">' + rows + "</div>";
    box.hidden = false;
  }

  /* =====================================================================
   *  ПРЕМИУМ: НЕДЕЛЬНЫЙ AI-ОТЧЁТ / WEEKLY AI REPORT
   *  Кнопка «Сформировать отчёт» -> App.api.getWeeklyReport().
   *  Показывает summary, список insights, focus-совет и ключевые stats.
   * ===================================================================== */

  /**
   * Строит содержимое раздела «Недельный AI-отчёт».
   */
  function renderReport() {
    var card = secBody("report");
    if (!card) return;

    card.innerHTML =
      '<p class="rep-hint">' +
      esc(
        L(
          "Сформируем краткий разбор вашей недели: калории, БЖУ, тренировки и вес.",
          "We'll build a short recap of your week: calories, macros, workouts and weight."
        )
      ) +
      "</p>" +
      '<button type="button" class="btn btn--cta rep-gen-btn" id="accReportGen">' +
      icon("sparkle") +
      '<span class="rep-gen-btn__label">' +
      esc(L("Сформировать отчёт", "Generate report")) +
      "</span>" +
      "</button>" +
      '<div class="rep-body" id="accReportBody"></div>';

    var genBtn = card.querySelector("#accReportGen");
    var body = card.querySelector("#accReportBody");
    if (genBtn) {
      genBtn.addEventListener("click", function () {
        loadReport(genBtn, body);
      });
    }
  }

  /**
   * Загружает недельный AI-отчёт и отрисовывает результат.
   * Показывает состояние загрузки, обрабатывает ошибки (с кнопкой повтора).
   */
  function loadReport(genBtn, body) {
    if (!body) return;
    if (genBtn) genBtn.disabled = true;
    App.haptic("light");

    // Состояние загрузки (скелетон) прямо в теле отчёта.
    body.innerHTML =
      '<div class="rep-loading">' +
      '<div class="skeleton skeleton--block"></div>' +
      '<div class="rep-loading__text">' +
      esc(L("Анализируем неделю…", "Analyzing your week…")) +
      "</div>" +
      "</div>";

    App.api
      .getWeeklyReport()
      .then(function (res) {
        renderReportResult(body, res || {});
        App.haptic("success");
      })
      .catch(function (err) {
        var reason =
          err && err.message ? err.message : L("Ошибка сети", "Network error");
        body.innerHTML =
          '<div class="rep-error">' +
          "<p>" +
          esc(L("Не удалось сформировать отчёт.", "Failed to generate the report.")) +
          "</p>" +
          '<p class="rep-error__msg">' +
          esc(reason) +
          "</p>" +
          '<button type="button" class="btn btn--ghost" id="accReportRetry">' +
          esc(L("Повторить", "Retry")) +
          "</button>" +
          "</div>";
        App.haptic("error");
        var retry = body.querySelector("#accReportRetry");
        if (retry) {
          retry.addEventListener("click", function () {
            loadReport(genBtn, body);
          });
        }
      })
      .finally(function () {
        if (genBtn) {
          genBtn.disabled = false;
          // После первой генерации меняем подпись на «Обновить отчёт»:
          // трогаем только текстовую часть, иконка кнопки остаётся на месте.
          var label = genBtn.querySelector(".rep-gen-btn__label");
          if (label) {
            label.textContent = L("Обновить отчёт", "Refresh report");
          }
        }
      });
  }

  /**
   * Отрисовывает результат недельного AI-отчёта.
   * @param {HTMLElement} body — контейнер тела отчёта.
   * @param {Object} res — {summary, insights:[str], focus:str|null, stats:{...}|null}.
   */
  function renderReportResult(body, res) {
    if (!body) return;

    var summary = res.summary ? String(res.summary).trim() : "";
    var insights = Array.isArray(res.insights) ? res.insights : [];
    var focus = res.focus ? String(res.focus).trim() : "";
    var stats = res.stats && typeof res.stats === "object" ? res.stats : null;

    // Пустой ответ — мягкое приглашение вести дневник.
    if (!summary && !insights.length && !focus && !stats) {
      body.innerHTML =
        '<div class="rep-empty">' +
        '<div class="rep-empty__icon" aria-hidden="true">' +
        icon("inbox", { size: 24 }) +
        "</div>" +
        '<div class="rep-empty__text">' +
        esc(
          L(
            "Пока мало данных за неделю. Добавляйте приёмы пищи и тренировки — и отчёт станет точнее.",
            "Not enough data this week yet. Log meals and workouts — the report will get sharper."
          )
        ) +
        "</div>" +
        "</div>";
      return;
    }

    var html = "";

    // ---- Сводка ----
    if (summary) {
      html += '<p class="rep-summary">' + esc(summary) + "</p>";
    }

    // ---- Список выводов (буллеты) ----
    if (insights.length) {
      var items = "";
      for (var i = 0; i < insights.length; i++) {
        var ins = insights[i];
        if (ins == null || String(ins).trim() === "") continue;
        items +=
          '<li class="rep-insight">' +
          '<span class="rep-insight__mark" aria-hidden="true">' +
          icon("dot", { size: 12 }) +
          "</span>" +
          '<span class="rep-insight__text">' +
          esc(String(ins).trim()) +
          "</span>" +
          "</li>";
      }
      if (items) {
        html +=
          '<div class="rep-insights-title">' +
          esc(L("Выводы", "Insights")) +
          "</div>" +
          '<ul class="rep-insights">' +
          items +
          "</ul>";
      }
    }

    // ---- Главный фокус (выделенный совет) ----
    if (focus) {
      html +=
        '<div class="rep-focus">' +
        '<span class="rep-focus__icon" aria-hidden="true">' +
        icon("target") +
        "</span>" +
        '<span class="rep-focus__body">' +
        '<span class="rep-focus__label">' +
        esc(L("Фокус недели", "Focus of the week")) +
        "</span>" +
        '<span class="rep-focus__text">' +
        esc(focus) +
        "</span>" +
        "</span>" +
        "</div>";
    }

    // ---- Компактные ключевые stats ----
    if (stats) {
      html += renderReportStats(stats);
    }

    body.innerHTML = html;
  }

  /**
   * Формирует компактный блок ключевых метрик отчёта.
   * Показываем только заполненные значения.
   * @param {Object} s — объект stats.
   * @returns {string} HTML-разметка блока статистики.
   */
  function renderReportStats(s) {
    var kcal = L("ккал", "kcal");
    var cells = [];

    // Хелпер добавления ячейки (значение + подпись). Пустые значения пропускаем.
    function add(value, label, cls) {
      if (value == null || value === "") return;
      cells.push(
        '<div class="rep-stat' +
          (cls ? " " + cls : "") +
          '">' +
          '<span class="rep-stat__value">' +
          esc(value) +
          "</span>" +
          '<span class="rep-stat__label">' +
          esc(label) +
          "</span>" +
          "</div>"
      );
    }

    // Средние калории.
    if (s.avg_calories != null && isFinite(Number(s.avg_calories))) {
      add(App.fmt(s.avg_calories) + " " + kcal, L("Средние калории", "Avg calories"));
    }

    // Цель по калориям.
    if (s.goal != null && isFinite(Number(s.goal))) {
      add(App.fmt(s.goal) + " " + kcal, L("Цель", "Goal"));
    }

    // Средний дефицит (со знаком: дефицит/профицит).
    if (s.avg_deficit != null && isFinite(Number(s.avg_deficit))) {
      add(
        fmtChange(s.avg_deficit) + " " + kcal,
        L("Средний дефицит", "Avg deficit"),
        Number(s.avg_deficit) < 0 ? "rep-stat--good" : ""
      );
    }

    // Дни с записями.
    if (s.days_logged != null && isFinite(Number(s.days_logged))) {
      add(App.fmt(s.days_logged), L("Дней с записями", "Days logged"));
    }

    // Тренировки за неделю.
    if (s.workouts_count != null && isFinite(Number(s.workouts_count))) {
      add(App.fmt(s.workouts_count), L("Тренировок", "Workouts"));
    }

    // Сожжено калорий.
    if (s.total_burned != null && isFinite(Number(s.total_burned))) {
      add(App.fmt(s.total_burned) + " " + kcal, L("Сожжено", "Burned"));
    }

    // Изменение веса за неделю.
    if (s.weight_change_kg != null && isFinite(Number(s.weight_change_kg))) {
      var wNum = Number(s.weight_change_kg);
      add(
        fmtChange(s.weight_change_kg) + " " + L("кг", "kg"),
        L("Изменение веса", "Weight change"),
        wNum < 0 ? "rep-stat--down" : wNum > 0 ? "rep-stat--up" : ""
      );
    }

    // Средние БЖУ (одной строкой в подписи).
    var prot = s.avg_proteins;
    var fat = s.avg_fats;
    var carb = s.avg_carbs;
    if (
      (prot != null && isFinite(Number(prot))) ||
      (fat != null && isFinite(Number(fat))) ||
      (carb != null && isFinite(Number(carb)))
    ) {
      var g = L("г", "g");
      var macroVal =
        App.fmt(prot || 0) +
        "/" +
        App.fmt(fat || 0) +
        "/" +
        App.fmt(carb || 0) +
        " " +
        g;
      add(macroVal, L("Б/Ж/У в среднем", "Avg P/F/C"));
    }

    if (!cells.length) return "";

    return (
      '<div class="rep-stats-title">' +
      esc(L("Ключевые цифры", "Key numbers")) +
      "</div>" +
      '<div class="rep-stats">' +
      cells.join("") +
      "</div>"
    );
  }

  /* =====================================================================
   *  ТРЕКИНГ ЦИКЛА
   *  Премиум-раздел: пользователь вводит дату начала последней менструации,
   *  среднюю длину цикла и длительность менструации; бэкенд считает фазу, день,
   *  прогноз и фертильное окно. Значения ориентировочные (дисклеймер).
   *  Женская фича: для профиля с gender="male" раздел скрыт целиком.
   * ===================================================================== */

  // Двузначная дополняющая функция (без зависимости от padStart в старых webview).
  function cyc2(n) {
    n = String(n);
    return n.length < 2 ? "0" + n : n;
  }

  // Сегодняшняя дата в ISO "YYYY-MM-DD" по локальному времени (для max/дефолта).
  function cycToday() {
    var d = new Date();
    return d.getFullYear() + "-" + cyc2(d.getMonth() + 1) + "-" + cyc2(d.getDate());
  }

  // Короткий формат ISO-даты -> "DD.MM" (для фертильного окна и прогнозов).
  function cycShortDate(iso) {
    if (!iso || String(iso).length < 10) return "";
    var p = String(iso).split("-");
    return p[2] + "." + p[1];
  }

  // Метаданные фаз: иконка, название и советы (питание/тренировки/самочувствие).
  function cyclePhaseInfo(phase) {
    var map = {
      menstrual: {
        icon: "droplet",
        name: L("Менструация", "Menstruation"),
        cls: "cyc-phase--menstrual",
        nutrition: L(
          "Больше железа: красное мясо, гречка, зелень, гранат. Тёплая еда и вода.",
          "More iron: red meat, buckwheat, greens, pomegranate. Warm food and water."
        ),
        training: L(
          "Мягкая активность: прогулки, растяжка, лёгкая йога.",
          "Gentle activity: walks, stretching, light yoga."
        ),
        wellbeing: L(
          "Отдых и сон в приоритете — не корите себя за усталость.",
          "Prioritize rest and sleep — be kind to yourself if you're tired."
        )
      },
      follicular: {
        icon: "sunrise",
        name: L("Фолликулярная фаза", "Follicular phase"),
        cls: "cyc-phase--follicular",
        nutrition: L(
          "Энергии больше — упор на белок и сложные углеводы.",
          "More energy — focus on protein and complex carbs."
        ),
        training: L(
          "Хорошее время для силовых и интенсивных тренировок.",
          "A great time for strength and high-intensity training."
        ),
        wellbeing: L(
          "Настроение на подъёме — планируйте важные дела.",
          "Mood is rising — plan important tasks."
        )
      },
      ovulation: {
        icon: "sparkle",
        name: L("Овуляция", "Ovulation"),
        cls: "cyc-phase--ovulation",
        nutrition: L(
          "Лёгкая клетчатка и антиоксиданты: овощи, ягоды, зелень.",
          "Light fiber and antioxidants: veggies, berries, greens."
        ),
        training: L(
          "Пик силы и выносливости — можно замахнуться на рекорды.",
          "Peak strength and endurance — go for personal bests."
        ),
        wellbeing: L(
          "Больше энергии и общения — используйте момент.",
          "More energy and sociability — make the most of it."
        )
      },
      luteal: {
        icon: "moon",
        name: L("Лютеиновая фаза", "Luteal phase"),
        cls: "cyc-phase--luteal",
        nutrition: L(
          "Тяга к сладкому — магний и сложные углеводы: орехи, тёмный шоколад, овощи.",
          "Sweet cravings — magnesium and complex carbs: nuts, dark chocolate, veggies."
        ),
        training: L(
          "Ближе к концу снижайте интенсивность: лёгкое кардио, йога.",
          "Ease off intensity toward the end: light cardio, yoga."
        ),
        wellbeing: L(
          "Возможна раздражительность — сон, вода, меньше кофеина.",
          "Possible irritability — sleep, water, less caffeine."
        )
      }
    };
    return map[phase] || null;
  }

  /**
   * Содержимое раздела «Трекинг цикла». Статус загружается один раз за показ
   * (кэш в els.cycleData), дальше перерисовки идут из кэша без запросов.
   */
  function renderCycle() {
    var card = secBody("cycle");
    if (!card) return;

    // Уже загружено в этот показ — рендерим из кэша, без повторного запроса.
    if (els.cycleLoaded) {
      renderCycleView(card, els.cycleData || { has_data: false });
      return;
    }
    // Уже идёт загрузка — не перезапускаем (оставляем скелетон).
    if (els.cycleFetching) return;

    els.cycleFetching = true;
    card.innerHTML = '<div class="skeleton skeleton--block"></div>';

    App.api
      .getCycleStatus()
      .then(function (res) {
        if (!els) return;
        els.cycleFetching = false;
        els.cycleLoaded = true;
        els.cycleData = res || { has_data: false };
        var box = secBody("cycle");
        if (box) renderCycleView(box, els.cycleData);
      })
      .catch(function (err) {
        if (!els) return;
        els.cycleFetching = false;
        var box = secBody("cycle");
        if (!box) return;
        var reason =
          err && err.message ? err.message : L("Ошибка сети", "Network error");
        box.innerHTML =
          '<div class="cyc-error">' +
          "<p>" +
          esc(L("Не удалось загрузить данные цикла.", "Failed to load cycle data.")) +
          "</p>" +
          '<p class="cyc-error__msg">' +
          esc(reason) +
          "</p>" +
          '<button type="button" class="btn btn--ghost" id="accCycleRetry">' +
          esc(L("Повторить", "Retry")) +
          "</button></div>";
        var retry = box.querySelector("#accCycleRetry");
        if (retry) {
          retry.addEventListener("click", function () {
            els.cycleLoaded = false;
            renderCycle();
          });
        }
      });
  }

  /**
   * Решает, что показать: форму настройки (нет данных / режим редактирования)
   * или статус текущей фазы.
   */
  function renderCycleView(card, data) {
    if (!card) return;
    if (!data || !data.has_data) {
      card.innerHTML =
        '<p class="cyc-hint">' +
        esc(
          L(
            "Отметьте начало последней менструации — покажем текущую фазу, прогноз и советы под неё.",
            "Log the start of your last period — we'll show the current phase, a forecast and phase-based tips."
          )
        ) +
        "</p>" +
        '<div class="cyc-body">' +
        cycleFormHtml(null) +
        "</div>";
      bindCycleForm(card, false);
      return;
    }
    // Есть данные — показываем статус.
    card.innerHTML = '<div class="cyc-body">' + cycleStatusHtml(data) + "</div>";
    bindCycleStatus(card);
  }

  /**
   * HTML формы ввода/редактирования данных цикла. При наличии data поля
   * предзаполняются текущими значениями.
   */
  function cycleFormHtml(data) {
    var startVal = data && data.cycle_start_date ? data.cycle_start_date : "";
    var clVal = data && data.cycle_length ? data.cycle_length : "";
    var plVal = data && data.period_length ? data.period_length : "";
    var notesVal = data && data.notes ? data.notes : "";
    var today = cycToday();

    return (
      '<form class="cyc-form" id="accCycleForm">' +
      // Дата начала менструации.
      '<label class="cyc-field">' +
      '<span class="cyc-field__label">' +
      esc(L("Начало последней менструации", "Last period start")) +
      "</span>" +
      '<input type="date" class="field cyc-input" id="accCycStart" max="' +
      today +
      '" value="' +
      esc(startVal) +
      '" required>' +
      "</label>" +
      // Средняя длина цикла.
      '<label class="cyc-field">' +
      '<span class="cyc-field__label">' +
      esc(L("Средняя длина цикла, дней", "Average cycle length, days")) +
      "</span>" +
      '<input type="number" inputmode="numeric" class="field cyc-input" id="accCycLen" ' +
      'min="20" max="45" placeholder="28" value="' +
      esc(String(clVal)) +
      '">' +
      "</label>" +
      // Длительность менструации.
      '<label class="cyc-field">' +
      '<span class="cyc-field__label">' +
      esc(L("Длительность менструации, дней", "Period length, days")) +
      "</span>" +
      '<input type="number" inputmode="numeric" class="field cyc-input" id="accCycPeriod" ' +
      'min="1" max="10" placeholder="5" value="' +
      esc(String(plVal)) +
      '">' +
      "</label>" +
      // Заметка (необязательно).
      '<label class="cyc-field">' +
      '<span class="cyc-field__label">' +
      esc(L("Заметка (необязательно)", "Note (optional)")) +
      "</span>" +
      '<input type="text" class="field cyc-input" id="accCycNotes" maxlength="200" placeholder="' +
      esc(L("самочувствие, симптомы…", "how you feel, symptoms…")) +
      '" value="' +
      esc(notesVal) +
      '">' +
      "</label>" +
      '<button type="submit" class="btn btn--cta cyc-save" id="accCycSave">' +
      esc(L("Сохранить", "Save")) +
      "</button>" +
      '<p class="cyc-disclaimer">' +
      esc(
        L(
          "Прогноз ориентировочный и не заменяет консультацию врача.",
          "The forecast is approximate and not a substitute for medical advice."
        )
      ) +
      "</p>" +
      "</form>"
    );
  }

  // Вешает submit-обработчик на форму цикла (сохранение данных).
  function bindCycleForm(card, isEdit) {
    var form = card.querySelector("#accCycleForm");
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      onCycleSave(card);
    });
    // В режиме редактирования (есть данные) добавим кнопку «Отмена» -> назад к статусу.
    if (isEdit) {
      var cancel = card.querySelector("#accCycCancel");
      if (cancel) {
        cancel.addEventListener("click", function () {
          renderCycleView(card, els.cycleData);
        });
      }
    }
  }

  // Собирает данные формы, валидирует и отправляет на бэкенд.
  function onCycleSave(card) {
    var startEl = card.querySelector("#accCycStart");
    var lenEl = card.querySelector("#accCycLen");
    var perEl = card.querySelector("#accCycPeriod");
    var notesEl = card.querySelector("#accCycNotes");
    var saveBtn = card.querySelector("#accCycSave");

    var start = startEl ? String(startEl.value || "").trim() : "";
    if (!start) {
      App.toast(L("Укажите дату начала менструации", "Please set the period start date"));
      App.haptic("error");
      return;
    }

    var payload = { cycle_start_date: start };
    if (lenEl && String(lenEl.value).trim() !== "") {
      payload.cycle_length = parseInt(lenEl.value, 10);
    }
    if (perEl && String(perEl.value).trim() !== "") {
      payload.period_length = parseInt(perEl.value, 10);
    }
    if (notesEl && String(notesEl.value).trim() !== "") {
      payload.notes = String(notesEl.value).trim();
    }

    if (saveBtn) saveBtn.disabled = true;
    App.haptic("light");

    App.api
      .logCycle(payload)
      .then(function (res) {
        els.cycleLoaded = true;
        els.cycleData = res || { has_data: false };
        renderCycleView(card, els.cycleData);
        App.toast(L("Данные цикла сохранены", "Cycle data saved"));
        App.haptic("success");
      })
      .catch(function (err) {
        if (saveBtn) saveBtn.disabled = false;
        var reason =
          err && err.message ? err.message : L("Ошибка сети", "Network error");
        App.toast(L("Не удалось сохранить: ", "Failed to save: ") + reason);
        App.haptic("error");
      });
  }

  // HTML блока текущего статуса цикла (фаза, день, прогнозы, советы).
  function cycleStatusHtml(data) {
    var info = cyclePhaseInfo(data.phase);
    var phaseName = info ? info.name : L("Фаза цикла", "Cycle phase");
    var phaseIcon = info ? info.icon : "droplet";
    var phaseCls = info ? info.cls : "";

    // Плашка фазы + день цикла.
    var html =
      '<div class="cyc-phase ' +
      phaseCls +
      '">' +
      '<span class="cyc-phase__icon" aria-hidden="true">' +
      icon(phaseIcon) +
      "</span>" +
      '<span class="cyc-phase__text">' +
      '<span class="cyc-phase__name">' +
      esc(phaseName) +
      "</span>" +
      '<span class="cyc-phase__day">' +
      esc(
        L("День цикла: ", "Cycle day: ") +
          (data.day_of_cycle != null ? data.day_of_cycle : "—")
      ) +
      "</span>" +
      "</span>" +
      "</div>";

    // Прогнозы: следующая менструация (обратный отсчёт) + фертильное окно.
    var facts = [];
    if (data.days_until_next_period != null) {
      var dleft = Number(data.days_until_next_period);
      var whenText;
      if (dleft <= 0) {
        whenText = L("ожидается сегодня", "expected today");
      } else {
        whenText =
          L("через ", "in ") +
          dleft +
          " " +
          cycDays(dleft) +
          (data.next_period_date ? " · " + cycShortDate(data.next_period_date) : "");
      }
      facts.push(
        cycFactHtml("calendar", L("Следующая менструация", "Next period"), whenText)
      );
    }
    if (data.ovulation_date) {
      facts.push(
        cycFactHtml(
          "sparkle",
          L("Овуляция (оценка)", "Ovulation (est.)"),
          cycShortDate(data.ovulation_date)
        )
      );
    }
    if (data.fertile_start && data.fertile_end) {
      facts.push(
        cycFactHtml(
          "heart",
          L("Фертильное окно", "Fertile window"),
          cycShortDate(data.fertile_start) + "–" + cycShortDate(data.fertile_end)
        )
      );
    }
    if (facts.length) {
      html += '<div class="cyc-facts">' + facts.join("") + "</div>";
    }

    // Советы под фазу (питание / тренировки / самочувствие).
    if (info) {
      html +=
        '<div class="cyc-tips">' +
        '<div class="cyc-tips__title">' +
        esc(L("Рекомендации на эту фазу", "Tips for this phase")) +
        "</div>" +
        cycTipHtml("utensils", L("Питание", "Nutrition"), info.nutrition) +
        cycTipHtml("run", L("Тренировки", "Training"), info.training) +
        cycTipHtml("heart", L("Самочувствие", "Well-being"), info.wellbeing) +
        "</div>";
    }

    // Заметка пользователя (если есть).
    if (data.notes) {
      html +=
        '<div class="cyc-note">' +
        '<span class="cyc-note__label">' +
        esc(L("Заметка: ", "Note: ")) +
        "</span>" +
        esc(data.notes) +
        "</div>";
    }

    // Действия: обновить данные / сбросить.
    html +=
      '<div class="cyc-actions">' +
      '<button type="button" class="btn btn--ghost" id="accCycEdit">' +
      esc(L("Обновить данные", "Update data")) +
      "</button>" +
      '<button type="button" class="btn btn--ghost cyc-reset" id="accCycReset">' +
      esc(L("Сбросить", "Reset")) +
      "</button>" +
      "</div>" +
      '<p class="cyc-disclaimer">' +
      esc(
        L(
          "Прогноз ориентировочный и не заменяет консультацию врача.",
          "The forecast is approximate and not a substitute for medical advice."
        )
      ) +
      "</p>";

    return html;
  }

  // Склонение слова «день» для русского (2 дня / 5 дней); для EN всегда day(s).
  function cycDays(n) {
    if (App.lang === "en") return n === 1 ? "day" : "days";
    var m10 = n % 10;
    var m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return "день";
    if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return "дня";
    return "дней";
  }

  // Один факт-прогноз (иконка + подпись + значение).
  function cycFactHtml(iconName, label, value) {
    return (
      '<div class="cyc-fact">' +
      '<span class="cyc-fact__icon" aria-hidden="true">' +
      icon(iconName) +
      "</span>" +
      '<span class="cyc-fact__body">' +
      '<span class="cyc-fact__label">' +
      esc(label) +
      "</span>" +
      '<span class="cyc-fact__value">' +
      esc(value) +
      "</span>" +
      "</span>" +
      "</div>"
    );
  }

  // Один совет под фазу (иконка + заголовок + текст).
  function cycTipHtml(iconName, title, text) {
    return (
      '<div class="cyc-tip">' +
      '<span class="cyc-tip__icon" aria-hidden="true">' +
      icon(iconName) +
      "</span>" +
      '<span class="cyc-tip__body">' +
      '<span class="cyc-tip__title">' +
      esc(title) +
      "</span>" +
      '<span class="cyc-tip__text">' +
      esc(text) +
      "</span>" +
      "</span>" +
      "</div>"
    );
  }

  // Вешает обработчики на кнопки «Обновить данные» и «Сбросить».
  function bindCycleStatus(card) {
    var editBtn = card.querySelector("#accCycEdit");
    if (editBtn) {
      editBtn.addEventListener("click", function () {
        App.haptic("selection");
        // Показываем форму, предзаполненную текущими данными, с кнопкой «Отмена».
        card.innerHTML = '<div class="cyc-body">' + cycleFormHtml(els.cycleData) + "</div>";
        // Добавляем кнопку отмены рядом с сохранением.
        var saveBtn = card.querySelector("#accCycSave");
        if (saveBtn) {
          var cancel = document.createElement("button");
          cancel.type = "button";
          cancel.className = "btn btn--ghost cyc-cancel";
          cancel.id = "accCycCancel";
          cancel.textContent = L("Отмена", "Cancel");
          saveBtn.insertAdjacentElement("afterend", cancel);
        }
        bindCycleForm(card, true);
      });
    }

    var resetBtn = card.querySelector("#accCycReset");
    if (resetBtn) {
      resetBtn.addEventListener("click", function () {
        confirmDanger(L("Удалить данные цикла?", "Delete cycle data?"), function () {
          resetBtn.disabled = true;
          App.haptic("light");
          App.api
            .resetCycle()
            .then(function () {
              els.cycleLoaded = true;
              els.cycleData = { has_data: false };
              renderCycleView(card, els.cycleData);
              App.toast(L("Данные цикла удалены", "Cycle data deleted"));
              App.haptic("success");
            })
            .catch(function (err) {
              resetBtn.disabled = false;
              var reason =
                err && err.message ? err.message : L("Ошибка сети", "Network error");
              App.toast(L("Не удалось удалить: ", "Failed to delete: ") + reason);
              App.haptic("error");
            });
        });
      });
    }
  }

  /* =====================================================================
   *  ФОТО-ПРОГРЕСС
   *  Премиум-раздел: приватные фото прогресса (загрузка, таймлайн, сравнение
   *  «до/после»). Файлы приватны — грузятся авторизованно как blob -> object URL.
   *  Object URL'ы освобождаются при уходе/перерисовке.
   * ===================================================================== */

  // Форматирование ISO-даты -> "DD.MM.YYYY" для подписей фото.
  function progFmtDate(iso) {
    if (!iso || String(iso).length < 10) return "";
    var p = String(iso).split("-");
    return p[2] + "." + p[1] + "." + p[0];
  }

  // Формат веса с точностью до 0.1 кг (без хвостового ".0"), для подписей фото.
  function progWeight(w) {
    var n = Number(w);
    if (!isFinite(n)) return "";
    return String(Math.round(n * 10) / 10);
  }

  // Освобождает все object URL'ы фото (защита от утечек памяти).
  function revokeProgressUrls() {
    if (els && els.progressUrlMap) {
      Object.keys(els.progressUrlMap).forEach(function (k) {
        try {
          URL.revokeObjectURL(els.progressUrlMap[k]);
        } catch (e) {}
      });
      els.progressUrlMap = {};
    }
    if (els && els.progressPreviewUrl) {
      try {
        URL.revokeObjectURL(els.progressPreviewUrl);
      } catch (e) {}
      els.progressPreviewUrl = null;
    }
  }

  // Возвращает (кэшируя) object URL приватного изображения по id.
  function getCachedProgressUrl(id) {
    if (!els) return Promise.reject(new Error("gone"));
    if (!els.progressUrlMap) els.progressUrlMap = {};
    if (els.progressUrlMap[id]) return Promise.resolve(els.progressUrlMap[id]);
    return App.api.getProgressImageUrl(id).then(function (url) {
      if (!els) {
        try {
          URL.revokeObjectURL(url);
        } catch (e) {}
        throw new Error("gone");
      }
      if (!els.progressUrlMap) els.progressUrlMap = {};
      els.progressUrlMap[id] = url;
      return url;
    });
  }

  /**
   * Содержимое раздела «Фото-прогресс». Список загружается один раз за показ
   * (кэш в els.progressData), дальше перерисовки идут из кэша.
   */
  function renderProgress() {
    var card = secBody("progress");
    if (!card) return;

    if (els.progressLoaded) {
      renderProgressView(card, els.progressData || []);
      return;
    }
    if (els.progressFetching) return;

    els.progressFetching = true;
    card.innerHTML = '<div class="skeleton skeleton--block"></div>';

    App.api
      .getProgressList()
      .then(function (res) {
        if (!els) return;
        els.progressFetching = false;
        els.progressLoaded = true;
        els.progressData = (res && res.items) || [];
        var box = secBody("progress");
        if (box) renderProgressView(box, els.progressData);
      })
      .catch(function (err) {
        if (!els) return;
        els.progressFetching = false;
        var box = secBody("progress");
        if (!box) return;
        var reason =
          err && err.message ? err.message : L("Ошибка сети", "Network error");
        box.innerHTML =
          '<div class="cyc-error">' +
          "<p>" +
          esc(L("Не удалось загрузить фото.", "Failed to load photos.")) +
          "</p>" +
          '<p class="cyc-error__msg">' +
          esc(reason) +
          "</p>" +
          '<button type="button" class="btn btn--ghost" id="accProgRetry">' +
          esc(L("Повторить", "Retry")) +
          "</button></div>";
        var retry = box.querySelector("#accProgRetry");
        if (retry) {
          retry.addEventListener("click", function () {
            els.progressLoaded = false;
            renderProgress();
          });
        }
      });
  }

  /**
   * Основное содержимое раздела: приватная пометка, кнопка добавления,
   * таймлайн миниатюр и (при >=2 фото) блок сравнения «до/после».
   */
  function renderProgressView(card, items) {
    if (!card) return;
    items = items || [];

    var html =
      '<p class="prog-privacy">' +
      icon("lock", { size: 16 }) +
      "<span>" +
      esc(
        L("Фото приватны и видны только вам.", "Photos are private and visible only to you.")
      ) +
      "</span>" +
      "</p>" +
      '<button type="button" class="btn btn--cta prog-add" id="accProgAdd">' +
      icon("plus") +
      "<span>" +
      esc(L("Добавить фото", "Add photo")) +
      "</span>" +
      "</button>" +
      '<input type="file" accept="image/*" id="accProgFile" hidden>' +
      '<div class="prog-upload" id="accProgForm"></div>';

    if (!items.length) {
      html +=
        '<div class="prog-empty">' +
        '<div class="prog-empty__icon" aria-hidden="true">' +
        icon("camera", { size: 24 }) +
        "</div>" +
        '<div class="prog-empty__text">' +
        esc(
          L(
            "Пока нет фото. Добавьте первое — так удобно отслеживать изменения.",
            "No photos yet. Add your first one — a handy way to track changes."
          )
        ) +
        "</div></div>";
    } else {
      html += progressTimelineHtml(items);
      if (items.length >= 2) {
        html += progressCompareHtml(items);
      }
    }

    card.innerHTML = html;
    bindProgress(card, items);
    loadProgressThumbs(card, items);
    if (items.length >= 2) setupProgressCompare(card, items);
  }

  // HTML таймлайна миниатюр (img подгружается асинхронно как blob).
  function progressTimelineHtml(items) {
    var cells = "";
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var meta = progFmtDate(it.date);
      if (it.weight != null && it.weight !== "") {
        meta += " · " + progWeight(it.weight) + " " + L("кг", "kg");
      }
      cells +=
        '<div class="prog-item" data-id="' +
        esc(it.id) +
        '">' +
        '<div class="prog-item__frame">' +
        '<img class="prog-item__img" alt="" data-id="' +
        esc(it.id) +
        '">' +
        '<button type="button" class="prog-item__del" data-id="' +
        esc(it.id) +
        '" ' +
        'aria-label="' +
        esc(L("Удалить", "Delete")) +
        '">' +
        icon("close", { size: 18 }) +
        "</button>" +
        "</div>" +
        '<div class="prog-item__meta">' +
        esc(meta) +
        "</div>" +
        "</div>";
    }
    return (
      '<div class="prog-timeline-title">' +
      esc(L("Таймлайн", "Timeline")) +
      "</div>" +
      '<div class="prog-timeline">' +
      cells +
      "</div>"
    );
  }

  // Асинхронно проставляет src миниатюрам (из кэша object URL).
  function loadProgressThumbs(card, items) {
    items.forEach(function (it) {
      var img = card.querySelector('.prog-item__img[data-id="' + it.id + '"]');
      if (!img) return;
      getCachedProgressUrl(it.id)
        .then(function (url) {
          // Карточка ещё жива и это тот же элемент?
          if (img && img.isConnected) img.src = url;
        })
        .catch(function () {
          /* фото не загрузилось — оставляем пустую рамку */
        });
    });
  }

  // HTML блока сравнения «до/после» (два селектора + слайдер-шторка).
  function progressCompareHtml(items) {
    var opts = "";
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var label = progFmtDate(it.date);
      if (it.weight != null && it.weight !== "") {
        label += " · " + progWeight(it.weight) + " " + L("кг", "kg");
      }
      opts += '<option value="' + esc(it.id) + '">' + esc(label) + "</option>";
    }
    return (
      '<div class="prog-compare-title">' +
      esc(L("Сравнение «до/после»", "Before / after")) +
      "</div>" +
      '<div class="prog-compare">' +
      '<div class="prog-compare__stage" id="accProgStage">' +
      '<img class="prog-compare__img prog-compare__before" id="accProgBeforeImg" alt="">' +
      '<img class="prog-compare__img prog-compare__after" id="accProgAfterImg" alt="">' +
      '<div class="prog-compare__handle" id="accProgHandle"></div>' +
      "</div>" +
      '<input type="range" min="0" max="100" value="50" class="prog-compare__range" id="accProgRange">' +
      '<div class="prog-compare__selects">' +
      '<label class="prog-compare__sel">' +
      "<span>" +
      esc(L("До", "Before")) +
      "</span>" +
      '<select class="field prog-compare__select" id="accProgBefore">' +
      opts +
      "</select>" +
      "</label>" +
      '<label class="prog-compare__sel">' +
      "<span>" +
      esc(L("После", "After")) +
      "</span>" +
      '<select class="field prog-compare__select" id="accProgAfter">' +
      opts +
      "</select>" +
      "</label>" +
      "</div>" +
      "</div>"
    );
  }

  // Инициализирует сравнение: дефолт до=первое, после=последнее; слайдер + селекты.
  function setupProgressCompare(card, items) {
    var beforeSel = card.querySelector("#accProgBefore");
    var afterSel = card.querySelector("#accProgAfter");
    var beforeImg = card.querySelector("#accProgBeforeImg");
    var afterImg = card.querySelector("#accProgAfterImg");
    var range = card.querySelector("#accProgRange");
    var handle = card.querySelector("#accProgHandle");
    if (!beforeSel || !afterSel || !beforeImg || !afterImg || !range || !handle) return;

    // По умолчанию сравниваем самое раннее фото с самым поздним.
    beforeSel.value = String(items[0].id);
    afterSel.value = String(items[items.length - 1].id);

    function setImg(imgEl, id) {
      getCachedProgressUrl(id)
        .then(function (url) {
          if (imgEl && imgEl.isConnected) imgEl.src = url;
        })
        .catch(function () {});
    }

    function applyClip() {
      var v = Number(range.value);
      // Показываем левые v% «после»-фото поверх «до»-фото.
      afterImg.style.clipPath = "inset(0 " + (100 - v) + "% 0 0)";
      afterImg.style.webkitClipPath = "inset(0 " + (100 - v) + "% 0 0)";
      handle.style.left = v + "%";
    }

    setImg(beforeImg, items[0].id);
    setImg(afterImg, items[items.length - 1].id);
    applyClip();

    range.addEventListener("input", applyClip);
    beforeSel.addEventListener("change", function () {
      setImg(beforeImg, beforeSel.value);
    });
    afterSel.addEventListener("change", function () {
      setImg(afterImg, afterSel.value);
    });
  }

  // Вешает обработчики: добавление фото, форму загрузки, удаление.
  function bindProgress(card, items) {
    var addBtn = card.querySelector("#accProgAdd");
    var fileInput = card.querySelector("#accProgFile");
    if (addBtn && fileInput) {
      addBtn.addEventListener("click", function () {
        App.haptic("selection");
        fileInput.value = ""; // позволяем повторно выбрать тот же файл
        fileInput.click();
      });
      fileInput.addEventListener("change", function () {
        var f = fileInput.files && fileInput.files[0];
        if (f) openProgressUploadForm(card, f);
      });
    }

    // Удаление фото (делегирование по кнопкам-крестикам).
    var timeline = card.querySelector(".prog-timeline");
    if (timeline) {
      timeline.addEventListener("click", function (e) {
        var btn = e.target.closest(".prog-item__del");
        if (!btn) return;
        var id = btn.getAttribute("data-id");
        if (!id) return;
        confirmDanger(L("Удалить это фото?", "Delete this photo?"), function () {
          onProgressDelete(card, id);
        });
      });
    }
  }

  // Показывает инлайн-форму загрузки: превью выбранного файла + вес + дата.
  function openProgressUploadForm(card, file) {
    var form = card.querySelector("#accProgForm");
    if (!form) return;

    // Готовим превью выбранного файла (локальный object URL — освобождаем при закрытии).
    if (els.progressPreviewUrl) {
      try {
        URL.revokeObjectURL(els.progressPreviewUrl);
      } catch (e) {}
    }
    els.progressPreviewUrl = URL.createObjectURL(file);
    els.progressPendingFile = file;

    form.innerHTML =
      '<div class="prog-upload__preview">' +
      '<img src="' +
      els.progressPreviewUrl +
      '" alt="" class="prog-upload__img">' +
      "</div>" +
      '<label class="cyc-field">' +
      '<span class="cyc-field__label">' +
      esc(L("Дата", "Date")) +
      "</span>" +
      '<input type="date" class="field prog-upload__input" id="accProgDate" max="' +
      cycToday() +
      '" value="' +
      cycToday() +
      '">' +
      "</label>" +
      '<label class="cyc-field">' +
      '<span class="cyc-field__label">' +
      esc(L("Вес, кг (необязательно)", "Weight, kg (optional)")) +
      "</span>" +
      '<input type="number" inputmode="decimal" step="0.1" min="0" class="field prog-upload__input" ' +
      'id="accProgWeight" placeholder="' +
      esc(L("напр. 72.5", "e.g. 72.5")) +
      '">' +
      "</label>" +
      '<div class="prog-upload__actions">' +
      '<button type="button" class="btn btn--cta" id="accProgSave">' +
      esc(L("Загрузить", "Upload")) +
      "</button>" +
      '<button type="button" class="btn btn--ghost" id="accProgCancel">' +
      esc(L("Отмена", "Cancel")) +
      "</button>" +
      "</div>";

    var save = form.querySelector("#accProgSave");
    var cancel = form.querySelector("#accProgCancel");
    if (save) {
      save.addEventListener("click", function () {
        onProgressUpload(card, form);
      });
    }
    if (cancel) {
      cancel.addEventListener("click", function () {
        closeProgressUploadForm(card);
      });
    }
  }

  // Закрывает форму загрузки и освобождает превью-URL.
  function closeProgressUploadForm(card) {
    var form = card.querySelector("#accProgForm");
    if (form) form.innerHTML = "";
    if (els && els.progressPreviewUrl) {
      try {
        URL.revokeObjectURL(els.progressPreviewUrl);
      } catch (e) {}
      els.progressPreviewUrl = null;
    }
    if (els) els.progressPendingFile = null;
  }

  // Отправляет выбранный файл на сервер, затем обновляет список.
  function onProgressUpload(card, form) {
    var file = els.progressPendingFile;
    if (!file) return;
    var dateEl = form.querySelector("#accProgDate");
    var weightEl = form.querySelector("#accProgWeight");
    var saveBtn = form.querySelector("#accProgSave");

    var date = dateEl ? String(dateEl.value || "").trim() : "";
    var weight = weightEl ? String(weightEl.value || "").trim() : "";

    if (saveBtn) saveBtn.disabled = true;
    App.haptic("light");

    App.api
      .uploadProgress(file, date, weight)
      .then(function (photo) {
        closeProgressUploadForm(card);
        // Добавляем новое фото в кэш данных и перерисовываем (без полного refetch).
        if (!Array.isArray(els.progressData)) els.progressData = [];
        els.progressData.push(photo);
        // Пересортировка по дате по возрастанию (как на бэкенде).
        els.progressData.sort(function (a, b) {
          if (a.date === b.date) return (a.id || 0) - (b.id || 0);
          return a.date < b.date ? -1 : 1;
        });
        renderProgressView(card, els.progressData);
        App.toast(L("Фото добавлено", "Photo added"));
        App.haptic("success");
      })
      .catch(function (err) {
        if (saveBtn) saveBtn.disabled = false;
        var reason =
          err && err.message ? err.message : L("Ошибка сети", "Network error");
        App.toast(L("Не удалось загрузить: ", "Failed to upload: ") + reason);
        App.haptic("error");
      });
  }

  // Удаляет фото на сервере и обновляет список/кэш.
  function onProgressDelete(card, id) {
    App.haptic("light");
    App.api
      .deleteProgress(id)
      .then(function () {
        // Освобождаем object URL удалённого фото.
        if (els.progressUrlMap && els.progressUrlMap[id]) {
          try {
            URL.revokeObjectURL(els.progressUrlMap[id]);
          } catch (e) {}
          delete els.progressUrlMap[id];
        }
        els.progressData = (els.progressData || []).filter(function (p) {
          return String(p.id) !== String(id);
        });
        renderProgressView(card, els.progressData);
        App.toast(L("Фото удалено", "Photo deleted"));
        App.haptic("success");
      })
      .catch(function (err) {
        var reason =
          err && err.message ? err.message : L("Ошибка сети", "Network error");
        App.toast(L("Не удалось удалить: ", "Failed to delete: ") + reason);
        App.haptic("error");
      });
  }

  /* =====================================================================
   *  УВЕДОМЛЕНИЯ — ОДИН ЭКРАН
   *
   *  Раньше напоминания жили в четырёх несовместимых видах на четырёх экранах:
   *  еда — в дневнике, тренировка — в удалённом разделе «Тренировки», добавки —
   *  в разделе добавок, вечерняя сводка — в листе настроек аккаунта. Здесь они
   *  собраны в ОДИН список одинаковых строк «иконка · название · тумблер».
   *
   *  Напоминание о тренировке НЕ редактируется здесь: им владеет анкета
   *  тренера (она же создаёт TrainingReminder с днями недели). Показываем его
   *  состоянием только для чтения со ссылкой в настройки тренера.
   *
   *  Недельный разбор тоже только для чтения: бэкенд шлёт его премиум-
   *  пользователям вместе с вечерней сводкой (тот же тумблер и то же время),
   *  отдельной настройки у него нет — и обещать её было бы враньём.
   * ===================================================================== */

  /**
   * Безопасно приводит значение времени к строке "HH:MM" для поля ввода.
   */
  function timeValue(v) {
    if (v == null) return "";
    var s = String(v).trim();
    // Сервер может вернуть "HH:MM:SS" — оставляем первые 5 символов.
    if (s.length >= 5) return s.slice(0, 5);
    return s;
  }

  /** Локализованные короткие названия дней недели (0=Пн … 6=Вс). */
  function weekdayShort(idx) {
    var ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
    var en = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    return L(ru[idx] || "", en[idx] || "");
  }

  /**
   * Загружает настройки уведомлений и напоминания о тренировках (последние —
   * best-effort: раздел должен открыться, даже если тренер недоступен).
   */
  function loadNotifications() {
    var box = secBody("notify");
    if (!box) return;
    box.innerHTML = '<div class="skeleton skeleton--block"></div>';

    var settingsP = App.api.getNotificationSettings();
    var trainingP = App.api.getTrainingReminders().catch(function () {
      return null;
    });

    Promise.all([settingsP, trainingP])
      .then(function (res) {
        if (!els) return;
        els.notifData = res[0] || {};
        els.trainingRems = (res[1] && res[1].items) || [];
        renderNotifications();
      })
      .catch(function (err) {
        var target = secBody("notify");
        if (!target) return;
        target.innerHTML =
          '<div class="acc-error">' +
          "<p>" +
          esc(
            L(
              "Не удалось загрузить настройки уведомлений.",
              "Failed to load notification settings."
            )
          ) +
          "</p>" +
          '<p class="acc-error__msg">' +
          esc(err && err.message ? err.message : L("Ошибка сети", "Network error")) +
          "</p>" +
          '<button type="button" class="btn btn--ghost" id="accNotifRetry">' +
          esc(L("Повторить", "Retry")) +
          "</button>" +
          "</div>";
        var retry = target.querySelector("#accNotifRetry");
        if (retry) retry.addEventListener("click", loadNotifications);
      });
  }

  /**
   * Разметка одной строки уведомления.
   * @param {object} o {icon, title, hint, control} — control это HTML тумблера
   *   или ссылки справа.
   */
  function notifRowHtml(o) {
    return (
      '<div class="acc-nrow">' +
      '<span class="acc-nrow__icon" aria-hidden="true">' +
      icon(o.icon) +
      "</span>" +
      '<span class="acc-nrow__text">' +
      '<span class="acc-nrow__title">' +
      esc(o.title) +
      "</span>" +
      (o.hint ? '<span class="acc-nrow__hint">' + esc(o.hint) + "</span>" : "") +
      "</span>" +
      (o.control || "") +
      "</div>"
    );
  }

  /** Тумблер-переключатель строки (роль switch, зона нажатия 44px). */
  function switchHtml(id, on, label) {
    return (
      '<button type="button" class="acc-switch' +
      (on ? " acc-switch--on" : "") +
      '" id="' +
      id +
      '" role="switch" aria-checked="' +
      (on ? "true" : "false") +
      '" aria-label="' +
      esc(label) +
      '">' +
      '<span class="acc-switch__track" aria-hidden="true">' +
      '<span class="acc-switch__knob"></span>' +
      "</span>" +
      "</button>"
    );
  }

  /** Кнопка-стрелка «перейти» в конце строки. */
  function goHtml(id, label) {
    return (
      '<button type="button" class="acc-nrow__go" id="' +
      id +
      '" aria-label="' +
      esc(label) +
      '">' +
      icon("chevron", { size: 18 }) +
      "</button>"
    );
  }

  /** Текущий список времён приёмов пищи из настроек (с фолбэком). */
  function mealTimesOf(s) {
    var out = [];
    if (s && s.meal_times && s.meal_times.length) {
      for (var i = 0; i < s.meal_times.length; i++) {
        var t = timeValue(s.meal_times[i]);
        if (t) out.push(t);
      }
    }
    if (!out.length && s) {
      // Старые профили хранят фиксированные завтрак/обед/ужин.
      [s.breakfast_time, s.lunch_time, s.dinner_time].forEach(function (t) {
        var v = timeValue(t);
        if (v) out.push(v);
      });
    }
    return out.length ? out : DEFAULT_MEAL_TIMES.slice();
  }

  /**
   * Отрисовывает единый экран уведомлений по кэшу els.notifData.
   */
  function renderNotifications() {
    var box = secBody("notify");
    if (!box) return;

    var s = els.notifData || {};
    var premium = App.isPremium();

    // ---- Еда ----
    var mealsOn = !!s.meal_reminder_enabled;
    var times = mealTimesOf(s);
    var mealsHint = mealsOn
      ? times.join(" · ")
      : L("Выключено", "Off");

    var mealTimesHtml = "";
    if (mealsOn) {
      var rows = times
        .map(function (t, i) {
          return (
            '<div class="acc-time">' +
            '<input type="time" class="field__input acc-time__input" value="' +
            esc(t) +
            '" data-meal-time="' +
            i +
            '">' +
            '<button type="button" class="acc-time__del" data-meal-del="' +
            i +
            '" aria-label="' +
            esc(L("Убрать время", "Remove time")) +
            '">' +
            icon("close", { size: 18 }) +
            "</button>" +
            "</div>"
          );
        })
        .join("");
      mealTimesHtml =
        '<div class="acc-nrow__extra" id="accMealTimes">' +
        rows +
        '<button type="button" class="btn btn--ghost acc-time__add" id="accMealAdd">' +
        icon("plus") +
        "<span>" +
        esc(L("Добавить время", "Add time")) +
        "</span>" +
        "</button>" +
        "</div>";
    }

    // ---- Тренировка (только чтение: владелец — анкета тренера) ----
    var rems = els.trainingRems || [];
    var activeRem = null;
    for (var i = 0; i < rems.length; i++) {
      if (rems[i] && rems[i].enabled) {
        activeRem = rems[i];
        break;
      }
    }
    var trainHint;
    if (activeRem) {
      var days = (activeRem.weekdays || [])
        .slice()
        .sort(function (a, b) {
          return a - b;
        })
        .map(weekdayShort)
        .join(", ");
      trainHint =
        (days ? days + " · " : "") + timeValue(activeRem.time);
    } else {
      trainHint = L("Выключено", "Off");
    }

    // ---- Вечерняя сводка ----
    var summaryOn = !!s.daily_summary_enabled;
    var summaryTime = timeValue(s.summary_time) || DEFAULT_SUMMARY_TIME;

    var summaryExtra = summaryOn
      ? '<div class="acc-nrow__extra">' +
        '<label class="field acc-time__field">' +
        '<span class="field__label">' +
        esc(L("Время сводки", "Summary time")) +
        "</span>" +
        '<input class="field__input" type="time" id="accSummaryTime" value="' +
        esc(summaryTime) +
        '">' +
        "</label>" +
        "</div>"
      : "";

    // ---- Недельный разбор (только чтение) ----
    var weekHint;
    if (!premium) {
      weekHint = L("По подписке", "With subscription");
    } else if (summaryOn) {
      weekHint = L(
        "Раз в неделю в " + summaryTime,
        "Once a week at " + summaryTime
      );
    } else {
      weekHint = L(
        "Включается вместе с вечерней сводкой",
        "Comes together with the evening summary"
      );
    }

    box.innerHTML =
      '<div class="acc-nlist">' +
      // Еда.
      notifRowHtml({
        icon: "utensils",
        title: L("Напоминания о еде", "Meal reminders"),
        hint: mealsHint,
        control: switchHtml(
          "accNotifMeals",
          mealsOn,
          L("Напоминания о еде", "Meal reminders")
        )
      }) +
      mealTimesHtml +
      // Тренировка (только чтение).
      notifRowHtml({
        icon: "dumbbell",
        title: L("Напоминание о тренировке", "Workout reminder"),
        hint:
          trainHint +
          " · " +
          L("настраивается у тренера", "set up in the trainer"),
        control: goHtml(
          "accNotifTrainer",
          L("Открыть настройки тренера", "Open trainer settings")
        )
      }) +
      // Добавки.
      notifRowHtml({
        icon: "pill",
        title: L("Напоминания о добавках", "Supplement reminders"),
        hint: s.supplement_reminder_enabled
          ? L("Время и состав — в разделе «Добавки»", "Times and items — in Supplements")
          : L("Выключено", "Off"),
        control: switchHtml(
          "accNotifSupp",
          !!s.supplement_reminder_enabled,
          L("Напоминания о добавках", "Supplement reminders")
        )
      }) +
      // Вечерняя сводка.
      notifRowHtml({
        icon: "moon",
        title: L("Вечерняя сводка", "Evening summary"),
        hint: summaryOn
          ? L("Каждый день в " + summaryTime, "Every day at " + summaryTime)
          : L("Итоги дня: калории и БЖУ", "Daily recap: calories and macros"),
        control: switchHtml(
          "accNotifSummary",
          summaryOn,
          L("Вечерняя сводка", "Evening summary")
        )
      }) +
      summaryExtra +
      // Недельный разбор (только чтение).
      notifRowHtml({
        icon: "chartBar",
        title: L("Недельный разбор", "Weekly recap"),
        hint: weekHint
      }) +
      "</div>";

    bindNotifications(box);
  }

  /** Навешивает обработчики на единый экран уведомлений. */
  function bindNotifications(box) {
    // Еда: тумблер.
    var meals = box.querySelector("#accNotifMeals");
    if (meals) {
      meals.addEventListener("click", function () {
        var on = !meals.classList.contains("acc-switch--on");
        saveNotif(
          on
            ? { meal_reminder_enabled: true, meal_times: collectMealTimes(box) }
            : { meal_reminder_enabled: false }
        );
      });
    }

    // Еда: правка времён (сохраняем по change — отдельной кнопки нет).
    var timesBox = box.querySelector("#accMealTimes");
    if (timesBox) {
      timesBox.addEventListener("change", function (e) {
        if (!e.target.hasAttribute("data-meal-time")) return;
        saveNotif({
          meal_reminder_enabled: true,
          meal_times: collectMealTimes(box)
        });
      });
      timesBox.addEventListener("click", function (e) {
        var del = e.target.closest("[data-meal-del]");
        if (del) {
          var row = del.closest(".acc-time");
          if (row && row.parentNode) row.parentNode.removeChild(row);
          saveNotif({
            meal_reminder_enabled: true,
            meal_times: collectMealTimes(box)
          });
          return;
        }
        var add = e.target.closest("#accMealAdd");
        if (add) {
          var times = collectMealTimes(box);
          // Новое время добавляем на час позже последнего — так его почти
          // никогда не приходится править вручную.
          times.push(nextMealTime(times));
          saveNotif({ meal_reminder_enabled: true, meal_times: times });
        }
      });
    }

    // Тренировка: только переход в настройки тренера.
    var trainer = box.querySelector("#accNotifTrainer");
    if (trainer) {
      trainer.addEventListener("click", function () {
        App.haptic("light");
        // Анкета тренера в режиме редактирования — именно она владеет
        // напоминанием о тренировке (днями недели и временем).
        App.state.trainerEdit = true;
        App.navigate("trainer-onboarding");
      });
    }

    // Добавки: тумблер общего флага.
    var supp = box.querySelector("#accNotifSupp");
    if (supp) {
      supp.addEventListener("click", function () {
        var on = !supp.classList.contains("acc-switch--on");
        saveNotif({ supplement_reminder_enabled: on });
      });
    }

    // Вечерняя сводка: тумблер + время.
    var summary = box.querySelector("#accNotifSummary");
    if (summary) {
      summary.addEventListener("click", function () {
        var on = !summary.classList.contains("acc-switch--on");
        var payload = { daily_summary_enabled: on };
        if (on) {
          var t = box.querySelector("#accSummaryTime");
          var val = t ? (t.value || "").trim() : "";
          payload.summary_time = val || DEFAULT_SUMMARY_TIME;
        }
        saveNotif(payload);
      });
    }
    var summaryTime = box.querySelector("#accSummaryTime");
    if (summaryTime) {
      summaryTime.addEventListener("change", function () {
        var val = (summaryTime.value || "").trim();
        if (!val) return;
        saveNotif({ daily_summary_enabled: true, summary_time: val });
      });
    }
  }

  /** Считывает времена приёмов пищи из полей ввода. */
  function collectMealTimes(box) {
    var out = [];
    var inputs = box.querySelectorAll("[data-meal-time]");
    for (var i = 0; i < inputs.length; i++) {
      var v = timeValue(inputs[i].value);
      if (v) out.push(v);
    }
    return out.length ? out : DEFAULT_MEAL_TIMES.slice();
  }

  /** Следующее разумное время приёма пищи: на час позже последнего. */
  function nextMealTime(times) {
    if (!times.length) return DEFAULT_MEAL_TIMES[0];
    var last = times[times.length - 1];
    var h = parseInt(last.split(":")[0], 10);
    var m = last.split(":")[1] || "00";
    if (!isFinite(h)) return DEFAULT_MEAL_TIMES[0];
    h = (h + 1) % 24;
    return (h < 10 ? "0" + h : String(h)) + ":" + m;
  }

  /**
   * Сохраняет частичные настройки уведомлений и перерисовывает экран
   * ответом сервера (он же источник правды по остальным полям).
   */
  function saveNotif(payload) {
    App.haptic("selection");
    App.api
      .saveNotificationSettings(payload)
      .then(function (settings) {
        if (!els) return;
        if (settings && typeof settings === "object") {
          els.notifData = settings;
        } else {
          // Сервер не вернул объект — накатываем отправленные поля на кэш,
          // чтобы экран не «откатился» к прежнему состоянию.
          var cur = els.notifData || {};
          Object.keys(payload).forEach(function (k) {
            cur[k] = payload[k];
          });
          els.notifData = cur;
        }
        renderNotifications();
        App.haptic("success");
      })
      .catch(function (err) {
        App.haptic("error");
        var reason = err && err.message ? err.message : L("ошибка", "error");
        App.toast(
          L("Не удалось сохранить: " + reason, "Failed to save: " + reason)
        );
        // Возвращаем экран к последнему подтверждённому серверу состоянию.
        renderNotifications();
      });
  }

  /* =====================================================================
   *  ИСТОРИЯ ПО ДНЯМ — КАЛЕНДАРЬ
   *  Календарь месяца (Пн-первый). Каждый прошедший день окрашивается по цели:
   *  зелёный — цель достигнута (ккал>0 и <=goal), серый — выше цели,
   *  нейтральный — нет данных. Если цель не задана — все дни нейтральны.
   *  Источник: App.api.getHistory() -> { goal, days:[{date,total_calories}] }.
   * ===================================================================== */

  // Локализованные названия месяцев (для заголовка календаря).
  var MONTH_NAMES_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
  ];
  var MONTH_NAMES_EN = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
  ];
  // Сокращения дней недели (Пн-первый).
  var DOW_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  var DOW_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  // Двузначное дополнение для сборки ISO-дат.
  function pad2(n) {
    n = String(n);
    return n.length < 2 ? "0" + n : n;
  }

  /**
   * Загружает историю и отрисовывает календарь текущего месяца.
   */
  function loadHistory() {
    var box = secBody("history");
    if (!box) return;
    box.innerHTML = '<div class="skeleton skeleton--block"></div>';

    App.api
      .getHistory()
      .then(function (res) {
        renderHistoryCalendar(res || {});
      })
      .catch(function (err) {
        var target = secBody("history");
        if (!target) return;
        target.innerHTML =
          '<div class="acc-error">' +
          "<p>" +
          esc(L("Не удалось загрузить историю.", "Failed to load history.")) +
          "</p>" +
          '<p class="acc-error__msg">' +
          esc(err && err.message ? err.message : L("Ошибка сети", "Network error")) +
          "</p>" +
          '<button type="button" class="btn btn--ghost" id="accHistRetry">' +
          esc(L("Повторить", "Retry")) +
          "</button>" +
          "</div>";
        var retry = target.querySelector("#accHistRetry");
        if (retry) retry.addEventListener("click", loadHistory);
      });
  }

  /**
   * Отрисовывает календарь текущего месяца с подсветкой дней по цели.
   * @param {Object} res — объект HistoryOut { goal, days:[{date,total_calories}] }.
   */
  function renderHistoryCalendar(res) {
    // Кэшируем данные истории и стартуем с текущего месяца. Стрелки листают
    // месяцы без повторного запроса (данные — последние ~30 дней с сервера).
    if (res) els.histRes = res;
    var now = new Date();
    els.histView = { year: now.getFullYear(), month: now.getMonth() };
    drawHistoryCalendar();
  }

  /**
   * Сдвигает просматриваемый месяц истории на delta и перерисовывает календарь.
   */
  function shiftHistoryMonth(delta) {
    if (!els || !els.histView) return;
    var d = new Date(els.histView.year, els.histView.month + delta, 1);
    els.histView = { year: d.getFullYear(), month: d.getMonth() };
    drawHistoryCalendar();
  }

  /**
   * Рисует календарь истории за просматриваемый месяц (els.histView) по
   * кэшированным данным (els.histRes).
   */
  function drawHistoryCalendar() {
    var box = secBody("history");
    if (!box || !els.histView) return;

    var res = els.histRes || {};
    var days = res.days || [];
    var goal = res.goal != null ? Number(res.goal) : null;

    // Карта данных по датам: "YYYY-MM-DD" -> total_calories.
    var byDate = {};
    days.forEach(function (d) {
      if (d && d.date) byDate[d.date] = Number(d.total_calories) || 0;
    });

    var year = els.histView.year;
    var month = els.histView.month; // 0..11

    var now = new Date();
    var isCurMonth = year === now.getFullYear() && month === now.getMonth();
    var todayIso =
      now.getFullYear() + "-" + pad2(now.getMonth() + 1) + "-" + pad2(now.getDate());

    var monthName =
      (App.lang === "en" ? MONTH_NAMES_EN : MONTH_NAMES_RU)[month] + " " + year;

    // Заголовок с навигацией по месяцам; «вперёд» отключаем в текущем месяце.
    var head =
      '<div class="hist-cal__head">' +
      '<button type="button" class="hist-cal__nav" data-hist-nav="prev" ' +
      'aria-label="' +
      esc(L("Предыдущий месяц", "Previous month")) +
      '">' +
      icon("chevron", { size: 18, rotate: 180 }) +
      "</button>" +
      '<span class="hist-cal__title">' +
      esc(monthName) +
      "</span>" +
      '<button type="button" class="hist-cal__nav" data-hist-nav="next"' +
      (isCurMonth ? " disabled" : "") +
      " " +
      'aria-label="' +
      esc(L("Следующий месяц", "Next month")) +
      '">' +
      icon("chevron", { size: 18 }) +
      "</button>" +
      "</div>";

    // Строка заголовков дней недели (Пн-первый).
    var dow = App.lang === "en" ? DOW_EN : DOW_RU;
    var dowRow = "";
    dow.forEach(function (name) {
      dowRow += '<div class="hist-cal__dow">' + esc(name) + "</div>";
    });

    // Первый день месяца: индекс дня недели (0=Пн ... 6=Вс).
    var firstDow = new Date(year, month, 1).getDay(); // 0=Вс..6=Сб
    var lead = (firstDow + 6) % 7; // сдвиг к Пн-первому
    var daysInMonth = new Date(year, month + 1, 0).getDate();

    var cells = "";
    // Пустые ячейки-заполнители до первого числа.
    for (var i = 0; i < lead; i++) {
      cells += '<div class="hist-cal__cell hist-cal__cell--empty"></div>';
    }
    // Числа месяца.
    for (var day = 1; day <= daysInMonth; day++) {
      var iso = year + "-" + pad2(month + 1) + "-" + pad2(day);
      var cls = "hist-cal__cell";
      var isFuture = iso > todayIso;

      if (isFuture) {
        // Будущие дни — нейтральные/пустые.
        cls += " hist-cal__cell--empty";
      } else if (byDate.hasOwnProperty(iso)) {
        var kcal = byDate[iso];
        if (kcal > 0 && goal != null && goal > 0) {
          // Цель достигнута (в пределах) или превышена.
          cls += kcal <= goal ? " hist-cal__cell--met" : " hist-cal__cell--miss";
        } else {
          // Есть данные, но цель не задана (или ккал=0) — нейтрально.
          cls += " hist-cal__cell--empty";
        }
      } else {
        // Нет данных за этот день.
        cls += " hist-cal__cell--empty";
      }

      cells += '<div class="' + cls + '">' + day + "</div>";
    }

    var grid = '<div class="hist-cal__grid">' + dowRow + cells + "</div>";

    // Легенда: зелёный = цель достигнута, серый = не достигнута.
    var legend =
      '<div class="hist-cal__legend">' +
      '<span class="hist-cal__legend-item">' +
      '<span class="hist-cal__cell hist-cal__cell--met" aria-hidden="true"></span>' +
      esc(L("цель достигнута", "goal met")) +
      "</span>" +
      '<span class="hist-cal__legend-item">' +
      '<span class="hist-cal__cell hist-cal__cell--miss" aria-hidden="true"></span>' +
      esc(L("не достигнута", "not met")) +
      "</span>" +
      "</div>";

    box.innerHTML = '<div class="hist-cal">' + head + grid + legend + "</div>";

    // Навигация по месяцам: листаем без повторного запроса истории.
    var navs = box.querySelectorAll(".hist-cal__nav");
    for (var n = 0; n < navs.length; n++) {
      navs[n].addEventListener("click", function (ev) {
        var btn = ev.currentTarget;
        if (btn.disabled) return;
        App.haptic("selection");
        shiftHistoryMonth(btn.getAttribute("data-hist-nav") === "next" ? 1 : -1);
      });
    }
  }

  /**
   * Преобразует ISO-дату "YYYY-MM-DD" в короткий формат "ДД.ММ".
   */
  function formatDate(iso) {
    if (!iso || typeof iso !== "string") return String(iso || "");
    var parts = iso.split("-");
    if (parts.length === 3) {
      return parts[2] + "." + parts[1];
    }
    return iso;
  }

  /**
   * Обновляет текст и подсветку карточки подписки по текущему App.subscription.
   * Безопасно к отсутствию элементов (если страница уже скрыта).
   */
  function refreshSubCard() {
    if (!els) return;
    var sub = subscriptionStatus();
    if (els.subStatus) {
      els.subStatus.textContent = sub.text;
    }
    if (els.subCard) {
      els.subCard.classList.toggle("acc-sub-card--premium", sub.premium);
    }
    // Подпись и вид кнопки следуют за статусом: активной подписке — белый
    // контур «Управлять», остальным — оранжевое «Оформить».
    if (els.subAction) {
      els.subAction.textContent = sub.action;
      els.subAction.classList.toggle("btn--cta", !sub.premium);
      els.subAction.classList.toggle("acc-sub-card__action--ghost", sub.premium);
    }
  }

  /**
   * Находит и кэширует ссылки на элементы формы внутри представления.
   */
  function bindElements(viewEl) {
    els = {
      viewEl: viewEl,
      subCard: viewEl.querySelector("#accSubCard"),
      subStatus: viewEl.querySelector("#accSubStatus"),
      subAction: viewEl.querySelector("#accSubAction"),
      form: viewEl.querySelector("#accForm"),
      weight: viewEl.querySelector("#accWeight"),
      height: viewEl.querySelector("#accHeight"),
      age: viewEl.querySelector("#accAge"),
      gender: viewEl.querySelector("#accGender"),
      activity: viewEl.querySelector("#accActivity"),
      dietGoal: viewEl.querySelector("#accDietGoal"),
      goal: viewEl.querySelector("#accGoal"),
      goalMacros: viewEl.querySelector("#accGoalMacros"),
      targetProt: viewEl.querySelector("#accTargetProt"),
      targetFat: viewEl.querySelector("#accTargetFat"),
      targetCarb: viewEl.querySelector("#accTargetCarb"),
      calcBtn: viewEl.querySelector("#accCalcBtn"),
      saveBtn: viewEl.querySelector("#accSaveBtn"),
      // Кэш данных разделов на время показа страницы.
      cycleLoaded: false,
      cycleFetching: false,
      cycleData: null,
      progressLoaded: false,
      progressFetching: false,
      progressData: null,
      progressUrlMap: {},
      progressPreviewUrl: null,
      progressPendingFile: null,
      notifData: null,
      trainingRems: [],
      histRes: null,
      histView: null
    };
  }

  // ---- Контроллер страницы ----
  var controller = {
    /**
     * Показ страницы: строит разметку, вешает обработчики и делает РОВНО ДВА
     * запроса — профиль и статус подписки. Содержимое разделов грузится лениво
     * при первом раскрытии.
     */
    onShow: function (viewEl) {
      viewEl.innerHTML = template();
      bindElements(viewEl);

      // Одна делегированная навеска на все разделы-строки.
      bindSections(viewEl);

      // Прокручиваем к началу при входе в раздел.
      App.scrollTop();

      // Карточка подписки ведёт на отдельный экран подписки.
      if (els.subCard) {
        els.subCard.addEventListener("click", function () {
          App.haptic("selection");
          App.goSubscription();
        });
      }

      // Форма профиля.
      if (els.calcBtn) els.calcBtn.addEventListener("click", onCalc);
      if (els.form) els.form.addEventListener("submit", onSave);

      // Переключатель языка (раздел «Язык»).
      var langRu = viewEl.querySelector("#accLangRu");
      var langEn = viewEl.querySelector("#accLangEn");
      if (langRu) {
        langRu.addEventListener("click", function () {
          onPickLang("ru");
        });
      }
      if (langEn) {
        langEn.addEventListener("click", function () {
          onPickLang("en");
        });
      }

      // Переключатель темы (раздел «Тема оформления»). Тема применяется
      // мгновенно и запоминается; перерисовывать экран не нужно — меняются
      // только значения токенов, разметка остаётся прежней.
      var themeBtns = viewEl.querySelectorAll("[data-theme-mode]");
      for (var ti = 0; ti < themeBtns.length; ti++) {
        themeBtns[ti].addEventListener("click", function () {
          var mode = this.getAttribute("data-theme-mode");
          if (App.theme) App.theme.set(mode);
          App.haptic("selection");
          for (var k = 0; k < themeBtns.length; k++) {
            var on = themeBtns[k] === this;
            themeBtns[k].classList.toggle("acc-lang__btn--active", on);
            themeBtns[k].setAttribute("aria-pressed", on ? "true" : "false");
          }
        });
      }

      // Удаление всех данных (раздел «Данные»).
      var delBtn = viewEl.querySelector("#accDeleteData");
      if (delBtn) {
        delBtn.addEventListener("click", function () {
          onDeleteData(delBtn);
        });
      }

      // Сначала показываем известное из кэша — экран не ждёт сеть.
      refreshSubCard();
      if (App.state.profile) fillForm(App.state.profile);
      applyCycleVisibility();

      // ЕДИНСТВЕННЫЙ проход по разделам — после того, как получены и профиль,
      // и статус подписки. Раньше renderPremiumSections вызывался трижды, и
      // график веса, цикл и фото грузились по три раза за одно открытие.
      var profileP = App.api.getProfile().catch(function (err) {
        var reason = err && err.message ? err.message : L("ошибка", "error");
        App.toast(
          L(
            "Не удалось загрузить профиль: " + reason,
            "Failed to load profile: " + reason
          )
        );
        return null;
      });
      var subP = App.refreshSubscription
        ? App.refreshSubscription().catch(function () {
            return null;
          })
        : Promise.resolve(null);

      Promise.all([profileP, subP]).then(function (res) {
        // Страница могла смениться, пока шли запросы.
        if (!els || !els.viewEl || !document.body.contains(els.viewEl)) return;
        if (res[0]) {
          App.state.profile = res[0];
          fillForm(res[0]);
        }
        refreshSubCard();
        applySectionsState();
      });

      // По внешнему флагу разворачиваем раздел «Мои параметры и цель» и
      // прокручиваем к нему (например, при переходе с просьбой заполнить профиль).
      if (App.state.openProfileFold) {
        toggleSection("profile");
        var profSec = secEl("profile");
        if (profSec && typeof profSec.scrollIntoView === "function") {
          profSec.scrollIntoView({ behavior: "smooth", block: "start" });
        }
        App.state.openProfileFold = false;
      }
    },

    /**
     * Уход со страницы — освобождаем object URL'ы фото и кэш ссылок.
     */
    onHide: function () {
      // Освобождаем blob-URL'ы приватных фото, чтобы не текла память.
      revokeProgressUrls();
      els = null;
    }
  };

  // Регистрируем страницу и публикуем контроллер для отладки.
  window.PageAccount = controller;
  App.registerPage("account", controller);
})();
