/*
 * page-trainer-progress.js — вкладка «Прогресс» раздела «AI-тренер»
 * (ТЗ §2.7, §6.2 блок «Прогресс/упражнения»).
 *
 * Публичный интерфейс — window.TrainerProgress:
 *   mount(el, opts) / unmount() — рисует прогресс в панели оболочки раздела
 *     (page-trainer.js), без своей шапки и кнопки «Назад»;
 *   focusReview()               — прокрутить к недельному разбору.
 *
 * Отдельной страницы у прогресса больше нет: это вид раздела, а не шаг
 * сценария. Имя "trainer-progress" оставлено зарегистрированным как
 * переадресация на вкладку (App.state.trainerSegment = "progress"), чтобы
 * старые переходы из других разделов не упирались в пустой экран.
 *
 * Экран собирается из трёх независимых запросов (каждый в своём контейнере,
 * чтобы сбой одного не гасил остальные):
 *   • GET /trainer/progress   — стрик, итоги за 4 недели, эта неделя vs прошлая,
 *                               объём по группам мышц за 7 дней, рекорды,
 *                               топ-5 упражнений и точки графика;
 *   • GET /trainer/sessions   — история сессий (тап по карточке — детали);
 *   • GET /trainer/review/latest + POST /trainer/review/weekly — недельный
 *     разбор с чекбоксами правок и кнопкой «Применить к следующей неделе».
 *
 * Вход по баннеру «Разбор готов» с «Сегодня»: App.state.trainerProgressSection
 * === "review" — после загрузки прокручиваем страницу к карточке разбора.
 *
 * Данные вкладки живут в состоянии модуля, пока открыт раздел: оболочка
 * монтирует вкладку один раз, а после карточки упражнения просит keep —
 * тогда всё рисуется из кэша, без скелетона и без новых запросов.
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

  // Метрики графика: ключ точки → подпись.
  var METRICS = ["est_1rm", "max_weight", "volume"];
  // Периоды графика (совпадают с параметром period маршрута /trainer/progress).
  var PERIODS = ["4w", "3m", "all"];

  // Внутреннее состояние модуля.
  var state = {
    host: null,             // панель оболочки, в которую смонтирована вкладка
    opts: null,             // опции mount: {onPaywall}
    data: null,             // последний TrainerProgressOut
    period: "4w",           // 4w|3m|all
    exerciseId: null,       // упражнение графика (null — самое частое)
    metric: "est_1rm",      // est_1rm|max_weight|volume
    loading: false,         // идёт загрузка прогресса
    chartLoading: false,    // идёт перезапрос графика
    sessions: null,         // последний ответ /trainer/sessions
    sessionCache: {},       // id сессии → TrainerSessionOut (детали истории)
    openSession: null,      // раскрытая карточка истории
    review: null,           // последний TrainerWeeklyReviewOut
    reviewLoaded: false,    // последний разбор уже запрошен (null — его нет)
    reviewBusy: false,      // идёт генерация/применение разбора
    scrollToReview: false,  // вход по баннеру «Разбор готов»
    reqId: 0                // счётчик запросов (игнорируем устаревшие ответы)
  };

  /* =====================================================================
   *  ФОРМАТИРОВАНИЕ
   * ===================================================================== */

  /** Подпись метрики графика. */
  function metricLabel(key) {
    if (key === "max_weight") return pick("Вес", "Weight");
    if (key === "volume") return pick("Объём", "Volume");
    return pick("1RM", "1RM");
  }

  /** Подпись периода графика. */
  function periodLabel(key) {
    if (key === "3m") return pick("3 мес", "3 mo");
    if (key === "all") return pick("Всё", "All");
    return pick("4 нед", "4 wk");
  }

  /** Объём в килограммах с разделителем тысяч: «3 200 кг». */
  function fmtVolume(v) {
    var n = Math.round(Number(v) || 0);
    var s = String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
    return s + " " + pick("кг", "kg");
  }

  /** Склонение «неделя/недели/недель». */
  function weekWord(n) {
    if (App.lang === "en") return n === 1 ? "week" : "weeks";
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return "неделя";
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "недели";
    return "недель";
  }

  /** Склонение «подход/подхода/подходов». */
  function setWord(n) {
    if (App.lang === "en") return n === 1 ? "set" : "sets";
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return "подход";
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "подхода";
    return "подходов";
  }

  /** Значение рекорда в человеческом виде по его типу. */
  function recordValue(rec) {
    var type = rec.record_type;
    if (type === "max_reps") {
      return T.fmtNum(rec.value) + " " + pick("повт.", "reps");
    }
    if (type === "max_time") {
      return T.fmtClock(rec.value);
    }
    return T.fmtKg(rec.value);
  }

  /** Уточнение к рекорду: «40 кг × 8» (для 1RM и объёма подхода). */
  function recordDetail(rec) {
    if (rec.weight_kg == null && rec.reps == null) return "";
    var parts = [];
    if (rec.weight_kg != null) parts.push(T.fmtKg(rec.weight_kg));
    if (rec.reps != null) parts.push("× " + T.fmtNum(rec.reps));
    return parts.join(" ");
  }

  /* =====================================================================
   *  РАЗМЕТКА: КАРКАС
   * ===================================================================== */

  /**
   * Три независимых контейнера: сводка с графиком, история, разбор. Шапки
   * нет — заголовок и полоса вкладок принадлежат оболочке раздела.
   */
  function shellHtml() {
    return (
      '<div class="tr-progress">' +
      '<div id="trProgBody"></div>' +
      '<div id="trProgHistory"></div>' +
      '<div id="trProgReview"></div>' +
      "</div>"
    );
  }

  /** 402 посреди работы (подписка кончилась) — paywall на весь экран раздела. */
  function showPaywall() {
    if (state.opts && typeof state.opts.onPaywall === "function") {
      state.opts.onPaywall();
      return;
    }
    var view = document.getElementById("view");
    if (view) T.paywall(view, T.paywallOpts());
  }

  /* =====================================================================
   *  РАЗМЕТКА: СВОДКА, МЫШЦЫ, ГРАФИК, РЕКОРДЫ
   * ===================================================================== */

  /**
   * Плитка ключевой цифры: число крупно сжатым шрифтом, единица отдельно
   * и мельче (иначе «кг» растягивает число), подпись — надзаголовком.
   * @param {object} o {value, unit, label, delta, mod}
   */
  function kpiHtml(o) {
    return (
      '<div class="tr-kpi' + (o.mod ? " " + o.mod : "") + '">' +
      '<span class="tr-kpi__value num">' + esc(o.value) +
      (o.unit ? '<span class="tr-kpi__unit">' + esc(o.unit) + "</span>" : "") +
      "</span>" +
      '<span class="tr-kpi__label">' + esc(o.label) + "</span>" +
      (o.delta ? '<span class="tr-kpi__delta">' + esc(o.delta) + "</span>" : "") +
      "</div>"
    );
  }

  /** Число с разделителем тысяч без единицы: «3 200». */
  function fmtThousands(v) {
    return String(Math.round(Number(v) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  /** Сводка: серия, тренировки за 4 недели, тоннаж и подходы этой недели. */
  function summaryHtml(d) {
    var streak = d.streak || {};
    var totals = d.totals_4w || {};
    var wc = d.week_compare || {};
    var cur = wc.this || {};
    var prev = wc.prev || {};

    var weeks = Number(streak.weeks) || 0;
    var streakDelta = streak.this_week_goal
      ? (Number(streak.this_week_done) || 0) + pick(" из ", " of ") + streak.this_week_goal +
        pick(" на этой неделе", " this week")
      : "";

    var curVol = Number(cur.volume_kg) || 0;
    var prevVol = Number(prev.volume_kg) || 0;
    var mod = "";
    var deltaText = "";
    if (prevVol > 0) {
      var pct = Math.round(((curVol - prevVol) / prevVol) * 100);
      if (pct > 0) {
        mod = "tr-kpi--up";
        deltaText = "+" + pct + pick("% к прошлой неделе", "% vs last week");
      } else if (pct < 0) {
        mod = "tr-kpi--down";
        deltaText = pct + pick("% к прошлой неделе", "% vs last week");
      } else {
        deltaText = pick("Как на прошлой неделе", "Same as last week");
      }
    } else if (curVol > 0) {
      // Прошлой недели нет — сравнивать не с чем; «+∞ %» или «−100 %»
      // читались бы как ошибка.
      deltaText = pick("Первая неделя с данными", "First week with data");
    }

    return (
      '<section class="card tr-progress-summary">' +
      '<span class="eyebrow">' + esc(pick("Сводка", "Summary")) + "</span>" +
      '<div class="tr-kpi-grid">' +
      kpiHtml({
        value: weeks ? String(weeks) : "—",
        unit: weeks ? weekWord(weeks) : "",
        label: pick("Серия недель", "Week streak"),
        delta: streakDelta
      }) +
      kpiHtml({
        value: App.fmt(totals.sessions || 0),
        label: pick("Тренировок за 4 недели", "Workouts in 4 weeks")
      }) +
      kpiHtml({
        value: fmtThousands(curVol),
        unit: pick("кг", "kg"),
        label: pick("Тоннаж недели", "Weekly tonnage"),
        delta: deltaText,
        mod: mod
      }) +
      kpiHtml({
        value: App.fmt(cur.sets || 0),
        unit: App.fmt(cur.minutes || 0) + " " + pick("мин", "min"),
        label: pick("Подходов за неделю", "Sets this week")
      }) +
      "</div>" +
      "</section>"
    );
  }

  /** Объём по группам мышц за 7 дней с рекомендуемой зоной 10–20 подходов. */
  function muscleBarsHtml(d) {
    var items = d.muscle_volume_7d || [];
    if (!items.length) {
      return (
        '<section class="card">' +
        '<h3 class="tr-section-title">' + esc(pick("Мышцы за 7 дней", "Muscles · 7 days")) + "</h3>" +
        '<p class="tr-muted">' +
        esc(pick("Пока нет отмеченных подходов", "No completed sets yet")) +
        "</p>" +
        "</section>"
      );
    }
    var scale = 1;
    for (var i = 0; i < items.length; i++) {
      var maxTarget = Number(items[i].target_max) || 20;
      scale = Math.max(scale, Number(items[i].sets) || 0, maxTarget);
    }
    scale = scale * 1.05;

    var rows = "";
    for (var j = 0; j < items.length; j++) {
      var it = items[j];
      var sets = Number(it.sets) || 0;
      var tMin = Number(it.target_min) || 10;
      var tMax = Number(it.target_max) || 20;
      var mod = sets < tMin ? "--low" : sets > tMax ? "--high" : "--ok";
      var w = Math.max(2, Math.round((sets / scale) * 100));
      var zoneLeft = Math.round((tMin / scale) * 100);
      var zoneWidth = Math.max(1, Math.round(((tMax - tMin) / scale) * 100));
      rows +=
        '<div class="tr-muscle-bar tr-muscle-bar' + mod + '">' +
        '<span class="tr-muscle-bar__label">' + esc(T.label("muscle", it.muscle_group)) + "</span>" +
        '<span class="tr-muscle-bar__track">' +
        '<span class="tr-muscle-bar__zone" style="left:' + zoneLeft + "%;width:" + zoneWidth + '%"></span>' +
        '<span class="tr-muscle-bar__fill" style="width:' + w + '%"></span>' +
        "</span>" +
        '<span class="tr-muscle-bar__val">' + esc(sets) + "</span>" +
        "</div>";
    }
    return (
      '<section class="card">' +
      '<h3 class="tr-section-title">' + esc(pick("Мышцы за 7 дней", "Muscles · 7 days")) + "</h3>" +
      '<div class="tr-muscle-bars">' + rows + "</div>" +
      '<p class="tr-muscle-bars__hint">' +
      esc(pick(
        "Полоса — рабочие подходы за 7 дней, светлая зона — рекомендуемые 10–20.",
        "The bar shows working sets over 7 days; the light zone is the recommended 10–20."
      )) +
      "</p>" +
      "</section>"
    );
  }

  /** Ряд чипов-переключателей. */
  function chipsHtml(items, activeKey, attr) {
    var html = "";
    for (var i = 0; i < items.length; i++) {
      var active = String(items[i].key) === String(activeKey);
      html +=
        '<button type="button" class="chip' + (active ? " chip--active" : "") + '" ' +
        attr + '="' + esc(items[i].key) + '">' +
        esc(items[i].label) +
        "</button>";
    }
    return '<div class="tr-chart-controls">' + html + "</div>";
  }

  /** Карточка графика: выбор упражнения, метрики и периода + SVG. */
  function chartHtml(d) {
    var chart = d.chart || null;
    var top = d.top_exercises || [];

    var exItems = [];
    for (var i = 0; i < top.length; i++) {
      exItems.push({ key: top[i].id, label: T.exName(top[i]) });
    }
    var activeEx = chart ? chart.exercise_id : state.exerciseId;

    var metricItems = [];
    for (var j = 0; j < METRICS.length; j++) {
      metricItems.push({ key: METRICS[j], label: metricLabel(METRICS[j]) });
    }
    var periodItems = [];
    for (var k = 0; k < PERIODS.length; k++) {
      periodItems.push({ key: PERIODS[k], label: periodLabel(PERIODS[k]) });
    }

    var body;
    if (state.chartLoading) {
      body = '<div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line short"></div>';
    } else {
      var points = (chart && chart.points) || [];
      var unit = state.metric === "volume" || state.metric === "max_weight" || state.metric === "est_1rm"
        ? pick("кг", "kg")
        : "";
      body =
        '<div class="tr-chart">' +
        T.lineChart(points, { w: 320, h: 150, key: state.metric, unit: unit }) +
        "</div>";
      if (!points.length) {
        body +=
          '<p class="tr-chart__caption">' +
          esc(pick(
            "Отметьте хотя бы один рабочий подход — точки появятся здесь.",
            "Log at least one working set — points will show up here."
          )) +
          "</p>";
      } else {
        body +=
          '<p class="tr-chart__caption">' +
          esc(metricLabel(state.metric) + " · " + periodLabel(state.period) +
            " · " + points.length + " " + T.plural(points.length, ["точка", "точки", "точек"], ["point", "points"])) +
          "</p>";
      }
    }

    var title = chart && chart.exercise ? T.exName(chart.exercise) : pick("Динамика", "Progress chart");
    return (
      '<section class="card tr-chart-card" id="trProgChart">' +
      '<h3 class="tr-section-title">' + esc(pick("График: ", "Chart: ") + title) + "</h3>" +
      (exItems.length ? chipsHtml(exItems, activeEx, "data-chart-ex") : "") +
      chipsHtml(metricItems, state.metric, "data-chart-metric") +
      chipsHtml(periodItems, state.period, "data-chart-period") +
      body +
      "</section>"
    );
  }

  /** Список рекордов: упражнение → тип рекорда, значение, дата. */
  function recordsHtml(d) {
    var records = d.records || [];
    if (!records.length) {
      return (
        '<section class="card">' +
        '<h3 class="tr-section-title">' + esc(pick("Рекорды", "Personal records")) + "</h3>" +
        '<p class="tr-muted">' +
        esc(pick("Рекорды появятся после первых тренировок", "Records appear after your first workouts")) +
        "</p>" +
        "</section>"
      );
    }
    var rows = "";
    for (var i = 0; i < records.length; i++) {
      var rec = records[i];
      var detail = recordDetail(rec);
      rows +=
        '<div class="tr-record-row"' +
        (rec.exercise && rec.exercise.id != null ? ' data-ex-id="' + esc(rec.exercise.id) + '"' : "") +
        ">" +
        '<span class="tr-record-row__name">' + esc(T.exName(rec.exercise)) +
        '<span class="tr-record-row__type">' + esc(T.label("recordType", rec.record_type)) + "</span>" +
        "</span>" +
        '<span class="tr-record-row__value">' + esc(recordValue(rec)) +
        (detail || rec.date
          ? '<span class="tr-record-row__meta">' +
            esc([detail, rec.date ? T.shortDate(rec.date, false) : ""].filter(Boolean).join(" · ")) +
            "</span>"
          : "") +
        "</span>" +
        "</div>";
    }
    return (
      '<section class="card">' +
      '<h3 class="tr-section-title">' + esc(pick("Рекорды", "Personal records")) + "</h3>" +
      '<div class="tr-record-list">' + rows + "</div>" +
      "</section>"
    );
  }

  /* =====================================================================
   *  РАЗМЕТКА: ИСТОРИЯ СЕССИЙ
   * ===================================================================== */

  function historyHtml(data) {
    var items = (data && data.items) || [];
    if (!items.length) {
      return (
        '<section class="card">' +
        '<h3 class="tr-section-title">' + esc(pick("История", "History")) + "</h3>" +
        '<p class="tr-muted">' +
        esc(pick("Здесь появятся завершённые тренировки", "Finished workouts will show up here")) +
        "</p>" +
        "</section>"
      );
    }
    var rows = "";
    for (var i = 0; i < items.length; i++) {
      var s = items[i];
      var meta = [];
      if (s.duration_min) meta.push(T.fmtDuration(s.duration_min));
      if (s.total_volume_kg) meta.push(fmtVolume(s.total_volume_kg));
      if (s.total_sets) meta.push(s.total_sets + " " + setWord(s.total_sets));
      if (s.calories_burned) meta.push(App.fmt(s.calories_burned) + " " + pick("ккал", "kcal"));
      // Рекорды — отдельным значком с числом: внутри meta их пришлось бы
      // склеивать с текстом и экранировать, а иконка в esc() не выживает.
      var prBadge = s.prs_count
        ? '<span class="tr-history-item__pr">' + icon("trophy", { size: 14 }) +
          "<span>" + esc(String(s.prs_count)) + "</span></span>"
        : "";
      var title = (s.date ? T.shortDate(s.date) : "") + (s.title ? " · " + s.title : "");
      rows +=
        '<div class="tr-history-item" data-session="' + esc(s.id) + '">' +
        '<button type="button" class="tr-history-item__head">' +
        '<span class="tr-history-item__title">' + esc(title) + "</span>" +
        '<span class="tr-history-item__meta">' + esc(meta.join(" · ")) + prBadge + "</span>" +
        '<span class="tr-history-item__chevron" aria-hidden="true">' + icon("chevron", { size: 18, rotate: 90 }) + "</span>" +
        "</button>" +
        '<div class="tr-history-item__body" hidden></div>' +
        "</div>";
    }
    var more = "";
    if (data.total && data.total > items.length) {
      more =
        '<p class="tr-muted tr-history__more">' +
        esc(pick("Показаны последние ", "Showing the latest ") + items.length +
          pick(" из ", " of ") + data.total) +
        "</p>";
    }
    return (
      '<section class="card">' +
      '<h3 class="tr-section-title">' + esc(pick("История", "History")) + "</h3>" +
      '<div class="tr-history">' + rows + "</div>" +
      more +
      "</section>"
    );
  }

  /** Детали сессии: сеты по упражнениям и отзыв. */
  function sessionDetailHtml(session) {
    if (!session) return "";
    var exs = session.exercises || [];
    var rows = "";
    for (var i = 0; i < exs.length; i++) {
      var sex = exs[i];
      if (sex.status === "replaced") continue;
      var sets = sex.sets || [];
      var parts = [];
      for (var j = 0; j < sets.length; j++) {
        var st = sets[j];
        if (!st.is_done) continue;
        var text = T.fmtSet(st.weight_kg, st.reps, st.time_sec);
        if (st.set_type === "warmup") text = pick("Р ", "W ") + text;
        // Рекорд помечаем иконкой рядом со значением: символ внутри строки
        // попадал бы в esc() вместе с текстом и печатался кубком-эмодзи.
        parts.push((st.is_pr ? icon("trophy", { size: 13, cls: "tr-pr-mark" }) : "") + esc(text));
      }
      if (!parts.length && sex.status !== "skipped") continue;
      rows +=
        '<div class="tr-history-set">' +
        '<span class="tr-history-set__name">' + esc(T.exName(sex)) + "</span>" +
        '<span class="tr-history-set__vals">' +
        (sex.status === "skipped" && !parts.length
          ? esc(pick("пропущено", "skipped"))
          : parts.join(", ")) +
        "</span>" +
        "</div>";
    }
    if (!rows) {
      rows = '<p class="tr-muted">' + esc(pick("Подходы не записаны", "No sets recorded")) + "</p>";
    }
    var feedback = "";
    if (session.feedback) {
      feedback =
        '<p class="tr-history-feedback">' +
        esc(pick("Отзыв: ", "Feedback: ") + T.label("feedback", session.feedback) +
          (session.feedback_note ? " — " + session.feedback_note : "")) +
        "</p>";
    }
    var adapt = "";
    var lines = (session.adaptation && session.adaptation.lines) || [];
    if (lines.length) {
      var li = "";
      for (var k = 0; k < lines.length; k++) li += "<li>" + esc(lines[k]) + "</li>";
      adapt =
        '<div class="tr-history-adapt">' +
        '<div class="tr-history-adapt__title">' + esc(pick("Учтено на следующий раз", "Applied next time")) + "</div>" +
        "<ul>" + li + "</ul>" +
        "</div>";
    }
    return rows + feedback + adapt;
  }

  /* =====================================================================
   *  РАЗМЕТКА: НЕДЕЛЬНЫЙ РАЗБОР
   * ===================================================================== */

  /** Текстовое описание одной правки разбора. */
  function changeText(ch) {
    var name = ch.exercise_name_ru || ch.exercise_name_en
      ? T.exName({ name_ru: ch.exercise_name_ru, name_en: ch.exercise_name_en })
      : "";
    var value = ch.value;
    if (ch.type === "weight_pct") {
      return (name ? name + ": " : "") +
        pick("вес ", "weight ") + (value > 0 ? "+" : "") + T.fmtNum(value) + "%";
    }
    if (ch.type === "sets") {
      return (name ? name + ": " : "") +
        (value > 0 ? "+" : "") + T.fmtNum(value) + " " + setWord(Math.abs(Number(value) || 0));
    }
    if (ch.type === "swap") {
      var newName = T.exName({ name_ru: ch.new_exercise_name_ru, name_en: ch.new_exercise_name_en });
      // Без стрелки-пиктограммы: текст уходит в esc(), и иконку туда не вставить.
      return (name || "?") + ": " + pick("заменить на ", "swap for ") + (newName || ch.new_slug || "?");
    }
    if (ch.type === "rest_sec") {
      return (name ? name + ": " : "") + pick("отдых ", "rest ") + T.fmtNum(value) + " " + pick("с", "s");
    }
    if (ch.type === "deload_next_week") {
      return pick("Разгрузочная неделя", "Deload week");
    }
    return (name ? name + ": " : "") + T.label("changeKind", ch.type);
  }

  /** Секция разбора со списком строк. */
  function reviewSectionHtml(title, list) {
    if (!list || !list.length) return "";
    var items = "";
    for (var i = 0; i < list.length; i++) items += "<li>" + esc(list[i]) + "</li>";
    return (
      '<div class="tr-review-card__section">' +
      '<div class="tr-review-card__subtitle">' + esc(title) + "</div>" +
      '<ul class="tr-review-card__list">' + items + "</ul>" +
      "</div>"
    );
  }

  /** Карточка разбора: пустая, с результатом или с ошибкой. */
  function reviewHtml() {
    var head =
      '<h3 class="tr-section-title">' + esc(pick("Недельный разбор", "Weekly review")) + "</h3>";

    if (state.reviewBusy) {
      return (
        '<section class="card tr-review-card" id="trReviewCard">' + head +
        '<p class="tr-muted">' + esc(pick("Тренер разбирает неделю…", "The coach is reviewing your week…")) + "</p>" +
        '<div class="skeleton skeleton-line"></div>' +
        '<div class="skeleton skeleton-line short"></div>' +
        "</section>"
      );
    }

    var r = state.review;
    if (!r) {
      return (
        '<section class="card tr-review-card" id="trReviewCard">' + head +
        '<p class="tr-muted">' +
        esc(pick(
          "Тренер посмотрит план против факта, объём по мышцам, ккал и белок — и предложит правки на следующую неделю.",
          "The coach compares plan vs actual, muscle volume, calories and protein — and suggests changes for next week."
        )) +
        "</p>" +
        '<button type="button" class="btn btn-cta btn-block" id="trReviewRun">' +
        esc(pick("Разобрать неделю", "Review my week")) +
        "</button>" +
        "</section>"
      );
    }

    var body = r.review || {};
    var changes = body.changes || [];
    var changesHtml = "";
    if (changes.length) {
      var rows = "";
      var pending = 0;
      for (var i = 0; i < changes.length; i++) {
        var ch = changes[i];
        if (!ch.applied) pending++;
        rows +=
          '<label class="tr-review-card__change' + (ch.applied ? " is-applied" : "") + '">' +
          '<input type="checkbox" class="tr-review-card__check" data-change="' + esc(ch.id) + '"' +
          (ch.applied ? " checked disabled" : " checked") + ">" +
          '<span class="tr-review-card__change-body">' +
          '<span class="tr-review-card__change-title">' + esc(changeText(ch)) + "</span>" +
          (ch.reason ? '<span class="tr-review-card__change-why">' + esc(ch.reason) + "</span>" : "") +
          (ch.applied
            ? '<span class="tr-review-card__badge">' + esc(pick("применено", "applied")) + "</span>"
            : "") +
          "</span>" +
          "</label>";
      }
      changesHtml =
        '<div class="tr-review-card__section">' +
        '<div class="tr-review-card__subtitle">' + esc(pick("Изменения на следующую неделю", "Changes for next week")) + "</div>" +
        rows +
        // Когда применены все правки — кнопка не нужна, показываем итог.
        (pending
          ? '<button type="button" class="btn btn-cta btn-block" id="trReviewApply">' +
            esc(pick("Применить к следующей неделе", "Apply to next week")) +
            "</button>"
          : '<p class="tr-muted tr-review-card__done">' +
            esc(pick(
              "Все правки применены к следующей неделе.",
              "All changes are applied to next week."
            )) +
            "</p>") +
        "</div>";
    }

    var period = "";
    if (r.week_start) {
      period = T.shortDate(r.week_start, false) +
        (r.week_end ? " — " + T.shortDate(r.week_end, false) : "");
    }

    return (
      '<section class="card tr-review-card" id="trReviewCard">' + head +
      (period ? '<p class="tr-review-card__period">' + esc(period) + "</p>" : "") +
      (body.summary ? '<p class="tr-review-card__summary">' + esc(body.summary) + "</p>" : "") +
      reviewSectionHtml(pick("Получилось", "Wins"), body.wins) +
      reviewSectionHtml(pick("Что менять", "What to change"), body.issues) +
      reviewSectionHtml(pick("Питание", "Nutrition"), body.nutrition) +
      changesHtml +
      (body.next_week_focus
        ? '<p class="tr-review-card__focus">' +
          esc(pick("Фокус недели: ", "Focus next week: ") + body.next_week_focus) + "</p>"
        : "") +
      (body.motivation ? '<p class="tr-review-card__motivation">' + esc(body.motivation) + "</p>" : "") +
      (r.disclaimer ? '<p class="rec-disclaimer">' + esc(r.disclaimer) + "</p>" : "") +
      '<button type="button" class="btn btn-ghost btn-block" id="trReviewRun">' +
      esc(pick("Разобрать заново", "Re-run the review")) +
      "</button>" +
      "</section>"
    );
  }

  /* =====================================================================
   *  РЕНДЕР
   * ===================================================================== */

  function renderProgress(d) {
    var body = byId("trProgBody");
    if (!body) return;
    state.data = d;
    body.innerHTML = summaryHtml(d) + muscleBarsHtml(d) + chartHtml(d) + recordsHtml(d);
    bindProgress();
  }

  function renderChart() {
    var card = byId("trProgChart");
    if (!card || !state.data) return;
    var host = document.createElement("div");
    host.innerHTML = chartHtml(state.data);
    var fresh = host.firstChild;
    card.parentNode.replaceChild(fresh, card);
    bindChart();
  }

  function renderHistory(data) {
    var host = byId("trProgHistory");
    if (!host) return;
    state.sessions = data;
    host.innerHTML = historyHtml(data);
    var wrap = host.querySelector(".tr-history");
    if (wrap) wrap.addEventListener("click", onHistoryClick);
    // Перерисовка из кэша (возврат на вкладку) раскрывает ту же тренировку,
    // что была открыта: детали уже лежат в sessionCache, сеть не нужна.
    var openId = state.openSession;
    var cached = openId != null ? state.sessionCache[openId] : null;
    var item = cached && wrap ? wrap.querySelector('[data-session="' + openId + '"]') : null;
    var bodyEl = item ? item.querySelector(".tr-history-item__body") : null;
    if (bodyEl) {
      item.classList.add("is-open");
      bodyEl.hidden = false;
      bodyEl.innerHTML = sessionDetailHtml(cached);
    } else {
      state.openSession = null;
    }
  }

  function renderReview() {
    var host = byId("trProgReview");
    if (!host) return;
    host.innerHTML = reviewHtml();
    var run = byId("trReviewRun");
    if (run) run.addEventListener("click", onReviewRun);
    var apply = byId("trReviewApply");
    if (apply) apply.addEventListener("click", onReviewApply);
  }

  function bindProgress() {
    bindChart();
    var body = byId("trProgBody");
    if (!body) return;
    var list = body.querySelector(".tr-record-list");
    if (list) {
      list.addEventListener("click", function (ev) {
        var row = ev.target.closest ? ev.target.closest("[data-ex-id]") : null;
        if (!row) return;
        var id = parseInt(row.getAttribute("data-ex-id"), 10);
        if (isNaN(id)) return;
        App.haptic("light");
        T.openExercise(id, { page: "trainer", state: { trainerSegment: "progress" } });
      });
    }
  }

  function bindChart() {
    var card = byId("trProgChart");
    if (!card) return;
    card.addEventListener("click", onChartClick);
  }

  function onChartClick(ev) {
    var el = ev.target.closest ? ev.target.closest(".chip") : null;
    if (!el) return;
    var exId = el.getAttribute("data-chart-ex");
    var metric = el.getAttribute("data-chart-metric");
    var period = el.getAttribute("data-chart-period");
    App.haptic("selection");
    if (metric) {
      if (metric === state.metric) return;
      state.metric = metric;
      renderChart();
      return;
    }
    if (period) {
      if (period === state.period) return;
      state.period = period;
      loadChart();
      return;
    }
    if (exId) {
      var id = parseInt(exId, 10);
      if (isNaN(id) || id === state.exerciseId) return;
      state.exerciseId = id;
      loadChart();
    }
  }

  /* =====================================================================
   *  ИСТОРИЯ: РАСКРЫТИЕ КАРТОЧКИ
   * ===================================================================== */

  function onHistoryClick(ev) {
    var head = ev.target.closest ? ev.target.closest(".tr-history-item__head") : null;
    if (!head) return;
    var item = head.parentNode;
    var idRaw = item.getAttribute("data-session");
    var id = parseInt(idRaw, 10);
    if (isNaN(id)) return;
    var bodyEl = item.querySelector(".tr-history-item__body");
    if (!bodyEl) return;
    App.haptic("light");

    if (item.classList.contains("is-open")) {
      item.classList.remove("is-open");
      bodyEl.hidden = true;
      state.openSession = null;
      return;
    }
    item.classList.add("is-open");
    bodyEl.hidden = false;
    state.openSession = id;

    var cached = state.sessionCache[id];
    if (cached) {
      bodyEl.innerHTML = sessionDetailHtml(cached);
      return;
    }
    bodyEl.innerHTML = '<div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line short"></div>';
    App.api
      .trainerSession(id)
      .then(function (session) {
        state.sessionCache[id] = session;
        if (!document.body.contains(bodyEl)) return;
        bodyEl.innerHTML = sessionDetailHtml(session);
      })
      .catch(function (err) {
        if (!document.body.contains(bodyEl)) return;
        bodyEl.innerHTML =
          '<p class="tr-muted">' + esc(T.errMessage(err, pick("Не удалось загрузить детали", "Failed to load details"))) + "</p>";
      });
  }

  /* =====================================================================
   *  РАЗБОР: ЗАПУСК И ПРИМЕНЕНИЕ
   * ===================================================================== */

  function onReviewRun() {
    if (state.reviewBusy) return;
    state.reviewBusy = true;
    renderReview();
    App.haptic("medium");
    // date — локальная дата клиента: границы недели считаются по ней,
    // иначе у пользователя в другом часовом поясе неделя «уезжает».
    App.api
      .trainerWeeklyReview({ date: App.todayStr() })
      .then(function (review) {
        state.reviewBusy = false;
        state.review = review || null;
        state.reviewLoaded = true;
        renderReview();
        App.haptic("success");
        scrollToReview();
      })
      .catch(function (err) {
        state.reviewBusy = false;
        renderReview();
        var host = byId("trReviewCard");
        if (host) {
          var note = document.createElement("p");
          note.className = "tr-review-card__error";
          note.textContent = reviewErrorText(err);
          host.appendChild(note);
        }
        App.toast(reviewErrorText(err));
      });
  }

  /** Понятный текст ошибки разбора (409 — за неделю нет тренировок). */
  function reviewErrorText(err) {
    if (err && err.status === 409) {
      return err.message ||
        pick("За эту неделю нет завершённых тренировок", "No finished workouts this week");
    }
    return T.errMessage(err, pick("Не удалось разобрать неделю", "Failed to review the week"));
  }

  function onReviewApply() {
    if (state.reviewBusy || !state.review) return;
    var host = byId("trReviewCard");
    if (!host) return;
    var boxes = host.querySelectorAll(".tr-review-card__check");
    var ids = [];
    for (var i = 0; i < boxes.length; i++) {
      if (boxes[i].disabled || !boxes[i].checked) continue;
      var id = parseInt(boxes[i].getAttribute("data-change"), 10);
      if (!isNaN(id)) ids.push(id);
    }
    if (!ids.length) {
      App.toast(pick("Отметьте, что применить", "Pick the changes to apply"));
      return;
    }
    state.reviewBusy = true;
    var btn = byId("trReviewApply");
    if (btn) btn.disabled = true;
    App.haptic("medium");
    App.api
      .trainerApplyReview(state.review.id, ids)
      .then(function (res) {
        var lines = (res && res.lines) || [];
        App.haptic("success");
        App.toast(
          lines.length
            ? lines[0]
            : pick("Правки применены", "Changes applied")
        );
        T.cache.invalidate();
        // Перечитываем разбор: у применённых правок появится applied=true.
        return App.api.trainerLatestReview();
      })
      .then(function (review) {
        state.reviewBusy = false;
        state.review = review || state.review;
        renderReview();
      })
      .catch(function (err) {
        state.reviewBusy = false;
        if (btn) btn.disabled = false;
        App.toast(T.errMessage(err, pick("Не удалось применить правки", "Failed to apply the changes")));
      });
  }

  /**
   * Прокручивает страницу к карточке разбора. Над карточкой остаётся место
   * под липкую полосу вкладок — отступ задан в CSS (scroll-margin-top).
   */
  function scrollToReview() {
    var card = byId("trReviewCard");
    if (!card || card.offsetParent === null || typeof card.scrollIntoView !== "function") return;
    try {
      card.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      card.scrollIntoView();
    }
  }

  /* =====================================================================
   *  ЗАГРУЗКА
   * ===================================================================== */

  function renderError(err) {
    var body = byId("trProgBody");
    if (!body) return;
    body.innerHTML = T.errorCard(T.errMessage(err), "trProgRetry");
    var btn = byId("trProgRetry");
    if (btn) {
      btn.addEventListener("click", function () {
        App.haptic("light");
        load();
      });
    }
  }

  /** Основная загрузка: прогресс, затем история и последний разбор. */
  function load() {
    var body = byId("trProgBody");
    if (!body || state.loading) return;
    state.loading = true;
    var reqId = ++state.reqId;
    body.innerHTML = T.skeleton(3) + T.skeleton(4);

    App.api
      .trainerProgress({ exercise_id: state.exerciseId, period: state.period })
      .then(function (d) {
        state.loading = false;
        if (reqId !== state.reqId || !byId("trProgBody")) return;
        d = d || {};
        if (d.chart && d.chart.exercise_id != null) state.exerciseId = d.chart.exercise_id;
        if (d.period) state.period = d.period;
        renderProgress(d);
        loadHistory();
        loadReview();
      })
      .catch(function (err) {
        state.loading = false;
        if (reqId !== state.reqId || !byId("trProgBody")) return;
        if (err && err.status === 402) {
          showPaywall();
          return;
        }
        renderError(err);
      });
  }

  /** Перезапрос только графика (смена упражнения или периода). */
  function loadChart() {
    if (state.chartLoading) return;
    state.chartLoading = true;
    renderChart();
    var reqId = ++state.reqId;
    App.api
      .trainerProgress({ exercise_id: state.exerciseId, period: state.period })
      .then(function (d) {
        state.chartLoading = false;
        if (reqId !== state.reqId || !byId("trProgChart")) return;
        d = d || {};
        if (d.chart && d.chart.exercise_id != null) state.exerciseId = d.chart.exercise_id;
        state.data = d;
        renderChart();
      })
      .catch(function (err) {
        state.chartLoading = false;
        if (!byId("trProgChart")) return;
        renderChart();
        App.toast(T.errMessage(err, pick("Не удалось обновить график", "Failed to update the chart")));
      });
  }

  function loadHistory() {
    var host = byId("trProgHistory");
    if (!host) return;
    host.innerHTML = T.skeleton(3);
    App.api
      .trainerSessions(20, 0)
      .then(function (data) {
        if (!byId("trProgHistory")) return;
        renderHistory(data || { items: [], total: 0 });
      })
      .catch(function (err) {
        var h = byId("trProgHistory");
        if (!h) return;
        h.innerHTML =
          '<section class="card">' +
          '<h3 class="tr-section-title">' + esc(pick("История", "History")) + "</h3>" +
          '<p class="tr-muted">' + esc(T.errMessage(err)) + "</p>" +
          "</section>";
      });
  }

  function loadReview() {
    var host = byId("trProgReview");
    if (!host) return;
    host.innerHTML = T.skeleton(2);
    App.api
      .trainerLatestReview()
      .then(function (review) {
        if (!byId("trProgReview")) return;
        state.review = review || null;
        state.reviewLoaded = true;
        renderReview();
        consumeScrollToReview();
      })
      .catch(function () {
        if (!byId("trProgReview")) return;
        // Разбора может не быть — показываем карточку с кнопкой запуска.
        state.review = null;
        state.reviewLoaded = true;
        renderReview();
        consumeScrollToReview();
      });
  }

  /** Отложенная прокрутка к разбору (вход по баннеру «Разбор готов»). */
  function consumeScrollToReview() {
    if (!state.scrollToReview) return;
    state.scrollToReview = false;
    scrollToReview();
  }

  /* =====================================================================
   *  МОНТИРОВАНИЕ ВКЛАДКИ
   * ===================================================================== */

  /**
   * Рисует прогресс в панели оболочки раздела.
   * @param {HTMLElement} el панель
   * @param {object} [opts] {keep: нарисовать из кэша (возврат из карточки
   *        упражнения), onPaywall()}
   */
  function mount(el, opts) {
    if (!el) return;
    opts = opts || {};
    state.host = el;
    state.opts = opts;
    state.reqId++;
    state.loading = false;
    state.chartLoading = false;
    state.reviewBusy = false;
    state.scrollToReview = App.state.trainerProgressSection === "review";
    App.state.trainerProgressSection = null;
    el.innerHTML = shellHtml();

    if (opts.keep && state.data) {
      renderProgress(state.data);
      if (state.sessions) renderHistory(state.sessions);
      else loadHistory();
      if (state.reviewLoaded) {
        renderReview();
        consumeScrollToReview();
      } else {
        loadReview();
      }
      return;
    }

    // Свежий заход: после тренировки и сводка, и история уже другие.
    state.data = null;
    state.sessions = null;
    state.sessionCache = {};
    state.openSession = null;
    state.review = null;
    state.reviewLoaded = false;
    state.exerciseId = null;
    state.period = "4w";
    state.metric = "est_1rm";
    load();
  }

  /** Снимает вкладку: ответы в полёте больше не рисуются. Кэш данных остаётся. */
  function unmount() {
    state.host = null;
    state.opts = null;
    state.loading = false;
    state.chartLoading = false;
    state.reviewBusy = false;
    state.scrollToReview = false;
    state.reqId++;
  }

  /**
   * Прокрутить к недельному разбору уже смонтированной вкладки. Если разбор
   * ещё грузится — прокрутка случится, когда он дорисуется.
   */
  function focusReview() {
    state.scrollToReview = true;
    if (state.reviewLoaded && byId("trReviewCard")) consumeScrollToReview();
  }

  window.TrainerProgress = { mount: mount, unmount: unmount, focusReview: focusReview };

  /**
   * Переадресация со старого имени страницы на вкладку раздела. Обработчики
   * в других разделах могли сохранить App.navigate("trainer-progress").
   */
  App.registerPage("trainer-progress", {
    onShow: function () {
      App.state.trainerSegment = "progress";
      App.navigate("trainer");
    }
  });
})();
