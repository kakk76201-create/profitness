# ТЗ: фича «AI-тренер» для Telegram Mini App «Калории»

Документ — техническое задание для исполнителей. Все имена (таблицы, поля, маршруты, функции, классы, файлы) — обязательные к использованию, чтобы параллельные агенты стыковались без согласований.

---

## 0. Рамка и ключевые решения

**Позиционирование:** «фитнес-тренер с трекером калорий». Тренер — отдельный раздел `trainer` (страницы вне таббара), вход — карточка на «Тренировках» + (should) кнопка на главной дневника. Всё, что перечислено владельцем в п.1–10, — must, входит в MVP.

**Архитектурные решения (не обсуждаются исполнителями):**

1. **Отдельные бэкенд-модули**, чтобы не толкаться в `main.py` / `ai_service.py`:
   - `backend/trainer.py` — `router = APIRouter(prefix="/trainer", tags=["trainer"])`, все маршруты. В `main.py` одна вставка `app.include_router(trainer.router)` выше `app.mount("/", StaticFiles(...))`. `get_current_user` берём из `backend.auth`, `get_db` из `backend.database` — циклического импорта нет.
   - `backend/trainer_schemas.py` — все Pydantic-схемы тренера (в `schemas.py` только одна точечная правка, см. §4.9).
   - `backend/trainer_ai.py` — промпты и функции ИИ; используют `ai_service._run_text_completion`, `ai_service.AIError`, `ai_service._normalize_lang`, `_coerce_int/_coerce_float`.
   - `backend/trainer_logic.py` — чистые функции без побочных эффектов: 1RM, объём, прогрессия, PR, стрик, раскрытие программы по неделям, подбор альтернатив, контекст для питания.
   - `backend/trainer_exercises_seed.py` — встроенная библиотека упражнений (список dict), `backend/trainer_seed.py` — идемпотентный загрузчик `ensure_exercises(db)` (upsert по `slug`).
   - `backend/trainer_notify.py` — обогащение текста существующего напоминания TrainingReminder и (should) пуши недельного разбора; в `notifications.py` — вставка ~10 строк.
2. **Ноль ALTER'ов существующих таблиц.** Все данные тренера — в 11 новых таблицах (create_all). Связь с `workouts` — через `trainer_sessions.workout_id` (Integer, nullable), а не через новую колонку в `workouts`. Следствие: `test_schema.py` менять не нужно; `test_smoke.py` — только добавить модели в цикл проверки существования таблиц.
3. **Библиотека упражнений — источник правды.** ИИ выбирает упражнения ТОЛЬКО из каталога по `slug` (в промпт передаётся отфильтрованный список). Техника/ошибки генерируются ИИ один раз на упражнение и язык, кэшируются в `trainer_exercises.technique_json`. Незнакомый slug от ИИ → фаззи-матч по имени → иначе создание строки с `created_by_ai=True`.
4. **ИИ генерирует шаблон недели + план периодизации**, а не 8 недель целиком (иначе 15–20k токенов и ненадёжный JSON). Бэкенд раскрывает шаблон в `weeks × days` строк `trainer_program_days` детерминированно (`trainer_logic.expand_program`). Это и «программа сохраняется в БД целиком», и экономия токенов, и стабильность упражнений от недели к неделе (главная жалоба на Fitbod: «каждый раз новая тренировка, не вижу прогресса»).
5. **Адаптация между сессиями — правила, не ИИ** (прозрачно, бесплатно, мгновенно): «повторы сначала, потом вес» (Hevy Trainer), откат −10% после двух неудач (StrongLifts), отзыв «легко/норм/тяжело» как модификатор. Каждое изменение объясняется одной строкой на языке пользователя. **ИИ — раз в неделю** («недельный разбор») с ограниченным набором допустимых правок, которые бэкенд валидирует и применяет к следующей неделе.
6. **Таймер отдыха** — на фронте, по `Date.now()` (переживает сворачивание WebView), хаптик + звук WebAudio по нулю; пуш ботом по окончании отдыха — should (см. §1).
7. **Завершённая сессия → строка `Workout`** (type по типу сессии, `duration_min`, `calories_burned` по MET через `fitness.estimate_calories_burned`, `description="Тренер: <название дня>"`). Дневник ничего не знает о тренере — баланс дня работает автоматически.
8. Всё под `Depends(subscription.require_premium)`. Фронт — `App.requirePremium` только как заглушка.

---

## 1. Матрица фич

| Фича (лучшая реализация у конкурента) | MVP (must) | Итерация 2 (should) | Не делаем (won't) и почему |
|---|---|---|---|
| Онбординг-анкета 6–10 экранов (Fitbod, Hevy Trainer, Freeletics) | 9 шагов, данные тела из профиля, не спрашиваем повторно | Импорт истории CSV из Strong/Hevy | 35 экранов Caliber — отток |
| Генерация программы под цель/уровень/оборудование/дни/время/травмы (Fitbod, Hevy Trainer) | Шаблон недели + периодизация 4/6/8 нед., сплит выбирает ИИ (full body ≤3 дн., upper/lower 4, PPL 5–6) | Gym Profiles (дом/зал/поездка) — второй набор оборудования | «Каждый день новая тренировка» (Fitbod) — ломает прогрессию |
| Экран «Сегодня» с одной кнопкой Start + чипы «сегодня 20 мин / без оборудования» (Fitbod Session Mods, Freeletics Adapt) | «Сегодня»: план дня, «Начать/Продолжить», день отдыха, лента недели | Adapt Session (длительность 15/30/45, только вес тела) — перестройка дня правилами без ИИ | Camera rep-counter (Zing) — нет смысла в WebView |
| Таблица подходов SET / PREVIOUS / KG / REPS / ✓ с автозаполнением (Hevy/Strong) | Да, в точности; предзаполнение из `TrainerExerciseState` и прошлого сета | RPE/RIR колонка как опция в настройках; тап по «прошлый раз» копирует | — |
| Типы подходов (разминочный / рабочий / дроп / до отказа) | warmup + work (разминочные не в объём и PR) | drop / failure | — |
| Авто-таймер отдыха с −15/+15/skip, звук/вибро, уведомление в фоне | Sticky-плашка внизу, `Date.now()`, хаптик+бип, не перекрывает поля | Пуш ботом «Отдых закончен, следующий: …» через `trainer_rest_alerts` + отдельный job 5 с | Live Activity / Watch — нет платформы |
| Замена упражнения из-за «занят тренажёр / нет оборудования / болит» с 3–5 альтернативами на ту же мышцу (GymBot, Hevy) | Да, правилами из библиотеки, с запоминанием замены в состоянии упражнения | ИИ-подсказка альтернативы по свободному тексту | — |
| Пропуск упражнения, «исключить навсегда» (Fitbod Never) | Да | Recommend More/Less | — |
| Суперсеты | Нет | Поле `superset_group` уже заложено в модели; UI + авто-скролл | — |
| Разминка/заминка в тренировке | Блоки warmup/cooldown в каждом дне (генерирует ИИ, при отсутствии — инжектим дефолт) | Авто-разминочные сеты 40/60/80% для первого штангового упражнения | — |
| Завершение с итогом: длительность, объём, ккал, PR-карточки (Hevy/Fitbod) | Да + «добавлено в дневник» + связь с балансом дня | Шаринг-картинка в Telegram (`shareToStory`) | Соцлента, лидерборды — не наш продукт |
| Отзыв после тренировки (легко/норм/тяжело + заметка) → адаптация (Zing, Freeletics) | Да, правилами; показываем «Учёл: …» | «Сколько повторов осталось в запасе» после последнего сета (RiR) | Max Effort Day |
| PR в реальном времени: max вес, расч. 1RM (Epley ≤12 повт.), объём сета, повторы, время | Да, тост при отметке сета + таблица рекордов | Set Records (лучший вес на 1..12 повт.) | — |
| Прогресс: история, объём/сеты по мышцам за неделю, стрик, графики 1RM (Hevy free) | Да: стрик по неделям, объём по группам мышц, SVG-графики 1RM/вес по упражнению, календарь-лента | Strength Score (единая цифра, Caliber/Fitbod) относительно веса тела и пола — данные есть | Heat-map тела 3D |
| Библиотека упражнений RU/EN, мышца, оборудование, техника, ошибки | ~140 упражнений в seed; техника/ошибки от ИИ один раз, кэш | GIF/видео (нужны ассеты) | 1600 видео с ракурсов |
| Недельный отчёт/разбор тренера (Fitbod Workout Report, FitnessAI «объясняет каждое изменение») | ИИ-разбор недели с правками на следующую, кнопка «Применить» | Автопуш «Разбор готов» по понедельникам, месячный отчёт | Fitbod Flex годовой |
| Напоминание о тренировке дня через бота | Через существующий TrainingReminder: онбординг создаёт/обновляет его; текст пуша дополняется «Сегодня по плану: День 2 — Верх, 45 мин» | Пуш-превью накануне вечером | — |
| Связь с питанием (нет ни у кого) | Совет дня «тренировка / отдых» (ИИ, кэш 1/день), «Что съесть?» знает про сегодняшнюю тренировку и белок; недельный разбор видит ккал/белок/вес | Авто-корректировка цели ккал в тренировочный день (+N) через adaptive | — |
| Чат с тренером (Zing Coach, Freeletics Coach+) | Нет | «Спросить тренера» в контексте упражнения (GPT-4o, enforce_ai) | Личности коуча |
| Comeback после паузы (Freeletics) | Нет | Если >10 дней без сессий — облегчённая сессия (−15% веса, −1 сет) | — |
| Мобильность в дни отдыха | Нет | Короткая сессия mobility из библиотеки | — |
| Прогрессия bodyweight через варианты (отжимания с колен → обычные) | Только +повторы | Цепочки `progression_next_slug` в seed | — |
| Плейт-калькулятор | Нет | Кнопка в поле веса | — |
| Челленджи с инвайт-ссылкой | Нет | Нет (пока) | Соц-механики — не ядро |

**Учёт ограничений Telegram Mini App:** нет фоновых таймеров → таймер по абсолютному времени + пуши ботом (should); узкий экран → одна колонка, таблица сетов 4 колонки; свайп-закрытие → `App.tg.enableClosingConfirmation()` на время сессии, `disableVerticalSwipes` уже включён; кеш ассетов → обязательный bump `?v=s30 → ?v=s31`; сессия автосохраняется на сервере после каждого сета, «Продолжить тренировку» на «Сегодня».

---

## 2. Пользовательский путь и экраны

### 2.1 Вход
Страница «Тренировки» (уже премиум) → новая карточка `coachCardHtml()` сверху формы: иконка 🧑‍🏫, заголовок «AI-тренер», подзаголовок (если профиля нет — «Персональная программа за 2 минуты», если есть — «Сегодня: День 2 — Верх тела · 45 мин» или «День отдыха»), стрелка `›`. Тап → `App.state.trainerOrigin="workouts"; App.navigate("trainer")`.

Страница `trainer` при `onShow`: `requirePremium` → `GET /trainer/overview` → если `profile.onboarding_completed=false` → `App.navigate("trainer-onboarding")`; если нет активной программы → экран «Программа не создана» с кнопкой «Собрать программу»; иначе — «Сегодня».

### 2.2 Онбординг тренера (`trainer-onboarding`), 9 шагов, прогресс-бар `.tr-onb-progress`
Каждый шаг — карточка с заголовком, вариантами (`.tr-option` — крупные тапабельные карточки с эмодзи, одиночный/множественный выбор), «Далее»/«Назад». Ответы копятся в `state.answers`, отправляются один раз на шаге 9. Значения — коды, отправляемые в `POST /trainer/profile`.

1. **Цель** (один): `loss` 🔥 «Похудеть», `muscle` 💪 «Набрать мышцы», `strength` 🏋️ «Стать сильнее», `endurance` 🏃 «Выносливость», `tone` ✨ «Тонус и здоровье». Дефолт подсвечиваем по `profile.diet_goal` (loss→loss, gain→muscle).
2. **Уровень**: `beginner` «Новичок — меньше 6 месяцев», `intermediate` «Средний — 6 мес.–2 года регулярно», `advanced` «Опытный — 2+ года».
3. **Где и с чем** (один + чипы): `gym` «Зал: штанги, тренажёры», `home_dumbbells` «Дома с гантелями/резинками», `bodyweight` «Только вес тела». Доп. чипы (множ.): `pullup_bar`, `bands`, `bench`, `kettlebell`, `barbell` (для home), `cardio_machine`. Подсказка-«education quip»: «Программа строится только из доступного оборудования».
4. **Сколько раз в неделю**: 2/3/4/5/6 (`.chip`) + дни недели `.wk-rem-day` (предзаполняем: 2→Пн Чт, 3→Пн Ср Пт, 4→Пн Вт Чт Пт, 5→Пн–Пт, 6→Пн–Сб). Хинт: «≤3 дня — full body, 4 — верх/низ, 5–6 — push/pull/legs».
5. **Длительность сессии**: 20/30/45/60/75/90 мин.
6. **Ограничения и травмы** (множ.): `knee` 🦵 «Колени», `lower_back` «Поясница», `shoulder` «Плечи», `wrist` «Запястья/локти», `neck` «Шея», `hip` «Тазобедренные», `pregnancy` «Беременность/после родов», `heart_bp` «Давление/сердце», `none` «Нет». Поле «Опишите подробнее» (≤300 симв.). Обязательный текст `.rec-disclaimer`: «Тренер не врач. При травмах и хронических состояниях проконсультируйтесь со специалистом».
7. **На что сделать акцент** (множ., опционально): `glutes`, `core`, `back`, `chest`, `shoulders`, `arms`, `legs`, `none`.
8. **Напоминание**: тумблер + время (дефолт 18:00). Хинт: «Бот напомнит в дни тренировок и покажет план дня».
9. **Длина программы**: 4 / 6 / 8 недель (дефолт 6; для beginner подсвечиваем 4). Сводка ответов + проверка профиля: если в `App.state.profile` нет `weight/gender/age` — жёлтая плашка «Заполните вес и пол в аккаунте — веса будут точнее» (не блокирует). Кнопка **«Собрать программу»**.

Далее: `POST /trainer/profile` → `POST /trainer/program/generate` с экраном ожидания (`.tr-gen-wait`: 3 сменяющихся строки «Подбираем упражнения… Расставляем нагрузку… Проверяем баланс мышц…», 15–40 с) → ошибка 502/429 → карточка `.wk-error` с «Повторить» (программа не создана, профиль сохранён).

### 2.3 Превью программы (`trainer-program`, режим `preview` сразу после генерации)
Шапка: название («Верх/Низ — 6 недель»), сплит, `summary` от ИИ («Почему так»), список недель `.tr-week-strip` с фазами (База · Рост · Пик · Разгрузка), раскрывающиеся дни (`.acc-fold`): для каждого — название, мышцы, длительность, список упражнений «Жим гантелей лёжа · 3×8–12 · 14 кг · отдых 90 с». Кнопки: «Начать программу» (→ `trainer`), «Пересобрать» (повторный `generate`, лимит heavy 2/мин, 30/день). В обычном режиме та же страница = «Программа» с прогресс-баром недели, статусами дней (✓ / пропущено / план), «Архивировать и создать новую».

### 2.4 «Сегодня» (страница `trainer`, `.tr-today`)
- Шапка `.sub-head` «← Назад» (→ `App.state.trainerOrigin || "workouts"`), заголовок «Тренер», подзаголовок «Неделя 2 из 6 · Верх/Низ».
- **Карточка дня** `.tr-today-card`:
  - если есть незавершённая сессия → «Тренировка в процессе · 18 мин · 2/6» + кнопка **«Продолжить»**;
  - если сегодня тренировочный день → бейдж «Сегодня по плану», название дня, «45 мин · 6 упражнений · Грудь, Спина, Плечи», превью 3 первых упражнений, кнопка **«Начать»**;
  - если день отдыха → «День отдыха 😌», «Следующая — ср: День 2 — Низ», ссылка «Всё равно потренироваться» (старт следующего дня плана);
  - если план недели выполнен → «Неделя закрыта 🎉», кнопка «Дополнительная тренировка».
- **Лента недели** `.tr-week-strip`: Пн–Вс, точка-статус (`--done` ✓, `--today`, `--planned`, `--skipped`, `--rest`).
- **Стрик** `.tr-streak`: «🔥 3 недели подряд · 2 из 3 на этой неделе» + прогресс-бар.
- **Питание сегодня** `.tr-nutrition-card` (ленивая загрузка `GET /trainer/nutrition/today`): «Тренировочный день: цель 2100 ккал, белок 140 г — съедено 60 г. За 1–2 ч до тренировки: … После: …» + кнопка «Что съесть?» → `App.navigate("diary")` с открытием suggest (should: deep-link).
- **Баннер разбора** (если `pending_review=true`): «Недельный разбор готов к запуску» → `trainer-progress#review`.
- Быстрые ссылки `.tr-links`: Программа · Прогресс · Упражнения · Настройки (= онбординг в режиме редактирования).

### 2.5 Выполнение тренировки (`trainer-session`)
- Sticky-шапка `.tr-session-head`: ✕ (→ confirm «Отменить тренировку? Прогресс не сохранится» → `abandon`), название дня, секундомер сессии (от `started_at`), «2/6 упражнений».
- **Блок разминки** `.tr-block--warmup` (свёрнутый после выполнения): чек-лист пунктов «5 мин лёгкое кардио», «Вращения плечами ×15» — отметка ✓ пишет `set_type=warmup` (не в объём/PR).
- **Карточки упражнений** `.tr-ex-card` (порядок = `order_index`):
  - заголовок: название (тап → лист «Техника» из `GET /trainer/exercises/{id}`), бейдж мышцы, цель «3×8–12 · 40 кг · отдых 90 с · RPE 7», подсказка `note` от ИИ («локти под 45°»), заметка адаптации если есть («+2.5 кг: в прошлый раз 12/12/12»);
  - меню ⋯ (`Trainer.sheet`): Заменить (лист «Причина: занят тренажёр / нет оборудования / болит / другое» → список 3–5 альтернатив с мышцей и оборудованием) · Пропустить · Техника · История · Исключить навсегда;
  - **таблица сетов** `.tr-set-table`: заголовок `# | Прошлый раз | кг | повт | ✓`. Строка `.tr-set-row`: номер (Р для разминочного), «40×10» (тап копирует в поля), `input.tr-set-input[type=number,inputmode=decimal]`, `input[inputmode=numeric]`, кнопка ✓ 44px. Для `measure_type=reps` колонка кг скрыта; для `time` — секунды + inline-таймер ▶. Поля предзаполнены целью (или прошлым результатом). ✓ → `POST /trainer/session/{id}/set` → строка `--done` (зелёная), `App.haptic("success")`, при `prs` тост `.tr-pr-toast` «🏆 Рекорд: 1RM 53 кг», старт отдыха. Повторный тап по ✓ снимает отметку (`is_done=false`). «+ подход» дублирует последнюю строку. Свайп/долгий тап — удалить (should; в MVP кнопка ✕ у добавленных вручную).
- **Плашка отдыха** `.tr-rest-bar` (fixed над таббарной зоной, не перекрывает инпуты за счёт `padding-bottom` у списка): «Отдых 1:30» с кольцом прогресса, кнопки −15 / +15 / Пропустить; по нулю — `haptic("success")`, бип, плашка «Следующий: подход 3». Не стартует между разными упражнениями (Caliber: тренажёр может быть занят), только после сета.
- **Блок заминки** `.tr-block--cooldown` — чек-лист.
- Низ: **«Завершить тренировку»** (`btn-cta btn-block`, активна при ≥1 рабочем сете ✓); если есть невыполненные упражнения → confirm «Осталось 2 упражнения. Завершить?».
- Автосохранение: каждый сет — сервер; при повторном открытии `GET /trainer/session/active` восстанавливает всё. `App.tg.enableClosingConfirmation()` в `onShow`, `disableClosingConfirmation()` в `onHide`.

### 2.6 Итог и отзыв (внутри `trainer-session`, экран `finish`)
`POST /trainer/session/{id}/finish` → экран `.tr-finish`: заголовок «Готово! 💪», сетка `.tr-stat-grid`: длительность, подходы, объём (кг), ккал «→ добавлено в дневник», список PR-карточек `.tr-record-card`. Ниже **отзыв**: чипы `.tr-feedback-chip` «😮‍💨 Слишком легко» / «👍 В самый раз» / «🥵 Слишком тяжело», поле заметки, кнопка «Отправить». `POST /trainer/session/{id}/feedback` → карточка `.tr-adapt-card` «Учёл на следующий раз:» + строки изменений («Жим гантелей: 14 → 16 кг — все подходы по 12», «Тяга: оставляем 50 кг, цель 10 повторов»). Кнопки «К прогрессу», «На главную тренера». Пропуск отзыва разрешён (сессия остаётся `completed`, адаптация по умолчанию `ok`).

### 2.7 Прогресс (`trainer-progress`)
- Сводка: стрик, тренировок за 4 недели, объём этой недели vs прошлой (`.rep-stat--up/--down`).
- **Объём по группам мышц за 7 дней** `.tr-muscle-bars`: горизонтальные бары «Грудь 12 подходов» с рекомендуемой зоной 10–20 (цвет muted/cta/danger).
- **Рекорды** `.tr-record-list`: упражнение → 1RM, max вес, повторы, дата.
- **Графики** `.tr-chart` (SVG, `Trainer.lineChart`): выбор упражнения (топ-5 по частоте) и метрики (1RM / вес / объём), период 4 нед / 3 мес / всё.
- **История сессий** `.tr-history`: карточки «Пн 2 сен · День 1 — Верх · 48 мин · 3 200 кг · 2 PR», тап → детали (сеты по упражнениям, отзыв).
- **Недельный разбор** `.tr-review-card`: кнопка «Разобрать неделю» (heavy) → результат: summary, «Получилось», «Что менять», «Питание», список изменений с чекбоксами → «Применить к следующей неделе». Если уже есть за эту неделю — показываем сохранённый.

### 2.8 Упражнения (`trainer-exercise`)
Список с фильтрами-чипами (мышца, оборудование), поиск по имени; карточка → детальный лист: описание (шаги, ключевые моменты, ошибки, дыхание, безопасность — генерируются при первом открытии, показываем скелетон «Тренер пишет технику…»), вкладка «История» (последние сеты, рекорды, график 1RM), кнопка «Исключить из программ» / «Вернуть».

---

## 3. Модель данных (`backend/models.py`, добавить в конец; все — новые таблицы)

Соглашения проекта: `Integer PK autoincrement`, `telegram_id BigInteger FK users.telegram_id index`, даты — `String` ISO, вложенное — JSON в `Text`, флаги — `Boolean`, `created_at DateTime default=datetime.utcnow`. ALTER'ов нет → `run_migrations` не трогаем.

```python
class TrainerExercise(Base):               # библиотека (глобальная, без telegram_id)
    __tablename__ = "trainer_exercises"
    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String, unique=True, index=True)          # "db_bench_press"
    name_ru = Column(String); name_en = Column(String)
    muscle_group = Column(String, index=True)  # chest|back|shoulders|biceps|triceps|quads|hamstrings|glutes|calves|core|full_body|cardio|mobility
    secondary_muscles_json = Column(Text, nullable=True)     # ["triceps","shoulders"]
    equipment = Column(String, index=True)     # barbell|dumbbell|machine|cable|bodyweight|band|kettlebell|pullup_bar|bench|cardio_machine|none
    category = Column(String)                  # compound|isolation|cardio|mobility|stretch
    measure_type = Column(String)              # reps_weight|reps|time|distance
    difficulty = Column(Integer, default=1)    # 1..3
    is_unilateral = Column(Boolean, default=False)
    contraindications_json = Column(Text, nullable=True)     # ["knee","lower_back"] — коды ограничений
    alternatives_json = Column(Text, nullable=True)          # ["slug1","slug2"] ручные альтернативы
    progression_next_slug = Column(String, nullable=True)    # should: цепочка bodyweight
    technique_json = Column(Text, nullable=True)  # {"ru": {steps,cues,mistakes,breathing,safety,muscles_text}, "en": {...}}
    technique_status = Column(String, default="none")        # none|ready|failed
    created_by_ai = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TrainerProfile(Base):
    __tablename__ = "trainer_profiles"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), unique=True, index=True)
    goal = Column(String)                      # loss|muscle|strength|endurance|tone
    level = Column(String)                     # beginner|intermediate|advanced
    equipment = Column(String)                 # gym|home_dumbbells|bodyweight
    equipment_extra_json = Column(Text, nullable=True)       # ["pullup_bar","bands",...]
    days_per_week = Column(Integer)            # 2..6
    preferred_weekdays = Column(String)        # CSV "0,2,4" (Пн=0)
    session_minutes = Column(Integer)          # 20|30|45|60|75|90
    program_weeks = Column(Integer, default=6) # 4|6|8
    limitations_json = Column(Text, nullable=True)           # ["knee","lower_back"]
    limitations_text = Column(Text, nullable=True)
    focus_json = Column(Text, nullable=True)                 # ["glutes","core"]
    rest_default_sec = Column(Integer, default=90)
    reminder_enabled = Column(Boolean, default=False)
    reminder_time = Column(String, nullable=True)            # "HH:MM"
    reminder_id = Column(Integer, nullable=True)             # -> training_reminders.id (без FK)
    onboarding_completed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TrainerProgram(Base):
    __tablename__ = "trainer_programs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    status = Column(String, index=True)        # active|completed|archived
    title = Column(String)
    split_type = Column(String)                # full_body|upper_lower|ppl|custom
    goal = Column(String); level = Column(String); equipment = Column(String)   # снимок профиля
    weeks = Column(Integer); days_per_week = Column(Integer)
    start_date = Column(String); end_date = Column(String)   # ISO
    summary = Column(Text, nullable=True)                    # «почему так» от ИИ
    periodization_json = Column(Text, nullable=True)         # [{"week":1,"phase":"base","weight_pct":100,"sets_delta":0}, ...]
    tips_json = Column(Text, nullable=True)                  # советы ИИ по программе
    ai_model = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TrainerProgramDay(Base):                 # источник правды по плану (раскрытый)
    __tablename__ = "trainer_program_days"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    program_id = Column(Integer, ForeignKey("trainer_programs.id"), index=True)
    week = Column(Integer); day_index = Column(Integer)      # 1..weeks, 1..days_per_week
    weekday = Column(Integer, nullable=True)                 # 0..6 из preferred_weekdays
    scheduled_date = Column(String, nullable=True, index=True)
    title = Column(String)                     # "Верх тела"
    session_type = Column(String)              # strength|cardio|mixed|mobility
    duration_min = Column(Integer)
    focus_muscles_json = Column(Text, nullable=True)
    warmup_json = Column(Text)                 # [{"slug","exercise_id","sets","reps","time_sec","note"}]
    exercises_json = Column(Text)              # [{"slug","exercise_id","sets","reps_min","reps_max","rest_sec","start_weight_kg","rpe","tempo","note","order"}]
    cooldown_json = Column(Text)
    adjustments_json = Column(Text, nullable=True)           # правки недельного разбора/отзыва к этому дню
    status = Column(String, default="planned", index=True)   # planned|done|skipped
    session_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class TrainerSession(Base):
    __tablename__ = "trainer_sessions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    program_id = Column(Integer, nullable=True, index=True)
    program_day_id = Column(Integer, nullable=True)
    date = Column(String, index=True)          # ISO дата старта (локальная дата клиента, см. §4)
    status = Column(String, index=True)        # in_progress|completed|abandoned
    title = Column(String); session_type = Column(String)
    week = Column(Integer, nullable=True); day_index = Column(Integer, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    duration_min = Column(Integer, nullable=True)
    total_sets = Column(Integer, default=0); total_reps = Column(Integer, default=0)
    total_volume_kg = Column(Float, default=0.0)
    calories_burned = Column(Integer, nullable=True)
    workout_id = Column(Integer, nullable=True)              # -> workouts.id, без FK
    feedback = Column(String, nullable=True)                 # easy|ok|hard
    feedback_note = Column(Text, nullable=True)
    adaptation_json = Column(Text, nullable=True)            # {"changes":[...], "lines":[...]}
    prs_json = Column(Text, nullable=True)                   # [{"exercise_id","type","value","prev"}]
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TrainerSessionExercise(Base):
    __tablename__ = "trainer_session_exercises"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    session_id = Column(Integer, ForeignKey("trainer_sessions.id"), index=True)
    exercise_id = Column(Integer, ForeignKey("trainer_exercises.id"), index=True)
    block = Column(String, default="main")     # warmup|main|cooldown
    order_index = Column(Integer)
    planned_sets = Column(Integer); planned_reps_min = Column(Integer, nullable=True)
    planned_reps_max = Column(Integer, nullable=True); planned_weight_kg = Column(Float, nullable=True)
    planned_time_sec = Column(Integer, nullable=True); planned_rest_sec = Column(Integer, nullable=True)
    planned_rpe = Column(Integer, nullable=True)
    superset_group = Column(Integer, nullable=True)          # should
    status = Column(String, default="pending") # pending|done|skipped|replaced
    replaced_from_exercise_id = Column(Integer, nullable=True)
    replace_reason = Column(String, nullable=True)           # busy|no_equipment|pain|other
    note = Column(Text, nullable=True)         # подсказка ИИ/адаптации для UI
    created_at = Column(DateTime, default=datetime.utcnow)

class TrainerSetLog(Base):
    __tablename__ = "trainer_set_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    session_id = Column(Integer, index=True); session_exercise_id = Column(Integer, index=True)
    exercise_id = Column(Integer, index=True)
    date = Column(String, index=True)
    set_index = Column(Integer)                # 1..N
    set_type = Column(String, default="work")  # warmup|work|drop|failure
    weight_kg = Column(Float, nullable=True); reps = Column(Integer, nullable=True)
    time_sec = Column(Integer, nullable=True); rpe = Column(Float, nullable=True)
    is_done = Column(Boolean, default=False)
    volume_kg = Column(Float, default=0.0)     # weight*reps для work
    est_1rm = Column(Float, nullable=True)     # Epley, только reps<=12
    is_pr = Column(Boolean, default=False)
    pr_types_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TrainerRecord(Base):                     # одна строка на (пользователь, упражнение, тип) — текущий лучший
    __tablename__ = "trainer_records"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    exercise_id = Column(Integer, index=True)
    record_type = Column(String)               # max_weight|est_1rm|max_reps|set_volume|max_time
    value = Column(Float)
    weight_kg = Column(Float, nullable=True); reps = Column(Integer, nullable=True)
    set_log_id = Column(Integer, nullable=True); session_id = Column(Integer, nullable=True)
    date = Column(String)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TrainerExerciseState(Base):              # состояние прогрессии пользователя по упражнению
    __tablename__ = "trainer_exercise_states"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    exercise_id = Column(Integer, index=True)
    working_weight_kg = Column(Float, nullable=True)
    target_reps_min = Column(Integer, nullable=True); target_reps_max = Column(Integer, nullable=True)
    target_time_sec = Column(Integer, nullable=True)
    rest_sec = Column(Integer, nullable=True)
    last_result = Column(String, nullable=True)   # success|partial|fail
    success_streak = Column(Integer, default=0); fail_streak = Column(Integer, default=0)
    last_session_id = Column(Integer, nullable=True); last_date = Column(String, nullable=True)
    excluded = Column(Boolean, default=False)     # «никогда не предлагать»
    preferred_alternative_id = Column(Integer, nullable=True)  # запомненная замена
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TrainerWeeklyReview(Base):
    __tablename__ = "trainer_weekly_reviews"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    program_id = Column(Integer, nullable=True); week = Column(Integer, nullable=True)
    week_start = Column(String, index=True); week_end = Column(String)
    stats_json = Column(Text)                  # собранный контекст (sessions, volume, muscles, kcal, protein, weight)
    review_json = Column(Text)                 # ответ ИИ (нормализованный)
    applied = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class TrainerDailyTip(Base):                   # кэш совета по питанию на день
    __tablename__ = "trainer_daily_tips"
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    date = Column(String, index=True); kind = Column(String)  # training|rest
    lang = Column(String, default="ru")
    tip_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
```

**Связи с существующими сущностями**
- `User` ← все таблицы по `telegram_id`; `TrainerProfile` 1:1. Данные тела (вес/рост/пол/возраст), `diet_goal`, `daily_goal_kcal`, `target_proteins`, `language` — из `User`, не дублируем.
- `Workout` ← `TrainerSession.workout_id`. При `finish`: `Workout(telegram_id, date=session.date, type=map_type(session_type), duration_min, calories_burned, description="Тренер: "+title)`. `map_type`: strength/mixed→`"strength"`, cardio→`"cardio"`, mobility→`"yoga"`. При `abandon` — Workout не создаётся; при удалении сессии (should) — удаляем связанный Workout. Дневник (`GET /diary/{date}`) суммирует `Workout.calories_burned` → `net_calories` работает без изменений.
- `TrainingReminder` ← `TrainerProfile.reminder_id`. `POST /trainer/profile` создаёт/обновляет строку (weekdays = `preferred_weekdays`, time = `reminder_time`, enabled = `reminder_enabled`); при выключении — `enabled=False`, не удаляем.
- `DiaryEntry`, `WeightLog` — только чтение для контекста ИИ (недельный разбор, совет дня, «Что съесть?»).
- **Право на забвение:** в `account_delete_data` (main.py ~3390) добавить в кортеж: `TrainerProfile, TrainerProgram, TrainerProgramDay, TrainerSession, TrainerSessionExercise, TrainerSetLog, TrainerRecord, TrainerExerciseState, TrainerWeeklyReview, TrainerDailyTip` (у всех есть `telegram_id`). `TrainerExercise` — глобальная, не удаляется.
- `test_smoke.py`: добавить эти же модели в цикл проверки существования таблиц.

**Seed библиотеки** (`trainer_exercises_seed.py`, `EXERCISES: list[dict]`, ~140 записей, поля = колонки `TrainerExercise` без technique). Покрытие: грудь 10 (штанга/гантели/тренажёр/отжимания 3 варианта), спина 12 (тяги, подтягивания, австралийские, гиперэкстензия), плечи 8, бицепс 6, трицепс 6, квадрицепсы 10 (присед со штангой/гоблет/воздушный, выпады, жим ногами, болгарские, разгибания), задняя/ягодицы 10 (становая, румынская, ягодичный мост, hip thrust, сгибания, махи), икры 3, кор 10 (планки, скручивания, dead bug, bird dog, велосипед), full_body/cardio 12 (берпи, jumping jacks, mountain climbers, дорожка, велотренажёр, скакалка, гребля, «быстрая ходьба»), mobility/warmup 15, stretch 12. Обязательно: `warmup_general_5min` (time, none), `stretch_full_body_5min` (time, none) — инжектируются при отсутствии разминки/заминки. Загрузчик `trainer_seed.ensure_exercises(db)`: upsert по `slug` (обновляет имена/поля, не трогает `technique_json`), вызывается в `lifespan` после `init_db()` в try/except.

---

## 4. API (`backend/trainer.py`, префикс `/trainer`, все — `Depends(subscription.require_premium)`)

Схемы — `backend/trainer_schemas.py` (Pydantic v2, `ConfigDict(from_attributes=True)` где выдаём ORM). Ошибки: 402 (не премиум), 404 (чужой/несуществующий объект), 409 (конфликт состояния: уже есть активная сессия / нет активной программы), 429 (rate limit), 502 (AIError/RuntimeError). Порядок объявления: `/session/active` ДО `/session/{session_id}`.

### 4.1 Обзор и профиль
- `GET /trainer/overview → TrainerOverviewOut {profile: TrainerProfileOut|None, program: TrainerProgramBriefOut|None, today: TrainerTodayOut|None, streak: TrainerStreakOut, week: [TrainerWeekDayOut×7], active_session_id: int|None, pending_review: bool}` — `trainer_overview`. Один вызов для страницы «Сегодня».
- `GET /trainer/profile → TrainerProfileOut` (404 если нет).
- `POST /trainer/profile` `TrainerProfileIn {goal, level, equipment, equipment_extra: List[str]=[], days_per_week: int (2..6), preferred_weekdays: List[int], session_minutes: int, program_weeks: int=6, limitations: List[str]=[], limitations_text: str|None (≤300), focus: List[str]=[], reminder_enabled: bool=False, reminder_time: str|None}` → `TrainerProfileOut` (все поля + `id, onboarding_completed, reminder_id`). Валидация через `field_validator` по спискам кодов; `preferred_weekdays` длина == `days_per_week` (иначе 422). Побочный эффект: upsert TrainingReminder (`_weekdays_to_csv` и `_normalize_time` — скопировать в `trainer_logic` как чистые функции, чтобы не импортировать main).

### 4.2 Программа
- `POST /trainer/program/generate` `TrainerGenerateIn {regenerate_note: str|None}` → `TrainerProgramOut {id, title, status, split_type, weeks, days_per_week, start_date, end_date, current_week, summary, tips: List[str], periodization: List[TrainerWeekPhaseOut{week, phase, weight_pct, sets_delta, label}], days: List[TrainerProgramDayOut]}`. `ratelimit.enforce_heavy` + `enforce_ai`. 409 если профиль не завершён. Сбой или непригодный ответ ИИ → 200 с программой из шаблона базы знаний (`ai_model = "knowledge-template"`, §5.1); 502 — только если не собрался и шаблон. Предыдущая `active` → `archived`. `TrainerProgramDayOut {id, week, day_index, weekday, scheduled_date, title, session_type, duration_min, focus_muscles: List[str], warmup: List[TrainerPlanItemOut], exercises: List[TrainerPlanItemOut], cooldown: List[...], status, session_id}`; `TrainerPlanItemOut {exercise_id, slug, name_ru, name_en, muscle_group, equipment, measure_type, sets, reps_min, reps_max, time_sec, rest_sec, target_weight_kg, rpe, tempo, note}`.
- `GET /trainer/program?program_id=` → активная (или указанная) `TrainerProgramOut`; 404 если нет.
- `POST /trainer/program/{program_id}/archive` → `{ok: true}`.

### 4.3 «Сегодня» и сессия
- `GET /trainer/today?date=YYYY-MM-DD` → `TrainerTodayOut {date, is_training_day: bool, kind: "planned"|"rest"|"week_done"|"no_program", day: TrainerProgramDayOut|None (следующий `planned` день по (week, day_index)), next_date: str|None, active_session: TrainerSessionOut|None}`. Дата — от клиента (`App.todayStr()`), как во всём проекте.
- `POST /trainer/session/start` `TrainerSessionStartIn {program_day_id: int|None, date: str}` → `TrainerSessionOut`. 409 если уже есть `in_progress` (вернуть её id в detail). Создаёт `TrainerSession` + `TrainerSessionExercise` для warmup/main/cooldown: цели из `exercises_json` с наложением `TrainerExerciseState` (working_weight, target reps), модификаторов недели (`periodization[week]`: deload → weight×0.85, sets−1), `adjustments_json` дня; если у упражнения `excluded` или есть `preferred_alternative_id` — подставляем альтернативу. Для каждого упражнения — `previous: List[TrainerPrevSetOut {set_index, weight_kg, reps, time_sec}]` из последней завершённой сессии с этим упражнением.
  `TrainerSessionOut {id, date, status, title, session_type, week, day_index, started_at, finished_at, duration_min, total_sets, total_reps, total_volume_kg, calories_burned, workout_id, feedback, feedback_note, adaptation: TrainerAdaptationOut|None, prs: List[TrainerPrOut], exercises: List[TrainerSessionExerciseOut]}`;
  `TrainerSessionExerciseOut {id, exercise: TrainerExerciseBriefOut{id, slug, name_ru, name_en, muscle_group, equipment, measure_type}, block, order_index, planned_sets, planned_reps_min, planned_reps_max, planned_weight_kg, planned_time_sec, planned_rest_sec, planned_rpe, status, note, previous: List[TrainerPrevSetOut], sets: List[TrainerSetOut]}`;
  `TrainerSetOut {id, set_index, set_type, weight_kg, reps, time_sec, rpe, is_done, is_pr, pr_types: List[str]}`.
- `GET /trainer/session/active` → `TrainerSessionOut|null` (200 с `null` телом через `Optional`).
- `GET /trainer/session/{session_id}` → `TrainerSessionOut`.
- `POST /trainer/session/{session_id}/set` `TrainerSetIn {session_exercise_id, set_index (1..12), set_type="work", weight_kg: float|None (0..500), reps: int|None (0..100), time_sec: int|None, rpe: float|None, is_done: bool=true}` → `TrainerSetSaveOut {set: TrainerSetOut, prs: List[TrainerPrOut{type, value, prev_value, exercise_name_ru, exercise_name_en}], rest_sec: int, exercise_status: str}`. Upsert по (session_exercise_id, set_index). Только `in_progress`. Считает `volume_kg`, `est_1rm`, PR через `trainer_logic.detect_prs` (только `work`, `is_done`), обновляет `TrainerRecord`. Если все planned сеты done → `exercise_status="done"`.
- `DELETE /trainer/session/{session_id}/set/{set_id}` → `{ok}` (пересчёт PR не делаем в MVP; рекорды «истинны» по последнему сохранению — should: пересчёт).
- `GET /trainer/exercises/{exercise_id}/alternatives?reason=busy|no_equipment|pain|other&session_id=` → `TrainerAlternativesOut {items: List[TrainerExerciseBriefOut], reason}` — `trainer_logic.alternatives_for(...)`: та же `muscle_group`, оборудование ∈ доступного пользователю (для `no_equipment` — исключая оборудование исходного), для `pain` — исключая упражнения с пересечением `contraindications` исходного и профиля + понижение `difficulty`, исключая `excluded`, порядок: ручные `alternatives_json` → та же `category` → остальное; максимум 5.
- `POST /trainer/session/{session_id}/exercise/{sex_id}/replace` `TrainerReplaceIn {new_exercise_id, reason, remember: bool=true}` → `TrainerSessionExerciseOut`. Помечает старую строку `status="replaced"`, создаёт новую с тем же `order_index` и целями (вес — из state новой или `None`); при `remember` пишет `preferred_alternative_id` в state исходного (для `pain` — ещё и `excluded=True` до конца программы; should: срок).
- `POST /trainer/session/{session_id}/exercise/{sex_id}/skip` → `{ok, status:"skipped"}`.
- `POST /trainer/session/{session_id}/exercise/add` `{exercise_id, sets:3, reps_min, reps_max}` → `TrainerSessionExerciseOut` (should, но роут дешёвый — включить в MVP-бэкенд).
- `POST /trainer/session/{session_id}/finish` `TrainerFinishIn {duration_min: int|None, note: str|None}` → `TrainerFinishOut {session: TrainerSessionOut, summary: {duration_min, total_sets, total_reps, total_volume_kg, calories_burned, exercises_done, exercises_skipped}, prs: List[TrainerPrOut], workout_id}`. 400 если нет ни одного `work` сета `is_done`. Действия: `finished_at`, `duration_min = data.duration_min or clamp(elapsed, 10, max(planned*1.5, 30))`, итоги `trainer_logic.session_totals`, калории `fitness.estimate_calories_burned(map_type, duration_min, user.weight)`, **создание `Workout`** и запись `workout_id`, `TrainerProgramDay.status="done", session_id`, обновление `TrainerExerciseState` (working_weight = медиана веса done-сетов, `last_result` по правилу §5.5), `status="completed"`.
- `POST /trainer/session/{session_id}/feedback` `TrainerFeedbackIn {feedback: easy|ok|hard, note: str|None, rpe: int|None}` → `TrainerAdaptationOut {changes: List[TrainerChangeOut{exercise_id, name_ru, name_en, kind: weight|reps|sets|keep|deload, old_value, new_value}], lines: List[str] (на языке пользователя), message: str}`. Разрешён для `completed`; повторный вызов перезаписывает (пересчёт от снимка состояния «до», хранимого в `adaptation_json.before`).
- `POST /trainer/session/{session_id}/abandon` → `{ok}` (`status="abandoned"`, день остаётся `planned`, Workout не создаётся).
- `GET /trainer/sessions?limit=20&offset=0` → `TrainerSessionsOut {items: List[TrainerSessionBriefOut{id, date, title, session_type, duration_min, total_volume_kg, total_sets, calories_burned, prs_count, feedback}], total}`.

### 4.4 Прогресс и библиотека
- `GET /trainer/progress?exercise_id=&period=4w|3m|all` → `TrainerProgressOut {streak: {weeks, this_week_done, this_week_goal}, totals_4w: {sessions, volume_kg, sets, minutes}, week_compare: {this: {...}, prev: {...}}, muscle_volume_7d: List[{muscle_group, sets, volume_kg, target_min:10, target_max:20}], records: List[TrainerRecordOut{exercise: Brief, record_type, value, weight_kg, reps, date}], top_exercises: List[Brief], chart: {exercise_id, points: List[{date, est_1rm, max_weight, volume}]}|None}`.
- `GET /trainer/exercises?muscle=&equipment=&q=&limit=100` → `{items: List[TrainerExerciseBriefOut + difficulty, category, technique_status, excluded]}`.
- `GET /trainer/exercises/{exercise_id}?technique=1` → `TrainerExerciseOut {..brief, secondary_muscles, contraindications, technique: TrainerTechniqueOut{steps, cues, mistakes, breathing, safety, muscles_text}|None, technique_status, excluded}`. При `technique=1` и отсутствии кэша для `user.language` → `enforce_ai` + `trainer_ai.exercise_technique` → сохраняем в `technique_json[lang]`; при AIError отдаём 200 с `technique=None, technique_status="failed"` (не 502 — экран должен открыться).
- `GET /trainer/exercises/{exercise_id}/history` → `{records: [...], sessions: List[{date, session_id, sets: List[TrainerSetOut]}], points: [{date, est_1rm, max_weight, volume}]}`.
- `POST /trainer/exercises/{exercise_id}/exclude` `{excluded: bool}` → `{ok, excluded}`.

### 4.5 Недельный разбор и питание
- `POST /trainer/review/weekly` `TrainerReviewIn {week_start: str|None}` (дефолт — понедельник текущей недели; если сегодня Пн/Вт и за текущую неделю 0 сессий — прошлая) → `TrainerWeeklyReviewOut {id, week_start, week_end, stats: {...}, review: {summary, wins: List[str], issues: List[str], nutrition: List[str], changes: List[TrainerReviewChangeOut{id(index), type: weight_pct|sets|swap|rest_sec|deload_next_week, exercise_id, exercise_name_ru/en, value, new_exercise_id|None, reason}], next_week_focus, motivation}, applied, disclaimer}`. `enforce_heavy`+`enforce_ai`. 409 если за неделю 0 сессий. Один разбор на (tid, week_start): повторный вызов — регенерация (перезапись).
- `GET /trainer/review/latest` → последний `TrainerWeeklyReviewOut|null`.
- `POST /trainer/review/{review_id}/apply` `{change_ids: List[int]}` → `{applied: List[int], lines: List[str]}` — `trainer_logic.apply_review_changes`: правит `exercises_json`/`adjustments_json` дней следующей недели активной программы и `TrainerExerciseState`; `applied=True`.
- `GET /trainer/nutrition/today?date=` → `TrainerNutritionTipOut {date, kind: training|rest, headline, calories_note, protein_note, pre_workout: str|None, post_workout: str|None, hydration, tips: List[str], numbers: {goal_kcal, eaten_kcal, burned_kcal, protein_goal, protein_eaten}}`. Кэш `TrainerDailyTip` по (tid, date, kind, lang); `kind=training` если на дату есть planned day с `scheduled_date==date` или сессия completed/in_progress. `enforce_ai` только при промахе кэша.

### 4.6 Изменения в существующих файлах (точечные)
- `main.py`: (а) `from backend import trainer, trainer_seed`; (б) в `lifespan` после `init_db()`: `try: with SessionLocal() as db: trainer_seed.ensure_exercises(db) except Exception: logger.exception(...)`; (в) `app.include_router(trainer.router)` перед `app.mount`; (г) кортеж в `account_delete_data`; (д) `food_suggest`: добавить `db: Session = Depends(get_db)`, строку `training_context = trainer_logic.today_training_context(db, user.telegram_id, data.date or today, user.language or "ru")` и передать `training_context=training_context` в `suggest_food`; в ответ `FoodSuggestOut.training_note = training_context`.
- `schemas.py`: `FoodSuggestIn.date: Optional[str] = None`, `FoodSuggestOut.training_note: Optional[str] = None`.
- `ai_service.py`: `suggest_food(..., training_context: str | None = None)` — если задан, добавить в `parts` строку «Контекст тренировок: …» / «Training context: …» и правило в system-промпт: «если сегодня была/будет тренировка — предпочитай белковые варианты и учитывай сожжённые калории».
- `notifications.py`: в `_process_training_reminder` после `text = _msg(...)`: `try: from backend import trainer_notify; text = trainer_notify.decorate_training_reminder(db, tid, today, lang, text) except Exception: logger.exception(...)`. Тексты в `trainer_notify._TEXTS` (свой словарь), чтобы не править `_TEXTS` notifications.

### 4.7 Rate-limit и премиум — сводка
| Маршрут | Премиум | Лимит ИИ |
|---|---|---|
| все `/trainer/*` | да | — |
| `POST /program/generate` | да | heavy + ai |
| `GET /exercises/{id}?technique=1` (промах кэша) | да | ai |
| `POST /review/weekly` | да | heavy + ai |
| `GET /nutrition/today` (промах кэша) | да | ai |
| остальное | да | без ИИ |

---

## 5. ИИ (`backend/trainer_ai.py`) и логика (`backend/trainer_logic.py`)

Все функции ИИ: пары `*_SYSTEM_PROMPT` / `*_SYSTEM_PROMPT_EN` с одинаковыми JSON-ключами, `_pick_prompt`, user_prompt из `parts`, `_run_text_completion(system, user, log_tag, max_tokens=...)`, нормализация → при пустом результате `raise AIError("empty", raw=...)`. Общие правила безопасности во всех system-промптах: «Ты тренер, не врач: НЕ ставишь диагнозы, НЕ назначаешь лекарства/добавки; при боли — прекратить упражнение и обратиться к врачу; при беременности/давлении/сердце — низкая интенсивность, без задержки дыхания, без упражнений лёжа на спине после 1 триместра, рекомендовать согласовать с врачом». Маршрут добавляет `disclaimer` там, где есть медицинский контекст (разбор, техника при ограничениях).

### 5.1 `generate_program(profile: dict, body: dict, catalog: str, lang) -> dict` — tag `trainer_program`, `max_tokens=4000`
**Схема генерации: база знаний → ИИ → аудит → запасной шаблон.** Программа строится не «из головы» модели, а по базе знаний `backend/trainer_knowledge.py` (исследования, позиционные заявления и известные программы; описание и источники — [docs/TRAINER_KNOWLEDGE.md](TRAINER_KNOWLEDGE.md)):
1. **База знаний задаёт каркас.** `trainer_knowledge.prompt_brief(profile, lang, catalog_map)` (≤3500 символов) идёт в user_prompt блоком «БАЗА ЗНАНИЙ» / «KNOWLEDGE BASE» **перед каталогом**: схема недели под анкету и пример недели с паттерном движения у каждого слота (пример собирается только из упражнений каталога промпта — исключённых пользователем там нет), повторы / RIR / отдых цели, диапазоны недельного объёма по мышцам, прогрессия и план периодизации, правила ограничений. System-промпт делает блок обязательным: отступать можно только из-за ограничений здоровья или отсутствия оборудования в каталоге, упражнения — из каталога под паттерны слотов.
2. **ИИ персонализирует** выбор упражнений, стартовые веса и подсказки в этих рамках; ответ проходит `normalize_program`.
3. **Аудит и безопасные правки** (`trainer_ai._audit_and_fix` на `trainer_knowledge.audit_week`), программа модели целиком не переписывается: недопустимое упражнение (противопоказано, не рекомендуется правилами ограничения, нет оборудования) заменяется через `pick_exercise` на то же движение (подходы и мера модели сохраняются); пределы интенсивности беременности/давления (повторы, RPE, отдых, удержание); многосуставные раньше изоляции; ±1 подход до диапазона `volume_targets`, крупной мышце ниже минимума — не больше двух новых упражнений (в день той же зоны тела); тяга ≥ жима. Любая добавка проверяется бюджетом времени дня (`estimate_day_seconds`), потолком подходов за тренировку и тем, что соседние мышцы не выходят за диапазон. Периодизация модели заменяется `periodization_for(profile)`, если она пустая, без фаз роста/пика, меняет вес вне разгрузки или противоречит правилам разгрузки уровня (новичку до 8 недель без плановой разгрузки, накопление не дольше лимита уровня и не короче 3 недель). Первой подсказкой в `tips` ставится `method_tip(profile, lang)` (на чём основана программа), дубль убирается. Замечания до/после и правки пишутся в лог и в поле `knowledge {source, split_id, fixes, issues}` результата.
4. **Запасной шаблон.** Если ИИ упал (любое исключение движка) или ответ непригоден (`AIError` нормализации), `generate_program` собирает программу без модели: `build_week` + `periodization_for` + title/summary/tips шаблона на языке пользователя, тот же `normalize_program` и тот же аудит; `ai_model = "knowledge-template"`. Маршрут отвечает 200; `AIError` (→ 502) — только если не собрался и шаблон. Лимиты 429 и 409 без анкеты не меняются.

Вход в user_prompt: цель, уровень, оборудование (+extra), дни/нед и их дни недели, минуты, недели, ограничения (коды + текст), фокус, тело (пол, возраст, вес, рост, `diet_goal`, `daily_goal_kcal`), «известные рабочие веса» (из `TrainerExerciseState`, если есть — при пересборке), `regenerate_note`, блок базы знаний и **каталог**: строки `slug | name_en | muscle | equipment | measure | difficulty`, отфильтрованный `trainer_logic.catalog_for_prompt` (по оборудованию, без контриндицированных, без excluded; ≤110 строк).
Ответ:
```json
{"title":"...", "split_type":"upper_lower", "summary":"почему так, 2-3 предложения",
 "week_template":{"days":[{"day_index":1,"title":"Верх тела","session_type":"strength","focus_muscles":["chest","back"],"duration_min":45,
   "warmup":[{"slug":"warmup_general_5min","time_sec":300,"note":"..."},{"slug":"arm_circles","sets":1,"reps":15}],
   "exercises":[{"slug":"db_bench_press","sets":3,"reps_min":8,"reps_max":12,"rest_sec":90,"start_weight_kg":14,"rpe":7,"tempo":"2-0-2","note":"локти 45°"}],
   "cooldown":[{"slug":"chest_stretch","time_sec":30}]}]},
 "periodization":[{"week":1,"phase":"base","weight_pct":100,"sets_delta":0},{"week":2,"phase":"build","weight_pct":100,"sets_delta":0}],
 "tips":["...","..."]}
```
Правила промпта: база знаний из user_prompt обязательна (схема недели и `split_type`, объём по мышцам, повторы, RIR и отдых, план периодизации; отступать — только из-за ограничений здоровья или оборудования); только slug из каталога, под паттерны слотов примера недели; ровно `days_per_week` дней; упражнений и подходов столько, чтобы уложиться в длительность сессии; базовые перед изолирующими; подходов тяги за неделю не меньше, чем жимов; запасные правила на случай, если блока базы нет (до 3 дней full_body, 4 — upper_lower, 5–6 — ppl; 10–20 подходов на мышцу, новичку 6–10; сила 3–6 повторов, остальные цели 6–15, отдых ≥60 с); всегда разминка 5–8 мин (общая + 1–2 специфичных) и заминка 3–5 мин; разгрузка — `weight_pct 85`, `sets_delta -1`, остальные недели 100 и 0 (вес по неделям ведёт `next_targets`); `start_weight_kg` консервативно по уровню/полу/весу тела, для bodyweight `null`; при ограничениях — заменять (колени: без глубоких приседов/выпадов с прыжком, поясница: без становой с пола и наклонов со штангой, плечи: без жима из-за головы и т.д.; подробно — в базе знаний); нельзя ставить два тяжёлых упражнения на низ спины в один день.
Нормализация `normalize_program(data, catalog_map)`: незнакомый slug → `difflib.get_close_matches` по name_en/name_ru → иначе `TrainerExercise(created_by_ai=True, muscle_group из ответа или "full_body")`; sets 1–6, reps 1–30, rest 20–300, weight 0–300; если `days` ≠ days_per_week → усечь/502; день без warmup/cooldown → инжект дефолтов; контриндицированные → `alternatives_for`; periodization дополнить до `weeks` (default phase=base; последняя deload).
Раскрытие: `trainer_logic.expand_program(template, periodization, profile, start_date) -> list[dict days]`, `scheduled_date` по `preferred_weekdays` начиная с ближайшего ≥ today.

### 5.2 `exercise_technique(exercise: dict, limitations: list, lang) -> dict` — tag `trainer_technique`, `max_tokens=700`
Ответ `{"steps":[3-6], "cues":[3-5], "mistakes":[3-5], "breathing":"...", "safety":"...", "muscles_text":"..."}`. Кэш в `technique_json[lang]`, `technique_status`. Без ограничений пользователя в промпте (кэш общий) — только общая безопасность.

### 5.3 `weekly_review(stats: dict, lang) -> dict` — tag `trainer_review`, `max_tokens=1500`
Контекст `stats` (собирает `trainer_logic.collect_week_stats(db, tid, week_start)`): программа (цель, неделя N из M, фаза), план vs факт (дни запланированы/выполнены/пропущены), сессии (дата, длительность, объём, отзыв, заметка), по упражнениям (план вес/повт → факт, success/partial/fail, PR), сеты по группам мышц, средние ккал/белок за неделю из `DiaryEntry` vs `daily_goal_kcal`/`target_proteins`, вес: первый/последний `WeightLog` за 14 дней, `diet_goal`, ограничения, разрешённые slug для swap (тот же каталог).
Ответ:
```json
{"summary":"...", "wins":["..."], "issues":["..."], "nutrition":["..."],
 "changes":[{"type":"weight_pct","exercise_slug":"bb_squat","value":-10,"reason":"два раза не добил"},
            {"type":"sets","exercise_slug":"lat_pulldown","value":1,"reason":"спина недогружена: 6 сетов"},
            {"type":"swap","exercise_slug":"bb_deadlift","new_slug":"rdl_db","reason":"жалоба на поясницу"},
            {"type":"deload_next_week","value":1,"reason":"3 тяжёлых отзыва подряд"}],
 "next_week_focus":"...", "motivation":"..."}
```
Правила: ≤5 изменений; `weight_pct` в [−15, +10]; `sets` ±1; `swap` только на slug из списка той же группы мышц; менять одну переменную на упражнение; если пропущено ≥50% — не усложнять, предложить сократить дни; если отзывы «легко» и все success — +вес/сеты; питание — только с опорой на цифры (дефицит/белок), без диет и лекарств. Валидация в `normalize_review`: неизвестные типы/slug — отбросить, значения — clamp.

### 5.4 `nutrition_day_tip(ctx: dict, lang) -> dict` — tag `trainer_nutrition`, `max_tokens=500`
Контекст: kind (training/rest), тип и длительность/ккал тренировки, время (план/сделано), `diet_goal`, `daily_goal_kcal`, `target_proteins`, съедено сегодня (ккал/Б), вес. Ответ `{"headline","calories_note","protein_note","pre_workout"|null,"post_workout"|null,"hydration","tips":[2-3]}`. Правило: не менять цель калорий, а объяснять распределение; в день отдыха — про белок и восстановление; цифры только из контекста.

### 5.5 Правила прогрессии (`trainer_logic`, без ИИ)
- `est_1rm(weight, reps)`: Epley `w*(1+reps/30)` при `1≤reps≤12`, иначе `None`.
- `weight_step(equipment, muscle_group)`: barbell → 2.5 (низ: 5.0), dumbbell → 2.0, machine/cable → 2.5 (низ 5.0), kettlebell → 4.0, иначе 2.5; `round_to_step`.
- `evaluate_exercise(planned, sets_done) -> success|partial|fail`: все planned work-сеты выполнены и все `reps ≥ reps_max` → `success`; все `reps ≥ reps_min` → `partial`; иначе `fail` (для `time`: ≥ target → success).
- `next_targets(state, exercise, result, feedback)`:
  - `success` и feedback ∈ {easy, ok} → weight + step (easy и низ тела у beginner → +2 шага); `target_reps` = min диапазона; `success_streak+=1`, `fail_streak=0`;
  - `success` и `hard` → вес прежний, повторы прежние (line «оставляем — было тяжело»);
  - `partial` → вес прежний, цель «+1 повтор в каждом подходе» (reps first); `fail_streak=0`;
  - `fail` → `fail_streak+=1`; при `fail_streak ≥ 2` → weight × 0.9 (round), `fail_streak=0` (line «−10% после двух неудач»); иначе вес прежний;
  - `easy` + `partial` → weight + step (пользователь сказал легко); `hard` + `fail` → сразу ×0.9;
  - bodyweight `reps`: success → target +2 повтора (cap 25, далее line «пора усложнять»); `time`: +10–15 с.
- Сессионный модификатор от отзыва на следующий день того же `day_index` (пишем в `adjustments_json` дня недели+1): `hard` → изоляционные −1 сет, компаунды −5% (если не было success); `easy` → компаунды +1 сет (max 5).
- `explain_changes(changes, lang)` — шаблоны RU/EN в `trainer_logic.EXPLAIN_TEXTS`.
- `detect_prs(records_by_type, set_log, exercise)`: `max_weight` (reps ≥1), `est_1rm`, `set_volume` (w×reps), `max_reps` (для `reps` и для reps_weight при том же/большем весе — упрощённо: max reps в одном сете), `max_time`. Первая запись — не PR (иначе каждая первая тренировка — «рекорд»): `is_pr` только при улучшении существующей записи; при отсутствии — создаём молча.
- `weekly_streak(session_dates, today)`: недели Пн–Вс подряд с ≥1 completed, считая текущую, если в ней уже есть сессия.
- `today_training_context(db, tid, date, lang) -> str|None` для «Что съесть?».

**Кэширование/экономия:** техника — 1 раз на упражнение и язык; совет дня — 1 раз на день и kind; разбор — 1 на неделю (регенерация — heavy-лимит); программа — heavy-лимит; альтернативы, PR, адаптация, прогресс — без ИИ. `_log_usage` теги: `trainer_program`, `trainer_technique`, `trainer_review`, `trainer_nutrition`.

---

## 6. UI

### 6.1 Файлы
- `frontend/js/trainer-common.js` → `window.Trainer`: `L` (словари RU/EN: goal, level, equipment, muscle, weekday, setType, feedback, reason, phase; `Trainer.label(group, key)` через `App.pick` в момент рендера), `exName(ex)`, `fmtKg`, `fmtSet(w, r, t)`, `humanDate`, `shiftDate`, `go(page)` (запоминает `App.state.trainerOrigin` при первом входе), `back()`, `sheet(items, onPick)` (копия паттерна `.diary-sheet` с классами `.tr-sheet*`), `RestTimer` (`start(sec)`, `adjust(±15)`, `skip()`, `remaining()`, `onTick/onDone`, `Date.now()`, `visibilitychange` пересчёт), `beep()` (WebAudio, разблокировка при первом тапе), `lineChart(points, {w,h,key})` → SVG-строка, `skeleton(n)`, `errorCard(msg, retryId)`, `cache` (overview, activeSession; `invalidate()`).
- Страницы (каждая — IIFE, `App.registerPage`): `page-trainer.js` («trainer»: маршрутизация в онбординг/пустое состояние/«Сегодня»), `page-trainer-onboarding.js` («trainer-onboarding», режим `edit` через `App.state.trainerEdit=true`), `page-trainer-program.js` («trainer-program»), `page-trainer-session.js` («trainer-session», экраны `run` и `finish`), `page-trainer-progress.js` («trainer-progress», включая разбор), `page-trainer-exercise.js` («trainer-exercise», список + детальный лист; параметр `App.state.trainerExerciseId`).
- `index.html`: 7 `<script>` после `page-workouts.js`, до `App.init()`; bump `?v=s30 → ?v=s31` во всех ссылках.
- `app.js`: методы `App.api.trainer*` (список в §7, этап F1).
- `page-workouts.js`: `coachCardHtml()` + обработчик в `bindEvents()`; текст карточки берётся из `App.state.trainerBrief` (заполняет `page-trainer` после `overview`; при отсутствии — статичный текст).
- `style.css`: блок в конце с маркерами `/* ===== trainer: common ===== */`, `/* ===== trainer: onboarding ===== */`, `/* ===== trainer: today/program ===== */`, `/* ===== trainer: session ===== */`, `/* ===== trainer: progress/exercise ===== */` — каждый агент правит только свой блок.

### 6.2 Классы (префикс `tr-`), переиспользование
- Обёртки: `.tr-page` (= `.page.sub-page`), шапка — существующие `.sub-head/.sub-back/.sub-title/.sub-subtitle`; карточки — `.card`; кнопки — `.btn .btn-cta .btn-ghost .btn-danger .btn-block`; поля — `.field .field__input`; чипы — `.chip/.chip--active`, дни — `.wk-rem-days/.wk-rem-day`; скелетоны `.skeleton-line`; пустые/ошибки `.wk-empty/.wk-error`; дисклеймер `.rec-disclaimer`; paywall — `App.requirePremium`.
- Онбординг: `.tr-onb`, `.tr-onb-progress` (полоса из N сегментов), `.tr-onb-step`, `.tr-option` (карточка-опция: `.tr-option__icon/__title/__desc`, `.is-active`), `.tr-option-grid`, `.tr-onb-nav`, `.tr-onb-summary`, `.tr-warn` (жёлтая плашка на `--accent`).
- Сегодня/программа: `.tr-today-card`, `.tr-day-badge` (`--today/--rest/--progress`), `.tr-ex-preview`, `.tr-week-strip > .tr-week-day` (`--done/--today/--planned/--skipped/--rest`), `.tr-streak`, `.tr-streak__bar`, `.tr-nutrition-card`, `.tr-links > .tr-link`, `.tr-program-head`, `.tr-phase-chip` (`--base/--build/--peak/--deload`), `.tr-plan-day` (аккордеон на `.acc-fold`), `.tr-plan-item`, `.tr-gen-wait`, `.tr-review-banner`.
- Сессия: `.tr-session-head` (sticky), `.tr-session-timer`, `.tr-session-progress`, `.tr-block` (`--warmup/--cooldown`, `.tr-block__title`, `.tr-check-row`), `.tr-ex-card` (`--done/--skipped`), `.tr-ex-card__head/__name/__target/__note/__menu`, `.tr-set-table`, `.tr-set-head`, `.tr-set-row` (`--done/--pr/--warmup`), `.tr-set-cell`, `.tr-set-num`, `.tr-set-prev`, `.tr-set-input`, `.tr-set-check`, `.tr-set-add`, `.tr-rest-bar` (`fixed; bottom: calc(var(--safe-bottom) + 12px)`; z-index 1100), `.tr-rest-bar__time/__ring/__btn`, `.tr-pr-toast` (fixed top, fadeIn/out), `.tr-sheet/.tr-sheet__backdrop/__panel/__item/__title`, `.tr-alt-list > .tr-alt-item`, `.tr-finish`, `.tr-stat-grid > .tr-stat(__value/__label)`, `.tr-record-card`, `.tr-feedback-chips > .tr-feedback-chip(.is-active)`, `.tr-adapt-card > .tr-adapt-line`.
- Прогресс/упражнения: `.tr-muscle-bars > .tr-muscle-bar(__label/__track/__fill/__val)` (`--low/--ok/--high`), `.tr-record-list`, `.tr-chart` (`.tr-chart__svg`, `.tr-chart__line`, `.tr-chart__dot`, `.tr-chart__axis`), `.tr-chart-controls`, `.tr-history > .tr-history-item`, `.tr-review-card` (`__section/__list/__change` с чекбоксом), `.tr-lib-filters`, `.tr-lib-search`, `.tr-lib-item(__name/__meta)`, `.tr-technique` (`__block/__title/__list`).
- Цвета только через токены: done — `--cta`, PR — `--accent` + 🏆, warmup-строка — `--muted`, опасные действия — `--danger`.

### 6.3 Локализация
Все строки — `App.pick(ru, en)` в функциях рендера; словари в `Trainer.L` содержат пары и резолвятся при отрисовке. Имена упражнений — `name_ru/name_en` из API. Бэкенд-тексты (адаптация, пуши) — по `user.language`.

---

## 7. Порядок реализации (этапы = отдельные коммиты; владение файлами)

Контракты §3–§5 фиксированы, поэтому этапы с разными файлами можно вести параллельно.

| Этап | Содержание | Создаёт | Правит (точечно) | Может идти параллельно с |
|---|---|---|---|---|
| **B0 Фундамент** | 11 моделей; seed ~140 упражнений; `trainer_logic.py` (все чистые функции §5.5, `expand_program`, `catalog_for_prompt`, `alternatives_for`, `collect_week_stats`, `today_training_context`, `_weekdays_to_csv/_csv_to_weekdays/_normalize_time` копии); `trainer_seed.ensure_exercises` | `backend/trainer_logic.py`, `backend/trainer_exercises_seed.py`, `backend/trainer_seed.py`, `tests/test_trainer_logic.py`, `tests/test_trainer_seed.py` | `backend/models.py` (append), `tests/test_smoke.py` (модели в цикл) | B1 |
| **B1 ИИ** | `trainer_ai.py`: 4 функции, RU/EN промпты, `normalize_program/normalize_review/normalize_technique/normalize_tip` | `backend/trainer_ai.py`, `tests/test_trainer_ai.py` | — | B0 |
| **B2 API: профиль/программа/библиотека** | `trainer.py` router + `trainer_schemas.py`: overview, profile (+TrainingReminder upsert), program generate/get/archive, today, exercises list/get(+technique)/alternatives/exclude | `backend/trainer.py`, `backend/trainer_schemas.py`, `tests/test_trainer_api.py` | `backend/main.py` (import, lifespan seed, include_router, delete-кортеж) | — (после B0+B1) |
| **B3 API: сессия** | start/active/get/set/delete set/replace/skip/add/finish (+Workout)/feedback (+адаптация)/abandon/sessions | `tests/test_trainer_session.py` | `backend/trainer.py`, `backend/trainer_schemas.py` (тот же владелец, что B2, или строго после B2) | F1–F3 |
| **B4 API: прогресс/разбор/питание + интеграции** | progress, exercise history, review generate/latest/apply, nutrition today; `food/suggest` контекст; `trainer_notify.decorate_training_reminder` | `backend/trainer_notify.py`, `tests/test_trainer_progress.py`, `tests/test_trainer_review.py`, `tests/test_trainer_integrations.py` | `backend/trainer.py` (после B3), `backend/main.py` (food_suggest, 5 строк), `backend/schemas.py` (2 поля), `backend/ai_service.py` (`suggest_food` параметр), `backend/notifications.py` (~8 строк) | F2, F3 |
| **F1 Фронт-каркас** | `trainer-common.js`; `App.api.trainer*` (все методы §4 сразу); `index.html` (7 скриптов, bump s31); `style.css` — 5 блоков-маркеров с базовыми стилями common; карточка входа в `page-workouts.js`; `page-trainer.js` («Сегодня», пустое состояние, роутинг в онбординг); `page-trainer-onboarding.js` | `frontend/js/trainer-common.js`, `page-trainer.js`, `page-trainer-onboarding.js` | `frontend/js/app.js` (append в `App.api`), `frontend/index.html`, `frontend/css/style.css` (append), `frontend/js/page-workouts.js` (2 вставки) | B2/B3 (по контракту) |
| **F2 Сессия** | `page-trainer-session.js` (run + finish + feedback), таймер, PR-тост, замена/пропуск, автосохранение, closingConfirmation, инвалидация `App.state.diaryByDate[date]` после finish | `frontend/js/page-trainer-session.js` | `style.css` только блок `trainer: session` | F3 |
| **F3 Программа/прогресс/упражнения** | `page-trainer-program.js`, `page-trainer-progress.js` (+разбор), `page-trainer-exercise.js` | 3 файла | `style.css` блоки `today/program` и `progress/exercise` | F2 |
| **P Полировка** | EN-проверка всех экранов, пустые состояния, README/.env.example (новых env нет; описать теги телеметрии и лимиты), `run_all.py` зелёный, прогон в браузере | — | по результатам | — |

Правила против конфликтов: общие файлы (`main.py`, `app.js`, `index.html`, `style.css`, `models.py`, `schemas.py`, `ai_service.py`, `notifications.py`, `page-workouts.js`) правит ровно один этап каждый, вставки — в конец файла/блока или в заранее названное место; `trainer.py` — один владелец последовательно (B2→B3→B4); фронт-страницы F2/F3 стартуют после F1 (им нужны `Trainer` и `App.api`); версия ассетов поднимается один раз в F1 (последующие этапы до деплоя её не трогают; при деплое между этапами — bump ещё раз). Коммиты/пуш — только по просьбе пользователя.

Методы `App.api` (F1): `trainerOverview, trainerProfile, trainerSaveProfile, trainerGenerateProgram, trainerProgram, trainerArchiveProgram, trainerToday, trainerStartSession, trainerActiveSession, trainerSession, trainerSaveSet, trainerDeleteSet, trainerAlternatives, trainerReplaceExercise, trainerSkipExercise, trainerAddExercise, trainerFinishSession, trainerFeedback, trainerAbandonSession, trainerSessions, trainerProgress, trainerExercises, trainerExercise, trainerExerciseHistory, trainerExcludeExercise, trainerWeeklyReview, trainerLatestReview, trainerApplyReview, trainerNutritionToday`.

---

## 8. Тестирование

### 8.1 Бэкенд (образец `tests/test_recovery.py`: env до импорта, `ALLOW_INSECURE_AUTH=1`, временный sqlite, `mock.patch.object(ai_service, "_run_text_completion", fake_run)`, `sys.exit(1)` при провалах)
- `test_trainer_logic.py` (без приложения): Epley (60×8 → 76; 13 повт. → None); `round_to_step`; `evaluate_exercise` на success/partial/fail; `next_targets` — таблица кейсов (success+ok → +шаг; success+hard → без изменений; partial → +1 повтор; fail×2 → ×0.9; easy+partial → +шаг; bodyweight success → +2 повтора); `detect_prs` — первая запись не PR, улучшение → PR нужных типов, warmup игнорируется; `weekly_streak` (пустая неделя обнуляет, текущая неделя с сессией засчитывается); `expand_program` (6 недель × 3 дня = 18 строк, даты по Пн/Ср/Пт начиная с ближайшего, deload-неделя помечена); `alternatives_for` (та же мышца, фильтр оборудования, `pain` исключает контриндицированные, excluded не возвращается, ≤5); `catalog_for_prompt` (bodyweight-профиль не содержит штангу; ограничение `knee` убирает прыжковые выпады); `explain_changes` RU/EN.
- `test_trainer_seed.py`: `ensure_exercises` идемпотентна (2 вызова → одно количество), slug уникальны, у каждого валидные `muscle_group/equipment/measure_type`, есть `warmup_general_5min` и `stretch_full_body_5min`, upsert не затирает `technique_json`.
- `test_trainer_ai.py`: `generate_program` — контекст (цель, ограничения, каталог) в user_prompt, tag `trainer_program`, `max_tokens=4000`, RU/EN промпт по `lang`, правила безопасности в system («не врач»/«not a doctor»), мусорный slug → фаззи-матч/отброс, лишний день усечён, отсутствие warmup → инжект, блок базы знаний перед каталогом, метод первой подсказкой, `({}, {})` и сбой движка → программа из шаблона (`knowledge-template`), не собрался и шаблон → AIError; `weekly_review` — недопустимый тип изменения отброшен, `weight_pct=−40` → clamp −15, swap на чужую мышцу отброшен; `exercise_technique`/`nutrition_day_tip` — нормализация списков и обрезка.
- `test_trainer_api.py`: free → 402 на `overview/profile/program/generate/exercises`; `POST /profile` валидация (days=7 → 422, weekdays≠days → 422), создаёт TrainingReminder с `weekdays="0,2,4"`, повторный POST обновляет ту же строку, `reminder_enabled=false` → `enabled=False`; `generate` без профиля → 409; с моком → 200, программа `active`, `TrainerProgramDay` = weeks×days, старая программа → `archived`; heavy-лимит → 429 (третий вызов за минуту); AI пусто → 200 с программой из шаблона базы знаний (`ai_model="knowledge-template"`, 18 дней, без противопоказанных); `today` — planned/rest/week_done; `exercises/{id}?technique=1` — первый вызов дергает ИИ и кэширует, второй — без вызова (счётчик fake_run), AIError → 200 с `technique=None`; `account/data DELETE` удаляет все trainer-строки пользователя, библиотека остаётся.
- `test_trainer_knowledge.py` (без приложения): копии констант базы знаний = `trainer_logic`; все slug паттернов и `SAFETY` есть в каталоге; `select_split` на все дни × уровень × оборудование × цель; `build_week` — только разрешённые упражнения и оборудование, во времени сессии, на типичных анкетах объём крупных мышц в `volume_targets` и тяга ≥ жима; `audit_week` ловит неделю без тяг, изоляцию раньше базового, противопоказания и оборудование; `prompt_brief` ru/en ≤3500 со схемой и объёмом; `periodization_for`; `generate_program` с подменённым ИИ — блок базы знаний в user_prompt, замены/порядок/объём/интенсивность, периодизация и метод первой подсказкой; сбой ИИ → шаблон.
- `test_trainer_session.py`: start (409 при второй), `previous` заполняется из прошлой сессии, deload-неделя снижает `planned_weight_kg`; set upsert, объём/1RM, PR только при улучшении, `warmup` не в объём, чужая сессия → 404; replace (старая `replaced`, новая с тем же order, `preferred_alternative_id` записан, `pain` → excluded); skip; finish без сетов → 400; finish → `Workout` создан с `type="strength"`, `calories_burned>0`, `description` начинается с «Тренер:», `GET /diary/{date}` показывает `total_burned` и `net_calories`; `TrainerProgramDay.status="done"`; state `working_weight`; feedback `hard` после fail → ×0.9 и строки на RU, `language="en"` → EN строки; abandon → Workout не создан, день `planned`; sessions пагинация.
- `test_trainer_progress.py`: muscle_volume_7d считает только work/done, streak, chart points по exercise_id, history.
- `test_trainer_review.py`: 0 сессий → 409; контекст содержит ккал/белок из DiaryEntry и вес из WeightLog; сохранение/latest; apply меняет `exercises_json` следующей недели и state, `applied=True`, повторный apply тех же id — идемпотентен.
- `test_trainer_integrations.py`: `food/suggest` с завершённой сессией сегодня → в user_prompt есть «тренировк»/«workout» и `training_note` в ответе; без сессии — нет; `trainer_notify.decorate_training_reminder` добавляет название дня при активной программе и возвращает исходный текст без неё; `nutrition/today` — кэш (второй вызов без ИИ), `kind` переключается training/rest.
- Регрессия: `tests/run_all.py` целиком зелёный (в т.ч. `test_smoke.py` с легаси-БД + новые модели, `test_schema.py` без изменений).

### 8.2 Браузер (preview `.claude/launch.json`, `ALLOW_INSECURE_AUTH=1`, dev-пользователь премиум через `subscription_type="lifetime"`; ИИ — реальный ключ или мок через `DEBUG_AI`)
1. «Тренировки» → карточка «AI-тренер» → онбординг 9 шагов, кнопки «Назад/Далее», валидация (нельзя «Далее» без выбора), сводка, «Собрать программу» → экран ожидания → превью программы (недели, дни, упражнения, разминка/заминка) → «Начать программу».
2. «Сегодня»: тренировочный день / день отдыха (сменить preferred_weekdays в настройках), лента недели, стрик, карточка питания грузится лениво, «Назад» возвращает на «Тренировки».
3. Сессия: предзаполненные поля, ✓ → зелёная строка, хаптик (в Telegram), плашка отдыха с −15/+15/Пропустить и бипом; свернуть Telegram на 40 с и вернуться — таймер показывает правильный остаток; закрыть и открыть Mini App — «Продолжить» восстанавливает сеты; заменить упражнение (лист причин → альтернативы), пропустить, исключить; PR-тост при большем весе; «Завершить» с confirm при невыполненных.
4. Итог: цифры, «добавлено в дневник» → перейти в «Дневник» на эту дату: баланс «Съедено − Сожжено = Итого» показывает ккал тренировки; в «Тренировках» появилась запись «Тренер: День 1 — Верх».
5. Отзыв «тяжело» → карточка «Учёл…» со строками; следующая сессия того же дня (неделя 2) — веса/сеты изменились согласно строкам.
6. Прогресс: бары мышц, рекорды, график по упражнению (переключение метрик/периода), история → детали; «Разобрать неделю» → разбор → отметить изменения → «Применить» → программа недели+1 обновлена.
7. Упражнения: фильтры, поиск, открыть карточку → техника генерируется один раз (повторное открытие мгновенно), вкладка история.
8. RU→EN в аккаунте: все экраны тренера перерисовываются на английском, имена упражнений `name_en`, строки адаптации на EN.
9. Free-пользователь (`subscription_type=None`): карточка на «Тренировках» не видна (страница за paywall), прямой `App.navigate("trainer")` → paywall, все `/trainer/*` → 402.
10. Мобильная ширина 375px: таблица сетов не вылезает за экран, плашка отдыха не перекрывает поле ввода активной строки, body не скроллится горизонтально; бампнутая версия `?v=s31` во всех ссылках.
