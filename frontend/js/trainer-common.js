/*
 * trainer-common.js — общий модуль раздела «AI-тренер» (window.Trainer).
 *
 * Подключается ПЕРВЫМ из скриптов тренера (после app.js). Страницы тренера
 * (page-trainer*.js) опираются на этот публичный контракт (ТЗ §6.1):
 *
 *   Trainer.L                — словари RU/EN (goal, level, equipment, muscle,
 *                              weekday, setType, feedback, reason, phase, …);
 *   Trainer.label(group,key) — подпись из словаря через App.pick НА МОМЕНТ рендера;
 *   Trainer.exName(ex)       — имя упражнения по языку (name_ru / name_en);
 *   Trainer.fmtKg / fmtSet / fmtTarget / fmtClock / fmtNum — форматирование;
 *   Trainer.fmtDuration(min) — длительность по-человечески («45 мин», «2 ч 15 мин»);
 *   Trainer.humanDate / shortDate / shiftDate / weekdayOf — даты;
 *   Trainer.go(page) / back() — навигация с запоминанием App.state.trainerOrigin;
 *   Trainer.SEGMENTS / normSegment / openSegment(seg) — вкладки раздела
 *                            (App.state.trainerSegment, см. page-trainer.js);
 *   Trainer.openExercise(id, ret) / takeExerciseReturn / returnFromExercise /
 *   isReturning()            — карточка упражнения и возврат туда, откуда пришли;
 *   Trainer.headHtml / bindBack — шапка страницы (классы .sub-head/.sub-back);
 *   Trainer.sheet(items,onPick,opts) / closeSheet — нижний лист (.tr-sheet*);
 *   Trainer.RestTimer        — таймер отдыха на Date.now() (переживает сворачивание);
 *   Trainer.beep()           — короткий сигнал WebAudio (разблокировка первым тапом);
 *   Trainer.lineChart(points,{w,h,key}) — SVG-строка графика;
 *   Trainer.skeleton(n) / errorCard(msg, retryId) / genWaitHtml / runGenerate;
 *   Trainer.errMessage(err) / confirm(msg) / parseServerDate / elapsedMin;
 *   Trainer.paywallOpts() / paywall(viewEl, opts) / isPro() — платный доступ;
 *   Trainer.openProgram(program, mode) / startSession(dayId) / openSession(s)
 *                            — общие сценарии переходов между страницами;
 *   Trainer.cache            — кэш overview/activeSession с invalidate() и
 *                              счётчиком версии данных cache.version.
 *
 * ИКОНКИ: везде App.icon(name) из js/icons.js. Имя иконки передаётся отдельным
 * полем (headHtml({icon:"coach"}), sheet([{icon:"swap"}])) и НИКОГДА не живёт
 * внутри переводимой строки: иначе картинка дублируется в обеих локалях и
 * попадает в перевод вместе с текстом.
 *
 * Локализация: все строки — App.pick(ru, en) в момент вызова; словари хранят
 * пары [ru, en] и резолвятся при отрисовке. Стиль — IIFE, 'use strict',
 * var/function, Promise-цепочки.
 */
(function () {
  "use strict";

  /**
   * Локализация с безопасным фолбэком на русский.
   */
  function pick(ru, en) {
    if (window.App && typeof App.pick === "function") return App.pick(ru, en);
    return ru;
  }

  function esc(s) {
    if (window.App && typeof App.escapeHtml === "function") {
      return App.escapeHtml(s == null ? "" : String(s));
    }
    return String(s == null ? "" : s);
  }

  /**
   * Иконка из общего набора (js/icons.js). Отдельная обёртка нужна потому,
   * что icons.js может быть ещё не подключён (порядок скриптов меняли) —
   * тогда вместо падения страницы получаем пустую строку.
   * @param {string} name имя из App.iconNames
   * @param {object} [opts] {size, rotate, cls, stroke}
   * @returns {string} разметка <svg …> или ""
   */
  function icon(name, opts) {
    if (!name) return "";
    if (window.App && typeof App.icon === "function") return App.icon(name, opts);
    if (typeof window.AppIcon === "function") return window.AppIcon(name, opts);
    return "";
  }

  /**
   * Значение переменной --hero-img для фото тёмного блока (тот же приём,
   * что на экране «Сегодня»). Путь делаем абсолютным: относительный url()
   * внутри custom property Chrome разрешает от адреса style.css, где
   * переменная подставляется, а не от страницы — картинка запрашивалась как
   * css/img/… и уходила в 404.
   * @param {string} file имя файла в frontend/img
   */
  function heroImg(file) {
    return App.heroImg(file);
  }

  /* =====================================================================
   *  СЛОВАРИ (пары [ru, en]; ключи = коды из ТЗ §2/§3)
   * ===================================================================== */

  var L = {
    // Цель программы.
    goal: {
      loss: ["Похудеть", "Lose weight"],
      muscle: ["Набрать мышцы", "Build muscle"],
      strength: ["Стать сильнее", "Get stronger"],
      endurance: ["Выносливость", "Endurance"],
      tone: ["Тонус и здоровье", "Tone & health"]
    },
    // Уровень подготовки.
    level: {
      beginner: ["Новичок", "Beginner"],
      intermediate: ["Средний", "Intermediate"],
      advanced: ["Опытный", "Advanced"]
    },
    // Оборудование: профильные наборы + коды каталога + доп. чипы.
    equipment: {
      gym: ["Зал", "Gym"],
      home_dumbbells: ["Дома с гантелями", "Home with dumbbells"],
      bodyweight: ["Вес тела", "Bodyweight"],
      barbell: ["Штанга", "Barbell"],
      dumbbell: ["Гантели", "Dumbbells"],
      machine: ["Тренажёр", "Machine"],
      cable: ["Блок", "Cable"],
      band: ["Резинка", "Band"],
      bands: ["Резинки", "Bands"],
      kettlebell: ["Гиря", "Kettlebell"],
      pullup_bar: ["Турник", "Pull-up bar"],
      bench: ["Скамья", "Bench"],
      cardio_machine: ["Кардиотренажёр", "Cardio machine"],
      none: ["Без оборудования", "No equipment"]
    },
    // Группы мышц (каталог) + коды фокуса.
    muscle: {
      chest: ["Грудь", "Chest"],
      back: ["Спина", "Back"],
      shoulders: ["Плечи", "Shoulders"],
      biceps: ["Бицепс", "Biceps"],
      triceps: ["Трицепс", "Triceps"],
      quads: ["Квадрицепсы", "Quads"],
      hamstrings: ["Задняя поверхность бедра", "Hamstrings"],
      glutes: ["Ягодицы", "Glutes"],
      calves: ["Икры", "Calves"],
      core: ["Кор", "Core"],
      full_body: ["Всё тело", "Full body"],
      cardio: ["Кардио", "Cardio"],
      mobility: ["Мобильность", "Mobility"],
      arms: ["Руки", "Arms"],
      legs: ["Ноги", "Legs"],
      none: ["Без акцента", "No focus"]
    },
    // Дни недели, 0=Пн … 6=Вс (короткие).
    weekday: {
      0: ["Пн", "Mon"],
      1: ["Вт", "Tue"],
      2: ["Ср", "Wed"],
      3: ["Чт", "Thu"],
      4: ["Пт", "Fri"],
      5: ["Сб", "Sat"],
      6: ["Вс", "Sun"]
    },
    // Дни недели полные.
    weekdayLong: {
      0: ["Понедельник", "Monday"],
      1: ["Вторник", "Tuesday"],
      2: ["Среда", "Wednesday"],
      3: ["Четверг", "Thursday"],
      4: ["Пятница", "Friday"],
      5: ["Суббота", "Saturday"],
      6: ["Воскресенье", "Sunday"]
    },
    // Тип подхода.
    setType: {
      warmup: ["Разминочный", "Warm-up"],
      work: ["Рабочий", "Working"],
      drop: ["Дроп-сет", "Drop set"],
      failure: ["До отказа", "To failure"]
    },
    // Отзыв после тренировки.
    feedback: {
      easy: ["Слишком легко", "Too easy"],
      ok: ["В самый раз", "Just right"],
      hard: ["Слишком тяжело", "Too hard"]
    },
    // Причина замены упражнения.
    reason: {
      busy: ["Занят тренажёр", "Equipment is busy"],
      no_equipment: ["Нет оборудования", "No equipment"],
      pain: ["Болит", "Pain / discomfort"],
      other: ["Другое", "Other"]
    },
    // Фаза периодизации.
    phase: {
      base: ["База", "Base"],
      build: ["Рост", "Build"],
      peak: ["Пик", "Peak"],
      deload: ["Разгрузка", "Deload"]
    },
    // Ограничения и травмы.
    limitation: {
      knee: ["Колени", "Knees"],
      lower_back: ["Поясница", "Lower back"],
      shoulder: ["Плечи", "Shoulders"],
      wrist: ["Запястья/локти", "Wrists/elbows"],
      neck: ["Шея", "Neck"],
      hip: ["Тазобедренные", "Hips"],
      pregnancy: ["Беременность/после родов", "Pregnancy/postpartum"],
      heart_bp: ["Давление/сердце", "Blood pressure/heart"],
      none: ["Нет", "None"]
    },
    // Тип сплита программы.
    split: {
      full_body: ["Full body", "Full body"],
      upper_lower: ["Верх/Низ", "Upper/Lower"],
      ppl: ["Push/Pull/Legs", "Push/Pull/Legs"],
      custom: ["Свой", "Custom"]
    },
    // Тип сессии/дня.
    sessionType: {
      strength: ["Силовая", "Strength"],
      cardio: ["Кардио", "Cardio"],
      mixed: ["Смешанная", "Mixed"],
      mobility: ["Мобильность", "Mobility"]
    },
    // Статус дня плана / точки ленты недели.
    dayStatus: {
      planned: ["По плану", "Planned"],
      done: ["Выполнено", "Done"],
      skipped: ["Пропущено", "Skipped"],
      rest: ["Отдых", "Rest"],
      today: ["Сегодня", "Today"],
      in_progress: ["В процессе", "In progress"]
    },
    // Тип измерения упражнения.
    measure: {
      reps_weight: ["Вес × повторы", "Weight × reps"],
      reps: ["Повторы", "Reps"],
      time: ["Время", "Time"],
      distance: ["Дистанция", "Distance"]
    },
    // Категория упражнения.
    category: {
      compound: ["Базовое", "Compound"],
      isolation: ["Изолирующее", "Isolation"],
      cardio: ["Кардио", "Cardio"],
      mobility: ["Мобильность", "Mobility"],
      stretch: ["Растяжка", "Stretch"]
    },
    // Вид изменения в адаптации/разборе.
    changeKind: {
      weight: ["Вес", "Weight"],
      reps: ["Повторы", "Reps"],
      sets: ["Подходы", "Sets"],
      keep: ["Без изменений", "Keep"],
      deload: ["Разгрузка", "Deload"],
      weight_pct: ["Вес, %", "Weight, %"],
      swap: ["Замена", "Swap"],
      rest_sec: ["Отдых", "Rest"],
      deload_next_week: ["Разгрузочная неделя", "Deload week"]
    },
    // Тип рекорда.
    recordType: {
      max_weight: ["Макс. вес", "Max weight"],
      est_1rm: ["1RM (расч.)", "Est. 1RM"],
      max_reps: ["Макс. повторов", "Max reps"],
      set_volume: ["Объём подхода", "Set volume"],
      max_time: ["Макс. время", "Max time"]
    }
  };

  /**
   * Подпись из словаря по группе и ключу (резолвится через App.pick сейчас).
   * Неизвестный ключ возвращается как есть (строкой), чтобы UI не «падал».
   * @param {string} group имя группы в Trainer.L
   * @param {string|number} key код
   * @returns {string}
   */
  function label(group, key) {
    var dict = L[group];
    if (!dict) return key == null ? "" : String(key);
    var pair = dict[key];
    if (!pair) return key == null ? "" : String(key);
    return pick(pair[0], pair[1]);
  }

  /**
   * Список подписей через запятую (например, мышцы дня).
   * @param {string} group
   * @param {Array} keys
   * @returns {string}
   */
  function labels(group, keys) {
    if (!keys || !keys.length) return "";
    var out = [];
    for (var i = 0; i < keys.length; i++) out.push(label(group, keys[i]));
    return out.join(", ");
  }

  /* =====================================================================
   *  ФОРМАТИРОВАНИЕ
   * ===================================================================== */

  /**
   * Имя упражнения по языку интерфейса. Принимает объект упражнения
   * ({name_ru, name_en}), строку сессии ({exercise:{…}}) или PR/изменение
   * ({exercise_name_ru, exercise_name_en}).
   */
  function exName(ex) {
    if (!ex) return "";
    var e = ex.exercise && typeof ex.exercise === "object" ? ex.exercise : ex;
    var ru = e.name_ru || e.exercise_name_ru || "";
    var en = e.name_en || e.exercise_name_en || "";
    var name = App.lang === "en" ? en || ru : ru || en;
    return name || e.slug || "";
  }

  /**
   * Число с не более чем одним знаком после точки (12.5, 40, 0.5).
   */
  function fmtNum(n) {
    var x = Number(n);
    if (!isFinite(x)) return "0";
    var r = Math.round(x * 10) / 10;
    return String(r);
  }

  /**
   * Вес: «40 кг» / «12.5 kg»; null/пусто → «—».
   */
  function fmtKg(w) {
    if (w === null || w === undefined || w === "") return "—";
    var x = Number(w);
    if (!isFinite(x)) return "—";
    return fmtNum(x) + " " + pick("кг", "kg");
  }

  /**
   * Секунды → «м:сс» (для таймеров и секундомера).
   */
  function fmtClock(sec) {
    var s = Math.max(0, Math.round(Number(sec) || 0));
    var m = Math.floor(s / 60);
    var r = s % 60;
    return m + ":" + (r < 10 ? "0" + r : "" + r);
  }

  /**
   * Результат подхода: «40×10», «10» (только повторы), «45 с» (время).
   * @param {number|null} w вес, кг
   * @param {number|null} r повторы
   * @param {number|null} t время, сек
   */
  function fmtSet(w, r, t) {
    var hasW = w !== null && w !== undefined && w !== "" && Number(w) > 0;
    var hasR = r !== null && r !== undefined && r !== "";
    var hasT = t !== null && t !== undefined && t !== "";
    if (hasT && !hasR) return fmtNum(t) + " " + pick("с", "s");
    if (hasW && hasR) return fmtNum(w) + "×" + fmtNum(r);
    if (hasR) return fmtNum(r);
    if (hasW) return fmtKg(w);
    return "—";
  }

  /**
   * Целевая строка плана: «3×8–12 · 14 кг · отдых 90 с» (ТЗ §2.3).
   * item — TrainerPlanItemOut или TrainerSessionExerciseOut (planned_*).
   */
  /**
   * Форма слова по числу: plural(3, ["точка","точки","точек"], ["point","points"]).
   * RU — три формы (1 / 2–4 / 5–20), EN — единственное и множественное.
   */
  function plural(n, ru, en) {
    var abs = Math.abs(Math.round(Number(n) || 0));
    if (App.lang === "en") return abs === 1 ? en[0] : en[1] || en[0];
    var mod10 = abs % 10;
    var mod100 = abs % 100;
    if (mod10 === 1 && mod100 !== 11) return ru[0];
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return ru[1];
    return ru[2];
  }

  function fmtTarget(item) {
    if (!item) return "";
    // В сессии `sets` — массив записанных подходов, план лежит в planned_sets.
    var sets = item.sets != null && !Array.isArray(item.sets) ? item.sets : item.planned_sets;
    var rMin = item.reps_min != null ? item.reps_min : item.planned_reps_min;
    var rMax = item.reps_max != null ? item.reps_max : item.planned_reps_max;
    var t = item.time_sec != null ? item.time_sec : item.planned_time_sec;
    var w = item.target_weight_kg != null ? item.target_weight_kg : item.planned_weight_kg;
    var rest = item.rest_sec != null ? item.rest_sec : item.planned_rest_sec;
    var rpe = item.rpe != null ? item.rpe : item.planned_rpe;
    var parts = [];
    var reps = "";
    if (rMin != null && rMax != null && rMin !== rMax) reps = rMin + "–" + rMax;
    else if (rMax != null) reps = String(rMax);
    else if (rMin != null) reps = String(rMin);
    if (t != null && !reps) {
      parts.push((sets != null ? sets + "×" : "") + fmtNum(t) + " " + pick("с", "s"));
    } else if (sets != null || reps) {
      parts.push((sets != null ? sets : "") + (sets != null && reps ? "×" : "") + reps);
    }
    if (w != null && Number(w) > 0) parts.push(fmtKg(w));
    if (rest != null) parts.push(pick("отдых ", "rest ") + fmtNum(rest) + " " + pick("с", "s"));
    if (rpe != null) parts.push("RPE " + fmtNum(rpe));
    return parts.join(" · ");
  }

  /* =====================================================================
   *  ДАТЫ
   * ===================================================================== */

  /**
   * Прибавляет к ISO-дате число дней (через полдень — без сдвига суток).
   */
  function shiftDate(isoDate, deltaDays) {
    var parts = String(isoDate).split("-");
    var y = parseInt(parts[0], 10);
    var m = parseInt(parts[1], 10) - 1;
    var d = parseInt(parts[2], 10);
    var dt = new Date(y, m, d, 12, 0, 0, 0);
    dt.setDate(dt.getDate() + (deltaDays || 0));
    var mm = String(dt.getMonth() + 1).padStart(2, "0");
    var dd = String(dt.getDate()).padStart(2, "0");
    return dt.getFullYear() + "-" + mm + "-" + dd;
  }

  /**
   * День недели ISO-даты: 0=Пн … 6=Вс.
   */
  function weekdayOf(isoDate) {
    var parts = String(isoDate).split("-");
    var dt = new Date(
      parseInt(parts[0], 10),
      parseInt(parts[1], 10) - 1,
      parseInt(parts[2], 10),
      12, 0, 0, 0
    );
    if (isNaN(dt.getTime())) return 0;
    return (dt.getDay() + 6) % 7;
  }

  var MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"
  ];
  var MONTHS_EN = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
  ];
  var MONTHS_SHORT_RU = [
    "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек"
  ];
  var MONTHS_SHORT_EN = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
  ];

  /**
   * Человекочитаемая дата: Сегодня / Вчера / Завтра / «18 июня 2026».
   */
  function humanDate(isoDate) {
    var today = App.todayStr();
    if (isoDate === today) return pick("Сегодня", "Today");
    if (isoDate === shiftDate(today, -1)) return pick("Вчера", "Yesterday");
    if (isoDate === shiftDate(today, 1)) return pick("Завтра", "Tomorrow");
    var parts = String(isoDate).split("-");
    var y = parseInt(parts[0], 10);
    var m = parseInt(parts[1], 10) - 1;
    var d = parseInt(parts[2], 10);
    if (isNaN(y) || isNaN(m) || isNaN(d) || !MONTHS_RU[m]) return isoDate || "";
    if (App.lang === "en") return MONTHS_EN[m] + " " + d + ", " + y;
    return d + " " + MONTHS_RU[m] + " " + y;
  }

  /**
   * Короткая дата с днём недели: «Пн, 2 сен» / «Mon, Sep 2». withDow=false →
   * без дня недели.
   */
  function shortDate(isoDate, withDow) {
    var parts = String(isoDate).split("-");
    var m = parseInt(parts[1], 10) - 1;
    var d = parseInt(parts[2], 10);
    if (isNaN(m) || isNaN(d) || !MONTHS_SHORT_RU[m]) return isoDate || "";
    var core = App.lang === "en" ? MONTHS_SHORT_EN[m] + " " + d : d + " " + MONTHS_SHORT_RU[m];
    if (withDow === false) return core;
    return label("weekday", weekdayOf(isoDate)) + ", " + core;
  }

  /**
   * Разбирает дату/время сервера. Бэкенд хранит datetime.utcnow без зоны —
   * строку без «Z»/смещения считаем UTC, иначе часы «уплывут».
   * @returns {Date|null}
   */
  function parseServerDate(raw) {
    if (!raw) return null;
    var s = String(raw);
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(s)) s += "Z";
    var d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }

  /**
   * Минут прошло с момента серверной метки (0 при ошибке разбора).
   * ВНИМАНИЕ: число не ограничено сверху — брошенная неделю назад сессия
   * честно вернёт 9532. Показывать его человеку нельзя: для подписи
   * всегда пропускаем результат через fmtDuration().
   */
  function elapsedMin(raw) {
    var d = parseServerDate(raw);
    if (!d) return 0;
    return Math.max(0, Math.floor((Date.now() - d.getTime()) / 60000));
  }

  /**
   * Длительность в минутах человеческим текстом. «9532 мин» — не число, а
   * бессмыслица: минуты читаются только внутри часа, дальше нужны часы, а
   * после суток точность уже никому не нужна и не заслуживает доверия.
   *   0…59   → «45 мин»
   *   60…1439→ «2 ч» / «2 ч 15 мин»
   *   ≥ 1440 → «давно»
   * @param {number} min минут (может быть любым, в том числе абсурдным)
   * @returns {string}
   */
  function fmtDuration(min) {
    var m = Math.max(0, Math.round(Number(min) || 0));
    if (m >= 1440) return pick("давно", "a while ago");
    if (m < 60) return m + " " + pick("мин", "min");
    var h = Math.floor(m / 60);
    var rest = m % 60;
    var hours = h + " " + pick("ч", "h");
    return rest ? hours + " " + rest + " " + pick("мин", "min") : hours;
  }

  /* =====================================================================
   *  НАВИГАЦИЯ
   *  App.state.trainerOrigin — страница, с которой вошли в тренера
   *  («workouts»/«diary»); запоминается при первом входе, чтобы «Назад»
   *  с главной тренера вернул туда же.
   * ===================================================================== */

  function isTrainerPage(name) {
    return typeof name === "string" && name.indexOf("trainer") === 0;
  }

  /**
   * Переход на страницу тренера. Если пришли извне раздела — запоминаем
   * origin. Незарегистрированная страница (этап ещё не подключён) — тост.
   * @returns {boolean} состоялся ли переход
   */
  function go(page) {
    if (!App._pages || !App._pages[page]) {
      App.toast(pick("Этот раздел ещё не подключён", "This section is not available yet"));
      return false;
    }
    var cur = App._current;
    if (cur && !isTrainerPage(cur) && cur !== "subscription") {
      App.state.trainerOrigin = cur;
    }
    App.navigate(page);
    return true;
  }

  /**
   * «Назад»: с главной тренера (и первичного онбординга) — выход в origin
   * (по умолчанию «workouts»); с внутренних страниц — на главную тренера.
   * Какая вкладка раздела откроется, решает App.state.trainerSegment: это
   * память последнего выбора, поэтому «Назад» возвращает туда, где человек был.
   */
  function back() {
    var cur = App._current;
    var exit = cur === "trainer" || (cur === "trainer-onboarding" && !App.state.trainerEdit);
    if (exit || !App._pages || !App._pages.trainer) {
      var origin = App.state.trainerOrigin;
      if (!origin || isTrainerPage(origin) || !App._pages[origin]) origin = "today";
      App.navigate(origin);
      return;
    }
    App.navigate("trainer");
  }

  /* =====================================================================
   *  ВКЛАДКИ РАЗДЕЛА
   *  «Сегодня / Программа / Прогресс / Упражнения» — вкладки ОДНОГО экрана
   *  "trainer", а не отдельные страницы: шапка и полоса вкладок стоят на
   *  месте, меняется только содержимое под ними. Выбор хранится в
   *  App.state.trainerSegment (контракт между разделами приложения): кто
   *  хочет открыть конкретную вкладку, ставит флаг и переходит на "trainer".
   * ===================================================================== */

  var SEGMENTS = ["today", "program", "progress", "exercises"];

  /**
   * Приводит ключ вкладки к одному из SEGMENTS. Неизвестное значение —
   * «Сегодня»: флаг приходит из других разделов, и опечатка в нём не должна
   * оставлять человека перед пустым экраном.
   * @param {string} key
   * @returns {string}
   */
  function normSegment(key) {
    return SEGMENTS.indexOf(key) !== -1 ? key : "today";
  }

  /**
   * Открывает вкладку раздела. Уже на экране "trainer" — переключает на месте
   * (без App.navigate: иначе шапка перерисуется и мигнёт), иначе переходит.
   * @param {string} segment ключ из SEGMENTS
   * @returns {boolean} состоялся ли переход
   */
  function openSegment(segment) {
    var seg = normSegment(segment);
    App.state.trainerSegment = seg;
    var shell = window.PageTrainer;
    if (App._current === "trainer" && shell && typeof shell.switchTo === "function") {
      shell.switchTo(seg);
      return true;
    }
    return go("trainer");
  }

  /* =====================================================================
   *  КАРТОЧКА УПРАЖНЕНИЯ И ВОЗВРАТ ИЗ НЕЁ
   *  Карточка — единственный переход ВГЛУБЬ раздела, у неё своя кнопка
   *  «Назад». Возвращать она должна туда, откуда её открыли, причём в то же
   *  состояние: к отфильтрованному списку, к раскрытому дню программы, на ту
   *  же высоту прокрутки. Иначе после каждой карточки человек заново ищет
   *  место, на котором остановился.
   * ===================================================================== */

  var exerciseReturn = null; // {exerciseId, page, state, scrollY}
  var returning = false;     // идёт возврат из карточки (читает оболочка раздела)

  function pageScrollY() {
    var y = window.pageYOffset;
    if (y == null && document.documentElement) y = document.documentElement.scrollTop;
    return Math.max(0, Math.round(Number(y) || 0));
  }

  /**
   * Открывает карточку упражнения и запоминает точку возврата.
   * @param {number} exerciseId
   * @param {object} [ret] {page:"trainer", state:{ключ App.state: значение}} —
   *        какую страницу открыть на «Назад» и какие флаги перед этим выставить
   * @returns {boolean} состоялся ли переход
   */
  function openExercise(exerciseId, ret) {
    var id = parseInt(exerciseId, 10);
    if (isNaN(id)) return false;
    ret = ret || {};
    exerciseReturn = {
      exerciseId: id,
      page: ret.page || "trainer",
      state: ret.state || null,
      scrollY: pageScrollY()
    };
    App.state.trainerExerciseId = id;
    return go("trainer-exercise");
  }

  /**
   * Забирает точку возврата для открытой карточки. Запись одноразовая и
   * привязана к id: если карточку открыли в обход openExercise, чужая
   * запись от прошлого захода не должна увести «Назад» не туда.
   * @returns {object|null}
   */
  function takeExerciseReturn(exerciseId) {
    var r = exerciseReturn;
    exerciseReturn = null;
    return r && r.exerciseId === parseInt(exerciseId, 10) ? r : null;
  }

  /**
   * «Назад» из карточки: на запомненную страницу с восстановлением прокрутки.
   * Без записи — обычный Trainer.back().
   */
  function returnFromExercise(ret) {
    if (!ret || !App._pages || !App._pages[ret.page]) {
      back();
      return;
    }
    if (ret.state) {
      for (var key in ret.state) {
        if (Object.prototype.hasOwnProperty.call(ret.state, key)) App.state[key] = ret.state[key];
      }
    }
    returning = true;
    try {
      App.navigate(ret.page);
    } finally {
      returning = false;
    }
    // App.navigate прокручивает наверх ПОСЛЕ onShow, поэтому высоту
    // возвращаем уже после него. Содержимое из кэша нарисовано синхронно,
    // так что страница уже нужной высоты и прокрутка не упрётся в низ.
    var y = ret.scrollY || 0;
    if (y > 0) window.scrollTo(0, y);
  }

  /**
   * true только во время returnFromExercise: страница, которая сейчас
   * показывается, может взять данные из кэша вместо новой загрузки.
   */
  function isReturning() {
    return returning;
  }

  /**
   * Шапка страницы тренера (переиспользует .sub-head/.sub-back/.sub-title).
   *
   * back:false — шапка КОРНЯ раздела: «Тренировка» теперь вкладка первого
   * уровня, и кнопка «Назад» на её главной вела бы в никуда (ниже таббара
   * уже ничего нет). Отдельные экраны — предпросмотр новой программы,
   * карточка упражнения, выбор упражнения в тренировку, сессия — кнопку
   * сохраняют: туда приходят именно из раздела и туда же возвращаются.
   *
   * @param {object} opts {title, subtitle, icon, backLabel, back, actions}
   *        icon    — ИМЯ иконки из js/icons.js (не эмодзи и не разметка);
   *        actions — готовая разметка кнопок в правом углу заголовка.
   */
  function headHtml(opts) {
    opts = opts || {};
    var root = opts.back === false;
    var backLabel = opts.backLabel || pick("Назад", "Back");
    var back = root
      ? ""
      : '<button type="button" class="sub-back" data-tr-back aria-label="' + esc(backLabel) + '">' +
        icon("arrow", { size: 18, rotate: 180, cls: "sub-back__arrow" }) +
        "<span>" + esc(backLabel) + "</span>" +
        "</button>";
    return (
      '<header class="sub-head tr-head' + (root ? " tr-head--root" : "") + '">' +
      back +
      '<div class="tr-head__row">' +
      '<h1 class="page-title sub-title">' +
      (opts.icon ? icon(opts.icon, { size: 22, cls: "tr-head__icon" }) : "") +
      "<span>" + esc(opts.title || pick("Тренер", "Coach")) + "</span>" +
      "</h1>" +
      (opts.actions || "") +
      "</div>" +
      // Подзаголовок рисуем всегда (пусть и пустым): страницы обновляют его
      // текстом после загрузки данных без перерисовки шапки.
      '<p class="page-subtitle sub-subtitle">' + esc(opts.subtitle || "") + "</p>" +
      "</header>"
    );
  }

  /**
   * Вешает обработчик на кнопку «Назад» ([data-tr-back]) внутри контейнера.
   * Кнопки может не быть (шапка корня раздела) — тогда просто выходим.
   * @param {HTMLElement} viewEl
   * @param {Function} [fn] своя реакция; по умолчанию Trainer.back()
   */
  function bindBack(viewEl, fn) {
    if (!viewEl) return;
    var btn = viewEl.querySelector("[data-tr-back]");
    if (!btn) return;
    btn.addEventListener("click", function () {
      App.haptic("light");
      if (typeof fn === "function") fn();
      else back();
    });
  }

  /* =====================================================================
   *  НИЖНИЙ ЛИСТ (копия паттерна .diary-sheet с классами .tr-sheet*)
   * ===================================================================== */

  var SHEET_ID = "tr-sheet";

  /**
   * Закрывает открытый лист. immediate=true — без анимации.
   */
  function closeSheet(immediate) {
    var el = document.getElementById(SHEET_ID);
    if (!el) return;
    if (immediate) {
      if (el.parentNode) el.parentNode.removeChild(el);
      return;
    }
    el.classList.remove("tr-sheet--open");
    setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 220);
  }

  /**
   * Открывает нижний лист.
   * @param {Array|string} items пункты [{key, icon, label, desc, danger, disabled}],
   *        где icon — ИМЯ иконки из js/icons.js,
   *        или готовая HTML-строка (тогда события вешает вызывающий на el)
   * @param {Function} [onPick] onPick(key, item) — после закрытия листа
   * @param {object} [opts] {title, cancel:false, onClose}
   * @returns {{el: HTMLElement, close: Function}}
   */
  function sheet(items, onPick, opts) {
    opts = opts || {};
    closeSheet(true);

    var body = "";
    if (typeof items === "string") {
      body = items;
    } else if (items && items.length) {
      for (var i = 0; i < items.length; i++) {
        var it = items[i] || {};
        body +=
          '<button type="button" class="tr-sheet__item' +
          (it.danger ? " tr-sheet__item--danger" : "") +
          (it.disabled ? " is-disabled" : "") +
          '" data-key="' + esc(it.key) + '"' + (it.disabled ? " disabled" : "") + ">" +
          '<span class="tr-sheet__icon" aria-hidden="true">' + icon(it.icon || "dot", { size: 20 }) + "</span>" +
          '<span class="tr-sheet__body">' +
          '<span class="tr-sheet__label">' + esc(it.label) + "</span>" +
          (it.desc ? '<span class="tr-sheet__desc">' + esc(it.desc) + "</span>" : "") +
          "</span>" +
          "</button>";
      }
    }

    var el = document.createElement("div");
    el.id = SHEET_ID;
    el.className = "tr-sheet";
    el.innerHTML =
      '<div class="tr-sheet__backdrop"></div>' +
      '<div class="tr-sheet__panel">' +
      '<div class="tr-sheet__handle"></div>' +
      (opts.title ? '<div class="tr-sheet__title">' + esc(opts.title) + "</div>" : "") +
      body +
      (opts.cancel === false
        ? ""
        : '<button type="button" class="btn btn-ghost btn-block tr-sheet__cancel">' +
          esc(pick("Отмена", "Cancel")) +
          "</button>") +
      "</div>";
    document.body.appendChild(el);
    requestAnimationFrame(function () {
      el.classList.add("tr-sheet--open");
    });

    function close() {
      closeSheet();
      if (typeof opts.onClose === "function") opts.onClose();
    }

    var backdrop = el.querySelector(".tr-sheet__backdrop");
    if (backdrop) backdrop.addEventListener("click", close);
    var cancel = el.querySelector(".tr-sheet__cancel");
    if (cancel) cancel.addEventListener("click", close);

    if (typeof items !== "string") {
      el.addEventListener("click", function (ev) {
        var btn = ev.target.closest(".tr-sheet__item");
        if (!btn || btn.disabled) return;
        var key = btn.getAttribute("data-key");
        var picked = null;
        for (var j = 0; j < items.length; j++) {
          if (String(items[j].key) === key) {
            picked = items[j];
            break;
          }
        }
        App.haptic("light");
        closeSheet();
        if (typeof onPick === "function") onPick(key, picked);
      });
    }

    return { el: el, close: close };
  }

  /* =====================================================================
   *  ТАЙМЕР ОТДЫХА
   *  Считает по абсолютному времени Date.now(): при сворачивании WebView
   *  интервалы «замирают», но остаток пересчитывается по visibilitychange.
   * ===================================================================== */

  /**
   * @param {object} [opts] {onTick(remainingSec, totalSec), onDone(), onSkip()}
   */
  function RestTimer(opts) {
    opts = opts || {};
    this.onTick = typeof opts.onTick === "function" ? opts.onTick : null;
    this.onDone = typeof opts.onDone === "function" ? opts.onDone : null;
    this.onSkip = typeof opts.onSkip === "function" ? opts.onSkip : null;
    this._endAt = 0;
    this._total = 0;
    this._timer = null;
    this._running = false;
    var self = this;
    this._onVis = function () {
      if (self._running) self._tick();
    };
  }

  /** Запуск на sec секунд (перезапускает, если уже шёл). */
  RestTimer.prototype.start = function (sec) {
    var s = Math.max(0, Math.round(Number(sec) || 0));
    this.stop();
    this._total = s;
    this._endAt = Date.now() + s * 1000;
    this._running = true;
    var self = this;
    document.addEventListener("visibilitychange", this._onVis);
    this._timer = setInterval(function () {
      self._tick();
    }, 250);
    this._tick();
  };

  /** Остаток в секундах (0, если не идёт). */
  RestTimer.prototype.remaining = function () {
    if (!this._running) return 0;
    return Math.max(0, Math.ceil((this._endAt - Date.now()) / 1000));
  };

  /** Общая длительность текущего отдыха (с учётом ±15). */
  RestTimer.prototype.total = function () {
    return this._total;
  };

  /** Сдвиг на ±delta секунд (например, −15/+15). */
  RestTimer.prototype.adjust = function (delta) {
    if (!this._running) return;
    var d = Math.round(Number(delta) || 0);
    this._endAt += d * 1000;
    this._total = Math.max(0, this._total + d);
    this._tick();
  };

  /** Пропустить отдых: останавливает без onDone, вызывает onSkip. */
  RestTimer.prototype.skip = function () {
    if (!this._running) return;
    this.stop();
    if (this.onSkip) this.onSkip();
  };

  /** Остановка без колбэков. */
  RestTimer.prototype.stop = function () {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
    document.removeEventListener("visibilitychange", this._onVis);
    this._running = false;
  };

  RestTimer.prototype.isRunning = function () {
    return this._running;
  };

  RestTimer.prototype._tick = function () {
    if (!this._running) return;
    var rem = this.remaining();
    if (this.onTick) {
      try {
        this.onTick(rem, this._total);
      } catch (e) {
        console.error("RestTimer.onTick", e);
      }
    }
    if (rem <= 0) {
      this.stop();
      if (this.onDone) {
        try {
          this.onDone();
        } catch (e2) {
          console.error("RestTimer.onDone", e2);
        }
      }
    }
  };

  /* =====================================================================
   *  ЗВУК (WebAudio). Контекст создаётся/разблокируется первым тапом:
   *  без пользовательского жеста мобильные вебвью не дают воспроизводить звук.
   * ===================================================================== */

  var audioCtx = null;

  function unlockAudio() {
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!audioCtx) audioCtx = new AC();
      if (audioCtx.state === "suspended" && typeof audioCtx.resume === "function") {
        audioCtx.resume();
      }
    } catch (e) {
      /* звук не критичен */
    }
  }

  function unlockOnce() {
    unlockAudio();
    document.removeEventListener("touchstart", unlockOnce);
    document.removeEventListener("click", unlockOnce);
  }
  document.addEventListener("touchstart", unlockOnce, { passive: true });
  document.addEventListener("click", unlockOnce);

  /**
   * Короткий двойной сигнал (880 Гц). Тихо игнорирует отсутствие WebAudio.
   */
  function beep() {
    unlockAudio();
    if (!audioCtx) return;
    try {
      var t0 = audioCtx.currentTime;
      for (var i = 0; i < 2; i++) {
        var osc = audioCtx.createOscillator();
        var gain = audioCtx.createGain();
        osc.type = "sine";
        osc.frequency.value = 880;
        var start = t0 + i * 0.22;
        gain.gain.setValueAtTime(0.0001, start);
        gain.gain.exponentialRampToValueAtTime(0.35, start + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.16);
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.start(start);
        osc.stop(start + 0.18);
      }
    } catch (e) {
      /* звук не критичен */
    }
  }

  /* =====================================================================
   *  ГРАФИК (SVG-строка). Цвета — через CSS-токены в style-атрибутах.
   * ===================================================================== */

  function r1(x) {
    return Math.round(x * 10) / 10;
  }

  /**
   * Линейный график по точкам.
   * @param {Array} points [{date, est_1rm, max_weight, volume, …}]
   * @param {object} [opts] {w:320, h:140, key:"est_1rm", unit:""}
   * @returns {string} SVG-разметка (class="tr-chart__svg")
   */
  function lineChart(points, opts) {
    opts = opts || {};
    var w = Number(opts.w) || 320;
    var h = Number(opts.h) || 140;
    var key = opts.key || "est_1rm";
    var padL = 38, padR = 12, padT = 12, padB = 24;
    var pts = [];
    if (points && points.length) {
      for (var i = 0; i < points.length; i++) {
        var p = points[i];
        if (!p || p[key] === null || p[key] === undefined) continue;
        var v = Number(p[key]);
        if (!isFinite(v)) continue;
        pts.push({ date: p.date, v: v });
      }
    }
    var open =
      '<svg class="tr-chart__svg" viewBox="0 0 ' + w + " " + h + '" width="100%" ' +
      'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="' + esc(pick("График", "Chart")) + '">';
    if (!pts.length) {
      return (
        open +
        '<text class="tr-chart__empty" x="' + w / 2 + '" y="' + h / 2 + '" text-anchor="middle" ' +
        'style="fill:var(--muted);font-size:13px">' +
        esc(pick("Пока нет данных", "No data yet")) +
        "</text></svg>"
      );
    }
    var min = pts[0].v, max = pts[0].v;
    for (var j = 1; j < pts.length; j++) {
      if (pts[j].v < min) min = pts[j].v;
      if (pts[j].v > max) max = pts[j].v;
    }
    var lo = min, hi = max;
    if (hi - lo < 1e-9) {
      lo = min - 1;
      hi = max + 1;
    } else {
      var pad = (hi - lo) * 0.12;
      lo -= pad;
      hi += pad;
    }
    var innerW = w - padL - padR;
    var innerH = h - padT - padB;
    function x(i) {
      if (pts.length === 1) return padL + innerW / 2;
      return padL + (innerW * i) / (pts.length - 1);
    }
    function y(v) {
      return padT + innerH * (1 - (v - lo) / (hi - lo));
    }
    var d = "";
    var dots = "";
    for (var k = 0; k < pts.length; k++) {
      var px = r1(x(k)), py = r1(y(pts[k].v));
      d += (k === 0 ? "M" : "L") + px + " " + py + " ";
      dots +=
        '<circle class="tr-chart__dot' + (k === pts.length - 1 ? " tr-chart__dot--last" : "") +
        '" cx="' + px + '" cy="' + py + '" r="' + (k === pts.length - 1 ? 4.5 : 3) +
        '" data-i="' + k + '" data-date="' + esc(pts[k].date || "") + '" data-value="' + pts[k].v +
        '" style="fill:var(--accent);stroke:var(--surface);stroke-width:1.5"/>';
    }
    var axisY = r1(padT + innerH);
    var unit = opts.unit ? " " + opts.unit : "";
    var svg =
      open +
      // Оси — волосяные линии --border. Раньше здесь стоял --accent: в новой
      // палитре это цвет действия (зелёный), и сетка графика кричала громче
      // самой линии данных.
      '<line class="tr-chart__axis" x1="' + padL + '" y1="' + axisY + '" x2="' + (w - padR) + '" y2="' + axisY +
      '" style="stroke:var(--border);stroke-width:1"/>' +
      '<line class="tr-chart__axis" x1="' + padL + '" y1="' + padT + '" x2="' + (w - padR) + '" y2="' + padT +
      '" style="stroke:var(--border);stroke-width:1;stroke-dasharray:3 3"/>' +
      '<text class="tr-chart__label" x="' + (padL - 6) + '" y="' + (padT + 4) + '" text-anchor="end" ' +
      'style="fill:var(--muted);font-size:10px">' + esc(fmtNum(max) + unit) + "</text>" +
      '<text class="tr-chart__label" x="' + (padL - 6) + '" y="' + (axisY + 3) + '" text-anchor="end" ' +
      'style="fill:var(--muted);font-size:10px">' + esc(fmtNum(min) + unit) + "</text>" +
      '<path class="tr-chart__line" d="' + d.trim() + '" style="fill:none;stroke:var(--accent);stroke-width:2;' +
      'stroke-linejoin:round;stroke-linecap:round"/>' +
      dots;
    if (pts[0].date) {
      svg +=
        '<text class="tr-chart__label" x="' + padL + '" y="' + (h - 6) + '" text-anchor="start" ' +
        'style="fill:var(--muted);font-size:10px">' + esc(shortDate(pts[0].date, false)) + "</text>";
    }
    if (pts.length > 1 && pts[pts.length - 1].date) {
      svg +=
        '<text class="tr-chart__label" x="' + (w - padR) + '" y="' + (h - 6) + '" text-anchor="end" ' +
        'style="fill:var(--muted);font-size:10px">' + esc(shortDate(pts[pts.length - 1].date, false)) + "</text>";
    }
    return svg + "</svg>";
  }

  /* =====================================================================
   *  СКЕЛЕТОН, ОШИБКА, ОЖИДАНИЕ ГЕНЕРАЦИИ
   * ===================================================================== */

  /**
   * Карточка-скелетон из n строк (переиспользует .skeleton/.skeleton-line).
   */
  function skeleton(n) {
    var count = Math.max(1, Number(n) || 3);
    var rows = "";
    for (var i = 0; i < count; i++) {
      rows +=
        '<div class="skeleton skeleton-line' +
        (i === 0 ? " skeleton-title" : i % 2 === 0 ? " short" : "") +
        '"></div>';
    }
    return '<div class="card tr-skeleton">' + rows + "</div>";
  }

  /**
   * Карточка ошибки (.wk-error) с кнопкой «Повторить» (id=retryId, если задан).
   */
  function errorCard(msg, retryId) {
    return (
      '<div class="card wk-error tr-error">' +
      '<div class="wk-error__icon" aria-hidden="true">' + icon("warning", { size: 32 }) + "</div>" +
      '<p class="wk-error__title">' + esc(pick("Что-то пошло не так", "Something went wrong")) + "</p>" +
      '<p class="wk-error__text">' + esc(msg || pick("Неизвестная ошибка", "Unknown error")) + "</p>" +
      (retryId
        ? '<button type="button" class="btn btn-ghost wk-error__retry" id="' + esc(retryId) + '">' +
          esc(pick("Повторить", "Retry")) +
          "</button>"
        : "") +
      "</div>"
    );
  }

  /**
   * Человеческое сообщение об ошибке API по статусу.
   */
  function errMessage(err, fallback) {
    var fb = fallback || pick("Не удалось выполнить запрос", "Request failed");
    if (!err) return fb;
    if (err.status === 429) {
      return pick("Слишком много запросов. Попробуйте через минуту.", "Too many requests. Try again in a minute.");
    }
    if (err.status === 402) return pick("Нужна подписка", "Subscription required");
    return err.message || fb;
  }

  /**
   * Подтверждение: нативный Telegram showConfirm либо window.confirm.
   * @returns {Promise<boolean>}
   */
  function confirm(msg) {
    return new Promise(function (resolve) {
      // showConfirm появился в Bot API 6.2; в старом клиенте (и в обычном
      // браузере, где версия «6.0») SDK лишь пишет ошибку в консоль и не зовёт
      // колбэк — тогда промис завис бы навсегда. Поэтому проверяем версию.
      var tg = App.tg;
      var supported =
        tg && typeof tg.showConfirm === "function" &&
        typeof tg.isVersionAtLeast === "function" && tg.isVersionAtLeast("6.2");
      if (supported) {
        try {
          tg.showConfirm(msg, function (ok) {
            resolve(!!ok);
          });
          return;
        } catch (e) {
          /* метод не поддерживается — фолбэк ниже */
        }
      }
      resolve(window.confirm(msg));
    });
  }

  // Строки экрана ожидания генерации (сменяются по кругу).
  function genWaitLines() {
    return [
      pick("Подбираем упражнения…", "Picking exercises…"),
      pick("Расставляем нагрузку…", "Balancing the load…"),
      pick("Проверяем баланс мышц…", "Checking muscle balance…")
    ];
  }

  /**
   * Разметка экрана ожидания генерации (.tr-gen-wait).
   */
  function genWaitHtml() {
    var lines = genWaitLines();
    return (
      '<div class="card tr-gen-wait" role="status" aria-live="polite">' +
      '<div class="tr-gen-wait__spinner" aria-hidden="true"></div>' +
      '<div class="tr-gen-wait__title">' + esc(pick("Собираем программу", "Building your program")) + "</div>" +
      '<div class="tr-gen-wait__line" data-gen-line>' + esc(lines[0]) + "</div>" +
      '<div class="tr-gen-wait__hint">' + esc(pick("Обычно 15–40 секунд", "Usually takes 15–40 seconds")) + "</div>" +
      "</div>"
    );
  }

  /**
   * Показывает экран ожидания в hostEl и запускает генерацию программы.
   * Успех/ошибку обрабатывает вызывающий (промис отдаёт TrainerProgramOut).
   * @param {HTMLElement} hostEl контейнер, куда рисуем ожидание
   * @param {object} [payload] {regenerate_note}
   * @returns {Promise<object>}
   */
  function runGenerate(hostEl, payload) {
    if (hostEl) hostEl.innerHTML = genWaitHtml();
    var lines = genWaitLines();
    var idx = 0;
    var timer = setInterval(function () {
      idx = (idx + 1) % lines.length;
      var el = hostEl && hostEl.querySelector("[data-gen-line]");
      if (el) el.textContent = lines[idx];
    }, 4000);
    App.scrollTop();
    return App.api
      .trainerGenerateProgram(payload || {})
      .then(function (program) {
        cache.invalidate();
        return program;
      })
      .finally(function () {
        clearInterval(timer);
      });
  }

  /* =====================================================================
   *  КЭШ (сбрасывается при действиях, меняющих план/сессию)
   * ===================================================================== */

  var cache = {
    overview: null,       // последний TrainerOverviewOut
    activeSession: null,  // последняя активная TrainerSessionOut
    // Версия данных тренера: растёт при каждом сбросе. Вкладки раздела
    // запоминают версию, с которой загрузились, и по расхождению понимают,
    // что их содержимое устарело (например, разбор недели применён на
    // «Прогрессе», а «Программа» ещё показывает старый план).
    version: 0,
    /**
     * Сброс: без аргумента — всё, с ключом — только его.
     */
    invalidate: function (key) {
      cache.version++;
      if (!key) {
        cache.overview = null;
        cache.activeSession = null;
        return;
      }
      if (key !== "version" && key !== "invalidate" && cache.hasOwnProperty(key)) cache[key] = null;
    }
  };

  /* =====================================================================
   *  ОБЩИЕ СЦЕНАРИИ СТРАНИЦ (контракт между page-trainer*.js)
   *
   *  Ключи App.state, которыми страницы тренера обмениваются:
   *    trainerOrigin       — откуда вошли в раздел («workouts»/«diary»);
   *    trainerSegment      — выбранная вкладка раздела: today|program|progress|
   *                          exercises (память выбора между заходами);
   *    trainerEdit         — онбординг в режиме редактирования настроек;
   *    trainerBrief        — сводка для карточки-входа на «Тренировках»
   *                          ({has_profile, kind, title, duration_min, in_progress});
   *    trainerProgram      — TrainerProgramOut для page-trainer-program;
   *    trainerProgramMode  — «preview» сразу после генерации, иначе null;
   *    trainerSessionId    — id активной сессии для page-trainer-session;
   *    trainerProgressSection — «review» — открыть прогресс на разборе недели;
   *    trainerExerciseId   — упражнение для детального листа (ТЗ §6.1).
   * ===================================================================== */

  /**
   * Есть ли платный доступ. Обёртка нужна, чтобы страницы тренера не лезли
   * в App напрямую и одинаково вели себя при отсутствии ядра подписки.
   */
  function isPro() {
    return !!(window.App && typeof App.isPremium === "function" && App.isPremium());
  }

  /**
   * Параметры paywall для страниц тренера (единый текст на всех экранах).
   * Текст рассказывает именно про тренера: раньше здесь стояли общие слова
   * про подписку, и человек, упёршийся в стену на экране тренировки, читал
   * про журнал калорий — то есть про другую функцию.
   */
  function paywallOpts() {
    return {
      icon: "coach",
      title: pick("AI-тренер", "AI Coach"),
      desc: pick(
        "Программа под вас, ведение тренировки подход за подходом и разбор недели",
        "A program built for you, set-by-set guidance during the workout and a weekly review"
      ),
      bullets: [
        pick(
          "Программа под цель, уровень, оборудование и травмы",
          "A program for your goal, level, equipment and injuries"
        ),
        pick(
          "Ведение тренировки: таблица подходов, таймер отдыха, рекорды",
          "Guided workout: set table, rest timer, personal records"
        ),
        pick(
          "Разбор недели: что добавить, что снять, когда разгрузка",
          "Weekly review: what to add, what to cut, when to deload"
        )
      ]
    };
  }

  /**
   * Paywall раздела тренера. Свой, а не App.paywall, по двум причинам:
   * App.paywall печатает иконку как ТЕКСТ (эмодзи), а здесь нужен App.icon;
   * и нужна вторая, бесплатная кнопка — анкету человек заполняет до оплаты,
   * иначе он платит, не увидев ни одного экрана продукта.
   *
   * @param {HTMLElement} viewEl контейнер страницы
   * @param {object} [opts] paywallOpts() + {extraLabel, onExtra}
   */
  function paywall(viewEl, opts) {
    if (!viewEl) return;
    opts = opts || paywallOpts();
    var bullets = "";
    var list = opts.bullets || [];
    for (var i = 0; i < list.length; i++) {
      bullets +=
        '<li class="tr-paywall__bullet">' +
        icon("check", { size: 18, cls: "tr-paywall__tick" }) +
        "<span>" + esc(list[i]) + "</span>" +
        "</li>";
    }
    // Тёмный блок с фото подписки — как экран подписки и тренировка дня:
    // paywall продаёт спорт, а не показывает замок в кружке.
    viewEl.innerHTML =
      '<section class="page tr-page tr-paywall">' +
      '<div class="hero hero--img tr-paywall__hero" style="' + heroImg("hero-premium.jpg") + '">' +
      '<span class="eyebrow">' + esc(pick("По подписке", "Subscription")) + "</span>" +
      '<h1 class="hero__title">' + esc(opts.title || pick("AI-тренер", "AI Coach")) + "</h1>" +
      '<p class="hero__meta">' + esc(opts.desc || "") + "</p>" +
      "</div>" +
      (bullets ? '<ul class="card tr-paywall__bullets">' + bullets + "</ul>" : "") +
      '<p class="tr-paywall__note">' +
      icon("lock", { size: 16 }) +
      "<span>" + esc(pick("Доступно по подписке", "Included in the subscription")) + "</span>" +
      "</p>" +
      '<button type="button" class="btn btn-cta btn-block" id="trPaywallCta">' +
      esc(pick("Оформить подписку", "Get subscription")) +
      "</button>" +
      (opts.extraLabel
        ? '<button type="button" class="btn btn-ghost btn-block" id="trPaywallExtra">' +
          esc(opts.extraLabel) +
          "</button>"
        : "") +
      "</section>";

    var cta = viewEl.querySelector("#trPaywallCta");
    if (cta) {
      cta.addEventListener("click", function () {
        App.haptic("light");
        App.goSubscription();
      });
    }
    var extra = viewEl.querySelector("#trPaywallExtra");
    if (extra && typeof opts.onExtra === "function") {
      extra.addEventListener("click", function () {
        App.haptic("light");
        opts.onExtra();
      });
    }
  }

  /**
   * Открывает программу. mode="preview" — отдельная страница сразу после
   * генерации (ТЗ §2.3) с «Начать программу» / «Пересобрать»; иначе — вкладка
   * «Программа» раздела. Программа кладётся в App.state.trainerProgram,
   * режим — в App.state.trainerProgramMode. Если страница ещё не подключена —
   * возвращаемся на главную тренера.
   */
  function openProgram(program, mode) {
    if (mode !== "preview") {
      App.state.trainerProgram = null;
      App.state.trainerProgramMode = null;
      openSegment("program");
      return;
    }
    App.state.trainerProgram = program || null;
    App.state.trainerProgramMode = mode;
    if (!go("trainer-program")) {
      if (App._pages && App._pages.trainer) App.navigate("trainer");
    }
  }

  /**
   * Старт сессии по дню плана (programDayId=null — свободная тренировка).
   * 409 «уже есть активная сессия» → подхватываем активную вместо ошибки.
   * @returns {Promise<object>} TrainerSessionOut
   */
  function startSession(programDayId) {
    return App.api
      .trainerStartSession({ program_day_id: programDayId || null, date: App.todayStr() })
      .catch(function (err) {
        if (err && err.status === 409) {
          return App.api.trainerActiveSession().then(function (s) {
            if (!s) throw err;
            return s;
          });
        }
        throw err;
      })
      .then(function (session) {
        cache.invalidate();
        cache.activeSession = session || null;
        App.state.trainerSessionId = session ? session.id : null;
        return session;
      });
  }

  /**
   * Переход в экран выполнения тренировки (после старта или «Продолжить»).
   * @param {object|null} session TrainerSessionOut (если уже есть на руках)
   * @returns {boolean} состоялся ли переход
   */
  function openSession(session) {
    if (session) {
      cache.activeSession = session;
      App.state.trainerSessionId = session.id;
    }
    return go("trainer-session");
  }

  /* =====================================================================
   *  ПУБЛИКАЦИЯ
   * ===================================================================== */

  window.Trainer = {
    L: L,
    label: label,
    labels: labels,
    pick: pick,
    esc: esc,
    icon: icon,
    exName: exName,
    fmtNum: fmtNum,
    fmtKg: fmtKg,
    fmtSet: fmtSet,
    fmtTarget: fmtTarget,
    plural: plural,
    fmtClock: fmtClock,
    fmtDuration: fmtDuration,
    humanDate: humanDate,
    shortDate: shortDate,
    shiftDate: shiftDate,
    weekdayOf: weekdayOf,
    parseServerDate: parseServerDate,
    elapsedMin: elapsedMin,
    isTrainerPage: isTrainerPage,
    go: go,
    back: back,
    SEGMENTS: SEGMENTS,
    normSegment: normSegment,
    openSegment: openSegment,
    openExercise: openExercise,
    takeExerciseReturn: takeExerciseReturn,
    returnFromExercise: returnFromExercise,
    isReturning: isReturning,
    headHtml: headHtml,
    heroImg: heroImg,
    bindBack: bindBack,
    sheet: sheet,
    closeSheet: closeSheet,
    RestTimer: RestTimer,
    beep: beep,
    lineChart: lineChart,
    skeleton: skeleton,
    errorCard: errorCard,
    errMessage: errMessage,
    confirm: confirm,
    genWaitHtml: genWaitHtml,
    runGenerate: runGenerate,
    paywallOpts: paywallOpts,
    paywall: paywall,
    isPro: isPro,
    openProgram: openProgram,
    startSession: startSession,
    openSession: openSession,
    cache: cache
  };
})();
