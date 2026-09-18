/**
 * page-diary.js — страница «Питание» / "Nutrition" (дневник питания).
 *
 * Регистрирует контроллер страницы через App.registerPage("diary", {...}).
 * Возможности:
 *   - Переключатель даты с мини-календарём по тапу на подпись даты.
 *     По умолчанию — сегодняшний день; но если экран определения еды оставил
 *     App.state.diaryReturnDate, открываем именно ту дату: запись, добавленную
 *     «за вчера», человек должен увидеть там, куда он её записал.
 *   - Загрузка дневника за выбранную дату через App.api.getDiary(date).
 *   - Приёмы пищи: с записями — карточкой со списком, без записей — одной
 *     компактной строкой-кнопкой «добавить в этот приём». Пустое состояние на
 *     весь день ровно одно (прежде их показывалось пять сразу).
 *   - Удаление записи — через подтверждение в нижнем листе (прежде запись
 *     исчезала мгновенно от тапа по кнопке 32 на 32 px впритык к зоне открытия
 *     редактора, то есть от промаха пальцем).
 *   - Итог калорий за день с учётом тренировок («съедено минус сожжено = итого»)
 *     + прогресс-бар относительно daily_goal_kcal по net_calories.
 *   - Секция «Активность»: сожжённое за день и список тренировок
 *     (App.api.getWorkouts). Свои записи удаляются, записи AI-тренера
 *     (description начинается с «Тренер:») помечены гантелью и защищены от
 *     удаления — они принадлежат журналу тренировок, а не дневнику.
 *   - ЕДИНСТВЕННЫЙ способ ручного ввода и правки блюда — нижний лист
 *     openFoodSheet(): название с поиском по базе, количество+единица, кнопка
 *     «Рассчитать КБЖУ» (App.api.calculateFood), КБЖУ, приём пищи; в режиме
 *     добавления снизу — быстрый повтор из «Недавние» и «Вчера».
 *   - Плавающая кнопка «+» открывает нижний лист выбора: Сфотографировать /
 *     Голосом / Вручную / Активность / Что съесть? (премиум) / AI-план меню
 *     (премиум). Камеры в таббаре больше нет, поэтому «Сфотографировать» —
 *     первый и главный пункт листа; запись ложится в ВЫБРАННЫЙ в дневнике день.
 *     Пока день пуст, «+» скрыта: в пустом состоянии есть своя кнопка
 *     «Добавить», и два одинаковых действия стояли друг над другом.
 *   - Другие экраны могут попросить сразу открыть нужный лист через
 *     App.state.diaryOpenSheet = "photo" | "voice" | "manual" | "activity"
 *     (флаг одноразовый; фото и голос сразу уводят на камеру).
 *       • «Активность»: тип, длительность, сожжённые ккал с оценкой через
 *         App.api.estimateWorkout, сохранение через App.api.addWorkout. Место
 *         здесь потому, что расход калорий — часть баланса дня, который ведёт
 *         дневник.
 *       • «Что съесть?» (Этап 5, премиум): выбор приёма пищи + свободный ввод
 *         «Чего хочется?» -> App.api.suggestFood (умные предложения), кнопка
 *         «Вкусняшки» -> App.api.getHealthySnacks; фолбэк — recommendFood.
 *       • «AI-план меню» (Этап 5, премиум): выбор охвата (День/Неделя) +
 *         предпочтения -> App.api.generateMealPlan; по дням приёмы пищи с КБЖУ,
 *         замена блюда -> App.api.regenerateMealItem; список покупок.
 *   - Карточка «Напоминания о еде»: тумблер + три поля времени (завтрак/обед/
 *     ужин). Хранится через App.api.getNotificationSettings /
 *     saveNotificationSettings (поля meal_reminder_enabled, breakfast_time,
 *     lunch_time, dinner_time).
 *   - Пустые состояния, скелетон загрузки, экран ошибки с кнопкой «Повторить».
 *
 * Локализация: весь видимый пользователю текст оборачивается в App.pick(ru, en)
 * НА МОМЕНТ РЕНДЕРА, чтобы смена языка (App.setLang) с перерисовкой давала нужный
 * текст. Данные от API (названия блюд) и пользовательский ввод не переводятся.
 *
 * Иконки — только App.icon() из js/icons.js. Эмодзи в роли иконок здесь нет:
 * их рисует операционная система (разный вид на разных телефонах, не наследуют
 * цвет текста), а главное — они сидели ВНУТРИ строк App.pick и дублировались в
 * обеих локалях, то есть картинка притворялась переводимым текстом.
 */
(function () {
  "use strict";

  /**
   * Локальный хелпер локализации. Делегирует в App.pick(ru, en); если App.pick
   * по какой-то причине ещё не готов — безопасно возвращает русский вариант.
   * Вызывается НА МОМЕНТ РЕНДЕРА, поэтому смена языка корректно перерисовывает UI.
   * @param {string} ru русский текст
   * @param {string} en английский текст
   * @returns {string}
   */
  function pick(ru, en) {
    if (App && typeof App.pick === "function") {
      return App.pick(ru, en);
    }
    return ru;
  }

  /**
   * Разметка иконки из общего набора (js/icons.js). Отдельная обёртка нужна
   * затем же, зачем pick(): страница не должна падать, если icons.js почему-то
   * не загрузился — тогда просто нет картинки, но текст на месте.
   * @param {string} name имя иконки
   * @param {Object} [opts] {size, rotate, cls, stroke}
   * @returns {string} строка '<svg …>' либо ""
   */
  function icon(name, opts) {
    if (App && typeof App.icon === "function") {
      return App.icon(name, opts);
    }
    return "";
  }

  /**
   * Значение переменной --hero-img для фото геройского блока.
   * Путь делаем абсолютным: относительный url() внутри custom property Chrome
   * разрешает от адреса style.css, где переменная подставляется, а не от
   * страницы — картинка запрашивалась как css/img/… и уходила в 404.
   * @param {string} file имя файла в frontend/img
   */
  function heroImg(file) {
    return App.heroImg(file);
  }

  // Порядок приёмов пищи (подписи берём из App.mealLabel — он уже локализован).
  var MEAL_ORDER = ["breakfast", "lunch", "dinner", "snack"];

  // Иконки приёмов пищи — имена из общего набора (App.icon), не эмодзи.
  // Смысл привязан ко времени суток: рассвет, миска, луна, яблоко-перекус.
  var MEAL_ICONS = {
    breakfast: "sunrise",
    lunch: "bowl",
    dinner: "moon",
    snack: "apple"
  };

  // Типы активности для ручного ввода тренировки (значения уходят на бэкенд
  // как есть: cardio | strength | walking | yoga | other).
  var WORKOUT_TYPES = ["cardio", "strength", "walking", "yoga", "other"];

  // Иконки типов активности. Точных «ходьбы» и «йоги» в наборе нет, поэтому
  // берём ближайшие по смыслу: бег для ходьбы, тело — для йоги.
  var WORKOUT_ICONS = {
    cardio: "run",
    strength: "dumbbell",
    walking: "run",
    yoga: "body",
    other: "heart"
  };

  // Префикс описания у тренировок, созданных AI-тренером при завершении
  // сессии. Такие записи принадлежат журналу тренировок: дневник их
  // показывает, но удалять не даёт — иначе программа тренера разъедется.
  var COACH_PREFIX = "Тренер:";

  // Канонические единицы измерения (языконезависимые ключи хранятся в БД).
  var UNIT_KEYS = ["pcs", "g", "ml", "serving"];

  // Внутреннее состояние контроллера страницы.
  var state = {
    date: null,        // текущая выбранная дата "YYYY-MM-DD"
    viewEl: null,      // корневой элемент страницы (#view)
    loading: false,    // флаг, чтобы не запускать параллельные перезагрузки
    day: null,         // последний загруженный DiaryDayOut (для модалок)
    panel: null,       // открытая AI-панель: "recommend" | "meal-plan" | null
    calMonth: null,    // просматриваемый месяц календаря "YYYY-MM" (для перелистывания)
    // Состояние панели AI-плана меню (Этап 5): выбранный охват, предпочтения,
    // последний полученный план (для замены блюд в UI без полного перезапроса).
    planScope: "day",  // "day" | "week"
    planPrefs: "",     // текст предпочтений из поля ввода
    plan: null,        // последний MealPlanOut {days:[...], shopping_list:[...]}
    // Состояние улучшенной панели «Что съесть?» (Этап 5).
    suggestMeal: null, // выбранный приём пищи ("breakfast"... ) или null (весь день)
    suggestText: ""    // свободный ввод «Чего хочется?»
  };

  /**
   * Прибавляет к дате в формате ISO ("YYYY-MM-DD") заданное число дней
   * и возвращает новую дату в том же формате. Работает в локальной зоне
   * без риска «уплыть» из-за часовых поясов (используем полдень).
   * @param {string} isoDate "YYYY-MM-DD"
   * @param {number} deltaDays смещение в днях (может быть отрицательным)
   * @returns {string} новая дата "YYYY-MM-DD"
   */
  function shiftDate(isoDate, deltaDays) {
    var parts = String(isoDate).split("-");
    var y = parseInt(parts[0], 10);
    var m = parseInt(parts[1], 10) - 1; // месяцы в Date считаются с нуля
    var d = parseInt(parts[2], 10);
    // 12:00 локального времени защищает от смещения через границу суток.
    var dt = new Date(y, m, d, 12, 0, 0, 0);
    dt.setDate(dt.getDate() + deltaDays);
    var yy = dt.getFullYear();
    var mm = String(dt.getMonth() + 1).padStart(2, "0");
    var dd = String(dt.getDate()).padStart(2, "0");
    return yy + "-" + mm + "-" + dd;
  }

  // Массивы названий месяцев (переиспользуются humanDate и мини-календарём).
  var MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"
  ];
  var MONTHS_EN = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
  ];

  /**
   * Человеко-читаемая подпись даты для переключателя.
   * Сегодня -> «Сегодня»/"Today", вчера -> «Вчера»/"Yesterday",
   * завтра -> «Завтра»/"Tomorrow", иначе «18 июня 2026» / "18 June 2026".
   * Локализуется на момент рендера через App.pick.
   * @param {string} isoDate "YYYY-MM-DD"
   * @returns {string}
   */
  function humanDate(isoDate) {
    var today = App.todayStr();
    if (isoDate === today) {
      return pick("Сегодня", "Today");
    }
    if (isoDate === shiftDate(today, -1)) {
      return pick("Вчера", "Yesterday");
    }
    if (isoDate === shiftDate(today, 1)) {
      return pick("Завтра", "Tomorrow");
    }
    var parts = String(isoDate).split("-");
    var y = parseInt(parts[0], 10);
    var m = parseInt(parts[1], 10) - 1;
    var d = parseInt(parts[2], 10);
    if (isNaN(y) || isNaN(m) || isNaN(d) || !MONTHS_RU[m]) {
      return isoDate; // запасной вариант, если дата вдруг некорректна
    }
    // Формат: RU «18 июня 2026», EN "18 June 2026".
    return pick(
      d + " " + MONTHS_RU[m] + " " + y,
      d + " " + MONTHS_EN[m] + " " + y
    );
  }

  /**
   * Локализованная подпись канонической единицы измерения.
   * pcs -> шт/pcs, g -> г/g, ml -> мл/ml, serving -> порция/serving.
   * Для неизвестного/пустого ключа возвращает "".
   * @param {string|null} key канонический ключ единицы
   * @returns {string}
   */
  function unitLabel(key) {
    switch (key) {
      case "pcs":
        return pick("шт", "pcs");
      case "g":
        return pick("г", "g");
      case "ml":
        return pick("мл", "ml");
      case "serving":
        return pick("порция", "serving");
      default:
        return "";
    }
  }

  /**
   * Возвращает приём пищи по текущему часу (для умных дефолтов).
   * @returns {string} breakfast | lunch | dinner | snack
   */
  function mealByHour() {
    var h = new Date().getHours();
    if (h < 11) return "breakfast";
    if (h < 16) return "lunch";
    if (h < 21) return "dinner";
    return "snack";
  }

  /**
   * Локализованная подпись типа активности (на момент рендера).
   * @param {string} type cardio | strength | walking | yoga | other
   * @returns {string}
   */
  function workoutTypeLabel(type) {
    switch (type) {
      case "cardio":
        return pick("Кардио", "Cardio");
      case "strength":
        return pick("Силовая", "Strength");
      case "walking":
        return pick("Ходьба", "Walking");
      case "yoga":
        return pick("Йога", "Yoga");
      case "other":
        return pick("Другое", "Other");
      default:
        return pick("Активность", "Activity");
    }
  }

  /**
   * Тренировка создана AI-тренером (а не руками в дневнике)?
   * Признак — префикс описания «Тренер:», который ставит экран сессии.
   * @param {Object} w WorkoutOut
   * @returns {boolean}
   */
  function isCoachWorkout(w) {
    var d = (w && w.description) || "";
    return String(d).indexOf(COACH_PREFIX) === 0;
  }

  /**
   * Ищет запись дневника по id в последнем загруженном дне (state.day).
   * @param {number} id
   * @returns {Object|null} DiaryEntryOut или null
   */
  function findEntryById(id) {
    var meals = (state.day && state.day.meals) || {};
    for (var i = 0; i < MEAL_ORDER.length; i++) {
      var arr = meals[MEAL_ORDER[i]] || [];
      for (var j = 0; j < arr.length; j++) {
        if (Number(arr[j].id) === Number(id)) return arr[j];
      }
    }
    return null;
  }

  /**
   * Загружает и отрисовывает счётчик серии («стрик») в шапке страницы.
   * Показывается только при current > 0. Тихо игнорирует ошибки.
   */
  function loadStreak() {
    var el = document.getElementById("diary-streak");
    if (!el) return;
    if (!(App.api && typeof App.api.getStreak === "function")) return;

    App.api
      .getStreak(App.todayStr())
      .then(function (s) {
        el = document.getElementById("diary-streak");
        if (!el) return;
        var cur = (s && Number(s.current)) || 0;
        if (cur > 0) {
          var longest = (s && Number(s.longest)) || cur;
          el.hidden = false;
          // Иконка отдельно от числа: число — данные, огонь — картинка.
          el.innerHTML =
            icon("flame", { size: 14 }) +
            '<span class="diary-streak__num">' + App.escapeHtml(String(cur)) + "</span>";
          el.title = pick(
            "Серия: " + cur + " дн. подряд. Рекорд: " + longest + ".",
            "Streak: " + cur + " days in a row. Best: " + longest + "."
          );
          // «Остывающий» вид, если сегодня ещё нет записей (серия под угрозой).
          el.classList.toggle(
            "diary-streak--cold",
            !!(s && s.logged_today === false)
          );
        } else {
          el.hidden = true;
          el.innerHTML = "";
        }
      })
      .catch(function () {
        /* вспомогательный элемент — при ошибке просто не показываем */
      });
  }

  /**
   * HTML-разметка скелетона загрузки (мягкие «пульсирующие» плашки).
   * @returns {string}
   */
  function skeletonHtml() {
    var rows = "";
    for (var i = 0; i < MEAL_ORDER.length; i++) {
      rows +=
        '<div class="diary-meal skeleton-block">' +
        '<div class="skeleton skeleton-line skeleton-title"></div>' +
        '<div class="skeleton skeleton-line"></div>' +
        '<div class="skeleton skeleton-line short"></div>' +
        "</div>";
    }
    return (
      '<div class="diary-skeleton">' +
      '<div class="skeleton skeleton-line skeleton-total"></div>' +
      rows +
      "</div>"
    );
  }

  /**
   * Разметка одной записи приёма пищи.
   * Название блюда (entry.dish_name) — данные от API/пользователя, не переводим.
   * Если у записи есть quantity — рядом с названием показываем бейдж
   * «2 шт» / «100 г» (span.diary-entry__qty).
   * @param {Object} entry DiaryEntryOut
   * @returns {string}
   */
  function entryRowHtml(entry) {
    var name = App.escapeHtml(entry.dish_name || pick("Без названия", "Untitled"));
    var kcal = App.fmt(entry.calories || 0);
    // Б/Ж/У -> P/F/C (Белки/Жиры/Углеводы -> Protein/Fat/Carbs).
    var pLabel = pick("Б", "P");
    var fLabel = pick("Ж", "F");
    var cLabel = pick("У", "C");

    // Бейдж количества (если задано quantity). Единицу локализуем.
    var qtyBadge = "";
    if (entry.quantity != null) {
      var uLabel = unitLabel(entry.unit);
      var qtyText = App.fmt(entry.quantity) + (uLabel ? " " + uLabel : "");
      qtyBadge =
        ' <span class="diary-entry__qty">' + App.escapeHtml(qtyText) + "</span>";
    }

    return (
      '<li class="diary-entry" data-id="' + entry.id + '">' +
      '<div class="diary-entry__main">' +
      '<span class="diary-entry__name">' + name + qtyBadge + "</span>" +
      '<span class="diary-entry__macros">' + pLabel + " " + App.fmt(entry.proteins || 0) +
      " · " + fLabel + " " + App.fmt(entry.fats || 0) +
      " · " + cLabel + " " + App.fmt(entry.carbs || 0) + "</span>" +
      "</div>" +
      '<span class="diary-entry__kcal">' + kcal + " " + pick("ккал", "kcal") + "</span>" +
      // Корзина, а не «крестик»: действие удаляет данные, а не закрывает окно.
      // Зона нажатия 44px задана в css/nutrition.css — прежние 32 на 32 px вплотную
      // к зоне открытия редактора ловили промахи пальцем.
      '<button class="diary-entry__del" type="button" ' +
      'data-id="' + entry.id + '" aria-label="' + App.escapeHtml(pick("Удалить запись", "Delete entry")) +
      '" title="' + App.escapeHtml(pick("Удалить", "Delete")) + '">' +
      icon("trash", { size: 18 }) + "</button>" +
      "</li>"
    );
  }

  /**
   * Разметка одной секции приёма пищи.
   *
   * Приём БЕЗ записей — не карточка с пустым состоянием во весь рост, а одна
   * компактная строка-кнопка «добавить в этот приём»: пять пустых состояний
   * на одном экране (общее + по одному в каждом приёме) не сообщали ничего,
   * кроме того, что день пуст, и это уже сказано один раз выше.
   *
   * @param {string} mealType "breakfast" | "lunch" | "dinner" | "snack"
   * @param {Array}  entries  список DiaryEntryOut
   * @returns {string}
   */
  function mealSectionHtml(mealType, entries) {
    entries = entries || [];
    var label = App.mealLabel(mealType); // App.mealLabel уже локализован
    var mealIcon = icon(MEAL_ICONS[mealType] || "plate", { size: 18 });

    if (entries.length === 0) {
      // Компактная строка: иконка + название + «+». Тап открывает лист
      // добавления с уже выбранным этим приёмом пищи.
      return (
        '<button type="button" class="diary-meal-row" data-add-meal="' + mealType + '" ' +
        'aria-label="' + App.escapeHtml(
          pick("Добавить в «", "Add to “") + label + pick("»", "”")
        ) + '">' +
        '<span class="diary-meal-row__icon">' + mealIcon + "</span>" +
        '<span class="diary-meal-row__title">' + App.escapeHtml(label) + "</span>" +
        '<span class="diary-meal-row__add">' + icon("plus", { size: 18 }) + "</span>" +
        "</button>"
      );
    }

    // Сумма калорий по приёму пищи.
    var mealKcal = 0;
    for (var i = 0; i < entries.length; i++) {
      mealKcal += Number(entries[i].calories) || 0;
    }

    var rows = "";
    for (var j = 0; j < entries.length; j++) {
      rows += entryRowHtml(entries[j]);
    }

    return (
      '<section class="card diary-meal">' +
      '<header class="diary-meal__head">' +
      // Название приёма — надзаголовок (.eyebrow), сумма справа — число
      // сжатым шрифтом: карточка читается как строка табло, а не как список.
      '<span class="diary-meal__title eyebrow">' +
      '<span class="diary-meal__icon">' + mealIcon + "</span>" +
      App.escapeHtml(label) +
      "</span>" +
      '<span class="diary-meal__kcal num">' + App.fmt(mealKcal) +
      '<span class="diary-meal__kcal-unit">' + pick("ккал", "kcal") + "</span></span>" +
      "</header>" +
      '<ul class="diary-meal__list">' + rows + "</ul>" +
      // Добавить ещё блюдо в этот же приём, не открывая общий лист выбора.
      '<button type="button" class="diary-meal__add" data-add-meal="' + mealType + '">' +
      icon("plus", { size: 16 }) +
      App.escapeHtml(pick("Добавить", "Add")) +
      "</button>" +
      "</section>"
    );
  }

  /**
   * Разметка карточки с итогом дня и прогресс-баром цели.
   * Если за день есть сожжённые калории (total_burned > 0), показываем
   * баланс «съедено X минус сожжено Y = итого Z ккал», а прогресс-бар
   * относительно цели считаем по net_calories. Иначе — как раньше.
   * @param {Object} day DiaryDayOut
   * @returns {string}
   */
  function totalsHtml(day) {
    var eaten = Number(day.total_calories) || 0;
    var burned = Number(day.total_burned) || 0;
    // net_calories с сервера; если поля нет — считаем сами.
    var net = (day.net_calories !== undefined && day.net_calories !== null)
      ? Number(day.net_calories)
      : eaten - burned;
    var goal = day.daily_goal_kcal; // может быть null

    var kcal = pick("ккал", "kcal");

    // Значение, относительно которого считаем прогресс и остаток.
    // При наличии тренировок учитываем «чистые» калории.
    var hasBurned = burned > 0;
    var basis = hasBurned ? net : eaten;

    // Блок баланса с учётом тренировок (только если что-то сожжено).
    var balanceBlock = "";
    if (hasBurned) {
      balanceBlock =
        '<div class="diary-balance">' +
        '<span class="diary-balance__part diary-balance__eaten">' +
        App.escapeHtml(pick("Съедено", "Eaten")) + " " + App.fmt(eaten) + "</span>" +
        '<span class="diary-balance__op">&minus;</span>' +
        '<span class="diary-balance__part diary-balance__burned">' +
        App.escapeHtml(pick("Сожжено", "Burned")) + " " + App.fmt(burned) + "</span>" +
        '<span class="diary-balance__op">=</span>' +
        '<span class="diary-balance__part diary-balance__net">' +
        App.escapeHtml(pick("Итого", "Total")) + " " + App.fmt(net) + " " + kcal + "</span>" +
        "</div>";
    }

    var progressBlock;
    if (goal && goal > 0) {
      var pct = Math.round((basis / goal) * 100);
      var width = Math.max(0, Math.min(100, pct)); // ширину ограничиваем 0..100%
      var over = basis > goal;
      var remaining = goal - basis;

      var hint;
      if (over) {
        hint = pick("Превышение на ", "Over by ") + App.fmt(Math.abs(remaining)) + " " + kcal;
      } else {
        hint = pick("Осталось ", "Remaining ") + App.fmt(remaining) + " " + kcal;
      }

      progressBlock =
        '<div class="diary-progress">' +
        '<div class="diary-progress__track">' +
        '<div class="diary-progress__fill' + (over ? " is-over" : "") + '" ' +
        'style="width:' + width + '%"></div>' +
        "</div>" +
        '<div class="diary-progress__labels">' +
        '<span>' + App.fmt(basis) + " / " + App.fmt(goal) + " " + kcal +
        (hasBurned ? pick(" (с учётом тренировок)", " (incl. workouts)") : "") + "</span>" +
        '<span class="diary-progress__hint' + (over ? " is-over" : "") + '">' +
        App.escapeHtml(hint) + "</span>" +
        "</div>" +
        "</div>";
    } else {
      // Цель не задана — короткая подсказка + кнопка перехода к настройке цели.
      // Кнопка (data-goto-goal) раскрывает свёртку профиля в «Мой аккаунт».
      progressBlock =
        '<div class="diary-total__nogoal">' +
        '<p class="diary-total__nogoal-hint">' +
        App.escapeHtml(pick(
          "Цель калорий пока не задана.",
          "Your calorie goal isn’t set yet."
        )) + "</p>" +
        '<button type="button" class="diary-total__nogoal-cta" data-goto-goal>' +
        App.escapeHtml(pick("Настроить цель", "Set your goal")) +
        icon("arrow", { size: 16 }) +
        "</button>" +
        "</div>";
    }

    // Главное число карточки: при наличии тренировок показываем net,
    // иначе — съеденные калории (как и было раньше).
    var headValue = hasBurned ? net : eaten;
    var headCaption = hasBurned
      ? pick("Итого за день (нетто)", "Daily total (net)")
      : pick("Итого за день", "Daily total");

    return (
      '<section class="card diary-total">' +
      '<div class="diary-total__row">' +
      '<span class="diary-total__caption eyebrow">' + App.escapeHtml(headCaption) + "</span>" +
      '<span class="diary-total__value num">' + App.fmt(headValue) +
      '<span class="diary-total__unit">' + kcal + "</span></span>" +
      "</div>" +
      balanceBlock +
      // БЖУ — три числа в ряд, каждое в цвете своего нутриента: так итог дня
      // сопоставляется с полосами на «Сегодня» без чтения подписей.
      '<div class="diary-total__macros">' +
      totalMacroHtml("p", pick("Белки", "Protein"), day.total_proteins) +
      totalMacroHtml("f", pick("Жиры", "Fat"), day.total_fats) +
      totalMacroHtml("c", pick("Углеводы", "Carbs"), day.total_carbs) +
      "</div>" +
      progressBlock +
      // Подбор блюда — прямо под остатком нормы: вопрос «что съесть» возникает
      // именно здесь. Без подписки — замок, по тапу пейволл функции.
      '<button type="button" class="diary-suggest" data-diary-suggest>' +
      '<span class="diary-suggest__icon" aria-hidden="true">' + icon("sparkle", { size: 18 }) + "</span>" +
      '<span class="diary-suggest__text">' +
      '<span class="diary-suggest__title">' + App.escapeHtml(pick("Что съесть?", "What to eat?")) + "</span>" +
      '<span class="diary-suggest__sub">' +
      App.escapeHtml(pick("ИИ подберёт блюдо под остаток нормы", "AI picks a dish for what's left of your goal")) +
      "</span>" +
      "</span>" +
      '<span class="diary-suggest__end" aria-hidden="true">' +
      icon(isPremium() ? "chevron" : "lock", { size: 16 }) +
      "</span>" +
      "</button>" +
      "</section>"
    );
  }

  /**
   * Одна колонка итога БЖУ: подпись-надзаголовок и число сжатым шрифтом.
   * @param {string} mod "p" | "f" | "c" — модификатор цвета нутриента
   * @param {string} label локализованная подпись
   * @param {number} grams граммы за день
   * @returns {string}
   */
  function totalMacroHtml(mod, label, grams) {
    return (
      '<div class="diary-total__macro diary-total__macro--' + mod + '">' +
      '<span class="diary-total__macro-label eyebrow">' + App.escapeHtml(label) + "</span>" +
      '<span class="diary-total__macro-value num">' + App.fmt(grams || 0) +
      '<span class="diary-total__macro-unit">' + pick("г", "g") + "</span></span>" +
      "</div>"
    );
  }

  /**
   * Возвращает true, если пользователь премиум (через единый App.isPremium).
   * Базовый дневник бесплатен; платные только AI-фичи.
   * @returns {boolean}
   */
  function isPremium() {
    return !!(App && typeof App.isPremium === "function" && App.isPremium());
  }

  /**
   * Контейнер для AI-панелей (рекомендации, план меню, paywall).
   *
   * Формы ввода сюда больше не разворачиваются — они живут в нижних листах.
   * Здесь остались только длинные РЕЗУЛЬТАТЫ (меню на неделю, список покупок,
   * карточки подсказок): их место — в потоке страницы, где их можно спокойно
   * листать, а не в листе поверх дня.
   * @returns {string}
   */
  function actionsHtml() {
    return '<div id="diary-panel" class="diary-panel"></div>';
  }

  /**
   * Разметка переключателя даты (стрелка · дата · стрелка). Центральная
   * подпись — кнопка, открывающая мини-календарь (data-open-cal). Контейнер
   * для попапа календаря — .diary-datebar__cal (под баром даты).
   * @returns {string}
   */
  function dateBarHtml() {
    // «Следующий день» недоступен для будущих дат (как и мини-календарь).
    var nextDisabled = state.date >= App.todayStr();
    return (
      '<div class="diary-datebar-wrap">' +
      '<div class="diary-datebar card">' +
      '<button class="diary-datebar__nav" type="button" data-nav="prev" ' +
      'aria-label="' + App.escapeHtml(pick("Предыдущий день", "Previous day")) + '">' +
      icon("chevron", { size: 20, rotate: 180 }) + "</button>" +
      '<button class="diary-datebar__label" type="button" data-open-cal ' +
      'aria-label="' + App.escapeHtml(pick("Открыть календарь", "Open calendar")) + '">' +
      '<span class="diary-datebar__date">' + App.escapeHtml(humanDate(state.date)) + "</span>" +
      '<span class="diary-datebar__iso">' + App.escapeHtml(state.date) + "</span>" +
      "</button>" +
      '<button class="diary-datebar__nav" type="button" data-nav="next" ' +
      (nextDisabled ? "disabled " : "") +
      'aria-label="' + App.escapeHtml(pick("Следующий день", "Next day")) + '">' +
      icon("chevron", { size: 20 }) + "</button>" +
      "</div>" +
      '<div id="diary-cal" class="diary-datebar__cal"></div>' +
      "</div>"
    );
  }

  /**
   * Полная отрисовка дня (итоги + область панели + 4 секции приёмов пищи).
   * @param {Object} day DiaryDayOut
   */
  function renderDay(day) {
    var content = document.getElementById("diary-content");
    if (!content) return;

    // Запоминаем день для модалок «Что съесть?» (нужны остатки КБЖУ).
    state.day = day;

    // Безопасные значения на случай неполного ответа сервера.
    var meals = day.meals || {};

    // Подсчёт общего количества записей за день для пустого состояния.
    var totalEntries = 0;
    for (var j = 0; j < MEAL_ORDER.length; j++) {
      var arr = meals[MEAL_ORDER[j]] || [];
      totalEntries += arr.length;
    }

    var body;
    if (totalEntries === 0) {
      // Пустой день — РОВНО одно пустое состояние с одним главным действием.
      // Компактные строки приёмов пищи здесь не нужны: они повторяли бы то же
      // самое сообщение ещё четыре раза.
      // Тёмный блок с фотографией (тот же приём, что у тренировки дня): пустой
      // дневник — не «ошибка», а приглашение, и выглядеть он должен как афиша.
      // Главная кнопка сразу открывает камеру — самый быстрый способ записать
      // еду; остальные способы остаются за ссылкой на общий лист.
      body =
        '<section class="hero hero--img diary-empty" style="' + heroImg("empty-diary.jpg") + '">' +
        '<span class="eyebrow">' + App.escapeHtml(pick("Дневник питания", "Food diary")) + "</span>" +
        '<h2 class="hero__title diary-empty__title">' +
        App.escapeHtml(pick("Записей нет", "Nothing yet")) + "</h2>" +
        '<p class="hero__meta diary-empty__text">' +
        App.escapeHtml(pick(
          "Сфотографируйте блюдо — ИИ посчитает калории.",
          "Snap your dish and AI will count the calories."
        )) + "</p>" +
        '<button type="button" class="btn btn--cta diary-empty__cta" data-open-scan>' +
        icon("camera", { size: 20 }) +
        App.escapeHtml(pick("Снять еду", "Snap food")) + "</button>" +
        '<button type="button" class="diary-empty__more" data-open-add>' +
        App.escapeHtml(pick("Голосом или вручную", "By voice or manually")) +
        icon("chevron", { size: 16 }) +
        "</button>" +
        "</section>";
    } else {
      var sections = "";
      for (var i = 0; i < MEAL_ORDER.length; i++) {
        var type = MEAL_ORDER[i];
        sections += mealSectionHtml(type, meals[type]);
      }
      body = sections;
    }

    // «+» только когда есть записи: на пустом дне действие уже есть в самом
    // пустом состоянии (см. setFabVisible).
    setFabVisible(totalEntries > 0);

    content.innerHTML =
      totalsHtml(day) + actionsHtml() + body +
      // Контейнер секции «Активность» (список тренировок грузится отдельно).
      '<div id="diary-activity" class="diary-activity"></div>' +
      // Контейнер карточки «Напоминания о еде» (рисуется отдельно).
      '<div id="diary-notif" class="diary-notif"></div>';

    // Навешиваем обработчики удаления записей.
    var delButtons = content.querySelectorAll(".diary-entry__del");
    for (var k = 0; k < delButtons.length; k++) {
      delButtons[k].addEventListener("click", onDeleteClick);
    }

    // Тап по строке записи (кроме кнопки удаления) открывает редактирование.
    var rowEls = content.querySelectorAll(".diary-entry");
    for (var re = 0; re < rowEls.length; re++) {
      rowEls[re].addEventListener("click", onEntryRowClick);
    }

    // Быстрое добавление в конкретный приём пищи: и компактная строка пустого
    // приёма, и кнопка «Добавить» внутри непустой карточки открывают один и
    // тот же лист — с этим приёмом, уже выбранным.
    var addMealBtns = content.querySelectorAll("[data-add-meal]");
    for (var am = 0; am < addMealBtns.length; am++) {
      addMealBtns[am].addEventListener("click", function (ev) {
        var meal = ev.currentTarget.getAttribute("data-add-meal");
        App.haptic && App.haptic("light");
        openFoodSheet(null, meal);
      });
    }

    // «Что съесть?» в итоге дня — тот же путь, что и пункт листа «+».
    var suggestBtn = content.querySelector("[data-diary-suggest]");
    if (suggestBtn) {
      suggestBtn.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        onSheetAction("recommend");
      });
    }

    // Главная кнопка пустого состояния — камера (тот же путь, что и пункт
    // «Сфотографировать» в листе: снимок ляжет в выбранный день).
    var emptyScan = content.querySelector("[data-open-scan]");
    if (emptyScan) {
      emptyScan.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        openScan("photo");
      });
    }

    // Ссылка «Голосом или вручную» — общий лист выбора способа добавления.
    var emptyCta = content.querySelector("[data-open-add]");
    if (emptyCta) {
      emptyCta.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        openSheet();
      });
    }

    // Кнопка «Настроить цель» (когда цель не задана) — уводит в аккаунт
    // и просит раскрыть свёртку профиля через общий флаг App.state.
    var goalBtn = content.querySelector("[data-goto-goal]");
    if (goalBtn) {
      goalBtn.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        if (App.state) App.state.openProfileFold = true;
        if (App && typeof App.navigate === "function") App.navigate("account");
      });
    }

    // Секция «Активность» за этот день (сожжённое + список тренировок).
    loadActivity();

    // Если перед перезагрузкой была открыта AI-панель — восстанавливаем её.
    if (state.panel === "recommend") {
      // Платную панель восстанавливаем с учётом статуса: free -> paywall.
      if (isPremium()) {
        openRecommendPanel();
      } else {
        openRecommendPaywall();
      }
    } else if (state.panel === "meal-plan") {
      // AI-план меню: free -> paywall, premium -> панель.
      if (isPremium()) {
        openMealPlanPanel();
      } else {
        openMealPlanPaywall();
      }
    } else if (state.panel === "activity") {
      // Единственное состояние панели активности — paywall для free:
      // у премиума активность открывается листом, а не панелью.
      openActivityPaywall();
    }

    // Карточка напоминаний о еде (загружается асинхронно, со своим состоянием).
    loadMealReminders();

    // Счётчик серии («стрик») в шапке — обновляем после каждой перерисовки дня.
    loadStreak();
  }

  /**
   * Отрисовка состояния ошибки с кнопкой «Повторить».
   * @param {string} message текст ошибки
   */
  function renderError(message) {
    var content = document.getElementById("diary-content");
    if (!content) return;
    content.innerHTML =
      '<div class="card diary-error">' +
      '<div class="diary-error__icon">' + icon("warning", { size: 28 }) + "</div>" +
      '<p class="diary-error__title">' +
      App.escapeHtml(pick("Не удалось загрузить рацион", "Failed to load diary")) + "</p>" +
      '<p class="diary-error__text">' +
      App.escapeHtml(message || pick("Неизвестная ошибка", "Unknown error")) + "</p>" +
      '<button class="btn btn--cta diary-error__retry" type="button">' +
      App.escapeHtml(pick("Повторить", "Retry")) + "</button>" +
      "</div>";

    var retryBtn = content.querySelector(".diary-error__retry");
    if (retryBtn) {
      retryBtn.addEventListener("click", function () {
        loadAndRender();
      });
    }
  }

  /**
   * Загрузка данных за текущую дату и их отрисовка.
   * Показывает скелетон на время загрузки, ошибку — при сбое.
   */
  function loadAndRender() {
    if (state.loading) return; // защита от двойных запросов
    state.loading = true;

    var content = document.getElementById("diary-content");
    if (content) {
      content.innerHTML = skeletonHtml();
    }

    App.api
      .getDiary(state.date)
      .then(function (day) {
        // Кладём в кэш состояния приложения (необязательно, но полезно).
        if (App.state && App.state.diaryByDate) {
          App.state.diaryByDate[state.date] = day;
        }
        renderDay(day);
        // Переход из AI-тренера «Что съесть?»: сразу открываем панель
        // подбора блюд (флаг одноразовый, ставит page-trainer.js).
        if (App.state && App.state.diaryOpenSuggest) {
          App.state.diaryOpenSuggest = false;
          onSheetAction("recommend");
        }
      })
      .catch(function (err) {
        renderError(
          (err && err.message) ||
          pick("Проблема с сетью. Проверьте соединение.", "Network problem. Check your connection.")
        );
      })
      .then(function () {
        // finally-аналог: снимаем флаг загрузки в любом случае.
        state.loading = false;
      });
  }

  /**
   * Обработчик клика по кнопке удаления записи.
   * Удаление необратимо, а кнопка стоит впритык к зоне открытия редактора,
   * поэтому сначала спрашиваем подтверждение в нижнем листе — тем же
   * паттерном, что и всё остальное на этом экране.
   * @param {Event} ev
   */
  function onDeleteClick(ev) {
    // Тап по кнопке удаления не должен заодно открыть редактор записи.
    ev.stopPropagation();

    var btn = ev.currentTarget;
    var id = parseInt(btn.getAttribute("data-id"), 10);
    if (isNaN(id)) return;
    if (btn.disabled) return;

    var entry = findEntryById(id);
    var name = (entry && entry.dish_name) || pick("Без названия", "Untitled");

    App.haptic && App.haptic("light");
    openConfirmSheet({
      title: pick("Удалить запись?", "Delete entry?"),
      text: name,
      confirmLabel: pick("Удалить", "Delete"),
      onConfirm: function () {
        deleteEntry(id);
      }
    });
  }

  /**
   * Собственно удаление записи (вызывается после подтверждения).
   * @param {number} id
   */
  function deleteEntry(id) {
    App.showLoading();

    App.api
      .deleteEntry(id)
      .then(function () {
        App.haptic && App.haptic("success");
        App.toast(pick("Запись удалена", "Entry deleted"));
        // Сбрасываем кэш по дате и перезагружаем актуальные данные.
        if (App.state && App.state.diaryByDate) {
          delete App.state.diaryByDate[state.date];
        }
        loadAndRender();
      })
      .catch(function (err) {
        App.haptic && App.haptic("error");
        App.toast((err && err.message) || pick("Не удалось удалить запись", "Failed to delete entry"));
      })
      .then(function () {
        App.hideLoading();
      });
  }

  // ===========================================================================
  // ОБЩАЯ МЕХАНИКА НИЖНИХ ЛИСТОВ.
  //
  // На экране ОДИН паттерн для всего, что требует формы или решения: панель,
  // приезжающая снизу. Прежде добавление блюда жило inline-панелью посреди
  // страницы, а правка того же самого объекта — нижним листом: две разные
  // формы для одной сущности, и обе надо было держать в голове.
  // ===========================================================================

  // Идентификаторы листов страницы. Собраны в одном месте, чтобы закрывать
  // их разом при уходе со страницы и не искать по коду.
  var SHEET_ACTIONS = "diary-sheet";        // выбор способа добавления
  var SHEET_FOOD = "diary-food-sheet";      // добавление/правка блюда
  var SHEET_ACTIVITY = "diary-activity-sheet"; // ручной ввод тренировки
  var SHEET_CONFIRM = "diary-confirm-sheet";   // подтверждение удаления
  var SHEET_IDS = [SHEET_ACTIONS, SHEET_FOOD, SHEET_ACTIVITY, SHEET_CONFIRM];

  /**
   * Создаёт и показывает нижний лист с заданной разметкой панели.
   * @param {string} id уникальный id элемента листа
   * @param {string} panelHtml разметка ВНУТРИ .diary-sheet__panel
   * @returns {HTMLElement|null} корневой элемент листа (null, если уже открыт)
   */
  function mountSheet(id, panelHtml) {
    if (document.getElementById(id)) return null; // не открываем повторно
    var host = document.getElementById("view") || state.viewEl;
    if (!host) return null;

    var sheet = document.createElement("div");
    sheet.id = id;
    sheet.className = "diary-sheet";
    sheet.innerHTML =
      '<div class="diary-sheet__backdrop"></div>' +
      '<div class="diary-sheet__panel">' +
      '<div class="diary-sheet__handle"></div>' +
      panelHtml +
      "</div>";

    host.appendChild(sheet);
    // Принудительный пересчёт layout: браузер обязан «увидеть» закрытое
    // состояние ДО добавления класса, иначе перехода не будет и лист
    // появится рывком. Раньше здесь был requestAnimationFrame, но его колбэк
    // в фоновой вкладке не вызывается — лист навсегда оставался за краем.
    void sheet.offsetHeight;
    sheet.classList.add("diary-sheet--open");

    // Тап по затемнению — отмена. Одинаково во всех листах.
    var backdrop = sheet.querySelector(".diary-sheet__backdrop");
    if (backdrop) {
      backdrop.addEventListener("click", function () {
        closeSheetById(id);
      });
    }
    return sheet;
  }

  /**
   * Закрывает лист по id.
   * @param {string} id
   * @param {boolean} [immediate] снять из DOM сразу, без анимации
   */
  function closeSheetById(id, immediate) {
    var sheet = document.getElementById(id);
    if (!sheet) return;
    if (immediate) {
      if (sheet.parentNode) sheet.parentNode.removeChild(sheet);
      return;
    }
    sheet.classList.remove("diary-sheet--open");
    // Удаляем после анимации закрытия (длительность совпадает с CSS).
    setTimeout(function () {
      if (sheet.parentNode) sheet.parentNode.removeChild(sheet);
    }, 220);
  }

  /**
   * Закрывает все листы страницы (уход со страницы, смена даты).
   * @param {boolean} [immediate]
   */
  function closeAllSheets(immediate) {
    for (var i = 0; i < SHEET_IDS.length; i++) {
      closeSheetById(SHEET_IDS[i], immediate);
    }
  }

  /**
   * Прокручивает панель открытого листа к началу — после автозаполнения формы
   * из «Вчера»/«Недавние» человек должен увидеть, что именно подставилось.
   * @param {string} id id листа
   */
  function scrollSheetToTop(id) {
    var sheet = document.getElementById(id);
    var panel = sheet && sheet.querySelector(".diary-sheet__panel");
    if (!panel) return;
    try {
      panel.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      panel.scrollTop = 0;
    }
  }

  /**
   * Нижний лист подтверждения необратимого действия.
   * @param {Object} opts {title, text, confirmLabel, onConfirm}
   */
  function openConfirmSheet(opts) {
    opts = opts || {};
    var html =
      '<h2 class="diary-sheet__title">' +
      App.escapeHtml(opts.title || pick("Удалить?", "Delete?")) + "</h2>" +
      (opts.text
        ? '<p class="diary-sheet__text">' + App.escapeHtml(opts.text) + "</p>"
        : "") +
      '<button type="button" class="btn btn-danger btn-block diary-confirm__yes">' +
      icon("trash", { size: 18 }) +
      App.escapeHtml(opts.confirmLabel || pick("Удалить", "Delete")) + "</button>" +
      '<button type="button" class="btn btn--ghost btn-block diary-confirm__no">' +
      App.escapeHtml(pick("Отмена", "Cancel")) + "</button>";

    var sheet = mountSheet(SHEET_CONFIRM, html);
    if (!sheet) return;

    var yes = sheet.querySelector(".diary-confirm__yes");
    if (yes) {
      yes.addEventListener("click", function () {
        closeSheetById(SHEET_CONFIRM);
        if (typeof opts.onConfirm === "function") opts.onConfirm();
      });
    }
    var no = sheet.querySelector(".diary-confirm__no");
    if (no) {
      no.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        closeSheetById(SHEET_CONFIRM);
      });
    }
  }

  // ===========================================================================
  // ВАЛИДАЦИЯ ФОРМ НА МЕСТЕ.
  //
  // Тост говорит, ЧТО не так, но не говорит ГДЕ: человеку приходилось искать
  // проблемное поле глазами. Поэтому поле ещё и подсвечивается, получает
  // фокус и прокручивается в зону видимости, а подсветка снимается при первом
  // же вводе — ошибка не должна «висеть» после исправления.
  // ===========================================================================

  /**
   * Помечает поле ошибочным: подсветка + фокус + тост.
   * @param {HTMLElement} field поле ввода
   * @param {string} message текст для тоста (уже локализованный)
   * @returns {boolean} всегда false — удобно писать `return invalidField(...)`
   */
  function invalidField(field, message) {
    if (field) {
      field.classList.add("is-invalid");
      var wrap = field.closest ? field.closest(".field") : null;
      if (wrap) wrap.classList.add("field--invalid");
      try {
        field.focus({ preventScroll: true });
      } catch (e) {
        try { field.focus(); } catch (e2) {}
      }
      if (typeof field.scrollIntoView === "function") {
        try {
          field.scrollIntoView({ block: "center", behavior: "smooth" });
        } catch (e3) {
          field.scrollIntoView();
        }
      }
    }
    App.haptic && App.haptic("error");
    if (message) App.toast(message);
    return false;
  }

  /**
   * Снимает подсветку ошибки с поля.
   * @param {HTMLElement} field
   */
  function clearInvalid(field) {
    if (!field) return;
    field.classList.remove("is-invalid");
    var wrap = field.closest ? field.closest(".field") : null;
    if (wrap) wrap.classList.remove("field--invalid");
  }

  /**
   * Включает снятие подсветки по первому вводу во всех полях формы.
   * @param {HTMLElement} form
   */
  function bindLiveValidation(form) {
    if (!form) return;
    var fields = form.querySelectorAll(".field__input");
    for (var i = 0; i < fields.length; i++) {
      (function (el) {
        var handler = function () { clearInvalid(el); };
        el.addEventListener("input", handler);
        el.addEventListener("change", handler);
      })(fields[i]);
    }
  }

  // ===========================================================================
  // ЛИСТ БЛЮДА: ДОБАВЛЕНИЕ И ПРАВКА — ОДНА ФОРМА.
  //
  // Тап по строке записи открывает её же на правку. Разница между режимами
  // только в предзаполнении, подписи кнопки, вызываемом методе API и блоках
  // быстрого повтора («Недавние»/«Вчера»), которые нужны лишь при добавлении.
  // ===========================================================================

  /**
   * Обработчик тапа по строке записи — открывает лист правки.
   * @param {Event} ev
   */
  function onEntryRowClick(ev) {
    // Клик по кнопке удаления обрабатывает onDeleteClick — не открываем правку.
    if (ev.target.closest(".diary-entry__del")) return;
    var li = ev.currentTarget;
    var id = parseInt(li.getAttribute("data-id"), 10);
    if (isNaN(id)) return;
    var entry = findEntryById(id);
    if (entry) openFoodSheet(entry);
  }

  /**
   * Разметка одного числового поля макроса.
   * @param {string} name имя поля
   * @param {string} label подпись (локализованная)
   * @param {number|null} val текущее значение (null -> пусто)
   * @returns {string}
   */
  function macroFieldHtml(name, label, val) {
    return (
      '<label class="field diary-manual__macro">' +
      '<span class="field__label">' + App.escapeHtml(label) + "</span>" +
      '<input class="field__input" type="number" name="' + name + '" ' +
      'inputmode="decimal" min="0" step="0.1" placeholder="0" value="' +
      App.escapeHtml(val != null ? String(val) : "") + '"></label>'
    );
  }

  /**
   * Разметка селекта единицы измерения с пустым вариантом «—»:
   * запись может быть без единицы (например, «тарелка супа»).
   * @param {string} selected выбранный ключ ("" — не выбрано)
   * @returns {string}
   */
  function unitOptionsHtml(selected) {
    var opts =
      '<option value=""' + (selected === "" ? " selected" : "") + ">—</option>";
    for (var i = 0; i < UNIT_KEYS.length; i++) {
      var k = UNIT_KEYS[i];
      opts +=
        '<option value="' + k + '"' + (k === selected ? " selected" : "") + ">" +
        App.escapeHtml(unitLabel(k)) + "</option>";
    }
    return opts;
  }

  /**
   * Открывает нижний лист блюда.
   * @param {Object|null} entry DiaryEntryOut для правки, либо null — добавление
   * @param {string} [presetMeal] приём пищи по умолчанию (для добавления)
   */
  function openFoodSheet(entry, presetMeal) {
    if (document.getElementById(SHEET_FOOD)) return;
    // Лист выбора способа своё дело сделал — убираем сразу, без анимации,
    // чтобы два листа не наезжали друг на друга.
    closeSheetById(SHEET_ACTIONS, true);
    App.haptic && App.haptic("light");

    var isEdit = !!entry;
    var e = entry || {};
    // Приём пищи: при правке — свой, при добавлении — переданный или по часам.
    var meal = isEdit
      ? (e.meal_type || "breakfast")
      : (presetMeal || mealByHour());
    var curUnit = (e.unit && UNIT_KEYS.indexOf(e.unit) !== -1)
      ? e.unit
      : (isEdit ? "" : "g");

    var title = isEdit
      ? pick("Редактировать запись", "Edit entry")
      : pick("Добавить блюдо", "Add a dish");
    var submitLabel = isEdit
      ? pick("Сохранить", "Save")
      : pick("Добавить в рацион", "Add to diary");

    var html =
      '<h2 class="diary-sheet__title">' + App.escapeHtml(title) + "</h2>" +
      '<form class="diary-manual__form" id="diary-food-form" novalidate>' +
      // Название блюда.
      '<label class="field">' +
      '<span class="field__label">' + App.escapeHtml(pick("Название блюда", "Dish name")) + "</span>" +
      '<input class="field__input" type="text" name="dish_name" autocomplete="off" ' +
      'placeholder="' + App.escapeHtml(pick("Например, овсянка с бананом", "e.g. oatmeal with banana")) +
      '" maxlength="120" value="' + App.escapeHtml(e.dish_name || "") + '" required>' +
      "</label>" +
      // Подсказки из базы продуктов: появляются по мере ввода названия.
      '<div id="fsearch" class="fsearch"></div>' +
      // Количество + единица.
      '<div class="manual-qty-row">' +
      '<label class="field manual-qty-field">' +
      '<span class="field__label">' + App.escapeHtml(pick("Количество", "Quantity")) + "</span>" +
      '<input class="field__input manual-qty" type="number" name="quantity" ' +
      'inputmode="decimal" min="0" step="any" placeholder="1" value="' +
      (e.quantity != null ? App.escapeHtml(String(e.quantity)) : "") + '">' +
      "</label>" +
      '<label class="field manual-unit-field">' +
      '<span class="field__label">' + App.escapeHtml(pick("Единица", "Unit")) + "</span>" +
      '<select class="field__input manual-unit" name="unit">' +
      unitOptionsHtml(curUnit) + "</select>" +
      "</label>" +
      "</div>" +
      // Кнопка расчёта КБЖУ + подсказка загрузки.
      '<button type="button" class="btn btn--ghost btn-block manual-calc">' +
      icon("sparkle", { size: 18 }) +
      App.escapeHtml(pick("Рассчитать КБЖУ", "Calculate")) + "</button>" +
      '<p class="manual-calc-hint" hidden></p>' +
      // Калории.
      '<label class="field">' +
      '<span class="field__label">' + App.escapeHtml(pick("Калории, ккал", "Calories, kcal")) + "</span>" +
      '<input class="field__input" type="number" name="calories" ' +
      'inputmode="numeric" min="0" step="1" placeholder="0" value="' +
      (e.calories != null ? App.escapeHtml(String(e.calories)) : "") + '" required>' +
      "</label>" +
      // Б / Ж / У в одну строку.
      '<div class="diary-manual__macros">' +
      macroFieldHtml("proteins", pick("Белки, г", "Protein, g"), isEdit ? e.proteins : null) +
      macroFieldHtml("fats", pick("Жиры, г", "Fat, g"), isEdit ? e.fats : null) +
      macroFieldHtml("carbs", pick("Углеводы, г", "Carbs, g"), isEdit ? e.carbs : null) +
      "</div>" +
      // Селектор приёма пищи.
      '<div class="diary-manual__meal">' +
      '<span class="field__label">' + App.escapeHtml(pick("Приём пищи", "Meal")) + "</span>" +
      mealChipsHtml(meal, "food-meal") +
      "</div>" +
      '<button class="btn btn--cta btn-block diary-manual__submit" type="submit">' +
      App.escapeHtml(submitLabel) + "</button>" +
      '<button type="button" class="btn btn--ghost btn-block diary-manual__cancel">' +
      App.escapeHtml(pick("Отмена", "Cancel")) + "</button>" +
      "</form>" +
      // Быстрый повтор нужен только при добавлении: в правке он бы затирал
      // то, что человек как раз пришёл поправить.
      (isEdit
        ? ""
        : '<div id="diary-recent" class="yday"></div>' +
          '<div id="diary-yday" class="yday"></div>');

    var sheet = mountSheet(SHEET_FOOD, html);
    if (!sheet) return;

    // Контекст формы: выбранный приём пищи, база пересчёта КБЖУ по количеству
    // и флаг ручного переопределения макросов пользователем.
    var ctx = {
      manualMeal: meal,
      // База «на единицу количества»: {cals, p, f, c} либо null (нет расчёта).
      perUnit: null,
      // При правке значения уже выставлены человеком/сервером — пересчитывать
      // их по количеству нельзя, иначе правка количества затрёт КБЖУ.
      manualOverride: isEdit
    };

    var form = sheet.querySelector("#diary-food-form");

    // Переключение приёма пищи.
    var mealsWrap = sheet.querySelector(".diary-manual__meal .meal-chips");
    if (mealsWrap) {
      mealsWrap.addEventListener("click", function (ev) {
        var btn = ev.target.closest(".meal-chip");
        if (!btn) return;
        var t = btn.getAttribute("data-food-meal");
        if (!t) return;
        ctx.manualMeal = t;
        App.haptic && App.haptic("light");
        var all = mealsWrap.querySelectorAll(".meal-chip");
        for (var i = 0; i < all.length; i++) {
          all[i].classList.toggle(
            "is-active",
            all[i].getAttribute("data-food-meal") === t
          );
        }
      });
    }

    if (form) {
      // Ручная правка любого КБЖУ-поля отключает авто-пересчёт по количеству.
      var macroFields = ["calories", "proteins", "fats", "carbs"];
      for (var mf = 0; mf < macroFields.length; mf++) {
        var el = form[macroFields[mf]];
        if (el) {
          el.addEventListener("input", function () {
            ctx.manualOverride = true;
          });
        }
      }

      // Живой пересчёт КБЖУ при изменении количества (если есть база расчёта
      // и человек не правил значения сам).
      var qtyInput = form.quantity;
      if (qtyInput) {
        qtyInput.addEventListener("input", function () {
          rescaleMacros(form, ctx);
        });
      }

      var calcBtn = sheet.querySelector(".manual-calc");
      if (calcBtn) {
        calcBtn.addEventListener("click", function () {
          calcManualMacros(form, ctx, calcBtn);
        });
      }

      var cancelBtn = sheet.querySelector(".diary-manual__cancel");
      if (cancelBtn) {
        cancelBtn.addEventListener("click", function () {
          App.haptic && App.haptic("light");
          closeSheetById(SHEET_FOOD);
        });
      }

      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        submitFoodSheet(form, ctx, isEdit ? e.id : null);
      });

      // Подсветка ошибки снимается при первом же вводе.
      bindLiveValidation(form);

      // Поиск блюд в базе продуктов по мере ввода названия.
      bindFoodSearch(form, ctx);

      // Блоки быстрого повтора (только в режиме добавления).
      if (!isEdit) {
        loadRecent(form, ctx);
        loadYesterday(form, ctx);
      }

      // Фокус в название сразу: чаще всего человек пришёл именно печатать.
      if (!isEdit && form.dish_name) {
        try { form.dish_name.focus({ preventScroll: true }); } catch (err) {}
      }
    }
  }

  /**
   * Валидирует лист блюда и сохраняет его: добавление (addManualFood) либо
   * правка (updateEntry). Обе ветки ведут к перезагрузке дня.
   * @param {HTMLFormElement} form форма листа
   * @param {Object} ctx контекст формы (manualMeal)
   * @param {number|null} entryId id записи при правке, иначе null
   */
  function submitFoodSheet(form, ctx, entryId) {
    var name = (form.dish_name.value || "").trim();
    if (!name) {
      invalidField(form.dish_name, pick("Укажите название блюда", "Enter a dish name"));
      return;
    }

    var caloriesRaw = (form.calories.value || "").trim();
    var calories = Number(caloriesRaw);
    if (caloriesRaw === "" || !isFinite(calories) || calories < 0) {
      invalidField(
        form.calories,
        pick("Укажите калорийность блюда", "Enter the dish calories")
      );
      return;
    }

    // Количество/единица необязательны. Пустое или отрицательное -> null.
    var qtyRaw = (form.quantity && form.quantity.value ? form.quantity.value : "").trim();
    var quantity = qtyRaw === "" ? null : Number(qtyRaw);
    if (quantity != null && (!isFinite(quantity) || quantity < 0)) {
      invalidField(
        form.quantity,
        pick("Количество не может быть отрицательным", "Quantity can’t be negative")
      );
      return;
    }
    var unit = form.unit ? (form.unit.value || null) : null;

    var mealType = ctx.manualMeal;
    var payload = {
      meal_type: mealType,
      dish_name: name,
      calories: Math.round(calories),
      proteins: Number(form.proteins.value) || 0,
      fats: Number(form.fats.value) || 0,
      carbs: Number(form.carbs.value) || 0,
      quantity: quantity,
      unit: unit
    };

    var submitBtn = form.querySelector(".diary-manual__submit");
    var isEdit = entryId != null;
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = isEdit
        ? pick("Сохраняем…", "Saving…")
        : pick("Добавляем…", "Adding…");
    }
    App.showLoading();

    var request;
    if (isEdit) {
      request = App.api.updateEntry(entryId, payload);
    } else {
      // Дата нужна только при создании: у существующей записи она уже своя.
      payload.date = state.date;
      request = App.api.addManualFood(payload);
    }

    request
      .then(function () {
        App.haptic && App.haptic("success");
        App.toast(
          isEdit
            ? pick("Изменения сохранены", "Changes saved")
            : pick("Добавлено: ", "Added: ") + App.mealLabel(mealType)
        );
        closeSheetById(SHEET_FOOD);
        if (App.state && App.state.diaryByDate) {
          delete App.state.diaryByDate[state.date];
        }
        loadAndRender();
      })
      .catch(function (err) {
        App.haptic && App.haptic("error");
        App.toast(
          (err && err.message) ||
          (isEdit
            ? pick("Не удалось сохранить", "Failed to save")
            : pick("Не удалось добавить блюдо", "Failed to add dish"))
        );
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = isEdit
            ? pick("Сохранить", "Save")
            : pick("Добавить в рацион", "Add to diary");
        }
      })
      .then(function () {
        App.hideLoading();
      });
  }

  // ===========================================================================
  // МИНИ-КАЛЕНДАРЬ (block, тап по подписи даты).
  //
  // Компактный попап под баром датой (класс cal-pop). Реализация вынесена в
  // общий хелпер App.miniCalendarToggle / App.miniCalendarClose (см. app.js),
  // который переиспользуется дневником и тренировками. Здесь остаётся только
  // тонкая обвязка: колбэк выбора дня и закрытие календаря.
  // ===========================================================================

  /**
   * Закрывает мини-календарь (делегирует общему хелперу).
   */
  function closeCalendar() {
    App.miniCalendarClose(document.getElementById("diary-cal"));
  }

  /**
   * Выбирает день из календаря: меняет дату, закрывает панель действий и
   * календарь, перезагружает день.
   * @param {string} iso "YYYY-MM-DD"
   */
  function pickCalendarDay(iso) {
    App.haptic && App.haptic("selection");
    state.date = iso;
    // Панель и листы относились к прежнему дню — закрываем.
    state.panel = null;
    var panel = document.getElementById("diary-panel");
    if (panel) panel.innerHTML = "";
    closeAllSheets();
    closeCalendar();
    updateDateLabel();
    loadAndRender();
  }

  // ===========================================================================
  // ПЛАВАЮЩАЯ «+» И ЛИСТ ВЫБОРА СПОСОБА.
  //
  // Камеры в таббаре больше нет: «+» — главный вход в добавление еды, и
  // «Сфотографировать» стоит в её листе первым пунктом.
  // ===========================================================================

  /**
   * Строит плавающую кнопку «+» и добавляет её в обёртку страницы,
   * чтобы она оставалась поверх контента при его перерисовке.
   * Кнопка монтируется СКРЫТОЙ: показать её или нет, решает renderDay, когда
   * станет известно, пуст ли день (см. setFabVisible). Иначе на пустом дне
   * «+» на мгновение появлялась поверх кнопки «Добавить» и тут же исчезала.
   */
  function mountFab() {
    if (!state.viewEl) return;
    // Не дублируем FAB, если он уже смонтирован.
    if (state.viewEl.querySelector(".diary-fab")) return;

    var host = state.viewEl.querySelector(".page-diary") || state.viewEl;
    var fab = document.createElement("button");
    fab.type = "button";
    fab.className = "diary-fab";
    fab.hidden = true;
    fab.setAttribute("aria-label", pick("Добавить", "Add"));
    // Штрих толще обычного: белый плюс на зелёном круге должен читаться
    // с расстояния вытянутой руки, а не теряться тонкой линией.
    fab.innerHTML = icon("plus", { size: 28, stroke: 2.25 });
    fab.addEventListener("click", function () {
      App.haptic && App.haptic("light");
      openSheet();
    });
    host.appendChild(fab);
  }

  /**
   * Показывает или прячет плавающую «+».
   * На пустом дне её нет: у пустого состояния своя кнопка «Добавить», и две
   * одинаковые кнопки стояли друг над другом (на скриншоте владельца круг
   * наезжал на «Добавить»). Когда записи есть, пустого состояния нет — и «+»
   * снова единственный вход в добавление.
   * @param {boolean} visible
   */
  function setFabVisible(visible) {
    if (!state.viewEl) return;
    var fab = state.viewEl.querySelector(".diary-fab");
    if (fab) fab.hidden = !visible;
  }

  /**
   * Убирает плавающую кнопку и все листы из DOM (при уходе со страницы).
   */
  function unmountFab() {
    if (!state.viewEl) return;
    var fab = state.viewEl.querySelector(".diary-fab");
    if (fab && fab.parentNode) fab.parentNode.removeChild(fab);
    closeAllSheets(true);
  }

  /**
   * Разметка одного пункта листа выбора способа.
   * @param {string} action ключ действия (data-sheet-action)
   * @param {string} iconName имя иконки из общего набора
   * @param {string} label подпись (локализованная)
   * @param {string} hint пояснение, чем этот способ отличается от соседнего
   * @param {boolean} locked показывать ли замок (для free)
   * @param {boolean} [primary] главный пункт листа (выделен цветом действия)
   * @returns {string}
   */
  function sheetItemHtml(action, iconName, label, hint, locked, primary) {
    var cls = "diary-sheet__item" +
      (locked ? " diary-sheet__item--locked" : "") +
      (primary ? " diary-sheet__item--primary" : "");
    var lock = locked
      ? '<span class="diary-sheet__item-lock">' + icon("lock", { size: 16 }) + "</span>"
      : "";
    return (
      '<button type="button" class="' + cls + '" data-sheet-action="' + action + '">' +
      '<span class="diary-sheet__item-icon">' + icon(iconName, { size: 22 }) + "</span>" +
      '<span class="diary-sheet__item-body">' +
      '<span class="diary-sheet__item-label">' + App.escapeHtml(label) + "</span>" +
      (hint ? '<span class="diary-sheet__item-hint">' + App.escapeHtml(hint) + "</span>" : "") +
      "</span>" +
      lock +
      "</button>"
    );
  }

  /**
   * Открывает лист выбора способа добавления.
   */
  function openSheet() {
    var locked = !isPremium();

    var html =
      '<div class="diary-sheet__group">' +
      '<div class="diary-sheet__group-title">' +
      App.escapeHtml(pick("Добавить в день", "Add to the day")) + "</div>" +
      // Фото — первый и главный пункт: камеры в таббаре больше нет, и это
      // самый быстрый способ записать еду. Снимок ложится в ВЫБРАННЫЙ день.
      sheetItemHtml("photo", "camera", pick("Сфотографировать", "Take a photo"),
        pick("ИИ посчитает калории по снимку", "AI counts calories from the photo"), false, true) +
      // Голос — премиум-функция (бэкенд отдаёт 402 для free): показываем замок,
      // но пункт остаётся тапабельным (уводит в paywall на экране определения).
      sheetItemHtml("voice", "mic", pick("Голосом", "By voice"),
        pick("Продиктовать, что съели", "Say what you ate"), locked) +
      sheetItemHtml("manual", "edit", pick("Вручную", "Manual"),
        pick("Название и КБЖУ", "Name and macros"), false) +
      // Расход калорий — часть баланса дня, поэтому активность живёт здесь же.
      sheetItemHtml("activity", "run", pick("Активность", "Activity"),
        pick("Тренировка и сожжённые калории", "Workout and burned calories"), locked) +
      "</div>" +
      '<div class="diary-sheet__group">' +
      '<div class="diary-sheet__group-title">' +
      App.escapeHtml(pick("AI-помощник", "AI assistant")) + "</div>" +
      sheetItemHtml("recommend", "sparkle", pick("Что съесть?", "What to eat?"),
        pick("Подбор под остаток нормы", "Fits your remaining allowance"), locked) +
      sheetItemHtml("meal-plan", "utensils", pick("AI-план меню", "AI meal plan"),
        pick("Меню и список покупок", "Menu and shopping list"), locked) +
      "</div>";

    var sheet = mountSheet(SHEET_ACTIONS, html);
    if (!sheet) return;

    // Обработка выбора пункта (делегированием).
    var panel = sheet.querySelector(".diary-sheet__panel");
    if (panel) {
      panel.addEventListener("click", function (ev) {
        var item = ev.target.closest(".diary-sheet__item");
        if (!item) return;
        onSheetAction(item.getAttribute("data-sheet-action"));
      });
    }
  }

  /**
   * Прокручивает область AI-панели в зону видимости.
   */
  function scrollPanelIntoView() {
    var panel = document.getElementById("diary-panel");
    if (panel && typeof panel.scrollIntoView === "function") {
      try {
        panel.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (e) {
        panel.scrollIntoView();
      }
    }
  }

  /**
   * Уводит на экран камеры (фото или сразу голос).
   * Флаги для page-scan:
   *   scanDate   — запись ляжет в выбранный в дневнике день, а не в «сегодня»;
   *   scanMode   — "voice": камера сразу откроет голосовой ввод;
   *   scanOrigin — камера без таббара, и её «Закрыть» вернёт сюда.
   * @param {string} mode "photo" | "voice"
   */
  function openScan(mode) {
    if (App.state) {
      App.state.scanDate = state.date;
      App.state.scanOrigin = "diary";
      if (mode === "voice") App.state.scanMode = "voice";
    }
    if (App && typeof App.navigate === "function") App.navigate("scan");
  }

  /**
   * Обрабатывает выбор пункта листа. Сначала закрывает лист.
   * @param {string} action
   */
  function onSheetAction(action) {
    App.haptic && App.haptic("light");
    closeSheetById(SHEET_ACTIONS);

    if (action === "photo" || action === "voice") {
      openScan(action);
      return;
    }
    if (action === "manual") {
      openFoodSheet(null, mealByHour());
      return;
    }
    if (action === "activity") {
      // Журнал тренировок на бэкенде закрыт подпиской — free показываем
      // paywall вместо формы, которая всё равно получит 402 при сохранении.
      if (isPremium()) {
        openActivitySheet();
      } else {
        state.panel = "activity";
        openActivityPaywall();
        scrollPanelIntoView();
      }
      return;
    }
    if (action === "recommend") {
      state.panel = "recommend";
      if (isPremium()) {
        openRecommendPanel();
      } else {
        openRecommendPaywall();
      }
      scrollPanelIntoView();
      return;
    }
    if (action === "meal-plan") {
      state.panel = "meal-plan";
      if (isPremium()) {
        openMealPlanPanel();
      } else {
        openMealPlanPaywall();
      }
      scrollPanelIntoView();
      return;
    }
  }

  // ===========================================================================
  // ДЕЙСТВИЯ: рекомендации, AI-план меню (paywall для free).
  // ===========================================================================

  /**
   * Показывает единый paywall для платной фичи «Что съесть?» в области панели
   * действий (без ухода со страницы). Кнопка внутри ведёт на экран подписки.
   * Контроль доступа серверный — это лишь визуальная заглушка.
   */
  function openRecommendPaywall() {
    var panel = document.getElementById("diary-panel");
    if (!panel) return;

    if (App && typeof App.paywall === "function") {
      // Иконку не передаём: App.paywall экранирует её как текст, то есть
      // принимает только символ, а не разметку App.icon. Пусть рисует свою.
      App.paywall(panel, {
        title: pick("Что съесть?", "What to eat?"),
        desc: pick(
          "AI подберёт блюда под остаток вашей дневной нормы КБЖУ",
          "AI will suggest dishes to fit your remaining daily calories and macros"
        ),
        bullets: [
          pick("Персональные рекомендации под цель", "Personalized recommendations for your goal"),
          pick("Учёт остатка калорий и БЖУ за день", "Accounts for remaining calories and macros"),
          pick("Добавление подсказанного блюда в один тап", "Add a suggested dish in one tap")
        ]
      });
      return;
    }

    // Запасной вариант, если единый paywall недоступен — ведём в подписку кнопкой.
    panel.innerHTML =
      '<section class="card diary-recommend diary-recommend--locked">' +
      '<h2 class="diary-recommend__title">' + icon("lock", { size: 18 }) +
      App.escapeHtml(pick("Что съесть?", "What to eat?")) + "</h2>" +
      '<p class="diary-recommend__sub">' +
      App.escapeHtml(pick("AI-подсказки доступны по подписке.", "AI suggestions are available with a subscription.")) + "</p>" +
      '<button type="button" class="btn btn--cta btn-block diary-recommend__subscribe">' +
      App.escapeHtml(pick("Оформить подписку", "Get subscription")) + "</button>" +
      "</section>";
    var subBtn = panel.querySelector(".diary-recommend__subscribe");
    if (subBtn) {
      subBtn.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        if (App && typeof App.navigate === "function") App.navigate("subscription");
      });
    }
  }

  /**
   * Показывает единый paywall для платной фичи «AI-план меню» в области панели
   * действий. Контроль доступа серверный — это лишь визуальная заглушка.
   */
  function openMealPlanPaywall() {
    var panel = document.getElementById("diary-panel");
    if (!panel) return;

    if (App && typeof App.paywall === "function") {
      App.paywall(panel, {
        title: pick("AI-план меню", "AI meal plan"),
        desc: pick(
          "AI составит меню на день или неделю и соберёт список покупок",
          "AI will compose a day or week menu and build a shopping list"
        ),
        bullets: [
          pick("Меню по приёмам пищи с КБЖУ", "Menu by meals with calories and macros"),
          pick("Замена любого блюда в один тап", "Swap any dish in one tap"),
          pick("Готовый список покупок", "Ready-made shopping list")
        ]
      });
      return;
    }

    // Запасной вариант, если единый paywall недоступен — ведём в подписку кнопкой.
    panel.innerHTML =
      '<section class="card plan-locked">' +
      '<h2 class="plan-locked__title">' + icon("lock", { size: 18 }) +
      App.escapeHtml(pick("AI-план меню", "AI meal plan")) + "</h2>" +
      '<p class="plan-locked__sub">' +
      App.escapeHtml(pick("AI-планировщик меню доступен по подписке.", "The AI meal planner is available with a subscription.")) + "</p>" +
      '<button type="button" class="btn btn--cta btn-block plan-locked__subscribe">' +
      App.escapeHtml(pick("Оформить подписку", "Get subscription")) + "</button>" +
      "</section>";
    var subBtn = panel.querySelector(".plan-locked__subscribe");
    if (subBtn) {
      subBtn.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        if (App && typeof App.navigate === "function") App.navigate("subscription");
      });
    }
  }

  /**
   * Возвращает разметку чипов выбора приёма пищи.
   * Подписи берём из App.mealLabel — он уже локализован.
   * @param {string} selected выбранный тип
   * @param {string} attr     имя data-атрибута (например "manual-meal")
   * @returns {string}
   */
  function mealChipsHtml(selected, attr) {
    var chips = "";
    for (var i = 0; i < MEAL_ORDER.length; i++) {
      var t = MEAL_ORDER[i];
      var active = t === selected ? " is-active" : "";
      chips +=
        '<button type="button" class="meal-chip' + active + '" ' +
        "data-" + attr + '="' + t + '">' +
        App.escapeHtml(App.mealLabel(t)) +
        "</button>";
    }
    return '<div class="meal-chips">' + chips + "</div>";
  }


  // ---------------------------------------------------------------------------
  // ПОИСК БЛЮД В БАЗЕ ПРОДУКТОВ.
  //
  // Пользователь вводит название — под полем появляются варианты с готовыми
  // КБЖУ (на 100 г). Тап по варианту заполняет форму и включает пересчёт по
  // количеству: значения приходят на 100 г, поэтому база пересчёта = /100.
  //
  // ВАЖНО: у внешней базы жёсткий лимит запросов, поэтому ищем НЕ на каждую
  // букву — только после паузы в наборе и от 3 символов.
  // ---------------------------------------------------------------------------

  // Пауза после последнего нажатия клавиши перед запросом, мс.
  var FSEARCH_DEBOUNCE_MS = 500;
  // Минимальная длина запроса.
  var FSEARCH_MIN_CHARS = 3;

  /**
   * Навешивает поиск по базе продуктов на поле названия блюда.
   * @param {HTMLFormElement} form
   * @param {Object} ctx контекст формы листа
   */
  function bindFoodSearch(form, ctx) {
    if (!form || !form.dish_name) return;
    if (!(App.api && typeof App.api.searchFood === "function")) return;

    var timer = null;
    var lastQuery = "";
    // Номер запроса: ответы могут приходить не по порядку — рисуем только свежий.
    var seq = 0;

    form.dish_name.addEventListener("input", function () {
      var q = (form.dish_name.value || "").trim();

      if (timer) clearTimeout(timer);

      if (q.length < FSEARCH_MIN_CHARS) {
        renderFoodSearch([], form, ctx);
        lastQuery = "";
        return;
      }
      if (q === lastQuery) return;

      timer = setTimeout(function () {
        lastQuery = q;
        var mySeq = ++seq;
        renderFoodSearchLoading();
        App.api
          .searchFood(q)
          .then(function (res) {
            if (mySeq !== seq) return; // пришёл устаревший ответ
            renderFoodSearch((res && res.items) || [], form, ctx);
          })
          .catch(function () {
            if (mySeq !== seq) return;
            // Поиск вспомогательный: при сбое просто убираем подсказки.
            renderFoodSearch([], form, ctx);
          });
      }, FSEARCH_DEBOUNCE_MS);
    });
  }

  /**
   * Показывает индикатор загрузки в области подсказок.
   */
  function renderFoodSearchLoading() {
    var box = document.getElementById("fsearch");
    if (!box) return;
    box.innerHTML = '<div class="skeleton skeleton-line fsearch__skeleton"></div>';
  }

  /**
   * Рисует найденные продукты. Пустой список — область скрывается.
   * @param {Array} items результаты поиска
   * @param {HTMLFormElement} form
   * @param {Object} ctx
   */
  function renderFoodSearch(items, form, ctx) {
    var box = document.getElementById("fsearch");
    if (!box) return;

    if (!items || !items.length) {
      box.innerHTML = "";
      return;
    }

    var kcal = pick("ккал", "kcal");
    var per100 = pick("на 100 г", "per 100 g");
    var pLabel = pick("Б", "P");
    var fLabel = pick("Ж", "F");
    var cLabel = pick("У", "C");

    var rows = "";
    for (var i = 0; i < items.length; i++) {
      var it = items[i] || {};
      var brand = it.brand
        ? ' <span class="fsearch__brand">' + App.escapeHtml(it.brand) + "</span>"
        : "";
      var macros =
        App.fmt(it.calories || 0) + " " + kcal + " " + per100 +
        " · " + pLabel + " " + App.fmt(it.proteins || 0) +
        " · " + fLabel + " " + App.fmt(it.fats || 0) +
        " · " + cLabel + " " + App.fmt(it.carbs || 0);

      rows +=
        '<button type="button" class="fsearch__item" data-idx="' + i + '">' +
        '<span class="fsearch__name">' + App.escapeHtml(it.name || "") + brand + "</span>" +
        '<span class="fsearch__macros">' + App.escapeHtml(macros) + "</span>" +
        "</button>";
    }

    box.innerHTML =
      '<div class="fsearch__list">' + rows + "</div>" +
      '<p class="fsearch__hint">' +
      App.escapeHtml(pick(
        "Не нашли? Введите своё блюдо и нажмите «Рассчитать КБЖУ».",
        "Not found? Type your own dish and tap “Calculate”."
      )) +
      "</p>";

    var list = box.querySelector(".fsearch__list");
    list.addEventListener("click", function (ev) {
      var btn = ev.target.closest(".fsearch__item");
      if (!btn) return;
      var idx = parseInt(btn.getAttribute("data-idx"), 10);
      if (isNaN(idx) || !items[idx]) return;
      applyFoundFood(items[idx], form, ctx);
    });
  }

  /**
   * Заполняет форму выбранным продуктом. КБЖУ приходят на 100 г, поэтому
   * ставим количество 100 г и базу пересчёта per-unit = значение/100 —
   * дальше существующая логика сама пересчитает КБЖУ при смене количества.
   * @param {Object} it найденный продукт
   * @param {HTMLFormElement} form
   * @param {Object} ctx
   */
  function applyFoundFood(it, form, ctx) {
    App.haptic && App.haptic("light");

    if (form.dish_name) form.dish_name.value = it.name || "";
    if (form.quantity) form.quantity.value = 100;
    if (form.unit) form.unit.value = "g";

    var cals = Math.round(Number(it.calories) || 0);
    var p = round1(it.proteins || 0);
    var f = round1(it.fats || 0);
    var c = round1(it.carbs || 0);

    // Это НЕ ручная правка пользователя, а подстановка из базы: разрешаем
    // авто-пересчёт по количеству.
    ctx.manualOverride = false;
    ctx.perUnit = { cals: cals / 100, p: p / 100, f: f / 100, c: c / 100 };
    setMacroFields(form, cals, p, f, c);

    // Подсказки больше не нужны — прячем, чтобы не мешали.
    var box = document.getElementById("fsearch");
    if (box) box.innerHTML = "";
  }

  /**
   * Пересчитывает КБЖУ-поля пропорционально количеству, если есть база
   * per-unit и пользователь не переопределял значения вручную.
   * @param {HTMLFormElement} form
   * @param {Object} ctx контекст формы (perUnit, manualOverride)
   */
  function rescaleMacros(form, ctx) {
    if (!ctx.perUnit || ctx.manualOverride) return;
    var qty = Number(form.quantity.value);
    if (!isFinite(qty) || qty <= 0) return;

    // Пишем значения напрямую, не помечая manualOverride (это авто-расчёт).
    setMacroFields(
      form,
      Math.round(ctx.perUnit.cals * qty),
      round1(ctx.perUnit.p * qty),
      round1(ctx.perUnit.f * qty),
      round1(ctx.perUnit.c * qty)
    );
  }

  /**
   * Округление до одного знака после запятой (для макросов).
   * @param {number} v
   * @returns {number}
   */
  function round1(v) {
    return Math.round((Number(v) || 0) * 10) / 10;
  }

  /**
   * Записывает значения КБЖУ в поля формы (без побочных эффектов на флаги).
   * @param {HTMLFormElement} form
   * @param {number} calories
   * @param {number} proteins
   * @param {number} fats
   * @param {number} carbs
   */
  function setMacroFields(form, calories, proteins, fats, carbs) {
    if (form.calories) form.calories.value = calories;
    if (form.proteins) form.proteins.value = proteins;
    if (form.fats) form.fats.value = fats;
    if (form.carbs) form.carbs.value = carbs;
  }

  /**
   * Рассчитывает КБЖУ по названию/количеству/единице через App.api.calculateFood
   * и автозаполняет поля формы. Сохраняет базу per-unit для живого пересчёта.
   * @param {HTMLFormElement} form
   * @param {Object} ctx контекст формы
   * @param {HTMLElement} calcBtn кнопка «Рассчитать» (для блокировки)
   */
  function calcManualMacros(form, ctx, calcBtn) {
    var name = (form.dish_name.value || "").trim();
    if (!name) {
      // Тем же способом, что и при отправке формы: подсветка + фокус + тост.
      // Раньше здесь был голый тост — он говорил ЧТО не так, но не ГДЕ.
      invalidField(form.dish_name, pick("Укажите название блюда", "Enter a dish name"));
      return;
    }

    var qtyRaw = (form.quantity.value || "").trim();
    var quantity = qtyRaw === "" ? null : Number(qtyRaw);
    if (quantity != null && (!isFinite(quantity) || quantity < 0)) {
      quantity = null;
    }
    var unit = form.unit ? form.unit.value : "g";

    if (!(App.api && typeof App.api.calculateFood === "function")) {
      App.toast(pick("Расчёт временно недоступен.", "Calculation is temporarily unavailable."));
      return;
    }

    var hint = form.querySelector(".manual-calc-hint");
    if (calcBtn) calcBtn.disabled = true;
    App.haptic && App.haptic("light");
    if (hint) {
      hint.hidden = false;
      hint.textContent = pick("Считаем…", "Calculating…");
    }

    App.api
      .calculateFood({ name: name, quantity: quantity, unit: unit })
      .then(function (res) {
        res = res || {};
        var cals = Math.round(Number(res.calories) || 0);
        var p = round1(res.proteins || 0);
        var f = round1(res.fats || 0);
        var c = round1(res.carbs || 0);

        // Заполняем КБЖУ (это авто-расчёт — сбрасываем ручное переопределение).
        ctx.manualOverride = false;
        setMacroFields(form, cals, p, f, c);

        // Если сервер вернул количество/единицу — отражаем их в форме.
        var respQty = null;
        if (res.quantity != null && isFinite(Number(res.quantity))) {
          respQty = Number(res.quantity);
          if (form.quantity) form.quantity.value = respQty;
        }
        if (res.unit && form.unit) {
          // Проставляем только валидный канонический ключ.
          if (UNIT_KEYS.indexOf(res.unit) !== -1) form.unit.value = res.unit;
        }

        // База per-unit для живого пересчёта (только при положительном qty).
        var basisQty = respQty != null ? respQty
          : (quantity != null ? quantity : null);
        if (basisQty != null && basisQty > 0) {
          ctx.perUnit = {
            cals: cals / basisQty,
            p: p / basisQty,
            f: f / basisQty,
            c: c / basisQty
          };
        } else {
          ctx.perUnit = null;
        }

        App.haptic && App.haptic("success");
      })
      .catch(function (err) {
        App.haptic && App.haptic("error");
        App.toast((err && err.message) || pick("Не удалось рассчитать КБЖУ", "Failed to calculate"));
      })
      .then(function () {
        if (calcBtn) calcBtn.disabled = false;
        if (hint) {
          hint.hidden = true;
          hint.textContent = "";
        }
      });
  }

  // ---------------------------------------------------------------------------
  // Блок «Недавние» — быстрый повтор недавно добавленных блюд в один тап.
  // Источник — App.api.getRecentFoods (уникальные блюда за всё время). Тап по
  // телу автозаполняет форму листа; кнопка «плюс» добавляет сразу в приём по времени
  // суток. Если недавних нет — блок скрыт.
  // ---------------------------------------------------------------------------

  /**
   * Загружает «Недавние» блюда и рисует их. При ошибке — тихо скрываем блок.
   * @param {HTMLFormElement} form форма листа блюда
   * @param {Object} ctx контекст формы
   */
  function loadRecent(form, ctx) {
    var box = document.getElementById("diary-recent");
    if (!box) return;

    box.innerHTML =
      '<h3 class="yday__title">' + App.escapeHtml(pick("Недавние", "Recent")) + "</h3>" +
      '<div class="yday__list">' +
      '<div class="skeleton skeleton-block yday__skeleton"></div>' +
      "</div>";

    if (!(App.api && typeof App.api.getRecentFoods === "function")) {
      box.innerHTML = "";
      return;
    }

    App.api
      .getRecentFoods()
      .then(function (res) {
        renderRecent((res && res.items) || [], form, ctx);
      })
      .catch(function () {
        if (box) box.innerHTML = "";
      });
  }

  /**
   * Рисует список «Недавние». Тап по телу — автозаполнение формы; «плюс» —
   * прямое добавление в приём пищи по времени суток.
   * @param {Array} items список {dish_name, calories, proteins, fats, carbs}
   * @param {HTMLFormElement} form форма листа блюда
   * @param {Object} ctx контекст формы
   */
  function renderRecent(items, form, ctx) {
    var box = document.getElementById("diary-recent");
    if (!box) return;

    // Нет недавних — просто скрываем блок (не мешаем «Вчера»).
    if (!items.length) {
      box.innerHTML = "";
      return;
    }

    var kcal = pick("ккал", "kcal");
    var pLabel = pick("Б", "P");
    var fLabel = pick("Ж", "F");
    var cLabel = pick("У", "C");

    var rows = "";
    for (var i = 0; i < items.length; i++) {
      var it = items[i] || {};
      var macros =
        App.fmt(it.calories || 0) + " " + kcal +
        " · " + pLabel + " " + App.fmt(it.proteins || 0) +
        " · " + fLabel + " " + App.fmt(it.fats || 0) +
        " · " + cLabel + " " + App.fmt(it.carbs || 0);

      rows +=
        '<div class="yday__item" data-idx="' + i + '">' +
        '<button type="button" class="yday__body" data-idx="' + i + '">' +
        '<span class="yday__name">' +
        App.escapeHtml(it.dish_name || pick("Без названия", "Untitled")) + "</span>" +
        '<span class="yday__macros">' + App.escapeHtml(macros) + "</span>" +
        "</button>" +
        '<button type="button" class="yday__add" data-idx="' + i + '" ' +
        'aria-label="' + App.escapeHtml(pick("Добавить", "Add")) + '">' +
        icon("plus", { size: 20 }) + "</button>" +
        "</div>";
    }

    box.innerHTML =
      '<h3 class="yday__title">' + App.escapeHtml(pick("Недавние", "Recent")) + "</h3>" +
      '<div class="yday__list">' + rows + "</div>";

    var list = box.querySelector(".yday__list");
    if (list) {
      list.addEventListener("click", function (ev) {
        // «Плюс» — прямое добавление в приём по времени суток (у недавних нет meal_type).
        var addBtn = ev.target.closest(".yday__add");
        if (addBtn) {
          var addIdx = parseInt(addBtn.getAttribute("data-idx"), 10);
          if (!isNaN(addIdx) && items[addIdx]) {
            quickAdd(items[addIdx], ctx.manualMeal || mealByHour(), addBtn);
          }
          return;
        }
        // Тап по телу — автозаполнение формы листа.
        var body = ev.target.closest(".yday__body");
        if (body) {
          var idx = parseInt(body.getAttribute("data-idx"), 10);
          if (!isNaN(idx) && items[idx]) {
            fillManualFromRecent(items[idx], form, ctx);
          }
        }
      });
    }
  }

  /**
   * Автозаполняет форму листа блюда значениями недавнего (без количества/
   * единицы — их у недавних нет). Значения берутся как есть.
   * @param {Object} it блюдо из «Недавние»
   * @param {HTMLFormElement} form форма листа блюда
   * @param {Object} ctx контекст формы
   */
  function fillManualFromRecent(it, form, ctx) {
    if (!form) return;
    App.haptic && App.haptic("light");

    if (form.dish_name) form.dish_name.value = it.dish_name || "";
    if (form.quantity) form.quantity.value = "";

    ctx.manualOverride = true;
    ctx.perUnit = null;
    setMacroFields(
      form,
      Math.round(Number(it.calories) || 0),
      round1(it.proteins || 0),
      round1(it.fats || 0),
      round1(it.carbs || 0)
    );

    scrollSheetToTop(SHEET_FOOD);
  }

  // ---------------------------------------------------------------------------
  // Блок «Вчера» (block 3.2) — быстрое добавление вчерашних блюд в один тап.
  // ---------------------------------------------------------------------------

  /**
   * Загружает список блюд «за вчера» и рисует их. При ошибке — тихо скрываем.
   * @param {HTMLFormElement} form форма листа блюда (для автозаполнения по тапу)
   * @param {Object} ctx контекст формы
   */
  function loadYesterday(form, ctx) {
    var box = document.getElementById("diary-yday");
    if (!box) return;

    box.innerHTML =
      '<h3 class="yday__title">' + App.escapeHtml(pick("Вчера", "Yesterday")) + "</h3>" +
      '<div class="yday__list">' +
      '<div class="skeleton skeleton-block yday__skeleton"></div>' +
      '<div class="skeleton skeleton-block yday__skeleton"></div>' +
      "</div>";

    if (!(App.api && typeof App.api.getYesterday === "function")) {
      box.innerHTML = "";
      return;
    }

    App.api
      .getYesterday(state.date)
      .then(function (res) {
        var items = (res && res.items) || [];
        renderYesterday(items, form, ctx);
      })
      .catch(function () {
        // Вспомогательный блок: при ошибке просто скрываем его.
        if (box) box.innerHTML = "";
      });
  }

  /**
   * Рисует список блюд «за вчера». Тап по телу — автозаполнение формы листа;
   * кнопка «плюс» — прямое добавление блюда в его собственный приём пищи.
   * @param {Array} items список {dish_name, quantity, unit, calories, proteins, fats, carbs, meal_type}
   * @param {HTMLFormElement} form форма листа блюда
   * @param {Object} ctx контекст формы
   */
  function renderYesterday(items, form, ctx) {
    var box = document.getElementById("diary-yday");
    if (!box) return;

    if (!items.length) {
      box.innerHTML =
        '<h3 class="yday__title">' + App.escapeHtml(pick("Вчера", "Yesterday")) + "</h3>" +
        '<p class="yday__empty">' +
        App.escapeHtml(pick("За вчера нет записей.", "No entries yesterday.")) + "</p>";
      return;
    }

    var kcal = pick("ккал", "kcal");
    var pLabel = pick("Б", "P");
    var fLabel = pick("Ж", "F");
    var cLabel = pick("У", "C");

    var rows = "";
    for (var i = 0; i < items.length; i++) {
      var it = items[i] || {};
      // Строка макросов: «320 ккал · 2 шт · Б .. Ж .. У ..».
      var qtyPart = "";
      if (it.quantity != null) {
        var uLabel = unitLabel(it.unit);
        qtyPart = " · " + App.fmt(it.quantity) + (uLabel ? " " + uLabel : "");
      }
      var macros =
        App.fmt(it.calories || 0) + " " + kcal + qtyPart +
        " · " + pLabel + " " + App.fmt(it.proteins || 0) +
        " · " + fLabel + " " + App.fmt(it.fats || 0) +
        " · " + cLabel + " " + App.fmt(it.carbs || 0);

      rows +=
        '<div class="yday__item" data-idx="' + i + '">' +
        '<button type="button" class="yday__body" data-idx="' + i + '">' +
        '<span class="yday__name">' +
        App.escapeHtml(it.dish_name || pick("Без названия", "Untitled")) + "</span>" +
        '<span class="yday__macros">' + App.escapeHtml(macros) + "</span>" +
        "</button>" +
        '<button type="button" class="yday__add" data-idx="' + i + '" ' +
        'aria-label="' + App.escapeHtml(pick("Добавить", "Add")) + '">' +
        icon("plus", { size: 20 }) + "</button>" +
        "</div>";
    }

    box.innerHTML =
      '<h3 class="yday__title">' + App.escapeHtml(pick("Вчера", "Yesterday")) + "</h3>" +
      '<div class="yday__list">' + rows + "</div>";

    var list = box.querySelector(".yday__list");
    if (list) {
      list.addEventListener("click", function (ev) {
        // Кнопка «плюс» — прямое добавление в собственный приём пищи.
        var addBtn = ev.target.closest(".yday__add");
        if (addBtn) {
          var addIdx = parseInt(addBtn.getAttribute("data-idx"), 10);
          if (!isNaN(addIdx) && items[addIdx]) {
            var it = items[addIdx];
            quickAdd(it, it.meal_type || "breakfast", addBtn);
          }
          return;
        }
        // Тап по телу — автозаполнение формы листа.
        var body = ev.target.closest(".yday__body");
        if (body) {
          var idx = parseInt(body.getAttribute("data-idx"), 10);
          if (!isNaN(idx) && items[idx]) {
            fillManualFromYesterday(items[idx], form, ctx);
          }
        }
      });
    }
  }

  /**
   * Автозаполняет форму листа значениями блюда «за вчера» (взяты как есть,
   * поэтому помечаем manualOverride=true, чтобы не пересчитывать по количеству).
   * @param {Object} it блюдо из «Вчера»
   * @param {HTMLFormElement} form форма листа блюда
   * @param {Object} ctx контекст формы
   */
  function fillManualFromYesterday(it, form, ctx) {
    if (!form) return;
    App.haptic && App.haptic("light");

    if (form.dish_name) form.dish_name.value = it.dish_name || "";
    if (form.quantity) form.quantity.value = (it.quantity != null ? it.quantity : "");
    if (form.unit) {
      form.unit.value = (it.unit && UNIT_KEYS.indexOf(it.unit) !== -1) ? it.unit : "g";
    }

    // Значения взяты как есть — фиксируем ручное переопределение.
    ctx.manualOverride = true;
    ctx.perUnit = null;
    setMacroFields(
      form,
      Math.round(Number(it.calories) || 0),
      round1(it.proteins || 0),
      round1(it.fats || 0),
      round1(it.carbs || 0)
    );

    // Приём пищи = приём блюда из «Вчера».
    var meal = it.meal_type || "breakfast";
    ctx.manualMeal = meal;
    var sheet = document.getElementById(SHEET_FOOD);
    var chips = sheet && sheet.querySelectorAll(".diary-manual__meal .meal-chip");
    if (chips) {
      for (var i = 0; i < chips.length; i++) {
        chips[i].classList.toggle(
          "is-active",
          chips[i].getAttribute("data-food-meal") === meal
        );
      }
    }

    scrollSheetToTop(SHEET_FOOD);
  }

  /**
   * Добавляет произвольное блюдо в рацион выбранного приёма пищи.
   * Используется «Вчера», рекомендациями и AI-планом меню.
   * Прокидывает количество/единицу (если есть) в запись дневника.
   * @param {Object} food {dish_name, calories, proteins, fats, carbs, quantity?, unit?}
   * @param {string} mealType
   * @param {HTMLElement} [trigger] кнопка-инициатор (для блокировки)
   */
  function quickAdd(food, mealType, trigger) {
    var quantity = (food.quantity != null && isFinite(Number(food.quantity)))
      ? Number(food.quantity)
      : null;
    var unit = (food.unit != null) ? food.unit : null;

    var entry = {
      date: state.date,
      meal_type: mealType,
      dish_name: food.dish_name || pick("Без названия", "Untitled"),
      calories: Math.round(Number(food.calories) || 0),
      proteins: Number(food.proteins) || 0,
      fats: Number(food.fats) || 0,
      carbs: Number(food.carbs) || 0,
      quantity: quantity,
      unit: unit
    };

    if (trigger) trigger.disabled = true;
    App.haptic && App.haptic("light");
    App.showLoading();

    App.api
      .addManualFood(entry)
      .then(function () {
        App.haptic && App.haptic("success");
        App.toast(pick("Добавлено: ", "Added: ") + App.mealLabel(mealType));
        state.panel = null;
        // Быстрое добавление могло идти из листа блюда («Недавние»/«Вчера») —
        // закрываем его, иначе лист остаётся висеть поверх обновлённого дня.
        closeSheetById(SHEET_FOOD);
        if (App.state && App.state.diaryByDate) {
          delete App.state.diaryByDate[state.date];
        }
        loadAndRender();
      })
      .catch(function (err) {
        App.haptic && App.haptic("error");
        App.toast((err && err.message) || pick("Не удалось добавить блюдо", "Failed to add dish"));
        if (trigger) trigger.disabled = false;
      })
      .then(function () {
        App.hideLoading();
      });
  }

  // ===========================================================================
  // АКТИВНОСТЬ (расход калорий).
  //
  // Переехала сюда из удалённого раздела «Тренировки»: расход — вторая
  // половина баланса дня («съедено минус сожжено»), а баланс ведёт дневник.
  // Секция компактная: одна строка итога + строки записей. Записи AI-тренера
  // (description начинается с «Тренер:») помечены гантелью и НЕ удаляются
  // отсюда — они принадлежат журналу тренировок, дневник их только показывает.
  // ===========================================================================

  /**
   * Загружает тренировки за выбранный день и рисует секцию «Активность».
   * Секция вспомогательная: при ошибке (в том числе 402 у free) просто
   * ничего не показываем — дневник еды должен работать и без неё.
   */
  function loadActivity() {
    var box = document.getElementById("diary-activity");
    if (!box) return;

    // Журнал тренировок на бэкенде закрыт подпиской: у free запрос заведомо
    // вернёт 402, поэтому даже не ходим.
    if (!isPremium() || !(App.api && typeof App.api.getWorkouts === "function")) {
      box.innerHTML = "";
      return;
    }

    var reqDate = state.date;
    App.api
      .getWorkouts(reqDate)
      .then(function (data) {
        // Пока ждали ответ, человек мог перелистнуть день — не рисуем чужое.
        if (reqDate !== state.date) return;
        renderActivity((data && data.workouts) || [], (data && data.total_burned) || 0);
      })
      .catch(function () {
        if (box) box.innerHTML = "";
      });
  }

  /**
   * Рисует секцию «Активность»: итог сожжённого + список записей.
   * Пустой день активности показываем одной строкой-кнопкой, а не карточкой
   * с пустым состоянием — на экране и так уже есть пустое состояние еды.
   * @param {Array} workouts список WorkoutOut
   * @param {number} totalBurned сожжено за день, ккал
   */
  function renderActivity(workouts, totalBurned) {
    var box = document.getElementById("diary-activity");
    if (!box) return;

    workouts = workouts || [];
    var kcal = pick("ккал", "kcal");

    if (!workouts.length) {
      box.innerHTML =
        '<button type="button" class="diary-meal-row" data-add-activity ' +
        'aria-label="' + App.escapeHtml(pick("Добавить активность", "Add activity")) + '">' +
        '<span class="diary-meal-row__icon">' + icon("run", { size: 18 }) + "</span>" +
        '<span class="diary-meal-row__title">' +
        App.escapeHtml(pick("Активность", "Activity")) + "</span>" +
        '<span class="diary-meal-row__add">' + icon("plus", { size: 18 }) + "</span>" +
        "</button>";
      bindActivityHandlers();
      return;
    }

    var rows = "";
    for (var i = 0; i < workouts.length; i++) {
      var w = workouts[i] || {};
      var fromCoach = isCoachWorkout(w);
      var label = fromCoach
        ? pick("Тренировка по программе", "Programme workout")
        : workoutTypeLabel(w.type);
      var mins = Number(w.duration_min) || 0;
      var meta = mins > 0 ? mins + " " + pick("мин", "min") : "";

      rows +=
        '<li class="diary-act-item">' +
        '<span class="diary-act-item__icon">' +
        icon(fromCoach ? "dumbbell" : (WORKOUT_ICONS[w.type] || "run"), { size: 18 }) +
        "</span>" +
        '<span class="diary-act-item__main">' +
        '<span class="diary-act-item__name">' + App.escapeHtml(label) + "</span>" +
        (meta ? '<span class="diary-act-item__meta">' + App.escapeHtml(meta) + "</span>" : "") +
        "</span>" +
        '<span class="diary-act-item__kcal">' +
        App.fmt(Number(w.calories_burned) || 0) + " " + kcal + "</span>" +
        // Записи тренера удалять нельзя: их источник — завершённая сессия
        // программы, а не дневник. Вместо кнопки показываем замок.
        (fromCoach
          ? '<span class="diary-act-item__locked" title="' +
            App.escapeHtml(pick(
              "Запись создана тренером — удаляется в тренировках",
              "Created by the coach — remove it in the trainer"
            )) + '">' + icon("lock", { size: 16 }) + "</span>"
          : '<button type="button" class="diary-act-item__del" data-workout-id="' + w.id + '" ' +
            'aria-label="' + App.escapeHtml(pick("Удалить активность", "Delete activity")) + '">' +
            icon("trash", { size: 18 }) + "</button>") +
        "</li>";
    }

    box.innerHTML =
      '<section class="card diary-act">' +
      '<header class="diary-act__head">' +
      '<span class="diary-act__title eyebrow">' +
      '<span class="diary-act__icon">' + icon("flame", { size: 18 }) + "</span>" +
      App.escapeHtml(pick("Сожжено за день", "Burned today")) +
      "</span>" +
      '<span class="diary-act__total num">' + App.fmt(totalBurned || 0) +
      '<span class="diary-meal__kcal-unit">' + kcal + "</span></span>" +
      "</header>" +
      '<ul class="diary-act__list">' + rows + "</ul>" +
      '<button type="button" class="diary-meal__add" data-add-activity>' +
      icon("plus", { size: 16 }) +
      App.escapeHtml(pick("Добавить активность", "Add activity")) +
      "</button>" +
      "</section>";

    bindActivityHandlers();
  }

  /**
   * Навешивает обработчики секции «Активность» (добавление и удаление).
   */
  function bindActivityHandlers() {
    var box = document.getElementById("diary-activity");
    if (!box) return;

    var addBtns = box.querySelectorAll("[data-add-activity]");
    for (var i = 0; i < addBtns.length; i++) {
      addBtns[i].addEventListener("click", function () {
        App.haptic && App.haptic("light");
        openActivitySheet();
      });
    }

    var delBtns = box.querySelectorAll(".diary-act-item__del");
    for (var j = 0; j < delBtns.length; j++) {
      delBtns[j].addEventListener("click", function (ev) {
        var id = parseInt(ev.currentTarget.getAttribute("data-workout-id"), 10);
        if (isNaN(id)) return;
        App.haptic && App.haptic("light");
        openConfirmSheet({
          title: pick("Удалить активность?", "Delete activity?"),
          text: pick(
            "Сожжённые калории вернутся в баланс дня.",
            "The burned calories will return to the day’s balance."
          ),
          confirmLabel: pick("Удалить", "Delete"),
          onConfirm: function () {
            deleteActivity(id);
          }
        });
      });
    }
  }

  /**
   * Удаляет запись активности и перезагружает день (баланс меняется).
   * @param {number} id id тренировки
   */
  function deleteActivity(id) {
    App.showLoading();
    App.api
      .deleteWorkout(id)
      .then(function () {
        App.haptic && App.haptic("success");
        App.toast(pick("Активность удалена", "Activity deleted"));
        if (App.state && App.state.diaryByDate) {
          delete App.state.diaryByDate[state.date];
        }
        loadAndRender();
      })
      .catch(function (err) {
        App.haptic && App.haptic("error");
        App.toast(
          (err && err.message) ||
          pick("Не удалось удалить активность", "Failed to delete activity")
        );
      })
      .then(function () {
        App.hideLoading();
      });
  }

  /**
   * Разметка чипов выбора типа активности.
   * @param {string} selected выбранный тип
   * @returns {string}
   */
  function activityChipsHtml(selected) {
    var chips = "";
    for (var i = 0; i < WORKOUT_TYPES.length; i++) {
      var t = WORKOUT_TYPES[i];
      chips +=
        '<button type="button" class="act-chip' + (t === selected ? " is-active" : "") + '" ' +
        'data-act-type="' + t + '">' +
        icon(WORKOUT_ICONS[t] || "run", { size: 16 }) +
        App.escapeHtml(workoutTypeLabel(t)) +
        "</button>";
    }
    return '<div class="act-chips">' + chips + "</div>";
  }

  /**
   * Открывает нижний лист ручного ввода активности:
   * тип, (для «Другое») описание, длительность, сожжённые ккал + оценка.
   */
  function openActivitySheet() {
    if (document.getElementById(SHEET_ACTIVITY)) return;
    closeSheetById(SHEET_ACTIONS, true);
    App.haptic && App.haptic("light");

    var defaultType = "cardio";

    var html =
      '<h2 class="diary-sheet__title">' +
      App.escapeHtml(pick("Добавить активность", "Add activity")) + "</h2>" +
      '<p class="diary-sheet__text">' +
      App.escapeHtml(pick(
        "Сожжённые калории войдут в баланс дня.",
        "Burned calories count toward the day’s balance."
      )) + "</p>" +
      '<form class="diary-manual__form" id="diary-activity-form" novalidate>' +
      '<div class="field">' +
      '<span class="field__label">' + App.escapeHtml(pick("Тип", "Type")) + "</span>" +
      activityChipsHtml(defaultType) +
      "</div>" +
      // Описание нужно только для «Другое»: по нему калории оценивает AI.
      '<label class="field act-desc" hidden>' +
      '<span class="field__label">' + App.escapeHtml(pick("Что вы делали?", "What did you do?")) + "</span>" +
      '<input class="field__input" type="text" name="description" maxlength="120" ' +
      'placeholder="' + App.escapeHtml(pick("например, катание на сноуборде", "e.g. snowboarding")) + '">' +
      "</label>" +
      '<label class="field">' +
      '<span class="field__label">' + App.escapeHtml(pick("Длительность, мин", "Duration, min")) + "</span>" +
      '<input class="field__input" type="number" name="duration" inputmode="numeric" ' +
      'min="1" step="1" placeholder="30" required>' +
      "</label>" +
      '<button type="button" class="btn btn--ghost btn-block act-estimate">' +
      icon("flame", { size: 18 }) +
      App.escapeHtml(pick("Оценить калории", "Estimate calories")) + "</button>" +
      '<label class="field">' +
      '<span class="field__label">' + App.escapeHtml(pick("Сожжено, ккал", "Burned, kcal")) + "</span>" +
      '<input class="field__input" type="number" name="calories" inputmode="numeric" ' +
      'min="0" step="1" placeholder="0" required>' +
      "</label>" +
      '<button type="submit" class="btn btn--cta btn-block act-submit">' +
      App.escapeHtml(pick("Добавить", "Add")) + "</button>" +
      '<button type="button" class="btn btn--ghost btn-block act-cancel">' +
      App.escapeHtml(pick("Отмена", "Cancel")) + "</button>" +
      "</form>";

    var sheet = mountSheet(SHEET_ACTIVITY, html);
    if (!sheet) return;

    var ctx = { type: defaultType };
    var form = sheet.querySelector("#diary-activity-form");
    var descField = sheet.querySelector(".act-desc");

    // Выбор типа активности. Поле описания показываем только для «Другое».
    var chipsWrap = sheet.querySelector(".act-chips");
    if (chipsWrap) {
      chipsWrap.addEventListener("click", function (ev) {
        var btn = ev.target.closest(".act-chip");
        if (!btn) return;
        var t = btn.getAttribute("data-act-type");
        if (!t) return;
        ctx.type = t;
        App.haptic && App.haptic("light");
        var all = chipsWrap.querySelectorAll(".act-chip");
        for (var i = 0; i < all.length; i++) {
          all[i].classList.toggle("is-active", all[i].getAttribute("data-act-type") === t);
        }
        if (descField) descField.hidden = t !== "other";
      });
    }

    var estimateBtn = sheet.querySelector(".act-estimate");
    if (estimateBtn) {
      estimateBtn.addEventListener("click", function () {
        estimateActivity(form, ctx, estimateBtn);
      });
    }

    var cancelBtn = sheet.querySelector(".act-cancel");
    if (cancelBtn) {
      cancelBtn.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        closeSheetById(SHEET_ACTIVITY);
      });
    }

    if (form) {
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        submitActivity(form, ctx);
      });
      bindLiveValidation(form);
    }
  }

  /**
   * Читает длительность и описание из формы активности с подсветкой ошибок.
   * @param {HTMLFormElement} form
   * @param {Object} ctx {type}
   * @returns {Object|null} {duration, description} либо null, если форма невалидна
   */
  function readActivityInput(form, ctx) {
    var durRaw = (form.duration.value || "").trim();
    var duration = Math.round(Number(durRaw));
    if (durRaw === "" || !isFinite(duration) || duration <= 0) {
      invalidField(
        form.duration,
        pick("Укажите длительность в минутах", "Enter the duration in minutes")
      );
      return null;
    }

    // Для «Другое» описание обязательно: именно по нему AI считает калории.
    var description = form.description ? (form.description.value || "").trim() : "";
    if (ctx.type === "other" && !description) {
      invalidField(form.description, pick("Опишите, что вы делали", "Describe what you did"));
      return null;
    }

    return { duration: duration, description: description };
  }

  /**
   * Оценивает сожжённые калории (App.api.estimateWorkout) и подставляет
   * результат в поле, оставляя его редактируемым.
   * @param {HTMLFormElement} form
   * @param {Object} ctx {type}
   * @param {HTMLElement} btn кнопка «Оценить калории»
   */
  function estimateActivity(form, ctx, btn) {
    if (!form) return;
    var input = readActivityInput(form, ctx);
    if (!input) return;

    if (!(App.api && typeof App.api.estimateWorkout === "function")) {
      App.toast(pick("Оценка временно недоступна.", "Estimation is temporarily unavailable."));
      return;
    }

    var payload = { type: ctx.type, duration_min: input.duration };
    if (ctx.type === "other") payload.description = input.description;

    if (btn) {
      btn.disabled = true;
      btn.textContent = pick("Оцениваем…", "Estimating…");
    }
    App.haptic && App.haptic("light");
    App.showLoading();

    App.api
      .estimateWorkout(payload)
      .then(function (res) {
        var kcal = res && res.calories_burned != null ? Number(res.calories_burned) : null;
        if (kcal == null || !isFinite(kcal)) {
          throw new Error(pick("Не удалось оценить калории", "Couldn’t estimate calories"));
        }
        if (form.calories) {
          form.calories.value = Math.round(kcal);
          clearInvalid(form.calories);
        }
        App.haptic && App.haptic("success");
        App.toast(pick("Оценка: ", "Estimate: ") + App.fmt(Math.round(kcal)) + " " + pick("ккал", "kcal"));
      })
      .catch(function (err) {
        App.haptic && App.haptic("error");
        App.toast((err && err.message) || pick("Не удалось оценить калории", "Couldn’t estimate calories"));
      })
      .then(function () {
        if (btn) {
          btn.disabled = false;
          // Кнопка с иконкой: восстанавливаем разметку, а не только текст.
          btn.innerHTML =
            icon("flame", { size: 18 }) +
            App.escapeHtml(pick("Оценить калории", "Estimate calories"));
        }
        App.hideLoading();
      });
  }

  /**
   * Валидирует и сохраняет активность (App.api.addWorkout), затем
   * перезагружает день: сожжённое участвует в итоге дня.
   * @param {HTMLFormElement} form
   * @param {Object} ctx {type}
   */
  function submitActivity(form, ctx) {
    var input = readActivityInput(form, ctx);
    if (!input) return;

    var calRaw = (form.calories.value || "").trim();
    var calories = Math.round(Number(calRaw));
    if (calRaw === "" || !isFinite(calories) || calories < 0) {
      invalidField(
        form.calories,
        pick("Укажите калории или нажмите «Оценить»", "Enter calories or tap “Estimate”")
      );
      return;
    }

    var payload = {
      date: state.date,
      type: ctx.type,
      duration_min: input.duration,
      calories_burned: calories
    };
    if (ctx.type === "other") payload.description = input.description;

    var submitBtn = form.querySelector(".act-submit");
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = pick("Добавляем…", "Adding…");
    }
    App.showLoading();

    App.api
      .addWorkout(payload)
      .then(function () {
        App.haptic && App.haptic("success");
        App.toast(pick("Активность добавлена", "Activity added"));
        closeSheetById(SHEET_ACTIVITY);
        // Итог дня считается с учётом сожжённого — кэш дня больше не годится.
        if (App.state && App.state.diaryByDate) {
          delete App.state.diaryByDate[state.date];
        }
        loadAndRender();
      })
      .catch(function (err) {
        App.haptic && App.haptic("error");
        App.toast(
          (err && err.message) ||
          pick("Не удалось добавить активность", "Failed to add activity")
        );
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = pick("Добавить", "Add");
        }
      })
      .then(function () {
        App.hideLoading();
      });
  }

  /**
   * Paywall для активности: журнал тренировок на бэкенде закрыт подпиской,
   * поэтому free показываем предложение вместо формы, которая всё равно
   * получит 402. Контроль доступа серверный — это визуальная заглушка.
   */
  function openActivityPaywall() {
    var panel = document.getElementById("diary-panel");
    if (!panel) return;

    if (App && typeof App.paywall === "function") {
      App.paywall(panel, {
        title: pick("Активность", "Activity"),
        desc: pick(
          "Учёт тренировок и сожжённых калорий в балансе дня",
          "Workout tracking and burned calories in your daily balance"
        ),
        bullets: [
          pick("Ручной ввод тренировки за любой день", "Log a workout for any day"),
          pick("Оценка калорий по типу и длительности", "Calorie estimate by type and duration"),
          pick("Итог дня: съедено минус сожжено", "Daily total: eaten minus burned")
        ]
      });
      return;
    }

    // Запасной вариант, если единый paywall недоступен — ведём в подписку.
    panel.innerHTML =
      '<section class="card diary-act-locked">' +
      '<h2 class="diary-act-locked__title">' +
      App.escapeHtml(pick("Активность", "Activity")) + "</h2>" +
      '<p class="diary-act-locked__sub">' +
      App.escapeHtml(pick(
        "Учёт тренировок доступен по подписке.",
        "Workout tracking is available with a subscription."
      )) + "</p>" +
      '<button type="button" class="btn btn--cta btn-block diary-act-locked__subscribe">' +
      App.escapeHtml(pick("Оформить подписку", "Get subscription")) + "</button>" +
      "</section>";
    var subBtn = panel.querySelector(".diary-act-locked__subscribe");
    if (subBtn) {
      subBtn.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        if (App && typeof App.navigate === "function") App.navigate("subscription");
      });
    }
  }
  // ---------------------------------------------------------------------------
  // Рекомендации «Что съесть?» (Этап 5: умные предложения).
  //
  // Панель содержит:
  //   - чипы выбора приёма пищи (Завтрак/Обед/Ужин/Перекус + «Весь день»);
  //   - поле свободного ввода «Чего хочется?»;
  //   - кнопку «Подобрать» -> App.api.suggestFood (если выбран приём/есть текст)
  //     ИЛИ существующий App.api.recommendFood (фолбэк, общий случай);
  //   - кнопку «Вкусняшки» -> App.api.getHealthySnacks.
  // Результаты — карточки {dish_name,calories,...,reason} с добавлением в один тап.
  // Префикс классов suggest-.
  // ---------------------------------------------------------------------------

  /**
   * Открывает панель рекомендаций «Что съесть?» с управлением (приём пищи,
   * свободный ввод, кнопки) и областью результатов.
   */
  function openRecommendPanel() {
    var panel = document.getElementById("diary-panel");
    if (!panel) return;

    // Подпись «Весь день» как отдельный чип (meal_type=null -> общий подбор).
    var allActive = state.suggestMeal == null ? " is-active" : "";
    var chips = "";
    for (var i = 0; i < MEAL_ORDER.length; i++) {
      var t = MEAL_ORDER[i];
      var active = state.suggestMeal === t ? " is-active" : "";
      chips +=
        '<button type="button" class="meal-chip' + active + '" ' +
        'data-suggest-meal="' + t + '">' +
        App.escapeHtml(App.mealLabel(t)) + "</button>";
    }
    var mealChips =
      '<div class="meal-chips suggest-chips">' +
      '<button type="button" class="meal-chip' + allActive + '" data-suggest-meal="all">' +
      App.escapeHtml(pick("Весь день", "Whole day")) + "</button>" +
      chips +
      "</div>";

    panel.innerHTML =
      '<section class="card diary-recommend suggest-panel">' +
      '<h2 class="diary-recommend__title">' +
      App.escapeHtml(pick("Что съесть?", "What to eat?")) + "</h2>" +
      '<p class="diary-recommend__sub">' +
      App.escapeHtml(pick(
        "Подбираем блюда под остаток дневной нормы.",
        "Suggesting dishes to fit your remaining daily allowance."
      )) + "</p>" +
      // Выбор приёма пищи.
      '<div class="suggest-controls">' +
      '<span class="field__label">' + App.escapeHtml(pick("Приём пищи", "Meal")) + "</span>" +
      mealChips +
      // Свободный ввод «Чего хочется?».
      '<label class="field suggest-free">' +
      '<span class="field__label">' + App.escapeHtml(pick("Чего хочется?", "What do you feel like?")) +
      ' <span class="field__hint">' + App.escapeHtml(pick("(необязательно)", "(optional)")) + "</span></span>" +
      '<input class="field__input suggest-free__input" type="text" maxlength="120" ' +
      'value="' + App.escapeHtml(state.suggestText || "") + '" ' +
      'placeholder="' + App.escapeHtml(pick("Например, что-то сладкое и белковое", "e.g. something sweet and high-protein")) + '">' +
      "</label>" +
      // Кнопки действий: подобрать + вкусняшки.
      '<div class="suggest-buttons">' +
      '<button type="button" class="btn btn--cta suggest-btn suggest-btn--go">' +
      App.escapeHtml(pick("Подобрать", "Suggest")) + "</button>" +
      '<button type="button" class="btn btn--ghost suggest-btn suggest-btn--snacks">' +
      icon("apple", { size: 18 }) +
      App.escapeHtml(pick("Вкусняшки", "Healthy snacks")) + "</button>" +
      "</div>" +
      "</div>" +
      // Область результатов.
      '<div id="diary-recommend-body" class="diary-recommend__body suggest-body">' +
      '<p class="suggest-hint">' +
      App.escapeHtml(pick(
        "Выберите приём пищи или опишите, чего хочется, и нажмите «Подобрать».",
        "Pick a meal or describe what you feel like, then tap “Suggest”."
      )) + "</p>" +
      "</div>" +
      "</section>";

    // Переключение приёма пищи (включая «Весь день» -> null).
    var chipsWrap = panel.querySelector(".suggest-chips");
    if (chipsWrap) {
      chipsWrap.addEventListener("click", function (ev) {
        var btn = ev.target.closest(".meal-chip");
        if (!btn) return;
        var v = btn.getAttribute("data-suggest-meal");
        if (!v) return;
        state.suggestMeal = v === "all" ? null : v;
        App.haptic && App.haptic("light");
        var all = chipsWrap.querySelectorAll(".meal-chip");
        for (var i = 0; i < all.length; i++) {
          var av = all[i].getAttribute("data-suggest-meal");
          var on = (av === "all" && state.suggestMeal == null) || av === state.suggestMeal;
          all[i].classList.toggle("is-active", on);
        }
      });
    }

    // Сохраняем свободный ввод в состоянии (чтобы переживал перерисовку).
    var freeInput = panel.querySelector(".suggest-free__input");
    if (freeInput) {
      freeInput.addEventListener("input", function () {
        state.suggestText = freeInput.value || "";
      });
    }

    // Кнопка «Подобрать».
    var goBtn = panel.querySelector(".suggest-btn--go");
    if (goBtn) {
      goBtn.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        if (freeInput) state.suggestText = freeInput.value || "";
        requestSuggestions();
      });
    }

    // Кнопка «Вкусняшки».
    var snacksBtn = panel.querySelector(".suggest-btn--snacks");
    if (snacksBtn) {
      snacksBtn.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        requestHealthySnacks();
      });
    }
  }

  /**
   * Считает остатки КБЖУ за текущий день относительно цели и target_* профиля.
   * @returns {Object} {remainingCalories, remainingProteins, remainingFats,
   *                     remainingCarbs}
   */
  function computeRemaining() {
    var day = state.day || {};
    var profile = (App.state && App.state.profile) || {};

    var goalKcal = Number(day.daily_goal_kcal) || 0;
    var eaten = Number(day.total_calories) || 0;

    return {
      remainingCalories: Math.max(0, goalKcal - eaten),
      remainingProteins: Math.max(0, (Number(profile.target_proteins) || 0) - (Number(day.total_proteins) || 0)),
      remainingFats: Math.max(0, (Number(profile.target_fats) || 0) - (Number(day.total_fats) || 0)),
      remainingCarbs: Math.max(0, (Number(profile.target_carbs) || 0) - (Number(day.total_carbs) || 0))
    };
  }

  /**
   * Показывает скелетон в области результатов «Что съесть?».
   */
  function showSuggestSkeleton() {
    var body = document.getElementById("diary-recommend-body");
    if (!body) return;
    body.innerHTML =
      '<div class="skeleton skeleton-block diary-recommend__skeleton"></div>' +
      '<div class="skeleton skeleton-block diary-recommend__skeleton"></div>' +
      '<div class="skeleton skeleton-block diary-recommend__skeleton"></div>';
  }

  /**
   * Подбирает блюда по выбранному приёму/свободному вводу.
   * Если выбран приём пищи ИЛИ есть свободный текст — используем умный
   * App.api.suggestFood. Иначе (общий случай «весь день» без текста) —
   * существующий App.api.recommendFood как фолбэк.
   */
  function requestSuggestions() {
    var rem = computeRemaining();
    var freeText = (state.suggestText || "").trim();
    var mealType = state.suggestMeal || null;

    showSuggestSkeleton();

    // Решаем, какой эндпоинт использовать.
    var useSuggest = !!mealType || !!freeText;

    if (useSuggest && App.api && typeof App.api.suggestFood === "function") {
      var payload = {
        remaining_calories: Math.round(rem.remainingCalories),
        remaining_proteins: Math.round(rem.remainingProteins),
        remaining_fats: Math.round(rem.remainingFats),
        remaining_carbs: Math.round(rem.remainingCarbs)
      };
      if (mealType) payload.meal_type = mealType;
      if (freeText) payload.free_text = freeText;
      // Дата нужна AI-тренеру: в тренировочный день подсказки учитывают нагрузку.
      payload.date = state.date;

      App.api
        .suggestFood(payload)
        .then(function (res) {
          var suggestions = (res && res.suggestions) || [];
          renderRecommendations(suggestions, rem.remainingCalories, res && res.training_note);
        })
        .catch(function (err) {
          renderRecommendError(
            (err && err.message) ||
            pick("Не удалось получить предложения.", "Failed to get suggestions.")
          );
        });
      return;
    }

    // Фолбэк: общий подбор под остаток нормы (как раньше).
    requestRecommendations(rem);
  }

  /**
   * Запрашивает «вкусняшки» (полезные перекусы) через App.api.getHealthySnacks.
   */
  function requestHealthySnacks() {
    var rem = computeRemaining();

    showSuggestSkeleton();

    if (!(App.api && typeof App.api.getHealthySnacks === "function")) {
      renderRecommendError(
        pick("Подсказки временно недоступны.", "Suggestions are temporarily unavailable.")
      );
      return;
    }

    App.api
      .getHealthySnacks()
      .then(function (res) {
        var suggestions = (res && res.suggestions) || [];
        renderRecommendations(suggestions, rem.remainingCalories);
      })
      .catch(function (err) {
        renderRecommendError(
          (err && err.message) ||
          pick("Не удалось получить вкусняшки.", "Failed to get healthy snacks.")
        );
      });
  }

  /**
   * Считает остатки КБЖУ и вызывает App.api.recommendFood (фолбэк-поток).
   * @param {Object} [rem] заранее посчитанные остатки (иначе считаем сами)
   */
  function requestRecommendations(rem) {
    rem = rem || computeRemaining();
    var profile = (App.state && App.state.profile) || {};

    var payload = {
      remaining_calories: Math.round(rem.remainingCalories),
      remaining_proteins: Math.round(rem.remainingProteins),
      remaining_fats: Math.round(rem.remainingFats),
      remaining_carbs: Math.round(rem.remainingCarbs),
      time_of_day: timeOfDay()
    };
    if (profile.diet_goal) {
      payload.diet_goal = profile.diet_goal;
    }

    App.api
      .recommendFood(payload)
      .then(function (res) {
        var suggestions = (res && res.suggestions) || [];
        renderRecommendations(suggestions, rem.remainingCalories);
      })
      .catch(function (err) {
        renderRecommendError(
          (err && err.message) ||
          pick("Не удалось получить рекомендации.", "Failed to get recommendations.")
        );
      });
  }

  /**
   * Грубая оценка времени суток для подсказки серверу.
   * @returns {string} "morning" | "afternoon" | "evening" | "night"
   */
  function timeOfDay() {
    var h = new Date().getHours();
    if (h >= 5 && h < 11) return "morning";
    if (h >= 11 && h < 16) return "afternoon";
    if (h >= 16 && h < 22) return "evening";
    return "night";
  }

  /**
   * Рисует карточки-рекомендации. Каждую можно добавить в рацион в один тап.
   * Название и причина (dish_name, reason) приходят от API — не переводим.
   * Если в панели «Что съесть?» выбран конкретный приём пищи — он становится
   * приёмом по умолчанию для добавления.
   * @param {Array} suggestions [{dish_name,calories,proteins,fats,carbs,reason}]
   * @param {number} remainingCalories остаток калорий (для подсказки)
   */
  function renderRecommendations(suggestions, remainingCalories, trainingNote) {
    var body = document.getElementById("diary-recommend-body");
    if (!body) return;

    // Заметка AI-тренера («сегодня силовая — добавьте белка»), если есть.
    var noteHtml = trainingNote
      ? '<p class="diary-recommend__note">' + icon("dumbbell", { size: 16 }) +
        App.escapeHtml(trainingNote) + "</p>"
      : "";

    if (!suggestions.length) {
      body.innerHTML =
        '<p class="diary-recommend__empty">' +
        App.escapeHtml(remainingCalories <= 0
          ? pick(
              "На сегодня дневная норма уже выбрана — подсказывать нечего.",
              "Today's allowance is already used up — nothing to suggest."
            )
          : pick(
              "Сейчас нет подходящих вариантов. Попробуйте позже.",
              "No suitable options right now. Try again later."
            )) +
        "</p>";
      return;
    }

    // Приём пищи для добавления рекомендации: по умолчанию — выбранный в панели
    // «Что съесть?» (если выбран), иначе завтрак.
    var recMeal = state.suggestMeal || "breakfast";

    var kcal = pick("ккал", "kcal");
    var pLabel = pick("Б", "P");
    var fLabel = pick("Ж", "F");
    var cLabel = pick("У", "C");

    var cards = "";
    for (var i = 0; i < suggestions.length; i++) {
      var s = suggestions[i] || {};
      var reason = s.reason
        ? '<p class="diary-recommend-item__reason">' + App.escapeHtml(s.reason) + "</p>"
        : "";
      cards +=
        '<div class="diary-recommend-item" data-idx="' + i + '">' +
        '<div class="diary-recommend-item__head">' +
        '<span class="diary-recommend-item__name">' +
        App.escapeHtml(s.dish_name || pick("Блюдо", "Dish")) + "</span>" +
        '<span class="diary-recommend-item__kcal">' +
        App.fmt(s.calories || 0) + " " + kcal + "</span>" +
        "</div>" +
        '<p class="diary-recommend-item__macros">' + pLabel + " " + App.fmt(s.proteins || 0) +
        " · " + fLabel + " " + App.fmt(s.fats || 0) +
        " · " + cLabel + " " + App.fmt(s.carbs || 0) + "</p>" +
        reason +
        '<button type="button" class="btn btn--cta diary-recommend-item__add" ' +
        'data-idx="' + i + '">' + App.escapeHtml(pick("Добавить в рацион", "Add to diary")) + "</button>" +
        "</div>";
    }

    body.innerHTML =
      noteHtml +
      '<div class="diary-recommend__meal">' +
      '<span class="field__label">' + App.escapeHtml(pick("Добавить как", "Add as")) + "</span>" +
      mealChipsHtml(recMeal, "rec-meal") +
      "</div>" +
      '<div class="diary-recommend__list">' + cards + "</div>";

    // Переключение приёма пищи для рекомендаций.
    var mealsWrap = body.querySelector(".diary-recommend__meal .meal-chips");
    if (mealsWrap) {
      mealsWrap.addEventListener("click", function (ev) {
        var btn = ev.target.closest(".meal-chip");
        if (!btn) return;
        var t = btn.getAttribute("data-rec-meal");
        if (!t) return;
        recMeal = t;
        App.haptic && App.haptic("light");
        var all = mealsWrap.querySelectorAll(".meal-chip");
        for (var i = 0; i < all.length; i++) {
          all[i].classList.toggle(
            "is-active",
            all[i].getAttribute("data-rec-meal") === t
          );
        }
      });
    }

    // Добавление рекомендации по кнопке.
    var list = body.querySelector(".diary-recommend__list");
    if (list) {
      list.addEventListener("click", function (ev) {
        var addBtn = ev.target.closest(".diary-recommend-item__add");
        if (!addBtn) return;
        var idx = parseInt(addBtn.getAttribute("data-idx"), 10);
        if (isNaN(idx) || !suggestions[idx]) return;
        quickAdd(suggestions[idx], recMeal, addBtn);
      });
    }
  }

  /**
   * Состояние ошибки внутри панели рекомендаций с кнопкой «Повторить».
   * @param {string} message
   */
  function renderRecommendError(message) {
    var body = document.getElementById("diary-recommend-body");
    if (!body) return;
    body.innerHTML =
      '<div class="diary-recommend__error">' +
      '<p class="diary-recommend__error-text">' + App.escapeHtml(message) + "</p>" +
      '<button type="button" class="btn btn--ghost diary-recommend__retry">' +
      App.escapeHtml(pick("Повторить", "Retry")) + "</button>" +
      "</div>";

    var retry = body.querySelector(".diary-recommend__retry");
    if (retry) {
      retry.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        // Повторяем последнее действие через основной поток подбора.
        requestSuggestions();
      });
    }
  }

  // ===========================================================================
  // AI-ПЛАН МЕНЮ (Этап 5, ПРЕМИУМ).
  //
  // Панель .plan-panel:
  //   - чипы выбора охвата (День / Неделя);
  //   - поле предпочтений (текст);
  //   - кнопка «Сгенерировать» -> App.api.generateMealPlan({scope, preferences});
  //   - по дням: label дня + блюда по приёмам (App.mealLabel) с КБЖУ; у каждого
  //     блюда кнопка «Заменить» -> App.api.regenerateMealItem -> замена в UI;
  //   - список покупок (shopping_list) буллетами;
  //   - кнопка «Сгенерировать заново».
  // Префикс классов plan-.
  // ===========================================================================

  /**
   * Открывает панель AI-плана меню: управление (охват + предпочтения + кнопка)
   * и область результатов плана.
   */
  function openMealPlanPanel() {
    var panel = document.getElementById("diary-panel");
    if (!panel) return;

    panel.innerHTML =
      '<section class="card plan-panel">' +
      '<h2 class="plan-panel__title">' +
      App.escapeHtml(pick("AI-план меню", "AI meal plan")) + "</h2>" +
      '<p class="plan-panel__sub">' +
      App.escapeHtml(pick(
        "Составим меню под вашу цель и соберём список покупок.",
        "We'll compose a menu for your goal and build a shopping list."
      )) + "</p>" +
      // Управление.
      '<div class="plan-controls">' +
      '<span class="field__label">' + App.escapeHtml(pick("На сколько", "Scope")) + "</span>" +
      planScopeChipsHtml() +
      '<label class="field plan-prefs">' +
      '<span class="field__label">' + App.escapeHtml(pick("Предпочтения", "Preferences")) +
      ' <span class="field__hint">' + App.escapeHtml(pick("(необязательно)", "(optional)")) + "</span></span>" +
      '<input class="field__input plan-prefs__input" type="text" maxlength="160" ' +
      'value="' + App.escapeHtml(state.planPrefs || "") + '" ' +
      'placeholder="' + App.escapeHtml(pick("Например, без свинины, больше рыбы", "e.g. no pork, more fish")) + '">' +
      "</label>" +
      '<button type="button" class="btn btn--cta btn-block plan-generate">' +
      App.escapeHtml(pick("Сгенерировать", "Generate")) + "</button>" +
      "</div>" +
      // Область результата.
      '<div id="plan-body" class="plan-body"></div>' +
      "</section>";

    // Чипы выбора охвата.
    var scopeWrap = panel.querySelector(".plan-scope");
    if (scopeWrap) {
      scopeWrap.addEventListener("click", function (ev) {
        var btn = ev.target.closest(".plan-scope__chip");
        if (!btn) return;
        var sc = btn.getAttribute("data-plan-scope");
        if (!sc) return;
        state.planScope = sc;
        App.haptic && App.haptic("light");
        var all = scopeWrap.querySelectorAll(".plan-scope__chip");
        for (var i = 0; i < all.length; i++) {
          all[i].classList.toggle(
            "is-active",
            all[i].getAttribute("data-plan-scope") === sc
          );
        }
      });
    }

    // Поле предпочтений -> состояние.
    var prefsInput = panel.querySelector(".plan-prefs__input");
    if (prefsInput) {
      prefsInput.addEventListener("input", function () {
        state.planPrefs = prefsInput.value || "";
      });
    }

    // Кнопка генерации.
    var genBtn = panel.querySelector(".plan-generate");
    if (genBtn) {
      genBtn.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        if (prefsInput) state.planPrefs = prefsInput.value || "";
        requestMealPlan();
      });
    }

    // Если план уже был сгенерирован ранее (в этой сессии панели) — показываем.
    if (state.plan) {
      renderMealPlan(state.plan);
    } else {
      var body = document.getElementById("plan-body");
      if (body) {
        body.innerHTML =
          '<p class="plan-hint">' +
          App.escapeHtml(pick(
            "Выберите охват и нажмите «Сгенерировать».",
            "Pick a scope and tap “Generate”."
          )) + "</p>";
      }
    }
  }

  /**
   * Разметка чипов выбора охвата плана (День / Неделя).
   * @returns {string}
   */
  function planScopeChipsHtml() {
    var scopes = [
      { v: "day", ru: "День", en: "Day" },
      { v: "week", ru: "Неделя", en: "Week" }
    ];
    var chips = "";
    for (var i = 0; i < scopes.length; i++) {
      var sc = scopes[i];
      var active = state.planScope === sc.v ? " is-active" : "";
      chips +=
        '<button type="button" class="meal-chip plan-scope__chip' + active + '" ' +
        'data-plan-scope="' + sc.v + '">' +
        App.escapeHtml(pick(sc.ru, sc.en)) + "</button>";
    }
    return '<div class="meal-chips plan-scope">' + chips + "</div>";
  }

  /**
   * Запрашивает план меню у сервера и рисует его (или ошибку).
   */
  function requestMealPlan() {
    var body = document.getElementById("plan-body");
    if (body) {
      body.innerHTML =
        '<div class="skeleton skeleton-block plan-skeleton"></div>' +
        '<div class="skeleton skeleton-block plan-skeleton"></div>' +
        '<div class="skeleton skeleton-block plan-skeleton"></div>';
    }

    if (!(App.api && typeof App.api.generateMealPlan === "function")) {
      renderMealPlanError(
        pick("Планировщик меню временно недоступен.", "The meal planner is temporarily unavailable.")
      );
      return;
    }

    var payload = { scope: state.planScope === "week" ? "week" : "day" };
    var prefs = (state.planPrefs || "").trim();
    if (prefs) payload.preferences = prefs;

    App.api
      .generateMealPlan(payload)
      .then(function (res) {
        state.plan = res || { days: [], shopping_list: [] };
        renderMealPlan(state.plan);
      })
      .catch(function (err) {
        renderMealPlanError(
          (err && err.message) ||
          pick("Не удалось составить план меню.", "Failed to generate the meal plan.")
        );
      });
  }

  /**
   * Рисует план меню: дни, приёмы пищи, блюда (с кнопкой замены) и список покупок.
   * Названия блюд и подписи дней приходят от API — не переводим (только экранируем).
   * @param {Object} plan {days:[{label, meals:{...}}], shopping_list:[str]}
   */
  function renderMealPlan(plan) {
    var body = document.getElementById("plan-body");
    if (!body) return;

    plan = plan || {};
    var days = plan.days || [];
    var shopping = plan.shopping_list || [];

    if (!days.length) {
      body.innerHTML =
        '<p class="plan-empty">' +
        App.escapeHtml(pick(
          "План пуст. Попробуйте сгенерировать ещё раз.",
          "The plan is empty. Try generating again."
        )) + "</p>";
      return;
    }

    var html = "";
    for (var d = 0; d < days.length; d++) {
      var day = days[d] || {};
      var meals = day.meals || {};
      var label = day.label || (pick("День ", "Day ") + (d + 1));

      var mealsHtml = "";
      for (var m = 0; m < MEAL_ORDER.length; m++) {
        var mealType = MEAL_ORDER[m];
        var dishes = meals[mealType] || [];
        if (!dishes.length) continue;

        var dishesHtml = "";
        for (var i = 0; i < dishes.length; i++) {
          dishesHtml += planDishHtml(dishes[i], d, mealType, i);
        }

        mealsHtml +=
          '<div class="plan-meal">' +
          '<div class="plan-meal__head">' +
          '<span class="plan-meal__icon" aria-hidden="true">' +
          icon(MEAL_ICONS[mealType] || "plate", { size: 16 }) + "</span>" +
          '<span class="plan-meal__title">' + App.escapeHtml(App.mealLabel(mealType)) + "</span>" +
          "</div>" +
          '<div class="plan-meal__dishes">' + dishesHtml + "</div>" +
          "</div>";
      }

      html +=
        '<section class="plan-day" data-day="' + d + '">' +
        '<h3 class="plan-day__title">' + App.escapeHtml(label) + "</h3>" +
        mealsHtml +
        "</section>";
    }

    // Список покупок.
    var shoppingHtml = "";
    if (shopping.length) {
      var lis = "";
      for (var s = 0; s < shopping.length; s++) {
        lis += '<li class="plan-shopping__item">' + App.escapeHtml(shopping[s]) + "</li>";
      }
      shoppingHtml =
        '<section class="plan-shopping card">' +
        '<h3 class="plan-shopping__title">' +
        icon("cart", { size: 18 }) +
        App.escapeHtml(pick("Список покупок", "Shopping list")) + "</h3>" +
        '<ul class="plan-shopping__list">' + lis + "</ul>" +
        "</section>";
    }

    // Кнопка «Сгенерировать заново».
    var regenHtml =
      '<button type="button" class="btn btn--ghost btn-block plan-regenerate">' +
      icon("refresh", { size: 18 }) +
      App.escapeHtml(pick("Сгенерировать заново", "Generate again")) + "</button>";

    body.innerHTML =
      '<div class="plan-days">' + html + "</div>" +
      shoppingHtml +
      regenHtml;

    // Делегируем клики по кнопкам «Заменить».
    var daysWrap = body.querySelector(".plan-days");
    if (daysWrap) {
      daysWrap.addEventListener("click", function (ev) {
        var swapBtn = ev.target.closest(".plan-dish__swap");
        if (!swapBtn) return;
        var dIdx = parseInt(swapBtn.getAttribute("data-day"), 10);
        var mealType = swapBtn.getAttribute("data-meal");
        var iIdx = parseInt(swapBtn.getAttribute("data-idx"), 10);
        if (isNaN(dIdx) || !mealType || isNaN(iIdx)) return;
        regenerateDish(dIdx, mealType, iIdx, swapBtn);
      });
    }

    // Кнопка повторной генерации.
    var regenBtn = body.querySelector(".plan-regenerate");
    if (regenBtn) {
      regenBtn.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        requestMealPlan();
      });
    }
  }

  /**
   * Разметка одного блюда в плане меню с кнопкой замены.
   * Название блюда — данные от API, не переводим (экранируем).
   * @param {Object} dish {dish_name, calories, proteins, fats, carbs}
   * @param {number} dayIdx индекс дня
   * @param {string} mealType тип приёма пищи
   * @param {number} idx индекс блюда внутри приёма
   * @returns {string}
   */
  function planDishHtml(dish, dayIdx, mealType, idx) {
    dish = dish || {};
    var kcal = pick("ккал", "kcal");
    var pLabel = pick("Б", "P");
    var fLabel = pick("Ж", "F");
    var cLabel = pick("У", "C");

    return (
      '<div class="plan-dish" data-day="' + dayIdx + '" data-meal="' + mealType + '" data-idx="' + idx + '">' +
      '<div class="plan-dish__info">' +
      '<span class="plan-dish__name">' +
      App.escapeHtml(dish.dish_name || pick("Блюдо", "Dish")) + "</span>" +
      '<span class="plan-dish__macros">' +
      App.fmt(dish.calories || 0) + " " + kcal + " · " +
      pLabel + " " + App.fmt(dish.proteins || 0) + " · " +
      fLabel + " " + App.fmt(dish.fats || 0) + " · " +
      cLabel + " " + App.fmt(dish.carbs || 0) +
      "</span>" +
      "</div>" +
      '<button type="button" class="plan-dish__swap" ' +
      'data-day="' + dayIdx + '" data-meal="' + mealType + '" data-idx="' + idx + '" ' +
      'title="' + App.escapeHtml(pick("Заменить блюдо", "Replace dish")) + '" ' +
      'aria-label="' + App.escapeHtml(pick("Заменить блюдо", "Replace dish")) + '">' +
      icon("swap", { size: 16 }) +
      App.escapeHtml(pick("Заменить", "Replace")) + "</button>" +
      "</div>"
    );
  }

  /**
   * Заменяет одно блюдо в плане: запрашивает новое у сервера и обновляет UI
   * на месте (без полного перезапроса плана).
   * @param {number} dayIdx индекс дня
   * @param {string} mealType тип приёма пищи
   * @param {number} idx индекс блюда внутри приёма
   * @param {HTMLElement} swapBtn кнопка-инициатор (для блокировки/спиннера)
   */
  function regenerateDish(dayIdx, mealType, idx, swapBtn) {
    if (!state.plan || !state.plan.days || !state.plan.days[dayIdx]) return;
    var meals = state.plan.days[dayIdx].meals || {};
    var dishes = meals[mealType] || [];
    var current = dishes[idx];
    if (!current) return;

    if (swapBtn) {
      if (swapBtn.disabled) return; // защита от повторных кликов
      swapBtn.disabled = true;
      swapBtn.textContent = pick("Меняем…", "Swapping…");
    }
    App.haptic && App.haptic("light");

    if (!(App.api && typeof App.api.regenerateMealItem === "function")) {
      if (swapBtn) {
        swapBtn.disabled = false;
        swapBtn.innerHTML =
          icon("swap", { size: 16 }) + App.escapeHtml(pick("Заменить", "Replace"));
      }
      App.toast(pick("Замена временно недоступна.", "Replacing is temporarily unavailable."));
      return;
    }

    var payload = {
      meal_type: mealType,
      around_calories: Math.round(Number(current.calories) || 0)
    };
    var prefs = (state.planPrefs || "").trim();
    if (prefs) payload.preferences = prefs;

    App.api
      .regenerateMealItem(payload)
      .then(function (newDish) {
        if (!newDish || typeof newDish !== "object") {
          throw new Error(pick("Пустой ответ сервера", "Empty server response"));
        }
        // Обновляем модель плана и перерисовываем конкретное блюдо в DOM.
        state.plan.days[dayIdx].meals[mealType][idx] = newDish;
        replaceDishInDom(dayIdx, mealType, idx, newDish);
        App.haptic && App.haptic("success");
      })
      .catch(function (err) {
        App.haptic && App.haptic("error");
        App.toast((err && err.message) || pick("Не удалось заменить блюдо", "Failed to replace dish"));
        if (swapBtn) {
          swapBtn.disabled = false;
          swapBtn.innerHTML =
          icon("swap", { size: 16 }) + App.escapeHtml(pick("Заменить", "Replace"));
        }
      });
  }

  /**
   * Заменяет разметку конкретного блюда в DOM новым блюдом (после замены).
   * @param {number} dayIdx
   * @param {string} mealType
   * @param {number} idx
   * @param {Object} newDish
   */
  function replaceDishInDom(dayIdx, mealType, idx, newDish) {
    var body = document.getElementById("plan-body");
    if (!body) return;
    var selector =
      '.plan-dish[data-day="' + dayIdx + '"][data-meal="' + mealType + '"][data-idx="' + idx + '"]';
    var oldEl = body.querySelector(selector);
    if (!oldEl) return;

    var wrap = document.createElement("div");
    wrap.innerHTML = planDishHtml(newDish, dayIdx, mealType, idx);
    var newEl = wrap.firstChild;
    if (newEl && oldEl.parentNode) {
      oldEl.parentNode.replaceChild(newEl, oldEl);
    }
  }

  /**
   * Состояние ошибки внутри панели AI-плана меню с кнопкой «Повторить».
   * @param {string} message
   */
  function renderMealPlanError(message) {
    var body = document.getElementById("plan-body");
    if (!body) return;
    body.innerHTML =
      '<div class="plan-error">' +
      '<p class="plan-error__text">' + App.escapeHtml(message) + "</p>" +
      '<button type="button" class="btn btn--ghost plan-error__retry">' +
      App.escapeHtml(pick("Повторить", "Retry")) + "</button>" +
      "</div>";

    var retry = body.querySelector(".plan-error__retry");
    if (retry) {
      retry.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        requestMealPlan();
      });
    }
  }

  // ---------------------------------------------------------------------------
  // Карточка «Напоминания о еде».
  //
  // Хранится через общие настройки уведомлений (NotificationSettings):
  //   meal_reminder_enabled — тумблер включения напоминаний;
  //   breakfast_time / lunch_time / dinner_time — время для завтрака/обеда/ужина.
  // На сохранение отправляем только эти поля (остальные настройки уведомлений
  // живут в своих разделах: тренировки, добавки, аккаунт).
  // ---------------------------------------------------------------------------

  // Локальное состояние карточки «Напоминания о еде».
  // Список произвольного числа времён "HH:MM" + флаг включения.
  var mealReminders = {
    enabled: false,
    times: [] // массив строк "HH:MM"
  };

  /**
   * Безопасно приводит значение времени к строке "HH:MM" для поля type=time.
   * Сервер может вернуть "HH:MM:SS" — оставляем первые 5 символов.
   * @param {*} v
   * @returns {string}
   */
  function timeValue(v) {
    if (v == null) return "";
    var s = String(v).trim();
    if (s.length >= 5) return s.slice(0, 5);
    return s;
  }

  /**
   * Считывает текущий список времён из DOM (строки mr-time-row),
   * нормализует к "HH:MM" и синхронизирует mealReminders.times.
   * @returns {string[]}
   */
  function collectMealTimes() {
    var box = document.getElementById("diary-notif");
    var out = [];
    if (!box) return out;
    var inputs = box.querySelectorAll("[data-mr-time]");
    for (var i = 0; i < inputs.length; i++) {
      var val = timeValue(inputs[i].value);
      if (val) out.push(val);
    }
    mealReminders.times = out;
    return out;
  }

  /**
   * Загружает настройки уведомлений и рисует карточку «Напоминания о еде».
   * Карточка вспомогательная: при ошибке показываем кнопку «Повторить».
   * Свёрнутая по умолчанию (acc-fold), поэтому скелетон рисуем внутри тела.
   */
  function loadMealReminders() {
    var box = document.getElementById("diary-notif");
    if (!box) return;

    box.innerHTML =
      '<section class="card acc-fold" id="diary-notif-fold">' +
      '<button type="button" class="acc-fold__head">' +
      '<span class="acc-fold__title">' +
      App.escapeHtml(pick("Напоминания о еде", "Meal reminders")) + "</span>" +
      '<span class="acc-fold__chevron">' + icon("chevron", { size: 18, rotate: 90 }) + "</span>" +
      "</button>" +
      '<div class="acc-fold__body" hidden>' +
      '<div class="skeleton skeleton-block diary-notif-skeleton"></div>' +
      "</div>" +
      "</section>";

    // Тап по заголовку сворачивает/разворачивает карточку (по умолчанию свёрнута).
    bindMealRemindersFold();

    App.api
      .getNotificationSettings()
      .then(function (settings) {
        var s = settings || {};
        mealReminders.enabled = !!s.meal_reminder_enabled;
        // meal_times приходит массивом "HH:MM"; нормализуем и фильтруем пустые.
        var times = [];
        if (s.meal_times && s.meal_times.length) {
          for (var i = 0; i < s.meal_times.length; i++) {
            var t = timeValue(s.meal_times[i]);
            if (t) times.push(t);
          }
        }
        mealReminders.times = times;
        renderMealReminders();
      })
      .catch(function (err) {
        renderMealRemindersError(
          (err && err.message) ? err.message : pick("Ошибка сети", "Network error")
        );
      });
  }

  /**
   * Навешивает обработчик сворачивания на заголовок карточки напоминаний.
   * Повторяет паттерн acc-fold из «Аккаунта»: тап по .acc-fold__head
   * переключает класс acc-fold--open и атрибут hidden у тела.
   */
  function bindMealRemindersFold() {
    var box = document.getElementById("diary-notif");
    if (!box) return;
    var card = box.querySelector("#diary-notif-fold");
    if (!card) return;
    var head = card.querySelector(".acc-fold__head");
    if (!head) return;
    head.addEventListener("click", function () {
      var body = card.querySelector(".acc-fold__body");
      var open = card.classList.toggle("acc-fold--open");
      if (body) body.hidden = !open;
      App.haptic && App.haptic("selection");
    });
  }

  /**
   * Рисует тело карточки «Напоминания о еде» внутри acc-fold:
   * редактируемый список времён (mr-times) + кнопка активации (tgl-btn).
   * Опирается на состояние mealReminders (enabled + times).
   * Сохраняет текущее состояние сворачивания карточки (acc-fold--open).
   */
  function renderMealReminders() {
    var box = document.getElementById("diary-notif");
    if (!box) return;

    // Запоминаем, была ли карточка развёрнута, чтобы не «схлопнуть» её при
    // перерисовке после сохранения/переключения.
    var prevCard = box.querySelector("#diary-notif-fold");
    var wasOpen = !!(prevCard && prevCard.classList.contains("acc-fold--open"));

    var enabled = !!mealReminders.enabled;
    var times = mealReminders.times || [];

    // Строки существующих времён с кнопкой удаления.
    var rowsHtml = times.map(function (t) {
      return (
        '<div class="mr-time-row">' +
        '<input class="field__input" type="time" data-mr-time value="' +
        App.escapeHtml(timeValue(t)) + '">' +
        '<button type="button" class="mr-time-del" aria-label="' +
        App.escapeHtml(pick("Удалить время", "Delete time")) +
        '">' + icon("close", { size: 18 }) + "</button>" +
        "</div>"
      );
    }).join("");

    // Подсказка, если времён ещё нет.
    var hintHtml = times.length
      ? ""
      : '<p class="diary-notif-sub mr-times__hint">' +
        App.escapeHtml(pick(
          "Добавьте время, чтобы получать напоминания залогировать приём пищи.",
          "Add a time to get reminders to log your meal."
        )) + "</p>";

    var tglOnClass = enabled ? " tgl-btn--on" : "";
    var tglLabel = enabled
      ? pick("Деактивировать", "Deactivate")
      : pick("Активировать", "Activate");

    box.innerHTML =
      '<section class="card acc-fold' + (wasOpen ? " acc-fold--open" : "") +
      '" id="diary-notif-fold">' +
      '<button type="button" class="acc-fold__head">' +
      '<span class="acc-fold__title">' +
      App.escapeHtml(pick("Напоминания о еде", "Meal reminders")) + "</span>" +
      '<span class="acc-fold__chevron">' + icon("chevron", { size: 18, rotate: 90 }) + "</span>" +
      "</button>" +
      '<div class="acc-fold__body"' + (wasOpen ? "" : " hidden") + ">" +
      '<p class="diary-notif-sub">' +
      App.escapeHtml(pick(
        "Будем напоминать залогировать приёмы пищи в выбранное время.",
        "We'll remind you to log your meals at the chosen times."
      )) + "</p>" +
      '<div class="mr-times" id="mr-times">' +
      rowsHtml +
      hintHtml +
      // Строка добавления нового времени.
      '<div class="mr-time-row mr-time-add">' +
      '<input class="field__input" type="time" id="mr-time-new">' +
      '<button type="button" class="btn btn--ghost mr-time-add__btn" id="mr-time-add">' +
      icon("plus", { size: 18 }) +
      App.escapeHtml(pick("Добавить время", "Add time")) + "</button>" +
      "</div>" +
      "</div>" +
      '<button type="button" class="tgl-btn' + tglOnClass +
      '" id="mr-toggle">' + App.escapeHtml(tglLabel) + "</button>" +
      "</div>" +
      "</section>";

    // Заголовок сворачивания.
    bindMealRemindersFold();

    // Кнопки удаления существующих времён.
    var delBtns = box.querySelectorAll(".mr-time-del");
    for (var i = 0; i < delBtns.length; i++) {
      delBtns[i].addEventListener("click", onMealTimeDelete);
    }

    // Кнопка добавления нового времени.
    var addBtn = box.querySelector("#mr-time-add");
    if (addBtn) addBtn.addEventListener("click", onMealTimeAdd);

    // Подсветка ошибки снимается при первом же вводе — ошибка не должна
    // «висеть» после того, как её исправили.
    var newTime = box.querySelector("#mr-time-new");
    if (newTime) {
      newTime.addEventListener("input", function () { clearInvalid(newTime); });
      newTime.addEventListener("change", function () { clearInvalid(newTime); });
    }

    // Кнопка активации/деактивации.
    var toggleBtn = box.querySelector("#mr-toggle");
    if (toggleBtn) toggleBtn.addEventListener("click", onToggleMealReminders);
  }

  /**
   * Добавляет введённое время в список и (если напоминания включены)
   * сохраняет обновлённый список на сервере.
   */
  function onMealTimeAdd() {
    var box = document.getElementById("diary-notif");
    if (!box) return;
    var input = box.querySelector("#mr-time-new");
    var val = input ? timeValue(input.value) : "";
    if (!val) {
      // Подсветка + фокус на самом поле времени, а не только тост: карточка
      // свёрнутая и длинная, и «где именно» без подсветки не видно.
      invalidField(input, pick("Укажите время", "Choose a time"));
      return;
    }
    clearInvalid(input);
    // Считываем текущие времена из DOM и добавляем новое.
    var times = collectMealTimes();
    times.push(val);
    mealReminders.times = times;
    App.haptic && App.haptic("light");
    // Если включено — персистим сразу; иначе просто перерисовываем локально.
    if (mealReminders.enabled) {
      persistMealTimes();
    } else {
      renderMealReminders();
    }
  }

  /**
   * Удаляет время из списка (клик по крестику) и, если напоминания включены,
   * сохраняет обновлённый список.
   * @param {Event} e
   */
  function onMealTimeDelete(e) {
    var box = document.getElementById("diary-notif");
    if (!box) return;
    var row = e.target.closest(".mr-time-row");
    if (row && row.parentNode) row.parentNode.removeChild(row);
    // Синхронизируем состояние с оставшимися строками DOM.
    var times = collectMealTimes();
    mealReminders.times = times;
    App.haptic && App.haptic("light");
    if (mealReminders.enabled) {
      persistMealTimes();
    } else {
      renderMealReminders();
    }
  }

  /**
   * Переключает активацию напоминаний (tgl-btn):
   * OFF -> сохраняем meal_reminder_enabled:true с текущим списком времён;
   * ON  -> сохраняем meal_reminder_enabled:false.
   */
  function onToggleMealReminders() {
    var box = document.getElementById("diary-notif");
    if (!box) return;

    var turningOn = !mealReminders.enabled;
    var payload;
    if (turningOn) {
      // Актуализируем список времён из DOM перед включением.
      var times = collectMealTimes();
      mealReminders.times = times;
      payload = { meal_reminder_enabled: true, meal_times: times };
    } else {
      payload = { meal_reminder_enabled: false };
    }

    var toggleBtn = box.querySelector("#mr-toggle");
    if (toggleBtn) {
      toggleBtn.disabled = true;
      toggleBtn.textContent = pick("Сохраняем…", "Saving…");
    }
    App.showLoading();

    App.api
      .saveNotificationSettings(payload)
      .then(function (settings) {
        applyMealRemindersResponse(settings, turningOn);
        renderMealReminders();
        App.haptic && App.haptic("success");
        App.toast(
          turningOn
            ? pick("Напоминания включены", "Reminders enabled")
            : pick("Напоминания выключены", "Reminders disabled")
        );
      })
      .catch(function (err) {
        App.haptic && App.haptic("error");
        App.toast((err && err.message) || pick("Не удалось сохранить напоминания", "Failed to save reminders"));
        // Возвращаем кнопку в исходное состояние.
        renderMealReminders();
      })
      .then(function () {
        App.hideLoading();
      });
  }

  /**
   * Сохраняет текущий список времён на сервере (используется при
   * редактировании списка, когда напоминания уже включены).
   */
  function persistMealTimes() {
    var payload = {
      meal_reminder_enabled: true,
      meal_times: mealReminders.times || []
    };
    App.showLoading();
    App.api
      .saveNotificationSettings(payload)
      .then(function (settings) {
        applyMealRemindersResponse(settings, true);
        renderMealReminders();
        App.haptic && App.haptic("success");
        App.toast(pick("Напоминания обновлены", "Reminders updated"));
      })
      .catch(function (err) {
        App.haptic && App.haptic("error");
        App.toast((err && err.message) || pick("Не удалось сохранить напоминания", "Failed to save reminders"));
        renderMealReminders();
      })
      .then(function () {
        App.hideLoading();
      });
  }

  /**
   * Обновляет локальное состояние mealReminders из ответа сервера
   * (или из ожидаемого состояния, если сервер не вернул детали).
   * @param {Object} settings NotificationSettingsOut
   * @param {boolean} expectedEnabled ожидаемое значение флага
   */
  function applyMealRemindersResponse(settings, expectedEnabled) {
    var s = settings || {};
    mealReminders.enabled =
      s.meal_reminder_enabled != null ? !!s.meal_reminder_enabled : !!expectedEnabled;
    if (s.meal_times && s.meal_times.length != null) {
      var times = [];
      for (var i = 0; i < s.meal_times.length; i++) {
        var t = timeValue(s.meal_times[i]);
        if (t) times.push(t);
      }
      mealReminders.times = times;
    }
  }

  /**
   * Состояние ошибки карточки напоминаний с кнопкой «Повторить».
   * @param {string} message
   */
  function renderMealRemindersError(message) {
    var box = document.getElementById("diary-notif");
    if (!box) return;

    // Сохраняем состояние сворачивания, чтобы ошибка не «схлопнула» карточку.
    var prevCard = box.querySelector("#diary-notif-fold");
    var wasOpen = !!(prevCard && prevCard.classList.contains("acc-fold--open"));

    box.innerHTML =
      '<section class="card acc-fold' + (wasOpen ? " acc-fold--open" : "") +
      '" id="diary-notif-fold">' +
      '<button type="button" class="acc-fold__head">' +
      '<span class="acc-fold__title">' +
      App.escapeHtml(pick("Напоминания о еде", "Meal reminders")) + "</span>" +
      '<span class="acc-fold__chevron">' + icon("chevron", { size: 18, rotate: 90 }) + "</span>" +
      "</button>" +
      '<div class="acc-fold__body"' + (wasOpen ? "" : " hidden") + ">" +
      '<div class="diary-notif-error">' +
      '<p class="diary-notif-error__text">' +
      App.escapeHtml(pick(
        "Не удалось загрузить настройки напоминаний.",
        "Failed to load reminder settings."
      )) + "</p>" +
      '<p class="diary-notif-error__msg">' + App.escapeHtml(message) + "</p>" +
      '<button type="button" class="btn btn--ghost diary-notif-retry" id="diary-notif-retry">' +
      App.escapeHtml(pick("Повторить", "Retry")) + "</button>" +
      "</div>" +
      "</div>" +
      "</section>";

    bindMealRemindersFold();

    var retry = box.querySelector("#diary-notif-retry");
    if (retry) {
      retry.addEventListener("click", function () {
        App.haptic && App.haptic("light");
        loadMealReminders();
      });
    }
  }

  /**
   * Обновляет подпись даты в шапке без полной перерисовки страницы.
   */
  function updateDateLabel() {
    var dateEl = state.viewEl && state.viewEl.querySelector(".diary-datebar__date");
    var isoEl = state.viewEl && state.viewEl.querySelector(".diary-datebar__iso");
    if (dateEl) dateEl.textContent = humanDate(state.date);
    if (isoEl) isoEl.textContent = state.date;
    // «Следующий день» блокируем для будущих дат (пересчитываем при смене даты).
    var nextBtn = state.viewEl && state.viewEl.querySelector('.diary-datebar__nav[data-nav="next"]');
    if (nextBtn) nextBtn.disabled = state.date >= App.todayStr();
  }

  /**
   * Переключение даты на заданное число дней и перезагрузка.
   * @param {number} delta
   */
  function changeDate(delta) {
    // Не уходим в будущее: «следующий день» недоступен для дат >= сегодня.
    if (delta > 0 && state.date >= App.todayStr()) return;
    state.date = shiftDate(state.date, delta);
    // Открытые листы и панель относились к прежнему дню — закрываем.
    state.panel = null;
    closeAllSheets();
    closeCalendar();
    App.haptic && App.haptic("selection");
    updateDateLabel();
    loadAndRender();
  }

  // ---------------------------------------------------------------------------
  // Контроллер страницы для App.registerPage.
  // ---------------------------------------------------------------------------
  var controller = {
    /**
     * Вызывается при показе страницы. Строит разметку и загружает данные.
     * @param {HTMLElement} viewEl контейнер #view
     */
    onShow: function (viewEl) {
      state.viewEl = viewEl;

      // Какой день открыть. По умолчанию — сегодня, НО если экран определения
      // еды записал блюдо в другой день (человек листнул дневник на вчера и
      // нажал «+»), он оставляет App.state.diaryReturnDate. Без этого запись
      // уходила во вчера, а дневник открывался на сегодня — и человек её не
      // находил. Флаг одноразовый: прочитали — сбросили.
      state.date = App.todayStr();
      if (App.state && App.state.diaryReturnDate) {
        state.date = App.state.diaryReturnDate;
        App.state.diaryReturnDate = null;
      }

      // КОНТРАКТ С «СЕГОДНЯ»: другой экран может попросить сразу открыть
      // нужный лист (App.state.diaryOpenSheet). Флаг одноразовый — гасим сразу,
      // иначе лист открывался бы при каждом следующем заходе во вкладку.
      var wantSheet = App.state ? App.state.diaryOpenSheet : null;
      if (App.state) App.state.diaryOpenSheet = null;

      // Фото и голос живут на экране камеры: уходим туда, не рисуя дневник.
      // Отрисовка здесь только запустила бы запрос дня, ответ на который
      // пришёл бы уже на чужой экран.
      if (wantSheet === "photo" || wantSheet === "voice") {
        openScan(wantSheet);
        return;
      }

      // AI-панель при входе на страницу закрыта.
      state.panel = null;
      state.day = null;
      state.calMonth = null;
      // Сбрасываем состояние AI-панелей (план меню / умные предложения).
      state.plan = null;
      state.planScope = "day";
      state.planPrefs = "";
      state.suggestMeal = null;
      state.suggestText = "";

      // Прокручиваем к началу при входе на страницу.
      App.scrollTop && App.scrollTop();

      // Базовая разметка: переключатель даты + контейнер для контента.
      // Заголовок локализуем на момент рендера.
      viewEl.innerHTML =
        '<div class="page page-diary">' +
        '<div class="diary-head">' +
        // Заголовок совпадает с подписью вкладки: человек нажал «Питание» —
        // и должен увидеть «Питание», а не другое имя того же раздела.
        '<h1 class="page__title">' + App.escapeHtml(pick("Питание", "Nutrition")) + "</h1>" +
        '<span id="diary-streak" class="diary-streak" hidden></span>' +
        "</div>" +
        dateBarHtml() +
        '<div id="diary-content" class="diary-content"></div>' +
        "</div>";

      // Навигация по датам (стрелки влево/вправо).
      var navButtons = viewEl.querySelectorAll(".diary-datebar__nav");
      for (var i = 0; i < navButtons.length; i++) {
        navButtons[i].addEventListener("click", function (ev) {
          var dir = ev.currentTarget.getAttribute("data-nav");
          changeDate(dir === "next" ? 1 : -1);
        });
      }

      // Открытие мини-календаря по тапу на подпись даты.
      var calToggle = viewEl.querySelector("[data-open-cal]");
      if (calToggle) {
        calToggle.addEventListener("click", function () {
          App.haptic && App.haptic("light");
          App.miniCalendarToggle(
            document.getElementById("diary-cal"),
            state.date,
            pickCalendarDay
          );
        });
      }

      // Плавающая «+» — вход в лист выбора способа добавления.
      mountFab();

      // Загружаем данные за выбранную дату.
      loadAndRender();

      // Ручной ввод и активность — нижние листы поверх дня. Листы монтируются
      // в #view и от загрузки дня не зависят, поэтому открываем сразу.
      // Для бесплатного пользователя «Активность» ставит state.panel, и
      // пейволл дорисуется, когда renderDay создаст область панели.
      if (wantSheet === "manual" || wantSheet === "activity") {
        onSheetAction(wantSheet);
      }
    },

    /**
     * Вызывается при уходе со страницы — чистим ссылки на DOM.
     */
    onHide: function () {
      // Убираем плавающую кнопку и все открытые листы.
      unmountFab();
      closeAllSheets(true);
      closeCalendar();

      state.viewEl = null;
      state.loading = false;
      state.panel = null;
      state.day = null;
      state.calMonth = null;
      // Сбрасываем состояние AI-панелей.
      state.plan = null;
      state.planPrefs = "";
      state.suggestMeal = null;
      state.suggestText = "";
    }
  };

  // Регистрируем страницу в приложении.
  // window.PageDiary — публичная ссылка на контроллер (на случай нужды извне).
  window.PageDiary = controller;
  App.registerPage("diary", controller);
})();
