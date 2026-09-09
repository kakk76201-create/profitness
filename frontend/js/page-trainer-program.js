/*
 * page-trainer-program.js — страница «Программа» раздела «AI-тренер»
 * (страница "trainer-program", ТЗ §2.3, §6.2 блок «Сегодня/программа»).
 *
 * Регистрирует контроллер через App.registerPage("trainer-program", {...}).
 * Публичная ссылка — window.PageTrainerProgram.
 *
 * Два режима одной страницы:
 *   • preview (App.state.trainerProgramMode === "preview") — сразу после
 *     генерации: шапка с названием и «Почему так», лента недель с фазами,
 *     раскрывающиеся дни, кнопки «Начать программу» и «Пересобрать»;
 *   • обычный — та же страница как «Программа»: прогресс-бар текущей недели,
 *     статусы дней (✓ / пропущено / план), «Архивировать и создать новую».
 *
 * Источник данных: App.state.trainerProgram (положила страница, вызвавшая
 * Trainer.openProgram) либо GET /trainer/program. 404 → «Программа не создана».
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

  // Внутреннее состояние контроллера.
  var state = {
    viewEl: null,     // корневой элемент (#view)
    program: null,    // последний TrainerProgramOut
    mode: null,       // "preview" | null
    week: 1,          // выбранная неделя (лента недель)
    loading: false,   // идёт загрузка программы
    busy: false       // идёт генерация/архивация (защита от двойного тапа)
  };

  /* =====================================================================
   *  ВСПОМОГАТЕЛЬНОЕ
   * ===================================================================== */

  /** Ограничивает номер недели диапазоном 1..weeks. */
  function clampWeek(week, weeks) {
    var w = parseInt(week, 10);
    if (isNaN(w) || w < 1) w = 1;
    var max = parseInt(weeks, 10);
    if (!isNaN(max) && max > 0 && w > max) w = max;
    return w;
  }

  /** Склонение «неделя/недели/недель». */
  function weekWord(n) {
    if (App.lang === "en") return n === 1 ? "week" : "weeks";
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return "неделя";
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "недели";
    return "недель";
  }

  /** Склонение «тренировка/тренировки/тренировок» (дней в неделю). */
  function dayWord(n) {
    if (App.lang === "en") return n === 1 ? "day" : "days";
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return "день";
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "дня";
    return "дней";
  }

  /** Склонение «упражнение/упражнения/упражнений». */
  function exWord(n) {
    if (App.lang === "en") return n === 1 ? "exercise" : "exercises";
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return "упражнение";
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "упражнения";
    return "упражнений";
  }

  /** Дни выбранной недели, отсортированные по day_index. */
  function daysOfWeek(program, week) {
    var out = [];
    var days = (program && program.days) || [];
    for (var i = 0; i < days.length; i++) {
      if (Number(days[i].week) === Number(week)) out.push(days[i]);
    }
    out.sort(function (a, b) {
      return (a.day_index || 0) - (b.day_index || 0);
    });
    return out;
  }

  /** Фаза недели из periodization (для чипа и подписи). */
  function phaseOf(program, week) {
    var list = (program && program.periodization) || [];
    for (var i = 0; i < list.length; i++) {
      if (Number(list[i].week) === Number(week)) return list[i];
    }
    return null;
  }

  /** Подпись фазы: label от сервера либо словарь Trainer.L.phase. */
  function phaseLabel(phase) {
    if (!phase) return "";
    return phase.label || T.label("phase", phase.phase);
  }

  /* =====================================================================
   *  РАЗМЕТКА
   * ===================================================================== */

  /** Каркас страницы: шапка + контейнер тела (#trPgmBody). */
  function shellHtml() {
    return (
      '<section class="page sub-page tr-page tr-program">' +
      T.headHtml({ icon: "📋", title: pick("Программа", "Program"), subtitle: "" }) +
      '<div id="trPgmBody"></div>' +
      "</section>"
    );
  }

  /** Обновляет подзаголовок шапки без перерисовки страницы. */
  function setSubtitle(text) {
    if (!state.viewEl) return;
    var el = state.viewEl.querySelector(".sub-subtitle");
    if (el) el.textContent = text || "";
  }

  /**
   * Шапка программы: название, сплит/недели/дни, «Почему так», советы,
   * а в обычном режиме — прогресс-бар текущей недели.
   */
  function headCardHtml(p) {
    var meta = [];
    if (p.split_type) meta.push(T.label("split", p.split_type));
    if (p.weeks) meta.push(p.weeks + " " + weekWord(p.weeks));
    if (p.days_per_week) {
      meta.push(p.days_per_week + " " + dayWord(p.days_per_week) + pick(" в неделю", "/week"));
    }
    if (p.start_date) {
      var range = T.shortDate(p.start_date, false);
      if (p.end_date) range += " — " + T.shortDate(p.end_date, false);
      meta.push(range);
    }

    var progress = "";
    if (state.mode !== "preview" && p.weeks) {
      var cur = clampWeek(p.current_week || 1, p.weeks);
      var pct = Math.min(100, Math.round((cur / p.weeks) * 100));
      var done = 0, total = 0;
      var days = p.days || [];
      for (var i = 0; i < days.length; i++) {
        if (Number(days[i].week) !== cur) continue;
        total++;
        if (days[i].status === "done") done++;
      }
      progress =
        '<div class="tr-program-progress">' +
        '<div class="tr-program-progress__row">' +
        "<span>" + esc(pick("Неделя ", "Week ") + cur + pick(" из ", " of ") + p.weeks) + "</span>" +
        (total ? "<span>" + esc(done + pick(" из ", " of ") + total + pick(" сделано", " done")) + "</span>" : "") +
        "</div>" +
        '<div class="tr-streak__bar"><div class="tr-streak__fill" style="width:' + pct + '%"></div></div>' +
        "</div>";
    }

    var tips = "";
    var list = p.tips || [];
    if (list.length) {
      var items = "";
      for (var j = 0; j < list.length; j++) {
        items += '<li class="tr-program-tips__item">' + esc(list[j]) + "</li>";
      }
      tips =
        '<div class="tr-program-tips">' +
        '<div class="tr-program-tips__title">' + esc(pick("Советы тренера", "Coach tips")) + "</div>" +
        '<ul class="tr-program-tips__list">' + items + "</ul>" +
        "</div>";
    }

    return (
      '<section class="card tr-program-head">' +
      '<h2 class="tr-program-head__title">' + esc(p.title || pick("Программа", "Program")) + "</h2>" +
      (meta.length ? '<p class="tr-program-head__meta">' + esc(meta.join(" · ")) + "</p>" : "") +
      progress +
      (p.summary ? '<p class="tr-program-head__summary">' + esc(p.summary) + "</p>" : "") +
      tips +
      "</section>"
    );
  }

  /**
   * Лента недель с фазами (База · Рост · Пик · Разгрузка). Тап — выбор недели.
   */
  function weeksStripHtml(p) {
    var weeks = parseInt(p.weeks, 10) || 0;
    if (weeks <= 0) return "";
    var cur = clampWeek(p.current_week || 1, weeks);
    var html = "";
    for (var w = 1; w <= weeks; w++) {
      var phase = phaseOf(p, w);
      var code = (phase && phase.phase) || "base";
      var cls = "tr-week-item";
      if (w === state.week) cls += " is-active";
      if (state.mode !== "preview" && w === cur) cls += " tr-week-item--current";
      html +=
        '<button type="button" class="' + cls + '" data-week="' + w + '">' +
        '<span class="tr-week-item__num">' + esc(pick("Нед. ", "W") + w) + "</span>" +
        '<span class="tr-phase-chip tr-phase-chip--' + esc(code) + '">' + esc(phaseLabel(phase)) + "</span>" +
        "</button>";
    }
    return (
      '<section class="card tr-program-weeks">' +
      '<h3 class="tr-section-title">' + esc(pick("Недели", "Weeks")) + "</h3>" +
      '<div class="tr-week-strip tr-week-strip--weeks">' + html + "</div>" +
      "</section>"
    );
  }

  /** Одна строка плана (упражнение разминки / основной части / заминки). */
  function planItemHtml(item, aux) {
    var target = aux ? shortAux(item) : T.fmtTarget(item);
    var note = item.note ? '<span class="tr-plan-item__note">' + esc(item.note) + "</span>" : "";
    var idAttr = item.exercise_id != null ? ' data-ex-id="' + esc(item.exercise_id) + '"' : "";
    return (
      '<button type="button" class="tr-plan-item' + (aux ? " tr-plan-item--aux" : "") + '"' + idAttr + ">" +
      '<span class="tr-plan-item__name">' + esc(T.exName(item)) + "</span>" +
      (target ? '<span class="tr-plan-item__meta">' + esc(target) + "</span>" : "") +
      note +
      "</button>"
    );
  }

  /** Короткая цель для разминки/заминки: «5 мин» / «1×15». */
  function shortAux(item) {
    if (item.time_sec != null) {
      var sec = Number(item.time_sec) || 0;
      if (sec >= 60 && sec % 60 === 0) return sec / 60 + " " + pick("мин", "min");
      return T.fmtNum(sec) + " " + pick("с", "s");
    }
    var sets = item.sets != null ? item.sets : 1;
    var reps = item.reps_max != null ? item.reps_max : item.reps_min;
    if (reps != null) return sets + "×" + reps;
    return "";
  }

  /** Основная часть дня (без модификатора --aux). */
  function mainBlockHtml(exs) {
    var rows = "";
    for (var i = 0; i < exs.length; i++) rows += planItemHtml(exs[i], false);
    if (!rows) {
      rows = '<p class="tr-plan-block__empty">' + esc(pick("Упражнений нет", "No exercises")) + "</p>";
    }
    return (
      '<div class="tr-plan-block">' +
      '<div class="tr-plan-block__title">' + esc(pick("Основная часть", "Main block")) + "</div>" +
      rows +
      "</div>"
    );
  }

  /** Блок разминки/заминки внутри дня. */
  function planBlockHtml(title, items) {
    if (!items || !items.length) return "";
    var rows = "";
    for (var i = 0; i < items.length; i++) rows += planItemHtml(items[i], true);
    return (
      '<div class="tr-plan-block">' +
      '<div class="tr-plan-block__title">' + esc(title) + "</div>" +
      rows +
      "</div>"
    );
  }

  /** День программы — карточка-аккордеон (.acc-fold + .tr-plan-day). */
  function dayHtml(day, index) {
    var exs = day.exercises || [];
    var meta = [];
    if (day.duration_min) meta.push(day.duration_min + " " + pick("мин", "min"));
    if (exs.length) meta.push(exs.length + " " + exWord(exs.length));
    var muscles = day.focus_muscles || [];
    if (muscles.length) meta.push(T.labels("muscle", muscles));

    var cls = "card acc-fold tr-plan-day";
    var mark = "";
    if (day.status === "done") {
      cls += " tr-plan-day--done";
      mark = "✓ ";
    } else if (day.status === "skipped") {
      cls += " tr-plan-day--skipped";
      mark = "× ";
    }

    var when = "";
    if (day.scheduled_date) when = T.shortDate(day.scheduled_date);
    else if (day.weekday != null) when = T.label("weekday", day.weekday);

    var title =
      mark + pick("День ", "Day ") + (day.day_index || index + 1) +
      (day.title ? " — " + day.title : "");

    return (
      '<section class="' + cls + '" data-day-index="' + index + '">' +
      '<button type="button" class="acc-fold__head" data-fold>' +
      '<span class="acc-fold__title">' + esc(title) +
      '<span class="tr-plan-day__meta">' + esc([when].concat(meta).filter(Boolean).join(" · ")) + "</span>" +
      "</span>" +
      '<span class="acc-fold__chevron" aria-hidden="true">▾</span>' +
      "</button>" +
      '<div class="acc-fold__body" hidden>' +
      planBlockHtml(pick("Разминка", "Warm-up"), day.warmup) +
      mainBlockHtml(exs) +
      planBlockHtml(pick("Заминка", "Cool-down"), day.cooldown) +
      "</div>" +
      "</section>"
    );
  }

  /** Список дней выбранной недели. */
  function daysHtml(p) {
    var days = daysOfWeek(p, state.week);
    if (!days.length) {
      return (
        '<section class="card wk-empty">' +
        '<p class="wk-empty__text">' + esc(pick("На эту неделю дней нет", "No days for this week")) + "</p>" +
        "</section>"
      );
    }
    var html = "";
    for (var i = 0; i < days.length; i++) html += dayHtml(days[i], i);
    return '<div id="trPgmDays">' + html + "</div>";
  }

  /** Кнопки внизу страницы — разные для preview и обычного режима. */
  function actionsHtml() {
    if (state.mode === "preview") {
      return (
        '<div class="tr-program-actions">' +
        '<button type="button" class="btn btn-cta btn-block" id="trPgmStart">' +
        esc(pick("Начать программу", "Start the program")) +
        "</button>" +
        '<button type="button" class="btn btn-ghost btn-block" id="trPgmRegen">' +
        esc(pick("Пересобрать", "Rebuild")) +
        "</button>" +
        "</div>"
      );
    }
    return (
      '<div class="tr-program-actions">' +
      '<button type="button" class="btn btn-cta btn-block" id="trPgmRegen">' +
      esc(pick("Архивировать и создать новую", "Archive and build a new one")) +
      "</button>" +
      '<button type="button" class="btn btn-ghost btn-block" id="trPgmArchive">' +
      esc(pick("Архивировать программу", "Archive the program")) +
      "</button>" +
      "</div>"
    );
  }

  /** Экран «Программа не создана» (404 от сервера). */
  function emptyHtml() {
    return (
      '<section class="card wk-empty tr-empty">' +
      '<div class="wk-empty__icon" aria-hidden="true">📋</div>' +
      '<p class="wk-empty__title">' + esc(pick("Программа не создана", "No program yet")) + "</p>" +
      '<p class="wk-empty__text">' +
      esc(pick(
        "Соберём программу под вашу цель, уровень и оборудование — это займёт до минуты.",
        "Let’s build a program for your goal, level and equipment — it takes under a minute."
      )) +
      "</p>" +
      '<button type="button" class="btn btn-cta btn-block" id="trPgmGenerate">' +
      esc(pick("Собрать программу", "Build my program")) +
      "</button>" +
      "</section>"
    );
  }

  /* =====================================================================
   *  РЕНДЕР И СОБЫТИЯ
   * ===================================================================== */

  function renderProgram(p) {
    var body = byId("trPgmBody");
    if (!body) return;
    state.program = p;
    state.week = clampWeek(state.week || p.current_week || 1, p.weeks);
    setSubtitle(
      state.mode === "preview"
        ? pick("Новая программа — посмотрите план", "New program — review the plan")
        // Название программы уже стоит заголовком карточки — не дублируем его.
        : pick("Ваш план тренировок", "Your training plan")
    );
    body.innerHTML =
      headCardHtml(p) +
      weeksStripHtml(p) +
      '<h3 class="tr-section-title">' +
      esc(pick("Дни недели ", "Week ") + state.week) +
      "</h3>" +
      '<div id="trPgmDaysWrap">' + daysHtml(p) + "</div>" +
      actionsHtml();
    bindProgram();
    // В обычном режиме сразу раскрываем ближайший невыполненный день —
    // чаще всего пользователь заходит именно за ним.
    autoOpenDay(p);
  }

  /** Раскрывает первый день недели со статусом planned (кроме preview). */
  function autoOpenDay(p) {
    if (state.mode === "preview") return;
    var days = daysOfWeek(p, state.week);
    for (var i = 0; i < days.length; i++) {
      if (days[i].status === "planned") {
        toggleDay(i, true);
        return;
      }
    }
  }

  /** Раскрывает/схлопывает день по индексу в списке текущей недели. */
  function toggleDay(index, forceOpen) {
    var wrap = byId("trPgmDaysWrap");
    if (!wrap) return;
    var card = wrap.querySelector('[data-day-index="' + index + '"]');
    if (!card) return;
    var bodyEl = card.querySelector(".acc-fold__body");
    if (!bodyEl) return;
    var open = forceOpen === true ? true : !card.classList.contains("acc-fold--open");
    if (open) {
      card.classList.add("acc-fold--open");
      bodyEl.hidden = false;
    } else {
      card.classList.remove("acc-fold--open");
      bodyEl.hidden = true;
    }
  }

  function bindProgram() {
    var wrap = byId("trPgmBody");
    if (!wrap) return;

    // Выбор недели.
    var weekBtns = wrap.querySelectorAll(".tr-week-item");
    for (var i = 0; i < weekBtns.length; i++) {
      weekBtns[i].addEventListener("click", onWeekClick);
    }

    // Аккордеон дней + переход к упражнению по тапу на строку плана.
    var days = byId("trPgmDaysWrap");
    if (days) days.addEventListener("click", onDaysClick);

    var start = byId("trPgmStart");
    if (start) {
      start.addEventListener("click", function () {
        App.haptic("success");
        App.state.trainerProgramMode = null;
        state.mode = null;
        T.cache.invalidate();
        T.go("trainer");
      });
    }
    var regen = byId("trPgmRegen");
    if (regen) regen.addEventListener("click", onRegenerate);
    var archive = byId("trPgmArchive");
    if (archive) archive.addEventListener("click", onArchive);
  }

  function onWeekClick(ev) {
    var raw = ev.currentTarget.getAttribute("data-week");
    var week = clampWeek(raw, state.program ? state.program.weeks : 0);
    if (week === state.week) return;
    App.haptic("selection");
    state.week = week;
    renderProgram(state.program);
    App.scrollTop();
  }

  function onDaysClick(ev) {
    var exBtn = ev.target.closest ? ev.target.closest("[data-ex-id]") : null;
    if (exBtn) {
      var exId = parseInt(exBtn.getAttribute("data-ex-id"), 10);
      if (!isNaN(exId)) {
        App.haptic("light");
        App.state.trainerExerciseId = exId;
        T.go("trainer-exercise");
      }
      return;
    }
    var head = ev.target.closest ? ev.target.closest("[data-fold]") : null;
    if (!head) return;
    var card = head.closest(".tr-plan-day");
    if (!card) return;
    App.haptic("light");
    toggleDay(parseInt(card.getAttribute("data-day-index"), 10));
  }

  /* =====================================================================
   *  ДЕЙСТВИЯ (генерация, архивация)
   * ===================================================================== */

  /** Пересборка программы (heavy-лимит: 2/мин, 30/сутки). */
  function onRegenerate() {
    if (state.busy) return;
    var question = state.mode === "preview"
      ? pick("Пересобрать программу заново?", "Rebuild the program from scratch?")
      : pick(
          "Текущая программа уйдёт в архив, история тренировок сохранится. Создать новую?",
          "The current program will be archived, your workout history stays. Build a new one?"
        );
    T.confirm(question).then(function (ok) {
      if (!ok) return;
      generate();
    });
  }

  function generate() {
    var body = byId("trPgmBody");
    if (!body || state.busy) return;
    state.busy = true;
    App.haptic("medium");
    T.runGenerate(body, {})
      .then(function (program) {
        state.busy = false;
        if (!byId("trPgmBody")) return;
        App.haptic("success");
        state.mode = "preview";
        App.state.trainerProgramMode = "preview";
        state.week = program && program.current_week ? program.current_week : 1;
        renderProgram(program || {});
        App.scrollTop();
      })
      .catch(function (err) {
        state.busy = false;
        if (!byId("trPgmBody")) return;
        renderGenerateError(err);
      });
  }

  /** Ошибка генерации: карточка с «Повторить» и возвратом к программе. */
  function renderGenerateError(err) {
    var body = byId("trPgmBody");
    if (!body) return;
    body.innerHTML =
      T.errorCard(
        T.errMessage(err, pick(
          "Не удалось собрать программу. Попробуйте ещё раз.",
          "Failed to build the program. Please try again."
        )),
        "trPgmGenRetry"
      ) +
      '<button type="button" class="btn btn-ghost btn-block" id="trPgmGenBack">' +
      esc(pick("Назад", "Back")) +
      "</button>";
    var retry = byId("trPgmGenRetry");
    if (retry) {
      retry.addEventListener("click", function () {
        App.haptic("light");
        generate();
      });
    }
    var back = byId("trPgmGenBack");
    if (back) {
      back.addEventListener("click", function () {
        App.haptic("light");
        if (state.program) renderProgram(state.program);
        else load();
      });
    }
  }

  /** Архивация без создания новой (страница «Сегодня» покажет пустое состояние). */
  function onArchive() {
    if (state.busy || !state.program) return;
    T.confirm(
      pick(
        "Архивировать программу? Тренировки останутся в истории.",
        "Archive the program? Your workouts stay in history."
      )
    ).then(function (ok) {
      if (!ok) return;
      state.busy = true;
      App.haptic("medium");
      App.api
        .trainerArchiveProgram(state.program.id)
        .then(function () {
          state.busy = false;
          T.cache.invalidate();
          App.state.trainerProgram = null;
          App.state.trainerProgramMode = null;
          App.toast(pick("Программа архивирована", "Program archived"));
          T.go("trainer");
        })
        .catch(function (err) {
          state.busy = false;
          App.toast(T.errMessage(err, pick("Не удалось архивировать", "Failed to archive")));
        });
    });
  }

  /* =====================================================================
   *  ЗАГРУЗКА
   * ===================================================================== */

  function renderEmpty() {
    var body = byId("trPgmBody");
    if (!body) return;
    setSubtitle("");
    body.innerHTML = emptyHtml();
    var gen = byId("trPgmGenerate");
    if (gen) {
      gen.addEventListener("click", function () {
        App.haptic("medium");
        generate();
      });
    }
  }

  function renderError(err) {
    var body = byId("trPgmBody");
    if (!body) return;
    body.innerHTML = T.errorCard(T.errMessage(err), "trPgmRetry");
    var btn = byId("trPgmRetry");
    if (btn) {
      btn.addEventListener("click", function () {
        App.haptic("light");
        load();
      });
    }
  }

  /** Загружает активную программу с сервера. */
  function load() {
    var body = byId("trPgmBody");
    if (!body || state.loading) return;
    state.loading = true;
    body.innerHTML = T.skeleton(3) + T.skeleton(4);
    App.api
      .trainerProgram()
      .then(function (program) {
        state.loading = false;
        if (!byId("trPgmBody")) return;
        renderProgram(program || {});
      })
      .catch(function (err) {
        state.loading = false;
        if (!byId("trPgmBody")) return;
        if (err && err.status === 402) {
          App.paywall(state.viewEl, T.paywallOpts());
          return;
        }
        if (err && err.status === 404) {
          renderEmpty();
          return;
        }
        renderError(err);
      });
  }

  /* =====================================================================
   *  КОНТРОЛЛЕР
   * ===================================================================== */

  var controller = {
    onShow: function (viewEl) {
      state.viewEl = viewEl;
      if (!App.requirePremium(viewEl, T.paywallOpts())) return;
      state.mode = App.state.trainerProgramMode === "preview" ? "preview" : null;
      state.busy = false;
      state.loading = false;
      viewEl.innerHTML = shellHtml();
      T.bindBack(viewEl);

      // Превью после генерации приходит на руки готовым объектом; в обычном
      // режиме всегда читаем свежую программу с сервера (статусы дней).
      var passed = App.state.trainerProgram;
      App.state.trainerProgram = null;
      if (state.mode === "preview" && passed) {
        state.week = passed.current_week || 1;
        renderProgram(passed);
        return;
      }
      state.week = 0;
      load();
    },

    onHide: function () {
      state.viewEl = null;
      state.program = null;
      state.loading = false;
      state.busy = false;
      T.closeSheet(true);
    }
  };

  window.PageTrainerProgram = controller;
  App.registerPage("trainer-program", controller);
})();
