/*
 * page-onboarding.js — экран первичной настройки (мастер онбординга).
 *
 * Регистрирует контроллер через App.registerPage("onboarding", {...}).
 * Показывается на первом запуске, когда у пользователя ещё нет цели по
 * калориям (profile.daily_goal_kcal == null) — см. маршрутизацию в App.init.
 *
 * Полноэкранный 3-шаговый мастер:
 *   Шаг 1 — пол (ОБЯЗАТЕЛЬНО, без значения по умолчанию), вес, рост, возраст.
 *   Шаг 2 — уровень активности + цель питания (loss/maintain/gain).
 *   Шаг 3 — необязательное включение вечерней сводки в 21:00 и финиш.
 *
 * По завершении:
 *   1) App.api.calculateGoal(...) — считает и сохраняет цель на сервере;
 *   2) App.api.saveProfile(...) — фиксирует введённые параметры в профиле
 *      (чтобы daily_goal_kcal / target_* точно сохранились);
 *   3) обновляет App.state.profile и переходит на «Рацион» (diary).
 *
 * «Пропустить» ТОЖЕ СОХРАНЯЕТ профиль: либо считает цель по уже введённым
 * параметрам, либо ставит разумную норму по умолчанию. Иначе мастер выходил
 * бесконечным — App.init решает по daily_goal_kcal == null и открывал его
 * заново при каждом запуске, сколько бы раз человек ни нажал «Пропустить».
 *
 * Локализация: весь видимый текст через App.pick(ru, en) НА МОМЕНТ рендера.
 * Классы: onb-* (onb-wizard, onb-step, onb-title, onb-field, onb-actions,
 * onb-next, onb-skip, onb-dots ...). Реиспользуем базовые field/btn классы.
 */
(function () {
  "use strict";

  // Короткий псевдоним локализации — все строки проходят через L на момент рендера.
  function L(ru, en) {
    return App.pick(ru, en);
  }

  // Общее число шагов мастера.
  var STEP_COUNT = 3;

  // Варианты уровня активности (значение — коэффициент TDEE, как в аккаунте).
  var ACTIVITY_OPTIONS = [
    { value: 1.2, ru: "Минимальная (сидячий образ жизни)", en: "Minimal (sedentary lifestyle)" },
    { value: 1.375, ru: "Лёгкая (1-3 тренировки в неделю)", en: "Light (1-3 workouts per week)" },
    { value: 1.55, ru: "Средняя (3-5 тренировок в неделю)", en: "Moderate (3-5 workouts per week)" },
    { value: 1.725, ru: "Высокая (6-7 тренировок в неделю)", en: "High (6-7 workouts per week)" },
    { value: 1.9, ru: "Очень высокая (тяжёлый физический труд)", en: "Very high (hard physical labor)" }
  ];

  // Варианты цели питания (diet_goal). value — то, что уходит на сервер.
  var DIET_GOAL_OPTIONS = [
    { value: "loss", ru: "Похудение", en: "Weight loss" },
    { value: "maintain", ru: "Поддержание", en: "Maintenance" },
    { value: "gain", ru: "Набор массы", en: "Muscle gain" }
  ];

  // Время вечерней сводки по умолчанию.
  var SUMMARY_TIME = "21:00";

  // Норма калорий, которую ставим при пропуске мастера, если параметров для
  // честного расчёта не хватило. Значение заведомо приблизительное — оно лишь
  // закрывает онбординг; человек меняет его в профиле одной кнопкой.
  var DEFAULT_GOAL_KCAL = 2000;

  /** Иконка из общего набора (js/icons.js). */
  function icon(name, opts) {
    return App.icon ? App.icon(name, opts) : "";
  }

  // Черновик введённых пользователем данных (переживает перерисовку шагов).
  var draft = null;

  // Ссылка на контейнер представления (#view), передаётся в onShow.
  var rootEl = null;

  /** Сбрасывает черновик к начальным значениям. */
  function resetDraft() {
    draft = {
      gender: null, // ОБЯЗАТЕЛЬНО выбрать (без значения по умолчанию)
      weight: "",
      height: "",
      age: "",
      activity_level: 1.375, // «Лёгкая» — разумное значение по умолчанию
      diet_goal: "maintain",
      summary_enabled: false,
      step: 1
    };
  }

  /* =====================================================================
   *  РЕНДЕР
   * ===================================================================== */

  /** Рисует индикатор шагов (точки). */
  function dotsHtml(step) {
    var out = "";
    for (var i = 1; i <= STEP_COUNT; i++) {
      out +=
        '<span class="onb-dot' +
        (i === step ? " onb-dot--active" : "") +
        (i < step ? " onb-dot--done" : "") +
        '"></span>';
    }
    return '<div class="onb-dots" aria-hidden="true">' + out + "</div>";
  }

  /** HTML шага 1: пол (обязательно), вес, рост, возраст. */
  function step1Html() {
    var maleSel = draft.gender === "male" ? " onb-choice--on" : "";
    var femaleSel = draft.gender === "female" ? " onb-choice--on" : "";

    return (
      '<div class="onb-step" data-step="1">' +
      '<h1 class="onb-title">' +
      App.escapeHtml(L("Расскажите о себе", "Tell us about you")) +
      "</h1>" +
      '<p class="onb-subtitle">' +
      App.escapeHtml(
        L(
          "Это нужно, чтобы рассчитать вашу дневную норму калорий.",
          "We need this to calculate your daily calorie goal."
        )
      ) +
      "</p>" +
      // Пол — обязательный выбор двумя кнопками (без предвыбора).
      '<div class="onb-field">' +
      '<span class="field__label">' +
      App.escapeHtml(L("Пол", "Gender")) +
      "</span>" +
      '<div class="onb-choices" id="onbGender">' +
      '<button type="button" class="onb-choice' +
      maleSel +
      '" data-gender="male">' +
      App.escapeHtml(L("Мужской", "Male")) +
      "</button>" +
      '<button type="button" class="onb-choice' +
      femaleSel +
      '" data-gender="female">' +
      App.escapeHtml(L("Женский", "Female")) +
      "</button>" +
      "</div>" +
      "</div>" +
      // Вес / рост / возраст.
      '<label class="field onb-field">' +
      '<span class="field__label">' +
      App.escapeHtml(L("Вес, кг", "Weight, kg")) +
      "</span>" +
      '<input class="field__input" id="onbWeight" type="number" inputmode="decimal" min="0" step="0.1" placeholder="70" value="' +
      App.escapeHtml(draft.weight) +
      '">' +
      "</label>" +
      '<label class="field onb-field">' +
      '<span class="field__label">' +
      App.escapeHtml(L("Рост, см", "Height, cm")) +
      "</span>" +
      '<input class="field__input" id="onbHeight" type="number" inputmode="decimal" min="0" step="0.1" placeholder="175" value="' +
      App.escapeHtml(draft.height) +
      '">' +
      "</label>" +
      '<label class="field onb-field">' +
      '<span class="field__label">' +
      App.escapeHtml(L("Возраст, лет", "Age, years")) +
      "</span>" +
      '<input class="field__input" id="onbAge" type="number" inputmode="numeric" min="0" step="1" placeholder="30" value="' +
      App.escapeHtml(draft.age) +
      '">' +
      "</label>" +
      "</div>"
    );
  }

  /** HTML шага 2: активность + цель питания. */
  function step2Html() {
    var actOpts = ACTIVITY_OPTIONS.map(function (o) {
      var sel = Number(draft.activity_level) === o.value ? " selected" : "";
      return (
        '<option value="' +
        o.value +
        '"' +
        sel +
        ">" +
        App.escapeHtml(L(o.ru, o.en)) +
        "</option>"
      );
    }).join("");

    var goalOpts = DIET_GOAL_OPTIONS.map(function (o) {
      var sel = draft.diet_goal === o.value ? " selected" : "";
      return (
        '<option value="' +
        o.value +
        '"' +
        sel +
        ">" +
        App.escapeHtml(L(o.ru, o.en)) +
        "</option>"
      );
    }).join("");

    return (
      '<div class="onb-step" data-step="2">' +
      '<h1 class="onb-title">' +
      App.escapeHtml(L("Ваша активность и цель", "Your activity & goal")) +
      "</h1>" +
      '<p class="onb-subtitle">' +
      App.escapeHtml(
        L(
          "Подберём норму под ваш образ жизни и цель.",
          "We'll tailor the goal to your lifestyle."
        )
      ) +
      "</p>" +
      '<label class="field onb-field">' +
      '<span class="field__label">' +
      App.escapeHtml(L("Уровень активности", "Activity level")) +
      "</span>" +
      '<select class="field__input" id="onbActivity">' +
      actOpts +
      "</select>" +
      "</label>" +
      '<label class="field onb-field">' +
      '<span class="field__label">' +
      App.escapeHtml(L("Цель питания", "Nutrition goal")) +
      "</span>" +
      '<select class="field__input" id="onbDietGoal">' +
      goalOpts +
      "</select>" +
      "</label>" +
      "</div>"
    );
  }

  /** HTML шага 3: необязательная вечерняя сводка + финиш. */
  function step3Html() {
    var on = draft.summary_enabled;
    return (
      '<div class="onb-step" data-step="3">' +
      '<h1 class="onb-title">' +
      App.escapeHtml(L("Почти готово!", "Almost done!")) +
      "</h1>" +
      '<p class="onb-subtitle">' +
      App.escapeHtml(
        L(
          "Можем присылать лёгкое напоминание с итогами дня.",
          "We can send a gentle reminder with your daily summary."
        )
      ) +
      "</p>" +
      '<button type="button" class="onb-toggle' +
      (on ? " onb-toggle--on" : "") +
      '" id="onbSummary" aria-pressed="' +
      (on ? "true" : "false") +
      '">' +
      '<span class="onb-toggle__emoji" aria-hidden="true">' +
      icon("moon") +
      "</span>" +
      '<span class="onb-toggle__text">' +
      '<span class="onb-toggle__title">' +
      App.escapeHtml(L("Вечерняя сводка в 21:00", "Evening summary at 9:00 PM")) +
      "</span>" +
      '<span class="onb-toggle__hint">' +
      App.escapeHtml(
        L("Итоги калорий и БЖУ за день", "Daily calories & macros recap")
      ) +
      "</span>" +
      "</span>" +
      '<span class="onb-toggle__mark" aria-hidden="true">' +
      (on ? icon("check", { size: 18 }) : "") +
      "</span>" +
      "</button>" +
      "</div>"
    );
  }

  /** Собирает и вставляет разметку текущего шага в контейнер. */
  function render() {
    if (!rootEl) return;

    var stepHtml =
      draft.step === 1
        ? step1Html()
        : draft.step === 2
        ? step2Html()
        : step3Html();

    var isLast = draft.step === STEP_COUNT;
    var nextLabel = isLast
      ? L("Готово", "Finish")
      : L("Далее", "Next");

    var backHtml =
      draft.step > 1
        ? '<button type="button" class="onb-back" id="onbBack">' +
          App.escapeHtml(L("Назад", "Back")) +
          "</button>"
        : "";

    rootEl.innerHTML =
      '<section class="onb-wizard">' +
      dotsHtml(draft.step) +
      stepHtml +
      '<div class="onb-actions">' +
      backHtml +
      '<button type="button" class="btn btn-cta btn-block onb-next" id="onbNext">' +
      App.escapeHtml(nextLabel) +
      "</button>" +
      '<button type="button" class="onb-skip" id="onbSkip">' +
      App.escapeHtml(L("Пропустить", "Skip")) +
      "</button>" +
      "</div>" +
      "</section>";

    bindStep();
    App.scrollTop();
  }

  /* =====================================================================
   *  ОБРАБОТЧИКИ
   * ===================================================================== */

  /** Считывает значения полей текущего шага в черновик. */
  function collectStep() {
    if (draft.step === 1) {
      var w = rootEl.querySelector("#onbWeight");
      var h = rootEl.querySelector("#onbHeight");
      var a = rootEl.querySelector("#onbAge");
      if (w) draft.weight = w.value.trim();
      if (h) draft.height = h.value.trim();
      if (a) draft.age = a.value.trim();
    } else if (draft.step === 2) {
      var act = rootEl.querySelector("#onbActivity");
      var dg = rootEl.querySelector("#onbDietGoal");
      if (act) draft.activity_level = Number(act.value) || 1.375;
      if (dg) draft.diet_goal = dg.value;
    }
    // Шаг 3 (сводка) переключается сразу в обработчике клика.
  }

  /** Навешивает обработчики на элементы текущего шага. */
  function bindStep() {
    // Выбор пола (шаг 1).
    var genderBox = rootEl.querySelector("#onbGender");
    if (genderBox) {
      genderBox.addEventListener("click", function (ev) {
        var btn = ev.target.closest(".onb-choice");
        if (!btn) return;
        draft.gender = btn.getAttribute("data-gender");
        App.haptic("selection");
        // Перекрашиваем выбранную кнопку без полной перерисовки.
        var all = genderBox.querySelectorAll(".onb-choice");
        for (var i = 0; i < all.length; i++) {
          all[i].classList.toggle(
            "onb-choice--on",
            all[i] === btn
          );
        }
      });
    }

    // Тумблер вечерней сводки (шаг 3).
    var summaryBtn = rootEl.querySelector("#onbSummary");
    if (summaryBtn) {
      summaryBtn.addEventListener("click", function () {
        draft.summary_enabled = !draft.summary_enabled;
        App.haptic("selection");
        render();
      });
    }

    // «Назад».
    var backBtn = rootEl.querySelector("#onbBack");
    if (backBtn) {
      backBtn.addEventListener("click", function () {
        collectStep();
        App.haptic("light");
        draft.step = Math.max(1, draft.step - 1);
        render();
      });
    }

    // «Далее» / «Готово».
    var nextBtn = rootEl.querySelector("#onbNext");
    if (nextBtn) {
      nextBtn.addEventListener("click", onNext);
    }

    // «Пропустить» — закрываем онбординг, сохранив цель (см. onSkip).
    var skipBtn = rootEl.querySelector("#onbSkip");
    if (skipBtn) {
      skipBtn.addEventListener("click", function () {
        onSkip(skipBtn);
      });
    }
  }

  /**
   * «Пропустить»: закрывает мастер НАВСЕГДА, а не только на этот раз.
   *
   * Раньше кнопка просто уводила в дневник без сохранения — и App.init,
   * который решает по profile.daily_goal_kcal == null, открывал мастер при
   * следующем же запуске. Получался круг, из которого нельзя выйти, не
   * заполнив анкету.
   *
   * Теперь: если человек успел ввести пол, вес, рост и возраст — считаем
   * настоящую цель тем же серверным расчётом, что и «Готово». Если нет —
   * сохраняем норму по умолчанию: цель перестаёт быть пустой, мастер больше
   * не появляется, а точное значение человек задаёт в профиле, когда захочет.
   * @param {HTMLElement} btn кнопка «Пропустить» (блокируем на время запроса)
   */
  function onSkip(btn) {
    App.haptic("light");
    // Забираем то, что уже введено на текущем шаге (его могли не подтвердить).
    collectStep();

    var w = Number(draft.weight);
    var h = Number(draft.height);
    var a = Number(draft.age);
    // Для честного расчёта нужны все четыре параметра.
    var canCalc = !!draft.gender && w > 0 && h > 0 && a > 0;

    if (btn) btn.disabled = true;
    App.showLoading();

    var request;
    if (canCalc) {
      var payload = {
        weight: w,
        height: h,
        age: Math.round(a),
        gender: draft.gender,
        activity_level: Number(draft.activity_level) || 1.375,
        diet_goal: draft.diet_goal
      };
      // calculateGoal сам сохраняет цель в профиль; следом фиксируем параметры.
      request = App.api.calculateGoal(payload).then(function (goal) {
        return App.api.saveProfile(payload).then(function () {
          var p = App.state.profile || {};
          p.weight = payload.weight;
          p.height = payload.height;
          p.age = payload.age;
          p.gender = payload.gender;
          p.activity_level = payload.activity_level;
          p.diet_goal = payload.diet_goal;
          if (goal && typeof goal === "object") {
            if (goal.daily_goal_kcal != null) p.daily_goal_kcal = goal.daily_goal_kcal;
            if (goal.target_proteins != null) p.target_proteins = goal.target_proteins;
            if (goal.target_fats != null) p.target_fats = goal.target_fats;
            if (goal.target_carbs != null) p.target_carbs = goal.target_carbs;
            if (goal.diet_goal) p.diet_goal = goal.diet_goal;
          }
          App.state.profile = p;
        });
      });
    } else {
      request = App.api
        .saveProfile({ daily_goal_kcal: DEFAULT_GOAL_KCAL })
        .then(function (profile) {
          if (profile && typeof profile === "object") {
            App.state.profile = profile;
          } else {
            var p = App.state.profile || {};
            p.daily_goal_kcal = DEFAULT_GOAL_KCAL;
            App.state.profile = p;
          }
          App.toast(
            L(
              "Поставили ориентир " + DEFAULT_GOAL_KCAL + " ккал — измените его в профиле",
              "Set a placeholder goal of " + DEFAULT_GOAL_KCAL + " kcal — change it in your profile"
            )
          );
        });
    }

    request
      .catch(function (err) {
        // Сохранить не удалось — честно предупреждаем: без цели мастер
        // откроется снова при следующем запуске.
        App.toast(
          err && err.message
            ? err.message
            : L(
                "Не удалось сохранить цель — мастер откроется снова",
                "Failed to save the goal — the wizard will open again"
              )
        );
      })
      .then(function () {
        App.hideLoading();
        if (btn) btn.disabled = false;
        App.navigate("diary");
      });
  }

  /** Валидирует шаг 1: пол обязателен, числовые поля — положительные. */
  function validateStep1() {
    if (!draft.gender) {
      App.toast(L("Выберите пол", "Please select gender"));
      return false;
    }
    var w = Number(draft.weight);
    var h = Number(draft.height);
    var a = Number(draft.age);
    if (!(w > 0)) {
      App.toast(L("Укажите вес", "Enter your weight"));
      return false;
    }
    if (!(h > 0)) {
      App.toast(L("Укажите рост", "Enter your height"));
      return false;
    }
    if (!(a > 0)) {
      App.toast(L("Укажите возраст", "Enter your age"));
      return false;
    }
    return true;
  }

  /** Обработчик «Далее»/«Готово»: переход по шагам или финальное сохранение. */
  function onNext() {
    collectStep();

    if (draft.step === 1) {
      if (!validateStep1()) return;
      App.haptic("light");
      draft.step = 2;
      render();
      return;
    }

    if (draft.step === 2) {
      App.haptic("light");
      draft.step = 3;
      render();
      return;
    }

    // Шаг 3 — финал: считаем цель, сохраняем профиль, идём в дневник.
    finish();
  }

  /* =====================================================================
   *  ФИНАЛ
   * ===================================================================== */

  /**
   * Финальное действие мастера: расчёт цели + сохранение профиля,
   * опциональное включение вечерней сводки, переход в дневник.
   */
  function finish() {
    App.haptic("medium");

    var payload = {
      weight: Number(draft.weight),
      height: Number(draft.height),
      age: Number(draft.age),
      gender: draft.gender,
      activity_level: Number(draft.activity_level) || 1.375,
      diet_goal: draft.diet_goal
    };

    App.showLoading();

    // 1) Считаем цель (сервер сам сохраняет daily_goal_kcal / target_* в профиль).
    App.api
      .calculateGoal(payload)
      .then(function (goal) {
        // 2) Явно сохраняем введённые параметры (вес/рост/возраст/пол/активность/цель),
        //    чтобы профиль был консистентным даже если calculateGoal их не пишет.
        return App.api.saveProfile(payload).then(function () {
          return goal;
        });
      })
      .then(function (goal) {
        // Обновляем кэш профиля: сначала параметры, затем результат расчёта.
        var p = App.state.profile || {};
        p.weight = payload.weight;
        p.height = payload.height;
        p.age = payload.age;
        p.gender = payload.gender;
        p.activity_level = payload.activity_level;
        p.diet_goal = payload.diet_goal;
        if (goal && typeof goal === "object") {
          if (goal.daily_goal_kcal != null) p.daily_goal_kcal = goal.daily_goal_kcal;
          if (goal.target_proteins != null) p.target_proteins = goal.target_proteins;
          if (goal.target_fats != null) p.target_fats = goal.target_fats;
          if (goal.target_carbs != null) p.target_carbs = goal.target_carbs;
          if (goal.diet_goal) p.diet_goal = goal.diet_goal;
        }
        App.state.profile = p;

        // 3) Необязательно — включаем вечернюю сводку в 21:00 (не блокируем финиш).
        if (draft.summary_enabled) {
          try {
            App.api
              .saveNotificationSettings({
                daily_summary_enabled: true,
                summary_time: SUMMARY_TIME
              })
              .catch(function (err) {
                console.warn(
                  "Не удалось включить вечернюю сводку: " +
                    (err && err.message)
                );
              });
          } catch (e) {
            console.warn("Не удалось включить вечернюю сводку", e);
          }
        }
      })
      .catch(function (err) {
        App.toast(
          err && err.message
            ? err.message
            : L("Не удалось рассчитать цель", "Failed to calculate goal")
        );
      })
      .then(function () {
        App.hideLoading();
        // В любом случае уводим в дневник: цель либо посчитана, либо
        // пользователь сможет задать её вручную в аккаунте.
        App.navigate("diary");
      });
  }

  /* =====================================================================
   *  КОНТРОЛЛЕР СТРАНИЦЫ
   * ===================================================================== */

  var controller = {
    onShow: function (viewEl) {
      rootEl = viewEl;
      // Каждый вход в мастер начинается с чистого черновика и первого шага.
      resetDraft();
      render();
    },
    onHide: function () {
      // Освобождаем ссылку на контейнер (сам #view очищает роутер).
      rootEl = null;
    }
  };

  App.registerPage("onboarding", controller);

  // Публикуем контроллер (симметрично прочим страницам, напр. window.PageScan).
  window.PageOnboarding = controller;
})();
