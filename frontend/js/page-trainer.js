/*
 * page-trainer.js — главная раздела «AI-тренер» (страница "trainer").
 *
 * Регистрирует контроллер через App.registerPage("trainer", {...}).
 * Публичная ссылка — window.PageTrainer.
 *
 * Сценарий onShow (ТЗ §2.1, §2.4):
 *   requirePremium → GET /trainer/overview →
 *     • профиля нет / onboarding_completed=false → App.navigate("trainer-onboarding");
 *     • нет активной программы → экран «Программа не создана» + «Собрать программу»;
 *     • иначе — экран «Сегодня»: карточка дня (в процессе / по плану / отдых /
 *       неделя закрыта), лента недели, стрик, «Питание сегодня» (лениво),
 *       баннер разбора, быстрые ссылки.
 *
 * Побочный эффект: после overview заполняет App.state.trainerBrief — текст
 * карточки-входа на «Тренировках» (page-workouts.js → coachCardHtml()).
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
    viewEl: null,       // корневой элемент (#view)
    overview: null,     // последний TrainerOverviewOut
    loading: false,     // идёт загрузка overview (защита от гонок)
    starting: false,    // идёт старт сессии
    nutritionReq: 0     // счётчик запросов совета (игнорируем устаревшие ответы)
  };

  /* =====================================================================
   *  СВОДКА ДЛЯ КАРТОЧКИ-ВХОДА НА «ТРЕНИРОВКАХ»
   * ===================================================================== */

  /**
   * Заполняет App.state.trainerBrief по overview (или по факту отсутствия
   * профиля). Текст собирает coachCardHtml() в page-workouts.js.
   */
  function setBrief(ov) {
    var hasProfile = !!(ov && ov.profile && ov.profile.onboarding_completed);
    var today = ov && ov.today;
    var brief = {
      has_profile: hasProfile,
      kind: hasProfile ? ((today && today.kind) || (ov.program ? "planned" : "no_program")) : "no_profile",
      title: today && today.day ? today.day.title || "" : "",
      duration_min: today && today.day ? today.day.duration_min || null : null,
      in_progress: !!(ov && ov.active_session_id)
    };
    App.state.trainerBrief = brief;
  }

  /* =====================================================================
   *  ВСПОМОГАТЕЛЬНОЕ
   * ===================================================================== */

  /**
   * Номер текущей недели программы: из brief-объекта, иначе по start_date.
   */
  function currentWeek(program) {
    if (!program) return null;
    if (program.current_week != null) return program.current_week;
    if (!program.start_date) return null;
    var start = program.start_date;
    var today = App.todayStr();
    var days = 0;
    var d = start;
    // Разница в днях без Date-арифметики через часовые пояса (максимум ~2 года).
    while (d < today && days < 800) {
      d = T.shiftDate(d, 1);
      days++;
    }
    var week = Math.floor(days / 7) + 1;
    if (program.weeks) week = Math.min(week, program.weeks);
    return Math.max(1, week);
  }

  /**
   * Подзаголовок шапки: «Неделя 2 из 6 · Верх/Низ».
   */
  function subtitleFor(ov) {
    var p = ov && ov.program;
    if (!p) return pick("Персональная программа", "Your personal program");
    var parts = [];
    var w = currentWeek(p);
    if (w && p.weeks) parts.push(pick("Неделя ", "Week ") + w + pick(" из ", " of ") + p.weeks);
    if (p.split_type) parts.push(T.label("split", p.split_type));
    return parts.join(" · ") || p.title || "";
  }

  /**
   * Короткая цель упражнения для превью: «3×8–12 · 14 кг».
   */
  function shortTarget(item) {
    if (!item) return "";
    var parts = [];
    var reps = "";
    if (item.reps_min != null && item.reps_max != null && item.reps_min !== item.reps_max) {
      reps = item.reps_min + "–" + item.reps_max;
    } else if (item.reps_max != null) {
      reps = String(item.reps_max);
    } else if (item.reps_min != null) {
      reps = String(item.reps_min);
    }
    if (item.time_sec != null && !reps) {
      parts.push((item.sets != null ? item.sets + "×" : "") + T.fmtNum(item.time_sec) + " " + pick("с", "s"));
    } else if (item.sets != null || reps) {
      parts.push((item.sets != null ? item.sets : "") + (item.sets != null && reps ? "×" : "") + reps);
    }
    if (item.target_weight_kg != null && Number(item.target_weight_kg) > 0) {
      parts.push(T.fmtKg(item.target_weight_kg));
    }
    return parts.join(" · ");
  }

  /**
   * Мета-строка дня: «45 мин · 6 упражнений · Грудь, Спина, Плечи».
   */
  function dayMeta(day) {
    if (!day) return "";
    var parts = [];
    if (day.duration_min) parts.push(day.duration_min + " " + pick("мин", "min"));
    var exs = day.exercises || [];
    if (exs.length) parts.push(exs.length + " " + exWord(exs.length));
    var muscles = day.focus_muscles || [];
    if (muscles.length) parts.push(T.labels("muscle", muscles));
    return parts.join(" · ");
  }

  /** Склонение «упражнение/упражнения/упражнений». */
  function exWord(n) {
    if (App.lang === "en") return n === 1 ? "exercise" : "exercises";
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return "упражнение";
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "упражнения";
    return "упражнений";
  }

  /** Склонение «неделя/недели/недель подряд». */
  function weekWord(n) {
    if (App.lang === "en") return n === 1 ? "week" : "weeks";
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return "неделя";
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "недели";
    return "недель";
  }

  /**
   * Прогресс активной сессии: {done, total, elapsed} по основным упражнениям.
   */
  function sessionProgress(session) {
    var done = 0, total = 0;
    var exs = (session && session.exercises) || [];
    for (var i = 0; i < exs.length; i++) {
      var e = exs[i];
      if (e.block && e.block !== "main") continue;
      if (e.status === "replaced") continue;
      total++;
      if (e.status === "done" || e.status === "skipped") done++;
    }
    return {
      done: done,
      total: total,
      elapsed: session ? T.elapsedMin(session.started_at) : 0
    };
  }

  /* =====================================================================
   *  РАЗМЕТКА
   * ===================================================================== */

  /**
   * Каркас страницы: шапка + контейнер тела (#trBody).
   */
  function shellHtml(subtitle) {
    return (
      '<section class="page sub-page tr-page tr-today">' +
      T.headHtml({ icon: "🧑‍🏫", title: pick("Тренер", "Coach"), subtitle: subtitle || "" }) +
      '<div id="trBody"></div>' +
      "</section>"
    );
  }

  /**
   * Карточка дня — четыре состояния (ТЗ §2.4).
   */
  function todayCardHtml(ov) {
    var today = ov.today || {};
    var day = today.day || null;
    var session = today.active_session || T.cache.activeSession || null;

    // 1) Незавершённая сессия → «Продолжить».
    if (ov.active_session_id) {
      var pr = sessionProgress(session);
      var meta = [];
      if (pr.elapsed) meta.push(pr.elapsed + " " + pick("мин", "min"));
      if (pr.total) meta.push(pr.done + "/" + pr.total);
      return (
        '<section class="card tr-today-card tr-today-card--progress">' +
        '<span class="tr-day-badge tr-day-badge--progress">' + esc(pick("Тренировка в процессе", "Workout in progress")) + "</span>" +
        '<h2 class="tr-today-card__title">' + esc((session && session.title) || (day && day.title) || pick("Тренировка", "Workout")) + "</h2>" +
        (meta.length ? '<p class="tr-today-card__meta">' + esc(meta.join(" · ")) + "</p>" : "") +
        '<div class="tr-today-card__actions">' +
        '<button type="button" class="btn btn-cta btn-block" id="trContinue">' + esc(pick("Продолжить", "Continue")) + "</button>" +
        "</div>" +
        "</section>"
      );
    }

    // 2) Тренировочный день по плану → «Начать».
    if (today.kind === "planned" && day) {
      var exs = day.exercises || [];
      var preview = "";
      for (var i = 0; i < Math.min(3, exs.length); i++) {
        preview +=
          '<div class="tr-ex-preview__item">' +
          '<span class="tr-ex-preview__name">' + esc(T.exName(exs[i])) + "</span>" +
          '<span class="tr-ex-preview__meta">' + esc(shortTarget(exs[i])) + "</span>" +
          "</div>";
      }
      if (exs.length > 3) {
        preview += '<div class="tr-ex-preview__more">' + esc(pick("и ещё ", "and ") + (exs.length - 3) + pick("", " more")) + "</div>";
      }
      return (
        '<section class="card tr-today-card">' +
        '<span class="tr-day-badge tr-day-badge--today">' + esc(pick("Сегодня по плану", "Planned for today")) + "</span>" +
        '<h2 class="tr-today-card__title">' + esc(day.title || pick("Тренировка", "Workout")) + "</h2>" +
        '<p class="tr-today-card__meta">' + esc(dayMeta(day)) + "</p>" +
        (preview ? '<div class="tr-ex-preview">' + preview + "</div>" : "") +
        '<div class="tr-today-card__actions">' +
        '<button type="button" class="btn btn-cta btn-block" id="trStart" data-day-id="' + esc(day.id) + '">' +
        esc(pick("Начать", "Start")) +
        "</button>" +
        "</div>" +
        "</section>"
      );
    }

    // 3) Тренировка на сегодня уже закрыта → итог дня + следующая.
    if (today.kind === "done" && day) {
      var skipped = day.status === "skipped";
      var nextText = "";
      if (today.next_date) {
        nextText = pick("Следующая — ", "Next — ") + T.shortDate(today.next_date) +
          (today.next_title ? ": " + today.next_title : "");
      }
      return (
        '<section class="card tr-today-card tr-today-card--done">' +
        '<span class="tr-day-badge tr-day-badge--today">' +
        esc(skipped ? pick("Сегодня пропущено", "Skipped today") : pick("Сегодня сделано", "Done today")) +
        "</span>" +
        '<h2 class="tr-today-card__title">' + esc((skipped ? "" : "✓ ") + (day.title || pick("Тренировка", "Workout"))) + "</h2>" +
        (nextText ? '<p class="tr-today-card__meta">' + esc(nextText) + "</p>" : "") +
        '<div class="tr-today-card__actions">' +
        '<button type="button" class="btn btn-ghost btn-block" id="trDoneProgress">' +
        esc(pick("Итоги и прогресс", "Results and progress")) +
        "</button>" +
        "</div>" +
        "</section>"
      );
    }

    // 4) Неделя закрыта → «Дополнительная тренировка».
    if (today.kind === "week_done") {
      return (
        '<section class="card tr-today-card tr-today-card--rest">' +
        '<span class="tr-day-badge tr-day-badge--today">' + esc(pick("План недели выполнен", "Weekly plan complete")) + "</span>" +
        '<h2 class="tr-today-card__title">' + esc(pick("Неделя закрыта 🎉", "Week complete 🎉")) + "</h2>" +
        '<p class="tr-today-card__meta">' +
        esc(pick("Все тренировки недели сделаны. Отдых — тоже часть плана.", "All workouts this week are done. Rest is part of the plan too.")) +
        "</p>" +
        '<div class="tr-today-card__actions">' +
        '<button type="button" class="btn btn-ghost btn-block" id="trStart" data-day-id="' + esc(day ? day.id : "") + '">' +
        esc(pick("Дополнительная тренировка", "Extra workout")) +
        "</button>" +
        "</div>" +
        "</section>"
      );
    }

    // 5) День отдыха (по умолчанию).
    var nextLine = "";
    if (day) {
      var when = today.next_date ? T.shortDate(today.next_date) : "";
      nextLine = pick("Следующая", "Next") + (when ? " — " + when : "") + ": " + (day.title || "");
    }
    return (
      '<section class="card tr-today-card tr-today-card--rest">' +
      '<span class="tr-day-badge tr-day-badge--rest">' + esc(pick("Отдых", "Rest")) + "</span>" +
      '<h2 class="tr-today-card__title">' + esc(pick("День отдыха 😌", "Rest day 😌")) + "</h2>" +
      (nextLine ? '<p class="tr-today-card__meta">' + esc(nextLine) + "</p>" : "") +
      (day
        ? '<button type="button" class="tr-today-card__link" id="trStart" data-day-id="' + esc(day.id) + '">' +
          esc(pick("Всё равно потренироваться", "Train anyway")) +
          "</button>"
        : "") +
      "</section>"
    );
  }

  /**
   * Лента недели Пн–Вс: по overview.week (7 элементов) либо пустая неделя.
   */
  function weekStripHtml(ov) {
    var today = App.todayStr();
    var monday = T.shiftDate(today, -T.weekdayOf(today));
    var items = (ov.week && ov.week.length) ? ov.week : [];
    var html = "";
    for (var i = 0; i < 7; i++) {
      var date = T.shiftDate(monday, i);
      var it = null;
      for (var j = 0; j < items.length; j++) {
        var cand = items[j] || {};
        if (cand.date === date || (cand.date == null && cand.weekday === i)) {
          it = cand;
          break;
        }
      }
      if (!it && items[i] && items[i].date == null) it = items[i];
      var status = (it && it.status) || "rest";
      if (status === "today") status = "planned";
      var cls = "tr-week-day tr-week-day--" + status;
      if (date === today) cls += " tr-week-day--today";
      var mark = String(parseInt(date.split("-")[2], 10));
      if (status === "done") mark = "✓";
      else if (status === "skipped") mark = "×";
      var title = it && it.title ? it.title : T.label("dayStatus", status);
      html +=
        '<div class="' + cls + '" title="' + esc(title) + '">' +
        '<span class="tr-week-day__label">' + esc(T.label("weekday", i)) + "</span>" +
        '<span class="tr-week-day__dot">' + esc(mark) + "</span>" +
        "</div>";
    }
    return '<div class="tr-week-strip">' + html + "</div>";
  }

  /**
   * Стрик: «🔥 3 недели подряд · 2 из 3 на этой неделе» + прогресс-бар.
   */
  function streakHtml(ov) {
    var s = ov.streak || {};
    var weeks = Number(s.weeks) || 0;
    var done = Number(s.this_week_done) || 0;
    var goal = Number(s.this_week_goal) || (ov.program && ov.program.days_per_week) || 0;
    var pctv = goal ? Math.min(100, Math.round((done / goal) * 100)) : 0;
    var main = weeks
      ? weeks + " " + weekWord(weeks) + pick(" подряд", " in a row")
      : pick("Начните серию на этой неделе", "Start your streak this week");
    var sub = goal
      ? done + pick(" из ", " of ") + goal + pick(" на этой неделе", " this week")
      : "";
    return (
      '<section class="card tr-streak">' +
      '<div class="tr-streak__row">' +
      '<span class="tr-streak__icon" aria-hidden="true">🔥</span>' +
      '<span class="tr-streak__text">' + esc(main) +
      (sub ? '<span class="tr-streak__sub">' + esc(sub) + "</span>" : "") +
      "</span>" +
      "</div>" +
      (goal
        ? '<div class="tr-streak__bar"><div class="tr-streak__fill" style="width:' + pctv + '%"></div></div>'
        : "") +
      "</section>"
    );
  }

  /**
   * Баннер «Недельный разбор готов к запуску».
   */
  function reviewBannerHtml(ov) {
    if (!ov.pending_review) return "";
    return (
      '<button type="button" class="card tr-review-banner" id="trReviewBanner">' +
      '<span class="tr-review-banner__icon" aria-hidden="true">📝</span>' +
      '<span class="tr-review-banner__text">' +
      esc(pick("Недельный разбор готов к запуску", "Your weekly review is ready to run")) +
      "</span>" +
      '<span class="tr-review-banner__arrow" aria-hidden="true">›</span>' +
      "</button>"
    );
  }

  /**
   * Карточка «Питание сегодня» — контейнер со скелетоном (грузится лениво).
   */
  function nutritionCardHtml() {
    return (
      '<section class="card tr-nutrition-card" id="trNutrition">' +
      '<h3 class="tr-nutrition-card__title">' + esc(pick("Питание сегодня", "Nutrition today")) + "</h3>" +
      '<div id="trNutritionBody">' +
      '<div class="skeleton skeleton-line"></div>' +
      '<div class="skeleton skeleton-line short"></div>' +
      "</div>" +
      "</section>"
    );
  }

  /**
   * Быстрые ссылки: Программа · Прогресс · Упражнения · Настройки.
   */
  function linksHtml() {
    var items = [
      { key: "program", icon: "📋", label: pick("Программа", "Program") },
      { key: "progress", icon: "📈", label: pick("Прогресс", "Progress") },
      { key: "exercise", icon: "📚", label: pick("Упражнения", "Exercises") },
      { key: "settings", icon: "⚙️", label: pick("Настройки", "Settings") }
    ];
    var html = "";
    for (var i = 0; i < items.length; i++) {
      html +=
        '<button type="button" class="tr-link" data-link="' + items[i].key + '">' +
        '<span class="tr-link__icon" aria-hidden="true">' + items[i].icon + "</span>" +
        '<span class="tr-link__label">' + esc(items[i].label) + "</span>" +
        "</button>";
    }
    return '<div class="tr-links">' + html + "</div>";
  }

  /**
   * Экран «Программа не создана».
   */
  function emptyProgramHtml() {
    return (
      '<section class="card wk-empty tr-empty">' +
      '<div class="wk-empty__icon" aria-hidden="true">📋</div>' +
      '<p class="wk-empty__title">' + esc(pick("Программа не создана", "No program yet")) + "</p>" +
      '<p class="wk-empty__text">' +
      esc(pick(
        "Анкета заполнена. Соберём программу под вашу цель, уровень и оборудование — это займёт до минуты.",
        "Your profile is ready. Let’s build a program for your goal, level and equipment — it takes under a minute."
      )) +
      "</p>" +
      '<button type="button" class="btn btn-cta btn-block" id="trGenerate">' + esc(pick("Собрать программу", "Build my program")) + "</button>" +
      '<button type="button" class="btn btn-ghost btn-block" id="trSettings">' + esc(pick("Изменить настройки", "Change settings")) + "</button>" +
      "</section>"
    );
  }

  /* =====================================================================
   *  РЕНДЕР И СОБЫТИЯ
   * ===================================================================== */

  /** Обновляет подзаголовок шапки без перерисовки страницы. */
  function setSubtitle(text) {
    if (!state.viewEl) return;
    var el = state.viewEl.querySelector(".sub-subtitle");
    if (el) el.textContent = text || "";
  }

  /**
   * Рисует экран «Сегодня» по overview.
   */
  function renderToday(ov) {
    var body = byId("trBody");
    if (!body) return;
    setSubtitle(subtitleFor(ov));
    body.innerHTML =
      todayCardHtml(ov) +
      reviewBannerHtml(ov) +
      '<h3 class="tr-section-title">' + esc(pick("Эта неделя", "This week")) + "</h3>" +
      '<section class="card">' + weekStripHtml(ov) + "</section>" +
      streakHtml(ov) +
      nutritionCardHtml() +
      linksHtml();
    bindToday(ov);
    loadNutrition();
  }

  /**
   * Рисует пустое состояние (профиль есть, программы нет).
   */
  function renderEmpty() {
    var body = byId("trBody");
    if (!body) return;
    setSubtitle(pick("Персональная программа", "Your personal program"));
    body.innerHTML = emptyProgramHtml();
    var gen = byId("trGenerate");
    if (gen) {
      gen.addEventListener("click", function () {
        App.haptic("medium");
        generate();
      });
    }
    var settings = byId("trSettings");
    if (settings) {
      settings.addEventListener("click", function () {
        App.haptic("light");
        openSettings();
      });
    }
  }

  /**
   * Ошибка загрузки overview: карточка с «Повторить».
   */
  function renderError(err) {
    var body = byId("trBody");
    if (!body) return;
    body.innerHTML = T.errorCard(T.errMessage(err), "trRetry");
    var btn = byId("trRetry");
    if (btn) {
      btn.addEventListener("click", function () {
        App.haptic("light");
        load();
      });
    }
  }

  /**
   * Обработчики экрана «Сегодня».
   */
  function bindToday(ov) {
    var start = byId("trStart");
    if (start) {
      start.addEventListener("click", function () {
        var raw = start.getAttribute("data-day-id");
        var dayId = raw ? parseInt(raw, 10) : null;
        startDay(isNaN(dayId) ? null : dayId, start);
      });
    }
    var doneBtn = byId("trDoneProgress");
    if (doneBtn) {
      doneBtn.addEventListener("click", function () {
        App.haptic("light");
        T.go("trainer-progress");
      });
    }
    var cont = byId("trContinue");
    if (cont) {
      cont.addEventListener("click", function () {
        App.haptic("medium");
        var session = (ov.today && ov.today.active_session) || T.cache.activeSession || null;
        if (!session && ov.active_session_id) App.state.trainerSessionId = ov.active_session_id;
        if (!T.openSession(session)) load();
      });
    }
    var banner = byId("trReviewBanner");
    if (banner) {
      banner.addEventListener("click", function () {
        App.haptic("light");
        App.state.trainerProgressSection = "review";
        T.go("trainer-progress");
      });
    }
    var links = state.viewEl ? state.viewEl.querySelectorAll(".tr-link") : [];
    for (var i = 0; i < links.length; i++) {
      links[i].addEventListener("click", onLink);
    }
  }

  function onLink(ev) {
    var key = ev.currentTarget.getAttribute("data-link");
    App.haptic("light");
    if (key === "program") {
      App.state.trainerProgramMode = null;
      T.go("trainer-program");
    } else if (key === "progress") {
      App.state.trainerProgressSection = null;
      T.go("trainer-progress");
    } else if (key === "exercise") {
      App.state.trainerExerciseId = null;
      T.go("trainer-exercise");
    } else if (key === "settings") {
      openSettings();
    }
  }

  /** Настройки = онбординг в режиме редактирования. */
  function openSettings() {
    App.state.trainerEdit = true;
    App.navigate("trainer-onboarding");
  }

  /**
   * Старт сессии дня → экран выполнения.
   */
  function startDay(dayId, btn) {
    if (state.starting) return;
    state.starting = true;
    if (btn) btn.disabled = true;
    App.haptic("medium");
    T.startSession(dayId)
      .then(function (session) {
        if (!T.openSession(session)) load();
      })
      .catch(function (err) {
        App.toast(T.errMessage(err, pick("Не удалось начать тренировку", "Failed to start the workout")));
      })
      .finally(function () {
        state.starting = false;
        if (btn) btn.disabled = false;
      });
  }

  /**
   * Генерация программы из пустого состояния (профиль уже заполнен).
   */
  function generate() {
    var body = byId("trBody");
    if (!body) return;
    T.runGenerate(body, {})
      .then(function (program) {
        App.haptic("success");
        T.openProgram(program, "preview");
      })
      .catch(function (err) {
        if (!byId("trBody")) return;
        body.innerHTML =
          T.errorCard(
            T.errMessage(err, pick("Не удалось собрать программу. Попробуйте ещё раз.", "Failed to build the program. Please try again.")),
            "trGenRetry"
          ) +
          '<button type="button" class="btn btn-ghost btn-block" id="trGenBack">' + esc(pick("Назад", "Back")) + "</button>";
        var retry = byId("trGenRetry");
        if (retry) {
          retry.addEventListener("click", function () {
            App.haptic("light");
            generate();
          });
        }
        var back = byId("trGenBack");
        if (back) {
          back.addEventListener("click", function () {
            App.haptic("light");
            renderEmpty();
          });
        }
      });
  }

  /* =====================================================================
   *  ПИТАНИЕ СЕГОДНЯ (ленивая загрузка, ИИ при промахе кэша)
   * ===================================================================== */

  function loadNutrition() {
    var box = byId("trNutritionBody");
    if (!box) return;
    var reqId = ++state.nutritionReq;
    App.api
      .trainerNutritionToday(App.todayStr())
      .then(function (tip) {
        if (reqId !== state.nutritionReq) return;
        renderNutrition(tip);
      })
      .catch(function (err) {
        if (reqId !== state.nutritionReq) return;
        var b = byId("trNutritionBody");
        if (!b) return;
        b.innerHTML =
          '<p class="tr-nutrition-card__muted">' +
          esc(T.errMessage(err, pick("Совет пока недоступен", "Tip is unavailable right now"))) +
          "</p>" +
          '<button type="button" class="btn btn-ghost tr-nutrition-card__retry" id="trNutritionRetry">' +
          esc(pick("Повторить", "Retry")) +
          "</button>";
        var retry = byId("trNutritionRetry");
        if (retry) {
          retry.addEventListener("click", function () {
            App.haptic("light");
            b.innerHTML = '<div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line short"></div>';
            loadNutrition();
          });
        }
      });
  }

  function renderNutrition(tip) {
    var box = byId("trNutritionBody");
    if (!box) return;
    tip = tip || {};
    var n = tip.numbers || {};
    var kindLabel = tip.kind === "training"
      ? pick("Тренировочный день", "Training day")
      : pick("День отдыха", "Rest day");
    var numbers = "";
    if (n.goal_kcal != null) {
      numbers += "<span>" + esc(pick("Цель ", "Goal ")) + "<b>" + esc(App.fmt(n.goal_kcal)) + "</b> " + esc(pick("ккал", "kcal")) + "</span>";
    }
    if (n.eaten_kcal != null) {
      numbers += "<span>" + esc(pick("Съедено ", "Eaten ")) + "<b>" + esc(App.fmt(n.eaten_kcal)) + "</b></span>";
    }
    if (n.burned_kcal) {
      numbers += "<span>" + esc(pick("Сожжено ", "Burned ")) + "<b>" + esc(App.fmt(n.burned_kcal)) + "</b></span>";
    }
    if (n.protein_goal != null) {
      numbers +=
        "<span>" + esc(pick("Белок ", "Protein ")) + "<b>" +
        esc(App.fmt(n.protein_eaten || 0)) + "</b> / " + esc(App.fmt(n.protein_goal)) + " " + esc(pick("г", "g")) + "</span>";
    }
    var rows = "";
    if (tip.calories_note) rows += row(pick("Калории", "Calories"), tip.calories_note);
    if (tip.protein_note) rows += row(pick("Белок", "Protein"), tip.protein_note);
    if (tip.pre_workout) rows += row(pick("До тренировки", "Before workout"), tip.pre_workout);
    if (tip.post_workout) rows += row(pick("После", "After"), tip.post_workout);
    if (tip.hydration) rows += row(pick("Вода", "Hydration"), tip.hydration);
    var tips = tip.tips || [];
    if (tips.length) rows += row(pick("Советы", "Tips"), tips.join(" · "));

    box.innerHTML =
      '<p class="tr-nutrition-card__headline">' +
      esc(kindLabel + (tip.headline ? ": " + tip.headline : "")) +
      "</p>" +
      (numbers ? '<div class="tr-nutrition-card__numbers">' + numbers + "</div>" : "") +
      rows +
      '<button type="button" class="btn btn-ghost btn-block" id="trWhatToEat">' +
      esc(pick("Что съесть?", "What should I eat?")) +
      "</button>";

    var btn = byId("trWhatToEat");
    if (btn) {
      btn.addEventListener("click", function () {
        App.haptic("light");
        // Подсказка дневнику открыть «Что съесть?» (should: deep-link).
        App.state.diaryOpenSuggest = true;
        App.navigate("diary");
      });
    }
  }

  function row(label, text) {
    return (
      '<div class="tr-nutrition-card__row">' +
      '<span class="tr-nutrition-card__label">' + esc(label) + "</span>" +
      esc(text) +
      "</div>"
    );
  }

  /* =====================================================================
   *  ЗАГРУЗКА
   * ===================================================================== */

  /**
   * Загружает overview и маршрутизирует: онбординг / пустое состояние / «Сегодня».
   */
  function load() {
    var body = byId("trBody");
    if (!body || state.loading) return;
    state.loading = true;
    body.innerHTML = T.skeleton(4) + T.skeleton(2);

    App.api
      .trainerOverview()
      .then(function (ov) {
        state.loading = false;
        if (!byId("trBody")) return; // страница уже закрыта
        ov = ov || {};
        state.overview = ov;
        T.cache.overview = ov;
        if (ov.today && ov.today.active_session) T.cache.activeSession = ov.today.active_session;
        setBrief(ov);

        // Профиль не заполнен → онбординг (первичный, не edit).
        if (!ov.profile || !ov.profile.onboarding_completed) {
          App.state.trainerEdit = false;
          App.navigate("trainer-onboarding");
          return;
        }
        // Нет активной программы → пустое состояние.
        if (!ov.program) {
          renderEmpty();
          return;
        }
        renderToday(ov);
      })
      .catch(function (err) {
        state.loading = false;
        if (!byId("trBody")) return;
        if (err && err.status === 402) {
          App.paywall(state.viewEl, T.paywallOpts());
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
      // Первый вход извне раздела — запоминаем, куда возвращаться.
      if (!App.state.trainerOrigin) App.state.trainerOrigin = "workouts";
      viewEl.innerHTML = shellHtml("");
      T.bindBack(viewEl);
      load();
    },

    onHide: function () {
      state.viewEl = null;
      state.loading = false;
      state.starting = false;
      state.nutritionReq++;
      T.closeSheet(true);
    },

    /** Принудительное обновление (для других страниц после изменений). */
    refresh: function () {
      T.cache.invalidate();
      if (state.viewEl) load();
    }
  };

  window.PageTrainer = controller;
  App.registerPage("trainer", controller);
})();
