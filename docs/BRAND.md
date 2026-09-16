# Fitness Up — оформление

Файл держит в одном месте всё, что относится к внешнему виду продукта за
пределами кода: палитру, правила иллюстраций и готовые промпты для их
генерации.

## Палитра «Спокойный зал»

Источник правды — `frontend/css/tokens.css`. Здесь дубль для дизайна и
для генерации картинок.

| Роль | HEX | Где применяется |
|---|---|---|
| Фон приложения | `#F6F7F4` | подложка всех экранов |
| Поверхность | `#FFFFFF` | карточки, листы, поля ввода |
| Вложенная поверхность | `#EDEFE9` | блок внутри карточки |
| Граница | `#DCE0D6` | контур карточек, разделители |
| Граница контрастная | `#8E978A` | контуры полей и чекбоксов |
| Основной текст | `#171A16` | заголовки и тело |
| Вторичный текст | `#5B6157` | подписи, мета |
| Действие | `#2F6F4E` | кнопки, ссылки, активная вкладка |
| Действие нажатое | `#245A3F` | состояние нажатия |
| Действие мягкое | `#E3EFE8` | подложка иконок и выбранных чипов |
| Успех | `#27784B` | подтверждения |
| Предупреждение | `#9A6000` | перебор нормы, важные заметки |
| Опасность | `#C0392B` | удаление, ошибки |

Контраст проверен по WCAG: текст к фону 16.3:1, вторичный текст к
карточке 6.4:1, белый на кнопке действия 6.0:1. Ухудшать эти пары при
правках нельзя.

## Иконки

Интерфейсные иконки живут в `frontend/js/icons.js` и рисуются кодом:
сетка 24×24, контур толщиной 1.75, скруглённые концы, цвет наследуется
от текста. Генерировать их нейросетью не нужно и вредно — на 24 пикселях
растровая картинка даёт рваные линии и разную толщину штриха, а SVG
масштабируется и перекрашивается сам.

Новая иконка добавляется одной строкой в объект `PATHS` того же файла.

## Иллюстрации

Вот здесь генерация уместна: это крупные картинки в пустых состояниях и
на экранах-приглашениях, где нужен характер, а не точность пиктограммы.

### Общие требования ко всем иллюстрациям

Дописывать к каждому промпту ниже:

```
Flat vector illustration, geometric shapes with rounded corners, no gradients,
no drop shadows, no outlines thicker than 2px. Strictly limited palette:
background #F6F7F4, main shapes #FFFFFF with #DCE0D6 hairline edges, accent
#2F6F4E, soft accent fill #E3EFE8, dark details #171A16, muted details #5B6157.
No text, no letters, no numbers, no logos, no human faces. Calm and confident
mood, plenty of empty space, centred composition, works at 320px wide.
Square 1:1.
```

### 1. Пустой дневник питания

```
An empty plate seen from directly above, with a fork and knife neatly placed
on both sides. A single small sprig of greenery rests on the plate rim. The
composition reads as "ready for the first meal", not as "nothing here".
```

### 2. Программа тренировок ещё не собрана

```
A blank weekly planner board with seven empty slots in a row, and one dumbbell
resting in front of it, as if waiting to be placed into a slot. Simple, no
clutter.
```

### 3. Экран подписки, главная картинка

```
Three stacked cards fanned out slightly: the front one shows an abstract
rising line chart, the middle one an abstract dumbbell silhouette, the back
one an abstract plate. They read as one connected product. Wide 16:9 instead
of square.
```

### 4. Первая тренировка завершена

```
A single dumbbell resting on the floor with a soft circular accent glow behind
it, and three small check marks floating above in an arc. Celebratory but
restrained, no confetti, no fireworks.
```

### 5. Приветствие в онбординге

```
A simple standing figure without facial features, seen from the side in a
relaxed posture, next to a large circular progress ring that is about one
third filled. The figure and the ring are the same height. Reads as "we start
together".
```

### 6. Нет данных для графика прогресса

```
An empty coordinate grid with a faint horizontal baseline and a single small
accent dot placed at the very start of it. Nothing else. Reads as "the first
point is yours to add".
```

## Тексты для BotFather

Имя бота, описание, короткое описание, подпись кнопки меню и приветствие
на `/start` — в истории задач проекта. Ключевое: подпись кнопки меню
короткая («Открыть»), описание до 512 символов, короткое описание до 120.
