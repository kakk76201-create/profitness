/*
 * page-today.js — главный экран «Сегодня».
 *
 * Регистрируется как App.registerPage("today", {...}); публичная ссылка —
 * window.PageToday. Это стартовый экран приложения.
 *
 * ЗАЧЕМ ОН НУЖЕН. До этого приложение открывалось дневником питания, а
 * тренер жил подстраницей за карточкой внутри другого раздела. Человек,
 * открыв приложение, не получал ответа на единственный вопрос, который у
 * него есть: «что у меня сейчас и что делать дальше». Этот экран отвечает
 * на него одним взглядом и уводит в нужный раздел одним касанием.
 *
 * ЧТО ПОКАЗЫВАЕТ (сверху вниз, по убыванию важности):
 *   1. Калории за день — кольцо «съедено из нормы» и три полосы БЖУ.
 *   2. Быстрые действия с едой — «Снять еду» и «Добавить вручную». Камеры
 *      в таббаре больше нет (там теперь сама «Сегодня»), а записать еду
 *      человек чаще всего хочет именно отсюда, глядя на остаток калорий.
 *   3. Тренировка дня — план, отдых, незаконченная сессия или «сделано»,
 *      с единственной кнопкой действия.
 *   4. Вес и серия тренировок — две компактные плитки.
 *
 * ЧЕГО ЗДЕСЬ НЕТ И НЕ ДОЛЖНО БЫТЬ: форм ввода, списков, настроек. Это
 * витрина состояния и развилка, а не рабочая поверхность. Всё, что требует
 * работы, живёт в своём разделе: быстрые действия лишь открывают камеру или
 * лист ручного ввода в «Питании» (App.state.diaryOpenSheet), а переходы в
 * тренера выбирают нужный раздел через App.state.trainerSegment.
 *
 * ДАННЫЕ. Три независимых запроса идут параллельно, и каждый блок рисуется,
 * как только пришёл его ответ: экран не ждёт самого медленного. Сбой любого
 * запроса гасит только свой блок.
 */
(function () {
  "use strict";

  function pick(ru, en) {
    if (window.App && typeof App.pick === "function") return App.pick(ru, en);
    return ru;
  }

  function esc(s) {
    return App.escapeHtml(s == null ? "" : String(s));
  }

  function icon(name, opts) {
    return App.icon ? App.icon(name, opts) : "";
  }

  // Геометрия кольца калорий. Радиус подобран так, чтобы кольцо вместе с
  // числом внутри занимало примерно треть экрана телефона и оставалось
  // читаемым на 360px.
  var RING_R = 52;
  var RING_LEN = 2 * Math.PI * RING_R;

  var state = {
    viewEl: null,
    day: null,        // ответ /diary/{date}
    overview: null,   // ответ /trainer/overview (или null, если недоступен)
    weight: null,     // ответ /weight/history
    loading: false
  };

  /* =====================================================================
   *  ВСПОМОГАТЕЛЬНОЕ
   * ===================================================================== */

  /**
   * Приветствие по времени суток. Мелочь, но именно она отличает «тренера»
   * от формы ввода: приложение обращается к человеку, а не к записи в базе.
   */
  function greeting() {
    var h = new Date().getHours();
    if (h < 5) return pick("Доброй ночи", "Good night");
    if (h < 12) return pick("Доброе утро", "Good morning");
    if (h < 18) return pick("Добрый день", "Good afternoon");
    return pick("Добрый вечер", "Good evening");
  }

  /** Сегодняшняя дата словами: «среда, 16 сентября». */
  function todayLabel() {
    var d = new Date();
    var days = pick(
      "воскресенье,понедельник,вторник,среда,четверг,пятница,суббота",
      "Sunday,Monday,Tuesday,Wednesday,Thursday,Friday,Saturday"
    ).split(",");
    var months = pick(
      "января,февраля,марта,апреля,мая,июня,июля,августа,сентября,октября,ноября,декабря",
      "January,February,March,April,May,June,July,August,September,October,November,December"
    ).split(",");
    var day = days[d.getDay()];
    var text = App.lang === "en"
      ? day + ", " + months[d.getMonth()] + " " + d.getDate()
      : day + ", " + d.getDate() + " " + months[d.getMonth()];
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  /** Кольцо прогресса: доля 0..1 (сверх нормы не «перекручиваем»). */
  function ringHtml(fraction, over) {
    var f = Math.max(0, Math.min(1, fraction || 0));
    var offset = RING_LEN * (1 - f);
    return (
      '<svg class="td-ring__svg" viewBox="0 0 120 120" aria-hidden="true">' +
      '<circle class="td-ring__track" cx="60" cy="60" r="' + RING_R + '"/>' +
      '<circle class="td-ring__value' + (over ? " td-ring__value--over" : "") + '" ' +
      'cx="60" cy="60" r="' + RING_R + '" ' +
      'stroke-dasharray="' + RING_LEN.toFixed(1) + '" ' +
      'stroke-dashoffset="' + offset.toFixed(1) + '"/>' +
      "</svg>"
    );
  }

  /** Полоса одного макронутриента. */
  function macroHtml(labelRu, labelEn, eaten, target, mod) {
    var has = target > 0;
    var pct = has ? Math.min(100, Math.round((eaten / target) * 100)) : 0;
    var value = has
      ? App.fmt(Math.round(eaten)) + " / " + App.fmt(Math.round(target))
      : App.fmt(Math.round(eaten));
    return (
      '<div class="td-macro td-macro--' + mod + '">' +
      '<div class="td-macro__head">' +
      '<span class="td-macro__label">' + esc(pick(labelRu, labelEn)) + "</span>" +
      '<span class="td-macro__value">' + esc(value) + " " + esc(pick("г", "g")) + "</span>" +
      "</div>" +
      '<div class="td-macro__track">' +
      '<div class="td-macro__fill" style="width:' + pct + '%"></div>' +
      "</div>" +
      "</div>"
    );
  }

  /* =====================================================================
   *  БЛОК: КАЛОРИИ
   * ===================================================================== */

  function caloriesHtml() {
    var day = state.day;
    if (!day) {
      return '<section class="card td-card"><div class="skeleton skeleton-block td-skel-ring"></div></section>';
    }

    var profile = (App.state && App.state.profile) || {};
    var eaten = Number(day.total_calories) || 0;
    var burned = Number(day.total_burned) || 0;
    var goal = Number(day.daily_goal_kcal) || 0;

    // Цель не задана — вместо бессмысленного кольца зовём её задать.
    if (!goal) {
      return (
        '<section class="card td-card td-card--cta">' +
        '<div class="td-empty">' +
        '<span class="td-empty__icon">' + icon("target", { size: 28 }) + "</span>" +
        '<h2 class="td-empty__title">' + esc(pick("Задайте норму калорий", "Set your calorie goal")) + "</h2>" +
        '<p class="td-empty__text">' +
        esc(pick(
          "Без неё не посчитать, сколько осталось на день и как идёт прогресс.",
          "Without it we cannot show what is left for the day or how you are progressing."
        )) +
        "</p>" +
        '<button type="button" class="btn btn--cta" id="tdSetGoal">' +
        esc(pick("Настроить", "Set up")) +
        "</button>" +
        "</div>" +
        "</section>"
      );
    }

    var left = goal - eaten;
    var over = left < 0;
    var ringText = over ? "+" + App.fmt(Math.abs(Math.round(left))) : App.fmt(Math.round(left));

    var burnedRow = burned > 0
      ? '<div class="td-cal__burned">' +
        icon("flame", { size: 14 }) +
        "<span>" + esc(pick("Сожжено ", "Burned ") + App.fmt(Math.round(burned)) + " " + pick("ккал", "kcal")) + "</span>" +
        "</div>"
      : "";

    return (
      '<section class="card td-card td-cal">' +
      '<div class="td-ring">' +
      ringHtml(eaten / goal, over) +
      '<div class="td-ring__center">' +
      '<span class="td-ring__num">' + esc(ringText) + "</span>" +
      '<span class="td-ring__cap">' +
      esc(over ? pick("сверх нормы", "over goal") : pick("осталось", "left")) +
      "</span>" +
      "</div>" +
      "</div>" +
      '<div class="td-cal__sum">' +
      esc(App.fmt(Math.round(eaten)) + " " + pick("из", "of") + " " + App.fmt(goal) + " " + pick("ккал", "kcal")) +
      "</div>" +
      burnedRow +
      '<div class="td-macros">' +
      macroHtml("Белки", "Protein", Number(day.total_proteins) || 0, Number(profile.target_proteins) || 0, "p") +
      macroHtml("Жиры", "Fat", Number(day.total_fats) || 0, Number(profile.target_fats) || 0, "f") +
      macroHtml("Углеводы", "Carbs", Number(day.total_carbs) || 0, Number(profile.target_carbs) || 0, "c") +
      "</div>" +
      "</section>"
    );
  }

  /* =====================================================================
   *  БЛОК: БЫСТРЫЕ ДЕЙСТВИЯ С ЕДОЙ
   *  Второстепенные по весу кнопки (контур, не заливка): главное на экране —
   *  кольцо калорий и кнопка тренировки, а эти две — короткий путь в работу,
   *  который не должен спорить с ними за внимание.
   * ===================================================================== */

  function quickHtml() {
    return (
      '<div class="td-quick">' +
      '<button type="button" class="td-quick__btn" id="tdQuickScan">' +
      icon("camera", { size: 18 }) +
      "<span>" + esc(pick("Снять еду", "Snap food")) + "</span>" +
      "</button>" +
      '<button type="button" class="td-quick__btn" id="tdQuickManual">' +
      icon("edit", { size: 18 }) +
      "<span>" + esc(pick("Добавить вручную", "Add manually")) + "</span>" +
      "</button>" +
      "</div>"
    );
  }

  /* =====================================================================
   *  БЛОК: ТРЕНИРОВКА ДНЯ
   * ===================================================================== */

  function workoutHtml() {
    // Тренер недоступен (нет подписки или запрос не прошёл) — зовём внутрь,
    // но честно, без имитации данных.
    if (state.overview === "locked") {
      return (
        '<section class="card td-card td-card--cta">' +
        '<div class="td-empty">' +
        '<span class="td-empty__icon">' + icon("dumbbell", { size: 28 }) + "</span>" +
        '<h2 class="td-empty__title">' + esc(pick("Персональные тренировки", "Personal training")) + "</h2>" +
        '<p class="td-empty__text">' +
        esc(pick(
          "Программа под вашу цель, уровень и инвентарь. Тренер ведёт занятие и подстраивает нагрузку.",
          "A program for your goal, level and equipment. The coach runs the session and adapts the load."
        )) +
        "</p>" +
        '<button type="button" class="btn btn--cta" id="tdOpenTrainer">' +
        esc(pick("Посмотреть", "Take a look")) +
        "</button>" +
        "</div>" +
        "</section>"
      );
    }

    if (!state.overview) {
      return '<section class="card td-card"><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line short"></div></section>';
    }

    var ov = state.overview;
    var today = ov.today || {};
    var day = today.day || null;

    var head, title, meta, action, mod = "";

    if (ov.active_session_id) {
      // Незаконченная сессия важнее всего остального: человек в процессе.
      mod = " td-workout--active";
      head = pick("Тренировка идёт", "Workout in progress");
      title = (ov.today && ov.today.active_session && ov.today.active_session.title) ||
        (day && day.title) || pick("Тренировка", "Workout");
      meta = "";
      action = pick("Продолжить", "Continue");
    } else if (today.kind === "planned" && day) {
      head = pick("Тренировка сегодня", "Workout today");
      title = day.title || pick("Тренировка", "Workout");
      var parts = [];
      if (day.duration_min) parts.push(day.duration_min + " " + pick("мин", "min"));
      var exCount = (day.exercises || []).length;
      if (exCount) parts.push(exCount + " " + exWord(exCount));
      meta = parts.join(" · ");
      action = pick("Начать", "Start");
    } else if (today.kind === "done" && day) {
      mod = " td-workout--done";
      head = pick("Сегодня сделано", "Done today");
      title = day.title || pick("Тренировка", "Workout");
      meta = today.next_date
        ? pick("Следующая — ", "Next — ") + window.Trainer.shortDate(today.next_date)
        : "";
      action = null;
    } else if (today.kind === "no_program") {
      head = pick("Тренировки", "Training");
      title = pick("Программы пока нет", "No program yet");
      meta = pick("Соберём её под вашу цель за пару минут", "We will build one for your goal in a couple of minutes");
      action = pick("Собрать программу", "Build a program");
    } else {
      // Отдых или неделя закрыта.
      mod = " td-workout--rest";
      head = pick("Сегодня", "Today");
      title = today.kind === "week_done"
        ? pick("Неделя закрыта", "Week complete")
        : pick("День отдыха", "Rest day");
      meta = today.next_date
        ? pick("Следующая — ", "Next — ") + window.Trainer.shortDate(today.next_date) +
          (day && day.title ? ": " + day.title : "")
        : pick("Отдых — часть плана", "Rest is part of the plan");
      action = null;
    }

    return (
      '<section class="card td-card td-workout' + mod + '" id="tdWorkout">' +
      '<div class="td-card__head">' + esc(head) + "</div>" +
      '<h2 class="td-workout__title">' + esc(title) + "</h2>" +
      (meta ? '<p class="td-workout__meta">' + esc(meta) + "</p>" : "") +
      (action
        ? '<button type="button" class="btn btn--cta td-workout__btn" id="tdWorkoutGo">' +
          esc(action) + "</button>"
        : '<button type="button" class="td-workout__link" id="tdWorkoutGo">' +
          esc(pick("Открыть тренировку", "Open training")) +
          icon("chevron", { size: 16 }) +
          "</button>") +
      "</section>"
    );
  }

  /** Форма слова «упражнение» по числу. */
  function exWord(n) {
    if (App.lang === "en") return n === 1 ? "exercise" : "exercises";
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return "упражнение";
    if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return "упражнения";
    return "упражнений";
  }

  /* =====================================================================
   *  БЛОК: ВЕС И СЕРИЯ
   * ===================================================================== */

  function tilesHtml() {
    var tiles = "";

    // Плитка веса.
    var w = state.weight;
    if (w && w.latest != null) {
      var change = w.change_kg;
      var changeText = "";
      var changeMod = "";
      if (change != null && Math.abs(change) >= 0.05) {
        changeText = (change > 0 ? "+" : "−") + App.fmt(Math.abs(change).toFixed(1)) + " " + pick("кг", "kg");
        changeMod = change > 0 ? " td-tile__delta--up" : " td-tile__delta--down";
      } else if (change != null) {
        changeText = pick("без изменений", "no change");
      }
      tiles +=
        '<button type="button" class="td-tile" id="tdWeight">' +
        '<span class="td-tile__label">' + icon("scale", { size: 14 }) + esc(pick("Вес", "Weight")) + "</span>" +
        '<span class="td-tile__value">' + esc(App.fmt(w.latest) + " " + pick("кг", "kg")) + "</span>" +
        (changeText ? '<span class="td-tile__delta' + changeMod + '">' + esc(changeText) + "</span>" : "") +
        "</button>";
    }

    // Плитка серии тренировок.
    var ov = state.overview;
    if (ov && ov !== "locked" && ov.streak) {
      var s = ov.streak;
      var weeks = Number(s.weeks) || 0;
      var done = Number(s.this_week_done) || 0;
      var goal = Number(s.this_week_goal) || 0;
      tiles +=
        '<button type="button" class="td-tile" id="tdStreak">' +
        '<span class="td-tile__label">' + icon("flame", { size: 14 }) + esc(pick("Серия", "Streak")) + "</span>" +
        '<span class="td-tile__value">' +
        esc(weeks ? weeks + " " + weekWord(weeks) : pick("нет", "none")) +
        "</span>" +
        (goal
          ? '<span class="td-tile__delta">' + esc(done + pick(" из ", " of ") + goal + pick(" на неделе", " this week")) + "</span>"
          : "") +
        "</button>";
    }

    if (!tiles) return "";
    return '<div class="td-tiles">' + tiles + "</div>";
  }

  function weekWord(n) {
    if (App.lang === "en") return n === 1 ? "week" : "weeks";
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return "неделя";
    if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return "недели";
    return "недель";
  }

  /**
   * Открывает тренера на нужном разделе. Разделы тренера (сегодня, программа,
   * прогресс, упражнения) переключаются внутри одного экрана, и выбранный
   * раздел тренер помнит между заходами. Поэтому раздел задаём ЯВНО: иначе
   * тап по «Серии» открыл бы тот раздел, где человек был в прошлый раз.
   * @param {string} segment "today" | "program" | "progress" | "exercises"
   */
  function openTrainerSegment(segment) {
    if (App.state) App.state.trainerSegment = segment;
    App.navigate("trainer");
  }

  /* =====================================================================
   *  РЕНДЕР
   * ===================================================================== */

  function shellHtml() {
    return (
      '<section class="page td-page">' +
      '<header class="td-head">' +
      '<h1 class="td-head__greeting">' + esc(greeting()) + "</h1>" +
      '<p class="td-head__date">' + esc(todayLabel()) + "</p>" +
      "</header>" +
      '<div id="tdBody"></div>' +
      "</section>"
    );
  }

  function render() {
    if (!state.viewEl) return;
    var body = document.getElementById("tdBody");
    if (!body) return;
    body.innerHTML = caloriesHtml() + quickHtml() + workoutHtml() + tilesHtml();
    bind();
  }

  function bind() {
    var goal = document.getElementById("tdSetGoal");
    if (goal) {
      goal.addEventListener("click", function () {
        App.haptic("light");
        if (App.state) App.state.openProfileFold = true;
        App.navigate("account");
      });
    }

    // Камера — экран-задача без таббара: запоминаем, куда вернёт её «Закрыть».
    var quickScan = document.getElementById("tdQuickScan");
    if (quickScan) {
      quickScan.addEventListener("click", function () {
        App.haptic("light");
        if (App.state) App.state.scanOrigin = "today";
        App.navigate("scan");
      });
    }

    // Ручной ввод живёт листом в «Питании»: просим дневник открыть его сразу.
    var quickManual = document.getElementById("tdQuickManual");
    if (quickManual) {
      quickManual.addEventListener("click", function () {
        App.haptic("light");
        if (App.state) App.state.diaryOpenSheet = "manual";
        App.navigate("diary");
      });
    }

    var openTrainer = document.getElementById("tdOpenTrainer");
    if (openTrainer) {
      openTrainer.addEventListener("click", function () {
        App.haptic("light");
        openTrainerSegment("today");
      });
    }

    var go = document.getElementById("tdWorkoutGo");
    if (go) {
      go.addEventListener("click", function () {
        App.haptic("medium");
        var ov = state.overview;
        // Незаконченную сессию открываем сразу, минуя главную тренера:
        // человек уже в процессе, лишний экран здесь только мешает.
        if (ov && ov !== "locked" && ov.active_session_id && window.Trainer) {
          if (App.state) App.state.trainerSessionId = ov.active_session_id;
          window.Trainer.openSession(
            (ov.today && ov.today.active_session) || null
          );
          return;
        }
        openTrainerSegment("today");
      });
    }

    var weight = document.getElementById("tdWeight");
    if (weight) {
      weight.addEventListener("click", function () {
        App.haptic("light");
        App.navigate("account");
      });
    }

    // Серия — это прогресс тренировок: открываем тренера сразу на нём.
    var streak = document.getElementById("tdStreak");
    if (streak) {
      streak.addEventListener("click", function () {
        App.haptic("light");
        openTrainerSegment("progress");
      });
    }
  }

  /* =====================================================================
   *  ЗАГРУЗКА
   *  Три запроса идут параллельно и рисуются по мере готовности: экран не
   *  ждёт самого медленного из них. Ошибка каждого гасит только свой блок.
   * ===================================================================== */

  function load() {
    if (state.loading) return;
    state.loading = true;

    var date = App.todayStr();

    App.api
      .getDiary(date)
      .then(function (day) {
        state.day = day;
        render();
      })
      .catch(function () {
        state.day = {};
        render();
      });

    App.api
      .trainerOverview()
      .then(function (ov) {
        state.overview = ov;
        render();
      })
      .catch(function () {
        // 402 (нет подписки) и сетевой сбой выглядят для экрана одинаково:
        // данных нет, показываем приглашение в раздел.
        state.overview = "locked";
        render();
      });

    App.api
      .getWeightHistory(30)
      .then(function (w) {
        state.weight = w;
        render();
      })
      .catch(function () {
        state.weight = null;
      })
      .then(function () {
        state.loading = false;
      });
  }

  /* =====================================================================
   *  КОНТРОЛЛЕР
   * ===================================================================== */

  var controller = {
    onShow: function (viewEl) {
      state.viewEl = viewEl;
      state.day = null;
      state.overview = null;
      state.weight = null;
      state.loading = false;

      viewEl.innerHTML = shellHtml();
      render();
      load();
    },

    onHide: function () {
      state.viewEl = null;
      state.loading = false;
    }
  };

  window.PageToday = controller;
  App.registerPage("today", controller);
})();
