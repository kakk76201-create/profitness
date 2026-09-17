/*
 * page-trainer-exercise.js — «Упражнения» раздела «AI-тренер»
 * (ТЗ §2.8, §4.4, §6.2 блок «Прогресс/упражнения»).
 *
 * Три места показа одного модуля:
 *   • вкладка «Упражнения» раздела — window.TrainerLibrary.mount(el, opts) /
 *     unmount(): библиотека (фильтры-чипы по мышце и оборудованию, поиск по
 *     имени, список `.tr-lib-item`, GET /trainer/exercises) внутри панели
 *     оболочки (page-trainer.js), без своей шапки и кнопки «Назад»;
 *   • страница "trainer-exercise" с App.state.trainerExerciseId — карточка
 *     упражнения: вкладка «Техника» (генерируется ИИ один раз на упражнение
 *     и язык, дальше приходит из кэша; на время генерации — скелетон
 *     «Тренер пишет технику…»), вкладка «История» (рекорды, график
 *     1RM/вес/объём, последние подходы) и «Исключить из программ» / «Вернуть».
 *     Это переход вглубь, поэтому у карточки своя шапка с «Назад»: он ведёт
 *     туда, откуда карточку открыли (Trainer.openExercise запоминает место);
 *   • страница "trainer-exercise" с App.state.trainerPickForSession — тот же
 *     список в режиме «Добавить упражнение» в идущую тренировку.
 *
 * Вход на страницу без упражнения и без режима выбора переадресуется на
 * вкладку раздела (App.state.trainerSegment = "exercises").
 *
 * Список, фильтры и поиск живут в состоянии модуля: после карточки вкладка
 * рисует тот же отфильтрованный список, а не грузит каталог с нуля.
 *
 * Экономия ИИ: карточка сначала грузится БЕЗ technique (быстро, из БД); если
 * техники в кэше нет — вторым запросом с `technique=1` (это единственное
 * место, где расходуется лимит enforce_ai).
 *
 * Графики — SVG-строкой через Trainer.lineChart, без внешних библиотек.
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

  function icon(name, opts) {
    return T.icon(name, opts);
  }

  // Группы мышц каталога (ТЗ §3, колонка muscle_group).
  var MUSCLES = [
    "chest", "back", "shoulders", "biceps", "triceps",
    "quads", "hamstrings", "glutes", "calves", "core",
    "full_body", "cardio", "mobility"
  ];
  // Оборудование каталога (ТЗ §3, колонка equipment).
  var EQUIPMENT = [
    "barbell", "dumbbell", "machine", "cable", "bodyweight",
    "band", "kettlebell", "pullup_bar", "bench", "cardio_machine", "none"
  ];
  // Метрики графика истории (ключи точек TrainerChartPointOut).
  var METRICS = ["est_1rm", "max_weight", "volume"];

  // Пауза перед запросом после ввода в поиск, мс.
  var SEARCH_DELAY = 350;

  // Внутреннее состояние. Вкладка и страница не показываются одновременно
  // (вкладка живёт на экране "trainer"), поэтому состояние общее.
  var state = {
    viewEl: null,          // #view в режиме страницы (карточка, выбор в тренировку)
    host: null,            // панель оболочки в режиме вкладки
    embedded: false,       // сейчас смонтирована вкладка, а не страница
    opts: null,            // опции mount: {onPaywall}
    mode: "list",          // list|detail
    // --- список ---
    muscle: null,          // выбранная группа мышц или null
    equipment: null,       // выбранное оборудование или null
    query: "",             // строка поиска
    items: null,           // последний список упражнений
    total: 0,              // всего найдено (может быть больше limit)
    listLoading: false,
    searchTimer: null,
    // --- карточка ---
    exerciseId: null,      // id открытого упражнения
    exercise: null,        // TrainerExerciseOut
    ret: null,             // куда вернуться из карточки (Trainer.openExercise)
    tab: "technique",      // technique|history
    techniqueBusy: false,  // идёт генерация техники
    techniqueTried: false, // уже пробовали сгенерировать в этом заходе
    history: null,         // TrainerExerciseHistoryOut
    historyBusy: false,
    historyMetric: "est_1rm",
    excludeBusy: false,
    filtersOpen: false     // раскрыт ли блок фильтров (см. filtersHtml)
  };

  // Счётчики запросов по видам: ответ применяем, только если он последний
  // в своей очереди (иначе медленный запрос затирает свежие данные).
  var tokens = { list: 0, detail: 0, tech: 0, hist: 0 };

  /** Выдаёт новый номер запроса для очереди kind. */
  function nextToken(kind) {
    tokens[kind] += 1;
    return tokens[kind];
  }

  /** true, если ответ устарел (пришёл после более нового запроса). */
  function stale(kind, token) {
    return tokens[kind] !== token;
  }

  /** Сбрасывает все очереди (уход со страницы, смена режима). */
  function dropRequests() {
    tokens.list += 1;
    tokens.detail += 1;
    tokens.tech += 1;
    tokens.hist += 1;
  }

  /* =====================================================================
   *  ФОРМАТИРОВАНИЕ
   * ===================================================================== */

  /** Подпись метрики графика. */
  function metricLabel(key) {
    if (key === "max_weight") return pick("Вес", "Weight");
    if (key === "volume") return pick("Объём", "Volume");
    return "1RM";
  }

  /**
   * Сложность тремя точками: залитая — набранная ступень, контурная — пустая.
   * Раньше рисовалось текстовыми кружками: шрифты рисуют их разного размера,
   * и ряд получался неровным. Форма у всех трёх одна и та же (icon "dot"),
   * различает их ТОЛЬКО заливка (класс --on, fill в CSS): если брать разные
   * иконки, ряд читается как «маленький, большой, большой», а не как шкала.
   */
  function difficultyMark(level) {
    var n = Math.min(3, Math.max(1, parseInt(level, 10) || 1));
    var out = "";
    for (var i = 1; i <= 3; i++) {
      out += icon("dot", {
        size: 11,
        cls: "tr-diff-dot" + (i <= n ? " tr-diff-dot--on" : "")
      });
    }
    return out;
  }

  /** Строка-описание упражнения под названием: мышца · оборудование · тип. */
  function itemMeta(ex) {
    var parts = [];
    if (ex.muscle_group) parts.push(T.label("muscle", ex.muscle_group));
    if (ex.equipment) parts.push(T.label("equipment", ex.equipment));
    if (ex.category) parts.push(T.label("category", ex.category));
    return parts.join(" · ");
  }

  /** Значение рекорда в человеческом виде по его типу. */
  function recordValue(rec) {
    if (rec.record_type === "max_reps") return T.fmtNum(rec.value) + " " + pick("повт.", "reps");
    if (rec.record_type === "max_time") return T.fmtClock(rec.value);
    return T.fmtKg(rec.value);
  }

  /* =====================================================================
   *  РАЗМЕТКА: КАРКАС
   * ===================================================================== */

  /** Каркас страницы под текущий режим. */
  function shellHtml(title, subtitle, iconName) {
    return (
      '<section class="page sub-page tr-page tr-library">' +
      T.headHtml({ icon: iconName || "book", title: title, subtitle: subtitle || "" }) +
      '<div id="trLibBody"></div>' +
      "</section>"
    );
  }

  /** Корень текущего показа: панель вкладки или #view страницы. */
  function rootEl() {
    return state.embedded ? state.host : state.viewEl;
  }

  /** Paywall: вкладка отдаёт его оболочке (на весь экран), страница рисует сама. */
  function showPaywall() {
    if (state.embedded) {
      if (state.opts && typeof state.opts.onPaywall === "function") state.opts.onPaywall();
      return;
    }
    if (state.viewEl) T.paywall(state.viewEl, T.paywallOpts());
  }

  /* =====================================================================
   *  РАЗМЕТКА: СПИСОК
   * ===================================================================== */

  /** Ряд чипов одного фильтра («Все» + коды словаря). */
  function filterRowHtml(attr, group, keys, active) {
    var html =
      '<button type="button" class="chip' + (active ? "" : " chip--active") + '" ' +
      attr + '="">' + esc(pick("Все", "All")) + "</button>";
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i];
      html +=
        '<button type="button" class="chip' + (String(active) === key ? " chip--active" : "") + '" ' +
        attr + '="' + esc(key) + '">' +
        esc(T.label(group, key)) +
        "</button>";
    }
    return '<div class="tr-lib-filters__row">' + html + "</div>";
  }

  /** Текущий выбор одной строкой: «Все мышцы · Всё оборудование». */
  function filterSummary() {
    var m = state.muscle
      ? T.label("muscle", state.muscle)
      : pick("Все мышцы", "All muscles");
    var e = state.equipment
      ? T.label("equipment", state.equipment)
      : pick("всё оборудование", "any equipment");
    return m + " · " + e;
  }

  /**
   * Панель фильтров: поиск + сворачиваемый блок с чипами мышц и оборудования.
   *
   * Раньше чипы лежали двумя рядами с горизонтальной прокруткой: фильтр,
   * который уехал за край экрана, никто не применяет. Развернуть их с
   * переносом мало — двадцать четыре чипа занимают экран целиком, и список
   * упражнений, ради которого сюда пришли, оказывается ниже сгиба. Поэтому
   * фильтры свёрнуты, а выбранное показано строкой: по умолчанию виден
   * список, фильтры открываются одним касанием и никуда не прокручиваются.
   */
  function filtersHtml() {
    var open = !!state.filtersOpen;
    return (
      '<section class="card tr-lib-filters">' +
      '<input class="field__input tr-lib-search" id="trLibSearch" type="text" ' +
      'inputmode="search" autocomplete="off" maxlength="60" ' +
      'placeholder="' + esc(pick("Поиск по названию", "Search by name")) + '" ' +
      'value="' + esc(state.query) + '">' +
      '<button type="button" class="tr-lib-filters__toggle" id="trLibFiltersToggle" ' +
      'aria-expanded="' + (open ? "true" : "false") + '" aria-controls="trLibFiltersBody">' +
      icon("list", { size: 18, cls: "tr-lib-filters__ico" }) +
      '<span class="tr-lib-filters__sum" id="trLibFiltersSum">' + esc(filterSummary()) + "</span>" +
      '<span class="tr-lib-filters__chev" aria-hidden="true">' +
      icon("chevron", { size: 18, rotate: open ? 270 : 90 }) +
      "</span>" +
      "</button>" +
      '<div class="tr-lib-filters__body" id="trLibFiltersBody"' + (open ? "" : " hidden") + ">" +
      filterRowHtml("data-muscle", "muscle", MUSCLES, state.muscle) +
      filterRowHtml("data-equipment", "equipment", EQUIPMENT, state.equipment) +
      "</div>" +
      "</section>"
    );
  }

  /** Одна строка списка библиотеки. */
  function itemHtml(ex) {
    var badges = "";
    if (ex.excluded) {
      badges +=
        '<span class="tr-lib-item__badge tr-lib-item__badge--excluded">' +
        esc(pick("исключено", "excluded")) +
        "</span>";
    }
    if (ex.technique_status === "ready") {
      badges +=
        '<span class="tr-lib-item__badge">' + esc(pick("техника", "technique")) + "</span>";
    }
    return (
      '<button type="button" class="tr-lib-item" data-ex-id="' + esc(ex.id) + '">' +
      '<span class="tr-lib-item__body">' +
      '<span class="tr-lib-item__name">' + esc(T.exName(ex)) + badges + "</span>" +
      '<span class="tr-lib-item__meta">' + esc(itemMeta(ex)) + "</span>" +
      "</span>" +
      '<span class="tr-lib-item__diff" aria-hidden="true">' + difficultyMark(ex.difficulty) + "</span>" +
      "</button>"
    );
  }

  /** Список упражнений или пустое состояние. */
  function listHtml() {
    var items = state.items || [];
    if (!items.length) {
      return (
        '<section class="card wk-empty tr-lib-empty">' +
        '<div class="wk-empty__icon" aria-hidden="true">' + icon("search", { size: 36 }) + "</div>" +
        '<p class="wk-empty__title">' + esc(pick("Ничего не нашлось", "Nothing found")) + "</p>" +
        '<p class="wk-empty__text">' +
        esc(pick(
          "Попробуйте другой фильтр или очистите поиск.",
          "Try another filter or clear the search."
        )) +
        "</p>" +
        "</section>"
      );
    }
    var rows = "";
    for (var i = 0; i < items.length; i++) rows += itemHtml(items[i]);
    var more = "";
    if (state.total && state.total > items.length) {
      more =
        '<p class="tr-muted tr-lib-more">' +
        esc(pick("Показаны первые ", "Showing the first ") + items.length +
          pick(" из ", " of ") + state.total + pick(" — уточните поиск", " — refine the search")) +
        "</p>";
    }
    return '<section class="card tr-lib-list">' + rows + "</section>" + more;
  }

  /* =====================================================================
   *  РАЗМЕТКА: КАРТОЧКА УПРАЖНЕНИЯ
   * ===================================================================== */

  /** Шапка карточки: название, мышцы, оборудование, сложность. */
  function exHeadHtml(ex) {
    var meta = [];
    if (ex.muscle_group) meta.push(T.label("muscle", ex.muscle_group));
    if (ex.equipment) meta.push(T.label("equipment", ex.equipment));
    if (ex.category) meta.push(T.label("category", ex.category));
    if (ex.measure_type) meta.push(T.label("measure", ex.measure_type));

    var secondary = "";
    if (ex.secondary_muscles && ex.secondary_muscles.length) {
      secondary =
        '<p class="tr-ex-head__secondary">' +
        esc(pick("Дополнительно: ", "Also works: ") + T.labels("muscle", ex.secondary_muscles)) +
        "</p>";
    }
    var contra = "";
    if (ex.contraindications && ex.contraindications.length) {
      contra =
        '<p class="tr-ex-head__contra">' +
        esc(pick("Осторожно при: ", "Take care with: ") +
          T.labels("limitation", ex.contraindications)) +
        "</p>";
    }
    return (
      '<section class="card tr-ex-head">' +
      '<h2 class="tr-ex-head__name">' + esc(T.exName(ex)) + "</h2>" +
      '<p class="tr-ex-head__meta">' + esc(meta.join(" · ")) + "</p>" +
      '<p class="tr-ex-head__diff">' +
      esc(pick("Сложность: ", "Difficulty: ")) +
      '<span aria-hidden="true">' + difficultyMark(ex.difficulty) + "</span>" +
      (ex.is_unilateral ? esc(" · " + pick("на одну сторону", "one side at a time")) : "") +
      "</p>" +
      secondary +
      contra +
      "</section>"
    );
  }

  /** Вкладки «Техника» / «История». */
  function tabsHtml() {
    var tabs = [
      { key: "technique", label: pick("Техника", "Technique") },
      { key: "history", label: pick("История", "History") }
    ];
    var html = "";
    for (var i = 0; i < tabs.length; i++) {
      html +=
        '<button type="button" class="tr-ex-tab' +
        (state.tab === tabs[i].key ? " is-active" : "") +
        '" data-tab="' + tabs[i].key + '">' +
        esc(tabs[i].label) +
        "</button>";
    }
    return '<div class="tr-ex-tabs">' + html + "</div>";
  }

  /** Блок техники со списком пунктов. */
  function techBlockHtml(title, list) {
    if (!list || !list.length) return "";
    var items = "";
    for (var i = 0; i < list.length; i++) {
      items += '<li class="tr-technique__item">' + esc(list[i]) + "</li>";
    }
    return (
      '<div class="tr-technique__block">' +
      '<div class="tr-technique__title">' + esc(title) + "</div>" +
      '<ul class="tr-technique__list">' + items + "</ul>" +
      "</div>"
    );
  }

  /** Блок техники с одним абзацем. */
  function techTextHtml(title, text) {
    if (!text) return "";
    return (
      '<div class="tr-technique__block">' +
      '<div class="tr-technique__title">' + esc(title) + "</div>" +
      '<p class="tr-technique__text">' + esc(text) + "</p>" +
      "</div>"
    );
  }

  /** Содержимое вкладки «Техника». */
  function techniqueHtml() {
    var ex = state.exercise || {};
    if (state.techniqueBusy) {
      return (
        '<section class="card tr-technique">' +
        '<p class="tr-technique__wait">' +
        esc(pick("Тренер пишет технику…", "The coach is writing the technique…")) +
        "</p>" +
        '<div class="skeleton skeleton-line skeleton-title"></div>' +
        '<div class="skeleton skeleton-line"></div>' +
        '<div class="skeleton skeleton-line short"></div>' +
        '<div class="skeleton skeleton-line"></div>' +
        "</section>"
      );
    }
    var tech = ex.technique;
    if (!tech) {
      return (
        '<section class="card tr-technique">' +
        '<p class="tr-muted">' +
        esc(pick(
          "Техника пока не готова — тренер не смог её описать. Попробуйте ещё раз.",
          "The technique is not ready yet — the coach could not describe it. Try again."
        )) +
        "</p>" +
        '<button type="button" class="btn btn-ghost btn-block" id="trExTechRetry">' +
        esc(pick("Описать технику", "Describe the technique")) +
        "</button>" +
        "</section>"
      );
    }
    return (
      '<section class="card tr-technique">' +
      techTextHtml(pick("Какие мышцы работают", "Muscles worked"), tech.muscles_text) +
      techBlockHtml(pick("Как выполнять", "How to perform"), tech.steps) +
      techBlockHtml(pick("Ключевые моменты", "Key cues"), tech.cues) +
      techBlockHtml(pick("Частые ошибки", "Common mistakes"), tech.mistakes) +
      techTextHtml(pick("Дыхание", "Breathing"), tech.breathing) +
      techTextHtml(pick("Безопасность", "Safety"), tech.safety) +
      (ex.disclaimer ? '<p class="rec-disclaimer">' + esc(ex.disclaimer) + "</p>" : "") +
      "</section>"
    );
  }

  /** Ряд чипов-метрик графика истории. */
  function metricChipsHtml() {
    var html = "";
    for (var i = 0; i < METRICS.length; i++) {
      html +=
        '<button type="button" class="chip' +
        (state.historyMetric === METRICS[i] ? " chip--active" : "") +
        '" data-metric="' + METRICS[i] + '">' +
        esc(metricLabel(METRICS[i])) +
        "</button>";
    }
    return '<div class="tr-chart-controls">' + html + "</div>";
  }

  /** Рекорды упражнения (внутри вкладки «История»). */
  function historyRecordsHtml(records) {
    if (!records || !records.length) return "";
    var rows = "";
    for (var i = 0; i < records.length; i++) {
      var rec = records[i];
      var detail = [];
      if (rec.weight_kg != null) detail.push(T.fmtKg(rec.weight_kg));
      if (rec.reps != null) detail.push("× " + T.fmtNum(rec.reps));
      if (rec.date) detail.push(T.shortDate(rec.date, false));
      rows +=
        '<div class="tr-record-row">' +
        '<span class="tr-record-row__name">' + esc(T.label("recordType", rec.record_type)) + "</span>" +
        '<span class="tr-record-row__value">' + esc(recordValue(rec)) +
        (detail.length
          ? '<span class="tr-record-row__meta">' + esc(detail.join(" · ")) + "</span>"
          : "") +
        "</span>" +
        "</div>";
    }
    return (
      '<div class="tr-ex-section">' +
      '<h3 class="tr-section-title">' + esc(pick("Рекорды", "Personal records")) + "</h3>" +
      '<div class="tr-record-list">' + rows + "</div>" +
      "</div>"
    );
  }

  /** График по точкам истории. */
  function historyChartHtml(points) {
    var body =
      '<div class="tr-chart">' +
      T.lineChart(points || [], {
        w: 320,
        h: 150,
        key: state.historyMetric,
        unit: pick("кг", "kg")
      }) +
      "</div>";
    var caption = points && points.length
      ? metricLabel(state.historyMetric) + " · " + points.length + " " +
        T.plural(points.length, ["точка", "точки", "точек"], ["point", "points"])
      : pick(
          "Отметьте хотя бы один рабочий подход — точки появятся здесь.",
          "Log at least one working set — points will show up here."
        );
    return (
      '<div class="tr-ex-section" id="trExChart">' +
      '<h3 class="tr-section-title">' + esc(pick("Динамика", "Progress chart")) + "</h3>" +
      metricChipsHtml() +
      body +
      '<p class="tr-chart__caption">' + esc(caption) + "</p>" +
      "</div>"
    );
  }

  /** Последние подходы по сессиям. */
  function historySessionsHtml(sessions) {
    if (!sessions || !sessions.length) {
      return (
        '<div class="tr-ex-section">' +
        '<h3 class="tr-section-title">' + esc(pick("Последние подходы", "Recent sets")) + "</h3>" +
        '<p class="tr-muted">' +
        esc(pick("Вы ещё не делали это упражнение", "You have not done this exercise yet")) +
        "</p>" +
        "</div>"
      );
    }
    var rows = "";
    for (var i = 0; i < sessions.length; i++) {
      var s = sessions[i];
      var sets = s.sets || [];
      var parts = [];
      for (var j = 0; j < sets.length; j++) {
        var st = sets[j];
        var text = T.fmtSet(st.weight_kg, st.reps, st.time_sec);
        if (st.set_type === "warmup") text = pick("Р ", "W ") + text;
        if (!st.is_done) text = "(" + text + ")";
        parts.push((st.is_pr ? icon("trophy", { size: 13, cls: "tr-pr-mark" }) : "") + esc(text));
      }
      rows +=
        '<div class="tr-history-set">' +
        '<span class="tr-history-set__name">' +
        esc((s.date ? T.shortDate(s.date) : "") + (s.title ? " · " + s.title : "")) +
        "</span>" +
        '<span class="tr-history-set__vals">' +
        (parts.length ? parts.join(", ") : esc(pick("нет подходов", "no sets"))) +
        "</span>" +
        "</div>";
    }
    return (
      '<div class="tr-ex-section">' +
      '<h3 class="tr-section-title">' + esc(pick("Последние подходы", "Recent sets")) + "</h3>" +
      rows +
      '<p class="tr-muted tr-ex-hint">' +
      esc(pick(
        "«Р» — разминочный подход, в объём и рекорды не идёт.",
        "“W” marks a warm-up set — it does not count towards volume or records."
      )) +
      "</p>" +
      "</div>"
    );
  }

  /** Содержимое вкладки «История». */
  function historyHtml() {
    if (state.historyBusy || !state.history) {
      return (
        '<section class="card">' +
        '<div class="skeleton skeleton-line skeleton-title"></div>' +
        '<div class="skeleton skeleton-line"></div>' +
        '<div class="skeleton skeleton-line short"></div>' +
        "</section>"
      );
    }
    var h = state.history;
    return (
      '<section class="card tr-ex-history">' +
      historyChartHtml(h.points) +
      historyRecordsHtml(h.records) +
      historySessionsHtml(h.sessions) +
      "</section>"
    );
  }

  /** Кнопка «Исключить из программ» / «Вернуть». */
  function excludeHtml() {
    var ex = state.exercise || {};
    var excluded = !!ex.excluded;
    return (
      '<div class="tr-ex-actions" id="trExActions">' +
      '<button type="button" class="btn btn-block ' +
      (excluded ? "btn-cta" : "btn-ghost tr-ex-exclude") + '" id="trExExclude">' +
      esc(excluded
        ? pick("Вернуть в программы", "Bring back to programs")
        : pick("Исключить из программ", "Never suggest this")) +
      "</button>" +
      '<p class="tr-muted tr-ex-hint">' +
      esc(excluded
        ? pick(
            "Упражнение снова может попадать в программы и замены.",
            "This exercise can appear in programs and swaps again."
          )
        : pick(
            "Тренер перестанет предлагать это упражнение в программах и заменах.",
            "The coach will stop suggesting this exercise in programs and swaps."
          )) +
      "</p>" +
      "</div>"
    );
  }

  /* =====================================================================
   *  РЕНДЕР: СПИСОК
   * ===================================================================== */

  function renderList() {
    state.mode = "list";
    if (state.embedded) {
      // Вкладка: без шапки — заголовок и полоса вкладок у оболочки раздела.
      if (!state.host) return;
      state.host.innerHTML = '<div class="tr-library" id="trLibBody"></div>';
    } else {
      // Страница существует только для выбора упражнения в идущую тренировку.
      if (!state.viewEl) return;
      state.viewEl.innerHTML = shellHtml(
        pick("Добавить упражнение", "Add exercise"),
        pick("Тап по упражнению — и оно в тренировке", "Tap an exercise to add it to the workout"),
        "plus"
      );
      T.bindBack(state.viewEl, function () {
        // Выход из режима выбора — назад в тренировку.
        var sid = state.pickSession;
        state.pickSession = null;
        if (sid) App.state.trainerSessionId = sid;
        T.go("trainer-session");
      });
    }
    var body = byId("trLibBody");
    if (!body) return;
    body.innerHTML = filtersHtml() + '<div id="trLibList"></div>';
    bindFilters();
    if (state.items) renderItems();
    else loadItems();
  }

  /** Перерисовывает только список, не трогая поле поиска (фокус сохраняется). */
  function renderItems() {
    var host = byId("trLibList");
    if (!host) return;
    host.innerHTML = listHtml();
    var list = host.querySelector(".tr-lib-list");
    if (list) list.addEventListener("click", onItemClick);
  }

  function bindFilters() {
    var search = byId("trLibSearch");
    if (search) {
      search.addEventListener("input", function () {
        var value = search.value || "";
        if (state.searchTimer) clearTimeout(state.searchTimer);
        state.searchTimer = setTimeout(function () {
          state.searchTimer = null;
          if (state.query === value.trim()) return;
          state.query = value.trim();
          loadItems();
        }, SEARCH_DELAY);
      });
    }
    var root = rootEl();
    var filters = root ? root.querySelector(".tr-lib-filters") : null;
    if (filters) filters.addEventListener("click", onFilterClick);
    var toggle = byId("trLibFiltersToggle");
    if (toggle) toggle.addEventListener("click", toggleFilters);
  }

  /** Раскрывает/сворачивает блок фильтров (состояние живёт до ухода с экрана). */
  function toggleFilters() {
    var body = byId("trLibFiltersBody");
    var toggle = byId("trLibFiltersToggle");
    if (!body || !toggle) return;
    App.haptic("light");
    state.filtersOpen = !state.filtersOpen;
    body.hidden = !state.filtersOpen;
    toggle.setAttribute("aria-expanded", state.filtersOpen ? "true" : "false");
    var chev = toggle.querySelector(".tr-lib-filters__chev");
    if (chev) chev.innerHTML = icon("chevron", { size: 18, rotate: state.filtersOpen ? 270 : 90 });
  }

  function onFilterClick(ev) {
    var chip = ev.target.closest ? ev.target.closest(".chip") : null;
    if (!chip) return;
    var muscle = chip.getAttribute("data-muscle");
    var equipment = chip.getAttribute("data-equipment");
    var attr = muscle !== null ? "data-muscle" : equipment !== null ? "data-equipment" : null;
    if (!attr) return;
    var value = (muscle !== null ? muscle : equipment) || null;
    App.haptic("selection");
    if (attr === "data-muscle") {
      if (state.muscle === value) return;
      state.muscle = value;
    } else {
      if (state.equipment === value) return;
      state.equipment = value;
    }
    // Подсветку меняем на месте, чтобы не перерисовывать поле поиска.
    var row = chip.parentNode;
    var chips = row ? row.querySelectorAll(".chip") : [];
    for (var i = 0; i < chips.length; i++) chips[i].classList.remove("chip--active");
    chip.classList.add("chip--active");
    // Строка-сводка — единственное, что видно при свёрнутых фильтрах:
    // без её обновления выбор пропадал бы из виду сразу после нажатия.
    var sum = byId("trLibFiltersSum");
    if (sum) sum.textContent = filterSummary();
    loadItems();
  }

  function onItemClick(ev) {
    var btn = ev.target.closest ? ev.target.closest("[data-ex-id]") : null;
    if (!btn) return;
    var id = parseInt(btn.getAttribute("data-ex-id"), 10);
    if (isNaN(id)) return;
    App.haptic("light");
    if (state.pickSession) {
      addToSession(id, btn);
      return;
    }
    // Карточка — отдельная страница с «Назад», который вернёт на эту же
    // вкладку к тому же списку (он остаётся в состоянии модуля).
    T.openExercise(id, { page: "trainer", state: { trainerSegment: "exercises" } });
  }

  /**
   * Режим выбора: добавляет упражнение в текущую сессию (3 подхода по плану
   * сервера) и возвращает на экран тренировки.
   */
  function addToSession(exerciseId, btn) {
    if (state.addBusy) return;
    state.addBusy = true;
    if (btn) btn.disabled = true;
    var sid = state.pickSession;
    // По умолчанию 3×8–12; для упражнений на время/дистанцию повторы не задаём.
    var payload = { exercise_id: exerciseId, sets: 3 };
    var items = state.items || [];
    for (var i = 0; i < items.length; i++) {
      if (String(items[i].id) !== String(exerciseId)) continue;
      var m = items[i].measure_type || "reps_weight";
      if (m !== "time" && m !== "distance") {
        payload.reps_min = 8;
        payload.reps_max = 12;
      }
      break;
    }
    App.api
      .trainerAddExercise(sid, payload)
      .then(function () {
        state.addBusy = false;
        App.haptic("success");
        App.toast(pick("Упражнение добавлено", "Exercise added"));
        state.pickSession = null;
        T.cache.activeSession = null;
        App.state.trainerSessionId = sid;
        T.go("trainer-session");
      })
      .catch(function (err) {
        state.addBusy = false;
        if (btn) btn.disabled = false;
        App.haptic("error");
        App.toast(T.errMessage(err, pick("Не удалось добавить упражнение", "Failed to add the exercise")));
      });
  }

  /* =====================================================================
   *  РЕНДЕР: КАРТОЧКА
   * ===================================================================== */

  function renderDetail() {
    if (!state.viewEl) return;
    state.mode = "detail";
    var ex = state.exercise;
    state.viewEl.innerHTML = shellHtml(
      ex ? T.exName(ex) : pick("Упражнение", "Exercise"),
      ex ? itemMeta(ex) : "",
      "dumbbell"
    );
    T.bindBack(state.viewEl, onDetailBack);
    var body = byId("trLibBody");
    if (!body) return;
    if (!ex) {
      body.innerHTML = T.skeleton(3) + T.skeleton(4);
      return;
    }
    body.innerHTML =
      exHeadHtml(ex) +
      tabsHtml() +
      '<div id="trExTab"></div>' +
      excludeHtml();
    bindDetail();
    renderTab();
  }

  /** Перерисовывает только активную вкладку. */
  function renderTab() {
    var host = byId("trExTab");
    if (!host) return;
    host.innerHTML = state.tab === "history" ? historyHtml() : techniqueHtml();
    if (state.tab === "history") {
      var chart = byId("trExChart");
      if (chart) chart.addEventListener("click", onMetricClick);
    } else {
      var retry = byId("trExTechRetry");
      if (retry) {
        retry.addEventListener("click", function () {
          App.haptic("medium");
          loadTechnique(true);
        });
      }
    }
  }

  function bindDetail() {
    var root = rootEl();
    var tabs = root ? root.querySelector(".tr-ex-tabs") : null;
    if (tabs) tabs.addEventListener("click", onTabClick);
    var excl = byId("trExExclude");
    if (excl) excl.addEventListener("click", onExcludeClick);
  }

  function onTabClick(ev) {
    var btn = ev.target.closest ? ev.target.closest("[data-tab]") : null;
    if (!btn) return;
    var tab = btn.getAttribute("data-tab");
    if (!tab || tab === state.tab) return;
    App.haptic("selection");
    state.tab = tab;
    var all = ev.currentTarget.querySelectorAll(".tr-ex-tab");
    for (var i = 0; i < all.length; i++) {
      all[i].classList.toggle("is-active", all[i].getAttribute("data-tab") === tab);
    }
    renderTab();
    if (tab === "history" && !state.history && !state.historyBusy) loadHistory();
  }

  function onMetricClick(ev) {
    var chip = ev.target.closest ? ev.target.closest("[data-metric]") : null;
    if (!chip) return;
    var metric = chip.getAttribute("data-metric");
    if (!metric || metric === state.historyMetric) return;
    App.haptic("selection");
    state.historyMetric = metric;
    renderTab();
  }

  /**
   * «← Назад» из карточки: туда, откуда её открыли (вкладка раздела или
   * предпросмотр программы), с прежней прокруткой. Без записи — Trainer.back().
   */
  function onDetailBack() {
    var ret = state.ret;
    state.ret = null;
    T.returnFromExercise(ret);
  }

  /* =====================================================================
   *  ДЕЙСТВИЯ
   * ===================================================================== */

  function onExcludeClick() {
    if (state.excludeBusy || !state.exercise) return;
    var next = !state.exercise.excluded;
    var btn = byId("trExExclude");
    state.excludeBusy = true;
    if (btn) btn.disabled = true;
    App.haptic("medium");
    App.api
      .trainerExcludeExercise(state.exercise.id, next)
      .then(function (res) {
        state.excludeBusy = false;
        state.exercise.excluded = res && res.excluded != null ? !!res.excluded : next;
        // Список мог быть загружен раньше — держим бейдж в актуальном виде.
        markExcludedInList(state.exercise.id, state.exercise.excluded);
        T.cache.invalidate();
        App.toast(
          state.exercise.excluded
            ? pick("Больше не предложим это упражнение", "We will not suggest it anymore")
            : pick("Упражнение вернулось в программы", "Exercise is back in your programs")
        );
        renderExcludeActions();
      })
      .catch(function (err) {
        state.excludeBusy = false;
        if (btn) btn.disabled = false;
        App.toast(T.errMessage(err, pick("Не удалось изменить", "Failed to update")));
      });
  }

  /** Перерисовывает блок «Исключить/Вернуть» после успешного запроса. */
  function renderExcludeActions() {
    if (state.mode !== "detail") return;
    var old = byId("trExActions");
    if (!old || !old.parentNode) return;
    var wrap = document.createElement("div");
    wrap.innerHTML = excludeHtml();
    var fresh = wrap.firstChild;
    old.parentNode.replaceChild(fresh, old);
    var btn = byId("trExExclude");
    if (btn) btn.addEventListener("click", onExcludeClick);
  }

  /** Обновляет флаг excluded в закэшированном списке. */
  function markExcludedInList(id, excluded) {
    var items = state.items || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].id === id) {
        items[i].excluded = !!excluded;
        return;
      }
    }
  }

  /* =====================================================================
   *  ЗАГРУЗКА
   * ===================================================================== */

  function listErrorHtml(err) {
    return T.errorCard(T.errMessage(err), "trLibRetry");
  }

  /** Список упражнений с текущими фильтрами. */
  function loadItems() {
    var host = byId("trLibList");
    if (!host) return;
    var token = nextToken("list");
    state.listLoading = true;
    host.innerHTML = T.skeleton(4);
    App.api
      .trainerExercises({
        muscle: state.muscle,
        equipment: state.equipment,
        q: state.query,
        limit: 100
      })
      .then(function (data) {
        state.listLoading = false;
        if (stale("list", token) || state.mode !== "list" || !byId("trLibList")) return;
        state.items = (data && data.items) || [];
        state.total = (data && data.total) || state.items.length;
        renderItems();
      })
      .catch(function (err) {
        state.listLoading = false;
        if (stale("list", token) || state.mode !== "list") return;
        if (err && err.status === 402) {
          showPaywall();
          return;
        }
        var h = byId("trLibList");
        if (!h) return;
        h.innerHTML = listErrorHtml(err);
        var btn = byId("trLibRetry");
        if (btn) {
          btn.addEventListener("click", function () {
            App.haptic("light");
            loadItems();
          });
        }
      });
  }

  /** Карточка упражнения: сначала быстро без техники, затем техника. */
  function openDetail(exerciseId) {
    // Ответы по предыдущему упражнению больше не нужны.
    tokens.tech += 1;
    tokens.hist += 1;
    state.exerciseId = exerciseId;
    state.exercise = null;
    state.history = null;
    state.techniqueTried = false;
    state.techniqueBusy = false;
    state.historyBusy = false;
    state.tab = "technique";
    state.historyMetric = "est_1rm";
    renderDetail();
    App.scrollTop();

    var token = nextToken("detail");
    App.api
      .trainerExercise(exerciseId, false)
      .then(function (ex) {
        if (stale("detail", token) || state.mode !== "detail") return;
        state.exercise = ex || null;
        renderDetail();
        if (state.exercise && !state.exercise.technique) loadTechnique(false);
      })
      .catch(function (err) {
        if (stale("detail", token) || state.mode !== "detail") return;
        if (err && err.status === 402) {
          T.paywall(state.viewEl, T.paywallOpts());
          return;
        }
        var body = byId("trLibBody");
        if (!body) return;
        body.innerHTML = T.errorCard(T.errMessage(err), "trExRetry");
        var btn = byId("trExRetry");
        if (btn) {
          btn.addEventListener("click", function () {
            App.haptic("light");
            openDetail(exerciseId);
          });
        }
      });
  }

  /**
   * Техника: единственный ИИ-вызов страницы (enforce_ai на промахе кэша).
   * Автоматически — один раз за заход; дальше только по кнопке «Описать технику».
   */
  function loadTechnique(force) {
    if (state.techniqueBusy || !state.exerciseId) return;
    if (state.techniqueTried && !force) return;
    state.techniqueTried = true;
    state.techniqueBusy = true;
    if (state.tab === "technique") renderTab();
    var token = nextToken("tech");
    App.api
      .trainerExercise(state.exerciseId, true)
      .then(function (ex) {
        state.techniqueBusy = false;
        if (stale("tech", token) || state.mode !== "detail") return;
        if (ex) state.exercise = ex;
        if (state.tab === "technique") renderTab();
      })
      .catch(function (err) {
        state.techniqueBusy = false;
        if (stale("tech", token) || state.mode !== "detail") return;
        if (state.tab === "technique") renderTab();
        App.toast(T.errMessage(err, pick("Не удалось описать технику", "Failed to describe the technique")));
      });
  }

  /** История упражнения: рекорды, точки графика, последние подходы. */
  function loadHistory() {
    if (state.historyBusy || !state.exerciseId) return;
    state.historyBusy = true;
    if (state.tab === "history") renderTab();
    var token = nextToken("hist");
    App.api
      .trainerExerciseHistory(state.exerciseId)
      .then(function (data) {
        state.historyBusy = false;
        if (stale("hist", token) || state.mode !== "detail") return;
        state.history = data || { records: [], sessions: [], points: [] };
        if (state.tab === "history") renderTab();
      })
      .catch(function (err) {
        state.historyBusy = false;
        if (stale("hist", token) || state.mode !== "detail") return;
        state.history = { records: [], sessions: [], points: [] };
        if (state.tab === "history") renderTab();
        App.toast(T.errMessage(err, pick("Не удалось загрузить историю", "Failed to load the history")));
      });
  }

  /* =====================================================================
   *  КОНТРОЛЛЕР
   * ===================================================================== */

  /** Сброс списка к виду «все упражнения» (свежий заход). */
  function resetList() {
    state.items = null;
    state.total = 0;
    state.muscle = null;
    state.equipment = null;
    state.query = "";
    state.filtersOpen = false;
    state.listLoading = false;
  }

  /** Гасит отложенный поиск и ответы в полёте (уход со страницы/вкладки). */
  function stopWork() {
    if (state.searchTimer) {
      clearTimeout(state.searchTimer);
      state.searchTimer = null;
    }
    dropRequests();
    state.listLoading = false;
    state.techniqueBusy = false;
    state.historyBusy = false;
    state.excludeBusy = false;
    state.addBusy = false;
  }

  /* =====================================================================
   *  ВКЛАДКА «УПРАЖНЕНИЯ» (монтируется оболочкой раздела)
   * ===================================================================== */

  /**
   * Рисует библиотеку в панели вкладки.
   * @param {HTMLElement} el панель оболочки
   * @param {object} [opts] {keep: тот же список и фильтры (возврат из
   *        карточки), onPaywall()}
   */
  function mount(el, opts) {
    if (!el) return;
    opts = opts || {};
    stopWork();
    state.viewEl = null;
    state.host = el;
    state.embedded = true;
    state.opts = opts;
    state.pickSession = null;
    // Фильтры и найденный список переживают только возврат из карточки:
    // свежий заход в раздел начинается со всего каталога.
    if (!opts.keep) resetList();
    renderList();
  }

  function unmount() {
    stopWork();
    state.host = null;
    state.embedded = false;
    state.opts = null;
  }

  window.TrainerLibrary = { mount: mount, unmount: unmount };

  /* =====================================================================
   *  СТРАНИЦА: КАРТОЧКА УПРАЖНЕНИЯ / ВЫБОР В ТРЕНИРОВКУ
   * ===================================================================== */

  var controller = {
    onShow: function (viewEl) {
      // Режим выбора упражнения для текущей тренировки (ставит page-trainer-session)
      // и прямой вход в карточку (Trainer.openExercise).
      var pickFor = App.state.trainerPickForSession;
      App.state.trainerPickForSession = null;
      var passed = App.state.trainerExerciseId;
      App.state.trainerExerciseId = null;
      var id = parseInt(passed, 10);
      var hasId = !isNaN(id) && id > 0;

      // Просто библиотека — это вкладка раздела, а не страница.
      if (!pickFor && !hasId) {
        App.state.trainerSegment = "exercises";
        App.navigate("trainer");
        return;
      }

      state.viewEl = viewEl;
      state.host = null;
      state.embedded = false;
      state.opts = null;
      if (!T.isPro()) {
        T.paywall(viewEl, T.paywallOpts());
        return;
      }
      stopWork();

      if (pickFor) {
        state.pickSession = pickFor;
        state.ret = null;
        // Свой список: фильтры вкладки для выбора в тренировку не подходят.
        resetList();
        renderList();
        return;
      }

      state.pickSession = null;
      state.ret = T.takeExerciseReturn(id);
      openDetail(id);
    },

    onHide: function () {
      stopWork();
      state.viewEl = null;
      T.closeSheet(true);
    }
  };

  window.PageTrainerExercise = controller;
  App.registerPage("trainer-exercise", controller);
})();
