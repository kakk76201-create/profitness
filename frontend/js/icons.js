/*
 * icons.js — единый набор иконок приложения.
 *
 * ЗАЧЕМ: до этого роль иконок играли эмодзи — 524 вхождения, 112 разных
 * символов. Эмодзи рисует операционная система, поэтому на разных телефонах
 * они выглядят по-разному, не наследуют цвет текста, не выравниваются по
 * базовой линии и мгновенно выдают самодельность. Здесь один набор:
 * сетка 24×24, контур толщиной 1.75, скруглённые концы, цвет — currentColor.
 *
 * ИСПОЛЬЗОВАНИЕ:
 *     App.icon("dumbbell")                     -> строка <svg …>
 *     App.icon("chevron", { rotate: 90 })      -> повёрнутая на 90°
 *     App.icon("check", { size: 16, cls: "x" })
 *
 * Возвращается СТРОКА: весь фронтенд собирает разметку конкатенацией, и так
 * иконка вставляется в любой шаблон без отдельной операции над DOM.
 *
 * ПОВОРОТЫ НЕ ДУБЛИРУЮТСЯ: вместо ‹ › ▾ ⌄ — одна иконка chevron с опцией
 * rotate. Вместо → ← — одна arrow. Это экономит треть набора.
 *
 * Иконки декоративны: им проставляется aria-hidden. Если иконка несёт смысл
 * (кнопка без подписи), подпись задаётся на самой кнопке через aria-label.
 */
(function () {
  "use strict";

  // Геометрия пути для каждой иконки в системе координат 24×24.
  // Всё рисуется контуром: заливка только там, где без неё фигура
  // не читается (треугольник «плей», точки меню).
  var PATHS = {
    /* ---------- Интерфейсные ---------- */
    // Угол «>». Влево/вверх/вниз получаем поворотом на 180/270/90.
    chevron: '<path d="M9 5l7 7-7 7"/>',
    arrow: '<path d="M4 12h15M13 6l6 6-6 6"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    check: '<path d="M20 6.5L9.5 17 4 11.5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    play: '<path d="M8 5.5l10.5 6.5L8 18.5z" fill="currentColor" stroke-linejoin="round"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/>',
    skip: '<path d="M6 5l9 7-9 7z" fill="currentColor" stroke-linejoin="round"/><path d="M18 5v14"/>',
    dots: '<circle cx="5" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.6" fill="currentColor" stroke="none"/>',
    refresh: '<path d="M20.5 12a8.5 8.5 0 1 1-2.5-6"/><path d="M20.5 4v5h-5"/>',
    swap: '<path d="M4 8h13M13 4l4 4-4 4"/><path d="M20 16H7m4-4l-4 4 4 4"/>',
    trash: '<path d="M4 7h16M10 7V4.5h4V7M6.5 7l1 12.5h9L17.5 7"/>',
    warning: '<path d="M12 3.5L21.5 20H2.5z" stroke-linejoin="round"/><path d="M12 9.5v4.5M12 17.2h.01"/>',
    lock: '<rect x="4.5" y="10.5" width="15" height="10" rx="2.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    settings: '<circle cx="12" cy="12" r="3.2"/><path d="M12 2.5v3M12 18.5v3M21.5 12h-3M5.5 12h-3M18.7 5.3l-2.1 2.1M7.4 16.6l-2.1 2.1M18.7 18.7l-2.1-2.1M7.4 7.4L5.3 5.3"/>',
    bell: '<path d="M18 9a6 6 0 1 0-12 0c0 6-2.5 7.5-2.5 7.5h17S18 15 18 9z"/><path d="M10.2 20.5a2.2 2.2 0 0 0 3.6 0"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5.2l3.2 2"/>',
    calendar: '<rect x="3.5" y="5.5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3.5v4M16 3.5v4"/>',
    user: '<circle cx="12" cy="8" r="3.8"/><path d="M4.5 20.5c0-3.9 3.4-6.5 7.5-6.5s7.5 2.6 7.5 6.5"/>',
    camera: '<path d="M21.5 18.5a2 2 0 0 1-2 2h-15a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2h3l1.7-2.5h6.6L17.5 7.5h2a2 2 0 0 1 2 2z"/><circle cx="12" cy="13.5" r="3.5"/>',
    mic: '<rect x="9" y="2.5" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3.5"/>',
    edit: '<path d="M16.5 3.7l3.8 3.8L8.4 19.4l-4.9 1.1 1.1-4.9z"/>',
    chartLine: '<path d="M4 4v16h16"/><path d="M7.5 15l3.5-4 3 2.5 4.5-6"/>',
    chartBar: '<path d="M4 20h16"/><path d="M7.5 20v-6M12 20V8M16.5 20v-9"/>',
    list: '<path d="M8.5 7h11M8.5 12h11M8.5 17h11"/><circle cx="4.5" cy="7" r="1.2" fill="currentColor" stroke="none"/><circle cx="4.5" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="4.5" cy="17" r="1.2" fill="currentColor" stroke="none"/>',
    book: '<path d="M4 5.5A2 2 0 0 1 6 3.5h5v16H6a2 2 0 0 0-2 2z"/><path d="M20 5.5a2 2 0 0 0-2-2h-5v16h5a2 2 0 0 1 2 2z"/>',
    card: '<rect x="2.5" y="5.5" width="19" height="13" rx="2.5"/><path d="M2.5 10h19"/>',
    cart: '<path d="M2.5 4h2.6l2.4 11h9.6l2.4-8H6.3"/><circle cx="9.5" cy="19.5" r="1.5"/><circle cx="17" cy="19.5" r="1.5"/>',
    ban: '<circle cx="12" cy="12" r="8.5"/><path d="M6 18L18 6"/>',
    infinity: '<path d="M7.5 15.5a3.5 3.5 0 1 1 0-7c3.5 0 5.5 7 9 7a3.5 3.5 0 1 0 0-7c-3.5 0-5.5 7-9 7z"/>',
    gift: '<rect x="3" y="9.5" width="18" height="11" rx="2"/><path d="M3 13.5h18M12 9.5v11"/><path d="M12 9.5S10.5 3.5 7.8 3.5a2.2 2.2 0 0 0 0 4.4h4.2zM12 9.5s1.5-6 4.2-6a2.2 2.2 0 0 1 0 4.4H12z"/>',
    bulb: '<path d="M9 17.5a6 6 0 1 1 6 0v1.5H9z"/><path d="M10 21.5h4"/>',
    target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/>',
    sparkle: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M18.5 16.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
    inbox: '<path d="M3.5 13h4l1.5 3h6l1.5-3h4"/><path d="M5.6 4.5h12.8l3.1 8.5v5a2 2 0 0 1-2 2H4.5a2 2 0 0 1-2-2v-5z"/>',
    dot: '<circle cx="12" cy="12" r="5"/>',
    home: '<path d="M3.5 10.5L12 3.5l8.5 7"/><path d="M5.5 9.5v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-9"/>',

    /* ---------- Предметные ---------- */
    dumbbell: '<path d="M6.5 8.5h2v7h-2zM15.5 8.5h2v7h-2z"/><path d="M3.5 10.5v3M20.5 10.5v3M8.5 12h7"/>',
    trophy: '<path d="M7 3.5h10v6a5 5 0 0 1-10 0z"/><path d="M7 5.5H4.2v1.8A3.3 3.3 0 0 0 7.5 10.6M17 5.5h2.8v1.8a3.3 3.3 0 0 1-3.3 3.3"/><path d="M12 14.5v3M8.5 20.5h7"/>',
    flame: '<path d="M12 21c3.6 0 6.3-2.5 6.3-6 0-4.3-4-6.2-4-9.7-2.4 1-3.6 3-3.6 5.2 0 1.1-.8 1.8-1.6 1.8-.9 0-1.5-.7-1.6-1.7-1.1 1.2-1.8 2.9-1.8 4.4 0 3.5 2.7 6 6.3 6z"/>',
    pill: '<rect x="2.6" y="8.8" width="18.8" height="6.4" rx="3.2" transform="rotate(-45 12 12)"/><path d="M9.2 9.2l5.6 5.6"/>',
    plate: '<circle cx="12" cy="12" r="6.5"/><circle cx="12" cy="12" r="2.5"/>',
    utensils: '<path d="M6 3v7a2.5 2.5 0 0 0 5 0V3M8.5 12.5V21"/><path d="M17 3c-1.7 1-2.5 3-2.5 5.5S15.3 12 17 12.5V21"/>',
    scale: '<path d="M4.5 3.5h15a1 1 0 0 1 1 1v15a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1z"/><path d="M8.5 7.5h7"/><path d="M12 11v3"/>',
    coach: '<circle cx="9" cy="6.5" r="3"/><path d="M3.5 20.5c0-3.3 2.5-5.5 5.5-5.5s5.5 2.2 5.5 5.5"/><path d="M16.5 9.5h4.5v4a2.2 2.2 0 0 1-4.5 0z"/>',
    robot: '<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 4.5V8M9 13h.01M15 13h.01M9.5 17h5"/><circle cx="12" cy="3.5" r="1.3"/>',
    gem: '<path d="M6 3.5h12l3.5 5.5L12 20.5 2.5 9z" stroke-linejoin="round"/><path d="M2.5 9h19M9 3.5L7 9l5 11.5M15 3.5l2 5.5-5 11.5"/>',
    run: '<circle cx="14.5" cy="4.5" r="2"/><path d="M5 20.5l3-5 3.5-2-1-4.5-3.5 2-1.5 3"/><path d="M11.5 9l3.5 2.5.5 4.5 3 4.5"/>',
    heart: '<path d="M12 20.5S3.5 15 3.5 9.2A4.7 4.7 0 0 1 12 6.4a4.7 4.7 0 0 1 8.5 2.8c0 5.8-8.5 11.3-8.5 11.3z"/>',
    droplet: '<path d="M12 3.2s6 6.3 6 10.3a6 6 0 0 1-12 0c0-4 6-10.3 6-10.3z"/>',
    moon: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5z"/>',
    sunrise: '<path d="M2.5 19h19M12 3.5v4M5.6 9.6L4.2 8.2M18.4 9.6l1.4-1.4"/><path d="M7 15a5 5 0 0 1 10 0"/>',
    bowl: '<path d="M3 11.5h18a9 9 0 0 1-18 0z"/><path d="M9 7.5c0-1 .8-1.5.8-2.5M13 7c0-1.2 1-1.8 1-3"/>',
    apple: '<path d="M12 7.5c-1-1.2-2.4-1.8-3.8-1.8C5.7 5.7 4 8 4 11.2c0 4 2.8 9.3 5.2 9.3 1 0 1.8-.6 2.8-.6s1.8.6 2.8.6c2.4 0 5.2-5.3 5.2-9.3 0-3.2-1.7-5.5-4.2-5.5-1.4 0-2.8.6-3.8 1.8z"/><path d="M12 7.5V4.5c0-1 .9-2 2.5-2"/>',
    flag: '<path d="M5.5 21V3.5M5.5 4.5h12l-2.5 4 2.5 4h-12"/>',
    bandage: '<rect x="2.2" y="8.8" width="19.6" height="6.4" rx="3.2" transform="rotate(-45 12 12)"/><path d="M9.2 9.2l5.6 5.6M11 11h.01M13 13h.01M13 11h.01M11 13h.01"/>',
    level: '<path d="M5 20v-4M12 20V10M19 20V4"/>',
    body: '<circle cx="12" cy="4.5" r="2.5"/><path d="M12 8v8M12 8L6.5 11M12 8l5.5 3M12 16l-3 5.5M12 16l3 5.5"/>',
    ring: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5a8.5 8.5 0 0 1 8.5 8.5"/>'
  };

  // Псевдонимы: одно и то же понятие в разных местах называлось по-разному.
  var ALIAS = {
    "arrow-right": "arrow",
    "chevron-right": "chevron",
    x: "close",
    tick: "check",
    add: "plus",
    remove: "minus",
    notification: "bell",
    gear: "settings",
    profile: "user",
    food: "utensils",
    workout: "dumbbell",
    fire: "flame",
    weight: "scale",
    premium: "gem",
    ai: "sparkle",
    stats: "chartLine"
  };

  /**
   * Возвращает разметку иконки.
   * @param {string} name  имя из PATHS или ALIAS
   * @param {object} [opts] { size:number=20, rotate:number=0, cls:string, stroke:number }
   * @returns {string} строка <svg …> либо пустая строка, если имя неизвестно
   */
  function icon(name, opts) {
    opts = opts || {};
    var key = ALIAS[name] || name;
    var body = PATHS[key];
    if (!body) {
      // Неизвестное имя не должно ронять экран: тихо отдаём пустоту,
      // но в консоли оставляем след, чтобы опечатка нашлась при разработке.
      if (window.console && console.warn) console.warn("App.icon: нет иконки " + name);
      return "";
    }

    var size = opts.size || 20;
    var cls = "icon" + (opts.cls ? " " + opts.cls : "");
    var style = opts.rotate ? ' style="transform:rotate(' + opts.rotate + 'deg)"' : "";

    return (
      '<svg class="' + cls + '" width="' + size + '" height="' + size + '" ' +
      'viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="' + (opts.stroke || 1.75) + '" ' +
      'stroke-linecap="round" stroke-linejoin="round" ' +
      'aria-hidden="true" focusable="false"' + style + ">" +
      body +
      "</svg>"
    );
  }

  // Публикуем на App, если ядро уже загружено, иначе — в глобальную область
  // (порядок скриптов в index.html гарантирует, что app.js идёт первым,
  // но фолбэк оставляем на случай изменения порядка).
  if (window.App) {
    window.App.icon = icon;
    window.App.iconNames = Object.keys(PATHS);
  }
  window.AppIcon = icon;
})();
