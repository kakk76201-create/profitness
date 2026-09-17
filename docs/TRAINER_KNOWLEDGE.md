# База знаний AI-тренера

Модуль `backend/trainer_knowledge.py`. Это правила составления программ, собранные из исследований, позиционных заявлений и известных программ, и детерминированный конструктор недели на их основе.

## Зачем

До базы знаний ИИ получал анкету и каталог упражнений и сам придумывал неделю, поэтому качество зависело от случая. Теперь:

- ИИ получает выжимку правил под конкретную анкету (`prompt_brief`, до 3500 символов): схему недели, диапазоны повторов, запас повторов (RIR), отдых, объём на мышцу, прогрессию, разгрузку и ограничения.
- Бэкенд проверяет любую неделю, свою или от ИИ (`audit_week`): объём, баланс тяг и жимов, порядок упражнений, противопоказания, время.
- Неделю можно собрать вообще без ИИ (`build_week`). Результат в том же формате, что `week_template` после `trainer_ai.normalize_program`, и совместим с `trainer_logic.expand_program`.

Модуль чистый: без БД, сети и ИИ. `trainer_logic` он не импортирует, поэтому `trainer_logic` и `trainer_ai` могут импортировать его сами. Используются только slug из `backend/trainer_exercises_seed.py`. У каждого правила и числа есть id источника из `SOURCES`.

## Что внутри

| Раздел | Что содержит |
|---|---|
| `SOURCES` | Источники: id → название, авторы, год, ссылка, тип (`meta-analysis`, `rct`, `position-stand`, `consensus`, `practitioner`, `program`), пометка о препринте или пересказе |
| `PRINCIPLES` | 25 ключевых правил на ru/en с числами, силой доказательности, тегами (цель, уровень) и краткой формой для промпта |
| `GOAL_PARAMS` | Для каждой цели: повторы (тяжёлый день, базовые, объёмный день, изоляция), подходы, RIR, отдых, множитель объёма, кардио |
| `LEVEL_PARAMS` | Для каждого уровня: подходы на мышцу в неделю, потолок подходов за тренировку, частота, модель прогрессии, разгрузка, максимум упражнений и сложность |
| `VOLUME_LANDMARKS` | Ориентиры RP по 10 группам мышц каталога: MV, MEV, MAV, MRV, MAV/MRV при приоритете, частота |
| `PROGRESSION_MODELS` | Линейная, двойная, волна: суть, когда переходить дальше, как реализовано у нас |
| `PHASE_GUIDE` | Фазы base/build/peak/deload: вес, подходы, целевой RIR |
| `PATTERNS` | 24 паттерна движений → упражнения каталога в порядке предпочтения, с заменами |
| `SPLITS` | 30 шаблонов недели на 2–6 дней со слотами (паттерн, роль, подходы, ключ повторов, приоритет, вариант) |
| `FAMOUS_PROGRAMS` | 17 программ, из которых взяты схемы: что позаимствовано и откуда |
| `SAFETY`, `SAFETY_GENERAL`, `SAFETY_BRIEF` | Правила для 8 ограничений анкеты, контроль боли, красные флаги |
| `TIME_MODEL`, `SESSION_BUDGET`, `TRIM_ORDER` | Расчёт времени тренировки и порядок сокращения |

Функции:

| Функция | Результат |
|---|---|
| `select_split(profile)` | Шаблон недели под анкету; всегда что-то возвращает |
| `volume_targets(profile)` | `{мышца: {min, target, max}}` рабочих подходов в неделю |
| `pick_exercise(pattern, profile, catalog_map, used, variant)` | slug или `None` с учётом оборудования, противопоказаний, сложности и повторов внутри дня |
| `build_week(profile, catalog_map, lang)` | `{split_id, split_type, title, days}` в формате `week_template` |
| `periodization_for(profile)` | Фазы по неделям в формате `periodization` |
| `weekly_sets_by_muscle(week, catalog_map)` | Подходы по мышцам: основная мышца 1, синергист 0,5 |
| `audit_week(week, profile, catalog_map)` | Список проблем `{code, severity, detail_ru, detail_en, muscle?, day_index?, slug?}` |
| `estimate_day_seconds(day, catalog_map)` | Оценка длительности дня |
| `prompt_brief(profile, lang, catalog_map=None)` | Текст для промпта ИИ; пример недели — из переданного каталога, у каждого упражнения в скобках паттерн слота |
| `method_tip(profile, lang)` | Одна строка для пользователя о том, на чём основана программа |
| `safety_tips(profile, lang)` | Для беременности и давления/сердца — строка «врач + красные флаги» (≤200 символов) |

Профиль может быть dict или ORM-объектом `TrainerProfile` (поля `*_json` понимаются). Если полей не хватает, берутся осторожные значения по умолчанию: новичок, «тонус», только вес тела, 3 дня по 45 минут, 6 недель. `catalog_map` может быть dict slug → запись, списком записей или ORM-объектов, `trainer_ai._Catalog` или `None` (тогда берётся встроенный seed).

## Как база подключена к генерации программы

`trainer_ai.generate_program` (подробно — [TRAINER_SPEC.md §5.1](TRAINER_SPEC.md)):

1. `prompt_brief` уходит в запрос модели блоком «БАЗА ЗНАНИЙ» перед каталогом; system-промпт делает его обязательным.
2. Ответ модели нормализуется, затем `audit_week` и безопасные правки: замена недопустимых упражнений через `pick_exercise`, пределы интенсивности ограничений, базовые раньше изоляции, подходы до диапазона `volume_targets` в рамках времени, тяга не меньше жима. Непригодную периодизацию заменяет `periodization_for`, первой подсказкой идёт `method_tip`, сразу за ней при беременности и давлении — `safety_tips` (врач и красные флаги), даже если модель о враче не написала.
3. Если ИИ упал или ответ непригоден, программа собирается из `build_week` без модели (`ai_model = "knowledge-template"`).

Проверки — `tests/test_trainer_knowledge.py`.

## Ключевые принципы с числами

**Объём.** Рост мышц зависит от рабочих подходов на мышцу в неделю, но каждый следующий подход даёт меньше. Считаем дробно: прямой подход 1, синергист 0,5 (Pelland 2025, 67 исследований). Минимальная действенная доза около 4 подходов в неделю. Отдача на подход: 5–10 подходов — высокая, 11–18 — средняя, 19–29 — низкая. Практические диапазоны: новичок 6–10, средний уровень 10–16 (у тренированных 12–20 не хуже, чем больше 20; Baz-Valle 2022), продвинутый 12–20, приоритетная мышца временно до 24. Для силы отдача от объёма выходит на плато уже после ~5 подходов в неделю: у тренированных 1 подход на упражнение трижды в неделю (~13 минут) дал ту же силу, что 3 или 5 подходов (Schoenfeld 2019).

**Объём за одну тренировку.** После ~11 дробных (≈8 прямых) подходов на мышцу эффект за тренировку перестаёт расти (Remmert 2025, препринт). У RP похожий потолок: 8–12.

**Частота.** При равном объёме частота на гипертрофию почти не влияет, но каждую мышцу тренируем не реже 2 раз в неделю. Для силы частота важна: основное движение 2 раза в неделю дало прирост 17,3% против 12,7% при одном разе.

**Нагрузка и повторы.** Мышцы растут одинаково в диапазоне примерно 30–85% 1ПМ, если подходы близки к отказу. Сила растёт больше с тяжёлыми весами. Сила: 1–6 повторов в базовых. Масса: 6–15, в изоляции до 20–30. Выносливость мышц: 15–25 при весе меньше 60% 1ПМ.

**Близость к отказу.** Рост мышц снижается примерно на 0,5% на каждый повтор в запасе. При этом 1–2 повтора в запасе дают тот же рост, что и отказ, с меньшей усталостью (Refalo 2024). Базовые упражнения со свободным весом — 1–3 RIR, изоляция — 0–2, новичкам первые недели 2–3. В программе RPE = 10 − RIR.

**Отдых.** Для массы и силы не меньше 60 с; дольше 90 с разницы для роста мышц нет (для цели «выносливость» в изоляции допустимы 30–60 с, ACSM). Тяжёлые базовые — 2–3 мин (для силы 3–5), изоляция — 60–120 с.

**Порядок.** Многосуставные упражнения идут раньше изолирующих. Упражнение на приоритетную мышцу ставим первым: сила растёт больше в том, что делается первым, а на гипертрофию порядок не влияет.

**Прогрессия.** Когда на двух тренировках подряд цель превышена на 1–2 повтора, вес увеличиваем на 2–10% (ACSM). Добавлять повторы так же эффективно, как вес (Plotkin 2022). Новичку — линейная прогрессия, среднему уровню — двойная, продвинутому — волна: для силы у тренированных она лучше линейной (ES 0,61), для массы разницы нет.

**Разгрузка.** На практике раз в 5,6±2,3 недели на ~6 дней. Снижают объём, частоту оставляют. Неделя полного отдыха пользы не дала, поэтому разгрузка активная. Данных мало (опрос, консенсус Delphi и одно RCT), поэтому сила правила — medium; «новичку до 8–12 недель разгрузка не нужна» — практика линейных программ, а не эксперимент. У нас: −15% веса и −1 подход (как `DELOAD_WEIGHT_PCT` и `DELOAD_SETS_DELTA` в `trainer_logic`).

**Кардио.** Кардио не мешает росту мышц и максимальной силе, но снижает взрывную силу. Разносим на 3 часа и больше; если в один день, сначала силовая. Перед днём ног — велосипед или ходьба, не бег. ВОЗ: 150–300 минут умеренной активности в неделю и силовые не реже 2 дней. Для заметного похудения нужно больше 250 минут.

**Похудение.** Силовые на дефиците сохраняют мышцы. Дефицит больше ~500 ккал в сутки мешает набору мышц, но не силе. Худеть на 0,5–1% массы тела в неделю. Белок 1,6–2,2 г/кг в сутки.

**Разминка и растяжка.** Разминочные подходы нужны перед первым тяжёлым упражнением (≤6 повторов). При весах около 10ПМ достаточно 0–1 подхода. Статическая растяжка 60 с и дольше перед силовой снижает результат на 4,6%. От травм защищают силовые (снижение до 0,315), а не растяжка (0,963).

## Цели и уровни

| Цель | Базовые | Тяжёлый / объёмный день | Изоляция | RIR базовые / изоляция | Отдых базовые / изоляция, с | Объём × | Кардио |
|---|---|---|---|---|---|---|---|
| muscle | 6–10 | 5–8 / 8–12 | 10–15 | 1–3 / 0–2 | 120–180 / 60–90 | 1,0 | 2–3 × 20–30 мин |
| strength | 4–6 (новичок 5–8) | 3–5 / 6–10 | 8–12 | 1–3 / 1–2 | 180–300 / 90–120 | 0,8 | 2 × 20–30 мин, лёгкое |
| loss | 6–10 | 6–8 / 10–12 | 12–15 | 1–3 / 0–2 | 90–150 / 60–75 | 0,85 | 150–300 мин в неделю |
| tone | 8–12 | 6–10 / 10–15 | 12–15 | 1–3 / 0–2 | 90–120 / 60–75 | 0,85 | 2–3 × 20–30 мин |
| endurance | 8–12 | 6–10 / 12–15 | 15–20 | 2–3 / 1–3 | 60–120 / 30–60 | 0,6 | 3–4 сессии, 80% лёгких |

| Уровень | Подходов на крупную мышцу в неделю (min–target–max) | За тренировку, прямых | Прогрессия | Накопление до разгрузки | Упражнений | Сложность |
|---|---|---|---|---|---|---|
| beginner | 6–8–10 (акцент до 12) | ≤6 | линейная | до 7 недель, плановая разгрузка с 8-й | ≤6 | ≤2 |
| intermediate | 10–12–16 (акцент до 20) | ≤8 | двойная | ≤5 недель | ≤7 | ≤3 |
| advanced | 12–16–20 (акцент до 24) | ≤10 | волна | ≤4 недели | ≤8 | ≤3 |

`volume_targets` считается так: норма уровня × множитель цели × доля мышцы (`factor`: мелкие мышцы и так получают много работы от базовых). Нижняя граница не ниже минимальной дозы: 4 подхода для крупной мышцы, 2 для мелкой и для цели «выносливость». Верхняя граница не ниже части MAV по RP (новичок 50%, средний 75%, продвинутый 100%), а потолок — MRV. Мышцы из акцента получают верх диапазона, а сам верх поднимается до потолка приоритетной мышцы уровня (12 / 20 / 24), но не ниже обычного максимума. MAV при приоритете по RP (до 30) не берётся: это выше правила «приоритетная мышца временно до 24». У спины норма ×1,2: RP советует делить объём поровну между вертикальными и горизонтальными тягами, по сути это две области. Кор как стабилизатор в базовых упражнениях не считается: дробный счёт относится к синергистам, а у RP ориентиры для пресса — прямые подходы.

## Ориентиры объёма RP (практические оценки)

Эти числа — оценки практиков RP для лифтеров со стажем 3–7 лет, а не результаты экспериментов. У новичков всё ниже.

| Мышца | MV | MEV | MAV | MRV | MRV при приоритете | Раз в неделю | Доля нормы уровня |
|---|---|---|---|---|---|---|---|
| грудь | 2–4 | 4–6 | 6–16 | 16–24 | 24–32 | 2–4 | 1,0 |
| спина | 2–6 | 6–8 | 8–20 | 20–26 | 26–34 | 2–4 | 1,2 |
| плечи (по средней дельте) | 2–6 | 6–8 | 8–24 | 24–30 | 30–40 | 2–6 | 0,9 |
| бицепс | 6–8 | 8–10 | 14–20 | 20–26 | 26–35 | 3–6 | 0,8 |
| трицепс | 0–4 | 4–6 | 6–16 | 16–20 | 20–26 | 2–4 | 0,8 |
| квадрицепс | 2–4 | 4–6 | 6–14 | 14–18 | 18–24 | 2–5 | 1,0 |
| бицепс бедра | 0–2 | 2–4 | 2–8 (так на странице) | 8–14 | 14–20 | 2–3 | 0,9 |
| ягодицы | 2–6 | 6–8 | 8–24 | 24–30 | 30–40 | 2–5 | 0,9 |
| икры | 2–4 | 4–6 | 6–16 | 16–24 | 24–32 | 3–6 | 0,6 |
| кор (пресс) | 0–4 | 0–4 | 4–12 | 12–20 | 24–32 | 3–6 | 0,6 |

## Анкета → схема недели

Результат `select_split`, 45 минут, без ограничений:

| Дней | Уровень | Масса / тонус, зал | Сила, зал | Похудение | Выносливость | Тонус + акцент на ягодицы | Дом, гантели | Вес тела |
|---|---|---|---|---|---|---|---|---|
| 2 | новичок | `fb2` | `fb2` | `loss_fb2` | `endurance_2` | `fb2` | `fb2` | `bw_fb2` |
| 2 | средний | `fb2` | `fb2` | `loss_fb2` | `endurance_2` | `fb2` | `fb2` | `bw_fb2` |
| 2 | продвинутый | `fb2` | `fb2` | `loss_fb2` | `endurance_2` | `fb2` | `fb2` | `bw_fb2` |
| 3 | новичок | `fb3` | `fb3_strength` | `loss_fb3` | `endurance_3` | `glutes_3` | `home_fb3` | `bw_fb3` |
| 3 | средний | `fb3` | `fb3_strength` | `loss_fb3` | `endurance_3` | `glutes_3` | `home_fb3` | `bw_fb3` |
| 3 | продвинутый | `ulf3` | `ulf3` | `loss_fb3` | `endurance_3` | `glutes_3` | `home_fb3` | `bw_fb3` |
| 4 | новичок | `ul4` | `ul4` | `loss_ul4` | `endurance_4` | `glutes_4` | `ul4` | `bw_ul4` |
| 4 | средний | `ul4_phul` | `ul4_phul` | `loss_ul4` | `endurance_4` | `glutes_4` | `ul4_phul` | `bw_ul4` |
| 4 | продвинутый | `ul4_phul` | `ul4_phul` | `loss_ul4` | `endurance_4` | `glutes_4` | `ul4_phul` | `bw_ul4` |
| 5 | новичок | `ul5_beginner` | `ul5_beginner` | `loss_5` | `endurance_5` | `glutes_5` | `ul5_beginner` | `bw5` |
| 5 | средний | `ulppl5` | `ulppl5` | `loss_5` | `endurance_5` | `glutes_5` | `ulppl5` | `bw5` |
| 5 | продвинутый | `phat5` | `phat5` | `loss_5` | `endurance_5` | `glutes_5` | `phat5` | `bw5` |
| 6 | новичок | `ppl6` | `ppl6` | `loss_6` | `endurance_6` | `loss_6` | `ppl6` | `bw6` |
| 6 | средний | `ppl6` | `ppl6` | `loss_6` | `endurance_6` | `loss_6` | `ppl6` | `bw6` |
| 6 | продвинутый | `ppl6` | `ppl6` | `loss_6` | `endurance_6` | `loss_6` | `ppl6` | `bw6` |

Как выбирается схема. Среди шаблонов с нужным числом дней побеждает тот, у кого меньше штрафов:

- не та цель: +20;
- не тот уровень: +10 за каждую ступень;
- шаблон для другого оборудования: +15, для своего: −3;
- «ягодичный» шаблон без такого акцента в анкете: +6, с акцентом: −4;
- тяжёлые схемы при беременности или давлении: +8;
- совпадение с желаемым типом сплита (`split_type` в профиле): −5.

При равенстве решают `priority` и id. `ppl3` (каждая мышца раз в неделю) выбирается только по явному желанию.

Откуда схемы:

| Шаблон | В духе | Суть |
|---|---|---|
| `fb2`, `fb3` | StrongLifts (2 дня), Nippard Fundamentals, Nuckols, r/Fitness BBR | всё тело, база + изоляция, мышца 2–3 раза в неделю |
| `fb3_strength` | Starting Strength, StrongLifts, GZCLP, 5/3/1 for Beginners | присед и жим 2 раза, вес растёт каждую тренировку |
| `ul4` | Helms Novice, Nippard U/L | верх/низ, мышца дважды в неделю |
| `ul4_phul` | PHUL, 5/3/1 BBB | тяжёлый и объёмный день на каждую половину тела |
| `ulf3`, `ulppl5` | Helms Intermediate, RP | верх/низ + всё тело или толкай/тяни/ноги |
| `phat5` | PHAT | силовые верх и низ + объёмные дни по группам |
| `ppl6`, `ppl3` | Reddit PPL, Nippard PPL | тяни/толкай/ноги |
| `home_fb3` | Nuckols, Contreras | гантели и резинки, повторы до близкого отказа |
| `bw_fb2`, `bw_fb3`, `bw_ul4`, `bw5`, `bw6` | r/bodyweightfitness RR | цепочки прогрессий; при 5–6 днях — RR 3 раза + кардио, а не 6 силовых |
| `loss_*` | Nippard, Helms, ВОЗ, ACSM | силовые с прежними весами, кардио после силовой или отдельным днём |
| `endurance_*` | Viada, Seiler, Rønnestad | 2–3 силовые без отказа, 80% кардио лёгкое, интервалы ≤2 в неделю |
| `glutes_3/4/5` | Contreras | хип-траст 2 раза + присед/выпад + наклон + отведение |

## Как собирается неделя (`build_week`)

1. Выбирается схема (`select_split`). К дням добавляются слоты под акцент: руки — сгибания и разгибания в верхние дни, ягодицы — отведение в нижние и так далее. Для целей «похудение» и «выносливость» добавляется кардио после силовой.
2. Каждый слот заполняется через `pick_exercise`. Фильтры: оборудование, противопоказания каталога и `SAFETY`, сложность уровня. Упражнения внутри дня не повторяются, второе тяжёлое упражнение на поясницу в тот же день не ставится. Новичку (кроме цели «сила») сначала предлагаются упражнения сложности 1. Для тяжёлого и объёмного дня берутся разные slug: `TrainerExerciseState` хранит одну цель повторов на упражнение.
3. Повторы, RPE и отдых берутся из цели и роли слота. Отдых в упражнениях с весом тела и удержаниях — не больше 120 с (нижний предел ограничений сохраняется): 3–5 минут цели «сила» нужны тяжёлым подходам со штангой, а не мосту без веса. Для упражнений с весом тела диапазон шире (до 25 — как `BODYWEIGHT_REPS_CAP`). Для подтягиваний, брусьев и скандинавских сгибаний диапазон ниже: 5–10 или 4–8.
4. Порядок в дне: многосуставные, затем изоляция; базовое упражнение на мышцу из акцента — первым.
5. Подгонка под `session_minutes` по `TIME_MODEL` в порядке `TRIM_ORDER`: подходы изоляции до 2 → убрать изоляцию → отдых к нижней границе → подходы базовых до 2 → только потом убирать многосуставные.
6. Балансировка недели:
   - объём выше максимума снижается, изоляция первой;
   - ниже минимума — добавляются подходы, пока хватает времени и не превышен потолок за тренировку;
   - крупная мышца без нагрузки получает упражнение;
   - тяга не меньше жима.
   Замена не ставит в день второе такое же движение сверх шаблона: при больных коленях «присед → ягодичный мост» не даёт двух мостов подряд, при больных плечах «жим вверх → задняя дельта» — двух тяг к лицу; слот берёт следующую замену или пропускается. Если ограничения убрали почти весь день (вес тела без турника и резинок при больных запястьях), день добирается мостом, приседом и кором до 3 упражнений.
7. Кардио получает остаток времени (ровное — от 8 минут, интервалы — от 6). Добавляются разминка (`warmup_general_5min` и одно движение по зоне дня) и заминка (`stretch_full_body_5min`). Если на кардио не хватило времени (30 минут), хвост «+ кардио» из названия дня убирается.

Разминка и заминка по длительности сессии: 20 мин — 3 и 2 мин, 30 — 4 и 3, 45 — 5 и 4, от 60 — по 5.

## Периодизация (`periodization_for`)

Разгрузка ставится в конце каждого блока накопления. Длина накопления не больше 7, 5 или 4 недель для новичка, среднего и продвинутого уровня, и не меньше 3 недель. Внутри блока: примерно 30% «база», одна неделя «пик» (при блоке от 3 недель), остальное «рост». Новичку до 8 недель плановая разгрузка не нужна (консенсус Delphi, StrongLifts): у него есть сброс −10% после двух неудач в `trainer_logic.next_targets`.

| Недель | Новичок | Средний | Продвинутый |
|---|---|---|---|
| 4 | база, рост, рост, пик | база, рост, пик, разгрузка | база, рост, пик, разгрузка |
| 6 | база ×2, рост ×3, пик | база ×2, рост ×2, пик, разгрузка | то же |
| 8 | база ×2, рост ×4, пик, разгрузка | (база, рост, пик, разгрузка) ×2 | то же |

Вес по фазам не меняется (кроме разгрузки: 85%, −1 подход), потому что вес ведёт `next_targets`. Меняется целевой запас повторов: база 3 → рост 2 → пик 1.

## Безопасность

Противопоказания из каталога (`contraindications`) исключаются всегда, так же как в `trainer_logic.filter_catalog`. `SAFETY` добавляет к ним:

| Ограничение | Дополнительно исключается | Замены | Пределы | Врач |
|---|---|---|---|---|
| knee | — (все упражнения на квадрицепс уже помечены в каталоге) | присед и выпад → ягодичный мост и наклон, разгибание → отведение | — | нет |
| lower_back | — | наклон → мост и сгибания, кор → «большая тройка» McGill, тяги с опорой | — | нет |
| shoulder | провороты с резинкой | жим вверх и махи → тяга к лицу | — | нет |
| wrist | — | отжимания → жим под углом, планка → «мёртвый жук» | — | нет |
| neck | — | скручивания → планка и антиротация | — | нет |
| hip | становая с пола, сумо, «доброе утро», фронтальный присед, глубокий присед в удержании | наклон → румынская тяга | боль ≤2/10 | нет |
| pregnancy | присед и жим со штангой, гакк, жим ногами, отжимания головой вниз, сгибание ног в тренажёре (обычно лёжа на животе), интенсивный пресс, прыжки, бег | мост и наклон → отведение, жим лёжа → жим под углом или в тренажёре | ≥10 повторов, RPE ≤7, отдых ≥90 с, удержание ≤30 с, без тяжёлых дней и интервалов | обязательно |
| heart_bp | фронтальный присед, жим стоя, «доброе утро», взятие гири, гакк, «скалолаз», «медвежья походка» | интервалы → ровное кардио | ≥8 повторов, RPE ≤7, отдых ≥90 с, удержание ≤30 с, без тяжёлых дней | обязательно |

Контроль боли (модель Silbernagel, консервативная версия):

- 0–3 из 10 — продолжать;
- 4–5 — снизить вес или амплитуду;
- больше 5, острая боль или утром хуже — заменить упражнение.

У каждого ограничения есть свои красные флаги. Общие флаги в `SAFETY_GENERAL`: сердце, перегрев, рабдомиолиз, острая травма сустава. Для беременности и давления/сердца красные флаги идут и в выжимку для ИИ (`RED_FLAGS_BRIEF`), и в советы программы (`DOCTOR_TIPS` через `safety_tips`) вместе с требованием согласовать нагрузку с врачом. При беременности принцип «хип-траст 2–3 раза в неделю» в выжимку не попадает (`PRINCIPLE_CONFLICTS`).

## Аудит недели (`audit_week`)

| Серьёзность | Код | Когда |
|---|---|---|
| high | `contraindicated` | упражнение противопоказано по каталогу |
| high | `volume_missing` | у крупной мышцы 0 подходов, хотя под анкету есть упражнения |
| high | `pull_push_ratio` | тяги меньше половины жимов (если тяги возможны) |
| high | `time_over` | день дольше плана на 35% и больше |
| high | `volume_high` | объём выше MRV при приоритете |
| medium | `volume_low` / `volume_high` | вне `volume_targets`; выше максимума больше чем на 25% |
| medium | `pull_push_ratio` | тяг меньше, чем жимов |
| medium | `no_vertical_pull`, `no_horizontal_pull` | нет тяги нужного направления, хотя она возможна |
| medium | `isolation_before_compound` | изоляция раньше базового (кроме кора и икр) |
| medium | `lower_back_overload` | два тяжёлых упражнения на поясницу в один день |
| medium | `not_recommended` | упражнение из `SAFETY.exclude_slugs` или запрещённого паттерна |
| medium | `intensity_limit` | нарушены пределы повторов или RPE при ограничении |
| medium | `unknown_slug`, `equipment_unavailable`, `time_over` (+10%), `too_many_exercises` | по названию |
| low | `frequency_low`, `session_volume_high`, `too_difficult`, `missing_warmup`, `missing_cooldown` | подсказки |

Если все силовые дни уже заняты на 85% времени или больше (или упираются в лимит упражнений), нехватка объёма помечается как low с пометкой «не хватает времени или дней». Это следствие выбора пользователя, а не ошибка схемы.

Проверка перебором (33 000 анкет):

- дни 2–6 × уровень × оборудование (с турником и скамьёй и без) × цель × 11 наборов ограничений × 4 акцента × 30 и 60 минут;
- `build_week` не падает, использует только существующие slug и не назначает противопоказанных и исключённых упражнений;
- выборочно (каждая 7-я анкета) неделя проходит `normalize_program` без замен и `expand_program`;
- `audit_week` не находит ни одной проблемы уровня high.

## Что здесь практическая оценка, а не эксперимент

- Ориентиры RP (MV, MEV, MAV, MRV) и доли `factor` и `_MAV_SHARE`, выведенные из них.
- Расчёт времени (`TIME_MODEL`, `SESSION_BUDGET`): подход ≈ 3,5 с на повтор, переход 60 с, разминочные подходы 60–120 с.
- Замены при боли в запястье, шее и тазобедренном суставе (E3 Rehab, уверенность низкая), лэндмайн-жим (в каталоге его нет).
- Ступени сложности упражнений, порог «2 неудачи → −10%» и схемы популярных программ.
- Правило «интервалы не чаще 2 раз в неделю» перенесено с выносливых спортсменов на обычных людей.
- Числа Robinson 2024 взяты из препринта v2, Remmert 2025 и Enes 2025 — препринты. Starting Strength и PHUL разобраны по пересказам (официальные сайты отдают 403), точные подходы Nippard Fundamentals — по пересказу пользователя Boostcamp.

## Источники

Тип источника указан в `SOURCES[id]["type"]`. В скобках — id, на который ссылаются правила.

### Метаанализы и систематические обзоры (36)

- Schoenfeld BJ, Ogborn D, Krieger JW (2017). [Dose-response relationship between weekly resistance training volume and increases in muscle mass (J Sports Sci)](https://doi.org/10.1080/02640414.2016.1210197) (`schoenfeld2017_volume`)
- Pelland JC, Remmert JF, Robinson ZP, Hinson SR, Zourdos MC (2025). [The Resistance Training Dose Response: Meta-Regressions Exploring the Effects of Weekly Volume and Frequency (Sports Med)](https://doi.org/10.1007/s40279-025-02344-w) — онлайн 2025, том 2026; препринт SportRxiv 2024 (`pelland2025_dose`)
- Baz-Valle E, Balsalobre-Fernández C, Alix-Fages C, Santos-Concejero J (2022). [A Systematic Review of The Effects of Different Resistance Training Volumes on Muscle Hypertrophy (J Hum Kinet)](https://doi.org/10.2478/hukin-2022-0017) (`baz_valle2022_volume`)
- Remmert JF, Pelland JC, Robinson ZP, Hinson SR, Zourdos MC (2025). [Is There Too Much of a Good Thing? Meta-Regressions of the Effect of Per-Session Volume (SportRxiv 537)](https://sportrxiv.org/index.php/server/preprint/view/537) — препринт без рецензии (`remmert2025_session`)
- Schoenfeld BJ, Ogborn D, Krieger JW (2016). [Effects of Resistance Training Frequency on Measures of Muscle Hypertrophy (Sports Med)](https://doi.org/10.1007/s40279-016-0543-8) (`schoenfeld2016_freq`)
- Schoenfeld BJ, Grgic J, Krieger J (2019). [How many times per week should a muscle be trained to maximize muscle hypertrophy? (J Sports Sci)](https://doi.org/10.1080/02640414.2018.1555906) (`schoenfeld2019_freq`)
- Grgic J, Schoenfeld BJ, Davies TB et al. (2018). [Effect of Resistance Training Frequency on Gains in Muscular Strength (Sports Med)](https://doi.org/10.1007/s40279-018-0872-x) (`grgic2018_freq_strength`)
- Currier BS, McLeod JC et al. (2023). [Resistance training prescription for muscle strength and hypertrophy in healthy adults: Bayesian network meta-analysis (BJSM)](https://doi.org/10.1136/bjsports-2023-106807) (`currier2023_nma`)
- Schoenfeld BJ, Grgic J, Ogborn D, Krieger JW (2017). [Strength and Hypertrophy Adaptations Between Low- vs. High-Load Resistance Training (J Strength Cond Res)](https://doi.org/10.1519/jsc.0000000000002200) (`schoenfeld2017_load`)
- Lopez P, Radaelli R, Taaffe DR et al. (2021). [Resistance Training Load Effects on Muscle Hypertrophy and Strength Gain: Network Meta-analysis (Med Sci Sports Exerc)](https://doi.org/10.1249/mss.0000000000002585) (`lopez2021_load`)
- Refalo MC, Helms ER, Trexler ET, Hamilton DL, Fyfe JJ (2023). [Influence of Resistance Training Proximity-to-Failure on Skeletal Muscle Hypertrophy (Sports Med)](https://doi.org/10.1007/s40279-022-01784-y) (`refalo2023_failure`)
- Robinson ZP, Pelland JC, Remmert JF, Refalo MC, Jukic I, Steele J, Zourdos MC (2024). [Exploring the Dose-Response Relationship Between Estimated Resistance Training Proximity to Failure, Strength Gain, and Muscle Hypertrophy (Sports Med)](https://doi.org/10.1007/s40279-024-02069-2) — числа взяты из препринта v2 (`robinson2024_rir`)
- Singer A, Wolf M, Generoso L et al. (2024). [Give it a rest: Bayesian meta-analysis on the effect of inter-set rest interval duration on muscle hypertrophy (Front Sports Act Living)](https://doi.org/10.3389/fspor.2024.1429789) (`singer2024_rest`)
- Nunes JP, Grgic J, Cunha PM et al. (2021). [What influence does resistance exercise order have on muscular strength gains and muscle hypertrophy? (Eur J Sport Sci)](https://doi.org/10.1080/17461391.2020.1733672) (`nunes2021_order`)
- Schoenfeld BJ, Ogborn DI, Krieger JW (2015). [Effect of repetition duration during resistance training on muscle hypertrophy (Sports Med)](https://doi.org/10.1007/s40279-015-0304-0) (`schoenfeld2015_tempo`)
- Moesgaard L, Beck MM, Christiansen L, Aagaard P, Lundbye-Jensen J (2022). [Effects of Periodization on Strength and Muscle Hypertrophy in Volume-Equated Resistance Training Programs (Sports Med)](https://doi.org/10.1007/s40279-021-01636-1) (`moesgaard2022_period`)
- Grgic J, Mikulic P, Podnar H, Pedisic Z (2017). [Effects of linear and daily undulating periodized resistance training programs on muscle hypertrophy (PeerJ)](https://doi.org/10.7717/peerj.3695) (`grgic2017_dup`)
- Morton RW, Murphy KT, McKellar SR et al. (2018). [Effect of protein supplementation on resistance training-induced gains in muscle mass and strength (Br J Sports Med)](https://doi.org/10.1136/bjsports-2017-097608) (`morton2018_protein`)
- Murphy C, Koehler K (2022). [Energy deficiency impairs resistance training gains in lean mass but not strength (Scand J Med Sci Sports)](https://doi.org/10.1111/sms.14075) (`murphy2022_deficit`)
- Sardeli AV, Komatsu TR, Mori MA, Gáspari AF, Chacon-Mikahil MPT (2018). [Resistance Training Prevents Muscle Loss Induced by Caloric Restriction in Obese Elderly Individuals (Nutrients)](https://doi.org/10.3390/nu10040423) (`sardeli2018_restriction`)
- Paluch AE et al. (2022). [Daily steps and all-cause mortality: a meta-analysis of 15 international cohorts (Lancet Public Health)](https://doi.org/10.1016/s2468-2667(21)00302-9) (`paluch2022_steps`)
- Viana RB, Naves JPA, Coswig VS et al. (2019). [Is interval training the magic bullet for fat loss? MICT vs HIIT (Br J Sports Med)](https://doi.org/10.1136/bjsports-2018-099928) (`viana2019_hiit`)
- Schumann M, Feuerbacher JF, Sünkeler M et al. (2022). [Compatibility of Concurrent Aerobic and Strength Training for Skeletal Muscle Size and Function (Sports Med)](https://doi.org/10.1007/s40279-021-01587-7) (`schumann2022_concurrent`)
- Eddens L, van Someren K, Howatson G (2018). [The Role of Intra-Session Exercise Sequence in the Interference Effect (Sports Med)](https://doi.org/10.1007/s40279-017-0784-1) (`eddens2018_sequence`)
- Wilson JM, Marin PJ, Rhea MR et al. (2012). [Concurrent training: a meta-analysis examining interference of aerobic and resistance exercises (J Strength Cond Res)](https://doi.org/10.1519/jsc.0b013e31823a3e2d) (`wilson2012_concurrent`)
- Behm DG, Blazevich AJ, Kay AD, McHugh M (2016). [Acute effects of muscle stretching on physical performance, range of motion, and injury incidence (Appl Physiol Nutr Metab)](https://doi.org/10.1139/apnm-2015-0235) (`behm2016_stretch`)
- Lauersen JB, Bertelsen DM, Andersen LB (2014). [The effectiveness of exercise interventions to prevent sports injuries (Br J Sports Med)](https://doi.org/10.1136/bjsports-2013-092538) (`lauersen2014_injury`)
- Zhang X, Weakley J, Li H, Li Z, García-Ramos A (2025). [Superset Versus Traditional Resistance Training Prescriptions (Sports Med)](https://doi.org/10.1007/s40279-025-02176-8) (`zhang2025_superset`)
- Roberts BM, Nuckols G, Krieger JW (2020). [Sex Differences in Resistance Training: A Systematic Review and Meta-Analysis (J Strength Cond Res)](https://doi.org/10.1519/JSC.0000000000003521) (`roberts2020_sex`)
- Colenso-Semple LM, D'Souza AC, Elliott-Sale KJ, Phillips SM (2023). [Current evidence shows no influence of women's menstrual cycle phase on acute strength performance or adaptations (Front Sports Act Living)](https://doi.org/10.3389/fspor.2023.1054542) (`colenso2023_cycle`)
- Lopes JSS et al. (2019). [Effects of training with elastic resistance versus conventional resistance on muscular strength (SAGE Open Med)](https://doi.org/10.1177/2050312119831116) (`lopes2019_bands`)
- Smith BE, Hendrick P, Smith TO et al. (2017). [Should exercises be painful in the management of chronic musculoskeletal pain? (Br J Sports Med)](https://doi.org/10.1136/bjsports-2016-097383) (`smith2017_painful_ex`)
- Hayden JA, Ellis J, Ogilvie R, Malmivaara A, van Tulder MW (2021). [Exercise therapy for chronic low back pain (Cochrane)](https://doi.org/10.1002/14651858.CD009790.pub2) (`hayden2021_lbp`)
- Rudin LR, Dunn L, Lyons K et al. (2021). [Professional Exercise Recommendations for Healthy Women Who Are Pregnant: A Systematic Review (Women's Health Reports)](https://pmc.ncbi.nlm.nih.gov/articles/PMC8524738/) (`rudin2021_pregnancy`)
- Edwards JJ, Deenmamode AHP, Griffiths M et al. (2023). [Exercise training and resting blood pressure: pairwise and network meta-analysis of 270 RCTs (Br J Sports Med)](https://doi.org/10.1136/bjsports-2022-106503) (`edwards2023_bp`)
- Sherrington C, Fairhall NJ, Wallbank GK et al. (2019). [Exercise for preventing falls in older people living in the community (Cochrane)](https://doi.org/10.1002/14651858.CD012424.pub2) (`sherrington2019_falls`)

### Рандомизированные исследования (14)

- Schoenfeld BJ, Contreras B, Krieger J et al. (2019). [Resistance Training Volume Enhances Muscle Hypertrophy but Not Strength in Trained Men (Med Sci Sports Exerc)](https://doi.org/10.1249/mss.0000000000001764) (`schoenfeld2019_volume_rct`)
- Bickel CS, Cross JM, Bamman MM (2011). [Exercise dosing to retain resistance training adaptations in young and older adults (Med Sci Sports Exerc)](https://doi.org/10.1249/mss.0b013e318207c15d) (`bickel2011_maintenance`)
- Refalo MC, Helms ER, Robinson ZP, Hamilton DL, Fyfe JJ (2024). [Similar muscle hypertrophy following eight weeks of resistance training to momentary muscular failure or with repetitions-in-reserve (J Sports Sci)](https://doi.org/10.1080/02640414.2024.2321021) (`refalo2024_rct`)
- Zourdos MC, Klemp A, Dolan C et al. (2016). [Novel Resistance Training-Specific Rating of Perceived Exertion Scale Measuring Repetitions in Reserve (J Strength Cond Res)](https://doi.org/10.1519/jsc.0000000000001049) (`zourdos2016_rpe`)
- Schoenfeld BJ, Pope ZK, Benik FM et al. (2016). [Longer Interset Rest Periods Enhance Muscle Strength and Hypertrophy in Resistance-Trained Men (J Strength Cond Res)](https://doi.org/10.1519/jsc.0000000000001272) (`schoenfeld2016_rest`)
- Maeo S et al. (2023). [Triceps brachii hypertrophy is substantially greater after elbow extension training performed in the overhead versus neutral arm position (Eur J Sport Sci)](https://doi.org/10.1080/17461391.2022.2100279) (`maeo2023_overhead`)
- Plotkin D, Coleman M, Van Every D et al. (2022). [Progressive overload without progressing load? Load or repetition progression (PeerJ)](https://doi.org/10.7717/peerj.14142) (`plotkin2022_reps`)
- Coleman M, Burke R, Augustin F et al. (2024). [Gaining more from doing less? The effects of a one-week deload period during supervised resistance training (PeerJ)](https://doi.org/10.7717/peerj.16777) (`coleman2024_deload`)
- Abad CC, Prado ML, Ugrinowitsch C, Tricoli V, Barroso R (2011). [Combination of general and specific warm-ups improves leg-press one repetition maximum (J Strength Cond Res)](https://doi.org/10.1519/jsc.0b013e3181e8611b) (`abad2011_warmup`)
- Enes A, Mohan A, Pinero A et al. (2025). [Warming up to improved performance? Effects of different specific warm-up protocols (SportRxiv 559)](https://sportrxiv.org/index.php/server/preprint/view/559) (`enes2025_warmup`)
- Ribeiro B, Pereira A, Neves PP et al. (2020). [The Role of Specific Warm-up during Bench Press and Squat Exercises (Int J Environ Res Public Health)](https://doi.org/10.3390/ijerph17186882) (`ribeiro2020_warmup`)
- Kikuchi N, Nakazato K (2017). [Low-load bench press and push-up induce similar muscle hypertrophy and strength gain (J Exerc Sci Fit)](https://doi.org/10.1016/j.jesf.2017.06.003) (`kikuchi2017_pushup`)
- Plotkin DL, Rodas MA, Vigotsky AD et al. (2023). [Hip thrust and back squat training elicit similar gluteus muscle hypertrophy (Front Physiol)](https://doi.org/10.3389/fphys.2023.1279170) (`plotkin2023_hipthrust`)
- Silbernagel KG, Thomeé R, Eriksson BI, Karlsson J (2007). [Continued sports activity, using a pain-monitoring model, during rehabilitation in Achilles tendinopathy (Am J Sports Med)](https://doi.org/10.1177/0363546506298279) (`silbernagel2007_pain`)

### Позиционные заявления и клинические руководства (16)

- American College of Sports Medicine (2009). [Progression models in resistance training for healthy adults (Med Sci Sports Exerc 41:687-708)](https://doi.org/10.1249/mss.0b013e3181915670) (`acsm2009_progression`)
- Bull FC, Al-Ansari SS, Biddle S et al. (2020). [World Health Organization 2020 guidelines on physical activity and sedentary behaviour (Br J Sports Med)](https://doi.org/10.1136/bjsports-2020-102955) (`who2020`)
- Donnelly JE, Blair SN, Jakicic JM et al. (2009). [ACSM Position Stand: physical activity intervention strategies for weight loss and prevention of weight regain (Med Sci Sports Exerc)](https://doi.org/10.1249/mss.0b013e3181949333) (`donnelly2009_weightloss`)
- Garber CE, Blissmer B, Deschenes MR et al. (2011). [Quantity and quality of exercise for developing and maintaining fitness in apparently healthy adults (Med Sci Sports Exerc)](https://doi.org/10.1249/MSS.0b013e318213fefb) (`garber2011_acsm`)
- Willy RW et al. (обзор AAFP 2020) (2019). [Patellofemoral Pain: Guidelines from the American Physical Therapy Association](https://www.aafp.org/afp/2020/1001/p442) (`willy2019_pfp`)
- Bannuru RR, Osani MC, Vaysbrot EE et al. (2019). [OARSI guidelines for the non-surgical management of knee, hip, and polyarticular osteoarthritis (Osteoarthritis Cartilage)](https://doi.org/10.1016/j.joca.2019.06.011) (`oarsi2019`)
- Oliveira CB, Maher CG, Pinto RZ et al. (2018). [Clinical practice guidelines for non-specific low back pain in primary care: an updated overview (Eur Spine J)](https://doi.org/10.1007/s00586-018-5673-2) (`oliveira2018_lbp`)
- Blanpied PR, Gross AR, Elliott JM et al. (2017). [Neck Pain: Revision 2017. Clinical Practice Guidelines (JOSPT)](https://doi.org/10.2519/jospt.2017.0302) (`blanpied2017_neck`)
- American College of Obstetricians and Gynecologists (2020). [Physical Activity and Exercise During Pregnancy and the Postpartum Period, Committee Opinion No. 804](https://doi.org/10.1097/AOG.0000000000003772) — сверено по пересказу DONA (`acog804_2020`)
- Mottola MF, Davenport MH, Ruchat SM et al. (2018). [2019 Canadian guideline for physical activity throughout pregnancy (Br J Sports Med)](https://csepguidelines.ca/guidelines/pregnancy/) (`mottola2019_canada`)
- Paluch AE, Boyer WR, Franklin BA et al. (AHA; тезисы по обзору ACC) (2023). [Resistance Exercise Training in Individuals With and Without Cardiovascular Disease: 2023 Update](https://www.acc.org/Latest-in-Cardiology/ten-points-to-remember/2023/12/19/15/33/resistance-exercise-training) (`paluch2023_aha`)
- Pelliccia A, Sharma S, Gati S et al. (2020). [2020 ESC Guidelines on sports cardiology and exercise in patients with cardiovascular disease (Eur Heart J)](https://academic.oup.com/eurheartj/article/42/1/17/5898937) (`pelliccia2020_esc`)
- American College of Sports Medicine (н/д). [Exercise for the Prevention and Treatment of Hypertension — Implications and Application](https://acsm.org/exercise-for-the-prevention-and-treatment-of-hypertension/) (`acsm_hypertension`)
- Riebe D, Franklin BA, Thompson PD et al. (2015). [Updating ACSM's Recommendations for Exercise Preparticipation Health Screening (Med Sci Sports Exerc)](https://doi.org/10.1249/MSS.0000000000000664) (`riebe2015_screening`)
- Fragala MS, Cadore EL, Dorgo S et al. (2019). [Resistance Training for Older Adults: NSCA Position Statement (J Strength Cond Res)](https://doi.org/10.1519/JSC.0000000000003230) (`fragala2019_nsca_older`)
- CDC / NIOSH (н/д). [Signs and Symptoms of Rhabdomyolysis](https://www.cdc.gov/niosh/rhabdo/signs-symptoms/index.html) (`cdc_rhabdo`)

### Консенсусы и обзоры экспертов (17)

- Schoenfeld BJ, Grgic J, Van Every DW, Plotkin DL (2021). [Loading Recommendations for Muscle Strength, Hypertrophy, and Local Endurance: A Re-Examination of the Repetition Continuum (Sports)](https://doi.org/10.3390/sports9020032) (`schoenfeld2021_continuum`)
- Iversen VM, Norum M, Schoenfeld BJ, Fimland MS (2021). [No Time to Lift? Designing Time-Efficient Training Programs for Strength and Hypertrophy (Sports Med)](https://doi.org/10.1007/s40279-021-01490-1) (`iversen2021_time`)
- Simão R, de Salles BF, Figueiredo T, Dias I, Willardson JM (2012). [Exercise order in resistance training (Sports Med)](https://doi.org/10.2165/11597240-000000000-00000) (`simao2012_order`)
- Bell L, Strafford BW, Coleman M, Androulakis Korakakis P, Nolan D (2023). [Integrating Deloading into Strength and Physique Sports Training Programmes: An International Delphi Consensus (Sports Med Open)](https://doi.org/10.1186/s40798-023-00633-0) (`bell2023_deload`)
- Helms ER, Aragon AA, Fitschen PJ (2014). [Evidence-based recommendations for natural bodybuilding contest preparation: nutrition and supplementation (JISSN)](https://doi.org/10.1186/1550-2783-11-20) (`helms2014_nutrition`)
- Helms ER, Fitschen PJ, Aragon AA, Cronin J, Schoenfeld BJ (2015). [Recommendations for natural bodybuilding contest preparation: resistance and cardiovascular training (J Sports Med Phys Fitness)](https://ro.ecu.edu.au/ecuworkspost2013/1869/) (`helms2015_training`)
- Storoschuk KL, Moran-MacDonald A, Gibala MJ, Gurd BJ (2025). [Much Ado About Zone 2: A Narrative Review (Sports Med)](https://doi.org/10.1007/s40279-025-02261-y) (`storoschuk2025_zone2`)
- Seiler S (2010). [What is best practice for training intensity and duration distribution in endurance athletes? (IJSPP)](https://doi.org/10.1123/ijspp.5.3.276) (`seiler2010_distribution`)
- Rønnestad BR, Mujika I (2014). [Optimizing strength training for running and cycling endurance performance: A review (Scand J Med Sci Sports)](https://pubmed.ncbi.nlm.nih.gov/23914932/) (`ronnestad2014_endurance`)
- Van Hooren B, Peake JM (2018). [Do We Need a Cool-Down After Exercise? (Sports Med)](https://doi.org/10.1007/s40279-018-0916-2) (`vanhooren2018_cooldown`)
- Stiell IG, Wells GA, Hoag RH et al. (1997). [Implementation of the Ottawa Knee Rule (JAMA)](https://doi.org/10.1001/jama.1997.03550230051036) (`stiell1997_ottawa`)
- Finucane LM, Downie A, Mercer C et al. (2020). [International Framework for Red Flags for Potential Serious Spinal Pathologies (JOSPT)](https://doi.org/10.2519/jospt.2020.9971) (`finucane2020_redflags`)
- Giangregorio LM, Papaioannou A, MacIntyre NJ et al. (2014). [Too Fit To Fracture: exercise recommendations for individuals with osteoporosis (Osteoporos Int)](https://doi.org/10.1007/s00198-013-2523-2) (`giangregorio2014_osteo`)
- Rushton A, Carlesso LC, Flynn T et al. (2023). [International IFOMPT Cervical Framework (JOSPT)](https://doi.org/10.2519/jospt.2022.11147) (`rushton2023_cervical`)
- Goom T, Donnelly G, Brockwell E (2019). [Returning to running postnatal — guidelines for professionals](https://absolute.physio/wp-content/uploads/2019/09/returning-to-running-postnatal-guidelines.pdf) (`goom2019_postnatal`)
- Ghadieh AS, Saab B (2015). [Evidence for exercise training in the management of hypertension in adults (Can Fam Physician)](https://pmc.ncbi.nlm.nih.gov/articles/PMC4369613/) (`ghadieh2015_htn`)
- Roberts WO, Armstrong LE, Sawka MN et al. (2023). [ACSM Expert Consensus Statement on Exertional Heat Illness (Curr Sports Med Rep)](https://doi.org/10.1249/JSR.0000000000001058) (`roberts2023_heat`)

### Практики (оценки тренеров, биомеханика, опросы) (23)

- Rogerson D, Nolan D, Androulakis Korakakis P, Immonen V, Wolf M, Bell L (2024). [Deloading Practices in Strength and Physique Sports: A Cross-sectional Survey (Sports Med Open)](https://doi.org/10.1186/s40798-024-00691-y) (`rogerson2024_deload`)
- Alex Viada (2016–2024). [5 Questions with Alex Viada (Juggernaut Training Systems); интервью Rox Lyfe](https://www.jtsstrength.com/5-questions-alex-viada/) (`viada_hybrid`)
- Bret Contreras (2013–2019). [Your Optimal Training Frequency for the Glutes; The Glute Guy's Secrets Part II – Programming](https://bretcontreras.com/your-optimal-training-frequency-for-the-glutes-part-i-exercise-type/) (`contreras_glute_frequency`)
- Israetel M (Renaissance Periodization) (2023–2024). [Training Volume Landmarks for Muscle Growth](https://rpstrength.com/blogs/articles/training-volume-landmarks-muscle-growth) (`rp_landmarks`)
- Israetel M (RP) (2024). [Chest Hypertrophy Training Tips](https://rpstrength.com/blogs/articles/chest-hypertrophy-training-tips) (`rp_chest`)
- Israetel M (RP) (2023). [Back Hypertrophy Training Tips](https://rpstrength.com/blogs/articles/back-hypertrophy-training-tips) (`rp_back`)
- Israetel M (RP) (2024). [Quad Hypertrophy Training Tips](https://rpstrength.com/blogs/articles/quad-hypertrophy-training-tips) (`rp_quads`)
- Israetel M (RP) (2023). [Hamstring Hypertrophy Training Tips](https://rpstrength.com/blogs/articles/hamstring-hypertrophy-training-tips) (`rp_hamstrings`)
- Israetel M (RP) (2024). [Glute Hypertrophy Training Tips](https://rpstrength.com/blogs/articles/glute-hypertrophy-training-tips) (`rp_glutes`)
- Israetel M (RP) (2024). [Side Delt Hypertrophy Training Tips](https://rpstrength.com/blogs/articles/side-delt-hypertrophy-training-tips) (`rp_side_delts`)
- Israetel M (RP) (2024). [Rear Delt Hypertrophy Training Tips](https://rpstrength.com/blogs/articles/rear-delt-hypertrophy-training-tips) (`rp_rear_delts`)
- Israetel M (RP) (2024). [Front Delt Hypertrophy Training Tips](https://rpstrength.com/blogs/articles/front-delt-hypertrophy-training-tips) (`rp_front_delts`)
- Israetel M (RP) (2023). [Bicep Hypertrophy Training Tips](https://rpstrength.com/blogs/articles/bicep-hypertrophy-training-tips) (`rp_biceps`)
- Israetel M (RP) (2024). [Triceps Hypertrophy Training Tips](https://rpstrength.com/blogs/articles/triceps-hypertrophy-training-tips) (`rp_triceps`)
- Israetel M (RP) (2024). [Calves Hypertrophy Training Tips](https://rpstrength.com/blogs/articles/calves-hypertrophy-training-tips) (`rp_calves`)
- Israetel M (RP) (2024). [Ab Hypertrophy Training Tips](https://rpstrength.com/blogs/articles/ab-hypertrophy-training-tips) (`rp_abs`)
- Powers CM, Ho KY, Chen YJ, Souza RB, Farrokhi S (2014). [Patellofemoral joint stress during weight-bearing and non-weight-bearing quadriceps exercises (JOSPT)](https://doi.org/10.2519/jospt.2014.4936) (`powers2014_pfj`)
- McGill SM (1998). [Low back exercises: evidence for improving exercise regimens (Phys Ther)](https://doi.org/10.1093/ptj/78.7.754) (`mcgill1998_back`)
- Swinton PA, Stewart A, Agouris I, Keogh JW, Lloyd R (2011). [A biomechanical analysis of straight and hexagonal barbell deadlifts (J Strength Cond Res)](https://doi.org/10.1519/JSC.0b013e3181e73f87) (`swinton2011_trapbar`)
- Kolber MJ, Cheatham SW, Salamh PA, Hanney WJ (2014). [Characteristics of shoulder impingement in the recreational weight-training population (J Strength Cond Res)](https://doi.org/10.1519/JSC.0000000000000250) (`kolber2014_shoulder`)
- Fees M, Decker T, Snyder-Mackler L, Axe MJ (1998). [Upper extremity weight-training modifications for the injured athlete (Am J Sports Med)](https://doi.org/10.1177/03635465980260052301) (`fees1998_upper`)
- E3 Rehab (н/д). [Wrist Pain Rehab](https://e3rehab.com/wrist-pain-rehab/) (`e3_wrist`)
- E3 Rehab (н/д). [Femoroacetabular Impingement (FAI)](https://e3rehab.com/fai/) (`e3_fai`)

### Программы (16)

- Mark Rippetoe (пересказ Legion Athletics) (2024). [Starting Strength program](https://legionathletics.com/starting-strength-program/) — официальный сайт отдаёт 403, структура по Legion и PowerliftingToWin (`prog_starting_strength`)
- Mehdi Hadim (2015–2024). [StrongLifts 5×5 workout program](https://stronglifts.com/stronglifts-5x5/workout-program/) (`prog_stronglifts`)
- r/Fitness wiki (н/д). [r/Fitness Basic Beginner Routine](https://thefitness.wiki/routines/r-fitness-basic-beginner-routine/) (`prog_bbr`)
- Cody LeFever (r/Fitness wiki) (н/д). [GZCLP](https://thefitness.wiki/routines/gzclp/) (`prog_gzclp`)
- Jim Wendler (н/д). [5/3/1 for Beginners (r/Fitness wiki); 5/3/1: How to Build Pure Strength](https://thefitness.wiki/routines/5-3-1-for-beginners/) (`prog_531`)
- Jim Wendler (н/д). [Boring But Big](https://jimwendler.com/blogs/jimwendler-com/101077382-boring-but-big) (`prog_bbb`)
- Brandon Campbell (пересказ Hevy) (н/д). [PHUL — Power Hypertrophy Upper Lower](https://www.hevyapp.com/phul-power-hypertrophy-upper-lower/) — оригинал muscleandstrength.com отдаёт 403 (`prog_phul`)
- Layne Norton (2011). [PHAT — Power Hypertrophy Adaptive Training](http://simplyshredded.com/mega-feature-layne-norton-training-series-full-powerhypertrophy-routine-updated-2011.html) (`prog_phat`)
- u/Metallicadpa (r/Fitness wiki archive) (н/д). [A Linear Progression Based PPL Program for Beginners](https://thefitness.wiki/reddit-archive/a-linear-progression-based-ppl-program-for-beginners/) (`prog_reddit_ppl`)
- Jeff Nippard (2019). [Fundamentals Hypertrophy Program](https://jeffnippard.com/products/fundamentals-hypertrophy-program) — точные подходы — из пересказа пользователя Boostcamp (`prog_nippard_fundamentals`)
- Jeff Nippard (2024). [The Min-Max Program](https://jeffnippard.com/products/the-min-max-program) (`prog_nippard_minmax`)
- Eric Helms (Muscle and Strength Pyramid, Boostcamp) (2019–2024). [Intermediate Bodybuilding Program](https://www.boostcamp.app/coaches/muscle-and-strength-pyramid/intermediate-bodybuilding-program) (`prog_helms`)
- Greg Nuckols (Boostcamp) (2021). [Greg Nuckols Beginner Program](https://www.boostcamp.app/coaches/greg-nuckols/greg-nuckols-beginner-program) (`prog_nuckols`)
- Arnold Schwarzenegger (пересказ BarBend) (н/д). [Arnold Schwarzenegger workout split](https://barbend.com/arnold-schwarzenegger-workout-split/) (`prog_arnold`)
- Bret Contreras (н/д). [The Glute Guy's Secrets — Part II: Programming](https://bretcontreras.com/the-glute-guys-secrets-the-art-of-glute-building-part-ii-programming/) (`prog_contreras`)
- r/bodyweightfitness (копия вики) (н/д). [Recommended Routine](https://gist.github.com/sgup/f10f1d57e54b7876495f4bafb6d697eb) — reddit недоступен, текст из копии вики (`prog_bwf_rr`)
