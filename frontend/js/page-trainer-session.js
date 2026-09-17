/*
 * page-trainer-session.js — выполнение тренировки (страница "trainer-session").
 *
 * Регистрирует контроллер через App.registerPage("trainer-session", {...}).
 * Публичная ссылка — window.PageTrainerSession.
 *
 * Два экрана внутри одной страницы (ТЗ §2.5, §2.6):
 *   run    — sticky-шапка (закрыть, название, секундомер, «2/6 упражнений»),
 *            блоки разминки/заминки чек-листом, карточки упражнений с
 *            таблицей подходов «# | Прошлый раз | кг | повт | отметка», липкая
 *            плашка отдыха (−15/+15/Пропустить), тост рекорда, «Завершить»;
 *   finish — «Готово!», сетка итогов, карточки рекордов, отзыв
 *            (легко/норм/тяжело + заметка) и карточка «Учёл на следующий раз».
 *
 * Автосохранение: каждый отмеченный подход уходит на сервер
 * (POST /trainer/session/{id}/set), поэтому закрытие Mini App ничего не теряет —
 * при следующем входе «Продолжить» на «Сегодня» восстанавливает всё из
 * GET /trainer/session/active.
 *
 * После finish инвалидируем App.state.diaryByDate[session.date]: бэкенд создал
 * строку Workout, и баланс дня в дневнике изменился.
 *
 * Зависимости: window.Trainer (trainer-common.js) — L/label, exName, fmtKg,
 * fmtSet, fmtTarget, sheet, RestTimer, beep, skeleton, errorCard, confirm,
 * cache; App.api.trainer*. Локализация — App.pick(ru, en) в момент рендера.
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

  function icon(name, opts) {
    return T.icon(name, opts);
  }

  /**
   * Иконка «плей» для кнопки таймера строки. Кнопка переключается между
   * иконкой и обратным отсчётом, поэтому разметка нужна в двух местах:
   * при отрисовке строки и при остановке таймера.
   */
  function playIcon() {
    return icon("play", { size: 18 });
  }

  // Бэкенд принимает set_index 1..12 (TrainerSetIn), больше строк не рисуем.
  var MAX_SETS = 12;
  // Отдых по умолчанию, если сервер не прислал rest_sec.
  var REST_FALLBACK = 90;
  // Длина окружности кольца прогресса (r=19): 2πr.
  var RING_LEN = 119.4;

  // Внутреннее состояние контроллера.
  var state = {
    viewEl: null,        // #view
    rootEl: null,        // корневая секция страницы (пересоздаётся при render)
    session: null,       // TrainerSessionOut
    screen: "run",       // run|finish
    loading: false,      // идёт загрузка сессии
    finish: null,        // TrainerFinishOut после завершения
    feedback: null,      // выбранный отзыв easy|ok|hard
    adaptation: null,    // TrainerAdaptationOut («Учёл на следующий раз»)
    saving: {},          // {«sexId:index»: true} — подход сохраняется
    busy: false,         // идёт finish/abandon/замена — блокируем повторы
    draft: {},           // {«sexId:index»: {w,r,t}} — введённое, но не сохранённое
    rowCount: {},        // {sexId: сколько строк показывать (с «+ подход»)}
    folded: {},          // {warmup|cooldown: свёрнут ли блок}
    rest: null,          // Trainer.RestTimer (отдых)
    inline: null,        // Trainer.RestTimer (таймер упражнения на время)
    inlineKey: "",       // «sexId:index» строки с идущим inline-таймером
    clock: null,         // интервал секундомера сессии
    prTimer: null,       // таймер скрытия тоста рекорда
    reqId: 0             // счётчик загрузок (игнорируем устаревшие ответы)
  };

  /* =====================================================================
   *  СКЛОНЕНИЯ И МЕЛКОЕ ФОРМАТИРОВАНИЕ
   * ===================================================================== */

  /** Склонение «упражнение/упражнения/упражнений». */
  function exWord(n) {
    if (App.lang === "en") return n === 1 ? "exercise" : "exercises";
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return "упражнение";
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "упражнения";
    return "упражнений";
  }

  /** Склонение «подход/подхода/подходов». */
  function setWord(n) {
    if (App.lang === "en") return n === 1 ? "set" : "sets";
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return "подход";
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "подхода";
    return "подходов";
  }

  /** Секунды → «м:сс» или «ч:мм:сс» (секундомер сессии). */
  function fmtElapsed(sec) {
    var s = Math.max(0, Math.round(Number(sec) || 0));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var r = s % 60;
    var mm = m < 10 && h ? "0" + m : String(m);
    var ss = r < 10 ? "0" + r : String(r);
    return (h ? h + ":" : "") + mm + ":" + ss;
  }

  /** Значение рекорда с единицей измерения по типу. */
  function prValue(type, value) {
    if (type === "max_reps") return T.fmtNum(value) + " " + pick("повт", "reps");
    if (type === "max_time") return T.fmtNum(value) + " " + pick("с", "s");
    return T.fmtKg(value);
  }

  /** Текст тоста рекорда: «Рекорд: 1RM (расч.) 53 кг». */
  function prText(pr) {
    if (!pr) return "";
    return T.label("recordType", pr.type) + " " + prValue(pr.type, pr.value);
  }

  /* =====================================================================
   *  ДОСТУП К ДАННЫМ СЕССИИ
   * ===================================================================== */

  function sessionId() {
    if (state.session && state.session.id) return state.session.id;
    return App.state.trainerSessionId || null;
  }

  /** Упражнения блока (warmup|main|cooldown) без заменённых. */
  function exercisesOf(block) {
    var out = [];
    var list = (state.session && state.session.exercises) || [];
    for (var i = 0; i < list.length; i++) {
      var e = list[i] || {};
      if ((e.block || "main") !== block) continue;
      if (e.status === "replaced") continue;
      out.push(e);
    }
    return out;
  }

  function findSex(id) {
    var list = (state.session && state.session.exercises) || [];
    for (var i = 0; i < list.length; i++) {
      if (String(list[i].id) === String(id)) return list[i];
    }
    return null;
  }

  /** Сохранённый подход упражнения по номеру (или null). */
  function setAt(sex, index) {
    var sets = (sex && sex.sets) || [];
    for (var i = 0; i < sets.length; i++) {
      if (Number(sets[i].set_index) === Number(index)) return sets[i];
    }
    return null;
  }

  /** Подход «прошлого раза» по номеру (или null). */
  function prevAt(sex, index) {
    var prev = (sex && sex.previous) || [];
    for (var i = 0; i < prev.length; i++) {
      if (Number(prev[i].set_index) === Number(index)) return prev[i];
    }
    return null;
  }

  /**
   * Тип таблицы подходов: reps_weight (кг+повт), reps (только повт),
   * time (секунды + таймер). distance складывать некуда — ведём как время.
   */
  function measureOf(sex) {
    var m = (sex && sex.exercise && sex.exercise.measure_type) || "reps_weight";
    if (m === "reps") return "reps";
    if (m === "time" || m === "distance") return "time";
    return "reps_weight";
  }

  /** Отмечен ли пункт чек-листа разминки/заминки. */
  function isChecked(sex) {
    var sets = (sex && sex.sets) || [];
    for (var i = 0; i < sets.length; i++) {
      if (sets[i].is_done) return true;
    }
    return false;
  }

  /** Есть ли хотя бы один выполненный рабочий подход (условие «Завершить»). */
  function anyWorkDone() {
    var list = exercisesOf("main");
    for (var i = 0; i < list.length; i++) {
      var sets = list[i].sets || [];
      for (var j = 0; j < sets.length; j++) {
        if (sets[j].is_done && (sets[j].set_type || "work") === "work") return true;
      }
    }
    return false;
  }

  /** {done, total} по основным упражнениям (пропущенные считаем закрытыми). */
  function mainProgress() {
    var list = exercisesOf("main");
    var done = 0;
    for (var i = 0; i < list.length; i++) {
      if (list[i].status === "done" || list[i].status === "skipped") done++;
    }
    return { done: done, total: list.length };
  }

  /** Сколько строк подходов рисовать: план, факт и добавленные вручную. */
  function rowCount(sex) {
    var base = Math.max(1, sex.planned_sets || 1);
    var sets = sex.sets || [];
    for (var i = 0; i < sets.length; i++) {
      if (Number(sets[i].set_index) > base) base = Number(sets[i].set_index);
    }
    var want = state.rowCount[sex.id];
    var n = want && want > base ? want : base;
    return Math.min(MAX_SETS, n);
  }

  function draftKey(sexId, index) {
    return String(sexId) + ":" + String(index);
  }

  /**
   * Снимает введённые, но ещё не сохранённые значения полей — чтобы
   * перерисовка списка (замена/пропуск/чек-лист) их не стирала.
   */
  function collectDrafts() {
    if (!state.rootEl) return;
    var inputs = state.rootEl.querySelectorAll(".tr-set-input");
    for (var i = 0; i < inputs.length; i++) {
      var inp = inputs[i];
      var row = inp.closest ? inp.closest(".tr-set-row") : null;
      if (!row) continue;
      var key = draftKey(row.getAttribute("data-sex"), row.getAttribute("data-i"));
      if (!state.draft[key]) state.draft[key] = {};
      state.draft[key][inp.getAttribute("data-f")] = inp.value;
    }
  }

  /**
   * Значение поля строки: черновик пользователя → сохранённый подход →
   * цель плана → результат прошлого раза.
   * @param {string} field w|r|t
   */
  function fieldValue(sex, index, field) {
    var key = draftKey(sex.id, index);
    var draft = state.draft[key];
    if (draft && draft[field] !== undefined && draft[field] !== null) return draft[field];

    var saved = setAt(sex, index);
    var prev = prevAt(sex, index);
    if (field === "w") {
      if (saved && saved.weight_kg != null) return T.fmtNum(saved.weight_kg);
      if (sex.planned_weight_kg != null && Number(sex.planned_weight_kg) > 0) {
        return T.fmtNum(sex.planned_weight_kg);
      }
      if (prev && prev.weight_kg != null) return T.fmtNum(prev.weight_kg);
      return "";
    }
    if (field === "r") {
      if (saved && saved.reps != null) return String(saved.reps);
      // Повторы берём из прошлого раза: план задаёт коридор (8–12), а
      // повторить прошлый результат — минимальная цель на сегодня.
      if (prev && prev.reps != null) return String(prev.reps);
      if (sex.planned_reps_min != null) return String(sex.planned_reps_min);
      if (sex.planned_reps_max != null) return String(sex.planned_reps_max);
      return "";
    }
    if (saved && saved.time_sec != null) return String(saved.time_sec);
    if (sex.planned_time_sec != null) return String(sex.planned_time_sec);
    if (prev && prev.time_sec != null) return String(prev.time_sec);
    return "";
  }

  /* =====================================================================
   *  РАЗМЕТКА: ЭКРАН ВЫПОЛНЕНИЯ
   * ===================================================================== */

  /** Sticky-шапка сессии: закрыть, название дня, секундомер, «2/6 упражнений». */
  function headHtml() {
    var s = state.session || {};
    return (
      '<header class="tr-session-head">' +
      '<button type="button" class="tr-session-head__close" data-act="close" ' +
      'aria-label="' + esc(pick("Закрыть тренировку", "Close workout")) + '">' +
      icon("close", { size: 20 }) +
      "</button>" +
      '<div class="tr-session-head__body">' +
      '<div class="tr-session-head__title" id="trsTitle">' +
      esc(s.title || pick("Тренировка", "Workout")) +
      "</div>" +
      '<div class="tr-session-head__meta">' +
      '<span class="tr-session-timer num" id="trsTimer">0:00</span>' +
      '<span class="tr-session-progress" id="trsProgress"></span>' +
      "</div>" +
      "</div>" +
      "</header>"
    );
  }

  /** Короткая цель пункта чек-листа: «5 мин», «×15», «2×10». */
  function checkMeta(sex) {
    var parts = [];
    if (sex.planned_time_sec) {
      var sec = Number(sex.planned_time_sec);
      parts.push(
        sec >= 60 && sec % 60 === 0
          ? Math.round(sec / 60) + " " + pick("мин", "min")
          : sec + " " + pick("с", "s")
      );
    }
    var reps = sex.planned_reps_max != null ? sex.planned_reps_max : sex.planned_reps_min;
    if (reps != null) {
      parts.push((sex.planned_sets && sex.planned_sets > 1 ? sex.planned_sets + "×" : "×") + reps);
    }
    return parts.join(" · ");
  }

  /** Строка чек-листа разминки/заминки. */
  function checkRowHtml(sex) {
    var done = isChecked(sex);
    var meta = checkMeta(sex);
    return (
      '<button type="button" class="tr-check-row' + (done ? " tr-check-row--done" : "") +
      '" data-act="check-item" data-sex="' + esc(sex.id) + '" aria-pressed="' + (done ? "true" : "false") + '">' +
      '<span class="tr-check-row__mark" aria-hidden="true">' + (done ? icon("check", { size: 14 }) : "") + "</span>" +
      '<span class="tr-check-row__body">' +
      '<span class="tr-check-row__name">' + esc(T.exName(sex.exercise)) + "</span>" +
      (meta ? '<span class="tr-check-row__meta">' + esc(meta) + "</span>" : "") +
      "</span>" +
      "</button>"
    );
  }

  /** Блок разминки/заминки (сворачивается, по умолчанию свёрнут, если всё done). */
  function blockHtml(block) {
    var list = exercisesOf(block);
    if (!list.length) return "";
    var done = 0;
    var rows = "";
    for (var i = 0; i < list.length; i++) {
      if (isChecked(list[i])) done++;
      rows += checkRowHtml(list[i]);
    }
    var allDone = done === list.length;
    var folded = state.folded[block] === undefined ? allDone : !!state.folded[block];
    var title = block === "warmup" ? pick("Разминка", "Warm-up") : pick("Заминка", "Cool-down");
    return (
      '<section class="card tr-block tr-block--' + block + (folded ? " tr-block--folded" : "") +
      '" data-block="' + block + '">' +
      '<button type="button" class="tr-block__head" data-act="fold" data-block="' + block + '" ' +
      'aria-expanded="' + (folded ? "false" : "true") + '">' +
      '<span class="tr-block__title">' + esc(title) + "</span>" +
      '<span class="tr-block__count">' + done + "/" + list.length + "</span>" +
      '<span class="tr-block__chevron" aria-hidden="true">' + icon("chevron", { size: 18, rotate: 90 }) + "</span>" +
      "</button>" +
      '<div class="tr-block__body">' + rows + "</div>" +
      "</section>"
    );
  }

  /** Заголовок таблицы подходов (набор колонок зависит от типа измерения). */
  function setHeadHtml(measure) {
    var cells =
      '<span class="tr-set-cell tr-set-num">#</span>' +
      '<span class="tr-set-cell">' + esc(pick("Прошлый раз", "Previous")) + "</span>";
    if (measure === "reps_weight") {
      cells += '<span class="tr-set-cell">' + esc(pick("кг", "kg")) + "</span>";
    }
    if (measure === "time") {
      cells +=
        '<span class="tr-set-cell">' + esc(pick("сек", "sec")) + "</span>" +
        '<span class="tr-set-cell" aria-hidden="true"></span>';
    } else {
      cells += '<span class="tr-set-cell">' + esc(pick("повт", "reps")) + "</span>";
    }
    cells += '<span class="tr-set-cell" aria-hidden="true">' + icon("check", { size: 14 }) + "</span>";
    return '<div class="tr-set-head">' + cells + "</div>";
  }

  /** Одна строка подхода. */
  function setRowHtml(sex, index, measure) {
    var saved = setAt(sex, index);
    var prev = prevAt(sex, index);
    var done = !!(saved && saved.is_done);
    var stype = (saved && saved.set_type) || "work";
    var manual = index > Math.max(1, sex.planned_sets || 1);
    var locked = sex.status === "skipped";

    var isPr = !!(saved && saved.is_pr);
    var cls = "tr-set-row";
    if (done) cls += " tr-set-row--done";
    if (isPr) cls += " tr-set-row--pr";
    if (stype === "warmup") cls += " tr-set-row--warmup";

    // Кубок рекорда рисуем разметкой, а не через content в CSS: там он был
    // эмодзи, то есть картинкой операционной системы — своего размера и
    // своего цвета на каждом телефоне.
    var prMark = isPr
      ? icon("trophy", { size: 12, cls: "tr-set-pr" })
      : "";

    var numCell = manual
      ? '<span class="tr-set-cell tr-set-num">' +
        '<button type="button" class="tr-set-del" data-act="del" ' +
        'aria-label="' + esc(pick("Удалить подход", "Delete set")) + '">' +
        icon("close", { size: 16 }) +
        "</button>" +
        "</span>"
      : '<span class="tr-set-cell tr-set-num">' +
        (stype === "warmup" ? esc(pick("Р", "W")) : index) +
        prMark +
        "</span>";

    var prevText = prev ? T.fmtSet(prev.weight_kg, prev.reps, prev.time_sec) : "—";
    var prevCell =
      '<button type="button" class="tr-set-cell tr-set-prev" data-act="prev"' +
      (prev
        ? ' data-w="' + esc(prev.weight_kg == null ? "" : prev.weight_kg) + '"' +
          ' data-r="' + esc(prev.reps == null ? "" : prev.reps) + '"' +
          ' data-t="' + esc(prev.time_sec == null ? "" : prev.time_sec) + '"'
        : " disabled") +
      ">" + esc(prevText) + "</button>";

    var inputs = "";
    if (measure === "reps_weight") {
      inputs +=
        '<span class="tr-set-cell">' +
        '<input class="tr-set-input" type="number" inputmode="decimal" step="0.5" min="0" max="500" ' +
        'data-f="w" value="' + esc(fieldValue(sex, index, "w")) + '" ' +
        'aria-label="' + esc(pick("Вес, кг", "Weight, kg")) + '"' + (locked ? " disabled" : "") + ">" +
        "</span>";
    }
    if (measure === "time") {
      inputs +=
        '<span class="tr-set-cell">' +
        '<input class="tr-set-input" type="number" inputmode="numeric" step="1" min="0" max="7200" ' +
        'data-f="t" value="' + esc(fieldValue(sex, index, "t")) + '" ' +
        'aria-label="' + esc(pick("Время, с", "Time, s")) + '"' + (locked ? " disabled" : "") + ">" +
        "</span>" +
        '<span class="tr-set-cell">' +
        '<button type="button" class="tr-set-timer" data-act="itimer" ' +
        'aria-label="' + esc(pick("Запустить таймер", "Start timer")) + '">' +
        playIcon() +
        "</button>" +
        "</span>";
    } else {
      inputs +=
        '<span class="tr-set-cell">' +
        '<input class="tr-set-input" type="number" inputmode="numeric" step="1" min="0" max="100" ' +
        'data-f="r" value="' + esc(fieldValue(sex, index, "r")) + '" ' +
        'aria-label="' + esc(pick("Повторы", "Reps")) + '"' + (locked ? " disabled" : "") + ">" +
        "</span>";
    }

    return (
      '<div class="' + cls + '" data-sex="' + esc(sex.id) + '" data-i="' + index + '">' +
      numCell +
      prevCell +
      inputs +
      '<button type="button" class="tr-set-check' + (done ? " tr-set-check--done" : "") +
      '" data-act="check" aria-pressed="' + (done ? "true" : "false") + '"' +
      (locked ? " disabled" : "") +
      ' aria-label="' + esc(pick("Отметить подход", "Mark set")) + '">' +
      icon("check", { size: 20 }) +
      "</button>" +
      "</div>"
    );
  }

  /** Таблица подходов упражнения целиком. */
  function setTableHtml(sex) {
    var measure = measureOf(sex);
    var n = rowCount(sex);
    var rows = "";
    for (var i = 1; i <= n; i++) rows += setRowHtml(sex, i, measure);
    return (
      '<div class="tr-set-table tr-set-table--' + measure + '" data-sex="' + esc(sex.id) + '">' +
      setHeadHtml(measure) +
      rows +
      "</div>"
    );
  }

  /**
   * Текущее упражнение — первое незакрытое в основном блоке: его название
   * рисуется крупнее, чтобы рабочая карточка находилась взглядом сразу.
   */
  function isCurrent(sex) {
    var list = exercisesOf("main");
    for (var i = 0; i < list.length; i++) {
      if (list[i].status === "done" || list[i].status === "skipped") continue;
      return list[i] === sex;
    }
    return false;
  }

  /** Карточка упражнения основного блока. */
  function exCardHtml(sex) {
    var ex = sex.exercise || {};
    var cls = "card tr-ex-card";
    if (sex.status === "done") cls += " tr-ex-card--done";
    if (sex.status === "skipped") cls += " tr-ex-card--skipped";
    if (isCurrent(sex)) cls += " tr-ex-card--current";
    var badges = "";
    if (ex.muscle_group) {
      badges += '<span class="tr-ex-card__badge">' + esc(T.label("muscle", ex.muscle_group)) + "</span>";
    }
    if (ex.equipment) {
      badges +=
        '<span class="tr-ex-card__badge tr-ex-card__badge--muted">' +
        esc(T.label("equipment", ex.equipment)) + "</span>";
    }
    if (sex.status === "skipped") {
      badges +=
        '<span class="tr-ex-card__badge tr-ex-card__badge--skip">' +
        esc(pick("Пропущено", "Skipped")) + "</span>";
    }
    var target = T.fmtTarget(sex);
    var canAdd = rowCount(sex) < MAX_SETS && sex.status !== "skipped";
    return (
      '<article class="' + cls + '" data-sex="' + esc(sex.id) + '" id="trsEx' + esc(sex.id) + '">' +
      '<div class="tr-ex-card__head">' +
      '<button type="button" class="tr-ex-card__name" data-act="tech" data-ex="' + esc(ex.id) + '">' +
      esc(T.exName(ex)) +
      "</button>" +
      '<button type="button" class="tr-ex-card__menu" data-act="menu" data-sex="' + esc(sex.id) + '" ' +
      'aria-label="' + esc(pick("Меню упражнения", "Exercise menu")) + '">' +
      icon("dots", { size: 20 }) +
      "</button>" +
      "</div>" +
      (badges ? '<div class="tr-ex-card__badges">' + badges + "</div>" : "") +
      (target ? '<p class="tr-ex-card__target">' + esc(target) + "</p>" : "") +
      (sex.note
        ? '<p class="tr-ex-card__note">' + icon("bulb", { size: 16 }) + "<span>" + esc(sex.note) + "</span></p>"
        : "") +
      setTableHtml(sex) +
      (canAdd
        ? '<button type="button" class="tr-set-add" data-act="add" data-sex="' + esc(sex.id) + '">+ ' +
          esc(pick("подход", "set")) + "</button>"
        : "") +
      "</article>"
    );
  }

  /** Тело экрана выполнения: разминка → упражнения → заминка → «Завершить». */
  function bodyHtml() {
    var main = exercisesOf("main");
    var html = blockHtml("warmup");
    if (!main.length) {
      html +=
        '<section class="card wk-empty">' +
        '<p class="wk-empty__title">' + esc(pick("Упражнений нет", "No exercises")) + "</p>" +
        '<p class="wk-empty__text">' +
        esc(pick("В этой тренировке нет упражнений — завершите её и соберите программу заново.",
                 "This workout has no exercises — finish it and rebuild your program.")) +
        "</p></section>";
    }
    for (var i = 0; i < main.length; i++) html += exCardHtml(main[i]);
    // Добавить упражнение вручную: выбор из библиотеки (страница упражнений
    // в режиме «выбор для тренировки»), пока сессия не завершена.
    html +=
      '<button type="button" class="tr-add-ex" data-act="addex">' +
      icon("plus", { size: 18 }) +
      "<span>" + esc(pick("Добавить упражнение", "Add exercise")) + "</span>" +
      "</button>";
    html += blockHtml("cooldown");
    html +=
      '<div class="tr-session-actions">' +
      '<button type="button" class="btn btn-cta btn-block" data-act="finish" id="trsFinish"' +
      (anyWorkDone() ? "" : " disabled") + ">" +
      esc(pick("Завершить тренировку", "Finish workout")) +
      "</button>" +
      '<p class="tr-session-hint">' +
      esc(pick("Каждый отмеченный подход сохраняется автоматически.",
               "Every checked set is saved automatically.")) +
      "</p>" +
      "</div>";
    return html;
  }

  /** Липкая плашка отдыха (создаётся один раз, скрыта до старта таймера). */
  function restBarHtml() {
    return (
      '<div class="tr-rest-bar" id="trsRest" hidden role="status" aria-live="polite">' +
      '<div class="tr-rest-bar__inner">' +
      '<span class="tr-rest-bar__ring">' +
      '<svg viewBox="0 0 44 44" width="44" height="44" aria-hidden="true">' +
      '<circle class="tr-rest-bar__ring-bg" cx="22" cy="22" r="19"></circle>' +
      '<circle class="tr-rest-bar__ring-fg" id="trsRing" cx="22" cy="22" r="19" ' +
      'stroke-dasharray="' + RING_LEN + '" stroke-dashoffset="0"></circle>' +
      "</svg>" +
      "</span>" +
      '<span class="tr-rest-bar__body">' +
      '<span class="tr-rest-bar__time num" id="trsRestTime">0:00</span>' +
      '<span class="tr-rest-bar__info" id="trsRestInfo"></span>' +
      "</span>" +
      '<span class="tr-rest-bar__btns">' +
      '<button type="button" class="tr-rest-bar__btn" data-act="rest" data-d="-15">−15</button>' +
      '<button type="button" class="tr-rest-bar__btn" data-act="rest" data-d="15">+15</button>' +
      '<button type="button" class="tr-rest-bar__btn tr-rest-bar__btn--skip" data-act="rest" data-d="skip">' +
      esc(pick("Пропустить", "Skip")) +
      "</button>" +
      "</span>" +
      "</div>" +
      "</div>"
    );
  }

  function runHtml() {
    return (
      '<section class="page sub-page tr-page tr-session">' +
      headHtml() +
      '<div class="tr-session-body" id="trsBody">' + bodyHtml() + "</div>" +
      restBarHtml() +
      '<div class="tr-pr-toast" id="trsPr" hidden role="status" aria-live="polite"></div>' +
      "</section>"
    );
  }

  /* =====================================================================
   *  РАЗМЕТКА: ЭКРАН ИТОГА И ОТЗЫВА
   * ===================================================================== */

  /** Итоги для завершённой сессии, открытой без ответа /finish. */
  function summaryFromSession(s) {
    s = s || {};
    return {
      duration_min: s.duration_min || 0,
      total_sets: s.total_sets || 0,
      total_reps: s.total_reps || 0,
      total_volume_kg: s.total_volume_kg || 0,
      calories_burned: s.calories_burned || 0,
      exercises_done: 0,
      exercises_skipped: 0
    };
  }

  function statHtml(value, label, sub) {
    return (
      '<div class="tr-stat">' +
      '<span class="tr-stat__value">' + esc(value) + "</span>" +
      '<span class="tr-stat__label">' + esc(label) + "</span>" +
      (sub ? '<span class="tr-stat__sub">' + esc(sub) + "</span>" : "") +
      "</div>"
    );
  }

  /** Карточка рекорда на экране итога. */
  function recordCardHtml(pr) {
    var name = T.exName(pr) || pick("Упражнение", "Exercise");
    var prevText =
      pr.prev_value != null && Number(pr.prev_value) > 0
        ? pick("было ", "was ") + prValue(pr.type, pr.prev_value)
        : pick("первый результат", "first result");
    return (
      '<div class="tr-record-card">' +
      '<span class="tr-record-card__icon" aria-hidden="true">' + icon("trophy", { size: 22 }) + "</span>" +
      '<span class="tr-record-card__body">' +
      '<span class="tr-record-card__name">' + esc(name) + "</span>" +
      '<span class="tr-record-card__value">' +
      esc(T.label("recordType", pr.type) + " " + prValue(pr.type, pr.value)) +
      "</span>" +
      '<span class="tr-record-card__prev">' + esc(prevText) + "</span>" +
      "</span>" +
      "</div>"
    );
  }

  /** Карточка «Учёл на следующий раз:» со строками адаптации. */
  function adaptCardHtml(adaptation) {
    if (!adaptation) return "";
    var lines = adaptation.lines || [];
    var body = "";
    for (var i = 0; i < lines.length; i++) {
      body += '<p class="tr-adapt-line">' + esc(lines[i]) + "</p>";
    }
    if (!body) {
      body =
        '<p class="tr-adapt-line">' +
        esc(pick("Изменений нет — продолжаем по плану.", "No changes — we stick to the plan.")) +
        "</p>";
    }
    return (
      '<section class="card tr-adapt-card" id="trsAdapt">' +
      '<h3 class="tr-adapt-card__title">' +
      esc((adaptation.message || pick("Учёл на следующий раз", "Noted for next time")) + ":") +
      "</h3>" +
      body +
      "</section>"
    );
  }

  /** Блок отзыва: чипы, заметка, «Отправить». */
  function feedbackHtml() {
    // Чипы без картинок: «Слишком легко / В самый раз / Слишком тяжело» —
    // это оценка, а не предмет; лица-эмодзи здесь только мешали читать.
    var codes = ["easy", "ok", "hard"];
    var chips = "";
    for (var i = 0; i < codes.length; i++) {
      var code = codes[i];
      chips +=
        '<button type="button" class="tr-feedback-chip' +
        (state.feedback === code ? " is-active" : "") +
        '" data-act="fb" data-fb="' + code + '" aria-pressed="' +
        (state.feedback === code ? "true" : "false") + '">' +
        esc(T.label("feedback", code)) +
        "</button>";
    }
    var note = (state.session && state.session.feedback_note) || "";
    return (
      '<section class="card tr-feedback" id="trsFeedback">' +
      '<h3 class="tr-feedback__title">' + esc(pick("Как прошла тренировка?", "How did it go?")) + "</h3>" +
      '<div class="tr-feedback-chips">' + chips + "</div>" +
      '<div class="field">' +
      '<textarea class="field__input tr-feedback-note" id="trsNote" rows="3" maxlength="500" ' +
      'placeholder="' + esc(pick("Заметка (необязательно)", "Note (optional)")) + '">' +
      esc(note) +
      "</textarea>" +
      "</div>" +
      '<button type="button" class="btn btn-cta btn-block" data-act="send-fb" id="trsSendFb"' +
      (state.feedback ? "" : " disabled") + ">" +
      esc(pick("Отправить", "Send")) +
      "</button>" +
      "</section>"
    );
  }

  function finishHtml() {
    var s = state.session || {};
    var sum = (state.finish && state.finish.summary) || summaryFromSession(s);
    var prs = (state.finish && state.finish.prs) || s.prs || [];
    var stats =
      statHtml(
        T.fmtDuration(sum.duration_min || 0),
        pick("Длительность", "Duration")
      ) +
      statHtml(String(sum.total_sets || 0), pick("Подходы", "Sets")) +
      statHtml(
        App.fmt(sum.total_volume_kg || 0) + " " + pick("кг", "kg"),
        pick("Объём", "Volume")
      ) +
      statHtml(
        App.fmt(sum.calories_burned || 0) + " " + pick("ккал", "kcal"),
        pick("Сожжено", "Burned"),
        pick("добавлено в дневник", "added to the diary")
      );

    var records = "";
    for (var i = 0; i < prs.length; i++) records += recordCardHtml(prs[i]);

    var extra = [];
    if (sum.exercises_done) {
      extra.push(sum.exercises_done + " " + exWord(sum.exercises_done) + pick(" выполнено", " done"));
    }
    if (sum.exercises_skipped) {
      extra.push(sum.exercises_skipped + pick(" пропущено", " skipped"));
    }
    if (sum.total_reps) {
      extra.push(App.fmt(sum.total_reps) + pick(" повторов", " reps"));
    }

    return (
      '<section class="page sub-page tr-page tr-session tr-session--finish">' +
      '<header class="sub-head tr-head">' +
      '<h1 class="page-title sub-title">' + esc(pick("Готово!", "Done!")) + "</h1>" +
      '<p class="page-subtitle sub-subtitle">' +
      esc((s.title || "") + (s.date ? (s.title ? " · " : "") + T.humanDate(s.date) : "")) +
      "</p>" +
      "</header>" +
      '<section class="card tr-finish">' +
      '<div class="tr-stat-grid">' + stats + "</div>" +
      (extra.length ? '<p class="tr-finish__extra">' + esc(extra.join(" · ")) + "</p>" : "") +
      "</section>" +
      (records
        ? '<h3 class="tr-section-title">' + esc(pick("Личные рекорды", "Personal records")) + "</h3>" +
          '<div class="tr-record-list">' + records + "</div>"
        : "") +
      (state.adaptation ? adaptCardHtml(state.adaptation) : feedbackHtml()) +
      '<button type="button" class="btn btn-ghost btn-block" data-act="go-progress">' +
      esc(pick("К прогрессу", "Go to progress")) +
      "</button>" +
      '<button type="button" class="btn btn-ghost btn-block" data-act="go-home">' +
      esc(pick("На главную тренера", "Coach home")) +
      "</button>" +
      "</section>"
    );
  }

  /* =====================================================================
   *  РЕНДЕР И ТОЧЕЧНЫЕ ОБНОВЛЕНИЯ
   * ===================================================================== */

  /** Полная перерисовка страницы (смена экрана run/finish). */
  function render() {
    if (!state.viewEl) return;
    stopRest();
    stopInline();
    state.viewEl.innerHTML = state.screen === "finish" ? finishHtml() : runHtml();
    state.rootEl = state.viewEl.querySelector(".tr-session");
    if (state.rootEl) {
      state.rootEl.addEventListener("click", onClick);
      state.rootEl.addEventListener("focusin", onFocusIn);
      state.rootEl.addEventListener("keydown", onKeyDown);
    }
    if (state.screen === "run") {
      updateHead();
      startClock();
    } else {
      stopClock();
    }
    App.scrollTop();
  }

  /** Перерисовка только списка упражнений (шапка, отдых и тост живут дальше). */
  function renderBody() {
    var body = byId("trsBody");
    if (!body) return;
    collectDrafts();
    body.innerHTML = bodyHtml();
    updateHead();
  }

  /** Перерисовка одного блока чек-листа. */
  function renderBlock(block) {
    if (!state.rootEl) return;
    var el = state.rootEl.querySelector('.tr-block[data-block="' + block + '"]');
    if (!el) {
      renderBody();
      return;
    }
    var html = blockHtml(block);
    if (!html) {
      if (el.parentNode) el.parentNode.removeChild(el);
      return;
    }
    el.outerHTML = html;
  }

  /** Перерисовка одной строки подхода (после сохранения/снятия отметки). */
  function renderRow(sex, index) {
    if (!state.rootEl) return;
    var row = state.rootEl.querySelector(
      '.tr-set-row[data-sex="' + sex.id + '"][data-i="' + index + '"]'
    );
    if (!row) return;
    row.outerHTML = setRowHtml(sex, index, measureOf(sex));
  }

  /** Класс карточки по статусу упражнения. */
  function refreshCard(sex) {
    if (!state.rootEl) return;
    var card = state.rootEl.querySelector('.tr-ex-card[data-sex="' + sex.id + '"]');
    if (!card) return;
    card.classList.toggle("tr-ex-card--done", sex.status === "done");
    card.classList.toggle("tr-ex-card--skipped", sex.status === "skipped");
    // Закрытое упражнение передаёт «текущий» следующему — без полной
    // перерисовки списка, чтобы не терять фокус и черновики полей.
    var cards = state.rootEl.querySelectorAll(".tr-ex-card");
    for (var i = 0; i < cards.length; i++) {
      var other = findSex(cards[i].getAttribute("data-sex"));
      cards[i].classList.toggle("tr-ex-card--current", !!other && isCurrent(other));
    }
  }

  /** Прогресс «2/6 упражнений» и доступность кнопки «Завершить». */
  function updateHead() {
    var pr = mainProgress();
    var el = byId("trsProgress");
    if (el) {
      el.textContent = pr.total
        ? pr.done + "/" + pr.total + " " + exWord(pr.total)
        : "";
    }
    var btn = byId("trsFinish");
    if (btn) btn.disabled = !anyWorkDone();
  }

  /* =====================================================================
   *  СЕКУНДОМЕР СЕССИИ (от started_at, переживает сворачивание)
   * ===================================================================== */

  function tickClock() {
    var el = byId("trsTimer");
    if (!el || !state.session) return;
    var started = T.parseServerDate(state.session.started_at);
    var sec = started ? Math.floor((Date.now() - started.getTime()) / 1000) : 0;
    // Сессию можно бросить и вернуться к ней через неделю. Секундомер тогда
    // показал бы «168:04:11» — это не длительность тренировки, а мусор,
    // который к тому же ломает ширину шапки. За сутками отдаём разговор
    // Trainer.fmtDuration: он скажет «давно».
    el.textContent = sec >= 86400 ? T.fmtDuration(Math.floor(sec / 60)) : fmtElapsed(sec);
  }

  function startClock() {
    stopClock();
    tickClock();
    state.clock = setInterval(tickClock, 1000);
  }

  function stopClock() {
    if (state.clock) {
      clearInterval(state.clock);
      state.clock = null;
    }
  }

  /* =====================================================================
   *  ПЛАШКА ОТДЫХА
   * ===================================================================== */

  function restBar() {
    return byId("trsRest");
  }

  function showRestBar(show) {
    var bar = restBar();
    if (bar) bar.hidden = !show;
    if (state.rootEl) state.rootEl.classList.toggle("tr-session--rest", !!show);
  }

  function paintRest(remaining, total) {
    var timeEl = byId("trsRestTime");
    if (timeEl) timeEl.textContent = T.fmtClock(remaining);
    var ring = byId("trsRing");
    if (ring) {
      var part = total > 0 ? Math.max(0, Math.min(1, remaining / total)) : 0;
      ring.setAttribute("stroke-dashoffset", String(Math.round(RING_LEN * (1 - part) * 10) / 10));
    }
  }

  /**
   * Запускает отдых после отмеченного подхода.
   * @param {number} sec длительность (rest_sec из ответа сервера)
   * @param {number} nextIndex номер следующего подхода для подписи
   */
  function startRest(sec, nextIndex) {
    var total = Math.max(5, Math.round(Number(sec) || REST_FALLBACK));
    var info = byId("trsRestInfo");
    var nextText = nextIndex
      ? pick("Следующий: подход ", "Next: set ") + nextIndex
      : pick("Отдых · дальше следующее упражнение", "Rest · then next exercise");
    if (info) info.textContent = nextText;
    var bar = restBar();
    if (bar) bar.classList.remove("tr-rest-bar--done");

    if (!state.rest) {
      state.rest = new T.RestTimer({
        onTick: function (remaining, totalSec) {
          paintRest(remaining, totalSec);
        },
        onDone: function () {
          App.haptic("success");
          T.beep();
          var el = restBar();
          if (el) el.classList.add("tr-rest-bar--done");
          var t = byId("trsRestTime");
          if (t) t.textContent = pick("Готово", "Ready");
          paintRest(0, 1);
          setTimeout(function () {
            if (state.rest && state.rest.isRunning()) return;
            showRestBar(false);
          }, 4000);
        },
        onSkip: function () {
          showRestBar(false);
        }
      });
    }
    showRestBar(true);
    state.rest.start(total);
  }

  function stopRest() {
    if (state.rest) state.rest.stop();
    showRestBar(false);
  }

  /* =====================================================================
   *  ТАЙМЕР УПРАЖНЕНИЯ НА ВРЕМЯ (кнопка «плей» в строке)
   * ===================================================================== */

  function inlineButton() {
    if (!state.rootEl || !state.inlineKey) return null;
    var parts = state.inlineKey.split(":");
    var row = state.rootEl.querySelector(
      '.tr-set-row[data-sex="' + parts[0] + '"][data-i="' + parts[1] + '"]'
    );
    return row ? row.querySelector(".tr-set-timer") : null;
  }

  function stopInline() {
    if (state.inline) state.inline.stop();
    var btn = inlineButton();
    if (btn) {
      // innerHTML, а не textContent: в состоянии покоя кнопка — иконка,
      // в состоянии отсчёта — текст «м:сс».
      btn.innerHTML = playIcon();
      btn.classList.remove("is-running");
    }
    state.inlineKey = "";
  }

  /** Запускает/останавливает обратный отсчёт по секундам строки. */
  function toggleInline(sex, index, rowEl) {
    var key = draftKey(sex.id, index);
    if (state.inlineKey === key) {
      stopInline();
      return;
    }
    stopInline();
    var input = rowEl.querySelector('.tr-set-input[data-f="t"]');
    var sec = input ? parseInt(input.value, 10) : 0;
    if (!sec || sec < 1) sec = sex.planned_time_sec || 30;
    state.inlineKey = key;
    if (!state.inline) {
      state.inline = new T.RestTimer({
        onTick: function (remaining) {
          var btn = inlineButton();
          if (btn) {
            btn.textContent = T.fmtClock(remaining);
            btn.classList.add("is-running");
          }
        },
        onDone: function () {
          App.haptic("success");
          T.beep();
          var btn = inlineButton();
          if (btn) {
            btn.innerHTML = playIcon();
            btn.classList.remove("is-running");
          }
          state.inlineKey = "";
        }
      });
    }
    App.haptic("light");
    state.inline.start(sec);
  }

  /* =====================================================================
   *  ТОСТ РЕКОРДА
   * ===================================================================== */

  function prToast(prs) {
    var el = byId("trsPr");
    if (!el || !prs || !prs.length) return;
    var texts = [];
    for (var i = 0; i < prs.length; i++) texts.push(prText(prs[i]));
    el.innerHTML =
      '<span class="tr-pr-toast__icon" aria-hidden="true">' + icon("trophy", { size: 20 }) + "</span>" +
      '<span class="tr-pr-toast__text">' +
      esc(pick("Рекорд! ", "Record! ") + texts.join(" · ")) +
      "</span>";
    el.hidden = false;
    // Класс вешаем следующим кадром — иначе анимация появления не сыграет.
    requestAnimationFrame(function () {
      el.classList.add("is-show");
    });
    App.haptic("success");
    if (state.prTimer) clearTimeout(state.prTimer);
    state.prTimer = setTimeout(function () {
      el.classList.remove("is-show");
      setTimeout(function () {
        var box = byId("trsPr");
        if (box && !box.classList.contains("is-show")) box.hidden = true;
      }, 320);
    }, 3400);
  }

  /* =====================================================================
   *  СОХРАНЕНИЕ ПОДХОДА
   * ===================================================================== */

  function numOrNull(rowEl, field) {
    var input = rowEl.querySelector('.tr-set-input[data-f="' + field + '"]');
    if (!input) return null;
    var raw = String(input.value || "").replace(",", ".").trim();
    if (!raw) return null;
    var n = Number(raw);
    return isFinite(n) ? n : null;
  }

  /** Кладёт сохранённый подход в state (upsert по set_index). */
  function mergeSet(sex, saved) {
    if (!saved) return;
    sex.sets = sex.sets || [];
    for (var i = 0; i < sex.sets.length; i++) {
      if (Number(sex.sets[i].set_index) === Number(saved.set_index)) {
        sex.sets[i] = saved;
        return;
      }
    }
    sex.sets.push(saved);
  }

  /**
   * Отметить/снять подход. Отмечая, отправляем введённые значения; снимая —
   * те же значения с is_done=false (бэкенд сбрасывает признак рекорда у сета).
   */
  function toggleSet(sex, index, rowEl) {
    var sid = sessionId();
    if (!sid) return;
    var key = draftKey(sex.id, index);
    if (state.saving[key]) return;

    var saved = setAt(sex, index);
    var done = !!(saved && saved.is_done);
    var measure = measureOf(sex);
    var payload = {
      session_exercise_id: sex.id,
      set_index: index,
      set_type: (saved && saved.set_type) || "work",
      weight_kg: null,
      reps: null,
      time_sec: null,
      is_done: !done
    };

    if (measure === "time") {
      payload.time_sec = numOrNull(rowEl, "t");
      if (!done && (payload.time_sec === null || payload.time_sec <= 0)) {
        App.toast(pick("Укажите время подхода", "Enter the set duration"));
        return;
      }
    } else {
      payload.reps = numOrNull(rowEl, "r");
      if (measure === "reps_weight") payload.weight_kg = numOrNull(rowEl, "w");
      if (!done && (payload.reps === null || payload.reps <= 0)) {
        App.toast(pick("Укажите количество повторов", "Enter the number of reps"));
        return;
      }
    }

    state.saving[key] = true;
    var check = rowEl.querySelector(".tr-set-check");
    if (check) check.disabled = true;

    App.api
      .trainerSaveSet(sid, payload)
      .then(function (res) {
        res = res || {};
        delete state.draft[key];
        mergeSet(sex, res.set);
        if (res.exercise_status) sex.status = res.exercise_status;
        renderRow(sex, index);
        refreshCard(sex);
        updateHead();
        syncCache();
        if (payload.is_done) {
          App.haptic("success");
          if (res.prs && res.prs.length) prToast(res.prs);
          // После последнего запланированного подхода — «отдых, дальше следующее упражнение».
          startRest(res.rest_sec || REST_FALLBACK, index < rowCount(sex) ? index + 1 : 0);
        } else {
          App.haptic("light");
        }
      })
      .catch(function (err) {
        App.toast(T.errMessage(err, pick("Не удалось сохранить подход", "Failed to save the set")));
        if (err && err.status === 409) reload();
      })
      .finally(function () {
        delete state.saving[key];
        var el = state.rootEl
          ? state.rootEl.querySelector(
              '.tr-set-row[data-sex="' + sex.id + '"][data-i="' + index + '"] .tr-set-check'
            )
          : null;
        if (el) el.disabled = false;
      });
  }

  /** Отметка пункта чек-листа разминки/заминки (set_type=warmup, не в объём). */
  function toggleCheckItem(sex) {
    var sid = sessionId();
    if (!sid) return;
    var key = draftKey(sex.id, 1);
    if (state.saving[key]) return;
    var saved = setAt(sex, 1);
    var done = isChecked(sex);
    state.saving[key] = true;
    App.api
      .trainerSaveSet(sid, {
        session_exercise_id: sex.id,
        set_index: 1,
        set_type: "warmup",
        weight_kg: null,
        reps: saved && saved.reps != null
          ? saved.reps
          : (sex.planned_reps_max != null ? sex.planned_reps_max : sex.planned_reps_min),
        time_sec: sex.planned_time_sec != null ? sex.planned_time_sec : null,
        is_done: !done
      })
      .then(function (res) {
        res = res || {};
        mergeSet(sex, res.set);
        if (res.exercise_status) sex.status = res.exercise_status;
        App.haptic(done ? "light" : "success");
        renderBlock(sex.block || "warmup");
        syncCache();
      })
      .catch(function (err) {
        App.toast(T.errMessage(err, pick("Не удалось отметить пункт", "Failed to save the item")));
        if (err && err.status === 409) reload();
      })
      .finally(function () {
        delete state.saving[key];
      });
  }

  /** «+ подход» — новая строка (дублирует последнюю, сохранится по отметке). */
  function addRow(sex) {
    var n = rowCount(sex);
    if (n >= MAX_SETS) {
      App.toast(pick("Больше 12 подходов не поддерживается", "Up to 12 sets are supported"));
      return;
    }
    collectDrafts();
    // ТЗ §2.5: «+ подход» дублирует последнюю строку — переносим её значения
    // в черновик новой, чтобы не набирать вес и повторы заново.
    var lastRow = state.rootEl
      ? state.rootEl.querySelector('.tr-set-row[data-sex="' + sex.id + '"][data-i="' + n + '"]')
      : null;
    if (lastRow) {
      var copy = {};
      var inputs = lastRow.querySelectorAll(".tr-set-input");
      for (var i = 0; i < inputs.length; i++) {
        copy[inputs[i].getAttribute("data-f")] = inputs[i].value;
      }
      state.draft[draftKey(sex.id, n + 1)] = copy;
    }
    state.rowCount[sex.id] = n + 1;
    App.haptic("light");
    var card = state.rootEl ? state.rootEl.querySelector('.tr-ex-card[data-sex="' + sex.id + '"]') : null;
    if (card) card.outerHTML = exCardHtml(sex);
    else renderBody();
  }

  /** Крест у добавленной вручную строки: удаляем на сервере (если сохранена). */
  function deleteRow(sex, index) {
    var saved = setAt(sex, index);
    var sid = sessionId();
    collectDrafts();
    delete state.draft[draftKey(sex.id, index)];

    function dropLocal() {
      var sets = sex.sets || [];
      for (var i = 0; i < sets.length; i++) {
        if (Number(sets[i].set_index) === Number(index)) {
          sets.splice(i, 1);
          break;
        }
      }
      var n = rowCount(sex);
      state.rowCount[sex.id] = Math.max(1, n - 1);
      var card = state.rootEl ? state.rootEl.querySelector('.tr-ex-card[data-sex="' + sex.id + '"]') : null;
      if (card) card.outerHTML = exCardHtml(sex);
      else renderBody();
      updateHead();
      syncCache();
    }

    if (!saved || !sid) {
      App.haptic("light");
      dropLocal();
      return;
    }
    App.api
      .trainerDeleteSet(sid, saved.id)
      .then(function () {
        App.haptic("light");
        dropLocal();
      })
      .catch(function (err) {
        App.toast(T.errMessage(err, pick("Не удалось удалить подход", "Failed to delete the set")));
      });
  }

  /** Тап по «прошлому разу» копирует значения в поля строки. */
  function copyPrev(btn, rowEl) {
    var map = { w: btn.getAttribute("data-w"), r: btn.getAttribute("data-r"), t: btn.getAttribute("data-t") };
    var filled = false;
    var fields = ["w", "r", "t"];
    for (var i = 0; i < fields.length; i++) {
      var value = map[fields[i]];
      if (value === null || value === undefined || value === "") continue;
      var input = rowEl.querySelector('.tr-set-input[data-f="' + fields[i] + '"]');
      if (!input) continue;
      input.value = value;
      filled = true;
    }
    if (filled) {
      App.haptic("selection");
      var key = draftKey(rowEl.getAttribute("data-sex"), rowEl.getAttribute("data-i"));
      state.draft[key] = { w: map.w, r: map.r, t: map.t };
    }
  }

  /* =====================================================================
   *  МЕНЮ УПРАЖНЕНИЯ: ЗАМЕНА / ПРОПУСК / ТЕХНИКА / ИСТОРИЯ / ИСКЛЮЧИТЬ
   * ===================================================================== */

  function openMenu(sex) {
    var ex = sex.exercise || {};
    var items = [
      { key: "replace", icon: "swap", label: pick("Заменить", "Replace"),
        desc: pick("Занят тренажёр, нет оборудования или болит", "Busy machine, no equipment or pain") },
      { key: "skip", icon: "skip", label: pick("Пропустить", "Skip") },
      { key: "tech", icon: "book", label: pick("Техника", "Technique") },
      { key: "history", icon: "chartBar", label: pick("История", "History") },
      { key: "exclude", icon: "ban", label: pick("Исключить навсегда", "Never suggest again"), danger: true }
    ];
    T.sheet(items, function (key) {
      if (key === "replace") openReasons(sex);
      else if (key === "skip") skipExercise(sex);
      else if (key === "tech") openTechnique(ex.id);
      else if (key === "history") openHistory(ex.id);
      else if (key === "exclude") excludeExercise(sex);
    }, { title: T.exName(ex) });
  }

  /** Лист причин замены (busy / no_equipment / pain / other). */
  function openReasons(sex) {
    var codes = ["busy", "no_equipment", "pain", "other"];
    var icons = { busy: "lock", no_equipment: "dumbbell", pain: "bandage", other: "dots" };
    var items = [];
    for (var i = 0; i < codes.length; i++) {
      items.push({ key: codes[i], icon: icons[codes[i]], label: T.label("reason", codes[i]) });
    }
    T.sheet(items, function (reason) {
      openAlternatives(sex, reason);
    }, { title: pick("Почему меняем?", "Why replace it?") });
  }

  function altItemHtml(item) {
    var meta = [];
    if (item.muscle_group) meta.push(T.label("muscle", item.muscle_group));
    if (item.equipment) meta.push(T.label("equipment", item.equipment));
    return (
      '<button type="button" class="tr-alt-item" data-alt="' + esc(item.id) + '">' +
      '<span class="tr-alt-item__name">' + esc(T.exName(item)) + "</span>" +
      (meta.length ? '<span class="tr-alt-item__meta">' + esc(meta.join(" · ")) + "</span>" : "") +
      "</button>"
    );
  }

  /** Лист альтернатив (3–5 упражнений на ту же мышцу). */
  function openAlternatives(sex, reason) {
    var ex = sex.exercise || {};
    var box = T.sheet(
      '<div class="tr-alt-list" id="trsAlt">' + T.skeleton(3) + "</div>",
      null,
      { title: pick("Чем заменить?", "Replace with") }
    );
    App.api
      .trainerAlternatives(ex.id, reason, sessionId())
      .then(function (res) {
        var host = byId("trsAlt");
        if (!host) return;
        var items = (res && res.items) || [];
        if (!items.length) {
          host.innerHTML =
            '<p class="tr-sheet__empty">' +
            esc(pick("Подходящих альтернатив не нашлось", "No suitable alternatives found")) +
            "</p>";
          return;
        }
        var html = "";
        for (var i = 0; i < items.length; i++) html += altItemHtml(items[i]);
        host.innerHTML = html;
        host.addEventListener("click", function (ev) {
          var btn = ev.target.closest ? ev.target.closest(".tr-alt-item") : null;
          if (!btn) return;
          replaceExercise(sex, parseInt(btn.getAttribute("data-alt"), 10), reason);
        });
      })
      .catch(function (err) {
        var host = byId("trsAlt");
        if (!host) return;
        host.innerHTML =
          '<p class="tr-sheet__empty">' +
          esc(T.errMessage(err, pick("Не удалось получить альтернативы", "Failed to load alternatives"))) +
          "</p>";
      });
    return box;
  }

  function replaceExercise(sex, newId, reason) {
    var sid = sessionId();
    if (!sid || !newId || state.busy) return;
    state.busy = true;
    App.api
      .trainerReplaceExercise(sid, sex.id, { new_exercise_id: newId, reason: reason, remember: true })
      .then(function (row) {
        T.closeSheet();
        sex.status = "replaced";
        var list = (state.session && state.session.exercises) || [];
        var at = list.indexOf(sex);
        if (at >= 0) list.splice(at + 1, 0, row);
        else list.push(row);
        collectDrafts();
        renderBody();
        App.haptic("success");
        App.toast(pick("Упражнение заменено", "Exercise replaced"));
        syncCache();
      })
      .catch(function (err) {
        App.toast(T.errMessage(err, pick("Не удалось заменить упражнение", "Failed to replace the exercise")));
      })
      .finally(function () {
        state.busy = false;
      });
  }

  function skipExercise(sex) {
    var sid = sessionId();
    if (!sid || state.busy) return;
    state.busy = true;
    App.api
      .trainerSkipExercise(sid, sex.id)
      .then(function () {
        sex.status = "skipped";
        collectDrafts();
        renderBody();
        App.haptic("warning");
        App.toast(pick("Упражнение пропущено", "Exercise skipped"));
        syncCache();
      })
      .catch(function (err) {
        App.toast(T.errMessage(err, pick("Не удалось пропустить упражнение", "Failed to skip the exercise")));
      })
      .finally(function () {
        state.busy = false;
      });
  }

  function excludeExercise(sex) {
    var ex = sex.exercise || {};
    T.confirm(
      pick(
        "Больше не предлагать это упражнение в программах?",
        "Never suggest this exercise in your programs again?"
      )
    ).then(function (ok) {
      if (!ok) return;
      App.api
        .trainerExcludeExercise(ex.id, true)
        .then(function () {
          App.haptic("success");
          App.toast(pick("Больше не предложим", "We won’t suggest it again"));
        })
        .catch(function (err) {
          App.toast(T.errMessage(err, pick("Не удалось исключить упражнение", "Failed to exclude the exercise")));
        });
    });
  }

  /** Лист «Техника»: генерируется ИИ при первом открытии и кэшируется. */
  function openTechnique(exId) {
    if (!exId) return;
    T.sheet(
      '<div class="tr-technique" id="trsTech">' +
      '<p class="tr-technique__wait">' + esc(pick("Тренер пишет технику…", "The coach is writing the technique…")) + "</p>" +
      T.skeleton(4) +
      "</div>",
      null,
      { title: pick("Техника", "Technique") }
    );
    App.api
      .trainerExercise(exId, true)
      .then(function (data) {
        var host = byId("trsTech");
        if (!host) return;
        host.innerHTML = techniqueHtml(data);
      })
      .catch(function (err) {
        var host = byId("trsTech");
        if (!host) return;
        host.innerHTML =
          '<p class="tr-sheet__empty">' +
          esc(T.errMessage(err, pick("Техника пока недоступна", "Technique is unavailable right now"))) +
          "</p>";
      });
  }

  function techList(title, items) {
    if (!items || !items.length) return "";
    var body = "";
    for (var i = 0; i < items.length; i++) {
      body += "<li>" + esc(items[i]) + "</li>";
    }
    return (
      '<div class="tr-technique__block">' +
      '<div class="tr-technique__title">' + esc(title) + "</div>" +
      '<ul class="tr-technique__list">' + body + "</ul>" +
      "</div>"
    );
  }

  function techText(title, text) {
    if (!text) return "";
    return (
      '<div class="tr-technique__block">' +
      '<div class="tr-technique__title">' + esc(title) + "</div>" +
      "<p>" + esc(text) + "</p>" +
      "</div>"
    );
  }

  function techniqueHtml(data) {
    data = data || {};
    var t = data.technique;
    if (!t) {
      return (
        '<p class="tr-sheet__empty">' +
        esc(pick("Техника пока не готова — попробуйте позже.", "The technique is not ready yet — try again later.")) +
        "</p>"
      );
    }
    return (
      techText(pick("Работают мышцы", "Muscles worked"), t.muscles_text) +
      techList(pick("Как делать", "How to do it"), t.steps) +
      techList(pick("Ключевые моменты", "Key cues"), t.cues) +
      techList(pick("Частые ошибки", "Common mistakes"), t.mistakes) +
      techText(pick("Дыхание", "Breathing"), t.breathing) +
      techText(pick("Безопасность", "Safety"), t.safety) +
      (data.disclaimer ? '<p class="rec-disclaimer">' + esc(data.disclaimer) + "</p>" : "")
    );
  }

  /** Лист «История»: рекорды и последние подходы по упражнению. */
  function openHistory(exId) {
    if (!exId) return;
    T.sheet(
      '<div class="tr-history" id="trsHist">' + T.skeleton(3) + "</div>",
      null,
      { title: pick("История", "History") }
    );
    App.api
      .trainerExerciseHistory(exId)
      .then(function (data) {
        var host = byId("trsHist");
        if (!host) return;
        host.innerHTML = historyHtml(data);
      })
      .catch(function (err) {
        var host = byId("trsHist");
        if (!host) return;
        host.innerHTML =
          '<p class="tr-sheet__empty">' +
          esc(T.errMessage(err, pick("История пока недоступна", "History is unavailable right now"))) +
          "</p>";
      });
  }

  function historyHtml(data) {
    data = data || {};
    var records = data.records || [];
    var sessions = data.sessions || [];
    var html = "";
    if (records.length) {
      var rows = "";
      for (var i = 0; i < records.length; i++) {
        var r = records[i];
        rows +=
          '<div class="tr-history-rec">' +
          "<span>" + esc(T.label("recordType", r.record_type)) + "</span>" +
          "<b>" + esc(prValue(r.record_type, r.value)) + "</b>" +
          "<span>" + esc(r.date ? T.shortDate(r.date, false) : "") + "</span>" +
          "</div>";
      }
      html +=
        '<div class="tr-history__block">' +
        '<div class="tr-history__title">' + esc(pick("Рекорды", "Records")) + "</div>" +
        rows +
        "</div>";
    }
    for (var j = 0; j < Math.min(5, sessions.length); j++) {
      var s = sessions[j] || {};
      var sets = s.sets || [];
      var parts = [];
      for (var k = 0; k < sets.length; k++) {
        parts.push(T.fmtSet(sets[k].weight_kg, sets[k].reps, sets[k].time_sec));
      }
      html +=
        '<div class="tr-history-item">' +
        '<span class="tr-history-item__date">' + esc(s.date ? T.shortDate(s.date) : "") + "</span>" +
        '<span class="tr-history-item__sets">' + esc(parts.join(" · ")) + "</span>" +
        "</div>";
    }
    if (!html) {
      html =
        '<p class="tr-sheet__empty">' +
        esc(pick("Здесь появятся ваши подходы и рекорды", "Your sets and records will appear here")) +
        "</p>";
    }
    return html;
  }

  /* =====================================================================
   *  ЗАВЕРШЕНИЕ, ОТМЕНА, ОТЗЫВ
   * ===================================================================== */

  function closingConfirmation(on) {
    if (!App.tg) return;
    try {
      if (on && typeof App.tg.enableClosingConfirmation === "function") {
        App.tg.enableClosingConfirmation();
      } else if (!on && typeof App.tg.disableClosingConfirmation === "function") {
        App.tg.disableClosingConfirmation();
      }
    } catch (e) {
      /* метод не поддерживается версией клиента — не критично */
    }
  }

  /** Сохраняем состояние для «Продолжить» на «Сегодня». */
  function syncCache() {
    if (!state.session) return;
    T.cache.activeSession = state.session.status === "in_progress" ? state.session : null;
    T.cache.overview = null;
    App.state.trainerSessionId = state.session.status === "in_progress" ? state.session.id : null;
  }

  /** Крест в шапке: свернуть (прогресс сохранён) или отменить тренировку. */
  function onClose() {
    T.sheet(
      [
        {
          key: "minimize",
          icon: "arrow",
          label: pick("Свернуть тренировку", "Leave for now"),
          desc: pick("Прогресс сохранён — вернётесь по кнопке «Продолжить»",
                     "Progress is saved — come back with “Continue”")
        },
        {
          key: "abandon",
          icon: "trash",
          label: pick("Отменить тренировку", "Cancel workout"),
          desc: pick("Прогресс не сохранится", "Progress will not be saved"),
          danger: true
        }
      ],
      function (key) {
        if (key === "minimize") {
          syncCache();
          // На «Сегодня» — там карточка «Продолжить», по которой возвращаются.
          T.openSegment("today");
          return;
        }
        if (key === "abandon") abandon();
      },
      { title: pick("Выйти из тренировки?", "Leave the workout?") }
    );
  }

  function abandon() {
    var sid = sessionId();
    T.confirm(
      pick("Отменить тренировку? Прогресс не сохранится", "Cancel the workout? Progress will not be saved")
    ).then(function (ok) {
      if (!ok) return;
      if (!sid) {
        T.openSegment("today");
        return;
      }
      state.busy = true;
      App.api
        .trainerAbandonSession(sid)
        .then(function () {
          App.haptic("warning");
        })
        .catch(function (err) {
          App.toast(T.errMessage(err, pick("Не удалось отменить тренировку", "Failed to cancel the workout")));
        })
        .finally(function () {
          state.busy = false;
          T.cache.invalidate();
          App.state.trainerSessionId = null;
          closingConfirmation(false);
          T.openSegment("today");
        });
    });
  }

  /** «Завершить тренировку» с подтверждением при невыполненных упражнениях. */
  function finishSession() {
    var sid = sessionId();
    if (!sid || state.busy) return;
    var pr = mainProgress();
    var left = pr.total - pr.done;
    if (left > 0) {
      T.confirm(
        pick(
          "Осталось " + left + " " + exWord(left) + ". Завершить?",
          left + " " + exWord(left) + " left. Finish anyway?"
        )
      ).then(function (ok) {
        if (ok) doFinish(sid);
      });
      return;
    }
    doFinish(sid);
  }

  function doFinish(sid) {
    if (state.busy) return;
    state.busy = true;
    var btn = byId("trsFinish");
    if (btn) btn.disabled = true;
    stopRest();
    stopInline();
    // duration_min не передаём: сервер считает фактическое время сам
    // (elapsed от started_at) и клампит по длительности дня плана.
    App.api
      .trainerFinishSession(sid, {})
      .then(function (res) {
        res = res || {};
        state.finish = res;
        state.session = res.session || state.session;
        state.feedback = (state.session && state.session.feedback) || null;
        state.adaptation = state.feedback ? state.session.adaptation || null : null;
        state.screen = "finish";
        // Дневник получил строку Workout — кэш дня устарел.
        if (App.state && App.state.diaryByDate && state.session && state.session.date) {
          delete App.state.diaryByDate[state.session.date];
        }
        T.cache.invalidate();
        App.state.trainerSessionId = null;
        closingConfirmation(false);
        App.haptic("success");
        render();
      })
      .catch(function (err) {
        App.toast(T.errMessage(err, pick("Не удалось завершить тренировку", "Failed to finish the workout")));
        var b = byId("trsFinish");
        if (b) b.disabled = !anyWorkDone();
        if (err && err.status === 409) reload();
      })
      .finally(function () {
        state.busy = false;
      });
  }

  function selectFeedback(code) {
    state.feedback = code;
    if (!state.rootEl) return;
    var chips = state.rootEl.querySelectorAll(".tr-feedback-chip");
    for (var i = 0; i < chips.length; i++) {
      var active = chips[i].getAttribute("data-fb") === code;
      chips[i].classList.toggle("is-active", active);
      chips[i].setAttribute("aria-pressed", active ? "true" : "false");
    }
    var btn = byId("trsSendFb");
    if (btn) btn.disabled = false;
    App.haptic("selection");
  }

  function sendFeedback() {
    var sid = sessionId();
    if (!sid || !state.feedback || state.busy) return;
    var noteEl = byId("trsNote");
    var note = noteEl ? String(noteEl.value || "").trim() : "";
    state.busy = true;
    var btn = byId("trsSendFb");
    if (btn) btn.disabled = true;
    App.api
      .trainerFeedback(sid, { feedback: state.feedback, note: note || null })
      .then(function (adaptation) {
        state.adaptation = adaptation || { changes: [], lines: [], message: "" };
        if (state.session) {
          state.session.feedback = state.feedback;
          state.session.feedback_note = note || null;
          state.session.adaptation = state.adaptation;
        }
        App.haptic("success");
        var box = byId("trsFeedback");
        if (box) box.outerHTML = adaptCardHtml(state.adaptation);
        T.cache.invalidate();
      })
      .catch(function (err) {
        App.toast(T.errMessage(err, pick("Не удалось отправить отзыв", "Failed to send the feedback")));
        var b = byId("trsSendFb");
        if (b) b.disabled = false;
      })
      .finally(function () {
        state.busy = false;
      });
  }

  /* =====================================================================
   *  СОБЫТИЯ
   * ===================================================================== */

  function onClick(ev) {
    var target = ev.target;
    if (!target || !target.closest) return;
    var el = target.closest("[data-act]");
    if (!el || !state.rootEl.contains(el)) return;
    var act = el.getAttribute("data-act");
    var row = el.closest(".tr-set-row");
    var sex = null;

    if (act === "close") {
      App.haptic("light");
      onClose();
      return;
    }
    if (act === "fold") {
      var block = el.getAttribute("data-block");
      var box = el.closest(".tr-block");
      var folded = box ? box.classList.toggle("tr-block--folded") : false;
      state.folded[block] = folded;
      el.setAttribute("aria-expanded", folded ? "false" : "true");
      App.haptic("light");
      return;
    }
    if (act === "check-item") {
      sex = findSex(el.getAttribute("data-sex"));
      if (sex) toggleCheckItem(sex);
      return;
    }
    if (act === "prev" && row) {
      copyPrev(el, row);
      return;
    }
    if (act === "check" && row) {
      sex = findSex(row.getAttribute("data-sex"));
      if (sex) toggleSet(sex, parseInt(row.getAttribute("data-i"), 10), row);
      return;
    }
    if (act === "itimer" && row) {
      sex = findSex(row.getAttribute("data-sex"));
      if (sex) toggleInline(sex, parseInt(row.getAttribute("data-i"), 10), row);
      return;
    }
    if (act === "del" && row) {
      sex = findSex(row.getAttribute("data-sex"));
      if (sex) deleteRow(sex, parseInt(row.getAttribute("data-i"), 10));
      return;
    }
    if (act === "add") {
      sex = findSex(el.getAttribute("data-sex"));
      if (sex) addRow(sex);
      return;
    }
    if (act === "menu") {
      sex = findSex(el.getAttribute("data-sex"));
      App.haptic("light");
      if (sex) openMenu(sex);
      return;
    }
    if (act === "tech") {
      App.haptic("light");
      openTechnique(parseInt(el.getAttribute("data-ex"), 10));
      return;
    }
    if (act === "rest") {
      onRestButton(el.getAttribute("data-d"));
      return;
    }
    if (act === "addex") {
      App.haptic("light");
      // Библиотека откроется в режиме выбора и после добавления вернёт сюда.
      App.state.trainerPickForSession = sessionId();
      T.go("trainer-exercise");
      return;
    }
    if (act === "finish") {
      App.haptic("medium");
      finishSession();
      return;
    }
    if (act === "fb") {
      selectFeedback(el.getAttribute("data-fb"));
      return;
    }
    if (act === "send-fb") {
      App.haptic("medium");
      sendFeedback();
      return;
    }
    if (act === "go-progress") {
      App.haptic("light");
      App.state.trainerProgressSection = null;
      // Прогресс — вкладка раздела, а не отдельная страница.
      T.openSegment("progress");
      return;
    }
    if (act === "go-home") {
      App.haptic("light");
      T.openSegment("today");
    }
  }

  function onRestButton(kind) {
    if (!state.rest) return;
    if (kind === "skip") {
      App.haptic("light");
      state.rest.skip();
      return;
    }
    var delta = parseInt(kind, 10);
    if (!isFinite(delta)) return;
    App.haptic("selection");
    state.rest.adjust(delta);
    if (state.rest.remaining() <= 0) state.rest.skip();
  }

  /**
   * Поле в фокусе не должно оказаться под липкой плашкой отдыха —
   * подтягиваем строку к центру экрана после появления клавиатуры.
   */
  function onFocusIn(ev) {
    var input = ev.target;
    if (!input || !input.classList || !input.classList.contains("tr-set-input")) return;
    setTimeout(function () {
      if (!input.isConnected) return;
      try {
        input.scrollIntoView({ block: "center", behavior: "smooth" });
      } catch (e) {
        /* старый вебвью без опций scrollIntoView */
      }
    }, 280);
  }

  /** Enter в поле — как нажатие отметки (удобно с числовой клавиатурой). */
  function onKeyDown(ev) {
    if (ev.key !== "Enter") return;
    var input = ev.target;
    if (!input || !input.classList || !input.classList.contains("tr-set-input")) return;
    var row = input.closest(".tr-set-row");
    if (!row) return;
    ev.preventDefault();
    if (input.blur) input.blur();
    var sex = findSex(row.getAttribute("data-sex"));
    if (sex) toggleSet(sex, parseInt(row.getAttribute("data-i"), 10), row);
  }

  /* =====================================================================
   *  ЗАГРУЗКА СЕССИИ
   * ===================================================================== */

  /** Пустое состояние: активной тренировки нет. */
  function renderEmpty(message) {
    if (!state.viewEl) return;
    state.viewEl.innerHTML =
      '<section class="page sub-page tr-page tr-session">' +
      T.headHtml({ icon: "dumbbell", title: pick("Тренировка", "Workout"), subtitle: "" }) +
      '<section class="card wk-empty">' +
      '<div class="wk-empty__icon" aria-hidden="true">' + icon("dumbbell", { size: 36 }) + "</div>" +
      '<p class="wk-empty__title">' + esc(pick("Активной тренировки нет", "No active workout")) + "</p>" +
      '<p class="wk-empty__text">' +
      esc(message || pick("Начните тренировку на главной тренера.", "Start a workout from the coach home.")) +
      "</p>" +
      '<button type="button" class="btn btn-cta btn-block" id="trsHome">' +
      esc(pick("На главную тренера", "Coach home")) +
      "</button>" +
      "</section></section>";
    state.rootEl = state.viewEl.querySelector(".tr-session");
    T.bindBack(state.viewEl, function () {
      App.navigate("trainer");
    });
    var btn = byId("trsHome");
    if (btn) {
      btn.addEventListener("click", function () {
        App.haptic("light");
        App.navigate("trainer");
      });
    }
  }

  function renderError(err) {
    if (!state.viewEl) return;
    state.viewEl.innerHTML =
      '<section class="page sub-page tr-page tr-session">' +
      T.headHtml({ icon: "dumbbell", title: pick("Тренировка", "Workout"), subtitle: "" }) +
      T.errorCard(T.errMessage(err), "trsRetry") +
      "</section>";
    state.rootEl = state.viewEl.querySelector(".tr-session");
    T.bindBack(state.viewEl, function () {
      App.navigate("trainer");
    });
    var btn = byId("trsRetry");
    if (btn) {
      btn.addEventListener("click", function () {
        App.haptic("light");
        load();
      });
    }
  }

  /** Принять сессию с сервера и нарисовать нужный экран. */
  function applySession(session) {
    if (!session) {
      renderEmpty();
      return;
    }
    // Отменённая сессия — не «Готово!»: показываем пустое состояние.
    if (session.status === "abandoned") {
      state.session = null;
      App.state.trainerSessionId = null;
      T.cache.invalidate();
      closingConfirmation(false);
      renderEmpty(pick("Эта тренировка была отменена.", "That workout was cancelled."));
      return;
    }
    state.session = session;
    state.rowCount = {};
    state.draft = {};
    state.folded = {};
    state.feedback = session.feedback || null;
    // Карточку «Учёл на следующий раз» показываем только после отправленного
    // отзыва: adaptation_json может содержать служебный снимок состояния.
    state.adaptation = session.feedback ? session.adaptation || null : null;
    state.screen = session.status === "in_progress" ? "run" : "finish";
    App.state.trainerSessionId = session.status === "in_progress" ? session.id : null;
    if (session.status === "in_progress") {
      T.cache.activeSession = session;
      closingConfirmation(true);
    } else {
      T.cache.activeSession = null;
      closingConfirmation(false);
    }
    render();
  }

  function load() {
    if (!state.viewEl || state.loading) return;
    var sid = App.state.trainerSessionId;
    var cached = T.cache.activeSession;
    // Кэш заполняют «Сегодня» и Trainer.startSession — данные свежие.
    if (cached && (!sid || String(cached.id) === String(sid)) && cached.status === "in_progress") {
      applySession(cached);
      return;
    }

    state.loading = true;
    var reqId = ++state.reqId;
    state.viewEl.innerHTML =
      '<section class="page sub-page tr-page tr-session">' + T.skeleton(3) + T.skeleton(4) + "</section>";
    state.rootEl = state.viewEl.querySelector(".tr-session");

    var promise = sid ? App.api.trainerSession(sid) : App.api.trainerActiveSession();
    promise
      .then(function (session) {
        state.loading = false;
        if (reqId !== state.reqId || !state.viewEl) return;
        applySession(session);
      })
      .catch(function (err) {
        state.loading = false;
        if (reqId !== state.reqId || !state.viewEl) return;
        if (err && err.status === 402) {
          T.paywall(state.viewEl, T.paywallOpts());
          return;
        }
        if (err && err.status === 404) {
          App.state.trainerSessionId = null;
          T.cache.invalidate();
          renderEmpty(pick("Тренировка не найдена — возможно, она уже завершена.",
                           "The workout was not found — it may already be finished."));
          return;
        }
        renderError(err);
      });
  }

  /** Перечитать сессию с сервера (после конфликта состояния 409). */
  function reload() {
    T.cache.activeSession = null;
    load();
  }

  /* =====================================================================
   *  КОНТРОЛЛЕР
   * ===================================================================== */

  var controller = {
    onShow: function (viewEl) {
      state.viewEl = viewEl;
      if (!T.isPro()) {
        T.paywall(viewEl, T.paywallOpts());
        return;
      }
      if (!App.state.trainerOrigin) App.state.trainerOrigin = "today";
      state.finish = null;
      state.busy = false;
      state.saving = {};
      load();
    },

    onHide: function () {
      stopClock();
      stopRest();
      stopInline();
      if (state.prTimer) {
        clearTimeout(state.prTimer);
        state.prTimer = null;
      }
      T.closeSheet(true);
      closingConfirmation(false);
      state.reqId++;
      state.loading = false;
      state.viewEl = null;
      state.rootEl = null;
    }
  };

  window.PageTrainerSession = controller;
  App.registerPage("trainer-session", controller);
})();
