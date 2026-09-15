/*
 * page-trainer-onboarding.js — анкета тренера (страница "trainer-onboarding").
 *
 * Регистрирует контроллер через App.registerPage("trainer-onboarding", {...}).
 * Публичная ссылка — window.PageTrainerOnboarding.
 *
 * 9 шагов (ТЗ §2.2): цель → уровень → где и с чем → дни в неделю → длительность
 * → ограничения → акцент → напоминание → длина программы + сводка.
 * Ответы копятся в state.answers и отправляются ОДИН раз на шаге 9:
 *   POST /trainer/profile → POST /trainer/program/generate (экран ожидания)
 *   → превью программы (Trainer.openProgram(program, "preview")).
 * Ошибка генерации → карточка .wk-error с «Повторить» (профиль уже сохранён).
 *
 * Режим редактирования (App.state.trainerEdit=true, «Настройки» на главной
 * тренера): анкета предзаполняется из профиля, кнопка «Сохранить»; при активной
 * программе после сохранения предлагаем пересобрать её.
 *
 * Зависимости: window.Trainer (trainer-common.js), App.api.trainer*.
 * Локализация: все строки — App.pick(ru, en) в момент рендера.
 */
(function () {
  "use strict";

  var T = window.Trainer;

  function pick(ru, en) {
    return App.pick(ru, en);
  }

  function esc(s) {
    return App.escapeHtml(s == null ? "" : String(s));
  }

  function byId(id) {
    return document.getElementById(id);
  }

  var TOTAL_STEPS = 9;
  var LIMIT_TEXT_MAX = 300;

  /* =====================================================================
   *  СПРАВОЧНИКИ ШАГОВ (коды = TrainerProfileIn; подписи — Trainer.L)
   * ===================================================================== */

  var GOALS = [
    { key: "loss", icon: "🔥" },
    { key: "muscle", icon: "💪" },
    { key: "strength", icon: "🏋️" },
    { key: "endurance", icon: "🏃" },
    { key: "tone", icon: "✨" }
  ];

  var LEVELS = [
    { key: "beginner", icon: "🌱", desc: ["меньше 6 месяцев", "less than 6 months"] },
    { key: "intermediate", icon: "🌿", desc: ["6 мес.–2 года регулярно", "6 months – 2 years, regularly"] },
    { key: "advanced", icon: "🌳", desc: ["2+ года", "2+ years"] }
  ];

  var EQUIPMENT = [
    { key: "gym", icon: "🏟️", desc: ["Штанги, гантели, тренажёры", "Barbells, dumbbells, machines"] },
    { key: "home_dumbbells", icon: "🏠", desc: ["Дома с гантелями/резинками", "At home with dumbbells/bands"] },
    { key: "bodyweight", icon: "🤸", desc: ["Только вес тела", "Bodyweight only"] }
  ];

  // Доп. чипы оборудования; barbell — только для home_dumbbells (ТЗ §2.2).
  var EXTRA_EQUIPMENT = ["pullup_bar", "bands", "bench", "kettlebell", "barbell", "cardio_machine"];

  var DAYS = [2, 3, 4, 5, 6];
  var DAY_PRESETS = {
    2: [0, 3],
    3: [0, 2, 4],
    4: [0, 1, 3, 4],
    5: [0, 1, 2, 3, 4],
    6: [0, 1, 2, 3, 4, 5]
  };

  var MINUTES = [20, 30, 45, 60, 75, 90];

  var LIMITATIONS = [
    { key: "knee", icon: "🦵" },
    { key: "lower_back", icon: "🧍" },
    { key: "shoulder", icon: "🤷" },
    { key: "wrist", icon: "✋" },
    { key: "neck", icon: "🧠" },
    { key: "hip", icon: "🦴" },
    { key: "pregnancy", icon: "🤰" },
    { key: "heart_bp", icon: "❤️" },
    { key: "none", icon: "✅" }
  ];

  var FOCUS = [
    { key: "glutes", icon: "🍑" },
    { key: "core", icon: "🎯" },
    { key: "back", icon: "🔙" },
    { key: "chest", icon: "🫁" },
    { key: "shoulders", icon: "🤷" },
    { key: "arms", icon: "💪" },
    { key: "legs", icon: "🦵" },
    { key: "none", icon: "⚖️" }
  ];

  var WEEKS = [4, 6, 8];

  // Внутреннее состояние контроллера.
  var state = {
    viewEl: null,
    step: 1,
    edit: false,          // режим редактирования настроек
    answers: null,        // ответы анкеты (см. defaultAnswers)
    weeksTouched: false,  // пользователь сам выбрал длину программы
    hasProgram: false,    // есть активная программа (для edit)
    busy: false,          // идёт сохранение/генерация
    loading: false
  };

  /* =====================================================================
   *  ОТВЕТЫ
   * ===================================================================== */

  /**
   * Ответы по умолчанию для первого прохождения. Цель подсвечиваем по
   * profile.diet_goal (loss→loss, gain→muscle).
   */
  function defaultAnswers() {
    var p = App.state.profile || {};
    var goal = null;
    if (p.diet_goal === "loss") goal = "loss";
    else if (p.diet_goal === "gain") goal = "muscle";
    return {
      goal: goal,
      level: null,
      equipment: null,
      equipment_extra: [],
      days_per_week: 3,
      preferred_weekdays: DAY_PRESETS[3].slice(),
      session_minutes: 45,
      limitations: [],
      limitations_text: "",
      focus: [],
      reminder_enabled: true,
      reminder_time: "18:00",
      program_weeks: 6
    };
  }

  /**
   * Список строк из массива / JSON-строки / CSV (защита от формата ответа).
   */
  function toList(v) {
    if (Array.isArray(v)) return v.slice();
    if (typeof v === "string" && v.trim()) {
      var s = v.trim();
      if (s.charAt(0) === "[") {
        try {
          var parsed = JSON.parse(s);
          return Array.isArray(parsed) ? parsed : [];
        } catch (e) {
          return [];
        }
      }
      return s.split(",").map(function (x) {
        return x.trim();
      }).filter(Boolean);
    }
    return [];
  }

  /** Список целых 0..6 (дни недели) из массива или CSV «0,2,4». */
  function toIntList(v) {
    var out = [];
    var raw = toList(v);
    for (var i = 0; i < raw.length; i++) {
      var n = parseInt(raw[i], 10);
      if (!isNaN(n) && out.indexOf(n) === -1) out.push(n);
    }
    return out;
  }

  /**
   * Ответы из сохранённого профиля (режим редактирования).
   */
  function answersFromProfile(p) {
    var a = defaultAnswers();
    if (!p) return a;
    a.goal = p.goal || a.goal;
    a.level = p.level || null;
    a.equipment = p.equipment || null;
    a.equipment_extra = toList(p.equipment_extra);
    a.days_per_week = p.days_per_week || 3;
    a.preferred_weekdays = toIntList(p.preferred_weekdays);
    if (!a.preferred_weekdays.length) a.preferred_weekdays = (DAY_PRESETS[a.days_per_week] || []).slice();
    a.session_minutes = p.session_minutes || 45;
    a.limitations = toList(p.limitations);
    if (!a.limitations.length) a.limitations = ["none"];
    a.limitations_text = p.limitations_text || "";
    a.focus = toList(p.focus);
    a.reminder_enabled = !!p.reminder_enabled;
    a.reminder_time = p.reminder_time || "18:00";
    a.program_weeks = p.program_weeks || 6;
    return a;
  }

  /**
   * Тело POST /trainer/profile (TrainerProfileIn).
   */
  function buildPayload() {
    var a = state.answers;
    var limits = [];
    for (var i = 0; i < a.limitations.length; i++) {
      if (a.limitations[i] !== "none") limits.push(a.limitations[i]);
    }
    var focus = [];
    for (var j = 0; j < a.focus.length; j++) {
      if (a.focus[j] !== "none") focus.push(a.focus[j]);
    }
    var extra = a.equipment === "gym" ? [] : a.equipment_extra.slice();
    if (a.equipment !== "home_dumbbells") {
      extra = extra.filter(function (k) {
        return k !== "barbell";
      });
    }
    var text = (a.limitations_text || "").trim().slice(0, LIMIT_TEXT_MAX);
    var days = a.preferred_weekdays.slice().sort(function (x, y) {
      return x - y;
    });
    return {
      goal: a.goal,
      level: a.level,
      equipment: a.equipment,
      equipment_extra: extra,
      days_per_week: a.days_per_week,
      preferred_weekdays: days,
      session_minutes: a.session_minutes,
      program_weeks: a.program_weeks,
      limitations: limits,
      limitations_text: text || null,
      focus: focus,
      reminder_enabled: !!a.reminder_enabled,
      reminder_time: a.reminder_time || "18:00"
    };
  }

  /**
   * Проверка шага. Возвращает текст ошибки или null, если можно идти дальше.
   */
  function validate(step) {
    var a = state.answers;
    switch (step) {
      case 1:
        return a.goal ? null : pick("Выберите цель", "Pick a goal");
      case 2:
        return a.level ? null : pick("Выберите уровень", "Pick your level");
      case 3:
        return a.equipment ? null : pick("Выберите, где будете тренироваться", "Pick where you train");
      case 4:
        if (!a.days_per_week) return pick("Выберите число дней", "Pick the number of days");
        if (a.preferred_weekdays.length !== a.days_per_week) {
          return pick("Отметьте ", "Select ") + a.days_per_week + pick(" дня недели", " weekdays");
        }
        return null;
      case 5:
        return a.session_minutes ? null : pick("Выберите длительность", "Pick a duration");
      case 6:
        return a.limitations.length ? null : pick("Отметьте ограничения или «Нет»", "Select limitations or “None”");
      case 8:
        if (a.reminder_enabled && !/^\d{2}:\d{2}$/.test(a.reminder_time || "")) {
          return pick("Укажите время напоминания", "Set a reminder time");
        }
        return null;
      case 9:
        return a.program_weeks ? null : pick("Выберите длину программы", "Pick the program length");
      default:
        return null;
    }
  }

  /* =====================================================================
   *  РАЗМЕТКА
   * ===================================================================== */

  function stepTitle(step) {
    switch (step) {
      case 1: return pick("Какая цель?", "What is your goal?");
      case 2: return pick("Ваш уровень", "Your level");
      case 3: return pick("Где и с чем тренируетесь?", "Where and with what do you train?");
      case 4: return pick("Сколько раз в неделю?", "How many days a week?");
      case 5: return pick("Сколько времени на тренировку?", "How long per session?");
      case 6: return pick("Ограничения и травмы", "Limitations and injuries");
      case 7: return pick("На что сделать акцент?", "What to focus on?");
      case 8: return pick("Напоминание о тренировке", "Workout reminder");
      case 9: return pick("Длина программы", "Program length");
      default: return "";
    }
  }

  function stepHint(step) {
    switch (step) {
      case 1: return pick("От цели зависят диапазоны повторов, отдых и кардио.", "Your goal sets rep ranges, rest and cardio.");
      case 2: return pick("Уровень определяет стартовые веса и темп прогрессии.", "Level sets starting weights and progression pace.");
      case 3: return pick("Выберите основной вариант и отметьте, что ещё есть.", "Pick the main option and mark what else you have.");
      case 4: return pick("≤3 дня — full body, 4 — верх/низ, 5–6 — push/pull/legs.", "≤3 days — full body, 4 — upper/lower, 5–6 — push/pull/legs.");
      case 5: return pick("Число упражнений подстроится под время.", "The number of exercises adapts to the time.");
      case 6: return pick("Можно выбрать несколько. Тренер подберёт безопасные варианты.", "Select any that apply. The coach will pick safe options.");
      case 7: return pick("Необязательно. Можно выбрать несколько.", "Optional. Select any.");
      case 8: return pick("Бот напомнит в дни тренировок и покажет план дня.", "The bot will remind you on training days and show the day’s plan.");
      case 9: return pick("Каждая 4-я неделя — разгрузочная. Потом соберём новую программу.", "Every 4th week is a deload. Then we build a new program.");
      default: return "";
    }
  }

  /**
   * Полоса прогресса из TOTAL_STEPS сегментов.
   */
  function progressHtml() {
    var html = "";
    for (var i = 1; i <= TOTAL_STEPS; i++) {
      var cls = "tr-onb-progress__seg";
      if (i < state.step) cls += " is-done";
      else if (i === state.step) cls += " is-current";
      html += '<span class="' + cls + '"></span>';
    }
    return (
      '<div class="tr-onb-progress" role="progressbar" aria-valuemin="1" aria-valuemax="' + TOTAL_STEPS +
      '" aria-valuenow="' + state.step + '">' + html + "</div>"
    );
  }

  /**
   * Карточка-опция (.tr-option).
   * @param {object} o {key, icon, title, desc}
   * @param {string} group имя группы ответа (data-group)
   * @param {boolean} active
   * @param {boolean} [compact] вертикальная раскладка для сеток
   */
  function optionHtml(o, group, active, compact) {
    return (
      '<button type="button" class="tr-option' + (compact ? " tr-option--compact" : "") + (active ? " is-active" : "") +
      '" data-opt="' + esc(o.key) + '" data-group="' + esc(group) + '" aria-pressed="' + (active ? "true" : "false") + '">' +
      (o.icon ? '<span class="tr-option__icon" aria-hidden="true">' + o.icon + "</span>" : "") +
      '<span class="tr-option__body">' +
      '<span class="tr-option__title">' + esc(o.title) + "</span>" +
      (o.desc ? '<span class="tr-option__desc">' + esc(o.desc) + "</span>" : "") +
      "</span>" +
      '<span class="tr-option__mark" aria-hidden="true">✓</span>' +
      "</button>"
    );
  }

  function chipHtml(attr, value, label, active) {
    return (
      '<button type="button" class="chip' + (active ? " chip--active" : "") + '" ' + attr + '="' + esc(value) +
      '" aria-pressed="' + (active ? "true" : "false") + '">' + esc(label) + "</button>"
    );
  }

  function inList(list, key) {
    return list.indexOf(key) !== -1;
  }

  /** Шаг 1 — цель. */
  function step1Html() {
    var a = state.answers;
    var html = "";
    for (var i = 0; i < GOALS.length; i++) {
      var g = GOALS[i];
      html += optionHtml({ key: g.key, icon: g.icon, title: T.label("goal", g.key) }, "goal", a.goal === g.key);
    }
    return '<div class="tr-option-grid">' + html + "</div>";
  }

  /** Шаг 2 — уровень. */
  function step2Html() {
    var a = state.answers;
    var html = "";
    for (var i = 0; i < LEVELS.length; i++) {
      var l = LEVELS[i];
      html += optionHtml(
        { key: l.key, icon: l.icon, title: T.label("level", l.key), desc: pick(l.desc[0], l.desc[1]) },
        "level",
        a.level === l.key
      );
    }
    return '<div class="tr-option-grid">' + html + "</div>";
  }

  /** Шаг 3 — где и с чем (+ доп. чипы). */
  function step3Html() {
    var a = state.answers;
    var html = "";
    for (var i = 0; i < EQUIPMENT.length; i++) {
      var e = EQUIPMENT[i];
      html += optionHtml(
        { key: e.key, icon: e.icon, title: T.label("equipment", e.key), desc: pick(e.desc[0], e.desc[1]) },
        "equipment",
        a.equipment === e.key
      );
    }
    var extras = "";
    if (a.equipment && a.equipment !== "gym") {
      var chips = "";
      for (var j = 0; j < EXTRA_EQUIPMENT.length; j++) {
        var key = EXTRA_EQUIPMENT[j];
        if (key === "barbell" && a.equipment !== "home_dumbbells") continue;
        chips += chipHtml("data-extra", key, T.label("equipment", key), inList(a.equipment_extra, key));
      }
      extras =
        '<span class="tr-onb-label">' + esc(pick("Что ещё есть?", "What else do you have?")) + "</span>" +
        '<div class="tr-onb-chips">' + chips + "</div>";
    }
    return (
      '<div class="tr-option-grid">' + html + "</div>" +
      extras +
      '<div class="tr-onb-quip"><span aria-hidden="true">💡</span><span>' +
      esc(pick("Программа строится только из доступного оборудования.", "The program uses only the equipment you have.")) +
      "</span></div>"
    );
  }

  /** Шаг 4 — дни в неделю + дни недели. */
  function step4Html() {
    var a = state.answers;
    var chips = "";
    for (var i = 0; i < DAYS.length; i++) {
      chips += chipHtml("data-days", DAYS[i], String(DAYS[i]), a.days_per_week === DAYS[i]);
    }
    var wd = "";
    for (var d = 0; d < 7; d++) {
      var active = inList(a.preferred_weekdays, d);
      wd +=
        '<button type="button" class="wk-rem-day' + (active ? " is-active" : "") + '" data-wd="' + d +
        '" aria-pressed="' + (active ? "true" : "false") + '">' + esc(T.label("weekday", d)) + "</button>";
    }
    var picked = a.preferred_weekdays.length;
    var counter = picked + pick(" из ", " of ") + a.days_per_week;
    return (
      '<div class="tr-onb-chips tr-onb-chips--even">' + chips + "</div>" +
      '<span class="tr-onb-label">' + esc(pick("В какие дни?", "Which days?")) + " · " + esc(counter) + "</span>" +
      '<div class="wk-rem-days">' + wd + "</div>"
    );
  }

  /** Шаг 5 — длительность. */
  function step5Html() {
    var a = state.answers;
    var chips = "";
    for (var i = 0; i < MINUTES.length; i++) {
      chips += chipHtml("data-min", MINUTES[i], MINUTES[i] + " " + pick("мин", "min"), a.session_minutes === MINUTES[i]);
    }
    return '<div class="tr-onb-chips tr-onb-chips--even">' + chips + "</div>";
  }

  /** Шаг 6 — ограничения + текст + дисклеймер. */
  function step6Html() {
    var a = state.answers;
    var html = "";
    for (var i = 0; i < LIMITATIONS.length; i++) {
      var l = LIMITATIONS[i];
      html += optionHtml({ key: l.key, icon: l.icon, title: T.label("limitation", l.key) }, "limitations", inList(a.limitations, l.key), true);
    }
    var text = a.limitations_text || "";
    return (
      '<div class="tr-option-grid tr-option-grid--3">' + html + "</div>" +
      '<label class="field" style="margin-top:14px">' +
      '<span class="field__label">' + esc(pick("Опишите подробнее (необязательно)", "Describe in more detail (optional)")) + "</span>" +
      '<textarea class="field__input tr-onb-textarea" id="trOnbLimitText" maxlength="' + LIMIT_TEXT_MAX + '" placeholder="' +
      esc(pick("напр. болит правое колено при глубоком приседе", "e.g. right knee hurts in deep squats")) + '">' +
      esc(text) +
      "</textarea>" +
      '<span class="tr-onb-count" id="trOnbLimitCount">' + text.length + "/" + LIMIT_TEXT_MAX + "</span>" +
      "</label>" +
      '<p class="rec-disclaimer">' +
      esc(pick(
        "Тренер не врач. При травмах и хронических состояниях проконсультируйтесь со специалистом.",
        "The coach is not a doctor. Consult a specialist for injuries and chronic conditions."
      )) +
      "</p>"
    );
  }

  /** Шаг 7 — акцент. */
  function step7Html() {
    var a = state.answers;
    var html = "";
    for (var i = 0; i < FOCUS.length; i++) {
      var f = FOCUS[i];
      html += optionHtml({ key: f.key, icon: f.icon, title: T.label("muscle", f.key) }, "focus", inList(a.focus, f.key), true);
    }
    return '<div class="tr-option-grid tr-option-grid--2">' + html + "</div>";
  }

  /** Шаг 8 — напоминание. */
  function step8Html() {
    var a = state.answers;
    var on = !!a.reminder_enabled;
    return (
      '<button type="button" class="tr-toggle' + (on ? " tr-toggle--on" : "") + '" id="trOnbReminder" aria-pressed="' + (on ? "true" : "false") + '">' +
      '<span class="tr-toggle__emoji" aria-hidden="true">🔔</span>' +
      '<span class="tr-toggle__text">' +
      '<span class="tr-toggle__title">' + esc(pick("Напоминать в дни тренировок", "Remind me on training days")) + "</span>" +
      '<span class="tr-toggle__hint">' + esc(pick("Сообщение от бота с планом дня", "A bot message with the day’s plan")) + "</span>" +
      "</span>" +
      '<span class="tr-toggle__mark" aria-hidden="true">✓</span>' +
      "</button>" +
      '<div id="trOnbTimeWrap" style="margin-top:14px"' + (on ? "" : " hidden") + ">" +
      '<label class="field">' +
      '<span class="field__label">' + esc(pick("Время", "Time")) + "</span>" +
      '<input class="field__input" id="trOnbTime" type="time" value="' + esc(a.reminder_time || "18:00") + '">' +
      "</label>" +
      "</div>"
    );
  }

  /** Строка сводки. */
  function summaryRow(key, val) {
    return (
      '<div class="tr-onb-summary__row">' +
      '<span class="tr-onb-summary__key">' + esc(key) + "</span>" +
      '<span class="tr-onb-summary__val">' + esc(val) + "</span>" +
      "</div>"
    );
  }

  /** Шаг 9 — длина программы + сводка + проверка профиля. */
  function step9Html() {
    var a = state.answers;
    var html = "";
    for (var i = 0; i < WEEKS.length; i++) {
      var w = WEEKS[i];
      html += optionHtml(
        {
          key: String(w),
          icon: w === 4 ? "🗓️" : w === 6 ? "📆" : "🏁",
          title: w + " " + pick("нед.", "wk"),
          desc: w === 4 ? pick("Быстрый старт", "Quick start") : w === 6 ? pick("Оптимально", "Optimal") : pick("Максимум", "Maximum")
        },
        "program_weeks",
        a.program_weeks === w,
        true
      );
    }

    var p = App.state.profile || {};
    var warn = "";
    if (p.weight == null || !p.gender || p.age == null) {
      warn =
        '<div class="tr-warn"><span class="tr-warn__icon" aria-hidden="true">⚠️</span><span>' +
        esc(pick(
          "Заполните вес и пол в аккаунте — стартовые веса будут точнее.",
          "Fill in weight and gender in your account — starting weights will be more accurate."
        )) +
        "</span></div>";
    }

    var extras = a.equipment === "gym" ? [] : a.equipment_extra;
    var equipText = T.label("equipment", a.equipment);
    if (extras.length) equipText += " + " + T.labels("equipment", extras);
    var daysText = a.days_per_week + pick(" дн./нед. · ", " days/wk · ") + T.labels("weekday", a.preferred_weekdays.slice().sort(function (x, y) { return x - y; }));
    var limits = a.limitations.filter(function (k) { return k !== "none"; });
    var limitText = limits.length ? T.labels("limitation", limits) : pick("Нет", "None");
    if (limits.length && a.limitations_text) limitText += " · " + a.limitations_text;
    var focus = a.focus.filter(function (k) { return k !== "none"; });
    var focusText = focus.length ? T.labels("muscle", focus) : pick("Без акцента", "No focus");
    var remText = a.reminder_enabled ? (a.reminder_time || "18:00") : pick("Выключено", "Off");

    var summary =
      '<div class="tr-onb-summary">' +
      summaryRow(pick("Цель", "Goal"), T.label("goal", a.goal)) +
      summaryRow(pick("Уровень", "Level"), T.label("level", a.level)) +
      summaryRow(pick("Оборудование", "Equipment"), equipText) +
      summaryRow(pick("Дни", "Days"), daysText) +
      summaryRow(pick("Длительность", "Duration"), a.session_minutes + " " + pick("мин", "min")) +
      summaryRow(pick("Ограничения", "Limitations"), limitText) +
      summaryRow(pick("Акцент", "Focus"), focusText) +
      summaryRow(pick("Напоминание", "Reminder"), remText) +
      "</div>";

    return '<div class="tr-option-grid tr-option-grid--3">' + html + "</div>" + '<div style="margin-top:14px">' + warn + "</div>" + summary;
  }

  function stepBodyHtml(step) {
    switch (step) {
      case 1: return step1Html();
      case 2: return step2Html();
      case 3: return step3Html();
      case 4: return step4Html();
      case 5: return step5Html();
      case 6: return step6Html();
      case 7: return step7Html();
      case 8: return step8Html();
      case 9: return step9Html();
      default: return "";
    }
  }

  /**
   * Кнопки «Назад» / «Далее» (на шаге 9 — «Собрать программу» / «Сохранить»).
   */
  function navHtml(step) {
    var nextLabel;
    if (step < TOTAL_STEPS) nextLabel = pick("Далее", "Next");
    else nextLabel = state.edit ? pick("Сохранить", "Save") : pick("Собрать программу", "Build my program");
    return (
      '<div class="tr-onb-nav">' +
      '<button type="button" class="btn btn-ghost tr-onb-nav__back" id="trOnbBack">' + esc(pick("Назад", "Back")) + "</button>" +
      '<button type="button" class="btn btn-cta tr-onb-nav__next" id="trOnbNext"' + (state.busy ? " disabled" : "") + ">" +
      esc(nextLabel) +
      "</button>" +
      "</div>"
    );
  }

  /**
   * Полный каркас страницы: шапка + тело (#trOnbBody: прогресс + карточка шага).
   */
  function shellHtml() {
    var title = state.edit ? pick("Настройки тренера", "Coach settings") : pick("Тренер", "Coach");
    var subtitle = pick("Шаг ", "Step ") + state.step + pick(" из ", " of ") + TOTAL_STEPS + " · " + stepTitle(state.step);
    return (
      '<section class="page sub-page tr-page tr-onb">' +
      T.headHtml({ icon: "🧑‍🏫", title: title, subtitle: subtitle }) +
      '<div id="trOnbBody">' +
      progressHtml() +
      '<div class="card tr-onb-step" id="trOnbCard"></div>' +
      "</div>" +
      "</section>"
    );
  }

  /* =====================================================================
   *  РЕНДЕР
   * ===================================================================== */

  /**
   * Полная перерисовка (смена шага): шапка, прогресс, карточка.
   */
  function render() {
    if (!state.viewEl) return;
    state.viewEl.innerHTML = shellHtml();
    T.bindBack(state.viewEl);
    var card = byId("trOnbCard");
    if (card) card.addEventListener("click", onCardClick);
    renderStep();
    App.scrollTop();
  }

  /**
   * Перерисовка только карточки текущего шага (после выбора опции).
   */
  function renderStep() {
    var card = byId("trOnbCard");
    if (!card) return;
    card.innerHTML =
      '<h2 class="tr-onb-step__title">' + esc(stepTitle(state.step)) + "</h2>" +
      '<p class="tr-onb-step__hint">' + esc(stepHint(state.step)) + "</p>" +
      stepBodyHtml(state.step) +
      navHtml(state.step);
    bindInputs();
  }

  /**
   * Обработчики полей ввода текущего шага (textarea, time).
   */
  function bindInputs() {
    var ta = byId("trOnbLimitText");
    if (ta) {
      ta.addEventListener("input", function () {
        state.answers.limitations_text = ta.value.slice(0, LIMIT_TEXT_MAX);
        var c = byId("trOnbLimitCount");
        if (c) c.textContent = state.answers.limitations_text.length + "/" + LIMIT_TEXT_MAX;
      });
    }
    var time = byId("trOnbTime");
    if (time) {
      time.addEventListener("change", function () {
        state.answers.reminder_time = time.value || "18:00";
      });
    }
  }

  /**
   * Делегированный обработчик тапов по карточке шага.
   */
  function onCardClick(ev) {
    var a = state.answers;
    if (!a) return;
    var t = ev.target;

    var opt = t.closest("[data-opt]");
    if (opt) {
      App.haptic("selection");
      onOption(opt.getAttribute("data-group"), opt.getAttribute("data-opt"));
      return;
    }
    var days = t.closest("[data-days]");
    if (days) {
      App.haptic("selection");
      var n = parseInt(days.getAttribute("data-days"), 10);
      a.days_per_week = n;
      a.preferred_weekdays = (DAY_PRESETS[n] || []).slice();
      renderStep();
      return;
    }
    var wd = t.closest("[data-wd]");
    if (wd) {
      var d = parseInt(wd.getAttribute("data-wd"), 10);
      var idx = a.preferred_weekdays.indexOf(d);
      if (idx !== -1) {
        a.preferred_weekdays.splice(idx, 1);
      } else if (a.preferred_weekdays.length >= a.days_per_week) {
        App.haptic("warning");
        App.toast(pick("Уже выбрано ", "Already selected ") + a.days_per_week + pick(" дня. Снимите лишний.", " days. Deselect one first."));
        return;
      } else {
        a.preferred_weekdays.push(d);
      }
      App.haptic("selection");
      renderStep();
      return;
    }
    var min = t.closest("[data-min]");
    if (min) {
      App.haptic("selection");
      a.session_minutes = parseInt(min.getAttribute("data-min"), 10);
      renderStep();
      return;
    }
    var extra = t.closest("[data-extra]");
    if (extra) {
      App.haptic("selection");
      toggleIn(a.equipment_extra, extra.getAttribute("data-extra"));
      renderStep();
      return;
    }
    if (t.closest("#trOnbReminder")) {
      App.haptic("selection");
      a.reminder_enabled = !a.reminder_enabled;
      renderStep();
      return;
    }
    if (t.closest("#trOnbBack")) {
      App.haptic("light");
      prev();
      return;
    }
    if (t.closest("#trOnbNext")) {
      next();
    }
  }

  function toggleIn(list, key) {
    var i = list.indexOf(key);
    if (i === -1) list.push(key);
    else list.splice(i, 1);
  }

  /**
   * Выбор опции: одиночные группы заменяют значение, множественные — переключают
   * (с эксклюзивным «none»).
   */
  function onOption(group, key) {
    var a = state.answers;
    if (group === "goal" || group === "level") {
      a[group] = key;
    } else if (group === "equipment") {
      a.equipment = key;
      if (key === "gym") a.equipment_extra = [];
    } else if (group === "program_weeks") {
      a.program_weeks = parseInt(key, 10);
      state.weeksTouched = true;
    } else if (group === "limitations" || group === "focus") {
      var list = a[group];
      if (key === "none") {
        a[group] = inList(list, "none") ? [] : ["none"];
      } else {
        var ni = list.indexOf("none");
        if (ni !== -1) list.splice(ni, 1);
        toggleIn(list, key);
      }
    }
    renderStep();
  }

  /* =====================================================================
   *  ПЕРЕХОДЫ МЕЖДУ ШАГАМИ
   * ===================================================================== */

  function prev() {
    if (state.step <= 1) {
      T.back();
      return;
    }
    state.step--;
    render();
  }

  function next() {
    if (state.busy) return;
    var err = validate(state.step);
    if (err) {
      App.haptic("warning");
      App.toast(err);
      return;
    }
    App.haptic("light");
    if (state.step >= TOTAL_STEPS) {
      submit();
      return;
    }
    state.step++;
    // Для новичка по умолчанию подсвечиваем 4 недели (если не выбирал сам).
    if (state.step === TOTAL_STEPS && !state.weeksTouched && !state.edit) {
      state.answers.program_weeks = state.answers.level === "beginner" ? 4 : 6;
    }
    render();
  }

  /* =====================================================================
   *  ОТПРАВКА: ПРОФИЛЬ → ГЕНЕРАЦИЯ
   * ===================================================================== */

  function setBusy(flag) {
    state.busy = flag;
    var btn = byId("trOnbNext");
    if (btn) btn.disabled = !!flag;
  }

  function submit() {
    var payload = buildPayload();
    setBusy(true);
    App.api
      .trainerSaveProfile(payload)
      .then(function () {
        T.cache.invalidate();
        App.state.trainerBrief = { has_profile: true, kind: "no_program", in_progress: false };
        if (state.edit && state.hasProgram) {
          return T.confirm(
            pick("Пересобрать программу под новые настройки?", "Rebuild the program with the new settings?")
          ).then(function (ok) {
            if (ok) return generate();
            App.toast(pick("Настройки сохранены", "Settings saved"));
            finishEdit();
            App.navigate("trainer");
            return null;
          });
        }
        return generate();
      })
      .catch(function (err) {
        setBusy(false);
        App.haptic("error");
        App.toast(T.errMessage(err, pick("Не удалось сохранить анкету", "Failed to save the profile")));
      });
  }

  /** Сброс режима редактирования и ответов после завершения. */
  function finishEdit() {
    state.answers = null;
    state.step = 1;
    state.weeksTouched = false;
    state.busy = false;
    App.state.trainerEdit = false;
  }

  /**
   * Генерация программы с экраном ожидания; ошибка → карточка с «Повторить»
   * (профиль уже сохранён, повторный POST /profile не нужен).
   */
  function generate() {
    var body = byId("trOnbBody");
    if (!body) return Promise.resolve(null);
    return T.runGenerate(body, {})
      .then(function (program) {
        App.haptic("success");
        finishEdit();
        T.openProgram(program, "preview");
        return program;
      })
      .catch(function (err) {
        state.busy = false;
        var b = byId("trOnbBody");
        if (!b) return null;
        b.innerHTML =
          T.errorCard(
            T.errMessage(err, pick(
              "Не удалось собрать программу. Анкета сохранена — попробуйте ещё раз.",
              "Failed to build the program. Your profile is saved — please try again."
            )),
            "trOnbRetry"
          ) +
          '<button type="button" class="btn btn-ghost btn-block" id="trOnbHome">' +
          esc(pick("На главную тренера", "Coach home")) +
          "</button>";
        var retry = byId("trOnbRetry");
        if (retry) {
          retry.addEventListener("click", function () {
            App.haptic("light");
            generate();
          });
        }
        var home = byId("trOnbHome");
        if (home) {
          home.addEventListener("click", function () {
            App.haptic("light");
            App.state.trainerEdit = false;
            App.navigate("trainer");
          });
        }
        return null;
      });
  }

  /* =====================================================================
   *  ЗАГРУЗКА ПРОФИЛЯ (режим редактирования)
   * ===================================================================== */

  function loadForEdit() {
    if (!state.viewEl) return;
    state.loading = true;
    state.viewEl.innerHTML =
      '<section class="page sub-page tr-page tr-onb">' +
      T.headHtml({ icon: "🧑‍🏫", title: pick("Настройки тренера", "Coach settings") }) +
      T.skeleton(5) +
      "</section>";
    T.bindBack(state.viewEl);

    var cached = T.cache.overview;
    var source = cached
      ? Promise.resolve(cached)
      : App.api.trainerOverview();
    source
      .then(function (ov) {
        if (!state.viewEl) return;
        state.loading = false;
        ov = ov || {};
        state.hasProgram = !!ov.program;
        state.answers = answersFromProfile(ov.profile);
        state.weeksTouched = true;
        state.step = 1;
        render();
      })
      .catch(function (err) {
        if (!state.viewEl) return;
        state.loading = false;
        if (err && err.status === 402) {
          App.paywall(state.viewEl, T.paywallOpts());
          return;
        }
        // Профиля нет/ошибка — начинаем с пустой анкеты.
        state.hasProgram = false;
        state.answers = defaultAnswers();
        state.step = 1;
        render();
      });
  }

  /* =====================================================================
   *  КОНТРОЛЛЕР
   * ===================================================================== */

  var controller = {
    onShow: function (viewEl) {
      state.viewEl = viewEl;
      if (!App.requirePremium(viewEl, T.paywallOpts())) return;
      if (!App.state.trainerOrigin) App.state.trainerOrigin = "today";

      var edit = !!App.state.trainerEdit;
      if (edit !== state.edit) {
        // Смена режима — ответы прошлого режима не переиспользуем.
        state.answers = null;
        state.step = 1;
        state.weeksTouched = false;
      }
      state.edit = edit;
      state.busy = false;

      if (state.edit) {
        loadForEdit();
        return;
      }
      if (!state.answers) {
        state.answers = defaultAnswers();
        state.step = 1;
        state.weeksTouched = false;
      }
      render();
    },

    onHide: function () {
      state.viewEl = null;
      state.busy = false;
      state.loading = false;
      T.closeSheet(true);
    }
  };

  window.PageTrainerOnboarding = controller;
  App.registerPage("trainer-onboarding", controller);
})();
