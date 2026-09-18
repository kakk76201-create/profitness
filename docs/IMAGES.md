# Fitness Up — фотографии

Все растровые картинки приложения лежат в `frontend/img/` и подключаются
по имени файла. Здесь — что в каждом файле должно быть, где он
показывается и готовый промпт для Nano Banana. Общие правила облика,
палитра и типографика — в `docs/BRAND.md`.

## Сводная таблица

| Файл | Размер | Где показывается | Что на фото |
|---|---|---|---|
| `hero-workout.jpg` | 1200×600 (2:1) | «Сегодня», тёмный блок «Тренировка дня» (`page-today.js`, `.hero--img`) | человек в середине повторения со штангой или гантелями, зал |
| `hero-nutrition.jpg` | 1200×600 (2:1) | зарезервировано под блок питания на «Сегодня» / шапку дневника (в коде пока не подключён) | контейнер с едой и шейкер на тёмной поверхности, натюрморт |
| `hero-premium.jpg` | 1200×600 (2:1) | «Сегодня», блок «Персональные тренировки», когда тренер закрыт подпиской (`page-today.js`) | тренер поправляет технику занимающегося, сцена «работа вдвоём» |
| `goal-lose.jpg` | 640×640 (1:1) | карточка цели «Похудеть» при выборе цели (онбординг, профиль; в коде пока не подключён) | бег или прыжки на скакалке, силуэт в движении |
| `goal-gain.jpg` | 640×640 (1:1) | карточка цели «Набрать» | тяжёлая штанга, момент срыва с пола |
| `goal-keep.jpg` | 640×640 (1:1) | карточка цели «Поддерживать» | спокойное упражнение с гирей или планка, ровный ритм |
| `empty-program.jpg` | 1200×600 (2:1) | вкладка «Тренер», пустое состояние «программа не собрана» (в коде пока не подключён) | пустая стойка с грифом, зал до тренировки |
| `empty-diary.jpg` | 1200×600 (2:1) | дневник питания, пустое состояние «записей нет» (в коде пока не подключён) | пустая тарелка и приборы на тёмном столе сверху |

Сейчас из кода подставляются `hero-workout.jpg` и `hero-premium.jpg`.
Остальные файлы лежат заранее, чтобы при подключении экранов не менять
ни имена, ни размеры.

## Затемнение поверх фото

Поверх любой фотографии в приложении всегда лежит градиент затемнения —
он прописан у `.hero--img` в `frontend/css/style.css` и повторяется
на других блоках с фото:

```
linear-gradient(180deg, rgba(0,0,0,.18) 0%, rgba(0,0,0,.74) 100%)
```

Сверху кадр затемняется на 18 %, снизу — на 74 %. Именно в нижней трети
стоят белый заголовок и оранжевая кнопка. Отсюда два правила:

1. **Фото заполняет весь кадр.** Никаких пустых полос, «полок», рамок и
   свободных зон под текст — читаемость обеспечивает затемнение, а не
   пустое место. Если попросить генератор «оставить место под заголовок»,
   он честно нарисует серую полосу снизу (проверено).
2. **Главный объект — в верхних двух третях.** Низ кадра — естественно
   более тёмная и спокойная часть сцены (пол в тени, тёмный стол), но это
   всё ещё часть фотографии, а не заливка.

**Пропорция геройских кадров — 2:1** (1200×600): блок на телефоне имеет
примерно такую форму (343×176), и кадр 3:2 терял бы верх и низ.

## Как заменить файл

1. Сгенерировать кадр по промпту ниже. Nano Banana отдаёт PNG; пропорцию
   задавать в генераторе. Если снизу всё же появилась серая полоса —
   срезать её и уже потом приводить к 2:1.
2. Уменьшить до указанного размера (не больше; меньше тоже не нужно —
   картинка растягивается на всю ширину экрана и станет мыльной).
3. Сохранить как **прогрессивный JPEG**, качество **80–85**, без
   метаданных. Вес **не больше 150 КБ** — файлы грузятся через мобильную
   сеть при каждом открытии, а Telegram WebView не всегда держит кеш.
4. Положить в `frontend/img/` **под тем же именем**: код ссылается на
   имя, переименование сломает экран.
   После замены поднять `IMG_VERSION` в `frontend/js/app.js` — иначе
   Telegram покажет закешированную старую картинку.
5. Открыть экран в обеих темах и убедиться, что заголовок читается и
   нижняя треть не ушла в чёрное.

Пример конвертации через ImageMagick:

```
magick in.png -resize 1200x600^ -gravity center -extent 1200x600 \
  -strip -interlace JPEG -quality 82 -sampling-factor 4:2:0 hero-workout.jpg
```

Проверить прогрессивность и вес:

```
magick identify -format "%w×%h %[interlace] %b\n" hero-workout.jpg
```

## Общая часть промпта

Каждый промпт ниже уже содержит эти требования, но если генерируете
что-то новое — дописывайте их целиком:

```
Editorial sports photography, moody gym lighting, high contrast.
Near-black background #111113, deep shadows, one warm orange accent light
#F5570B (rim light or a single practical light source). Natural skin
tones, real sweat, no glamour retouching. The scene fills the entire
frame edge to edge: no blank areas, no bands, no borders, no letterboxing,
no empty space reserved for text. The main subject sits in the upper two
thirds; the bottom of the frame stays naturally darker and quieter but
remains part of the scene. No text, no letters, no numbers, no logos, no
brand marks, no watermark. Shot on a 35mm lens at eye level, shallow depth of
field, slight film grain.
```

Негатив-подсказки, общие для всех кадров:

```
text, letters, numbers, typography, logo, brand name, watermark,
signature, smiling at camera, posing, looking into the lens, stock photo,
studio white background, bright background, neon blue, neon purple, green
tint, oversaturated, HDR halo, glossy skin, plastic skin, extra fingers,
deformed hands, duplicated limbs, cropped head, blurry, low resolution,
jpeg artifacts, cluttered background, mirror selfie, collage, frame,
border, vignette too strong
```

## Промпты по файлам

### hero-workout.jpg — 1200×600

Тренировка дня. Кадр про усилие: середина повторения, а не финальная
поза.

```
Editorial sports photography, aspect ratio 2:1, wide horizontal. A fit person
mid-repetition of a barbell squat in a dark gym, seen from a low three-
quarter angle, face turned away from the camera, eyes on the floor.
Muscles under tension, real effort, chalk dust in the air. Near-black
background #111113, deep shadows, one warm orange accent light #F5570B
as a rim light from the upper left catching the shoulders and the bar.
High contrast, moody gym lighting. The scene fills the entire frame edge to edge: no blank areas, no bands, no borders, no letterboxing, no empty space reserved for text. The main subject sits in the upper two thirds; the bottom of the frame stays naturally darker and quieter (floor or table in shadow) but remains part of the scene. No text, no letters, no logos, no brand marks, no
watermark. 35mm lens, shallow depth of field, slight film grain.
```

### hero-nutrition.jpg — 1200×600

Питание. Натюрморт без людей: контейнер с едой и шейкер. Еда узнаваемая,
но не «фуд-фото» с боке и салфетками.

```
Editorial sports photography, aspect ratio 2:1, wide horizontal. Overhead
three-quarter view of a black meal-prep container with grilled chicken,
rice and greens, next to a matte black protein shaker, on a dark concrete
gym bench. Near-black background #111113, deep shadows, one warm orange
accent light #F5570B from the side giving the food a warm edge. High
contrast, moody gym lighting, appetising but restrained. The scene fills the entire frame edge to edge: no blank areas, no bands, no borders, no letterboxing, no empty space reserved for text. The main subject sits in the upper two thirds; the bottom of the frame stays naturally darker and quieter (floor or table in shadow) but remains part of the scene. No text, no letters, no logos, no packaging labels, no
brand marks, no watermark. 50mm lens, shallow depth of field, slight
film grain.
```

### hero-premium.jpg — 1200×600

Приглашение в подписку: тренер рядом. Кадр про внимание и контроль
техники, а не про «продажу».

```
Editorial sports photography, aspect ratio 2:1, wide horizontal. A coach
standing beside an athlete who is performing a dumbbell row, the coach's
hand lightly correcting the athlete's back position. Both faces turned
away from the camera, focused on the movement, no smiling. Near-black
background #111113, deep shadows, one warm orange accent light #F5570B
as a rim light outlining both figures. High contrast, moody gym lighting,
sense of trust and focus. The scene fills the entire frame edge to edge: no blank areas, no bands, no borders, no letterboxing, no empty space reserved for text. The main subject sits in the upper two thirds; the bottom of the frame stays naturally darker and quieter (floor or table in shadow) but remains part of the scene.
No text, no letters, no logos, no brand marks, no watermark. 35mm lens,
shallow depth of field, slight film grain.
```

### goal-lose.jpg — 640×640

Цель «Похудеть». Кардио, лёгкость, скорость.

```
Editorial sports photography, aspect ratio 1:1, square. A runner mid-
stride on a dark indoor track, captured from the side with slight motion
blur on the legs, torso sharp, face turned away from the camera. Near-
black background #111113, deep shadows, one warm orange accent light
#F5570B as a rim light from behind outlining the body. High contrast,
moody lighting, sense of speed and lightness. The scene fills the entire frame edge to edge: no blank areas, no bands, no borders, no letterboxing, no empty space reserved for text. The main subject sits in the upper two thirds; the bottom of the frame stays naturally darker and quieter (floor or table in shadow) but remains part of the scene. No text, no letters, no logos, no brand marks, no
watermark. 35mm lens, slight film grain.
```

### goal-gain.jpg — 640×640

Цель «Набрать». Тяжёлая штанга, масса, сила.

```
Editorial sports photography, aspect ratio 1:1, square. Close three-
quarter view of a heavily loaded barbell at the moment it leaves the
floor in a deadlift, the athlete's hands and forearms in frame, torso
cropped, no face visible. Bumper plates, chalk on the knurling. Near-
black background #111113, deep shadows, one warm orange accent light
#F5570B from the upper right catching the bar and the plates. High
contrast, moody gym lighting, weight and power. The scene fills the entire frame edge to edge: no blank areas, no bands, no borders, no letterboxing, no empty space reserved for text. The main subject sits in the upper two thirds; the bottom of the frame stays naturally darker and quieter (floor or table in shadow) but remains part of the scene. No text, no letters, no numbers on the
plates, no logos, no brand marks, no watermark. 50mm lens, slight film
grain.
```

### goal-keep.jpg — 640×640

Цель «Поддерживать». Ровный ритм, контроль, без надрыва.

```
Editorial sports photography, aspect ratio 1:1, square. A person holding
a kettlebell in the rack position, standing tall and steady, seen from
the side, face turned away from the camera, calm breathing. Near-black
background #111113, deep shadows, one warm orange accent light #F5570B
as a soft rim light from behind. High contrast, moody gym lighting, sense
of balance and control rather than effort. The scene fills the entire frame edge to edge: no blank areas, no bands, no borders, no letterboxing, no empty space reserved for text. The main subject sits in the upper two thirds; the bottom of the frame stays naturally darker and quieter (floor or table in shadow) but remains part of the scene. No text, no letters, no logos, no brand marks, no watermark.
35mm lens, slight film grain.
```

### empty-program.jpg — 1200×600

Пустое состояние «Тренер»: программа ещё не собрана. Зал до тренировки,
без людей, читается как «всё готово, начинай», а не «здесь пусто».

```
Editorial sports photography, aspect ratio 2:1, wide horizontal. An empty
squat rack with a bare barbell resting on the J-hooks in a dark, quiet
gym before the session, no people. Near-black background #111113, deep
shadows, one warm orange accent light #F5570B from a single lamp above
the rack, catching the bar. High contrast, moody gym lighting, calm
anticipation. The scene fills the entire frame edge to edge: no blank areas, no bands, no borders, no letterboxing, no empty space reserved for text. The main subject sits in the upper two thirds; the bottom of the frame stays naturally darker and quieter (floor or table in shadow) but remains part of the scene. No
text, no letters, no logos, no brand marks, no watermark. 35mm lens,
slight film grain.
```

### empty-diary.jpg — 1200×600

Пустое состояние дневника: записей нет. Тарелка чистая, приборы на
месте — «готово к первому приёму».

```
Editorial sports photography, aspect ratio 2:1, wide horizontal. Top-down view
of a clean matte black plate with a fork and knife placed on both sides,
on a dark slate table, no food, no people. Near-black background #111113,
deep shadows, one warm orange accent light #F5570B from the side giving
the plate rim a thin warm edge. High contrast, moody lighting, minimal
and ready. The scene fills the entire frame edge to edge: no blank areas, no bands, no borders, no letterboxing, no empty space reserved for text. The main subject sits in the upper two thirds; the bottom of the frame stays naturally darker and quieter (floor or table in shadow) but remains part of the scene. No text, no letters,
no logos, no brand marks, no watermark. 50mm lens, slight film grain.
```

## Картинки, которых пока нет

Два кадра для Telegram в той же палитре. Файлов в `frontend/img/` для
них нет: стартовое изображение загружается в бота, обложка пейволла
отправляется сообщением. Отправлять в Telegram тоже JPEG, до 150 КБ.

### Стартовое сообщение бота — 16:9 (1280×720)

Показывается при `/start`. Нижняя треть под текст не нужна: подпись
идёт под картинкой в самом сообщении, зато картинка должна читаться в
превью 300 пикселей шириной.

```
Editorial sports photography, aspect ratio 16:9, horizontal. Wide shot of
a dark gym floor with a single athlete walking towards a loaded barbell,
seen from behind, small in the frame, about to begin. Near-black
background #111113, deep shadows, one warm orange accent light #F5570B
from a lamp ahead of the athlete, drawing a warm path on the floor. High
contrast, moody gym lighting, sense of a fresh start. Simple composition
that reads at thumbnail size. No text, no letters, no logos, no brand
marks, no watermark. 35mm lens, slight film grain.
```

### Обложка пейволла в Telegram — 4:3 (1200×900)

Отправляется вместе с предложением подписки. Должна работать без
интерфейса вокруг, поэтому свободное место — не только снизу, но и по
центру не перегружено.

```
Editorial sports photography, aspect ratio 4:3, horizontal. A coach and
an athlete side by side at a dark gym rack, the coach pointing at the
bar, both faces turned away from the camera, focused. Near-black
background #111113, deep shadows, one warm orange accent light #F5570B
as a rim light outlining both figures against the dark. High contrast,
moody gym lighting, sense of guidance and trust. The scene fills the entire frame edge to edge: no blank areas, no bands, no borders, no letterboxing, no empty space reserved for text. The main subject sits in the upper two thirds; the bottom of the frame stays naturally darker and quieter (floor or table in shadow) but remains part of the scene. No text, no letters, no logos, no brand
marks, no watermark. 35mm lens, shallow depth of field, slight film
grain.
```
