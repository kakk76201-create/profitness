/*
 * page-scan.js — страница «Определение» (камера и распознавание еды)
 * Контроллер window.PageScan, регистрируется через App.registerPage("scan", {...}).
 *
 * Назначение страницы:
 *   1. Дать пользователю сфотографировать / загрузить фото блюда.
 *   2. Показать превью выбранного фото.
 *   3. Отправить фото на бэкенд (App.api.analyzeFood) с индикатором загрузки.
 *   4. Показать карточку результата с РЕДАКТИРУЕМЫМИ значениями: название,
 *      вес порции (граммы), калории, Б/Ж/У, бейдж уверенности, комментарий.
 *      При изменении веса порции калории и БЖУ пересчитываются пропорционально.
 *   5. Дать выбрать приём пищи (Завтрак/Обед/Ужин/Перекус) и добавить запись
 *      в рацион за сегодня (App.api.addDiary) с отредактированными значениями.
 *   6. Корректно обрабатывать ошибки (сеть/AI/неверный файл) с кнопкой повтора.
 *
 * Подписка (Этап 1): сканирование РАБОТАЕТ для бесплатных пользователей, но с
 *   дневным лимитом. На экране загрузки показываем счётчик «Осталось N из 3
 *   бесплатных сканирований» (для premium — «Безлимит»/скрыт). Если бэкенд
 *   отвечает 402 про исчерпанный лимит — вместо экрана ошибки показываем пейволл
 *   над сохранённым кадром (контроль доступа серверный, фронт лишь показывает).
 *
 * Локализация (RU/EN): весь видимый пользователю текст оборачивается в
 *   App.pick("рус","eng") В МОМЕНТ РЕНДЕРА, чтобы смена языка давала нужный
 *   текст после перерисовки. Идентификаторы/ключи/console — НЕ переводятся.
 *
 * ЭКРАН-ЗАДАЧА. Камера — не вкладка: таббар на ней скрыт (app.js,
 *   TASK_PAGES), а открывают её «Питание» и «Сегодня». Уйти можно только
 *   кнопкой «Закрыть» в шапке, и она есть в КАЖДОМ состоянии экрана.
 *   Открывающий экран кладёт свой id в App.state.scanOrigin — туда «Закрыть»
 *   и возвращает (по умолчанию в «Питание»).
 *
 * ТРИ ПРАВИЛА, КОТОРЫЕ ЗДЕСЬ НЕЛЬЗЯ НАРУШАТЬ (каждое чинит реальную потерю
 * пользовательских данных, см. историю правок):
 *   1. СПУСК ЗАТВОРА — ОДНА кнопка на самом экране (#scan-shutter). Раньше
 *      вторым затвором служила кнопка камеры в таббаре, и её повторный тап
 *      звал reset(), стирая кадр вместе со всеми правками полей. Кнопки в
 *      таббаре больше нет; сбрасывать снимок имеют право только явные
 *      «Снять заново» / «Отмена» на самом экране.
 *   2. ИСЧЕРПАННЫЙ ЛИМИТ (402) НЕ СТИРАЕТ КАДР. renderScanLimit сохраняет
 *      файл и превью (pending), а пейволл рисует поверх снимка. Кадр переживает
 *      уход на страницу подписки и возврат — после оплаты анализ продолжается
 *      с тем же фото.
 *   3. ЗАПИСЬ УХОДИТ В ВЫБРАННЫЙ ДЕНЬ. Перед переходом в дневник выставляем
 *      App.state.diaryReturnDate = дата записи: дневник откроется на том дне,
 *      куда реально легла еда, а не на «сегодня».
 */
(function () {
  "use strict";

  // Безопасный выбор языка: если App.pick недоступен — отдаём русский вариант.
  // ВАЖНО: вызывать НА МОМЕНТ РЕНДЕРА (внутри render/обработчиков), а не один раз
  // на уровне модуля, чтобы смена языка отражалась после перерисовки.
  function L(ru, en) {
    if (App && typeof App.pick === "function") return App.pick(ru, en);
    return ru;
  }

  // Иконка из общего набора (js/icons.js) — ВОЗВРАЩАЕТ СТРОКУ '<svg …>'.
  // Иконки собираются отдельно от подписей: раньше они были зашиты внутрь
  // App.pick("<эмодзи> Оценить", ...) и попадали в переводимый текст.
  function icon(name, opts) {
    if (App && typeof App.icon === "function") return App.icon(name, opts);
    return "";
  }

  // ===== Внутреннее состояние контроллера =====
  // Хранит выбранный файл, результат анализа и выбранный приём пищи.
  // Сбрасывается при reset() и при каждом новом show страницы.
  var state = {
    file: null,            // выбранный File (фото)
    previewUrl: null,      // objectURL для превью (нужно освобождать)
    result: null,          // результат анализа { dish_name, calories, proteins, fats, carbs, note, weight_grams, confidence, debug }
    base: null,            // исходные («сырые») значения анализа для пропорционального пересчёта
    edited: null,          // текущие отредактированные значения формы { dish_name, weight, calories, proteins, fats, carbs }
    mealType: "breakfast", // выбранный приём пищи по умолчанию
    scans: null,           // последний ответ getScansRemaining { used, limit, remaining, is_premium } или null
  };

  // ===== Внутреннее состояние ГОЛОСОВОГО ввода (Этап 2) =====
  // Отдельно от фото-потока. Хранит активную запись (MediaRecorder/stream/чанки)
  // и результат распознавания со списком РЕДАКТИРУЕМЫХ блюд.
  // Сбрасывается через voiceReset() и при каждом show страницы.
  var voice = {
    recording: false,      // идёт ли сейчас запись
    recorder: null,        // экземпляр MediaRecorder
    stream: null,          // активный MediaStream (треки нужно останавливать)
    chunks: [],            // собранные аудио-чанки (ondataavailable)
    timer: null,           // setInterval таймера записи
    seconds: 0,            // длительность текущей записи (сек)
    result: null,          // { transcript, meal_type } последнего распознавания
    items: null,           // массив редактируемых блюд [{dish_name,calories,proteins,fats,carbs}]
    mealType: "breakfast", // выбранный приём пищи для голосового результата
  };

  // ===== Внутреннее состояние ЖИВОЙ КАМЕРЫ главного экрана =====
  // Главный экран «Определение» сразу показывает живой видеопоток камеры
  // (getUserMedia, facingMode "environment"). Спуск затвора — круглая кнопка
  // под видоискателем (#scan-shutter).
  // ВАЖНО (утечки): треки потока обязательно останавливаются во ВСЕХ ветках ухода
  // с главного экрана (превью/результат/ошибка/голос/анализ), при onHide и перед
  // повторным открытием камеры (не плодим потоки).
  var cam = {
    active: false,        // показан ли сейчас живой экран камеры (renderCamera)
    stream: null,         // активный MediaStream видео (треки нужно останавливать)
    video: null,          // ссылка на <video> элемент текущего экрана
    starting: false,      // идёт ли сейчас запрос getUserMedia (защита от гонок)
    token: 0,             // монотонный токен запроса камеры (отбрасываем устаревшие)
    live: false,          // подключён ли поток к <video> (готов ли спуск затвора)
  };

  // ===== ОТЛОЖЕННЫЙ КАДР (снимок, упёршийся в лимит 402) =====
  // Человек снял блюдо, бэкенд ответил «лимит исчерпан». Кадр НЕ выбрасываем:
  // он ждёт здесь, пока пользователь ходит на страницу подписки и обратно.
  // active=true заставляет onShow/onHide обходить обычную зачистку состояния,
  // иначе возвращаться после оплаты было бы не к чему.
  var pending = {
    active: false,  // есть ли отложенный кадр, ждущий подписки
    date: null,     // целевая дата записи на момент съёмки (App.state.scanDate)
  };

  // Сбрасывает отложенный кадр (кадр отснят заново / добавлен / отменён).
  function pendingClear() {
    pending.active = false;
    pending.date = null;
  }

  // Ключ localStorage: пользователь хотя бы раз успешно включил камеру.
  // Пока флаг НЕ выставлен — первый визит НЕ дёргает getUserMedia автоматически,
  // а показывает нейтральную заглушку с кнопкой «Включить камеру» (task 1).
  var CAM_OK_KEY = "scan_cam_ok";

  // Считывает флаг «камера уже разрешалась» (best-effort — localStorage может
  // быть недоступен в приватном режиме / вебвью).
  function camPreviouslyGranted() {
    try {
      return window.localStorage && localStorage.getItem(CAM_OK_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  // Помечает камеру как успешно включённую (после первого удачного getUserMedia),
  // чтобы при следующих визитах открывать поток сразу.
  function markCamGranted() {
    try {
      if (window.localStorage) localStorage.setItem(CAM_OK_KEY, "1");
    } catch (e) {
      /* игнорируем — недоступность localStorage не критична */
    }
  }

  // Поддерживается ли получение видеопотока камеры в этом окружении.
  function cameraSupported() {
    return !!(
      navigator &&
      navigator.mediaDevices &&
      typeof navigator.mediaDevices.getUserMedia === "function"
    );
  }

  // Останавливает все треки активного видеопотока камеры и сбрасывает ссылки.
  // Идемпотентна: безопасно вызывать в любой ветке (уход с экрана/onHide/перезапуск).
  function camStopStream() {
    // Инвалидируем текущий запрос камеры: если getUserMedia ещё резолвится —
    // его результат будет отброшен по устаревшему токену (см. startCamera).
    cam.token += 1;
    cam.starting = false;
    cam.active = false;
    cam.live = false;
    if (cam.video) {
      try {
        cam.video.srcObject = null;
      } catch (e) {
        /* игнорируем — элемент мог быть уже удалён из DOM */
      }
      cam.video = null;
    }
    if (cam.stream) {
      try {
        var tracks = cam.stream.getTracks ? cam.stream.getTracks() : [];
        for (var i = 0; i < tracks.length; i++) {
          try {
            tracks[i].stop();
          } catch (e2) {
            /* игнорируем — трек мог уже остановиться */
          }
        }
      } catch (e3) {
        /* игнорируем */
      }
      cam.stream = null;
    }
  }

  // Соответствие ключей приёмов пищи и подписей (для кнопок-чипов).
  // Источник истины по подписям — App.mealLabel, но порядок задаём здесь.
  var MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"];

  // Подписи уровней уверenности модели. Локализуются в момент рендера
  // через confidenceLabel(), а не на уровне модуля.
  function confidenceLabel(conf) {
    if (conf === "low") return L("низкая", "low");
    if (conf === "medium") return L("средняя", "medium");
    if (conf === "high") return L("высокая", "high");
    return "";
  }

  // ===== Утилиты =====

  // Освобождает ранее созданный objectURL превью (чтобы не текла память).
  function revokePreview() {
    if (state.previewUrl) {
      try {
        URL.revokeObjectURL(state.previewUrl);
      } catch (e) {
        /* игнорируем — браузер мог уже освободить URL */
      }
      state.previewUrl = null;
    }
  }

  // Полный сброс состояния страницы к исходному (главный экран сканера).
  // Камеру останавливаем здесь же — render()/renderUpload() переоткроют её
  // заново на главном экране (не плодим параллельные потоки).
  function reset() {
    revokePreview();
    camStopStream();
    pendingClear();
    state.file = null;
    state.result = null;
    state.base = null;
    state.edited = null;
    state.mealType = defaultMealTypeByHour();
    render();
  }

  // Безопасное экранирование текста от AI/пользователя перед вставкой в HTML.
  function esc(s) {
    if (App && typeof App.escapeHtml === "function") {
      return App.escapeHtml(s == null ? "" : String(s));
    }
    // Запасной вариант, если хелпер недоступен.
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Форматирование числа (округление) через хелпер App.fmt с запасным вариантом.
  function fmt(n) {
    if (App && typeof App.fmt === "function") return App.fmt(n);
    var num = Number(n);
    return isFinite(num) ? String(Math.round(num)) : "0";
  }

  // Безопасное приведение к числу (для значений из input/анализа).
  function num(v) {
    var n = Number(v);
    return isFinite(n) ? n : 0;
  }

  // Округление значения макроса до одного знака (для аккуратного отображения в input).
  function round1(v) {
    return Math.round(num(v) * 10) / 10;
  }

  // Локализованная подпись приёма пищи (через App.mealLabel, который сам
  // выбирает язык по App.pick в момент вызова).
  function mealLabel(type) {
    if (App && typeof App.mealLabel === "function") return App.mealLabel(type);
    return type;
  }

  // Приём пищи по умолчанию исходя из локального часа: до 11 — завтрак,
  // 11–16 — обед, 16–22 — ужин, иначе — перекус. Используется как разумное
  // начальное значение фото-потока (голос берёт meal_type от бэкенда).
  function defaultMealTypeByHour() {
    var h = new Date().getHours();
    if (h < 11) return "breakfast";
    if (h < 16) return "lunch";
    if (h < 22) return "dinner";
    return "snack";
  }

  // Названия месяцев для человекочитаемой дады цели (task 5).
  var MONTHS_RU_SCAN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
  ];
  var MONTHS_EN_SCAN = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];

  // Целевая дата добавления записи (task 5): читаем App.state.scanDate,
  // выставленную FAB-меню дневника; при отсутствии/некорректном значении —
  // сегодняшняя дата. НЕ очищаем здесь (чистим в onHide).
  function targetDate() {
    var d = App.state && App.state.scanDate;
    if (d && /^\d{4}-\d{2}-\d{2}$/.test(String(d))) return String(d);
    return App.todayStr();
  }

  // Человекочитаемая подпись даты цели ("18 июня 2026" | "18 June 2026").
  function humanScanDate(isoDate) {
    var parts = String(isoDate).split("-");
    var y = parseInt(parts[0], 10);
    var m = parseInt(parts[1], 10) - 1;
    var d = parseInt(parts[2], 10);
    if (isNaN(y) || isNaN(m) || isNaN(d) || !MONTHS_RU_SCAN[m]) return isoDate;
    return L(
      d + " " + MONTHS_RU_SCAN[m] + " " + y,
      d + " " + MONTHS_EN_SCAN[m] + " " + y
    );
  }

  // HTML подсказки о целевой дате: показываем ТОЛЬКО когда цель != сегодня.
  // Иначе "" (запись уйдёт в сегодняшний день — подсказка не нужна).
  function scanDateHintHtml() {
    var d = targetDate();
    if (d === App.todayStr()) return "";
    return (
      '<p class="scan-cam-date">' +
        icon("calendar", { size: 16 }) +
        "<span>" +
          esc(L("Добавится в день: ", "Will be added to: ")) +
          esc(humanScanDate(d)) +
        "</span>" +
      "</p>"
    );
  }

  // Короткая локализованная подпись канонической единицы измерения.
  // pcs -> шт/pcs, g -> г/g, ml -> мл/ml, serving -> порция/serving.
  function unitLabel(key) {
    switch (key) {
      case "pcs":
        return L("шт", "pcs");
      case "g":
        return L("г", "g");
      case "ml":
        return L("мл", "ml");
      case "serving":
        return L("порция", "serving");
      default:
        return "";
    }
  }

  // Короткий показ уведомления.
  function toast(msg) {
    if (App && typeof App.toast === "function") App.toast(msg);
  }

  // Лёгкая тактильная отдача (если доступна в Telegram).
  function haptic(kind) {
    if (App && typeof App.haptic === "function") App.haptic(kind);
  }

  // Премиум-статус пользователя (источник истины — App; здесь только удобный доступ).
  function isPremium() {
    return !!(App && typeof App.isPremium === "function" && App.isPremium());
  }

  // ===== Лимит бесплатных сканирований =====

  // Эвристика: похоже ли сообщение/ошибка на «лимит сканирований» (HTTP 402).
  // Бэкенд при free отдаёт detail {error:"scan_limit", message}; App.api бросает
  // Error с этим message. Дополнительно проверяем числовой код 402, если он есть.
  function isScanLimitError(err) {
    if (!err) return false;
    var status = err.status || err.code || err.httpStatus;
    if (status === 402 || status === "402") return true;
    var msg = (err && err.message ? String(err.message) : "").toLowerCase();
    if (!msg) return false;
    return (
      msg.indexOf("лимит") !== -1 ||
      msg.indexOf("scan") !== -1 ||
      msg.indexOf("scan_limit") !== -1 ||
      msg.indexOf("402") !== -1 ||
      msg.indexOf("сканир") !== -1 ||
      msg.indexOf("limit") !== -1
    );
  }

  // Запрашивает остаток бесплатных сканирований и обновляет счётчик на экране.
  // Best-effort: при ошибке просто не показываем счётчик (основной поток не ломаем).
  function loadScansRemaining() {
    if (!(App && App.api && typeof App.api.getScansRemaining === "function")) return;
    App.api
      .getScansRemaining()
      .then(function (res) {
        state.scans = res || null;
        updateScanCounter();
      })
      .catch(function () {
        // Счётчик вспомогательный — при сбое тихо скрываем.
        state.scans = null;
        updateScanCounter();
      });
  }

  // Возвращает HTML текущего счётчика сканирований (или "" если показывать нечего).
  function scanCounterHtml() {
    // Премиум / безлимит — либо «Безлимит», либо скрываем при remaining === -1.
    var s = state.scans;
    if (isPremium()) {
      return (
        '<p class="scan-counter scan-counter--premium">' +
        esc(L("Сканирование без ограничений", "Unlimited scans")) +
        "</p>"
      );
    }
    if (!s) return ""; // нет данных — ничего не показываем
    var remaining = num(s.remaining);
    if (s.is_premium || s.remaining === -1 || remaining < 0) {
      // На всякий случай дублируем premium-ветку, если статус пришёл из ответа.
      return (
        '<p class="scan-counter scan-counter--premium">' +
        esc(L("Сканирование без ограничений", "Unlimited scans")) +
        "</p>"
      );
    }
    var limit = num(s.limit);
    var low = remaining <= 0 ? " scan-counter--empty" : "";
    if (remaining <= 0) {
      return (
        '<p class="scan-counter scan-counter--free' + low + '">' +
        esc(L(
          "Бесплатные сканирования на сегодня закончились",
          "No free scans left today"
        )) +
        "</p>"
      );
    }
    return (
      '<p class="scan-counter scan-counter--free' + low + '">' +
      esc(L(
        "Осталось " + fmt(remaining) + " из " + fmt(limit) + " бесплатных сканирований",
        fmt(remaining) + " of " + fmt(limit) + " free scans left"
      )) +
      "</p>"
    );
  }

  // Перерисовывает только узел счётчика на экране загрузки (если он сейчас виден).
  function updateScanCounter() {
    if (!viewEl) return;
    var slot = viewEl.querySelector("#scan-counter-slot");
    if (!slot) return; // мы не на экране загрузки — обновлять нечего
    slot.innerHTML = scanCounterHtml();
  }

  // ===== Корневой элемент представления =====
  // viewEl передаётся в onShow и сохраняется для перерисовок.
  var viewEl = null;

  // ===== Шапка экрана с кнопкой «Закрыть» =====
  // Таббара на камере нет, поэтому шапка с крестиком рисуется в КАЖДОМ
  // состоянии: камера, превью, результат, голос, ошибки, пейволлы. Без неё
  // экран ошибки или пейволла становился тупиком — назад было только к
  // камере, а с камеры уже некуда.
  // Крестик слева, в строке с заголовком: там же, где «Назад» на остальных
  // экранах-задачах, — палец ищет выход в привычном углу.
  //   title    — заголовок состояния (уже локализованный);
  //   subtitle — необязательная строка пояснения под заголовком.
  function headHtml(title, subtitle) {
    return (
      '<header class="page-head scan-head">' +
        '<div class="scan-head__row">' +
          '<button type="button" class="scan-head__close" data-scan-close ' +
            'aria-label="' + esc(L("Закрыть", "Close")) + '">' +
            icon("close", { size: 22 }) +
          "</button>" +
          '<h1 class="page-title scan-head__title">' + esc(title) + "</h1>" +
        "</div>" +
        (subtitle ? '<p class="page-subtitle">' + esc(subtitle) + "</p>" : "") +
      "</header>"
    );
  }

  // Закрывает камеру: возвращает на экран, откуда её открыли.
  // Флаг scanOrigin гасим ДО перехода: экран назначения может сразу же снова
  // открыть камеру со своим scanOrigin, и поздний сброс стёр бы его.
  // Если камеру открыли из дневника на прошлый день (scanDate), возвращаем
  // дневник на тот же день: он открывается на «сегодня», пока не попросить
  // иначе через diaryReturnDate, и человек терял бы выбранную дату.
  /**
   * Есть ли на экране распознанное, но ещё не сохранённое блюдо. Распознавание
   * тратит сканирование из дневного лимита, а человек мог уже поправить вес и
   * КБЖУ — выбросить это одним касанием крестика нельзя.
   */
  function hasUnsavedResult() {
    return !!(state.edited || voice.result);
  }

  /**
   * Подтверждение выхода. Нативный диалог Telegram появился в Bot API 6.2: в
   * старом клиенте SDK лишь пишет ошибку и не зовёт колбэк, поэтому версию
   * проверяем явно, а иначе берём обычный confirm браузера.
   */
  function confirmLeave(cb) {
    var msg = L("Выйти? Распознанное блюдо не сохранится.", "Leave? The recognised dish will not be saved.");
    var tg = App.tg;
    if (tg && typeof tg.showConfirm === "function" &&
        typeof tg.isVersionAtLeast === "function" && tg.isVersionAtLeast("6.2")) {
      try {
        tg.showConfirm(msg, function (ok) { if (ok) cb(); });
        return;
      } catch (e) { /* фолбэк ниже */ }
    }
    if (window.confirm(msg)) cb();
  }

  function closeScan() {
    if (hasUnsavedResult()) {
      confirmLeave(doCloseScan);
      return;
    }
    doCloseScan();
  }

  function doCloseScan() {
    var st = App.state || {};
    var origin = st.scanOrigin;
    if (!origin || origin === "scan" || !(App._pages && App._pages[origin])) {
      origin = "diary";
    }
    if (origin === "diary" && App.state) {
      var d = st.scanDate;
      if (d && /^\d{4}-\d{2}-\d{2}$/.test(String(d)) && String(d) !== App.todayStr()) {
        App.state.diaryReturnDate = String(d);
      }
    }
    if (App.state) App.state.scanOrigin = null;
    App.navigate(origin);
  }

  // Один делегированный обработчик на документ вместо навески в каждом из
  // дюжины рендеров: разметка экрана пересобирается целиком, и забытая
  // навеска в новом состоянии оставила бы крестик мёртвым. Проверка
  // App._current отсекает клики, когда камера не на экране.
  document.addEventListener("click", function (ev) {
    if (!window.App || App._current !== "scan") return;
    var t = ev.target;
    var btn = t && typeof t.closest === "function" ? t.closest("[data-scan-close]") : null;
    if (!btn) return;
    haptic("light");
    closeScan();
  });

  // ===== Рендеринг экранов =====
  // Страница имеет несколько состояний, переключаемых через render():
  //   - нет файла           -> экран загрузки фото
  //   - есть файл, нет result-> экран превью (анализ запускается автоматически)
  //   - есть result         -> карточка результата + редактируемые поля + выбор приёма пищи
  // Ошибки показываются поверх через renderError().

  function render() {
    if (!viewEl) return;

    // При каждой смене экрана возвращаем прокрутку наверх,
    // чтобы короткий экран не «залип» прокрученным вниз.
    if (App && typeof App.scrollTop === "function") App.scrollTop();

    if (state.result) {
      renderResult();
    } else if (state.file) {
      renderPreview();
    } else {
      renderUpload();
    }
  }

  // --- Экран 1: ГЛАВНЫЙ экран сканера ---
  // Камера с НАСТОЯЩИМ спуском затвора на самом экране: рамка с живым видео,
  // под ней ряд управления «галерея — круглая кнопка съёмки — голос».
  // Разрешение на камеру объясняем ВНУТРИ рамки, а не отдельным полноэкранным
  // шагом: объяснение стоит ровно там, где появится картинка, и не заслоняет
  // остальной экран.
  // Режимы renderCamera:
  //   "live"   — поток запрашивается/подключён: в рамке <video>, затвор активен;
  //   "ask"    — доступ ещё ни разу не выдавался: в рамке объяснение + кнопка;
  //   "denied" — доступ отклонён или поток не получен: причина + «Попробовать снова».
  // Если камера не поддерживается окружением — renderUploadFallback (дропзона).
  function renderUpload() {
    // Перед любым повторным показом главного экрана гасим прошлый поток камеры,
    // чтобы не держать два потока одновременно.
    camStopStream();

    if (!cameraSupported()) {
      renderUploadFallback();
      return;
    }
    // ПЕРВЫЙ ВИЗИТ (флаг scan_cam_ok не выставлен): не дёргаем getUserMedia сами —
    // рисуем рамку с объяснением доступа. Поток запросим по явному тапу.
    if (!camPreviouslyGranted()) {
      renderCamera("ask");
      return;
    }
    // Камера уже разрешалась ранее — рисуем экран и сразу запрашиваем поток.
    renderCamera("live");
    startCamera();
  }

  // --- Экран 1a: ФОЛБЭК главного экрана — дропзона с системным выбором файла ---
  // Используется, если живая камера в принципе не поддерживается окружением.
  function renderUploadFallback() {
    viewEl.innerHTML =
      '<section class="page page-scan">' +
        headHtml(
          L("Сфотографировать еду", "Photograph your food"),
          L(
            "Сфотографируйте блюдо или загрузите фото — ИИ оценит калории и БЖУ.",
            "Take a photo of your dish or upload one — AI will estimate calories and macros."
          )
        ) +
        '<div class="card scan-dropzone" id="scan-dropzone">' +
          '<span class="scan-dropzone__icon">' + icon("camera", { size: 28 }) + "</span>" +
          '<p class="scan-dropzone__hint">' +
            esc(L(
              "Чёткое фото одной порции даёт точный результат.",
              "A clear photo of a single serving gives the best accuracy."
            )) +
          "</p>" +
          // Скрытый input: открываем камеру/галерею кнопкой.
          '<input type="file" id="scan-file" accept="image/*" capture="environment" hidden>' +
          '<button type="button" class="btn btn-cta btn-block" id="scan-pick">' +
            icon("camera", { size: 18 }) +
            "<span>" + esc(L("Сфотографировать / Загрузить", "Take photo / Upload")) + "</span>" +
          "</button>" +
          // Голосовой ввод (Этап 2, премиум). Отдельная кнопка под фото.
          '<button type="button" class="btn btn-ghost btn-block scan-voice-btn" id="scan-voice-pick">' +
            icon("mic", { size: 18 }) +
            "<span>" + esc(L("Записать голосом", "Record by voice")) + "</span>" +
          "</button>" +
          // Счётчик бесплатных сканирований (заполняется асинхронно из state.scans).
          '<div id="scan-counter-slot">' + scanCounterHtml() + "</div>" +
        "</div>" +
      "</section>";

    var fileInput = viewEl.querySelector("#scan-file");
    var pickBtn = viewEl.querySelector("#scan-pick");

    // Кнопка открывает системный диалог выбора файла/камеры.
    pickBtn.addEventListener("click", function () {
      haptic("light");
      fileInput.click();
    });

    // При выборе файла — валидируем и запускаем анализ.
    fileInput.addEventListener("change", function () {
      var f = fileInput.files && fileInput.files[0];
      if (!f) return;
      onFileChosen(f);
    });

    // Голосовой ввод — отдельный поток (премиум-гейтинг внутри).
    var voiceBtn = viewEl.querySelector("#scan-voice-pick");
    if (voiceBtn) {
      voiceBtn.addEventListener("click", function () {
        haptic("light");
        onVoiceTap();
      });
    }

    // Подгружаем актуальный остаток сканирований (best-effort) — обновит счётчик.
    loadScansRemaining();
  }

  // Содержимое рамки, когда видео ещё нет: зачем нужен доступ к камере и кнопка,
  // выдающая его (task 6 — объяснение живёт В РАМКЕ, а не отдельным экраном).
  //   denied=true — доступ был запрошен и отклонён: меняем текст и подпись кнопки.
  function camAskHtml(denied) {
    return (
      '<div class="scan-cam-ask" id="scan-cam-ask">' +
        '<span class="scan-cam-ask__icon">' + icon("camera", { size: 28 }) + "</span>" +
        '<p class="scan-cam-ask__text">' +
          esc(
            denied
              ? L(
                  "Доступ к камере не выдан. Разрешите его в настройках телефона или загрузите фото из галереи.",
                  "Camera access was denied. Allow it in your phone settings or upload a photo from the gallery."
                )
              : L(
                  "Нужен доступ к камере: видоискатель остаётся на устройстве, на сервер уходит только снятый вами кадр.",
                  "Camera access is needed: the viewfinder stays on your device, only the frame you take is sent to the server."
                )
          ) +
        "</p>" +
        '<button type="button" class="btn btn-cta scan-cam-ask__btn" id="scan-cam-enable">' +
          icon("camera", { size: 18 }) +
          "<span>" +
            esc(denied ? L("Попробовать снова", "Try again") : L("Включить камеру", "Enable camera")) +
          "</span>" +
        "</button>" +
      "</div>"
    );
  }

  // --- Экран 1b: ЖИВАЯ камера (рамка + спуск затвора + галерея и голос) ---
  function renderCamera(mode) {
    cam.active = true;
    // Затвор активируется только когда поток реально подключён к <video>
    // (revealCameraLive). Кнопка, стреляющая в пустоту, читается как сломанная.
    cam.live = false;

    // Целевая дата добавления (task 5): если отличается от сегодня — показываем
    // подсказку прямо на главном экране камеры, чтобы пользователь понимал,
    // что снимок уйдёт в другой день.
    var dateHintHtml = scanDateHintHtml();

    // Содержимое рамки: видео (ждём поток) либо объяснение доступа.
    var frameInner =
      mode === "live"
        ? '<video class="scan-cam-video" id="scan-cam-video" autoplay playsinline muted></video>' +
          '<span class="scan-cam-boot" id="scan-cam-boot">' +
            esc(L("Включаем камеру…", "Starting the camera…")) +
          "</span>"
        : camAskHtml(mode === "denied");

    viewEl.innerHTML =
      '<section class="page page-scan">' +
        // «Снять еду» — так же зовутся входы в камеру на «Сегодня» и в
        // дневнике; «Сфотографировать» одним словом на 30px не помещалось
        // в строку рядом с крестиком.
        headHtml(L("Снять еду", "Snap food")) +
        dateHintHtml +
        '<div class="card scan-cam-card">' +
          '<div class="scan-cam-window">' + frameInner + "</div>" +
          // Счётчик бесплатных сканирований (заполняется асинхронно из state.scans).
          '<div id="scan-counter-slot">' + scanCounterHtml() + "</div>" +
          // Ряд управления камерой: галерея слева, спуск по центру, голос справа.
          '<div class="scan-controls">' +
            '<button type="button" class="scan-control" id="scan-cam-gallery">' +
              '<span class="scan-control__icon">' + icon("inbox", { size: 20 }) + "</span>" +
              '<span class="scan-control__label">' +
                esc(L("Галерея", "Gallery")) +
              "</span>" +
            "</button>" +
            '<button type="button" class="scan-shutter" id="scan-shutter" disabled ' +
              'aria-label="' + esc(L("Снять кадр", "Take a photo")) + '">' +
              '<span class="scan-shutter__ring" aria-hidden="true"></span>' +
            "</button>" +
            '<button type="button" class="scan-control scan-voice-btn" id="scan-voice-pick">' +
              '<span class="scan-control__icon">' + icon("mic", { size: 20 }) + "</span>" +
              '<span class="scan-control__label">' +
                esc(L("Голос", "Voice")) +
              "</span>" +
            "</button>" +
          "</div>" +
          '<p class="scan-cam-hint" id="scan-cam-hint">' +
            esc(
              mode === "live"
                ? L("Наведите на блюдо и нажмите круглую кнопку", "Point at your dish and tap the round button")
                : L("Снимок можно сделать после доступа к камере", "You can take a photo once the camera is allowed")
            ) +
          "</p>" +
          // Скрытый input для выбора файла из галереи/камеры системы.
          '<input type="file" id="scan-file" accept="image/*" capture="environment" hidden>' +
        "</div>" +
      "</section>";

    // Кнопка выдачи доступа внутри рамки: показываем видео и запрашиваем поток.
    var enableBtn = viewEl.querySelector("#scan-cam-enable");
    if (enableBtn) {
      enableBtn.addEventListener("click", function () {
        haptic("light");
        startCamera();
      });
    }

    var fileInput = viewEl.querySelector("#scan-file");

    // «Галерея» открывает системный выбор файла.
    var galleryBtn = viewEl.querySelector("#scan-cam-gallery");
    if (galleryBtn) {
      galleryBtn.addEventListener("click", function () {
        haptic("light");
        if (fileInput) fileInput.click();
      });
    }

    // При выборе файла — валидируем и запускаем анализ (как в фолбэке).
    if (fileInput) {
      fileInput.addEventListener("change", function () {
        var f = fileInput.files && fileInput.files[0];
        if (!f) return;
        onFileChosen(f);
      });
    }

    // СПУСК ЗАТВОРА на самом экране — главная кнопка этой страницы.
    var shutterBtn = viewEl.querySelector("#scan-shutter");
    if (shutterBtn) {
      shutterBtn.addEventListener("click", function () {
        shutter();
      });
    }

    // Голосовой ввод — отдельный поток (премиум-гейтинг внутри).
    var voiceBtn = viewEl.querySelector("#scan-voice-pick");
    if (voiceBtn) {
      voiceBtn.addEventListener("click", function () {
        haptic("light");
        onVoiceTap();
      });
    }

    // Подгружаем актуальный остаток сканирований (best-effort) — обновит счётчик.
    loadScansRemaining();
  }

  // Спуск затвора: снимает кадр с живого видео. Единственный вызов — кнопка
  // на экране (#scan-shutter).
  function shutter() {
    haptic("medium");

    // Доступ ещё не выдан (рамка показывает объяснение) либо поток так и не
    // дошёл до <video> (cam.live) — запрашиваем камеру заново.
    if (!cam.active || !cam.stream || !cam.live) {
      // Запрос уже идёт: второй getUserMedia отменил бы первый и начал всё
      // заново — человек ждал бы доступ дважды.
      if (cam.starting) {
        toast(L("Запрашиваем доступ к камере…", "Requesting camera access…"));
        return;
      }
      startCamera();
      return;
    }
    if (captureFromVideo()) return;

    // Поток есть, но кадров ещё нет: молчаливая кнопка читается как сломанная.
    toast(L(
      "Камера ещё готовится — попробуйте через секунду",
      "The camera is still warming up — try again in a second"
    ));
  }

  // Запрашивает видеопоток камеры и подключает его к <video> главного экрана.
  // Защита от гонок: каждый вызов получает свой token; если за время запроса
  // экран сменился (token устарел) — поток сразу останавливается. При отказе
  // остаёмся на экране камеры и показываем причину прямо в рамке (не уводим
  // человека на другой экран).
  function startCamera() {
    if (!cameraSupported()) {
      renderUploadFallback();
      return;
    }
    cam.token += 1;
    var myToken = cam.token;
    cam.starting = true;
    cam.active = true;

    var constraints = { video: { facingMode: "environment" }, audio: false };

    navigator.mediaDevices
      .getUserMedia(constraints)
      .then(function (stream) {
        // Экран сменился, пока ждали доступ (ушли с главного/повторный запуск) —
        // полученный поток нам уже не нужен, освобождаем камеру.
        if (myToken !== cam.token || !cam.active) {
          stopRawStream(stream);
          return;
        }
        cam.stream = stream;
        cam.starting = false;

        // Успешно получили поток — запоминаем разрешение камеры, чтобы при
        // следующих визитах открывать её сразу, без кнопки «Включить».
        markCamGranted();

        // Заменяем объяснение доступа живым видео (затвор включим ниже, когда
        // поток будет реально привязан к <video>).
        revealCameraLive();

        var videoEl = viewEl && viewEl.querySelector("#scan-cam-video");
        if (!videoEl) {
          // Видео-узла нет (DOM сменился) — поток не к чему подключать.
          stopRawStream(stream);
          cam.stream = null;
          return;
        }
        cam.video = videoEl;

        try {
          videoEl.srcObject = stream;
        } catch (e) {
          // Совсем старые вебвью без srcObject — фолбэк через createObjectURL.
          try {
            videoEl.src = URL.createObjectURL(stream);
          } catch (e2) {
            camStopStream();
            renderUploadFallback();
            return;
          }
        }
        // Поток подключён — только теперь затвор имеет смысл.
        cam.live = true;
        camShutterReady();
        // play() может вернуть промис, который отклоняется в фоне — гасим.
        try {
          var p = videoEl.play();
          if (p && typeof p.catch === "function") p.catch(function () {});
        } catch (e3) {
          /* автоплей с muted обычно работает и без явного play() */
        }
      })
      .catch(function () {
        // Доступ запрещён / камера занята / нет устройства. Остаёмся на своём
        // экране: причина и повтор показываются в рамке, галерея рядом.
        if (myToken !== cam.token) return; // экран уже сменился — не трогаем
        camStopStream();
        if (!viewEl) return;
        renderCamera("denied");
      });
  }

  // Превращает рамку с объяснением доступа в живое окно камеры: ставит <video>.
  // Идемпотентна: если видео уже стоит (режим "live"), просто снимает заглушку
  // «Включаем камеру…».
  // ЗАТВОР ЗДЕСЬ НЕ ВКЛЮЧАЕМ: рамка готова раньше, чем поток реально привязан
  // к <video>, и в ветке «видео-узел исчез» привязки не будет вовсе. Кнопку
  // оживляет camShutterReady() — ровно там, где выставляется cam.live.
  function revealCameraLive() {
    if (!viewEl) return;
    var win = viewEl.querySelector(".scan-cam-window");
    if (!win) return;

    if (!win.querySelector("#scan-cam-video")) {
      win.innerHTML =
        '<video class="scan-cam-video" id="scan-cam-video" autoplay playsinline muted></video>';
    } else {
      var boot = win.querySelector("#scan-cam-boot");
      if (boot && boot.parentNode) boot.parentNode.removeChild(boot);
    }
  }

  // Включает спуск затвора и меняет подсказку под рядом управления.
  // Вызывается ТОЛЬКО когда поток уже подключён к <video> (cam.live === true):
  // кнопка, стреляющая в пустоту, читается как сломанная.
  function camShutterReady() {
    if (!viewEl) return;

    var shutterBtn = viewEl.querySelector("#scan-shutter");
    if (shutterBtn) shutterBtn.disabled = false;

    var hint = viewEl.querySelector("#scan-cam-hint");
    if (hint) {
      hint.textContent = L(
        "Наведите на блюдо и нажмите круглую кнопку",
        "Point at your dish and tap the round button"
      );
    }
  }

  // Останавливает «сырой» MediaStream (когда он не сохранён в cam.stream).
  function stopRawStream(stream) {
    if (!stream) return;
    try {
      var tracks = stream.getTracks ? stream.getTracks() : [];
      for (var i = 0; i < tracks.length; i++) {
        try {
          tracks[i].stop();
        } catch (e) {
          /* игнорируем */
        }
      }
    } catch (e2) {
      /* игнорируем */
    }
  }

  // --- Экран 2: превью выбранного фото (анализ идёт автоматически) ---
  function renderPreview() {
    var src = state.previewUrl || "";
    viewEl.innerHTML =
      '<section class="page page-scan">' +
        headHtml(L("Определение еды", "Food recognition")) +
        '<div class="card scan-preview">' +
          '<img class="scan-preview__img" src="' + esc(src) + '" alt="' +
            esc(L("Выбранное фото", "Selected photo")) + '">' +
          '<p class="scan-preview__status">' +
            esc(L("Анализируем фото…", "Analyzing photo…")) +
          "</p>" +
        "</div>" +
        '<button type="button" class="btn btn-ghost btn-block" id="scan-cancel">' +
          esc(L("Отмена", "Cancel")) +
        "</button>" +
      "</section>";

    var cancelBtn = viewEl.querySelector("#scan-cancel");
    cancelBtn.addEventListener("click", function () {
      haptic("light");
      reset();
    });
  }

  // --- Экран 3: карточка результата с редактируемыми полями + выбор приёма пищи ---
  function renderResult() {
    var r = state.result || {};
    var e = state.edited || {};
    var src = state.previewUrl || "";

    // Кнопки-чипы выбора приёма пищи.
    var chips = MEAL_TYPES.map(function (t) {
      var active = t === state.mealType ? " is-active" : "";
      return (
        '<button type="button" class="meal-chip' + active + '" data-meal="' + t + '">' +
          esc(mealLabel(t)) +
        "</button>"
      );
    }).join("");

    // Бейдж уверенности модели (low/medium/high -> низкая/средняя/высокая | low/medium/high).
    var conf = r.confidence;
    var confHtml = "";
    var confLabel = confidenceLabel(conf);
    if (conf && confLabel) {
      confHtml =
        '<div class="scan-edit-confidence scan-edit-confidence--' + esc(conf) + '">' +
          '<span class="scan-edit-confidence__label">' +
            esc(L("Уверенность ИИ:", "AI confidence:")) +
          "</span> " +
          '<span class="scan-edit-confidence__value">' + esc(confLabel) + "</span>" +
        "</div>";
    }

    // Комментарий от ИИ показываем только если он есть (это данные API — не переводим).
    var noteHtml = r.note
      ? '<p class="result-note">' + esc(r.note) + "</p>"
      : "";

    // Отладочный блок с «сырым» ответом модели (приходит только при DEBUG_AI).
    var debugHtml = r.debug
      ? '<details class="ai-debug">' +
          '<summary class="ai-debug__sum">' +
            esc(L("Ответ модели (debug)", "Model response (debug)")) +
          "</summary>" +
          '<pre class="ai-debug__pre">' + esc(JSON.stringify(r.debug, null, 2)) + "</pre>" +
        "</details>"
      : "";

    // Поле веса порции показываем всегда; если исходный вес неизвестен —
    // пропорциональный пересчёт не делаем, разрешая ручное редактирование значений.
    var hasBaseWeight = state.base && num(state.base.weight) > 0;
    var weightHintHtml = hasBaseWeight
      ? '<p class="scan-edit-hint">' +
          esc(L(
            "При изменении веса калории и БЖУ пересчитываются автоматически.",
            "Calories and macros recalculate automatically when you change the weight."
          )) +
        "</p>"
      : '<p class="scan-edit-hint">' +
          esc(L(
            "Вес порции не определён — отредактируйте значения вручную.",
            "Serving weight not detected — edit the values manually."
          )) +
        "</p>";

    viewEl.innerHTML =
      '<section class="page page-scan">' +
        headHtml(
          L("Результат", "Result"),
          L(
            "Проверьте и при необходимости поправьте значения перед добавлением.",
            "Review and adjust the values if needed before adding."
          )
        ) +
        '<div class="card result-card scan-edit-card">' +
          (src
            ? '<img class="result-card__img" src="' + esc(src) + '" alt="' +
                esc(L("Фото блюда", "Dish photo")) + '">'
            : "") +
          // Табло результата: название крупно и четыре числа КБЖУ. Форма
          // ниже — для правки; табло повторяет её значения вживую, чтобы
          // итог был виден одним взглядом, а не собирался по полям.
          resultSummaryHtml(e) +
          confHtml +
          '<span class="eyebrow scan-edit-eyebrow">' +
            esc(L("Поправить значения", "Adjust the values")) +
          "</span>" +
          '<form class="scan-edit-form" id="scan-edit-form" autocomplete="off">' +
            // Название блюда.
            '<label class="field scan-edit-field scan-edit-field--name">' +
              '<span class="field__label">' +
                esc(L("Название", "Name")) +
              "</span>" +
              '<input type="text" class="field__input scan-edit-input" id="scan-edit-name" ' +
                'value="' + esc(e.dish_name == null ? "" : e.dish_name) + '" ' +
                'placeholder="' + esc(L("Название блюда", "Dish name")) + '" maxlength="120">' +
            "</label>" +
            // Вес порции (граммы).
            '<label class="field scan-edit-field scan-edit-field--weight">' +
              '<span class="field__label">' +
                esc(L("Вес порции, г", "Serving weight, g")) +
              "</span>" +
              '<input type="number" inputmode="decimal" min="0" step="1" ' +
                'class="field__input scan-edit-input scan-edit-input--num" id="scan-edit-weight" ' +
                'value="' + esc(e.weight == null ? "" : e.weight) + '" placeholder="0">' +
            "</label>" +
            weightHintHtml +
            // Калории.
            '<label class="field scan-edit-field scan-edit-field--calories">' +
              '<span class="field__label">' +
                esc(L("Калории, ккал", "Calories, kcal")) +
              "</span>" +
              '<input type="number" inputmode="decimal" min="0" step="1" ' +
                'class="field__input scan-edit-input scan-edit-input--num" id="scan-edit-calories" ' +
                'value="' + esc(e.calories == null ? "" : e.calories) + '" placeholder="0">' +
            "</label>" +
            // Б/Ж/У в одну сетку.
            '<div class="scan-edit-macros">' +
              '<label class="field scan-edit-field scan-edit-field--macro">' +
                '<span class="field__label">' +
                  esc(L("Белки, г", "Protein, g")) +
                "</span>" +
                '<input type="number" inputmode="decimal" min="0" step="0.1" ' +
                  'class="field__input scan-edit-input scan-edit-input--num" id="scan-edit-proteins" ' +
                  'value="' + esc(e.proteins == null ? "" : e.proteins) + '" placeholder="0">' +
              "</label>" +
              '<label class="field scan-edit-field scan-edit-field--macro">' +
                '<span class="field__label">' +
                  esc(L("Жиры, г", "Fat, g")) +
                "</span>" +
                '<input type="number" inputmode="decimal" min="0" step="0.1" ' +
                  'class="field__input scan-edit-input scan-edit-input--num" id="scan-edit-fats" ' +
                  'value="' + esc(e.fats == null ? "" : e.fats) + '" placeholder="0">' +
              "</label>" +
              '<label class="field scan-edit-field scan-edit-field--macro">' +
                '<span class="field__label">' +
                  esc(L("Углеводы, г", "Carbs, g")) +
                "</span>" +
                '<input type="number" inputmode="decimal" min="0" step="0.1" ' +
                  'class="field__input scan-edit-input scan-edit-input--num" id="scan-edit-carbs" ' +
                  'value="' + esc(e.carbs == null ? "" : e.carbs) + '" placeholder="0">' +
              "</label>" +
            "</div>" +
          "</form>" +
          noteHtml +
        "</div>" +
        debugHtml +
        '<div class="meal-picker">' +
          '<p class="meal-picker__label">' +
            esc(L("Добавить как:", "Add as:")) +
          "</p>" +
          '<div class="meal-chips" id="scan-meals">' + chips + "</div>" +
        "</div>" +
        // Подсказка о целевой дате (task 5) — только если она отличается от сегодня.
        scanDateHintHtml() +
        '<button type="button" class="btn btn-cta btn-block" id="scan-add">' +
          icon("plus", { size: 18 }) +
          "<span>" + esc(L("Добавить в рацион", "Add to diary")) + "</span>" +
        "</button>" +
        // Пересъёмка — ЯВНОЕ действие пользователя. Никакой другой путь
        // внутри экрана не имеет права стереть этот результат.
        '<button type="button" class="btn btn-ghost btn-block" id="scan-reset">' +
          icon("camera", { size: 18 }) +
          "<span>" + esc(L("Снять заново", "Retake")) + "</span>" +
        "</button>" +
      "</section>";

    bindResultInputs(hasBaseWeight);

    // Переключение выбранного приёма пищи.
    var mealsWrap = viewEl.querySelector("#scan-meals");
    mealsWrap.addEventListener("click", function (ev) {
      var btn = ev.target.closest(".meal-chip");
      if (!btn) return;
      var t = btn.getAttribute("data-meal");
      if (!t) return;
      state.mealType = t;
      haptic("light");
      // Перерисовываем чипы, чтобы обновить активный класс.
      var all = mealsWrap.querySelectorAll(".meal-chip");
      all.forEach(function (b) {
        b.classList.toggle("is-active", b.getAttribute("data-meal") === t);
      });
    });

    // Добавление записи в рацион.
    viewEl.querySelector("#scan-add").addEventListener("click", function () {
      addToDiary();
    });

    // Пересъёмка — полный сброс к экрану камеры (только по явному тапу).
    viewEl.querySelector("#scan-reset").addEventListener("click", function () {
      haptic("light");
      reset();
    });
  }

  // Привязка обработчиков к редактируемым полям результата.
  // hasBaseWeight: есть ли исходный (ненулевой) вес для пропорционального пересчёта.
  // Табло результата над формой правки: название блюда и четыре числа.
  function resultSummaryHtml(e) {
    var name = e.dish_name == null || e.dish_name === ""
      ? L("Блюдо", "Dish")
      : e.dish_name;
    return (
      '<div class="scan-sum">' +
        '<h2 class="scan-sum__name" id="scan-sum-name">' + esc(name) + "</h2>" +
        '<div class="scan-sum__grid">' +
          summaryStatHtml("kcal", L("ккал", "kcal"), e.calories) +
          summaryStatHtml("p", L("Белки", "Protein"), e.proteins) +
          summaryStatHtml("f", L("Жиры", "Fat"), e.fats) +
          summaryStatHtml("c", L("Углев.", "Carbs"), e.carbs) +
        "</div>" +
      "</div>"
    );
  }

  function summaryStatHtml(mod, label, value) {
    return (
      '<div class="scan-sum__stat scan-sum__stat--' + mod + '">' +
        '<span class="eyebrow scan-sum__label">' + esc(label) + "</span>" +
        '<span class="num scan-sum__value" id="scan-sum-' + mod + '">' +
          esc(summaryValue(value)) +
        "</span>" +
      "</div>"
    );
  }

  // Пустое поле формы на табло показываем как «—», а не как 0: ноль выглядел
  // бы как результат распознавания, которого не было.
  function summaryValue(v) {
    if (v === "" || v == null) return "—";
    return fmt(v);
  }

  // Переносит текущие значения state.edited на табло (без перерисовки формы).
  function syncResultSummary() {
    var e = state.edited || {};
    var nameEl = viewEl.querySelector("#scan-sum-name");
    if (nameEl) {
      nameEl.textContent = e.dish_name ? e.dish_name : L("Блюдо", "Dish");
    }
    var map = { kcal: e.calories, p: e.proteins, f: e.fats, c: e.carbs };
    for (var key in map) {
      var el = viewEl.querySelector("#scan-sum-" + key);
      if (el) el.textContent = summaryValue(map[key]);
    }
  }

  function bindResultInputs(hasBaseWeight) {
    var nameEl = viewEl.querySelector("#scan-edit-name");
    var weightEl = viewEl.querySelector("#scan-edit-weight");
    var calEl = viewEl.querySelector("#scan-edit-calories");
    var protEl = viewEl.querySelector("#scan-edit-proteins");
    var fatEl = viewEl.querySelector("#scan-edit-fats");
    var carbEl = viewEl.querySelector("#scan-edit-carbs");

    // Синхронизирует значение из input в state.edited (без перерасчёта).
    function syncField(key, el, isInt) {
      if (!el) return;
      if (key === "dish_name") {
        state.edited.dish_name = el.value;
        return;
      }
      // Пустую строку оставляем как "", чтобы не подставлять 0 на лету.
      if (el.value === "") {
        state.edited[key] = "";
        return;
      }
      var v = num(el.value);
      state.edited[key] = isInt ? Math.round(v) : round1(v);
    }

    if (nameEl) {
      nameEl.addEventListener("input", function () {
        state.edited.dish_name = nameEl.value;
        syncResultSummary();
      });
    }

    // Изменение веса -> пропорциональный пересчёт калорий и БЖУ от исходных значений.
    if (weightEl) {
      weightEl.addEventListener("input", function () {
        state.edited.weight = weightEl.value === "" ? "" : num(weightEl.value);

        if (!hasBaseWeight) {
          // Исходный вес неизвестен — пересчёт невозможен, оставляем ручное редактирование.
          return;
        }
        var baseW = num(state.base.weight);
        var newW = num(weightEl.value);
        // При пустом/нулевом весе пересчёт не делаем — ждём осмысленное значение.
        if (weightEl.value === "" || newW <= 0 || baseW <= 0) return;

        var k = newW / baseW;
        state.edited.calories = Math.round(num(state.base.calories) * k);
        state.edited.proteins = round1(num(state.base.proteins) * k);
        state.edited.fats = round1(num(state.base.fats) * k);
        state.edited.carbs = round1(num(state.base.carbs) * k);

        // Обновляем зависимые поля без полной перерисовки (фокус остаётся на весе).
        if (calEl) calEl.value = String(state.edited.calories);
        if (protEl) protEl.value = String(state.edited.proteins);
        if (fatEl) fatEl.value = String(state.edited.fats);
        if (carbEl) carbEl.value = String(state.edited.carbs);
        syncResultSummary();
      });
    }

    // Ручное редактирование калорий/БЖУ — синхронизируем в state.edited и на табло.
    if (calEl) calEl.addEventListener("input", function () { syncField("calories", calEl, true); syncResultSummary(); });
    if (protEl) protEl.addEventListener("input", function () { syncField("proteins", protEl, false); syncResultSummary(); });
    if (fatEl) fatEl.addEventListener("input", function () { syncField("fats", fatEl, false); syncResultSummary(); });
    if (carbEl) carbEl.addEventListener("input", function () { syncField("carbs", carbEl, false); syncResultSummary(); });
  }

  // --- Экран ошибки (с возможностью повтора) ---
  // mode: "analyze" — повтор анализа того же файла; "upload" — вернуться к выбору.
  function renderError(message, mode) {
    // Сбрасываем прокрутку наверх, чтобы экран ошибки был сразу виден целиком.
    if (App && typeof App.scrollTop === "function") App.scrollTop();

    viewEl.innerHTML =
      '<section class="page page-scan">' +
        headHtml(L("Что-то пошло не так", "Something went wrong")) +
        '<div class="card error-card">' +
          '<span class="error-card__icon">' + icon("warning", { size: 28 }) + "</span>" +
          // Текст в прокручиваемом блоке: при DEBUG_AI сюда приходит и сырой ответ.
          '<div class="error-card__msg">' + esc(message) + "</div>" +
          '<button type="button" class="btn btn-cta btn-block" id="scan-retry">' +
            icon("refresh", { size: 18 }) +
            "<span>" + esc(L("Повторить", "Retry")) + "</span>" +
          "</button>" +
          '<button type="button" class="btn btn-ghost btn-block" id="scan-back">' +
            esc(L("Выбрать другое фото", "Choose another photo")) +
          "</button>" +
        "</div>" +
      "</section>";

    viewEl.querySelector("#scan-retry").addEventListener("click", function () {
      haptic("medium");
      if (mode === "analyze" && state.file) {
        // Повторяем анализ того же файла.
        renderPreview();
        analyze();
      } else {
        reset();
      }
    });

    viewEl.querySelector("#scan-back").addEventListener("click", function () {
      haptic("light");
      reset();
    });
  }

  // ===== Пейволл страницы =====
  // Собственный рендер вместо App.paywall: тот вставляет иконки эмодзи
  // и затирает контейнер целиком, а на экране лимита рядом с пейволлом обязан
  // уцелеть снятый кадр. Классы намеренно те же (.paywall*), чтобы оформление
  // осталось единым с остальными заглушками приложения.
  //   opts: { iconName, title, desc, bullets:[...] }
  function paywallCardHtml(opts) {
    opts = opts || {};
    var bullets = Array.isArray(opts.bullets) ? opts.bullets : [];
    var bulletsHtml = "";
    if (bullets.length) {
      var items = "";
      for (var i = 0; i < bullets.length; i++) {
        items +=
          '<li class="paywall-bullet">' +
            '<span class="paywall-bullet-mark">' + icon("check", { size: 16 }) + "</span>" +
            '<span class="paywall-bullet-text">' + esc(bullets[i]) + "</span>" +
          "</li>";
      }
      bulletsHtml = '<ul class="paywall-bullets">' + items + "</ul>";
    }

    return (
      '<div class="card paywall-card">' +
        '<span class="paywall-icon">' + icon(opts.iconName || "lock", { size: 28 }) + "</span>" +
        '<h2 class="paywall-title">' + esc(opts.title || L("Премиум-функция", "Premium feature")) + "</h2>" +
        '<p class="paywall-desc">' +
          esc(opts.desc || L(
            "Эта возможность доступна по подписке",
            "This feature is available with a subscription"
          )) +
        "</p>" +
        bulletsHtml +
      "</div>" +
      '<div class="paywall-lock">' +
        '<span class="paywall-lock-icon">' + icon("lock", { size: 16 }) + "</span>" +
        '<span class="paywall-lock-text">' +
          esc(L("Недоступно — нужна подписка", "Unavailable — subscription required")) +
        "</span>" +
      "</div>"
    );
  }

  // HTML кнопки перехода на подписку (бинд — bindPaywallCta).
  function paywallCtaHtml() {
    return (
      '<button type="button" class="btn btn-cta btn-block paywall-cta" id="scan-paywall-cta">' +
        icon("gem", { size: 18 }) +
        "<span>" + esc(L("Оформить подписку", "Get subscription")) + "</span>" +
      "</button>"
    );
  }

  // Навешивает переход на страницу подписки (App.goSubscription помнит, откуда
  // пришли, поэтому «Назад» там вернёт на сканер — и к отложенному кадру).
  function bindPaywallCta() {
    var btn = viewEl && viewEl.querySelector("#scan-paywall-cta");
    if (!btn) return;
    btn.addEventListener("click", function () {
      haptic("light");
      if (App && typeof App.goSubscription === "function") {
        App.goSubscription();
      } else if (App && typeof App.navigate === "function") {
        App.navigate("subscription");
      }
    });
  }

  // --- Экран лимита сканирований (пейволл ПОВЕРХ снятого кадра) ---
  // HTTP 402 приходит уже ПОСЛЕ съёмки: человек навёл камеру, снял блюдо и ждал
  // ответа. Выбрасывать кадр здесь — значит заставить его снимать тарелку заново
  // после оплаты, а еда к тому моменту уже съедена. Поэтому кадр и состояние
  // остаются в памяти (pending), пейволл рисуется над затемнённым снимком, и
  // возврат со страницы подписки продолжает работу с тем же фото (см. onShow).
  function renderScanLimit() {
    if (App && typeof App.scrollTop === "function") App.scrollTop();

    // Камеру гасим (мы не на экране съёмки), но ФАЙЛ И ПРЕВЬЮ НЕ ТРОГАЕМ.
    camStopStream();

    var src = state.previewUrl || "";
    var hasShot = !!(state.file && src);
    if (hasShot) {
      pending.active = true;
      pending.date = targetDate();
    }

    // Снимок под пейволлом: приглушён, с подписью «кадр сохранён» — видно, что
    // работа не пропала и после подписки продолжится ровно с него.
    var shotHtml = hasShot
      ? '<div class="scan-hold">' +
          '<img class="scan-hold__img" src="' + esc(src) + '" alt="' +
            esc(L("Снятый кадр", "Captured photo")) + '">' +
          '<div class="scan-hold__veil">' +
            '<span class="scan-hold__icon">' + icon("lock", { size: 20 }) + "</span>" +
            '<span class="scan-hold__text">' +
              esc(L("Кадр сохранён — продолжим с него", "Photo saved — we'll continue with it")) +
            "</span>" +
          "</div>" +
        "</div>"
      : "";

    viewEl.innerHTML =
      '<section class="page page-scan">' +
        headHtml(L("Лимит сканирований", "Scan limit reached")) +
        shotHtml +
        paywallCardHtml({
          iconName: "camera",
          title: L("Лимит сканирований", "Scan limit reached"),
          desc: L(
            "На сегодня бесплатные сканирования закончились",
            "You've used all free scans for today"
          ),
          bullets: [
            L("Безлимитные сканирования по подписке", "Unlimited scans with a subscription"),
            L("AI-распознавание еды по фото", "AI food recognition from photos"),
          ],
        }) +
        paywallCtaHtml() +
        // Повтор нужен на случай, если подписка оформлена в этом же сеансе:
        // кадр на месте, остаётся просто отправить его ещё раз.
        (hasShot
          ? '<button type="button" class="btn btn-ghost btn-block" id="scan-limit-retry">' +
              icon("refresh", { size: 18 }) +
              "<span>" + esc(L("Повторить анализ", "Retry analysis")) + "</span>" +
            "</button>"
          : "") +
        '<button type="button" class="btn btn-ghost btn-block" id="scan-limit-back">' +
          icon("camera", { size: 18 }) +
          "<span>" + esc(L("Снять другое фото", "Take another photo")) + "</span>" +
        "</button>" +
      "</section>";

    bindPaywallCta();

    var retryBtn = viewEl.querySelector("#scan-limit-retry");
    if (retryBtn) {
      retryBtn.addEventListener("click", function () {
        haptic("medium");
        renderPreview();
        analyze();
      });
    }

    viewEl.querySelector("#scan-limit-back").addEventListener("click", function () {
      haptic("light");
      reset();
    });
  }

  // ===== Логика =====

  // Обработка выбранного файла: валидация типа, создание превью, запуск анализа.
  function onFileChosen(file) {
    // Проверяем, что это изображение (мягкая проверка по MIME).
    if (file.type && file.type.indexOf("image/") !== 0) {
      toast(L("Пожалуйста, выберите изображение", "Please choose an image"));
      return;
    }
    // Уходим с главного экрана — освобождаем камеру (не держим поток в фоне).
    camStopStream();
    // Новый кадр заменяет отложенный: ждать больше нечего.
    pendingClear();
    // Освобождаем предыдущее превью и готовим новое.
    revokePreview();
    state.file = file;
    state.result = null;
    state.base = null;
    state.edited = null;
    try {
      state.previewUrl = URL.createObjectURL(file);
    } catch (e) {
      state.previewUrl = null;
    }
    renderPreview();
    analyze();
  }

  // Отправка фото на бэкенд и обработка ответа.
  function analyze() {
    if (!state.file) {
      reset();
      return;
    }
    var fileAtStart = state.file; // фиксируем, чтобы не показать чужой результат

    if (App && typeof App.showLoading === "function") App.showLoading();

    App.api
      .analyzeFood(state.file)
      .then(function (res) {
        // Если за время запроса пользователь сбросил/сменил файл — игнорируем ответ.
        if (state.file !== fileAtStart) return;

        // Кадр дошёл до результата — он больше НЕ «отложен из-за лимита».
        // Иначе pending.active пережил бы успешный повтор анализа (кнопка
        // «Повторить анализ» на экране лимита), и следующий возврат на камеру
        // ушёл бы в ветку onShow для отложенного кадра: повторный анализ поверх
        // готового результата — потраченное сканирование и стёртые правки полей.
        pendingClear();

        var weight = res && res.weight_grams != null ? num(res.weight_grams) : 0;
        state.result = {
          dish_name:
            res && res.dish_name
              ? res.dish_name
              : L("Не удалось распознать еду", "Could not recognize the food"),
          calories: res ? num(res.calories) : 0,
          proteins: res ? num(res.proteins) : 0,
          fats: res ? num(res.fats) : 0,
          carbs: res ? num(res.carbs) : 0,
          weight_grams: weight,
          confidence: res && res.confidence ? res.confidence : null,
          note: res && res.note ? res.note : "",
          debug: res && res.debug ? res.debug : null,
        };

        // Исходные («сырые») значения — база для пропорционального пересчёта по весу.
        state.base = {
          weight: weight,
          calories: state.result.calories,
          proteins: state.result.proteins,
          fats: state.result.fats,
          carbs: state.result.carbs,
        };

        // Предзаполняем редактируемую форму результатами анализа.
        state.edited = {
          dish_name: state.result.dish_name,
          weight: weight > 0 ? weight : "",
          calories: Math.round(state.result.calories),
          proteins: round1(state.result.proteins),
          fats: round1(state.result.fats),
          carbs: round1(state.result.carbs),
        };

        render();

        // Успешный анализ потратил одно бесплатное сканирование — обновляем счётчик
        // (отрисуется при следующем возврате на экран загрузки; данные подтянем заранее).
        loadScansRemaining();
      })
      .catch(function (err) {
        if (state.file !== fileAtStart) return;
        // Если бэкенд отверг запрос из-за исчерпанного лимита (402) — показываем
        // единый paywall вместо обычного экрана ошибки.
        if (isScanLimitError(err)) {
          renderScanLimit();
          // Подтянем актуальный остаток (на случай, если paywall сменится).
          loadScansRemaining();
          return;
        }
        var msg =
          (err && err.message) ||
          L(
            "Не удалось проанализировать фото. Проверьте соединение и попробуйте снова.",
            "Could not analyze the photo. Check your connection and try again."
          );
        renderError(msg, "analyze");
      })
      .finally(function () {
        if (App && typeof App.hideLoading === "function") App.hideLoading();
      });
  }

  // Мост «после добавления -> дневник» (task 3). Освобождает ресурсы сканера
  // (камера/превью) и переходит на вкладку дневника, чтобы пользователь увидел
  // итог дня. Если навигация недоступна — мягкий откат к главному экрану сканера.
  // Даже если камеру открыли с «Сегодня», после добавления ведём в дневник:
  // человек хочет увидеть, куда легла запись. Задача закончена, поэтому
  // scanOrigin гасим — иначе он «протёк» бы в следующее открытие камеры.
  function goToDiaryAfterAdd() {
    revokePreview();
    camStopStream();
    // Кадр отработан — отложенный снимок больше не нужен.
    pendingClear();
    state.file = null;
    state.result = null;
    state.base = null;
    state.edited = null;
    if (App.state) App.state.scanOrigin = null;
    if (App && typeof App.navigate === "function") {
      App.navigate("diary");
    } else {
      // Навигация недоступна — возвращаемся на главный экран сканера.
      state.mealType = defaultMealTypeByHour();
      render();
    }
  }

  // Добавление отредактированного блюда в дневник за сегодня.
  function addToDiary() {
    if (!state.edited) return;
    var e = state.edited;

    var dishName = (e.dish_name == null ? "" : String(e.dish_name)).trim();
    if (!dishName) {
      haptic("warning");
      toast(L("Укажите название блюда", "Enter a dish name"));
      var nameEl = viewEl && viewEl.querySelector("#scan-edit-name");
      if (nameEl) nameEl.focus();
      return;
    }

    // Формируем запись строго по форме DiaryEntryIn — из ОТРЕДАКТИРОВАННЫХ значений.
    // Дата цели: App.state.scanDate (из FAB дневника) либо сегодня (task 5).
    var entry = {
      date: targetDate(),
      meal_type: state.mealType,
      dish_name: dishName,
      calories: Math.round(num(e.calories)),
      proteins: num(e.proteins),
      fats: num(e.fats),
      carbs: num(e.carbs),
    };

    // Количество/единица: фото-поток измеряет порцию в граммах. Если вес порции
    // известен (отредактированный или оценённый ИИ) — прокидываем его как g.
    var weightVal = num(e.weight);
    if (weightVal > 0) {
      entry.quantity = weightVal;
      entry.unit = "g";
    }

    if (App && typeof App.showLoading === "function") App.showLoading();

    App.api
      .addDiary(entry)
      .then(function () {
        haptic("success");
        toast(
          L("Добавлено в рацион: ", "Added to diary: ") + mealLabel(state.mealType)
        );
        // Инвалидируем кэш дневника за дату записи, чтобы «Питание» показало свежий день.
        if (App.state && App.state.diaryByDate) {
          delete App.state.diaryByDate[entry.date];
        }
        // КОНТРАКТ С ДНЕВНИКОМ: запись легла в entry.date (это может быть НЕ
        // сегодня — день выбирают в дневнике перед съёмкой). Дневник читает
        // diaryReturnDate и открывается на этом дне; без флага он открывался на
        // «сегодня», и добавленное блюдо выглядело пропавшим.
        if (App.state) App.state.diaryReturnDate = entry.date;
        // Цель использована — очищаем, чтобы следующий скан шёл в сегодня.
        if (App.state) App.state.scanDate = null;
        // МОСТ СКАН -> ДНЕВНИК (task 3): вместо тихого возврата к камере ведём
        // пользователя в дневник, чтобы он сразу увидел итог/прогресс дня.
        // Не вызываем reset() (он бы зря переоткрыл камеру) — освобождаем ресурсы
        // и чистим состояние; navigate("diary") запустит onHide (камера/микрофон).
        goToDiaryAfterAdd();
      })
      .catch(function (err) {
        haptic("error");
        var msg =
          (err && err.message) ||
          L(
            "Не удалось добавить запись. Проверьте соединение и попробуйте снова.",
            "Could not add the entry. Check your connection and try again."
          );
        toast(msg);
      })
      .finally(function () {
        if (App && typeof App.hideLoading === "function") App.hideLoading();
      });
  }

  /* =====================================================================
   *  ГОЛОСОВОЙ ВВОД ЕДЫ (Этап 2)
   *  Отдельный от фото поток. Премиум-фича: гейтинг через showVoicePaywall.
   *  Поддержка записи проверяется по navigator.mediaDevices + MediaRecorder.
   *  При отсутствии поддержки/доступа — фолбэк «отправьте голосовое боту».
   * ===================================================================== */

  // Поддерживается ли запись звука в этом окружении.
  function voiceRecordingSupported() {
    return !!(
      navigator &&
      navigator.mediaDevices &&
      typeof navigator.mediaDevices.getUserMedia === "function" &&
      typeof window.MediaRecorder !== "undefined"
    );
  }

  // Останавливает все треки активного аудиопотока и очищает таймер записи.
  // Вызывается в ЛЮБОМ исходе (стоп/отмена/ошибка), чтобы не держать микрофон.
  function voiceStopStream() {
    if (voice.timer) {
      try {
        clearInterval(voice.timer);
      } catch (e) {
        /* игнорируем */
      }
      voice.timer = null;
    }
    if (voice.stream) {
      try {
        var tracks = voice.stream.getTracks ? voice.stream.getTracks() : [];
        for (var i = 0; i < tracks.length; i++) {
          try {
            tracks[i].stop();
          } catch (e2) {
            /* игнорируем — трек мог уже остановиться */
          }
        }
      } catch (e3) {
        /* игнорируем */
      }
      voice.stream = null;
    }
    voice.recorder = null;
    voice.recording = false;
  }

  // Полный сброс голосового состояния (ресурсы + данные результата).
  function voiceReset() {
    voiceStopStream();
    voice.chunks = [];
    voice.seconds = 0;
    voice.result = null;
    voice.items = null;
    voice.mealType = "breakfast";
  }

  // Параметры paywall голосового ввода (контракт задачи).
  function voicePaywallOpts() {
    return {
      iconName: "mic",
      title: L("Голосовой ввод", "Voice input"),
      desc: L(
        "Опишите еду голосом — ИИ распознает блюда и калории",
        "Describe your meal by voice — AI detects dishes and calories"
      ),
      bullets: [
        L("Голосом вместо фото", "Voice instead of photo"),
        L("Несколько блюд за раз", "Several dishes at once"),
        L("Авто-расчёт КБЖУ", "Automatic calories & macros"),
      ],
    };
  }

  // Показывает paywall голосового ввода в текущем контейнере.
  // Рисуем своим рендером (paywallCardHtml), а не App.paywall: тот подставляет
  // эмодзи вместо иконок. «К камере» возвращает к съёмке, крестик в шапке —
  // туда, откуда камеру открыли.
  function showVoicePaywall() {
    if (!viewEl) return;
    if (App && typeof App.scrollTop === "function") App.scrollTop();

    viewEl.innerHTML =
      '<section class="page page-scan">' +
        headHtml(L("Голосовой ввод", "Voice input")) +
        paywallCardHtml(voicePaywallOpts()) +
        paywallCtaHtml() +
        '<button type="button" class="btn btn-ghost btn-block" id="scan-voice-paywall-back">' +
          icon("camera", { size: 18 }) +
          "<span>" + esc(L("К камере", "Back to camera")) + "</span>" +
        "</button>" +
      "</section>";

    bindPaywallCta();

    viewEl.querySelector("#scan-voice-paywall-back").addEventListener("click", function () {
      haptic("light");
      voiceReset();
      render();
    });
  }

  // Похоже ли на ошибку «нужен премиум» (402) для голосового потока.
  function isVoicePremiumError(err) {
    if (!err) return false;
    var status = err.status || err.code || err.httpStatus;
    if (status === 402 || status === "402") return true;
    var code = (err && err.code ? String(err.code) : "").toLowerCase();
    if (code.indexOf("premium") !== -1 || code.indexOf("subscription") !== -1) return true;
    var msg = (err && err.message ? String(err.message) : "").toLowerCase();
    if (!msg) return false;
    return (
      msg.indexOf("402") !== -1 ||
      msg.indexOf("премиум") !== -1 ||
      msg.indexOf("premium") !== -1 ||
      msg.indexOf("подписк") !== -1 ||
      msg.indexOf("subscription") !== -1
    );
  }

  // Тап по кнопке голосового ввода: гейтинг -> запись (или фолбэк).
  function onVoiceTap() {
    // Уходим с главного экрана камеры в голосовой поток — освобождаем камеру,
    // чтобы не держать видеопоток во время записи/paywall. На возврате к
    // главному экрану (render -> renderUpload) камера откроется заново.
    camStopStream();
    // ГЕЙТИНГ: голос — премиум.
    if (!isPremium()) {
      showVoicePaywall();
      return;
    }
    // Запись недоступна -> сразу фолбэк «отправьте голосовое боту».
    if (!voiceRecordingSupported()) {
      renderVoiceUnavailable();
      return;
    }
    startVoiceRecording();
  }

  // Запрашивает доступ к микрофону и запускает запись.
  function startVoiceRecording() {
    voiceReset();
    var stream;
    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then(function (s) {
        stream = s;
        voice.stream = s;

        var rec;
        try {
          rec = new MediaRecorder(s);
        } catch (e) {
          // Некоторые окружения не умеют создать MediaRecorder из потока.
          voiceStopStream();
          renderVoiceUnavailable();
          return;
        }
        voice.recorder = rec;
        voice.chunks = [];

        rec.ondataavailable = function (ev) {
          if (ev && ev.data && ev.data.size > 0) {
            voice.chunks.push(ev.data);
          }
        };
        // onstop обрабатываем явно при нажатии «Стоп» (см. stopVoiceRecording),
        // чтобы отличить отправку от отмены.
        rec.onerror = function () {
          voiceStopStream();
          renderVoiceError(
            L(
              "Не удалось записать голос. Попробуйте ещё раз.",
              "Could not record the voice. Please try again."
            )
          );
        };

        try {
          rec.start();
        } catch (e2) {
          voiceStopStream();
          renderVoiceUnavailable();
          return;
        }

        voice.recording = true;
        voice.seconds = 0;
        renderVoiceRecording();

        // Таймер длительности записи.
        voice.timer = setInterval(function () {
          voice.seconds += 1;
          updateVoiceTimer();
        }, 1000);
      })
      .catch(function () {
        // Доступ к микрофону отклонён или недоступен -> фолбэк.
        voiceStopStream();
        renderVoiceUnavailable();
      });
  }

  // Останавливает запись по «Стоп», собирает Blob и отправляет на распознавание.
  function stopVoiceRecording() {
    var rec = voice.recorder;
    if (!rec) {
      // Нечего останавливать — возвращаемся к экрану загрузки.
      voiceReset();
      render();
      return;
    }

    // Останавливаем таймер сразу (визуально запись завершена).
    if (voice.timer) {
      try {
        clearInterval(voice.timer);
      } catch (e) {}
      voice.timer = null;
    }
    voice.recording = false;

    var mime = rec.mimeType || "audio/webm";

    // По событию stop собираем Blob и отправляем.
    rec.onstop = function () {
      // Освобождаем микрофон СРАЗУ после остановки рекордера.
      voiceStopStream();

      var blob;
      try {
        blob = new Blob(voice.chunks, { type: mime });
      } catch (e) {
        blob = new Blob(voice.chunks);
      }
      voice.chunks = [];

      if (!blob || blob.size === 0) {
        renderVoiceError(
          L(
            "Запись пустая. Попробуйте ещё раз.",
            "The recording is empty. Please try again."
          )
        );
        return;
      }

      var file = blobToVoiceFile(blob, mime);
      submitVoice(file);
    };

    try {
      rec.stop();
    } catch (e) {
      // Если stop не сработал — освобождаем ресурсы и показываем ошибку.
      voiceStopStream();
      renderVoiceError(
        L(
          "Не удалось завершить запись. Попробуйте ещё раз.",
          "Could not finish the recording. Please try again."
        )
      );
    }
  }

  // Отмена записи: останавливаем без отправки, сбрасываем к экрану загрузки.
  function cancelVoiceRecording() {
    var rec = voice.recorder;
    if (rec) {
      // Глушим onstop, чтобы не отправить запись после отмены.
      rec.onstop = null;
      try {
        if (rec.state !== "inactive") rec.stop();
      } catch (e) {}
    }
    voiceReset();
    render();
  }

  // Делает File из Blob с именем по mime (контракт: webm/mp4/ogg -> иначе webm).
  function blobToVoiceFile(blob, mime) {
    var m = (mime || "").toLowerCase();
    var name = "voice.webm";
    if (m.indexOf("mp4") !== -1) name = "voice.mp4";
    else if (m.indexOf("ogg") !== -1) name = "voice.ogg";
    else if (m.indexOf("webm") !== -1) name = "voice.webm";

    var type = blob.type || mime || "audio/webm";
    try {
      return new File([blob], name, { type: type });
    } catch (e) {
      // Старые webview без конструктора File: дополняем Blob именем вручную.
      try {
        blob.name = name;
      } catch (e2) {}
      return blob;
    }
  }

  // Отправка аудио на бэкенд и обработка ответа.
  function submitVoice(file) {
    if (App && typeof App.showLoading === "function") App.showLoading();

    App.api
      .analyzeVoice(file)
      .then(function (res) {
        var rawItems = res && Array.isArray(res.items) ? res.items : [];
        // Нормализуем блюда к редактируемой форме.
        voice.items = rawItems.map(function (it) {
          it = it || {};
          return {
            dish_name: it.dish_name == null ? "" : String(it.dish_name),
            calories: Math.round(num(it.calories)),
            proteins: round1(num(it.proteins)),
            fats: round1(num(it.fats)),
            carbs: round1(num(it.carbs)),
            // Количество/единица приходят из parse_food_text (могут быть null).
            // Прокидываем как есть — редактируем только КБЖУ, эти поля read-only.
            quantity: it.quantity == null ? null : num(it.quantity),
            unit: it.unit == null ? null : String(it.unit),
          };
        });
        voice.result = {
          transcript: res && res.transcript ? String(res.transcript) : "",
          meal_type: res && res.meal_type ? res.meal_type : null,
        };
        // Приём пищи по умолчанию: из ответа или "breakfast".
        var mt = voice.result.meal_type;
        voice.mealType = MEAL_TYPES.indexOf(mt) !== -1 ? mt : "breakfast";

        renderVoiceResult();
      })
      .catch(function (err) {
        // 402/премиум -> paywall; иначе экран ошибки голоса.
        if (isVoicePremiumError(err)) {
          showVoicePaywall();
          return;
        }
        renderVoiceError(
          L(
            "Не удалось распознать голос. Попробуйте ещё раз.",
            "Could not recognize the voice. Please try again."
          )
        );
      })
      .finally(function () {
        if (App && typeof App.hideLoading === "function") App.hideLoading();
      });
  }

  // --- Экран записи голоса (таймер + индикатор + Стоп/Отмена) ---
  function renderVoiceRecording() {
    if (App && typeof App.scrollTop === "function") App.scrollTop();

    viewEl.innerHTML =
      '<section class="page page-scan">' +
        headHtml(
          L("Голосовой ввод", "Voice input"),
          L(
            "Назовите блюда и примерные порции — затем нажмите «Стоп».",
            "Say the dishes and rough portions — then tap “Stop”."
          )
        ) +
        '<div class="card scan-voice-rec">' +
          '<div class="scan-voice-rec__indicator" aria-hidden="true">' +
            '<span class="scan-voice-rec__pulse"></span>' +
            '<span class="scan-voice-rec__mic">' + icon("mic", { size: 32 }) + "</span>" +
          "</div>" +
          '<p class="scan-voice-rec__status">' +
            esc(L("Идёт запись…", "Recording…")) +
          "</p>" +
          '<div class="scan-voice-rec__timer num" id="scan-voice-timer">' +
            esc(formatVoiceTime(voice.seconds)) +
          "</div>" +
        "</div>" +
        '<button type="button" class="btn btn-cta btn-block scan-voice-stop" id="scan-voice-stop">' +
          icon("stop", { size: 18 }) +
          "<span>" + esc(L("Стоп", "Stop")) + "</span>" +
        "</button>" +
        '<button type="button" class="btn btn-ghost btn-block scan-voice-cancel" id="scan-voice-cancel">' +
          esc(L("Отмена", "Cancel")) +
        "</button>" +
      "</section>";

    viewEl.querySelector("#scan-voice-stop").addEventListener("click", function () {
      haptic("medium");
      stopVoiceRecording();
    });
    viewEl.querySelector("#scan-voice-cancel").addEventListener("click", function () {
      haptic("light");
      cancelVoiceRecording();
    });
  }

  // Форматирует длительность записи в M:SS.
  function formatVoiceTime(totalSec) {
    var s = Math.max(0, Math.round(num(totalSec)));
    var m = Math.floor(s / 60);
    var sec = s % 60;
    return m + ":" + (sec < 10 ? "0" + sec : "" + sec);
  }

  // Обновляет только узел таймера (без полной перерисовки экрана записи).
  function updateVoiceTimer() {
    if (!viewEl) return;
    var t = viewEl.querySelector("#scan-voice-timer");
    if (t) t.textContent = formatVoiceTime(voice.seconds);
  }

  // --- Экран «запись недоступна» (фолбэк на бота) ---
  function renderVoiceUnavailable() {
    if (App && typeof App.scrollTop === "function") App.scrollTop();
    voiceReset();

    viewEl.innerHTML =
      '<section class="page page-scan">' +
        headHtml(L("Голосовой ввод", "Voice input")) +
        '<div class="card scan-voice-unavailable">' +
          '<span class="scan-voice-unavailable__icon">' + icon("mic", { size: 28 }) + "</span>" +
          '<p class="scan-voice-unavailable__msg">' +
            esc(L(
              "Запись недоступна. Отправьте голосовое сообщение боту — он распознает и добавит еду.",
              "Recording is unavailable. Send a voice message to the bot — it will recognize and add the food."
            )) +
          "</p>" +
          '<button type="button" class="btn btn-cta btn-block scan-voice-back" id="scan-voice-back">' +
            esc(L("Назад", "Back")) +
          "</button>" +
        "</div>" +
      "</section>";

    viewEl.querySelector("#scan-voice-back").addEventListener("click", function () {
      haptic("light");
      voiceReset();
      render();
    });
  }

  // --- Экран ошибки голоса (Повторить/Назад) ---
  function renderVoiceError(message) {
    if (App && typeof App.scrollTop === "function") App.scrollTop();
    voiceStopStream();

    viewEl.innerHTML =
      '<section class="page page-scan">' +
        headHtml(L("Что-то пошло не так", "Something went wrong")) +
        '<div class="card error-card scan-voice-error">' +
          '<span class="error-card__icon">' + icon("warning", { size: 28 }) + "</span>" +
          '<div class="error-card__msg">' + esc(message) + "</div>" +
          '<button type="button" class="btn btn-cta btn-block" id="scan-voice-retry">' +
            icon("refresh", { size: 18 }) +
            "<span>" + esc(L("Повторить", "Retry")) + "</span>" +
          "</button>" +
          '<button type="button" class="btn btn-ghost btn-block" id="scan-voice-error-back">' +
            esc(L("Назад", "Back")) +
          "</button>" +
        "</div>" +
      "</section>";

    viewEl.querySelector("#scan-voice-retry").addEventListener("click", function () {
      haptic("medium");
      voiceReset();
      onVoiceTap();
    });
    viewEl.querySelector("#scan-voice-error-back").addEventListener("click", function () {
      haptic("light");
      voiceReset();
      render();
    });
  }

  // --- Экран результата голоса: транскрипт + выбор приёма + список блюд ---
  function renderVoiceResult() {
    if (App && typeof App.scrollTop === "function") App.scrollTop();

    var items = Array.isArray(voice.items) ? voice.items : [];

    // Если ничего не распознано — сообщение и кнопка назад.
    if (!items.length) {
      viewEl.innerHTML =
        '<section class="page page-scan">' +
          headHtml(L("Голосовой ввод", "Voice input")) +
          // Пустой результат — без декоративной картинки: объяснение и действие
          // важнее иллюстрации.
          '<div class="card scan-voice-empty">' +
            (voice.result && voice.result.transcript
              ? '<p class="scan-voice-transcript">' + esc(voice.result.transcript) + "</p>"
              : "") +
            '<p class="scan-voice-empty__msg">' +
              esc(L(
                "Не удалось распознать блюда. Попробуйте сказать чётче.",
                "Could not detect any dishes. Try speaking more clearly."
              )) +
            "</p>" +
            '<button type="button" class="btn btn-cta btn-block" id="scan-voice-empty-retry">' +
              esc(L("Записать снова", "Record again")) +
            "</button>" +
            '<button type="button" class="btn btn-ghost btn-block" id="scan-voice-empty-back">' +
              esc(L("Назад", "Back")) +
            "</button>" +
          "</div>" +
        "</section>";

      viewEl.querySelector("#scan-voice-empty-retry").addEventListener("click", function () {
        haptic("medium");
        voiceReset();
        onVoiceTap();
      });
      viewEl.querySelector("#scan-voice-empty-back").addEventListener("click", function () {
        haptic("light");
        voiceReset();
        render();
      });
      return;
    }

    // Транскрипт (мелким) — данные распознавания, экранируем.
    var transcriptHtml =
      voice.result && voice.result.transcript
        ? '<p class="scan-voice-transcript">' +
            '<span class="scan-voice-transcript__label">' +
              esc(L("Распознано:", "Recognized:")) +
            "</span> " +
            esc(voice.result.transcript) +
          "</p>"
        : "";

    // Чипы выбора приёма пищи.
    var chips = MEAL_TYPES.map(function (t) {
      var active = t === voice.mealType ? " is-active" : "";
      return (
        '<button type="button" class="meal-chip' + active + '" data-meal="' + t + '">' +
          esc(mealLabel(t)) +
        "</button>"
      );
    }).join("");

    // Редактируемые строки блюд.
    var rowsHtml = items.map(function (it, idx) {
      return voiceItemRowHtml(it, idx);
    }).join("");

    viewEl.innerHTML =
      '<section class="page page-scan">' +
        headHtml(
          L("Распознанные блюда", "Recognized dishes"),
          L(
            "Проверьте и поправьте значения перед добавлением.",
            "Review and adjust the values before adding."
          )
        ) +
        '<div class="card scan-voice-card">' +
          transcriptHtml +
          '<div class="meal-picker">' +
            '<p class="meal-picker__label">' +
              esc(L("Добавить как:", "Add as:")) +
            "</p>" +
            '<div class="meal-chips" id="scan-voice-meals">' + chips + "</div>" +
          "</div>" +
          '<div class="scan-voice-items" id="scan-voice-items">' + rowsHtml + "</div>" +
        "</div>" +
        // Подсказка о целевой дате (task 5) — только если она отличается от сегодня.
        scanDateHintHtml() +
        '<button type="button" class="btn btn-cta btn-block" id="scan-voice-add">' +
          esc(L("Добавить в рацион", "Add to diary")) +
        "</button>" +
        '<button type="button" class="btn btn-ghost btn-block" id="scan-voice-result-cancel">' +
          esc(L("Отмена", "Cancel")) +
        "</button>" +
      "</section>";

    // Выбор приёма пищи.
    var mealsWrap = viewEl.querySelector("#scan-voice-meals");
    mealsWrap.addEventListener("click", function (ev) {
      var btn = ev.target.closest(".meal-chip");
      if (!btn) return;
      var t = btn.getAttribute("data-meal");
      if (!t) return;
      voice.mealType = t;
      haptic("light");
      var all = mealsWrap.querySelectorAll(".meal-chip");
      all.forEach(function (b) {
        b.classList.toggle("is-active", b.getAttribute("data-meal") === t);
      });
    });

    bindVoiceItemInputs();

    // Добавить все оставшиеся блюда в рацион.
    viewEl.querySelector("#scan-voice-add").addEventListener("click", function () {
      addVoiceToDiary();
    });

    // Отмена — полный сброс к экрану загрузки.
    viewEl.querySelector("#scan-voice-result-cancel").addEventListener("click", function () {
      haptic("light");
      voiceReset();
      render();
    });
  }

  // HTML одной редактируемой строки блюда голосового результата.
  function voiceItemRowHtml(it, idx) {
    it = it || {};
    // Небольшой бейдж количества/единицы (read-only), если распознаны из речи.
    var qtyHtml = "";
    if (it.quantity != null) {
      var uLbl = unitLabel(it.unit);
      var qtyText = fmt(it.quantity) + (uLbl ? " " + uLbl : "");
      qtyHtml =
        '<span class="scan-voice-item__qty">' + esc(qtyText) + "</span>";
    }
    return (
      '<div class="scan-voice-item" data-idx="' + idx + '">' +
        '<div class="scan-voice-item__head">' +
          '<input type="text" class="field__input scan-voice-input scan-voice-input--name" ' +
            'data-field="dish_name" data-idx="' + idx + '" ' +
            'value="' + esc(it.dish_name == null ? "" : it.dish_name) + '" ' +
            'placeholder="' + esc(L("Название блюда", "Dish name")) + '" maxlength="120">' +
          qtyHtml +
          // Кнопка удаления строки: иконка вместо текстового крестика, зона нажатия 44px.
          '<button type="button" class="scan-voice-item__remove" data-idx="' + idx + '" ' +
            'aria-label="' + esc(L("Удалить", "Remove")) + '">' +
            icon("close", { size: 18 }) +
          "</button>" +
        "</div>" +
        '<div class="scan-voice-item__nums">' +
          voiceNumFieldHtml(L("Ккал", "Kcal"), "calories", idx, it.calories, "1") +
          voiceNumFieldHtml(L("Б", "P"), "proteins", idx, it.proteins, "0.1") +
          voiceNumFieldHtml(L("Ж", "F"), "fats", idx, it.fats, "0.1") +
          voiceNumFieldHtml(L("У", "C"), "carbs", idx, it.carbs, "0.1") +
        "</div>" +
      "</div>"
    );
  }

  // HTML компактного числового поля строки блюда.
  function voiceNumFieldHtml(label, field, idx, value, step) {
    return (
      '<label class="scan-voice-num">' +
        '<span class="scan-voice-num__label">' + esc(label) + "</span>" +
        '<input type="number" inputmode="decimal" min="0" step="' + esc(step) + '" ' +
          'class="field__input scan-voice-input scan-voice-input--num" ' +
          'data-field="' + esc(field) + '" data-idx="' + idx + '" ' +
          'value="' + esc(value == null ? "" : value) + '" placeholder="0">' +
      "</label>"
    );
  }

  // Привязка обработчиков к инпутам и кнопкам удаления строк голосового результата.
  function bindVoiceItemInputs() {
    var wrap = viewEl.querySelector("#scan-voice-items");
    if (!wrap) return;

    // Синхронизация значений инпутов в voice.items.
    wrap.addEventListener("input", function (ev) {
      var el = ev.target;
      if (!el || !el.getAttribute) return;
      var field = el.getAttribute("data-field");
      if (!field) return;
      var idx = parseInt(el.getAttribute("data-idx"), 10);
      if (isNaN(idx) || !voice.items || !voice.items[idx]) return;

      if (field === "dish_name") {
        voice.items[idx].dish_name = el.value;
        return;
      }
      if (el.value === "") {
        voice.items[idx][field] = "";
        return;
      }
      var v = num(el.value);
      voice.items[idx][field] = field === "calories" ? Math.round(v) : round1(v);
    });

    // Удаление строки блюда.
    wrap.addEventListener("click", function (ev) {
      var btn = ev.target.closest(".scan-voice-item__remove");
      if (!btn) return;
      var idx = parseInt(btn.getAttribute("data-idx"), 10);
      if (isNaN(idx) || !voice.items) return;
      voice.items.splice(idx, 1);
      haptic("light");
      // Перерисовываем результат (переиндексация строк); если строк не осталось —
      // renderVoiceResult покажет «пусто».
      renderVoiceResult();
    });
  }

  // Добавление всех оставшихся распознанных блюд в дневник за сегодня.
  function addVoiceToDiary() {
    var items = Array.isArray(voice.items) ? voice.items : [];
    if (!items.length) {
      toast(L("Нет блюд для добавления", "No dishes to add"));
      return;
    }

    // Готовим записи строго по DiaryEntryIn; пропускаем строки без названия.
    // Дата цели: App.state.scanDate (из FAB дневника) либо сегодня (task 5).
    var date = targetDate();
    var mealType = voice.mealType;
    var entries = [];
    for (var i = 0; i < items.length; i++) {
      var it = items[i] || {};
      var dishName = (it.dish_name == null ? "" : String(it.dish_name)).trim();
      if (!dishName) continue;
      entries.push({
        date: date,
        meal_type: mealType,
        dish_name: dishName,
        calories: Math.round(num(it.calories)),
        proteins: num(it.proteins),
        fats: num(it.fats),
        carbs: num(it.carbs),
        // Количество/единица прокидываются как есть (из parse_food_text).
        quantity: it.quantity == null ? null : num(it.quantity),
        unit: it.unit == null ? null : it.unit,
      });
    }

    if (!entries.length) {
      haptic("warning");
      toast(L("Укажите названия блюд", "Enter dish names"));
      return;
    }

    if (App && typeof App.showLoading === "function") App.showLoading();

    Promise.all(
      entries.map(function (entry) {
        return App.api.addDiary(entry);
      })
    )
      .then(function () {
        haptic("success");
        toast(
          L("Добавлено в рацион: ", "Added to diary: ") + mealLabel(mealType)
        );
        // Инвалидируем кэш дневника за дату записи.
        if (App.state && App.state.diaryByDate) {
          delete App.state.diaryByDate[date];
        }
        // КОНТРАКТ С ДНЕВНИКОМ (тот же, что в фото-потоке): открыть день,
        // в который реально легли записи, а не «сегодня».
        if (App.state) App.state.diaryReturnDate = date;
        // Цель использована — очищаем, чтобы следующий скан шёл в сегодня.
        if (App.state) App.state.scanDate = null;
        // МОСТ ГОЛОС -> ДНЕВНИК (task 3): после добавления ведём в дневник,
        // чтобы пользователь сразу увидел итог/прогресс дня.
        voiceReset();
        goToDiaryAfterAdd();
      })
      .catch(function (err) {
        haptic("error");
        var msg =
          (err && err.message) ||
          L(
            "Не удалось добавить записи. Проверьте соединение и попробуйте снова.",
            "Could not add the entries. Check your connection and try again."
          );
        toast(msg);
      })
      .finally(function () {
        if (App && typeof App.hideLoading === "function") App.hideLoading();
      });
  }

  // Снимает текущий кадр живого видео в JPEG-File и запускает существующий анализ.
  // Возвращает true, если кадр успешно снят (иначе false — звать фолбэк).
  function captureFromVideo() {
    var videoEl = cam.video || (viewEl && viewEl.querySelector("#scan-cam-video"));
    if (!videoEl) return false;

    var w = videoEl.videoWidth || 0;
    var h = videoEl.videoHeight || 0;
    // Поток ещё не готов (нет кадров) — снять нечего.
    if (!w || !h) return false;

    var canvas;
    try {
      canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      var ctx = canvas.getContext("2d");
      if (!ctx) return false;
      ctx.drawImage(videoEl, 0, 0, w, h);
    } catch (e) {
      // Рисование может упасть (напр. CORS-tainted) — отдаём управление фолбэку.
      return false;
    }

    // canvas.toBlob может отсутствовать в очень старых вебвью — проверяем.
    if (typeof canvas.toBlob !== "function") return false;

    // Освобождаем камеру СРАЗУ после снятия кадра: дальше идёт превью/анализ.
    camStopStream();

    canvas.toBlob(
      function (blob) {
        if (!blob) {
          // Не удалось получить Blob — возвращаемся на главный экран камеры.
          reset();
          return;
        }
        var file;
        try {
          file = new File([blob], "photo.jpg", { type: "image/jpeg" });
        } catch (e) {
          // Старые вебвью без конструктора File — дополняем Blob именем.
          try {
            blob.name = "photo.jpg";
          } catch (e2) {}
          file = blob;
        }
        onFileChosen(file);
      },
      "image/jpeg",
      0.9
    );
    return true;
  }

  // ===== Контроллер страницы =====
  window.PageScan = {
    // Вызывается при показе экрана. Получаем контейнер и рисуем главный экран
    // (живую камеру или фолбэк-дропзону).
    onShow: function (el) {
      viewEl = el;

      // ОТЛОЖЕННЫЙ КАДР: вернулись со страницы подписки (или закрыли камеру и
      // открыли её снова), а снимок ждёт анализа. Обычную зачистку состояния пропускаем —
      // иначе кадр, ради которого человек и уходил оформлять подписку, исчезнет.
      if (pending.active && state.file) {
        camStopStream();
        voiceReset();
        // Возвращаем целевую дату записи, снятую в onHide.
        if (App.state && pending.date) App.state.scanDate = pending.date;
        if (isPremium()) {
          // Подписка оформлена — продолжаем ровно с того же кадра.
          pendingClear();
          renderPreview();
          analyze();
        } else {
          renderScanLimit();
        }
        return;
      }
      pendingClear();

      // Каждый показ начинаем «с чистого листа», освобождая прошлое превью.
      revokePreview();
      // На всякий случай гасим прошлый видеопоток камеры (не плодим потоки).
      camStopStream();
      state.file = null;
      state.result = null;
      state.base = null;
      state.edited = null;
      // Приём пищи по умолчанию — по локальному часу (task 4), не всегда «завтрак».
      state.mealType = defaultMealTypeByHour();
      // Сбрасываем голосовой поток и освобождаем микрофон, если он был занят.
      voiceReset();

      // Одноразовый хинт: если на страницу пришли с намерением «голос»
      // (App.state.scanMode === "voice"), открываем голосовой ввод сразу после
      // рендера. Флаг гасим, чтобы не срабатывал повторно.
      var wantVoice = !!(App.state && App.state.scanMode === "voice");
      if (wantVoice && App.state) {
        App.state.scanMode = null;
      }

      render();

      // После рендера главного экрана — переходим к голосовому вводу
      // (onVoiceTap сам решает: paywall / запись / фолбэк). Делаем это
      // асинхронно, чтобы не мешать первичной отрисовке камеры.
      if (wantVoice) {
        setTimeout(function () {
          if (App._current === "scan") onVoiceTap();
        }, 0);
      }
    },
    // Вызывается при уходе с экрана — освобождаем ресурсы превью, камеру и микрофон.
    onHide: function () {
      // Останавливаем видеопоток камеры при уходе со страницы (важно: иначе
      // индикатор камеры останется гореть).
      camStopStream();
      // Останавливаем активную запись/поток (микрофон) при уходе со страницы.
      voiceStopStream();

      if (pending.active && state.file) {
        // Кадр ждёт подписки: НЕ освобождаем превью (иначе возвращаться будет
        // не к чему) и запоминаем целевую дату — её сейчас обнулят ниже.
        pending.date = pending.date || targetDate();
      } else {
        revokePreview();
      }

      // Целевую дату используем один раз: чистим при уходе, чтобы следующий
      // вход на сканер (без FAB) добавлял записи в сегодня (task 5).
      if (App.state) App.state.scanDate = null;
    }
  };

  // Регистрируем контроллер в приложении.
  if (window.App && typeof App.registerPage === "function") {
    App.registerPage("scan", window.PageScan);
  }
})();
