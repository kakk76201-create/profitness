/*
 * page-supplements.js — страница «Добавки».
 *
 * Регистрирует контроллер страницы через App.registerPage("supplements", {...}).
 * Публичная ссылка — window.PageSupplements.
 *
 * Это ВКЛАДКА первого уровня (крайняя левая в таббаре), а не подэкран
 * «Профиля»: добавки становятся самостоятельным разделом. Поэтому кнопки
 * «Назад» нет, а шапка такая же, как у других корневых экранов, — крупный
 * заголовок с подписью. Бесплатному пользователю под той же шапкой
 * показывается пейволл: вкладка не должна «терять» заголовок и выглядеть
 * чужим экраном только потому, что раздел платный.
 *
 * Раздел состоит из трёх частей:
 *
 *   1. МОИ ДОБАВКИ
 *      - Список добавок (App.api.getSupplements) с удалением
 *        (App.api.deleteSupplement).
 *      - Форма добавления: название, тип, дозировка, время приёма (HH:MM),
 *        чекбокс «напоминать» -> App.api.addSupplement.
 *
 *   2. НАПОМИНАНИЯ О ПРИЁМЕ
 *      - Список напоминаний (App.api.getSupplementReminders) с удалением
 *        (App.api.deleteSupplementReminder). Показывается состав каждого
 *        («Ночь, 22:00 — магний, ZMA»).
 *      - Форма создания: метка/название (Утро/Ночь/своё) + время (input time) +
 *        множественный выбор добавок (чекбоксы по App.api.getSupplements) +
 *        вкл/выкл -> App.api.addSupplementReminder({label,time,enabled,supplement_ids}).
 *
 *   3. AI-СОВЕТЫ ПО ДОБАВКАМ
 *      - Пресеты цели улучшения чипами (Сон / Восстановление / Сила / Энергия /
 *        Иммунитет) + поле свободного ввода.
 *      - Кнопка «Получить совет» -> App.api.recommendSupplements({improvement_goal})
 *        -> карточки {name,dosage,note} + обязательный дисклеймер из ответа.
 *      - Предзаполнение improvement_goal из profile.supplement_goal.
 *
 * Локализация RU/EN: все видимые пользователю строки оборачиваются в
 * App.pick("рус","eng") НА МОМЕНТ РЕНДЕРА, чтобы смена языка давала нужный
 * текст при перерисовке. Полная обработка ошибок (сеть/AI), состояния
 * загрузки (скелетоны), пустые состояния.
 */
(function () {
  "use strict";

  /**
   * Локализация: возвращает строку по текущему языку App.lang.
   * Используем безопасный фолбэк на русский, если App.pick недоступен.
   */
  function pick(ru, en) {
    if (App && typeof App.pick === "function") return App.pick(ru, en);
    return ru;
  }

  // Пресеты цели улучшения (чипы AI-советов): ключ для локализации.
  // Значение, отправляемое серверу (improvement_goal), берётся на момент
  // рендера через presetValue() — на текущем языке.
  var IMPROVEMENT_PRESETS = ["sleep", "recovery", "strength", "energy", "immunity"];

  // Имена иконок из общего набора. Точных «молнии» и «щита» в нём нет:
  // для энергии берём пламя (тот же смысл — «горючее»), для иммунитета —
  // сердце (здоровье). Выдумывать эмодзи ради двух чипов не стоит.
  var PRESET_ICON = {
    sleep: "moon",
    recovery: "refresh",
    strength: "dumbbell",
    energy: "flame",
    immunity: "heart"
  };

  /**
   * Локализованное значение пресета (улетает на сервер как improvement_goal).
   */
  function presetValue(key) {
    switch (key) {
      case "sleep":
        return pick("Сон", "Sleep");
      case "recovery":
        return pick("Восстановление", "Recovery");
      case "strength":
        return pick("Сила", "Strength");
      case "energy":
        return pick("Энергия", "Energy");
      case "immunity":
        return pick("Иммунитет", "Immunity");
      default:
        return key;
    }
  }

  // Внутреннее состояние контроллера.
  var state = {
    viewEl: null,           // корневой элемент страницы (#view)
    supLoading: false,      // флаг загрузки списка добавок (защита от гонок)
    remLoading: false,      // флаг загрузки списка напоминаний
    supplements: [],        // последний загруженный список добавок (для чекбоксов)
    improvementGoal: ""     // выбранная/введённая цель улучшения для AI-советов
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

  function toast(msg) {
    if (App && typeof App.toast === "function") App.toast(msg);
  }

  /* =====================================================================
   *  PAYWALL: ЕДИНЫЙ СПИСОК ВЫГОД
   *  Буллеты берём у страницы подписки (window.PageSubscription): список
   *  выгод в приложении ОДИН, и AI-тренер в нём первый.
   *  Цену и пробный период сюда НЕ дописываем — их печатает сам App.paywall
   *  отдельной строкой, и в описании они дублировались бы слово в слово.
   * ===================================================================== */

  /** Первые пункты единого списка выгод — буллеты paywall. */
  function paywallBullets() {
    var PS = window.PageSubscription;
    if (PS && typeof PS.benefits === "function") {
      var list = PS.benefits();
      if (list && list.length) return list.slice(0, 4);
    }
    // Фолбэк, если страница подписки почему-то не загрузилась.
    return [
      pick("AI-тренер и программа тренировок", "AI trainer and workout program"),
      pick("Фото и голос без лимита", "Unlimited photo and voice input"),
      pick("Добавки, напоминания и AI-советы", "Supplements, reminders and AI tips")
    ];
  }

  /**
   * Хелпер: поиск элемента по id внутри текущего представления.
   */
  function byId(id) {
    if (!state.viewEl) return null;
    return state.viewEl.querySelector("#" + id);
  }

  /**
   * Приводит значение времени к строке "HH:MM" для поля ввода/отображения.
   * Сервер может вернуть "HH:MM:SS" — оставляем первые 5 символов.
   */
  function timeValue(v) {
    if (v == null) return "";
    var s = String(v).trim();
    if (s.length >= 5) return s.slice(0, 5);
    return s;
  }

  /* =====================================================================
   *  РАЗМЕТКА: СКЕЛЕТОНЫ
   * ===================================================================== */

  /**
   * Скелетон списка добавок.
   */
  function supplementsSkeletonHtml() {
    var rows = "";
    for (var i = 0; i < 2; i++) {
      rows +=
        '<div class="sup-item skeleton-block">' +
        '<div class="skeleton skeleton-line skeleton-title"></div>' +
        '<div class="skeleton skeleton-line short"></div>' +
        "</div>";
    }
    return '<div class="sup-skeleton">' + rows + "</div>";
  }

  /**
   * Скелетон списка напоминаний.
   */
  function remindersSkeletonHtml() {
    var rows = "";
    for (var i = 0; i < 2; i++) {
      rows +=
        '<div class="sup-rem-item skeleton-block">' +
        '<div class="skeleton skeleton-line skeleton-title"></div>' +
        '<div class="skeleton skeleton-line short"></div>' +
        "</div>";
    }
    return '<div class="sup-rem-skeleton">' + rows + "</div>";
  }

  /* =====================================================================
   *  РАЗМЕТКА: СТАТИЧЕСКИЙ КАРКАС СТРАНИЦЫ
   * ===================================================================== */

  /**
   * Форма добавления добавки.
   */
  function supplementFormHtml() {
    return (
      '<form class="sup-form" id="supForm" novalidate>' +
      '<h3 class="sup-form__title">' + esc(pick("Добавить добавку", "Add supplement")) + "</h3>" +

      '<label class="field">' +
      '<span class="field__label">' + esc(pick("Название", "Name")) + "</span>" +
      '<input class="field__input" id="supName" type="text" ' +
      'placeholder="' + esc(pick("Креатин", "Creatine")) + '" maxlength="100" autocomplete="off">' +
      "</label>" +

      '<label class="field">' +
      '<span class="field__label">' + esc(pick("Дозировка", "Dosage")) + "</span>" +
      '<input class="field__input" id="supDosage" type="text" ' +
      'placeholder="' + esc(pick("5 г", "5 g")) + '" maxlength="60" autocomplete="off">' +
      "</label>" +

      '<label class="field">' +
      '<span class="field__label">' + esc(pick("Время приёма", "Intake time")) + "</span>" +
      // (Поле «Тип» удалено — тип больше не задаётся вручную.)
      '<input class="field__input" id="supTime" type="time">' +
      "</label>" +

      '<label class="sup-form__check">' +
      '<input type="checkbox" id="supReminder" class="sup-form__checkbox">' +
      '<span class="sup-form__check-label">' + esc(pick("Напоминать о приёме", "Remind me to take it")) + "</span>" +
      "</label>" +

      '<button type="submit" class="btn btn-cta btn-block sup-add" id="supAddBtn">' +
      esc(pick("Добавить добавку", "Add supplement")) +
      "</button>" +
      "</form>"
    );
  }

  /**
   * Карточка раздела «Мои добавки» (список + форма).
   */
  function supplementCardHtml() {
    return (
      '<section class="card sup-card">' +
      '<h2 class="sup-card__title">' + esc(pick("Мои добавки", "My supplements")) + "</h2>" +
      '<p class="sup-card__subtitle">' +
      esc(pick("Ваши добавки и приёмы спортивного питания.", "Your supplements and sports nutrition intake.")) +
      "</p>" +

      // Контейнер списка добавок (наполняется отдельно).
      '<div id="supList" class="sup-list"></div>' +

      // Форма добавления.
      supplementFormHtml() +
      "</section>"
    );
  }

  /**
   * Форма создания напоминания о приёме добавок.
   */
  function reminderFormHtml() {
    return (
      '<form class="sup-rem-form" id="supRemForm" novalidate>' +
      '<h3 class="sup-rem-form__title">' + esc(pick("Новое напоминание", "New reminder")) + "</h3>" +

      '<div class="sup-rem-form__grid">' +
      '<label class="field">' +
      '<span class="field__label">' + esc(pick("Время", "Time")) + "</span>" +
      '<input class="field__input" id="remTime" type="time">' +
      "</label>" +
      "</div>" +

      // Множественный выбор добавок (наполняется по списку принимаемых).
      '<div class="sup-rem-form__pick">' +
      '<span class="field__label">' + esc(pick("Какие добавки напомнить", "Which supplements to remind")) + "</span>" +
      '<div id="remPicks" class="sup-rem-picks"></div>' +
      "</div>" +

      '<button type="submit" class="btn btn-cta btn-block sup-rem-add" id="remAddBtn">' +
      esc(pick("Создать напоминание", "Create reminder")) +
      "</button>" +
      "</form>"
    );
  }

  /**
   * Карточка раздела «Напоминания о приёме» (список + форма).
   */
  function reminderCardHtml() {
    return (
      '<section class="card sup-rem-card">' +
      '<h2 class="sup-rem-card__title">' + esc(pick("Напоминания о приёме", "Intake reminders")) + "</h2>" +
      '<p class="sup-rem-card__subtitle">' +
      esc(pick("Telegram напомнит вовремя принять добавки.", "Telegram will remind you to take your supplements on time.")) +
      "</p>" +

      // Контейнер списка напоминаний (наполняется отдельно).
      '<div id="remList" class="sup-rem-list"></div>' +

      // Форма создания.
      reminderFormHtml() +
      "</section>"
    );
  }

  /**
   * Чипы пресетов цели улучшения для AI-советов.
   */
  function presetChipsHtml() {
    return IMPROVEMENT_PRESETS.map(function (key) {
      var value = presetValue(key);
      return (
        '<button type="button" class="chip sup-ai-chip" ' +
        'data-goal="' + esc(value) + '">' +
        '<span class="sup-ai-chip__icon" aria-hidden="true">' +
        icon(PRESET_ICON[key] || "target", { size: 18 }) +
        "</span>" +
        '<span class="sup-ai-chip__label">' + esc(value) + "</span>" +
        "</button>"
      );
    }).join("");
  }

  /**
   * Карточка раздела «AI-советы по добавкам».
   */
  function aiCardHtml() {
    return (
      '<section class="card sup-ai-card">' +
      '<span class="eyebrow sup-ai-card__eyebrow">' + esc(pick("Совет тренера", "Coach’s advice")) + "</span>" +
      '<h2 class="sup-ai-card__title">' + esc(pick("AI-советы по добавкам", "AI supplement advice")) + "</h2>" +
      '<p class="sup-ai-card__subtitle">' +
      esc(pick(
        "Выберите, что хотите улучшить, или опишите цель своими словами.",
        "Pick what you want to improve, or describe your goal in your own words."
      )) +
      "</p>" +

      '<div class="sup-ai-chips" id="supAiChips">' +
      presetChipsHtml() +
      "</div>" +

      '<label class="field sup-ai-field">' +
      '<span class="field__label">' + esc(pick("Цель улучшения", "Improvement goal")) + "</span>" +
      '<input class="field__input" id="supAiGoal" type="text" ' +
      'placeholder="' + esc(pick("Например: меньше усталости", "E.g. less fatigue")) + '" maxlength="80" autocomplete="off">' +
      "</label>" +

      '<button type="button" class="btn btn-cta btn-block sup-ai-btn" id="supAiBtn">' +
      icon("robot") +
      '<span class="sup-ai-btn__label">' +
      esc(pick("Получить совет", "Get advice")) +
      "</span>" +
      "</button>" +

      '<div id="supAiBox" class="sup-ai-box"></div>' +
      "</section>"
    );
  }

  /**
   * Шапка корневого экрана: крупный заголовок и строка пояснения. Та же
   * иерархия, что у «Сегодня» (.td-head): вкладки первого уровня выглядят
   * одинаково, а «Назад» у вкладки быть не может — возвращаться некуда.
   */
  function headHtml() {
    return (
      '<header class="sup-head">' +
      '<h1 class="sup-head__title">' + esc(pick("Добавки", "Supplements")) + "</h1>" +
      '<p class="sup-head__sub">' +
      esc(pick("Учёт, напоминания и AI-советы", "Tracking, reminders and AI tips")) +
      "</p>" +
      "</header>"
    );
  }

  /**
   * Полный каркас страницы. Динамические части (списки) наполняются
   * отдельными функциями после монтирования.
   */
  function pageTemplate() {
    return (
      '<section class="page page-supplements">' +
      headHtml() +

      // Раздел «Мои добавки».
      supplementCardHtml() +

      // Раздел «Напоминания о приёме».
      reminderCardHtml() +

      // Раздел «AI-советы по добавкам».
      aiCardHtml() +
      "</section>"
    );
  }

  /* =====================================================================
   *  РАЗМЕТКА: ДИНАМИЧЕСКИЕ ЧАСТИ — МОИ ДОБАВКИ
   * ===================================================================== */

  /**
   * Разметка одной строки добавки.
   */
  function supplementRowHtml(s) {
    // Собираем строку с деталями (дозировка / время), пропуская пустые.
    // Дозировка — приглушённой строкой под названием, время приёма — чипом:
    // время ищут взглядом чаще всего, и в общей строке «5 г · 08:00» оно
    // терялось.
    var meta = s.dosage ? '<span class="sup-item__meta">' + esc(s.dosage) + "</span>" : "";
    var chips = "";
    if (s.intake_time) {
      chips +=
        '<span class="sup-item__chip">' +
        icon("clock", { size: 14 }) +
        "<span>" + esc(timeValue(s.intake_time)) + "</span>" +
        "</span>";
    }
    if (s.reminder_enabled) {
      chips +=
        '<span class="sup-item__chip sup-item__badge">' +
        icon("bell", { size: 14 }) +
        "<span>" +
        esc(pick("напоминание", "reminder")) +
        "</span></span>";
    }

    return (
      '<li class="sup-item" data-id="' + esc(s.id) + '">' +
      '<div class="sup-item__main">' +
      '<span class="sup-item__name">' + esc(s.name || pick("Без названия", "Untitled")) + "</span>" +
      meta +
      (chips ? '<span class="sup-item__chips">' + chips + "</span>" : "") +
      "</div>" +
      '<button class="sup-item__del" type="button" data-id="' + esc(s.id) + '" ' +
      'aria-label="' + esc(pick("Удалить добавку", "Delete supplement")) + '" ' +
      'title="' + esc(pick("Удалить", "Delete")) + '">' +
      icon("close", { size: 18 }) +
      "</button>" +
      "</li>"
    );
  }

  /**
   * Отрисовка списка добавок.
   * @param {Object} data { items:[...] }
   */
  function renderSupplements(data) {
    var box = byId("supList");
    var items = (data && data.items) || [];

    // Запоминаем список — он нужен для чекбоксов в форме напоминаний.
    state.supplements = items;

    // Список добавок мог измениться — перерисуем чекбоксы напоминаний.
    renderReminderPicks();

    if (!box) return;

    if (!items.length) {
      box.innerHTML =
        '<div class="sup-empty">' +
        '<div class="sup-empty__icon" aria-hidden="true">' +
        icon("pill", { size: 24 }) +
        "</div>" +
        '<p class="sup-empty__text">' + esc(pick("Добавки пока не добавлены.", "No supplements added yet.")) + "</p>" +
        "</div>";
      return;
    }

    var rows = "";
    for (var i = 0; i < items.length; i++) {
      rows += supplementRowHtml(items[i]);
    }
    box.innerHTML = '<ul class="sup-item-list">' + rows + "</ul>";

    var delButtons = box.querySelectorAll(".sup-item__del");
    for (var k = 0; k < delButtons.length; k++) {
      delButtons[k].addEventListener("click", onSupplementDelete);
    }
  }

  /**
   * Состояние ошибки загрузки добавок с кнопкой «Повторить».
   */
  function renderSupplementsError(message) {
    var box = byId("supList");
    if (!box) return;
    box.innerHTML =
      '<div class="sup-error">' +
      '<div class="sup-error__icon" aria-hidden="true">' +
      icon("warning", { size: 24 }) +
      "</div>" +
      '<p class="sup-error__title">' + esc(pick("Не удалось загрузить добавки", "Couldn’t load supplements")) + "</p>" +
      '<p class="sup-error__text">' + esc(message || pick("Неизвестная ошибка", "Unknown error")) + "</p>" +
      '<button class="btn btn-ghost sup-error__retry" type="button">' + esc(pick("Повторить", "Retry")) + "</button>" +
      "</div>";
    var retry = box.querySelector(".sup-error__retry");
    if (retry) {
      retry.addEventListener("click", function () {
        loadSupplements();
      });
    }
  }

  /* =====================================================================
   *  РАЗМЕТКА: ДИНАМИЧЕСКИЕ ЧАСТИ — НАПОМИНАНИЯ
   * ===================================================================== */

  /**
   * Отрисовывает чекбоксы выбора добавок в форме напоминания
   * на основе текущего списка принимаемых добавок (state.supplements).
   */
  function renderReminderPicks() {
    var box = byId("remPicks");
    if (!box) return;

    var items = state.supplements || [];
    if (!items.length) {
      box.innerHTML =
        '<p class="sup-rem-picks__empty">' +
        esc(pick(
          "Сначала добавьте добавки выше — тогда их можно будет выбрать для напоминания.",
          "Add supplements above first — then you can pick them for a reminder."
        )) +
        "</p>";
      return;
    }

    var html = items
      .map(function (s) {
        var label = s.name || pick("Без названия", "Untitled");
        return (
          '<label class="sup-rem-pick">' +
          '<input type="checkbox" class="sup-rem-pick__input" ' +
          'value="' + esc(s.id) + '">' +
          '<span class="sup-rem-pick__label">' + esc(label) + "</span>" +
          "</label>"
        );
      })
      .join("");

    box.innerHTML = html;
  }

  /**
   * Разметка одной строки напоминания.
   * Показывает состав («Ночь, 22:00 — магний, ZMA»).
   */
  function reminderRowHtml(r) {
    var time = timeValue(r.time);
    var sups = (r.supplements || [])
      .map(function (s) {
        return esc(s.name || "");
      })
      .filter(function (n) {
        return n !== "";
      });

    // Заголовок теперь только время (метка убрана из UI).
    var head = time ? esc(time) : esc(pick("Напоминание", "Reminder"));

    // Состав через тире: «— магний, ZMA».
    var composition = sups.length
      ? '<span class="sup-rem-item__sups"> — ' + sups.join(", ") + "</span>"
      : '<span class="sup-rem-item__sups sup-rem-item__sups--empty"> — ' +
        esc(pick("добавки не выбраны", "no supplements selected")) + "</span>";

    return (
      '<li class="sup-rem-item" data-id="' + esc(r.id) + '">' +
      '<div class="sup-rem-item__main">' +
      '<span class="sup-rem-item__head">' + head + composition + "</span>" +
      "</div>" +
      '<button class="sup-rem-item__del" type="button" data-id="' + esc(r.id) + '" ' +
      'aria-label="' + esc(pick("Удалить напоминание", "Delete reminder")) + '" ' +
      'title="' + esc(pick("Удалить", "Delete")) + '">' +
      icon("close", { size: 18 }) +
      "</button>" +
      "</li>"
    );
  }

  /**
   * Отрисовка списка напоминаний.
   * @param {Object} data { items:[...] }
   */
  function renderReminders(data) {
    var box = byId("remList");
    if (!box) return;

    var items = (data && data.items) || [];

    if (!items.length) {
      box.innerHTML =
        '<div class="sup-rem-empty">' +
        '<div class="sup-rem-empty__icon" aria-hidden="true">' +
        icon("bell", { size: 24 }) +
        "</div>" +
        '<p class="sup-rem-empty__text">' +
        esc(pick(
          "Напоминаний пока нет. Создайте первое с помощью формы ниже.",
          "No reminders yet. Create your first one with the form below."
        )) +
        "</p>" +
        "</div>";
      return;
    }

    var rows = "";
    for (var i = 0; i < items.length; i++) {
      rows += reminderRowHtml(items[i]);
    }
    box.innerHTML = '<ul class="sup-rem-item-list">' + rows + "</ul>";

    var delButtons = box.querySelectorAll(".sup-rem-item__del");
    for (var k = 0; k < delButtons.length; k++) {
      delButtons[k].addEventListener("click", onReminderDelete);
    }
  }

  /**
   * Состояние ошибки загрузки напоминаний с кнопкой «Повторить».
   */
  function renderRemindersError(message) {
    var box = byId("remList");
    if (!box) return;
    box.innerHTML =
      '<div class="sup-rem-error">' +
      '<div class="sup-rem-error__icon" aria-hidden="true">' +
      icon("warning", { size: 24 }) +
      "</div>" +
      '<p class="sup-rem-error__title">' + esc(pick("Не удалось загрузить напоминания", "Couldn’t load reminders")) + "</p>" +
      '<p class="sup-rem-error__text">' + esc(message || pick("Неизвестная ошибка", "Unknown error")) + "</p>" +
      '<button class="btn btn-ghost sup-rem-error__retry" type="button">' + esc(pick("Повторить", "Retry")) + "</button>" +
      "</div>";
    var retry = box.querySelector(".sup-rem-error__retry");
    if (retry) {
      retry.addEventListener("click", function () {
        loadReminders();
      });
    }
  }

  /* =====================================================================
   *  РАЗМЕТКА: ДИНАМИЧЕСКИЕ ЧАСТИ — AI-СОВЕТЫ
   * ===================================================================== */

  /**
   * Отрисовка результата AI-совета.
   * @param {Object} res { suggestions:[{name,dosage,note}], disclaimer,
   *                        training_count, improvement_goal }
   */
  function renderAi(res) {
    var box = byId("supAiBox");
    if (!box) return;

    var suggestions = (res && res.suggestions) || [];
    var disclaimer = res && res.disclaimer ? res.disclaimer : "";
    var goal = res && res.improvement_goal ? res.improvement_goal : "";

    if (!suggestions.length) {
      // Даже при пустых рекомендациях показываем дисклеймер, если он пришёл.
      var emptyDisclaimer = disclaimer
        ? '<p class="sup-ai-disclaimer">' +
          icon("warning", { size: 16 }) +
          "<span>" +
          esc(disclaimer) +
          "</span></p>"
        : "";
      box.innerHTML =
        '<div class="sup-ai-box__inner">' +
        '<div class="sup-ai-box__empty">' +
        esc(pick(
          "Подходящих рекомендаций не нашлось. Попробуйте уточнить цель.",
          "No suitable recommendations found. Try refining your goal."
        )) +
        "</div>" +
        emptyDisclaimer +
        "</div>";
      return;
    }

    var cards = suggestions
      .map(function (s) {
        var note = s.note
          ? '<p class="sup-ai-suggest__note">' + esc(s.note) + "</p>"
          : "";
        var dosage = s.dosage
          ? '<span class="sup-ai-suggest__dosage">' + esc(s.dosage) + "</span>"
          : "";
        // Кнопка «Купить» — только если бэкенд отдал ссылку на магазин.
        var buy = s.buy_url
          ? '<a class="btn btn-ghost sup-ai-suggest__buy" href="' + esc(s.buy_url) + '" ' +
            'target="_blank" rel="noopener noreferrer nofollow sponsored" ' +
            'data-buy-url="' + esc(s.buy_url) + '">' +
            icon("cart", { size: 18 }) +
            "<span>" + esc(pick("Купить", "Buy")) + "</span></a>"
          : "";

        return (
          '<div class="sup-ai-suggest">' +
          '<div class="sup-ai-suggest__head">' +
          '<span class="sup-ai-suggest__name">' + esc(s.name || pick("Добавка", "Supplement")) + "</span>" +
          dosage +
          "</div>" +
          note +
          '<div class="sup-ai-suggest__actions">' +
          '<button type="button" class="btn btn-ghost sup-ai-suggest__use" ' +
          'data-name="' + esc(s.name || "") + '" ' +
          'data-dosage="' + esc(s.dosage || "") + '">' + esc(pick("Заполнить форму", "Fill the form")) + "</button>" +
          buy +
          "</div>" +
          "</div>"
        );
      })
      .join("");

    // Дисклеймер ОБЯЗАТЕЛЕН — показываем всегда, когда он пришёл с сервера.
    var disclaimerHtml = disclaimer
      ? '<p class="sup-ai-disclaimer">' +
        icon("warning", { size: 16 }) +
        "<span>" +
        esc(disclaimer) +
        "</span></p>"
      : "";

    // Если показываем ссылки на магазин — по закону о рекламе (ст. 25 ФЗ-38)
    // для БАД обязательна оговорка, что это не лекарственные средства.
    var hasBuyLinks = suggestions.some(function (s) { return !!s.buy_url; });
    if (hasBuyLinks) {
      disclaimerHtml +=
        '<p class="sup-ai-disclaimer sup-ai-disclaimer--bad">' +
        esc(pick(
          "БАД не являются лекарственными средствами.",
          "Supplements are not medicinal products."
        )) +
        "</p>";
    }

    var goalHtml = goal
      ? '<p class="sup-ai-box__goal">' + esc(pick("Цель: ", "Goal: ")) + esc(goal) + "</p>"
      : "";

    box.innerHTML =
      '<div class="sup-ai-box__inner">' +
      '<p class="sup-ai-box__heading">' + esc(pick("Рекомендации", "Recommendations")) + "</p>" +
      goalHtml +
      '<div class="sup-ai-suggest-list">' + cards + "</div>" +
      disclaimerHtml +
      "</div>";

    // Кнопки «Заполнить форму» переносят данные совета в форму добавления.
    var useButtons = box.querySelectorAll(".sup-ai-suggest__use");
    for (var i = 0; i < useButtons.length; i++) {
      useButtons[i].addEventListener("click", onAiSuggestionUse);
    }

    // Кнопки «Купить»: внутри Telegram обычная навигация по ссылке блокируется,
    // поэтому открываем внешним браузером через SDK (openLink).
    var buyButtons = box.querySelectorAll(".sup-ai-suggest__buy");
    for (var b = 0; b < buyButtons.length; b++) {
      buyButtons[b].addEventListener("click", function (ev) {
        var url = ev.currentTarget.getAttribute("data-buy-url");
        if (!url) return;
        App.haptic && App.haptic("light");
        if (App.tg && typeof App.tg.openLink === "function") {
          ev.preventDefault();
          App.tg.openLink(url);
        }
        // Вне Telegram — обычный переход по href (target="_blank").
      });
    }
  }

  /* =====================================================================
   *  ЗАГРУЗКА ДАННЫХ
   * ===================================================================== */

  /**
   * Загрузка списка добавок (со скелетоном и обработкой ошибок).
   */
  function loadSupplements() {
    if (state.supLoading) return;
    state.supLoading = true;

    var box = byId("supList");
    if (box) box.innerHTML = supplementsSkeletonHtml();

    App.api
      .getSupplements()
      .then(function (data) {
        renderSupplements(data);
      })
      .catch(function (err) {
        renderSupplementsError(
          (err && err.message) || pick("Проблема с сетью. Проверьте соединение.", "Network problem. Check your connection.")
        );
      })
      .then(function () {
        state.supLoading = false;
      });
  }

  /**
   * Загрузка списка напоминаний (со скелетоном и обработкой ошибок).
   */
  function loadReminders() {
    if (state.remLoading) return;
    state.remLoading = true;

    var box = byId("remList");
    if (box) box.innerHTML = remindersSkeletonHtml();

    App.api
      .getSupplementReminders()
      .then(function (data) {
        renderReminders(data);
      })
      .catch(function (err) {
        renderRemindersError(
          (err && err.message) || pick("Проблема с сетью. Проверьте соединение.", "Network problem. Check your connection.")
        );
      })
      .then(function () {
        state.remLoading = false;
      });
  }

  /* =====================================================================
   *  ОБРАБОТЧИКИ: МОИ ДОБАВКИ
   * ===================================================================== */

  /**
   * Отправка формы добавления добавки.
   */
  function onSupplementSubmit(e) {
    if (e) e.preventDefault();

    var nameEl = byId("supName");
    var dosageEl = byId("supDosage");
    var timeEl = byId("supTime");
    var reminderEl = byId("supReminder");
    var btn = byId("supAddBtn");
    if (!nameEl) return;

    var name = (nameEl.value || "").trim();
    if (!name) {
      toast(pick("Укажите название добавки", "Enter the supplement name"));
      haptic("error");
      nameEl.focus();
      return;
    }

    var payload = {
      name: name,
      // Тип больше не задаётся в UI — бэкенд подставит "" по умолчанию.
      type: "",
      dosage: (dosageEl && dosageEl.value || "").trim(),
      reminder_enabled: !!(reminderEl && reminderEl.checked)
    };

    // Время приёма передаём только если оно задано (HH:MM).
    var time = (timeEl && timeEl.value || "").trim();
    if (time) {
      payload.intake_time = time;
    }

    if (btn) btn.disabled = true;
    App.showLoading();

    App.api
      .addSupplement(payload)
      .then(function () {
        haptic("success");
        toast(pick("Добавка добавлена", "Supplement added"));
        // Очищаем форму.
        nameEl.value = "";
        if (dosageEl) dosageEl.value = "";
        if (timeEl) timeEl.value = "";
        if (reminderEl) reminderEl.checked = false;
        // Перезагружаем список (он же обновит чекбоксы напоминаний).
        loadSupplements();
      })
      .catch(function (err) {
        haptic("error");
        toast((err && err.message) || pick("Не удалось добавить добавку", "Couldn’t add supplement"));
      })
      .finally(function () {
        if (btn) btn.disabled = false;
        App.hideLoading();
      });
  }

  /**
   * Удаление добавки по кнопке-крестику.
   */
  function onSupplementDelete(ev) {
    var btn = ev.currentTarget;
    var id = parseInt(btn.getAttribute("data-id"), 10);
    if (isNaN(id)) return;
    if (btn.disabled) return;

    // Кнопку только блокируем: подменять её содержимое текстом нельзя —
    // внутри иконка, и после отмены её пришлось бы собирать заново.
    btn.disabled = true;
    btn.classList.add("is-busy");
    haptic("light");
    App.showLoading();

    App.api
      .deleteSupplement(id)
      .then(function () {
        toast(pick("Добавка удалена", "Supplement deleted"));
        loadSupplements();
      })
      .catch(function (err) {
        btn.disabled = false;
        btn.classList.remove("is-busy");
        haptic("error");
        toast((err && err.message) || pick("Не удалось удалить добавку", "Couldn’t delete supplement"));
      })
      .finally(function () {
        App.hideLoading();
      });
  }

  /* =====================================================================
   *  ОБРАБОТЧИКИ: НАПОМИНАНИЯ
   * ===================================================================== */

  /**
   * Отправка формы создания напоминания.
   */
  function onReminderSubmit(e) {
    if (e) e.preventDefault();

    var timeEl = byId("remTime");
    var picksBox = byId("remPicks");
    var btn = byId("remAddBtn");
    if (!timeEl) return;

    var time = (timeEl.value || "").trim();
    if (!time) {
      toast(pick("Укажите время напоминания", "Set the reminder time"));
      haptic("error");
      timeEl.focus();
      return;
    }

    // Собираем id выбранных добавок.
    var supplementIds = [];
    if (picksBox) {
      var checks = picksBox.querySelectorAll(".sup-rem-pick__input:checked");
      for (var i = 0; i < checks.length; i++) {
        var sid = parseInt(checks[i].value, 10);
        if (!isNaN(sid)) supplementIds.push(sid);
      }
    }

    if (!supplementIds.length) {
      toast(pick("Выберите хотя бы одну добавку", "Pick at least one supplement"));
      haptic("error");
      return;
    }

    var payload = {
      // Метка убрана из UI — бэкенд подставит "" по умолчанию.
      label: "",
      time: time,
      // Существующее напоминание всегда активно.
      enabled: true,
      supplement_ids: supplementIds
    };

    if (btn) btn.disabled = true;
    App.showLoading();

    App.api
      .addSupplementReminder(payload)
      .then(function () {
        haptic("success");
        toast(pick("Напоминание создано", "Reminder created"));
        // Сбрасываем форму.
        timeEl.value = "";
        if (picksBox) {
          var allChecks = picksBox.querySelectorAll(".sup-rem-pick__input");
          for (var j = 0; j < allChecks.length; j++) {
            allChecks[j].checked = false;
          }
        }
        loadReminders();
      })
      .catch(function (err) {
        haptic("error");
        toast((err && err.message) || pick("Не удалось создать напоминание", "Couldn’t create reminder"));
      })
      .finally(function () {
        if (btn) btn.disabled = false;
        App.hideLoading();
      });
  }

  /**
   * Удаление напоминания по кнопке-крестику.
   */
  function onReminderDelete(ev) {
    var btn = ev.currentTarget;
    var id = parseInt(btn.getAttribute("data-id"), 10);
    if (isNaN(id)) return;
    if (btn.disabled) return;

    btn.disabled = true;
    btn.classList.add("is-busy");
    haptic("light");
    App.showLoading();

    App.api
      .deleteSupplementReminder(id)
      .then(function () {
        toast(pick("Напоминание удалено", "Reminder deleted"));
        loadReminders();
      })
      .catch(function (err) {
        btn.disabled = false;
        btn.classList.remove("is-busy");
        haptic("error");
        toast((err && err.message) || pick("Не удалось удалить напоминание", "Couldn’t delete reminder"));
      })
      .finally(function () {
        App.hideLoading();
      });
  }

  /* =====================================================================
   *  ОБРАБОТЧИКИ: AI-СОВЕТЫ
   * ===================================================================== */

  /**
   * Подсвечивает активный чип пресета (по совпадению значения с полем ввода).
   */
  function syncChips() {
    var chipsBox = byId("supAiChips");
    var goalEl = byId("supAiGoal");
    if (!chipsBox || !goalEl) return;
    var current = (goalEl.value || "").trim().toLowerCase();
    var chips = chipsBox.querySelectorAll(".sup-ai-chip");
    for (var i = 0; i < chips.length; i++) {
      var val = (chips[i].getAttribute("data-goal") || "").trim().toLowerCase();
      if (val && val === current) {
        chips[i].classList.add("chip--active");
        chips[i].classList.add("sup-ai-chip--active");
      } else {
        chips[i].classList.remove("chip--active");
        chips[i].classList.remove("sup-ai-chip--active");
      }
    }
  }

  /**
   * Клик по чипу пресета — подставляет цель в поле ввода.
   */
  function onChipClick(ev) {
    var chip = ev.currentTarget;
    var goal = chip.getAttribute("data-goal") || "";
    var goalEl = byId("supAiGoal");
    if (goalEl) {
      goalEl.value = goal;
    }
    state.improvementGoal = goal;
    syncChips();
    haptic("selection");
  }

  /**
   * Кнопка «Получить совет» — запрашивает рекомендации у сервера.
   */
  function onAiRequest() {
    var btn = byId("supAiBtn");
    var box = byId("supAiBox");
    var goalEl = byId("supAiGoal");
    if (!box) return;

    var goal = goalEl ? (goalEl.value || "").trim() : "";
    if (!goal) {
      toast(pick("Выберите цель или опишите её своими словами", "Pick a goal or describe it in your own words"));
      haptic("error");
      if (goalEl) goalEl.focus();
      return;
    }

    state.improvementGoal = goal;

    if (btn) {
      btn.disabled = true;
      // Меняем только подпись: иконка кнопки остаётся на месте.
      var busyLabel = btn.querySelector(".sup-ai-btn__label");
      if (busyLabel) busyLabel.textContent = pick("Подбираем…", "Finding…");
    }
    box.innerHTML =
      '<div class="sup-ai-box__loading">' +
      '<div class="skeleton skeleton-line"></div>' +
      '<div class="skeleton skeleton-line short"></div>' +
      "</div>";

    App.api
      .recommendSupplements({ improvement_goal: goal })
      .then(function (res) {
        renderAi(res);
        haptic("success");
      })
      .catch(function (err) {
        box.innerHTML =
          '<div class="sup-ai-box__error">' +
          '<p class="sup-ai-box__error-text">' +
          esc((err && err.message) || pick("Не удалось получить совет", "Couldn’t get advice")) +
          "</p>" +
          '<button type="button" class="btn btn-ghost sup-ai-box__retry">' + esc(pick("Повторить", "Retry")) + "</button>" +
          "</div>";
        var retry = box.querySelector(".sup-ai-box__retry");
        if (retry) retry.addEventListener("click", onAiRequest);
        haptic("error");
      })
      .finally(function () {
        if (btn) {
          btn.disabled = false;
          var label = btn.querySelector(".sup-ai-btn__label");
          if (label) label.textContent = pick("Получить совет", "Get advice");
        }
      });
  }

  /**
   * Кнопка «Заполнить форму» в карточке совета — переносит
   * название и дозировку в поля формы добавления.
   */
  function onAiSuggestionUse(ev) {
    var btn = ev.currentTarget;
    var name = btn.getAttribute("data-name") || "";
    var dosage = btn.getAttribute("data-dosage") || "";

    var nameEl = byId("supName");
    var dosageEl = byId("supDosage");
    if (nameEl) nameEl.value = name;
    if (dosageEl) dosageEl.value = dosage;

    haptic("light");
    toast(pick("Поля заполнены — проверьте и сохраните", "Fields filled — review and save"));

    // Подскроллим к форме, чтобы пользователь видел заполненные поля.
    var form = byId("supForm");
    if (form && typeof form.scrollIntoView === "function") {
      try {
        form.scrollIntoView({ behavior: "smooth", block: "center" });
      } catch (e) {
        form.scrollIntoView();
      }
    }
  }

  /* =====================================================================
   *  ПРЕДЗАПОЛНЕНИЕ ЦЕЛИ УЛУЧШЕНИЯ
   * ===================================================================== */

  /**
   * Предзаполняет поле цели улучшения сохранённым profile.supplement_goal.
   * Сначала использует кэш профиля, затем подтягивает актуальный профиль.
   */
  function prefillImprovementGoal() {
    var goalEl = byId("supAiGoal");
    if (!goalEl) return;

    // 1) Сразу подставляем из кэша, если он есть.
    var cached = App.state && App.state.profile;
    if (cached && cached.supplement_goal) {
      goalEl.value = cached.supplement_goal;
      state.improvementGoal = cached.supplement_goal;
      syncChips();
    }

    // 2) Подтягиваем актуальный профиль (best-effort, без агрессивных ошибок).
    App.api
      .getProfile()
      .then(function (profile) {
        if (profile) {
          App.state.profile = profile;
          // Заполняем только если пользователь ещё не тронул поле вручную.
          var el = byId("supAiGoal");
          if (el && !(el.value || "").trim() && profile.supplement_goal) {
            el.value = profile.supplement_goal;
            state.improvementGoal = profile.supplement_goal;
            syncChips();
          }
        }
      })
      .catch(function () {
        // Профиль не критичен для этой страницы — молча игнорируем.
      });
  }

  /* =====================================================================
   *  ПРИВЯЗКА ОБРАБОТЧИКОВ
   * ===================================================================== */

  /**
   * Навешивает все обработчики событий после монтирования разметки.
   */
  function bindEvents() {
    // Форма добавки.
    var supForm = byId("supForm");
    if (supForm) supForm.addEventListener("submit", onSupplementSubmit);

    // Форма напоминания.
    var remForm = byId("supRemForm");
    if (remForm) remForm.addEventListener("submit", onReminderSubmit);

    // Чипы AI-целей.
    var chipsBox = byId("supAiChips");
    if (chipsBox) {
      var chips = chipsBox.querySelectorAll(".sup-ai-chip");
      for (var i = 0; i < chips.length; i++) {
        chips[i].addEventListener("click", onChipClick);
      }
    }

    // Поле свободного ввода цели — синхронизируем подсветку чипов.
    var goalEl = byId("supAiGoal");
    if (goalEl) {
      goalEl.addEventListener("input", function () {
        state.improvementGoal = (goalEl.value || "").trim();
        syncChips();
      });
    }

    // Кнопка «Получить совет».
    var aiBtn = byId("supAiBtn");
    if (aiBtn) aiBtn.addEventListener("click", onAiRequest);
  }

  /* =====================================================================
   *  КОНТРОЛЛЕР СТРАНИЦЫ
   * ===================================================================== */

  var controller = {
    /**
     * Вызывается при показе страницы: строит разметку, вешает обработчики,
     * загружает добавки и напоминания, предзаполняет цель AI-советов.
     */
    onShow: function (viewEl) {
      state.viewEl = viewEl;

      // Гейтинг: добавки — премиум-функция. Если подписки нет, показываем
      // единый paywall и выходим (доступ контролируется сервером).
      // Пейволл рисуется ПОД шапкой вкладки, а не вместо всего экрана: это
      // вкладка таббара, и без заголовка она выглядела бы случайной заглушкой.
      // Заголовок карточки поэтому не повторяет «Добавки» из шапки, а
      // называет пользу.
      if (App && typeof App.isPremium === "function" && !App.isPremium()) {
        viewEl.innerHTML =
          '<section class="page page-supplements page-supplements--locked">' +
          headHtml() +
          '<div id="supGate"></div>' +
          "</section>";
        App.paywall(byId("supGate"), {
          // icon — ИМЯ иконки из js/icons.js (App.paywall сам её рисует).
          icon: "pill",
          title: pick("Приём добавок под контролем", "Stay on top of your supplements"),
          desc: pick(
            "Спортпит, напоминания и AI-советы под вашу цель",
            "Sports nutrition, reminders and AI advice for your goal"
          ),
          bullets: paywallBullets()
        });
        // Пейволл — тот же тёмный блок с фотографией, что и карточка подписки
        // в профиле: разметку рисует App.paywall, а фон и палитру задаём
        // здесь, чтобы не трогать общий код.
        // Путь абсолютный: относительный url() внутри custom property Chrome
        // разрешает от адреса style.css, а не от страницы (уходил в 404).
        var gateCard = byId("supGate") && byId("supGate").querySelector(".paywall-card");
        if (gateCard) {
          // .card задаёт background сокращённо и стирал бы фотографию блока.
          gateCard.classList.remove("card");
          gateCard.classList.add("hero", "hero--img");
          gateCard.style.cssText += ";" + App.heroImg("hero-premium.jpg");
        }
        App.scrollTop();
        return;
      }

      state.supLoading = false;
      state.remLoading = false;
      state.supplements = [];

      viewEl.innerHTML = pageTemplate();

      // Стартовое пустое состояние чекбоксов напоминаний (до загрузки добавок).
      renderReminderPicks();

      bindEvents();

      // Параллельно загружаем добавки и напоминания.
      loadSupplements();
      loadReminders();

      // Предзаполняем цель улучшения для AI-советов.
      prefillImprovementGoal();

      // Прокрутка наверх, чтобы экран не «залип» прокрученным вниз.
      App.scrollTop();
    },

    /**
     * Вызывается при уходе со страницы — освобождаем ссылки на DOM.
     */
    onHide: function () {
      state.viewEl = null;
      state.supLoading = false;
      state.remLoading = false;
      state.supplements = [];
    }
  };

  // Публикуем контроллер и регистрируем страницу.
  window.PageSupplements = controller;
  if (window.App && typeof App.registerPage === "function") {
    App.registerPage("supplements", controller);
  }
})();
