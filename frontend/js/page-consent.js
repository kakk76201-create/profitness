/*
 * page-consent.js — экран согласий на обработку персональных данных (152-ФЗ).
 *
 * Показывается ДО первого экрана приложения, когда сервер отвечает
 * needs_consent: true (GET /consent). Это и первый запуск, и смена редакции
 * документов: тогда экран увидят и те, кто пользуется приложением давно.
 *
 * Четыре отметки — по числу разных оснований обработки, их нельзя объединять
 * в одну «я со всем согласен»:
 *   pd           — обработка персональных данных (обязательно);
 *   terms        — соглашение, оферта и 18+ (обязательно);
 *   health       — данные о здоровье, ст. 10 (добровольно);
 *   cross_border — передача за границу, ст. 12 (добровольно).
 *
 * Ни одна галочка не проставлена заранее — ни обязательная, ни добровольная:
 * отметка, сделанная за человека, согласием не является. Под каждым пунктом
 * написано, что он даёт и что перестанет работать без него.
 *
 * Тот же экран открывается при ошибке 403 consent_required — когда функция
 * упёрлась в выключенное согласие (см. App.requestConsent).
 */
(function () {
  "use strict";

  function L(ru, en) {
    return App.pick(ru, en);
  }

  function esc(s) {
    return App.escapeHtml(s == null ? "" : String(s));
  }

  function icon(name, opts) {
    return App.icon ? App.icon(name, opts) : "";
  }

  // Описание пунктов. Порядок фиксированный: сначала обязательные.
  var ITEMS = [
    {
      key: "pd",
      required: true,
      ru: "Согласен на обработку моих персональных данных",
      en: "I consent to the processing of my personal data",
      hintRu: "Профиль Telegram, анкета, дневник, вес и тренировки — чтобы приложение работало.",
      hintEn: "Telegram profile, questionnaire, diary, weight and workouts — so the app can work.",
      doc: "consent"
    },
    {
      key: "terms",
      required: true,
      ru: "Мне есть 18 лет, принимаю соглашение и оферту",
      en: "I am 18+, I accept the terms and the offer",
      hintRu: "Приложение не оказывает медицинских услуг и не заменяет врача.",
      hintEn: "The app is not a medical service and does not replace a doctor.",
      doc: "terms"
    },
    {
      key: "health",
      required: false,
      ru: "Согласен на обработку данных о здоровье",
      en: "I consent to processing of health data",
      hintRu: "Травмы, ограничения, цикл. Без этого не работают тренер и трекер цикла.",
      hintEn: "Injuries, limitations, cycle. Without it the trainer and cycle tracker are off.",
      doc: "consent"
    },
    {
      key: "cross_border",
      required: false,
      ru: "Согласен на передачу данных ИИ-сервису (США)",
      en: "I consent to sending data to the AI service (USA)",
      hintRu: "Распознавание еды и советы работают на серверах в США. Без этого фото, голос и советы ИИ выключены.",
      hintEn: "Food recognition and advice run on servers in the USA. Without it photo, voice and AI advice are off.",
      doc: "privacy"
    }
  ];

  // Текущее состояние галочек на экране.
  var picked = {};
  var busy = false;

  /** Ссылки на документы: с сервера, иначе — относительные пути. */
  function docUrl(name) {
    var docs = (App.consent && App.consent.docs) || {};
    return docs[name] || "/legal/" + name + ".html";
  }

  /** Открыть документ: внутри Telegram — его браузером, иначе обычной вкладкой. */
  function openDoc(name) {
    var url = docUrl(name);
    try {
      if (App.tg && typeof App.tg.openLink === "function") {
        App.tg.openLink(url);
        return;
      }
    } catch (e) {
      /* ниже обычное открытие */
    }
    window.open(url, "_blank");
  }

  function rowHtml(item) {
    var on = !!picked[item.key];
    return (
      '<div class="cns-row' + (on ? " cns-row--on" : "") + '" data-key="' + item.key + '">' +
      '<button type="button" class="cns-check" role="checkbox" aria-checked="' +
      (on ? "true" : "false") +
      '" data-check="' + item.key + '">' +
      (on ? icon("check", { size: 16 }) : "") +
      "</button>" +
      '<div class="cns-row__text">' +
      '<span class="cns-row__title">' +
      esc(L(item.ru, item.en)) +
      (item.required
        ? ' <span class="cns-req">' + esc(L("обязательно", "required")) + "</span>"
        : "") +
      "</span>" +
      '<span class="cns-row__hint">' + esc(L(item.hintRu, item.hintEn)) + "</span>" +
      '<button type="button" class="cns-doc" data-doc="' + item.doc + '">' +
      esc(L("Читать документ", "Read the document")) +
      "</button>" +
      "</div>" +
      "</div>"
    );
  }

  function template() {
    var rows = "";
    for (var i = 0; i < ITEMS.length; i++) {
      rows += rowHtml(ITEMS[i]);
    }
    var version = (App.consent && App.consent.version) || "";
    var date = (App.consent && App.consent.date) || "";
    return (
      '<section class="page page-consent">' +
      '<header class="cns-head">' +
      '<span class="eyebrow">' + esc(L("Прежде чем начать", "Before we start")) + "</span>" +
      "<h1>" + esc(L("Ваши данные", "Your data")) + "</h1>" +
      '<p class="cns-lead">' +
      esc(
        L(
          "Приложение считает калории и ведёт дневник — для этого оно хранит ваши записи. Отметьте, на что вы согласны: без обязательных пунктов приложение не работает, необязательные можно выключить сейчас или потом в профиле.",
          "The app counts calories and keeps your diary — that is why it stores your records. Mark what you agree to: the app needs the required items, the optional ones you can switch off now or later in your profile."
        )
      ) +
      "</p>" +
      "</header>" +
      '<div class="cns-list">' + rows + "</div>" +
      '<p class="cns-error" id="cnsError" hidden></p>' +
      '<div class="cns-actions">' +
      '<button type="button" class="btn btn--cta btn-block" id="cnsAccept">' +
      esc(L("Продолжить", "Continue")) +
      "</button>" +
      '<p class="cns-meta">' +
      esc(L("Редакция ", "Version ") + version + L(" от ", " of ") + date) +
      "</p>" +
      "</div>" +
      "</section>"
    );
  }

  /** Перерисовать одну строку после переключения галочки. */
  function syncRow(key) {
    var row = document.querySelector('.cns-row[data-key="' + key + '"]');
    var box = document.querySelector('[data-check="' + key + '"]');
    if (!row || !box) return;
    var on = !!picked[key];
    row.classList.toggle("cns-row--on", on);
    box.setAttribute("aria-checked", on ? "true" : "false");
    box.innerHTML = on ? icon("check", { size: 16 }) : "";
  }

  function showError(msg) {
    var el = document.getElementById("cnsError");
    if (!el) return;
    el.textContent = msg || "";
    el.hidden = !msg;
  }

  /** Сохранить решения и вернуться туда, откуда пришли. */
  function onAccept(btn) {
    if (busy) return;
    var missing = [];
    for (var i = 0; i < ITEMS.length; i++) {
      if (ITEMS[i].required && !picked[ITEMS[i].key]) missing.push(ITEMS[i]);
    }
    if (missing.length) {
      App.haptic("error");
      showError(
        L(
          "Отметьте обязательные пункты — без них приложение не может обрабатывать ваши записи.",
          "Please tick the required items — without them the app cannot process your records."
        )
      );
      return;
    }

    busy = true;
    btn.disabled = true;
    var old = btn.textContent;
    btn.textContent = L("Сохраняем…", "Saving…");
    showError("");

    App.api
      .saveConsent(picked)
      .then(function (state) {
        App.consent = state;
        App.haptic("success");
        App.afterConsent();
      })
      .catch(function (err) {
        busy = false;
        btn.disabled = false;
        btn.textContent = old;
        showError(err.message);
      });
  }

  App.registerPage("consent", {
    onShow: function (view) {
      busy = false;
      // НИ ОДНОЙ галочки заранее: согласие, проставленное за человека, — не
      // согласие (ст. 9 ч. 1 152-ФЗ требует конкретного и однозначного).
      // Исключение — возврат на экран из середины работы (ошибка 403): там
      // человек уже что-то решал раньше, и стирать его выбор нельзя, иначе он
      // застрянет на экране без кнопки «назад».
      var state = (App.consent && App.consent.state) || {};
      var back = !!App.state.consentReturn;
      picked = {};
      for (var i = 0; i < ITEMS.length; i++) {
        var it = ITEMS[i];
        picked[it.key] = back && !!(state[it.key] && state[it.key].granted);
      }

      view.innerHTML = template();

      // Слушатель вешаем на саму страницу, а не на #view: контейнер живёт
      // всё время работы приложения, и при втором заходе обработчиков стало
      // бы два — клик отрабатывал бы дважды.
      var page = view.querySelector(".page-consent");
      page.addEventListener("click", function (ev) {
        var doc = ev.target.closest ? ev.target.closest("[data-doc]") : null;
        if (doc) {
          App.haptic("light");
          openDoc(doc.getAttribute("data-doc"));
          return;
        }
        var row = ev.target.closest ? ev.target.closest(".cns-row") : null;
        if (row) {
          var key = row.getAttribute("data-key");
          picked[key] = !picked[key];
          syncRow(key);
          App.haptic("selection");
          showError("");
          return;
        }
        var accept = ev.target.closest ? ev.target.closest("#cnsAccept") : null;
        if (accept) onAccept(accept);
      });
    }
  });
})();
