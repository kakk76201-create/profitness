/*
 * page-trainer.js — раздел «AI-тренер» (страница "trainer").
 *
 * Регистрирует контроллер через App.registerPage("trainer", {...}).
 * Публичная ссылка — window.PageTrainer ({switchTo(segment), refresh()}).
 *
 * Это КОРЕНЬ раздела (вкладка «Тренировка» в нижней навигации) и ОБОЛОЧКА
 * четырёх вкладок: шапка «Тренировка» с неделей программы и шестерёнкой,
 * липкая полоса вкладок «Сегодня / Программа / Прогресс / Упражнения» и
 * панели содержимого под ней. Переключение вкладки не вызывает App.navigate:
 * раньше каждая вкладка была отдельной страницей со своей шапкой и кнопкой
 * «Назад», и человек, переключая виды одного раздела, каждый раз «уходил»
 * с экрана и искал дорогу обратно.
 *
 * Панели живут в DOM, пока открыт раздел: вкладка монтируется при первом
 * открытии, дальше скрывается и показывается атрибутом hidden. Повторное
 * переключение мгновенное, без скелетона и без потери раскрытых дней,
 * поиска и выбранного графика. Панель перезагружается, только если данные
 * тренера изменились с момента её загрузки (Trainer.cache.version).
 *
 *   • «Сегодня»    — рисуется здесь же, по overview;
 *   • «Программа»  — window.TrainerProgram.mount/unmount (page-trainer-program.js);
 *   • «Прогресс»   — window.TrainerProgress.mount/unmount (page-trainer-progress.js);
 *   • «Упражнения» — window.TrainerLibrary.mount/unmount (page-trainer-exercise.js).
 *
 * Выбранная вкладка — App.state.trainerSegment (контракт между разделами):
 * onShow открывает её и не сбрасывает, это память выбора между заходами.
 *
 * Сценарий onShow (ТЗ §2.1, §2.4):
 *   GET /trainer/overview (всегда: из него подзаголовок шапки) →
 *     • 402 (нет подписки) → paywall тренера с бесплатным входом в анкету;
 *     • профиля нет / onboarding_completed=false → App.navigate("trainer-onboarding");
 *     • вкладка «Сегодня»: нет активной программы → «Программа не создана»;
 *       иначе карточка дня (в процессе / по плану / отдых / неделя закрыта),
 *       лента недели, стрик, «Самочувствие», «Питание сегодня».
 *
 * «Самочувствие» — совет по восстановлению (POST /recovery/advice): переехал
 * сюда из удалённого раздела «Тренировки». Место естественное: зону тела
 * выбирают либо перед тренировкой, либо сразу после неё.
 *
 * Побочный эффект: после overview заполняет App.state.trainerBrief — текст
 * вкладки «Тренировка» в нижней навигации.
 *
 * Зависимости: window.Trainer (trainer-common.js), App.api.trainer*,
 * App.api.getRecoveryAdvice, App.icon (js/icons.js).
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

  // Зоны тела для «Самочувствия». Ключ уходит на бэкенд как есть
  // (backend/schemas.py: legs|back|shoulders|arms|chest|core|neck|knees).
  // Иконок здесь нет намеренно: в прежней версии зоны кодировались эмодзи,
  // и половину из них («шея», «колени») картинка не изображала вовсе.
  var RECOVERY_ZONES = [
    { key: "legs", ru: "Ноги", en: "Legs" },
    { key: "back", ru: "Спина", en: "Back" },
    { key: "shoulders", ru: "Плечи", en: "Shoulders" },
    { key: "arms", ru: "Руки", en: "Arms" },
    { key: "chest", ru: "Грудь", en: "Chest" },
    { key: "core", ru: "Пресс", en: "Core" },
    { key: "neck", ru: "Шея", en: "Neck" },
    { key: "knees", ru: "Колени", en: "Knees" }
  ];

  // Вкладки раздела. Ключи — контракт App.state.trainerSegment.
  var SEGMENTS = [
    { key: "today", ru: "Сегодня", en: "Today" },
    { key: "program", ru: "Программа", en: "Program" },
    { key: "progress", ru: "Прогресс", en: "Progress" },
    { key: "exercises", ru: "Упражнения", en: "Exercises" }
  ];

  // Модули, которые рисуют содержимое вкладок (кроме «Сегодня»). Ищем их
  // по имени в момент монтирования: скрипты вкладок подключаются ПОСЛЕ
  // этого файла, и прямая ссылка при загрузке была бы undefined.
  var MODULES = {
    program: "TrainerProgram",
    progress: "TrainerProgress",
    exercises: "TrainerLibrary"
  };

  // Внутреннее состояние контроллера.
  var state = {
    viewEl: null,        // корневой элемент (#view)
    overview: null,      // последний TrainerOverviewOut
    overviewErr: null,   // ошибка последней загрузки overview
    overviewVersion: -1, // Trainer.cache.version на момент загрузки overview
    loading: false,      // идёт загрузка overview (защита от гонок)
    ovReq: 0,            // счётчик запросов overview (устаревшие ответы — мимо)
    subtitle: "",        // последний подзаголовок шапки (см. onShow)
    segment: null,       // открытая вкладка
    panes: {},           // вкладка → {mounted, version}
    starting: false,     // идёт старт сессии
    nutritionReq: 0,     // счётчик запросов совета (игнорируем устаревшие ответы)
    recoveryZone: null,  // выбранная зона тела в «Самочувствии»
    recoveryBusy: false  // идёт запрос совета по восстановлению
  };

  /* =====================================================================
   *  СВОДКА ДЛЯ КАРТОЧКИ-ВХОДА НА «ТРЕНИРОВКАХ»
   * ===================================================================== */

  /**
   * Заполняет App.state.trainerBrief по overview (или по факту отсутствия
   * профиля). Используется экраном «Сегодня» для карточки тренировки дня.
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
   * Полоса вкладок (ARIA tabs). Стоит сразу под заголовком и прилипает к
   * верху при прокрутке: «Прогресс» и «Программа» длиннее экрана, и без
   * липкой полосы для смены вида пришлось бы сначала долистать до верха.
   * Кнопки отрисованы все неактивными — выбор расставляет activate().
   */
  function tabsHtml() {
    var html = "";
    for (var i = 0; i < SEGMENTS.length; i++) {
      var s = SEGMENTS[i];
      html +=
        '<button type="button" class="tr-tab" role="tab" id="trTab-' + s.key + '" ' +
        'data-seg="' + s.key + '" aria-controls="trPanel-' + s.key + '" ' +
        'aria-selected="false" tabindex="-1">' +
        esc(pick(s.ru, s.en)) +
        "</button>";
    }
    return (
      '<div class="tr-tabs-bar" id="trTabsBar">' +
      '<div class="tr-tabs" role="tablist" aria-label="' + esc(pick("Разделы тренера", "Coach sections")) + '">' +
      html +
      "</div>" +
      "</div>"
    );
  }

  /** Панели вкладок: пустые и скрытые, наполняются при первом открытии. */
  function panelsHtml() {
    var html = "";
    for (var i = 0; i < SEGMENTS.length; i++) {
      var key = SEGMENTS[i].key;
      html +=
        '<div class="tr-seg-panel' + (key === "today" ? " tr-today" : "") + '" ' +
        'id="trPanel-' + key + '" role="tabpanel" aria-labelledby="trTab-' + key + '" hidden></div>';
    }
    return '<div class="tr-seg-body" id="trSegBody">' + html + "</div>";
  }

  /**
   * Каркас раздела: шапка корня (без «Назад», с иконкой настроек), полоса
   * вкладок и панели. Рисуется один раз за заход и при переключении вкладок
   * не перерисовывается.
   */
  function shellHtml(subtitle) {
    var settings =
      '<button type="button" class="tr-head__action" id="trHeadSettings" ' +
      'aria-label="' + esc(pick("Настройки тренера", "Coach settings")) + '">' +
      icon("settings", { size: 22 }) +
      "</button>";
    return (
      '<section class="page tr-page tr-shell">' +
      T.headHtml({
        back: false,
        title: pick("Тренировка", "Workout"),
        subtitle: subtitle || "",
        actions: settings
      }) +
      tabsHtml() +
      panelsHtml() +
      "</section>"
    );
  }

  /**
   * Тёмный блок дня — тот же приём, что тренировка на экране «Сегодня»
   * приложения: фото через переменную --hero-img (затемнение кладёт CSS),
   * надзаголовок, крупный заголовок, мета и одно действие. Состояния без
   * главного действия ведут внутрь ссылкой, а не кнопкой.
   * @param {object} o {img, mod, eyebrow, eyebrowIcon, title, meta,
   *        button:{id, label, attrs}, link:{id, label, attrs}}
   */
  function heroHtml(o) {
    var cls = "hero tr-hero" + (o.img ? " hero--img" : "") + (o.mod ? " " + o.mod : "");
    var style = o.img ? ' style="' + T.heroImg(o.img) + '"' : "";
    var html =
      '<section class="' + cls + '"' + style + ">" +
      '<span class="eyebrow">' +
      (o.eyebrowIcon ? icon(o.eyebrowIcon, { size: 14 }) : "") +
      "<span>" + esc(o.eyebrow) + "</span></span>" +
      '<h2 class="hero__title tr-hero__title">' + esc(o.title) + "</h2>" +
      (o.meta ? '<p class="hero__meta">' + esc(o.meta) + "</p>" : "");
    if (o.button) {
      html +=
        '<button type="button" class="btn btn--cta tr-hero__btn" id="' + esc(o.button.id) + '"' +
        (o.button.attrs || "") + ">" + esc(o.button.label) + "</button>";
    }
    if (o.link) {
      html +=
        '<button type="button" class="tr-hero__link" id="' + esc(o.link.id) + '"' +
        (o.link.attrs || "") + ">" + esc(o.link.label) + icon("chevron", { size: 16 }) + "</button>";
    }
    return html + "</section>";
  }

  /**
   * Блок дня — четыре состояния (ТЗ §2.4).
   */
  function todayCardHtml(ov) {
    var today = ov.today || {};
    var day = today.day || null;
    var session = today.active_session || T.cache.activeSession || null;

    // 1) Незавершённая сессия → «Продолжить». Оранжевая полоса слева:
    //    человек в процессе, это должно бросаться в глаза.
    if (ov.active_session_id) {
      var pr = sessionProgress(session);
      var meta = [];
      // Длительность — только через fmtDuration: брошенная неделю назад
      // сессия иначе показывает «9532 мин».
      if (pr.elapsed) meta.push(T.fmtDuration(pr.elapsed));
      if (pr.total) meta.push(pr.done + "/" + pr.total + " " + exWord(pr.total));
      return heroHtml({
        img: "hero-workout.jpg",
        mod: "tr-hero--active",
        eyebrow: pick("Тренировка идёт", "Workout in progress"),
        title: (session && session.title) || (day && day.title) || pick("Тренировка", "Workout"),
        meta: meta.join(" · "),
        button: { id: "trContinue", label: pick("Продолжить", "Continue") }
      });
    }

    // 2) Тренировочный день по плану → «Начать».
    if (today.kind === "planned" && day) {
      return heroHtml({
        img: "hero-workout.jpg",
        eyebrow: pick("Тренировка дня", "Workout of the day"),
        title: day.title || pick("Тренировка", "Workout"),
        meta: dayMeta(day),
        button: { id: "trStart", label: pick("Начать", "Start"), attrs: ' data-day-id="' + esc(day.id) + '"' }
      });
    }

    // 3) Тренировка на сегодня уже закрыта → итог дня + следующая.
    if (today.kind === "done" && day) {
      var skipped = day.status === "skipped";
      var nextText = "";
      if (today.next_date) {
        nextText = pick("Следующая — ", "Next — ") + T.shortDate(today.next_date) +
          (today.next_title ? ": " + today.next_title : "");
      }
      return heroHtml({
        mod: "tr-hero--calm",
        eyebrow: skipped ? pick("Сегодня пропущено", "Skipped today") : pick("Сегодня сделано", "Done today"),
        eyebrowIcon: skipped ? "" : "check",
        title: day.title || pick("Тренировка", "Workout"),
        meta: nextText,
        link: { id: "trDoneProgress", label: pick("Итоги и прогресс", "Results and progress") }
      });
    }

    // 4) Неделя закрыта → «Дополнительная тренировка».
    if (today.kind === "week_done") {
      return heroHtml({
        mod: "tr-hero--calm",
        eyebrow: pick("План недели выполнен", "Weekly plan complete"),
        title: pick("Неделя закрыта", "Week complete"),
        meta: pick("Все тренировки недели сделаны. Отдых — тоже часть плана.", "All workouts this week are done. Rest is part of the plan too."),
        link: {
          id: "trStart",
          label: pick("Дополнительная тренировка", "Extra workout"),
          attrs: ' data-day-id="' + esc(day ? day.id : "") + '"'
        }
      });
    }

    // 5) День отдыха (по умолчанию).
    var nextLine = pick("Отдых — часть плана", "Rest is part of the plan");
    if (day) {
      var when = today.next_date ? T.shortDate(today.next_date) : "";
      nextLine = pick("Следующая", "Next") + (when ? " — " + when : "") + ": " + (day.title || "");
    }
    return heroHtml({
      mod: "tr-hero--calm",
      eyebrow: pick("Отдых", "Rest"),
      title: pick("День отдыха", "Rest day"),
      meta: nextLine,
      link: day
        ? { id: "trStart", label: pick("Всё равно потренироваться", "Train anyway"), attrs: ' data-day-id="' + esc(day.id) + '"' }
        : null
    });
  }

  /**
   * Упражнения дня под тёмным блоком: номер, название, цель «3×8–12 · 14 кг».
   * Только для запланированного дня — в отдых и после тренировки план дня
   * уже не нужен.
   */
  function dayListHtml(ov) {
    var today = ov.today || {};
    var day = today.day || null;
    if (ov.active_session_id || today.kind !== "planned" || !day) return "";
    var exs = day.exercises || [];
    if (!exs.length) return "";
    var rows = "";
    for (var i = 0; i < exs.length; i++) {
      rows +=
        '<div class="tr-day-list__row">' +
        '<span class="tr-day-list__num">' + (i + 1) + "</span>" +
        '<span class="tr-day-list__name">' + esc(T.exName(exs[i])) + "</span>" +
        '<span class="tr-day-list__meta num">' + esc(shortTarget(exs[i])) + "</span>" +
        "</div>";
    }
    return (
      '<section class="card tr-day-list">' +
      '<span class="eyebrow">' + esc(pick("План на сегодня", "Today’s plan")) + "</span>" +
      rows +
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
      // Выполнено/пропущено читается иконкой, остальные дни — числом месяца.
      var mark = esc(String(parseInt(date.split("-")[2], 10)));
      if (status === "done") mark = icon("check", { size: 16 });
      else if (status === "skipped") mark = icon("close", { size: 14 });
      var title = it && it.title ? it.title : T.label("dayStatus", status);
      html +=
        '<div class="' + cls + '" title="' + esc(title) + '">' +
        '<span class="tr-week-day__label">' + esc(T.label("weekday", i)) + "</span>" +
        '<span class="tr-week-day__dot">' + mark + "</span>" +
        "</div>";
    }
    return '<div class="tr-week-strip">' + html + "</div>";
  }

  /**
   * Стрик: «3 недели подряд · 2 из 3 на этой неделе» + прогресс-бар.
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
      '<span class="eyebrow">' + esc(pick("Серия", "Streak")) + "</span>" +
      '<div class="tr-streak__row">' +
      '<span class="tr-streak__icon" aria-hidden="true">' + icon("flame", { size: 22 }) + "</span>" +
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
      '<span class="tr-review-banner__icon" aria-hidden="true">' + icon("chartLine", { size: 22 }) + "</span>" +
      '<span class="tr-review-banner__text">' +
      esc(pick("Недельный разбор готов к запуску", "Your weekly review is ready to run")) +
      "</span>" +
      '<span class="tr-review-banner__arrow" aria-hidden="true">' + icon("chevron", { size: 18 }) + "</span>" +
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
   * Карточка «Самочувствие» — совет по восстановлению (POST /recovery/advice).
   * Зоны тела — обычные текстовые чипы: список из восьми пунктов читается
   * быстрее без картинок, а половину зон («шея», «колени») эмодзи и вовсе
   * не изображают.
   */
  function recoveryCardHtml() {
    var chips = "";
    for (var i = 0; i < RECOVERY_ZONES.length; i++) {
      var z = RECOVERY_ZONES[i];
      chips +=
        '<button type="button" class="chip tr-zone' +
        (state.recoveryZone === z.key ? " chip--active" : "") +
        '" data-zone="' + z.key + '" aria-pressed="' +
        (state.recoveryZone === z.key ? "true" : "false") + '">' +
        esc(pick(z.ru, z.en)) +
        "</button>";
    }
    return (
      '<section class="card tr-recovery" id="trRecovery">' +
      '<h3 class="tr-recovery__title">' +
      icon("heart", { size: 20 }) +
      "<span>" + esc(pick("Самочувствие", "How you feel")) + "</span>" +
      "</h3>" +
      '<p class="tr-recovery__hint">' +
      esc(pick("Что беспокоит после тренировки?", "What bothers you after training?")) +
      "</p>" +
      '<div class="tr-recovery__zones">' + chips + "</div>" +
      '<label class="field tr-recovery__field">' +
      '<span class="field__label">' + esc(pick("Подробнее", "More details")) +
      ' <span class="field__hint">' + esc(pick("(необязательно)", "(optional)")) + "</span></span>" +
      '<input class="field__input" id="trRecComplaint" type="text" maxlength="200" ' +
      'placeholder="' + esc(pick(
        "напр. тянет заднюю поверхность после становой",
        "e.g. hamstrings feel tight after deadlifts"
      )) + '">' +
      "</label>" +
      '<button type="button" class="btn btn-ghost btn-block" id="trRecGo"' +
      (state.recoveryZone ? "" : " disabled") + ">" +
      esc(pick("Получить совет", "Get advice")) +
      "</button>" +
      '<div id="trRecResult" class="tr-recovery__result"></div>' +
      "</section>"
    );
  }

  /**
   * Ответ по восстановлению. «Красные флаги» и дисклеймер обязательны:
   * тренер не врач, и ответ сервера прямо об этом пишет.
   */
  function recoveryResultHtml(res) {
    function listHtml(title, items, cls, iconName) {
      if (!items || !items.length) return "";
      var li = "";
      for (var i = 0; i < items.length; i++) li += "<li>" + esc(items[i]) + "</li>";
      return (
        '<div class="rec-block ' + cls + '">' +
        '<div class="rec-block__title">' +
        (iconName ? icon(iconName, { size: 16 }) : "") +
        "<span>" + esc(title) + "</span>" +
        "</div><ul>" + li + "</ul></div>"
      );
    }
    var badge = res.is_typical_soreness
      ? '<span class="rec-badge rec-badge--ok">' +
        esc(pick("Похоже на обычную крепатуру", "Looks like ordinary soreness")) + "</span>"
      : '<span class="rec-badge rec-badge--warn">' +
        esc(pick("Требует осторожности", "Needs caution")) + "</span>";
    return (
      '<div class="rec-answer">' +
      badge +
      (res.likely_cause ? '<p class="rec-cause">' + esc(res.likely_cause) + "</p>" : "") +
      listHtml(pick("Сегодня", "Today"), res.today, "rec-block--today") +
      listHtml(pick("Избегать", "Avoid"), res.avoid, "rec-block--avoid") +
      (res.training
        ? '<div class="rec-block rec-block--training">' +
          '<div class="rec-block__title"><span>' + esc(pick("Когда тренироваться", "When to train")) + "</span></div>" +
          "<p>" + esc(res.training) + "</p></div>"
        : "") +
      listHtml(pick("К врачу, если", "See a doctor if"), res.red_flags, "rec-block--flags", "warning") +
      '<p class="rec-disclaimer">' +
      esc(res.disclaimer || pick(
        "Не является медицинской рекомендацией, проконсультируйтесь со специалистом",
        "This is not medical advice, consult a specialist"
      )) +
      "</p>" +
      "</div>"
    );
  }

  /**
   * Экран «Программа не создана»: тот же тёмный блок с фото, что и
   * тренировка дня, — раздел не должен выглядеть пустым ещё до старта.
   */
  function emptyProgramHtml() {
    return heroHtml({
      img: "empty-program.jpg",
      eyebrow: pick("Тренировки", "Training"),
      title: pick("Программы пока нет", "No program yet"),
      meta: pick(
        "Анкета заполнена. Соберём программу под вашу цель, уровень и оборудование — это займёт до минуты.",
        "Your profile is ready. Let’s build a program for your goal, level and equipment — it takes under a minute."
      ),
      button: { id: "trGenerate", label: pick("Собрать программу", "Build my program") },
      link: { id: "trSettings", label: pick("Изменить настройки", "Change settings") }
    });
  }

  /* =====================================================================
   *  РЕНДЕР И СОБЫТИЯ
   * ===================================================================== */

  /**
   * Обновляет подзаголовок шапки без перерисовки страницы. Текст запоминаем:
   * следующий заход рисует шапку сразу с ним, и строка не «прыгает» из
   * пустой в заполненную, сдвигая всё содержимое вниз после загрузки.
   */
  function setSubtitle(text) {
    state.subtitle = text || "";
    if (!state.viewEl) return;
    var el = state.viewEl.querySelector(".tr-shell .sub-subtitle");
    if (el) el.textContent = state.subtitle;
  }

  /**
   * Рисует экран «Сегодня» по overview.
   */
  function renderToday(ov) {
    var body = byId("trBody");
    if (!body) return;
    body.innerHTML =
      todayCardHtml(ov) +
      dayListHtml(ov) +
      reviewBannerHtml(ov) +
      '<section class="card">' +
      '<span class="eyebrow">' + esc(pick("Эта неделя", "This week")) + "</span>" +
      weekStripHtml(ov) +
      "</section>" +
      streakHtml(ov) +
      recoveryCardHtml() +
      nutritionCardHtml();
    bindToday(ov);
    bindRecovery();
    loadNutrition();
  }

  /**
   * Рисует пустое состояние (профиль есть, программы нет).
   */
  function renderEmpty() {
    var body = byId("trBody");
    if (!body) return;
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
        loadOverview();
      });
    }
  }

  /**
   * Содержимое вкладки «Сегодня» по текущему состоянию overview: скелетон,
   * ошибка, «Программа не создана» или сам экран дня.
   */
  function renderTodayPane() {
    var body = byId("trBody");
    if (!body) return;
    if (state.overviewErr) {
      renderError(state.overviewErr);
      return;
    }
    var ov = state.overview;
    if (!ov) {
      body.innerHTML = T.skeleton(4) + T.skeleton(2);
      return;
    }
    if (!ov.program) {
      renderEmpty();
      return;
    }
    renderToday(ov);
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
        activate("progress", { scroll: true });
      });
    }
    var cont = byId("trContinue");
    if (cont) {
      cont.addEventListener("click", function () {
        App.haptic("medium");
        var session = (ov.today && ov.today.active_session) || T.cache.activeSession || null;
        if (!session && ov.active_session_id) App.state.trainerSessionId = ov.active_session_id;
        if (!T.openSession(session)) loadOverview();
      });
    }
    var banner = byId("trReviewBanner");
    if (banner) {
      banner.addEventListener("click", function () {
        App.haptic("light");
        App.state.trainerProgressSection = "review";
        activate("progress", { scroll: true });
      });
    }
  }

  /* =====================================================================
   *  ВКЛАДКИ
   * ===================================================================== */

  /** Модуль содержимого вкладки или null («Сегодня» рисуется здесь). */
  function moduleOf(seg) {
    var name = MODULES[seg];
    var mod = name ? window[name] : null;
    return mod && typeof mod.mount === "function" ? mod : null;
  }

  /**
   * Наполняет панель вкладки. keep=true — возврат из карточки упражнения:
   * модуль берёт данные из своего кэша, а не грузит заново.
   */
  function mountPane(seg, keep) {
    var panel = byId("trPanel-" + seg);
    if (!panel) return;
    state.panes[seg] = { mounted: true, version: T.cache.version };

    if (seg === "today") {
      panel.innerHTML = '<div id="trBody"></div>';
      // overview грузится при каждом заходе; здесь догружаем, только если
      // данные тренера успели измениться, пока была открыта другая вкладка.
      if (!state.loading && state.overviewVersion !== T.cache.version) loadOverview();
      else renderTodayPane();
      return;
    }

    var mod = moduleOf(seg);
    if (!mod) {
      panel.innerHTML = T.errorCard(pick("Раздел не загрузился. Обновите приложение.", "This section failed to load. Please reload the app."));
      return;
    }
    try {
      mod.mount(panel, {
        keep: !!keep,
        onPaywall: showPaywall,
        onReload: reloadSection
      });
    } catch (e) {
      console.error("Ошибка монтирования вкладки " + seg, e);
      panel.innerHTML = T.errorCard(T.errMessage(e));
    }
  }

  /**
   * Снимает вкладку: модуль гасит таймеры и отбрасывает запросы в полёте.
   * Разметку не трогаем — её заменит монтирование или очистит App.navigate.
   */
  function unmountPane(seg) {
    var pane = state.panes[seg];
    if (!pane || !pane.mounted) return;
    pane.mounted = false;
    if (seg === "today") {
      state.nutritionReq++;
      state.recoveryBusy = false;
      return;
    }
    var mod = moduleOf(seg);
    if (mod && typeof mod.unmount === "function") {
      try {
        mod.unmount();
      } catch (e) {
        console.error("Ошибка размонтирования вкладки " + seg, e);
      }
    }
  }

  function unmountAll() {
    for (var i = 0; i < SEGMENTS.length; i++) unmountPane(SEGMENTS[i].key);
  }

  /**
   * Открывает вкладку: подсветка, показ панели, монтирование при первом
   * открытии. Шапку и полосу вкладок не трогает.
   * @param {string} segment ключ вкладки
   * @param {object} [opts] {keep: взять данные из кэша модуля,
   *                         scroll: вернуть прокрутку к началу раздела}
   */
  function activate(segment, opts) {
    opts = opts || {};
    if (!state.viewEl || !byId("trSegBody")) return;
    var seg = T.normSegment(segment);
    var prev = state.segment;
    // Уходящая вкладка была на экране, пока в ней меняли данные (применили
    // разбор, архивировали программу), и уже показывает результат своих же
    // действий. Отмечаем её актуальной, иначе на обратном переключении она
    // без нужды перезагрузилась бы со скелетоном.
    if (prev && prev !== seg && state.panes[prev] && state.panes[prev].mounted) {
      state.panes[prev].version = T.cache.version;
    }
    state.segment = seg;
    App.state.trainerSegment = seg;

    var tabs = state.viewEl.querySelectorAll(".tr-tab");
    for (var i = 0; i < tabs.length; i++) {
      var on = tabs[i].getAttribute("data-seg") === seg;
      tabs[i].classList.toggle("is-active", on);
      tabs[i].setAttribute("aria-selected", on ? "true" : "false");
      // Роуминговый tabindex: Tab попадает только на выбранную вкладку,
      // между вкладками ходят стрелками (см. onTabKey).
      tabs[i].setAttribute("tabindex", on ? "0" : "-1");
    }
    for (var j = 0; j < SEGMENTS.length; j++) {
      var panel = byId("trPanel-" + SEGMENTS[j].key);
      if (panel) panel.hidden = SEGMENTS[j].key !== seg;
    }

    var pane = state.panes[seg];
    if (pane && pane.mounted && pane.version !== T.cache.version) {
      unmountPane(seg);
      pane = null;
    }
    var fresh = !pane || !pane.mounted;
    if (fresh) mountPane(seg, opts.keep);

    // К началу раздела: шапка и полоса вкладок снова на виду.
    if (opts.scroll) App.scrollTop();

    // Уже смонтированная вкладка флаг «открыть на разборе» сама не прочитает
    // (баннер «Разбор готов» на «Сегодня») — передаём явно. Строго после
    // прокрутки наверх, иначе она отменила бы прокрутку к разбору.
    if (!fresh && seg === "progress" && App.state.trainerProgressSection === "review") {
      var progress = moduleOf("progress");
      App.state.trainerProgressSection = null;
      if (progress && typeof progress.focusReview === "function") progress.focusReview();
    }
    updateStuck();
  }

  function onTabClick(ev) {
    var btn = ev.target.closest ? ev.target.closest(".tr-tab") : null;
    if (!btn) return;
    var seg = btn.getAttribute("data-seg");
    if (seg === state.segment) {
      // Повторный тап по выбранной вкладке — наверх, как в таббаре.
      App.scrollTop();
      return;
    }
    App.haptic("selection");
    activate(seg, { scroll: true });
  }

  /** Стрелки/Home/End по полосе вкладок (шаблон ARIA tabs). */
  function onTabKey(ev) {
    var keys = { ArrowLeft: -1, ArrowRight: 1, Home: "first", End: "last" };
    if (!Object.prototype.hasOwnProperty.call(keys, ev.key)) return;
    var idx = 0;
    for (var i = 0; i < SEGMENTS.length; i++) {
      if (SEGMENTS[i].key === state.segment) idx = i;
    }
    var step = keys[ev.key];
    if (step === "first") idx = 0;
    else if (step === "last") idx = SEGMENTS.length - 1;
    else idx = (idx + step + SEGMENTS.length) % SEGMENTS.length;
    ev.preventDefault();
    var seg = SEGMENTS[idx].key;
    activate(seg, { scroll: true });
    var btn = byId("trTab-" + seg);
    if (btn) btn.focus();
  }

  /**
   * Полная перезагрузка раздела на указанной вкладке. Нужна после действий,
   * меняющих всё сразу (архивировали программу, начали новую): точечно
   * обновлять четыре вкладки и шапку дороже и ненадёжнее.
   */
  function reloadSection(segment) {
    T.cache.invalidate();
    App.state.trainerSegment = T.normSegment(segment || state.segment);
    App.navigate("trainer");
  }

  /**
   * Тонкая линия под полосой вкладок, когда та прилипла к верху: без неё
   * содержимое при прокрутке «уходит под» полосу без видимой границы.
   * Считаем по положению элемента — событий «прилипло» у sticky нет.
   */
  function updateStuck() {
    var bar = byId("trTabsBar");
    if (!bar) return;
    var top = parseFloat(window.getComputedStyle(bar).top) || 0;
    var stuck = pageYOffsetSafe() > 0 && bar.getBoundingClientRect().top <= top + 1;
    bar.classList.toggle("is-stuck", stuck);
  }

  function pageYOffsetSafe() {
    return window.pageYOffset || (document.documentElement && document.documentElement.scrollTop) || 0;
  }

  // Отдельный кадр (requestAnimationFrame) не заводим: браузер и так шлёт
  // scroll не чаще раза за кадр, а проверка — один замер без записи в DOM
  // (classList.toggle с тем же значением стиль не пересчитывает).
  function onScroll() {
    updateStuck();
  }

  function stopStuckWatch() {
    window.removeEventListener("scroll", onScroll);
  }

  /**
   * Полоса вкладок и кнопка настроек живут в каркасе, а не в панелях:
   * вешаем обработчики один раз при onShow.
   */
  function bindShell() {
    if (!state.viewEl) return;
    var list = state.viewEl.querySelector(".tr-tabs");
    if (list) {
      list.addEventListener("click", onTabClick);
      list.addEventListener("keydown", onTabKey);
    }
    var settings = byId("trHeadSettings");
    if (settings) {
      settings.addEventListener("click", function () {
        App.haptic("light");
        openSettings();
      });
    }
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* =====================================================================
   *  САМОЧУВСТВИЕ (совет по восстановлению)
   * ===================================================================== */

  function bindRecovery() {
    var box = byId("trRecovery");
    if (!box) return;
    var zones = box.querySelector(".tr-recovery__zones");
    var go = byId("trRecGo");
    if (zones) {
      zones.addEventListener("click", function (ev) {
        var btn = ev.target.closest(".tr-zone");
        if (!btn) return;
        state.recoveryZone = btn.getAttribute("data-zone");
        App.haptic("selection");
        var all = zones.querySelectorAll(".tr-zone");
        for (var i = 0; i < all.length; i++) {
          var on = all[i].getAttribute("data-zone") === state.recoveryZone;
          all[i].classList.toggle("chip--active", on);
          all[i].setAttribute("aria-pressed", on ? "true" : "false");
        }
        if (go) go.disabled = false;
      });
    }
    if (go) {
      go.addEventListener("click", function () {
        var input = byId("trRecComplaint");
        requestRecovery(state.recoveryZone, input ? input.value : "", go);
      });
    }
  }

  function requestRecovery(zone, complaint, btn) {
    if (!zone || state.recoveryBusy) return;
    if (!(App.api && typeof App.api.getRecoveryAdvice === "function")) return;
    var out = byId("trRecResult");
    state.recoveryBusy = true;
    if (btn) {
      btn.disabled = true;
      btn.textContent = pick("Думаем…", "Thinking…");
    }
    if (out) out.innerHTML = '<div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line short"></div>';
    App.haptic("light");

    App.api
      .getRecoveryAdvice({ zone: zone, complaint: (complaint || "").trim() || null })
      .then(function (res) {
        App.haptic("success");
        var box = byId("trRecResult");
        if (!box || !res) return;
        box.innerHTML = recoveryResultHtml(res);
        // Карточка лежит в середине длинной страницы: без подкрутки ответ
        // появляется ниже края экрана, и человек его просто не видит.
        try {
          box.scrollIntoView({ behavior: "smooth", block: "nearest" });
        } catch (e) {
          /* старый WebView без опций scrollIntoView — не критично */
        }
      })
      .catch(function (err) {
        App.haptic("error");
        var box = byId("trRecResult");
        if (!box) return;
        box.innerHTML =
          '<p class="rec-error">' +
          esc(T.errMessage(err, pick("Не удалось получить совет", "Failed to get advice"))) +
          "</p>";
      })
      .finally(function () {
        state.recoveryBusy = false;
        var b = byId("trRecGo");
        if (b) {
          b.disabled = false;
          b.textContent = pick("Получить совет", "Get advice");
        }
      });
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
        if (!T.openSession(session)) loadOverview();
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
   * Загружает overview: подзаголовок шапки, сводка для других экранов,
   * маршрутизация в анкету и содержимое вкладки «Сегодня» (если открыта).
   * Грузится при любой открытой вкладке: шапка общая для всех, а человек
   * без анкеты должен попасть в анкету, а не в пустую библиотеку.
   */
  function loadOverview() {
    if (!state.viewEl || state.loading) return;
    state.loading = true;
    state.overview = null;
    state.overviewErr = null;
    state.overviewVersion = T.cache.version;
    var reqId = ++state.ovReq;
    renderTodayPane();

    App.api
      .trainerOverview()
      .then(function (ov) {
        if (reqId !== state.ovReq) return; // раздел закрыт или запрос устарел
        state.loading = false;
        if (!state.viewEl) return;
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
        setSubtitle(subtitleFor(ov));
        renderTodayPane();
      })
      .catch(function (err) {
        if (reqId !== state.ovReq) return;
        state.loading = false;
        if (!state.viewEl) return;
        if (err && err.status === 402) {
          showPaywall();
          return;
        }
        state.overviewErr = err || {};
        renderTodayPane();
      });
  }

  /**
   * Paywall раздела. Анкету оставляем открытой: ответы на девять вопросов —
   * единственное, что человек может сделать до оплаты, и именно они дают
   * понять, за что он платит. Всё остальное закрыто бэкендом (каждый
   * маршрут /trainer/* требует подписки), поэтому дальше анкеты без неё
   * пройти нельзя.
   */
  function showPaywall() {
    if (!state.viewEl) return;
    // Paywall заменяет весь экран: вкладки гасим, чтобы их запросы и
    // таймеры не дорисовывали что-то в уже несуществующие панели.
    unmountAll();
    stopStuckWatch();
    state.segment = null;
    var opts = T.paywallOpts();
    opts.extraLabel = pick("Заполнить анкету", "Fill in the profile");
    opts.onExtra = function () {
      App.state.trainerEdit = false;
      App.navigate("trainer-onboarding");
    };
    T.paywall(state.viewEl, opts);
  }

  /* =====================================================================
   *  КОНТРОЛЛЕР
   * ===================================================================== */

  var controller = {
    onShow: function (viewEl) {
      state.viewEl = viewEl;
      state.segment = null;
      state.panes = {};
      // Первый вход извне раздела — запоминаем, куда возвращаться.
      if (!App.state.trainerOrigin) App.state.trainerOrigin = "today";
      // Без подписки не ходим за overview вовсе: ответ всё равно 402,
      // а лишний запрос задерживает показ paywall на время сети.
      if (!T.isPro()) {
        showPaywall();
        return;
      }
      // Возврат из карточки упражнения: открытая вкладка берёт данные из
      // кэша, чтобы человек вернулся к тому же списку и той же прокрутке.
      var keep = T.isReturning();
      viewEl.innerHTML = shellHtml(state.subtitle);
      bindShell();
      loadOverview();
      activate(App.state.trainerSegment, { keep: keep });
    },

    onHide: function () {
      unmountAll();
      stopStuckWatch();
      state.viewEl = null;
      state.segment = null;
      state.loading = false;
      state.ovReq++;
      state.starting = false;
      state.nutritionReq++;
      state.recoveryBusy = false;
      T.closeSheet(true);
    },

    /**
     * Открывает вкладку на месте, без App.navigate. Вызывается через
     * Trainer.openSegment из обработчиков внутри раздела. Если раздел
     * сейчас закрыт paywall'ом, только запоминаем выбор.
     */
    switchTo: function (segment) {
      App.state.trainerSegment = T.normSegment(segment);
      if (!state.viewEl || !byId("trSegBody")) return;
      activate(segment, { scroll: true });
    },

    /** Принудительное обновление (для других страниц после изменений). */
    refresh: function () {
      T.cache.invalidate();
      if (state.viewEl && byId("trSegBody")) reloadSection(state.segment);
    }
  };

  window.PageTrainer = controller;
  App.registerPage("trainer", controller);
})();
