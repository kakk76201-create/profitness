"""
База знаний AI-тренера: на чём строится программа тренировок.

Зачем модуль. Раньше ИИ получал анкету и каталог и сам «придумывал» неделю —
результат зависел от удачи. Здесь собраны правила из метаанализов, позиционных
заявлений (ACSM, ВОЗ, NSCA, ESC) и известных программ (GZCLP, 5/3/1, PHUL, PHAT,
Reddit PPL, RP, Helms, Contreras, r/bodyweightfitness), а также детерминированный
конструктор недели. ИИ получает краткую выжимку (`prompt_brief`), а бэкенд может
проверить любую неделю (`audit_week`) или собрать её без ИИ (`build_week`).
Подробное человеческое описание — docs/TRAINER_KNOWLEDGE.md.

Правила модуля:
  * только данные и чистые функции: без БД, сети и ИИ; импорт безопасен из любого места;
  * trainer_logic НЕ импортируется — наоборот, trainer_logic/trainer_ai могут
    импортировать этот модуль; нужные коды оборудования скопированы (см. EQUIPMENT_BY_PROFILE);
  * все тексты двуязычные (ru/en), у каждого правила и числа есть id источника из SOURCES;
  * используются только slug из backend/trainer_exercises_seed.py;
  * функции переживают неполный профиль (dict или ORM, поля *_json) — берутся значения по умолчанию.

Что считается «практической оценкой», а не данными экспериментов, помечено
strength="practitioner" / evidence="practitioner": ориентиры объёма RP
(MV/MEV/MAV/MRV), расчёт времени на тренировку, замены упражнений при боли.
"""

from __future__ import annotations

import copy
import json
import math
import re

from backend.trainer_exercises_seed import EXERCISES_BY_SLUG as _SEED_BY_SLUG

# --------------------------------------------------------------------------- #
#  Коды анкеты (копии trainer_logic; не импортируем, чтобы не было цикла)
# --------------------------------------------------------------------------- #
GOALS = ("loss", "muscle", "strength", "endurance", "tone")
LEVELS = ("beginner", "intermediate", "advanced")
EQUIPMENT_PROFILES = ("gym", "home_dumbbells", "bodyweight")
LIMITATIONS = ("knee", "lower_back", "shoulder", "wrist", "neck", "hip", "pregnancy", "heart_bp")
FOCUS_CODES = ("glutes", "core", "back", "chest", "shoulders", "arms", "legs")
DAYS_RANGE = (2, 6)
SESSION_MINUTES = (20, 30, 45, 60, 75, 90)
PROGRAM_WEEKS = (4, 6, 8)

# Значения по умолчанию для неполного профиля. Выбраны самые осторожные:
# новичок, «тонус и здоровье», только вес тела (так же по умолчанию считает
# trainer_logic.available_equipment), 3 дня по 45 минут, 6 недель.
DEFAULT_PROFILE = {
    "goal": "tone",
    "level": "beginner",
    "equipment": "bodyweight",
    "days_per_week": 3,
    "session_minutes": 45,
    "program_weeks": 6,
}

# Группы, по которым считаем недельный объём (как trainer_logic.STRENGTH_GROUPS).
MUSCLES = ("chest", "back", "shoulders", "biceps", "triceps", "quads", "hamstrings", "glutes", "calves", "core")
MAJOR_MUSCLES = frozenset({"chest", "back", "shoulders", "quads", "hamstrings", "glutes"})
# Мышцы, которые в каталоге указаны вторичными как стабилизаторы (кор в приседе, отжиманиях, тягах).
# Дробный счёт Pelland относится к синергистам, работающим в амплитуде; удержание корпуса им не
# является, поэтому для кора засчитываем только прямые подходы (ориентиры RP для пресса — тоже прямые).
STABILIZER_MUSCLES = frozenset({"core"})
MUSCLE_NAMES = {
    "chest": ("грудь", "chest"),
    "back": ("спина", "back"),
    "shoulders": ("плечи", "shoulders"),
    "biceps": ("бицепс", "biceps"),
    "triceps": ("трицепс", "triceps"),
    "quads": ("квадрицепс", "quads"),
    "hamstrings": ("бицепс бедра", "hamstrings"),
    "glutes": ("ягодицы", "glutes"),
    "calves": ("икры", "calves"),
    "core": ("кор", "core"),
}
# Акцент анкеты → группы мышц каталога.
FOCUS_MUSCLES = {
    "glutes": ("glutes",),
    "core": ("core",),
    "back": ("back",),
    "chest": ("chest",),
    "shoulders": ("shoulders",),
    "arms": ("biceps", "triceps"),
    "legs": ("quads", "hamstrings", "glutes"),
}

# Копия trainer_logic.EQUIPMENT_BY_PROFILE / EXTRA_TO_EQUIPMENT (проверяется тестом на равенство).
_ALL_EQUIPMENT = frozenset({
    "barbell", "dumbbell", "machine", "cable", "bodyweight", "band",
    "kettlebell", "pullup_bar", "bench", "cardio_machine", "none",
})
EQUIPMENT_BY_PROFILE = {
    "gym": _ALL_EQUIPMENT,
    "home_dumbbells": frozenset({"dumbbell", "bodyweight", "band", "none"}),
    "bodyweight": frozenset({"bodyweight", "none"}),
}
EXTRA_TO_EQUIPMENT = {
    "pullup_bar": "pullup_bar", "bands": "band", "band": "band", "bench": "bench",
    "kettlebell": "kettlebell", "barbell": "barbell", "cardio_machine": "cardio_machine",
    "dumbbells": "dumbbell", "dumbbell": "dumbbell",
}

WARMUP_SLUG = "warmup_general_5min"
COOLDOWN_SLUG = "stretch_full_body_5min"

# Пределы нормализатора программы (trainer_ai): не выходим за них, иначе значения обрежутся.
SETS_RANGE = (1, 6)
REPS_RANGE = (1, 30)
REST_RANGE = (20, 300)
RPE_RANGE = (5, 10)
TIME_RANGE = (5, 3600)
MAX_MAIN_EXERCISES = 10

# Тяжёлая нагрузка на поясницу: два таких упражнения в один день не ставим
# (правило PROGRAM_SYSTEM_PROMPT; обоснование — осевая нагрузка и утомление разгибателей).
HEAVY_LOWER_BACK = frozenset({
    "bb_deadlift", "bb_rdl", "good_morning", "rdl_db", "sumo_deadlift_db", "kb_swing", "hyperextension",
})


# --------------------------------------------------------------------------- #
#  Мелкие хелперы: dict/ORM, JSON-поля, язык
# --------------------------------------------------------------------------- #
def _get(obj, name, default=None):
    if obj is None:
        return default
    value = obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)
    return default if value is None else value


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text[0] in "[{":
            try:
                parsed = json.loads(text)
            except ValueError:
                return []
            return parsed if isinstance(parsed, list) else []
        return [part.strip() for part in text.split(",") if part.strip()]
    return [value]


def _list_field(obj, name) -> list:
    value = _get(obj, name)
    if value is None:
        value = _get(obj, name + "_json")
    return _as_list(value)


def _to_int(value, default=None):
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _rnd(value) -> int:
    """Округление «половина вверх» (round() в Python банковский: 8.5 → 8)."""
    return int(math.floor(value + 0.5))


def _is_en(lang) -> bool:
    return str(lang or "ru").strip().lower().startswith("en")


def _t(pair, lang):
    """Текст из пары (ru, en) или dict {"ru","en"}."""
    if isinstance(pair, dict):
        return pair.get("en" if _is_en(lang) else "ru") or pair.get("ru") or ""
    if isinstance(pair, (list, tuple)) and pair:
        return pair[1] if _is_en(lang) and len(pair) > 1 else pair[0]
    return str(pair or "")


# --------------------------------------------------------------------------- #
#  1. Источники
#  type: meta-analysis | rct | position-stand | consensus | practitioner | program
#  Только то, что исследователи реально открыли (аннотация Europe PMC, полный
#  текст, официальная страница программы). Книги, которые не открывались
#  (например, The Muscle and Strength Pyramid), сюда не включены.
# --------------------------------------------------------------------------- #
def _src(sid, type_, year, authors, title, url, note=None):
    row = {"id": sid, "type": type_, "year": year, "authors": authors, "title": title, "url": url}
    if note:
        row["note"] = note
    return sid, row


SOURCES: dict[str, dict] = dict([
    # --- Объём и частота ---
    _src("schoenfeld2017_volume", "meta-analysis", "2017", "Schoenfeld BJ, Ogborn D, Krieger JW",
         "Dose-response relationship between weekly resistance training volume and increases in muscle mass (J Sports Sci)",
         "https://doi.org/10.1080/02640414.2016.1210197"),
    _src("pelland2025_dose", "meta-analysis", "2025", "Pelland JC, Remmert JF, Robinson ZP, Hinson SR, Zourdos MC",
         "The Resistance Training Dose Response: Meta-Regressions Exploring the Effects of Weekly Volume and Frequency (Sports Med)",
         "https://doi.org/10.1007/s40279-025-02344-w", "онлайн 2025, том 2026; препринт SportRxiv 2024"),
    _src("baz_valle2022_volume", "meta-analysis", "2022", "Baz-Valle E, Balsalobre-Fernández C, Alix-Fages C, Santos-Concejero J",
         "A Systematic Review of The Effects of Different Resistance Training Volumes on Muscle Hypertrophy (J Hum Kinet)",
         "https://doi.org/10.2478/hukin-2022-0017"),
    _src("schoenfeld2019_volume_rct", "rct", "2019", "Schoenfeld BJ, Contreras B, Krieger J et al.",
         "Resistance Training Volume Enhances Muscle Hypertrophy but Not Strength in Trained Men (Med Sci Sports Exerc)",
         "https://doi.org/10.1249/mss.0000000000001764"),
    _src("remmert2025_session", "meta-analysis", "2025", "Remmert JF, Pelland JC, Robinson ZP, Hinson SR, Zourdos MC",
         "Is There Too Much of a Good Thing? Meta-Regressions of the Effect of Per-Session Volume (SportRxiv 537)",
         "https://sportrxiv.org/index.php/server/preprint/view/537", "препринт без рецензии"),
    _src("schoenfeld2016_freq", "meta-analysis", "2016", "Schoenfeld BJ, Ogborn D, Krieger JW",
         "Effects of Resistance Training Frequency on Measures of Muscle Hypertrophy (Sports Med)",
         "https://doi.org/10.1007/s40279-016-0543-8"),
    _src("schoenfeld2019_freq", "meta-analysis", "2019", "Schoenfeld BJ, Grgic J, Krieger J",
         "How many times per week should a muscle be trained to maximize muscle hypertrophy? (J Sports Sci)",
         "https://doi.org/10.1080/02640414.2018.1555906"),
    _src("grgic2018_freq_strength", "meta-analysis", "2018", "Grgic J, Schoenfeld BJ, Davies TB et al.",
         "Effect of Resistance Training Frequency on Gains in Muscular Strength (Sports Med)",
         "https://doi.org/10.1007/s40279-018-0872-x"),
    _src("currier2023_nma", "meta-analysis", "2023", "Currier BS, McLeod JC et al.",
         "Resistance training prescription for muscle strength and hypertrophy in healthy adults: Bayesian network meta-analysis (BJSM)",
         "https://doi.org/10.1136/bjsports-2023-106807"),
    _src("bickel2011_maintenance", "rct", "2011", "Bickel CS, Cross JM, Bamman MM",
         "Exercise dosing to retain resistance training adaptations in young and older adults (Med Sci Sports Exerc)",
         "https://doi.org/10.1249/mss.0b013e318207c15d"),
    # --- Нагрузка, повторы, отказ, отдых, порядок, темп ---
    _src("schoenfeld2017_load", "meta-analysis", "2017", "Schoenfeld BJ, Grgic J, Ogborn D, Krieger JW",
         "Strength and Hypertrophy Adaptations Between Low- vs. High-Load Resistance Training (J Strength Cond Res)",
         "https://doi.org/10.1519/jsc.0000000000002200"),
    _src("lopez2021_load", "meta-analysis", "2021", "Lopez P, Radaelli R, Taaffe DR et al.",
         "Resistance Training Load Effects on Muscle Hypertrophy and Strength Gain: Network Meta-analysis (Med Sci Sports Exerc)",
         "https://doi.org/10.1249/mss.0000000000002585"),
    _src("schoenfeld2021_continuum", "consensus", "2021", "Schoenfeld BJ, Grgic J, Van Every DW, Plotkin DL",
         "Loading Recommendations for Muscle Strength, Hypertrophy, and Local Endurance: A Re-Examination of the Repetition Continuum (Sports)",
         "https://doi.org/10.3390/sports9020032"),
    _src("acsm2009_progression", "position-stand", "2009", "American College of Sports Medicine",
         "Progression models in resistance training for healthy adults (Med Sci Sports Exerc 41:687-708)",
         "https://doi.org/10.1249/mss.0b013e3181915670"),
    _src("iversen2021_time", "consensus", "2021", "Iversen VM, Norum M, Schoenfeld BJ, Fimland MS",
         "No Time to Lift? Designing Time-Efficient Training Programs for Strength and Hypertrophy (Sports Med)",
         "https://doi.org/10.1007/s40279-021-01490-1"),
    _src("refalo2023_failure", "meta-analysis", "2023", "Refalo MC, Helms ER, Trexler ET, Hamilton DL, Fyfe JJ",
         "Influence of Resistance Training Proximity-to-Failure on Skeletal Muscle Hypertrophy (Sports Med)",
         "https://doi.org/10.1007/s40279-022-01784-y"),
    _src("robinson2024_rir", "meta-analysis", "2024", "Robinson ZP, Pelland JC, Remmert JF, Refalo MC, Jukic I, Steele J, Zourdos MC",
         "Exploring the Dose-Response Relationship Between Estimated Resistance Training Proximity to Failure, Strength Gain, and Muscle Hypertrophy (Sports Med)",
         "https://doi.org/10.1007/s40279-024-02069-2", "числа взяты из препринта v2"),
    _src("refalo2024_rct", "rct", "2024", "Refalo MC, Helms ER, Robinson ZP, Hamilton DL, Fyfe JJ",
         "Similar muscle hypertrophy following eight weeks of resistance training to momentary muscular failure or with repetitions-in-reserve (J Sports Sci)",
         "https://doi.org/10.1080/02640414.2024.2321021"),
    _src("zourdos2016_rpe", "rct", "2016", "Zourdos MC, Klemp A, Dolan C et al.",
         "Novel Resistance Training-Specific Rating of Perceived Exertion Scale Measuring Repetitions in Reserve (J Strength Cond Res)",
         "https://doi.org/10.1519/jsc.0000000000001049"),
    _src("schoenfeld2016_rest", "rct", "2016", "Schoenfeld BJ, Pope ZK, Benik FM et al.",
         "Longer Interset Rest Periods Enhance Muscle Strength and Hypertrophy in Resistance-Trained Men (J Strength Cond Res)",
         "https://doi.org/10.1519/jsc.0000000000001272"),
    _src("singer2024_rest", "meta-analysis", "2024", "Singer A, Wolf M, Generoso L et al.",
         "Give it a rest: Bayesian meta-analysis on the effect of inter-set rest interval duration on muscle hypertrophy (Front Sports Act Living)",
         "https://doi.org/10.3389/fspor.2024.1429789"),
    _src("simao2012_order", "consensus", "2012", "Simão R, de Salles BF, Figueiredo T, Dias I, Willardson JM",
         "Exercise order in resistance training (Sports Med)", "https://doi.org/10.2165/11597240-000000000-00000"),
    _src("nunes2021_order", "meta-analysis", "2021", "Nunes JP, Grgic J, Cunha PM et al.",
         "What influence does resistance exercise order have on muscular strength gains and muscle hypertrophy? (Eur J Sport Sci)",
         "https://doi.org/10.1080/17461391.2020.1733672"),
    _src("schoenfeld2015_tempo", "meta-analysis", "2015", "Schoenfeld BJ, Ogborn DI, Krieger JW",
         "Effect of repetition duration during resistance training on muscle hypertrophy (Sports Med)",
         "https://doi.org/10.1007/s40279-015-0304-0"),
    _src("maeo2023_overhead", "rct", "2023", "Maeo S et al.",
         "Triceps brachii hypertrophy is substantially greater after elbow extension training performed in the overhead versus neutral arm position (Eur J Sport Sci)",
         "https://doi.org/10.1080/17461391.2022.2100279"),
    # --- Прогрессия, периодизация, разгрузка ---
    _src("plotkin2022_reps", "rct", "2022", "Plotkin D, Coleman M, Van Every D et al.",
         "Progressive overload without progressing load? Load or repetition progression (PeerJ)",
         "https://doi.org/10.7717/peerj.14142"),
    _src("moesgaard2022_period", "meta-analysis", "2022", "Moesgaard L, Beck MM, Christiansen L, Aagaard P, Lundbye-Jensen J",
         "Effects of Periodization on Strength and Muscle Hypertrophy in Volume-Equated Resistance Training Programs (Sports Med)",
         "https://doi.org/10.1007/s40279-021-01636-1"),
    _src("grgic2017_dup", "meta-analysis", "2017", "Grgic J, Mikulic P, Podnar H, Pedisic Z",
         "Effects of linear and daily undulating periodized resistance training programs on muscle hypertrophy (PeerJ)",
         "https://doi.org/10.7717/peerj.3695"),
    _src("bell2023_deload", "consensus", "2023", "Bell L, Strafford BW, Coleman M, Androulakis Korakakis P, Nolan D",
         "Integrating Deloading into Strength and Physique Sports Training Programmes: An International Delphi Consensus (Sports Med Open)",
         "https://doi.org/10.1186/s40798-023-00633-0"),
    _src("rogerson2024_deload", "practitioner", "2024", "Rogerson D, Nolan D, Androulakis Korakakis P, Immonen V, Wolf M, Bell L",
         "Deloading Practices in Strength and Physique Sports: A Cross-sectional Survey (Sports Med Open)",
         "https://doi.org/10.1186/s40798-024-00691-y"),
    _src("coleman2024_deload", "rct", "2024", "Coleman M, Burke R, Augustin F et al.",
         "Gaining more from doing less? The effects of a one-week deload period during supervised resistance training (PeerJ)",
         "https://doi.org/10.7717/peerj.16777"),
    # --- Питание, похудение, активность ---
    _src("morton2018_protein", "meta-analysis", "2018", "Morton RW, Murphy KT, McKellar SR et al.",
         "Effect of protein supplementation on resistance training-induced gains in muscle mass and strength (Br J Sports Med)",
         "https://doi.org/10.1136/bjsports-2017-097608"),
    _src("murphy2022_deficit", "meta-analysis", "2022", "Murphy C, Koehler K",
         "Energy deficiency impairs resistance training gains in lean mass but not strength (Scand J Med Sci Sports)",
         "https://doi.org/10.1111/sms.14075"),
    _src("sardeli2018_restriction", "meta-analysis", "2018", "Sardeli AV, Komatsu TR, Mori MA, Gáspari AF, Chacon-Mikahil MPT",
         "Resistance Training Prevents Muscle Loss Induced by Caloric Restriction in Obese Elderly Individuals (Nutrients)",
         "https://doi.org/10.3390/nu10040423"),
    _src("helms2014_nutrition", "consensus", "2014", "Helms ER, Aragon AA, Fitschen PJ",
         "Evidence-based recommendations for natural bodybuilding contest preparation: nutrition and supplementation (JISSN)",
         "https://doi.org/10.1186/1550-2783-11-20"),
    _src("helms2015_training", "consensus", "2015", "Helms ER, Fitschen PJ, Aragon AA, Cronin J, Schoenfeld BJ",
         "Recommendations for natural bodybuilding contest preparation: resistance and cardiovascular training (J Sports Med Phys Fitness)",
         "https://ro.ecu.edu.au/ecuworkspost2013/1869/"),
    _src("who2020", "position-stand", "2020", "Bull FC, Al-Ansari SS, Biddle S et al.",
         "World Health Organization 2020 guidelines on physical activity and sedentary behaviour (Br J Sports Med)",
         "https://doi.org/10.1136/bjsports-2020-102955"),
    _src("donnelly2009_weightloss", "position-stand", "2009", "Donnelly JE, Blair SN, Jakicic JM et al.",
         "ACSM Position Stand: physical activity intervention strategies for weight loss and prevention of weight regain (Med Sci Sports Exerc)",
         "https://doi.org/10.1249/mss.0b013e3181949333"),
    _src("paluch2022_steps", "meta-analysis", "2022", "Paluch AE et al.",
         "Daily steps and all-cause mortality: a meta-analysis of 15 international cohorts (Lancet Public Health)",
         "https://doi.org/10.1016/s2468-2667(21)00302-9"),
    _src("garber2011_acsm", "position-stand", "2011", "Garber CE, Blissmer B, Deschenes MR et al.",
         "Quantity and quality of exercise for developing and maintaining fitness in apparently healthy adults (Med Sci Sports Exerc)",
         "https://doi.org/10.1249/MSS.0b013e318213fefb"),
    _src("viana2019_hiit", "meta-analysis", "2019", "Viana RB, Naves JPA, Coswig VS et al.",
         "Is interval training the magic bullet for fat loss? MICT vs HIIT (Br J Sports Med)",
         "https://doi.org/10.1136/bjsports-2018-099928"),
    _src("storoschuk2025_zone2", "consensus", "2025", "Storoschuk KL, Moran-MacDonald A, Gibala MJ, Gurd BJ",
         "Much Ado About Zone 2: A Narrative Review (Sports Med)", "https://doi.org/10.1007/s40279-025-02261-y"),
    # --- Кардио + силовые ---
    _src("schumann2022_concurrent", "meta-analysis", "2022", "Schumann M, Feuerbacher JF, Sünkeler M et al.",
         "Compatibility of Concurrent Aerobic and Strength Training for Skeletal Muscle Size and Function (Sports Med)",
         "https://doi.org/10.1007/s40279-021-01587-7"),
    _src("eddens2018_sequence", "meta-analysis", "2018", "Eddens L, van Someren K, Howatson G",
         "The Role of Intra-Session Exercise Sequence in the Interference Effect (Sports Med)",
         "https://doi.org/10.1007/s40279-017-0784-1"),
    _src("wilson2012_concurrent", "meta-analysis", "2012", "Wilson JM, Marin PJ, Rhea MR et al.",
         "Concurrent training: a meta-analysis examining interference of aerobic and resistance exercises (J Strength Cond Res)",
         "https://doi.org/10.1519/jsc.0b013e31823a3e2d"),
    _src("seiler2010_distribution", "consensus", "2010", "Seiler S",
         "What is best practice for training intensity and duration distribution in endurance athletes? (IJSPP)",
         "https://doi.org/10.1123/ijspp.5.3.276"),
    _src("ronnestad2014_endurance", "consensus", "2014", "Rønnestad BR, Mujika I",
         "Optimizing strength training for running and cycling endurance performance: A review (Scand J Med Sci Sports)",
         "https://pubmed.ncbi.nlm.nih.gov/23914932/"),
    _src("viada_hybrid", "practitioner", "2016–2024", "Alex Viada",
         "5 Questions with Alex Viada (Juggernaut Training Systems); интервью Rox Lyfe",
         "https://www.jtsstrength.com/5-questions-alex-viada/"),
    # --- Разминка, растяжка, суперсеты ---
    _src("abad2011_warmup", "rct", "2011", "Abad CC, Prado ML, Ugrinowitsch C, Tricoli V, Barroso R",
         "Combination of general and specific warm-ups improves leg-press one repetition maximum (J Strength Cond Res)",
         "https://doi.org/10.1519/jsc.0b013e3181e8611b"),
    _src("enes2025_warmup", "rct", "2025", "Enes A, Mohan A, Pinero A et al.",
         "Warming up to improved performance? Effects of different specific warm-up protocols (SportRxiv 559)",
         "https://sportrxiv.org/index.php/server/preprint/view/559"),
    _src("ribeiro2020_warmup", "rct", "2020", "Ribeiro B, Pereira A, Neves PP et al.",
         "The Role of Specific Warm-up during Bench Press and Squat Exercises (Int J Environ Res Public Health)",
         "https://doi.org/10.3390/ijerph17186882"),
    _src("behm2016_stretch", "meta-analysis", "2016", "Behm DG, Blazevich AJ, Kay AD, McHugh M",
         "Acute effects of muscle stretching on physical performance, range of motion, and injury incidence (Appl Physiol Nutr Metab)",
         "https://doi.org/10.1139/apnm-2015-0235"),
    _src("lauersen2014_injury", "meta-analysis", "2014", "Lauersen JB, Bertelsen DM, Andersen LB",
         "The effectiveness of exercise interventions to prevent sports injuries (Br J Sports Med)",
         "https://doi.org/10.1136/bjsports-2013-092538"),
    _src("vanhooren2018_cooldown", "consensus", "2018", "Van Hooren B, Peake JM",
         "Do We Need a Cool-Down After Exercise? (Sports Med)", "https://doi.org/10.1007/s40279-018-0916-2"),
    _src("zhang2025_superset", "meta-analysis", "2025", "Zhang X, Weakley J, Li H, Li Z, García-Ramos A",
         "Superset Versus Traditional Resistance Training Prescriptions (Sports Med)",
         "https://doi.org/10.1007/s40279-025-02176-8"),
    # --- Женщины, дом, ягодицы ---
    _src("roberts2020_sex", "meta-analysis", "2020", "Roberts BM, Nuckols G, Krieger JW",
         "Sex Differences in Resistance Training: A Systematic Review and Meta-Analysis (J Strength Cond Res)",
         "https://doi.org/10.1519/JSC.0000000000003521"),
    _src("colenso2023_cycle", "meta-analysis", "2023", "Colenso-Semple LM, D'Souza AC, Elliott-Sale KJ, Phillips SM",
         "Current evidence shows no influence of women's menstrual cycle phase on acute strength performance or adaptations (Front Sports Act Living)",
         "https://doi.org/10.3389/fspor.2023.1054542"),
    _src("kikuchi2017_pushup", "rct", "2017", "Kikuchi N, Nakazato K",
         "Low-load bench press and push-up induce similar muscle hypertrophy and strength gain (J Exerc Sci Fit)",
         "https://doi.org/10.1016/j.jesf.2017.06.003"),
    _src("lopes2019_bands", "meta-analysis", "2019", "Lopes JSS et al.",
         "Effects of training with elastic resistance versus conventional resistance on muscular strength (SAGE Open Med)",
         "https://doi.org/10.1177/2050312119831116"),
    _src("plotkin2023_hipthrust", "rct", "2023", "Plotkin DL, Rodas MA, Vigotsky AD et al.",
         "Hip thrust and back squat training elicit similar gluteus muscle hypertrophy (Front Physiol)",
         "https://doi.org/10.3389/fphys.2023.1279170"),
    _src("contreras_glute_frequency", "practitioner", "2013–2019", "Bret Contreras",
         "Your Optimal Training Frequency for the Glutes; The Glute Guy's Secrets Part II – Programming",
         "https://bretcontreras.com/your-optimal-training-frequency-for-the-glutes-part-i-exercise-type/"),
    # --- Ориентиры объёма RP (практические оценки, не эксперименты) ---
    _src("rp_landmarks", "practitioner", "2023–2024", "Israetel M (Renaissance Periodization)",
         "Training Volume Landmarks for Muscle Growth", "https://rpstrength.com/blogs/articles/training-volume-landmarks-muscle-growth"),
    _src("rp_chest", "practitioner", "2024", "Israetel M (RP)", "Chest Hypertrophy Training Tips",
         "https://rpstrength.com/blogs/articles/chest-hypertrophy-training-tips"),
    _src("rp_back", "practitioner", "2023", "Israetel M (RP)", "Back Hypertrophy Training Tips",
         "https://rpstrength.com/blogs/articles/back-hypertrophy-training-tips"),
    _src("rp_quads", "practitioner", "2024", "Israetel M (RP)", "Quad Hypertrophy Training Tips",
         "https://rpstrength.com/blogs/articles/quad-hypertrophy-training-tips"),
    _src("rp_hamstrings", "practitioner", "2023", "Israetel M (RP)", "Hamstring Hypertrophy Training Tips",
         "https://rpstrength.com/blogs/articles/hamstring-hypertrophy-training-tips"),
    _src("rp_glutes", "practitioner", "2024", "Israetel M (RP)", "Glute Hypertrophy Training Tips",
         "https://rpstrength.com/blogs/articles/glute-hypertrophy-training-tips"),
    _src("rp_side_delts", "practitioner", "2024", "Israetel M (RP)", "Side Delt Hypertrophy Training Tips",
         "https://rpstrength.com/blogs/articles/side-delt-hypertrophy-training-tips"),
    _src("rp_rear_delts", "practitioner", "2024", "Israetel M (RP)", "Rear Delt Hypertrophy Training Tips",
         "https://rpstrength.com/blogs/articles/rear-delt-hypertrophy-training-tips"),
    _src("rp_front_delts", "practitioner", "2024", "Israetel M (RP)", "Front Delt Hypertrophy Training Tips",
         "https://rpstrength.com/blogs/articles/front-delt-hypertrophy-training-tips"),
    _src("rp_biceps", "practitioner", "2023", "Israetel M (RP)", "Bicep Hypertrophy Training Tips",
         "https://rpstrength.com/blogs/articles/bicep-hypertrophy-training-tips"),
    _src("rp_triceps", "practitioner", "2024", "Israetel M (RP)", "Triceps Hypertrophy Training Tips",
         "https://rpstrength.com/blogs/articles/triceps-hypertrophy-training-tips"),
    _src("rp_calves", "practitioner", "2024", "Israetel M (RP)", "Calves Hypertrophy Training Tips",
         "https://rpstrength.com/blogs/articles/calves-hypertrophy-training-tips"),
    _src("rp_abs", "practitioner", "2024", "Israetel M (RP)", "Ab Hypertrophy Training Tips",
         "https://rpstrength.com/blogs/articles/ab-hypertrophy-training-tips"),
    # --- Программы (официальные страницы или открытые пересказы) ---
    _src("prog_starting_strength", "program", "2024", "Mark Rippetoe (пересказ Legion Athletics)",
         "Starting Strength program", "https://legionathletics.com/starting-strength-program/",
         "официальный сайт отдаёт 403, структура по Legion и PowerliftingToWin"),
    _src("prog_stronglifts", "program", "2015–2024", "Mehdi Hadim", "StrongLifts 5×5 workout program",
         "https://stronglifts.com/stronglifts-5x5/workout-program/"),
    _src("prog_bbr", "program", "н/д", "r/Fitness wiki", "r/Fitness Basic Beginner Routine",
         "https://thefitness.wiki/routines/r-fitness-basic-beginner-routine/"),
    _src("prog_gzclp", "program", "н/д", "Cody LeFever (r/Fitness wiki)", "GZCLP", "https://thefitness.wiki/routines/gzclp/"),
    _src("prog_531", "program", "н/д", "Jim Wendler", "5/3/1 for Beginners (r/Fitness wiki); 5/3/1: How to Build Pure Strength",
         "https://thefitness.wiki/routines/5-3-1-for-beginners/"),
    _src("prog_bbb", "program", "н/д", "Jim Wendler", "Boring But Big", "https://jimwendler.com/blogs/jimwendler-com/101077382-boring-but-big"),
    _src("prog_phul", "program", "н/д", "Brandon Campbell (пересказ Hevy)", "PHUL — Power Hypertrophy Upper Lower",
         "https://www.hevyapp.com/phul-power-hypertrophy-upper-lower/", "оригинал muscleandstrength.com отдаёт 403"),
    _src("prog_phat", "program", "2011", "Layne Norton", "PHAT — Power Hypertrophy Adaptive Training",
         "http://simplyshredded.com/mega-feature-layne-norton-training-series-full-powerhypertrophy-routine-updated-2011.html"),
    _src("prog_reddit_ppl", "program", "н/д", "u/Metallicadpa (r/Fitness wiki archive)", "A Linear Progression Based PPL Program for Beginners",
         "https://thefitness.wiki/reddit-archive/a-linear-progression-based-ppl-program-for-beginners/"),
    _src("prog_nippard_fundamentals", "program", "2019", "Jeff Nippard", "Fundamentals Hypertrophy Program",
         "https://jeffnippard.com/products/fundamentals-hypertrophy-program", "точные подходы — из пересказа пользователя Boostcamp"),
    _src("prog_nippard_minmax", "program", "2024", "Jeff Nippard", "The Min-Max Program",
         "https://jeffnippard.com/products/the-min-max-program"),
    _src("prog_helms", "program", "2019–2024", "Eric Helms (Muscle and Strength Pyramid, Boostcamp)",
         "Intermediate Bodybuilding Program", "https://www.boostcamp.app/coaches/muscle-and-strength-pyramid/intermediate-bodybuilding-program"),
    _src("prog_nuckols", "program", "2021", "Greg Nuckols (Boostcamp)", "Greg Nuckols Beginner Program",
         "https://www.boostcamp.app/coaches/greg-nuckols/greg-nuckols-beginner-program"),
    _src("prog_arnold", "program", "н/д", "Arnold Schwarzenegger (пересказ BarBend)", "Arnold Schwarzenegger workout split",
         "https://barbend.com/arnold-schwarzenegger-workout-split/"),
    _src("prog_contreras", "program", "н/д", "Bret Contreras", "The Glute Guy's Secrets — Part II: Programming",
         "https://bretcontreras.com/the-glute-guys-secrets-the-art-of-glute-building-part-ii-programming/"),
    _src("prog_bwf_rr", "program", "н/д", "r/bodyweightfitness (копия вики)", "Recommended Routine",
         "https://gist.github.com/sgup/f10f1d57e54b7876495f4bafb6d697eb", "reddit недоступен, текст из копии вики"),
    # --- Безопасность ---
    _src("silbernagel2007_pain", "rct", "2007", "Silbernagel KG, Thomeé R, Eriksson BI, Karlsson J",
         "Continued sports activity, using a pain-monitoring model, during rehabilitation in Achilles tendinopathy (Am J Sports Med)",
         "https://doi.org/10.1177/0363546506298279"),
    _src("smith2017_painful_ex", "meta-analysis", "2017", "Smith BE, Hendrick P, Smith TO et al.",
         "Should exercises be painful in the management of chronic musculoskeletal pain? (Br J Sports Med)",
         "https://doi.org/10.1136/bjsports-2016-097383"),
    _src("powers2014_pfj", "practitioner", "2014", "Powers CM, Ho KY, Chen YJ, Souza RB, Farrokhi S",
         "Patellofemoral joint stress during weight-bearing and non-weight-bearing quadriceps exercises (JOSPT)",
         "https://doi.org/10.2519/jospt.2014.4936"),
    _src("willy2019_pfp", "position-stand", "2019", "Willy RW et al. (обзор AAFP 2020)",
         "Patellofemoral Pain: Guidelines from the American Physical Therapy Association", "https://www.aafp.org/afp/2020/1001/p442"),
    _src("oarsi2019", "position-stand", "2019", "Bannuru RR, Osani MC, Vaysbrot EE et al.",
         "OARSI guidelines for the non-surgical management of knee, hip, and polyarticular osteoarthritis (Osteoarthritis Cartilage)",
         "https://doi.org/10.1016/j.joca.2019.06.011"),
    _src("stiell1997_ottawa", "consensus", "1997", "Stiell IG, Wells GA, Hoag RH et al.",
         "Implementation of the Ottawa Knee Rule (JAMA)", "https://doi.org/10.1001/jama.1997.03550230051036"),
    _src("hayden2021_lbp", "meta-analysis", "2021", "Hayden JA, Ellis J, Ogilvie R, Malmivaara A, van Tulder MW",
         "Exercise therapy for chronic low back pain (Cochrane)", "https://doi.org/10.1002/14651858.CD009790.pub2"),
    _src("oliveira2018_lbp", "position-stand", "2018", "Oliveira CB, Maher CG, Pinto RZ et al.",
         "Clinical practice guidelines for non-specific low back pain in primary care: an updated overview (Eur Spine J)",
         "https://doi.org/10.1007/s00586-018-5673-2"),
    _src("mcgill1998_back", "practitioner", "1998", "McGill SM",
         "Low back exercises: evidence for improving exercise regimens (Phys Ther)", "https://doi.org/10.1093/ptj/78.7.754"),
    _src("swinton2011_trapbar", "practitioner", "2011", "Swinton PA, Stewart A, Agouris I, Keogh JW, Lloyd R",
         "A biomechanical analysis of straight and hexagonal barbell deadlifts (J Strength Cond Res)",
         "https://doi.org/10.1519/JSC.0b013e3181e73f87"),
    _src("finucane2020_redflags", "consensus", "2020", "Finucane LM, Downie A, Mercer C et al.",
         "International Framework for Red Flags for Potential Serious Spinal Pathologies (JOSPT)",
         "https://doi.org/10.2519/jospt.2020.9971"),
    _src("giangregorio2014_osteo", "consensus", "2014", "Giangregorio LM, Papaioannou A, MacIntyre NJ et al.",
         "Too Fit To Fracture: exercise recommendations for individuals with osteoporosis (Osteoporos Int)",
         "https://doi.org/10.1007/s00198-013-2523-2"),
    _src("kolber2014_shoulder", "practitioner", "2014", "Kolber MJ, Cheatham SW, Salamh PA, Hanney WJ",
         "Characteristics of shoulder impingement in the recreational weight-training population (J Strength Cond Res)",
         "https://doi.org/10.1519/JSC.0000000000000250"),
    _src("fees1998_upper", "practitioner", "1998", "Fees M, Decker T, Snyder-Mackler L, Axe MJ",
         "Upper extremity weight-training modifications for the injured athlete (Am J Sports Med)",
         "https://doi.org/10.1177/03635465980260052301"),
    _src("blanpied2017_neck", "position-stand", "2017", "Blanpied PR, Gross AR, Elliott JM et al.",
         "Neck Pain: Revision 2017. Clinical Practice Guidelines (JOSPT)", "https://doi.org/10.2519/jospt.2017.0302"),
    _src("rushton2023_cervical", "consensus", "2023", "Rushton A, Carlesso LC, Flynn T et al.",
         "International IFOMPT Cervical Framework (JOSPT)", "https://doi.org/10.2519/jospt.2022.11147"),
    _src("e3_wrist", "practitioner", "н/д", "E3 Rehab", "Wrist Pain Rehab", "https://e3rehab.com/wrist-pain-rehab/"),
    _src("e3_fai", "practitioner", "н/д", "E3 Rehab", "Femoroacetabular Impingement (FAI)", "https://e3rehab.com/fai/"),
    _src("acog804_2020", "position-stand", "2020", "American College of Obstetricians and Gynecologists",
         "Physical Activity and Exercise During Pregnancy and the Postpartum Period, Committee Opinion No. 804",
         "https://doi.org/10.1097/AOG.0000000000003772", "сверено по пересказу DONA"),
    _src("rudin2021_pregnancy", "meta-analysis", "2021", "Rudin LR, Dunn L, Lyons K et al.",
         "Professional Exercise Recommendations for Healthy Women Who Are Pregnant: A Systematic Review (Women's Health Reports)",
         "https://pmc.ncbi.nlm.nih.gov/articles/PMC8524738/"),
    _src("mottola2019_canada", "position-stand", "2018", "Mottola MF, Davenport MH, Ruchat SM et al.",
         "2019 Canadian guideline for physical activity throughout pregnancy (Br J Sports Med)",
         "https://csepguidelines.ca/guidelines/pregnancy/"),
    _src("goom2019_postnatal", "consensus", "2019", "Goom T, Donnelly G, Brockwell E",
         "Returning to running postnatal — guidelines for professionals",
         "https://absolute.physio/wp-content/uploads/2019/09/returning-to-running-postnatal-guidelines.pdf"),
    _src("paluch2023_aha", "position-stand", "2023", "Paluch AE, Boyer WR, Franklin BA et al. (AHA; тезисы по обзору ACC)",
         "Resistance Exercise Training in Individuals With and Without Cardiovascular Disease: 2023 Update",
         "https://www.acc.org/Latest-in-Cardiology/ten-points-to-remember/2023/12/19/15/33/resistance-exercise-training"),
    _src("pelliccia2020_esc", "position-stand", "2020", "Pelliccia A, Sharma S, Gati S et al.",
         "2020 ESC Guidelines on sports cardiology and exercise in patients with cardiovascular disease (Eur Heart J)",
         "https://academic.oup.com/eurheartj/article/42/1/17/5898937"),
    _src("ghadieh2015_htn", "consensus", "2015", "Ghadieh AS, Saab B",
         "Evidence for exercise training in the management of hypertension in adults (Can Fam Physician)",
         "https://pmc.ncbi.nlm.nih.gov/articles/PMC4369613/"),
    _src("acsm_hypertension", "position-stand", "н/д", "American College of Sports Medicine",
         "Exercise for the Prevention and Treatment of Hypertension — Implications and Application",
         "https://acsm.org/exercise-for-the-prevention-and-treatment-of-hypertension/"),
    _src("edwards2023_bp", "meta-analysis", "2023", "Edwards JJ, Deenmamode AHP, Griffiths M et al.",
         "Exercise training and resting blood pressure: pairwise and network meta-analysis of 270 RCTs (Br J Sports Med)",
         "https://doi.org/10.1136/bjsports-2022-106503"),
    _src("riebe2015_screening", "position-stand", "2015", "Riebe D, Franklin BA, Thompson PD et al.",
         "Updating ACSM's Recommendations for Exercise Preparticipation Health Screening (Med Sci Sports Exerc)",
         "https://doi.org/10.1249/MSS.0000000000000664"),
    _src("fragala2019_nsca_older", "position-stand", "2019", "Fragala MS, Cadore EL, Dorgo S et al.",
         "Resistance Training for Older Adults: NSCA Position Statement (J Strength Cond Res)",
         "https://doi.org/10.1519/JSC.0000000000003230"),
    _src("sherrington2019_falls", "meta-analysis", "2019", "Sherrington C, Fairhall NJ, Wallbank GK et al.",
         "Exercise for preventing falls in older people living in the community (Cochrane)",
         "https://doi.org/10.1002/14651858.CD012424.pub2"),
    _src("cdc_rhabdo", "position-stand", "н/д", "CDC / NIOSH", "Signs and Symptoms of Rhabdomyolysis",
         "https://www.cdc.gov/niosh/rhabdo/signs-symptoms/index.html"),
    _src("roberts2023_heat", "consensus", "2023", "Roberts WO, Armstrong LE, Sawka MN et al.",
         "ACSM Expert Consensus Statement on Exertional Heat Illness (Curr Sports Med Rep)",
         "https://doi.org/10.1249/JSR.0000000000001058"),
])


# --------------------------------------------------------------------------- #
#  2. Ключевые принципы
#  strength: high — метаанализы/позиционные заявления сходятся;
#            medium — данные есть, но мало или препринт;
#            practitioner — практика тренеров, не эксперимент.
#  brief_ru/brief_en — короткая форма для промпта ИИ (prompt_brief).
#  tags — для выбора самых релевантных принципов под профиль:
#         goal:<код>, level:<код>, limit:<код>, all.
# --------------------------------------------------------------------------- #
def _pr(pid, strength, sources, tags, ru, en, brief_ru, brief_en):
    return {"id": pid, "strength": strength, "sources": list(sources), "tags": list(tags),
            "ru": ru, "en": en, "brief_ru": brief_ru, "brief_en": brief_en}


PRINCIPLES: list[dict] = [
    _pr("volume_dose_response", "high", ["pelland2025_dose", "schoenfeld2017_volume"],
        ["all", "goal:muscle", "goal:tone"],
        "Рост мышц зависит от числа рабочих подходов на мышцу в неделю с убывающей отдачей. Считаем «дробно»: "
        "прямой подход = 1, для мышцы-синергиста = 0,5. Минимальная действенная доза — около 4 подходов в неделю; "
        "5–10 подходов дают наибольший эффект на подход, 11–18 средний, 19–29 низкий.",
        "Muscle growth depends on weekly working sets per muscle, with diminishing returns. Count fractionally: "
        "direct set = 1, synergist = 0.5. Minimum effective dose is about 4 sets/week; 5–10 sets give the most per set, "
        "11–18 moderate, 19–29 low.",
        "Объём = рабочие подходы на мышцу в неделю (прямой 1, синергист 0,5); минимум ~4, отдача падает после ~10–18.",
        "Volume = weekly sets per muscle (direct 1, synergist 0.5); minimum ~4, returns shrink past ~10–18."),
    _pr("volume_by_level", "medium", ["pelland2025_dose", "baz_valle2022_volume", "acsm2009_progression", "iversen2021_time"],
        ["all"],
        "Недельный объём на мышцу: новичок 6–10 подходов, средний уровень 10–16 (у тренированных 12–20 не хуже >20), "
        "продвинутый 12–20 и временно до 24 для 1–3 приоритетных мышц при снижении остальных. Выше ~30 не назначаем.",
        "Weekly sets per muscle: beginner 6–10, intermediate 10–16 (in trained lifters 12–20 is as good as >20), advanced "
        "12–20 and temporarily up to 24 for 1–3 priority muscles while others are reduced. Never above ~30.",
        "Подходов на мышцу в неделю: новичок 6–10, средний 10–16, продвинутый 12–20 (приоритет до 24).",
        "Sets per muscle per week: beginner 6–10, intermediate 10–16, advanced 12–20 (priority up to 24)."),
    _pr("strength_volume", "high", ["pelland2025_dose", "schoenfeld2019_volume_rct"],
        ["goal:strength"],
        "Для силы отдача от объёма выходит на плато быстро: уже 1–2 подхода в неделю дают заметный прирост, после ~5 "
        "стабильного прибавления нет. У тренированных три тренировки по ~13 минут (1 подход на упражнение) дали тот же прирост силы, что и 3 или 5 подходов.",
        "For strength, returns to volume plateau quickly: 1–2 sets/week already help, beyond ~5 there is no consistent "
        "gain. In trained men three ~13-minute sessions (1 set per exercise) matched the strength gains of 3 or 5 sets.",
        "Сила: объём быстро упирается в плато — важнее тяжёлый вес и частая практика движения.",
        "Strength: volume plateaus fast — heavy loads and frequent practice of the lift matter more."),
    _pr("session_volume_cap", "medium", ["remmert2025_session", "rp_landmarks"],
        ["all"],
        "За одну тренировку эффект на мышцу перестаёт расти примерно после 11 дробных (≈8 прямых) подходов; у RP "
        "похожий потолок 8–12. Больший объём делим на несколько дней: частота ≈ недельный объём / 8–10.",
        "Per session, the effect on a muscle stops rising after about 11 fractional (≈8 direct) sets; RP gives a "
        "similar cap of 8–12. Spread larger volume over more days: frequency ≈ weekly volume / 8–10.",
        "Не больше ~8–10 прямых подходов на мышцу за тренировку; остальное — в другой день.",
        "No more than ~8–10 direct sets per muscle per session; spread the rest over other days."),
    _pr("frequency", "high", ["schoenfeld2016_freq", "schoenfeld2019_freq", "pelland2025_dose", "grgic2018_freq_strength"],
        ["all", "goal:strength"],
        "При равном объёме частота почти не влияет на гипертрофию, поэтому её выбираем по удобству, но каждую мышцу — "
        "не реже 2 раз в неделю. Для силы частота важна: основное движение 2 раза в неделю заметно лучше 1 "
        "(прирост 17,3% против 12,7%), дальше отдача падает.",
        "At equal volume frequency barely affects hypertrophy, so pick it for convenience, but train each muscle at "
        "least twice a week. For strength frequency matters: a main lift twice a week beats once (17.3% vs 12.7% gain), "
        "with diminishing returns after that.",
        "Каждую мышцу ≥2 раз в неделю; основные силовые движения 2–3 раза.",
        "Each muscle ≥2×/week; main strength lifts 2–3×/week."),
    _pr("load_and_reps", "high", ["schoenfeld2017_load", "lopez2021_load", "schoenfeld2021_continuum", "acsm2009_progression"],
        ["all"],
        "Гипертрофия одинакова в широком диапазоне нагрузок (~30–85% 1ПМ) при работе близко к отказу; сила растёт "
        "больше с тяжёлыми весами (≤8ПМ и 9–15ПМ лучше, чем >15ПМ). Сила — 1–6 повторов в базовых, масса — 6–15 "
        "(изоляция до 20–30), выносливость мышц — 15–25 при <60% 1ПМ.",
        "Hypertrophy is similar across a wide load range (~30–85% 1RM) when sets are close to failure; strength grows "
        "more with heavy loads (≤8RM and 9–15RM beat >15RM). Strength 1–6 reps on compounds, size 6–15 (isolation up to "
        "20–30), muscular endurance 15–25 at <60% 1RM.",
        "Сила 1–6 повт., масса 6–15 (изоляция до 20–30), выносливость 15–25; лёгкий вес работает только близко к отказу.",
        "Strength 1–6 reps, size 6–15 (isolation up to 20–30), endurance 15–25; light loads work only near failure."),
    _pr("proximity_to_failure", "high", ["refalo2023_failure", "robinson2024_rir", "refalo2024_rct", "zourdos2016_rpe"],
        ["all"],
        "Чем ближе подход к отказу, тем больше рост мышц (≈−0,5% прироста на каждый повтор в запасе), но 1–2 повтора в "
        "запасе (RIR) дают тот же рост, что и отказ, при меньшей усталости. Силе RIR почти не важен. Базовые со "
        "свободным весом — 1–3 RIR, изоляция и тренажёры — 0–2 RIR, новички первые недели — 2–3 RIR. RPE ≈ 10 − RIR.",
        "The closer a set is to failure the more growth (≈−0.5% per rep in reserve), yet 1–2 reps in reserve (RIR) grow "
        "muscle as well as failure with less fatigue. RIR hardly matters for strength. Free-weight compounds 1–3 RIR, "
        "isolation and machines 0–2 RIR, beginners 2–3 RIR in the first weeks. RPE ≈ 10 − RIR.",
        "Базовые 1–3 повтора в запасе, изоляция 0–2, новички 2–3; до отказа в базовых не доводить. RPE = 10 − RIR.",
        "Compounds 1–3 reps in reserve, isolation 0–2, beginners 2–3; no failure on compounds. RPE = 10 − RIR."),
    _pr("rest_intervals", "high", ["singer2024_rest", "schoenfeld2016_rest", "acsm2009_progression"],
        ["all"],
        "Для роста мышц и силы отдых не короче 60 с: дольше 60 с немного лучше для роста, после 90 с разницы нет. Тяжёлые многосуставные — "
        "2–3 мин (сила 3–5 мин), изоляция — 60–120 с; у тренированных 3 мин лучше 1 мин и для силы, и для массы.",
        "For muscle and strength rest at least 60 s: longer than 60 s is slightly better for growth, beyond 90 s no difference. Heavy compounds "
        "2–3 min (strength 3–5 min), isolation 60–120 s; in trained lifters 3 min beat 1 min for strength and size.",
        "Отдых: базовые 2–3 мин (сила 3–5), изоляция 60–120 с; для массы и силы не меньше 60 с.",
        "Rest: compounds 2–3 min (strength 3–5), isolation 60–120 s; for size and strength never under 60 s."),
    _pr("exercise_order", "high", ["nunes2021_order", "simao2012_order", "acsm2009_progression"],
        ["all"],
        "Сила растёт больше в упражнении, стоящем первым; на гипертрофию порядок не влияет. По умолчанию многосуставные "
        "раньше изолирующих, крупные мышцы раньше мелких; приоритетное упражнение или мышцу ставим в начало.",
        "Strength improves most in the exercise done first; order does not affect hypertrophy. By default compounds "
        "before isolation and large muscles before small; put the priority lift or muscle first.",
        "Многосуставные первыми; приоритетная мышца — в начало тренировки.",
        "Compounds first; the priority muscle goes at the start of the session."),
    _pr("progression", "high", ["acsm2009_progression", "plotkin2022_reps", "moesgaard2022_period", "grgic2017_dup"],
        ["all"],
        "Прогрессия: вес +2–10%, когда на двух тренировках подряд сделано на 1–2 повтора больше цели; добавлять повторы "
        "при том же весе так же эффективно (двойная прогрессия). Новичку — линейная, среднему — двойная, продвинутому — "
        "волна (тяжёлый/объёмный день): для силы тренированных она лучше линейной (ES 0,61), для массы разницы нет.",
        "Progression: add 2–10% load after beating the rep target by 1–2 reps in two sessions; adding reps at the same "
        "load works as well (double progression). Beginners linear, intermediates double progression, advanced an "
        "undulating wave (heavy/volume days): better than linear for strength in trained lifters (ES 0.61), equal for size.",
        "Прогрессия: сначала повторы до верха диапазона, потом +вес (2–10%); новичок — линейно, средний — двойная, продвинутый — волна.",
        "Progression: reps to the top of the range, then +load (2–10%); beginner linear, intermediate double, advanced wave."),
    _pr("failure_reset", "practitioner", ["prog_stronglifts", "prog_gzclp", "prog_reddit_ppl", "prog_starting_strength"],
        ["level:beginner", "goal:strength"],
        "Популярные программы для новичков сходятся: не выполнил план — повтори вес; после повторных неудач снизь вес на "
        "10% и снова поднимайся; когда сбросы повторяются — меньше подходов (5×5 → 3×5) или переход на недельную "
        "прогрессию. У нас: −10% после двух неудач подряд.",
        "Popular beginner programs agree: missed the plan — repeat the load; after repeated misses drop 10% and climb "
        "again; when resets keep coming — fewer sets (5×5 → 3×5) or move to weekly progression. Here: −10% after two misses.",
        "Не выполнил — повтори вес; после двух неудач −10% и снова вверх.",
        "Missed — repeat the load; after two misses −10% and build back up."),
    _pr("deload", "medium", ["bell2023_deload", "rogerson2024_deload", "coleman2024_deload", "rp_quads", "prog_stronglifts"],
        ["level:intermediate", "level:advanced"],
        "Разгрузка — период сниженного объёма: на практике раз в 5,6±2,3 недели на ~6 дней. Снижают подходы, повторы, "
        "число упражнений; вес можно сохранить или немного снизить; частоту не меняют. Неделя полного отдыха не дала "
        "пользы и снизила прирост силы ног, поэтому разгрузка активная. Новичкам в первые 8–12 недель плановая не нужна — это практика линейных программ (StrongLifts, Starting Strength), а не эксперимент.",
        "A deload is a period of reduced volume: in practice every 5.6±2.3 weeks for ~6 days. Cut sets, reps and "
        "exercises; load may stay or drop slightly; keep frequency. A full week off gave no benefit and reduced leg "
        "strength gains, so deloads stay active. Beginners need no planned deload in the first 8–12 weeks — this is the practice of linear beginner programs (StrongLifts, Starting Strength), not an experiment.",
        "Разгрузка раз в 4–6 недель: −1 подход и −15% веса, частота прежняя; не полный отдых.",
        "Deload every 4–6 weeks: −1 set and −15% load, same frequency; not a full week off."),
    _pr("warmup", "medium", ["abad2011_warmup", "enes2025_warmup", "ribeiro2020_warmup", "iversen2021_time"],
        ["all"],
        "Специальные разминочные подходы нужны перед первым тяжёлым (≤6 повторов, >80% 1ПМ) упражнением на движение: "
        "~8×50%, 3–5×70%, 1–2×85% рабочего веса. При весах ~10ПМ хватает 0–1 подхода. Общая разминка 3–5 мин — по "
        "желанию; следующие упражнения на те же мышцы разминки не требуют.",
        "Specific warm-up sets are needed before the first heavy (≤6 reps, >80% 1RM) exercise of a movement: ~8×50%, "
        "3–5×70%, 1–2×85% of the working load. At ~10RM loads 0–1 set is enough. A 3–5 min general warm-up is optional; "
        "later exercises for the same muscles need no warm-up sets.",
        "Перед первым тяжёлым упражнением 2–3 разминочных подхода (50/70/85%); при 8–12 повторах — 0–1.",
        "Before the first heavy lift 2–3 warm-up sets (50/70/85%); with 8–12 reps 0–1."),
    _pr("stretching", "high", ["behm2016_stretch", "lauersen2014_injury", "vanhooren2018_cooldown"],
        ["all"],
        "Статическая растяжка ≥60 с на мышцу прямо перед силовой снижает результат на 4,6% (<60 с — на 1,1%), "
        "динамическая даёт +1,3%. Растяжка не снижает травмы (0,963), силовые снижают до трети (0,315). Перед "
        "тренировкой — динамика, долгая растяжка и заминка 3–5 мин — после, по желанию.",
        "Static stretching ≥60 s per muscle right before lifting cuts performance by 4.6% (<60 s by 1.1%); dynamic "
        "stretching adds 1.3%. Stretching does not reduce injuries (0.963); strength training cuts them to a third "
        "(0.315). Dynamic moves before, long stretches and a 3–5 min cool-down after, optionally.",
        "До тренировки — динамика, статическая растяжка — после; от травм защищают силовые, а не растяжка.",
        "Dynamic moves before, static stretching after; strength work, not stretching, prevents injuries."),
    _pr("concurrent_training", "high", ["schumann2022_concurrent", "eddens2018_sequence", "wilson2012_concurrent"],
        ["goal:loss", "goal:endurance", "goal:tone", "goal:muscle"],
        "Кардио не мешает гипертрофии и максимальной силе (SMD −0,01 и −0,06), но снижает взрывную силу, особенно в "
        "одной тренировке. Разносить на ≥3 ч; если в один день — сначала силовая (+6,9% к силе ног). Бег мешает больше "
        "велосипеда: перед днём ног — велосипед или ходьба.",
        "Cardio does not hurt hypertrophy or maximal strength (SMD −0.01 and −0.06) but reduces explosive strength, "
        "especially in the same session. Separate by ≥3 h; on the same day lift first (+6.9% leg strength). Running "
        "interferes more than cycling: before leg days prefer bike or walking.",
        "Кардио после силовой или через ≥3 ч; перед днём ног — велосипед/ходьба, не бег.",
        "Cardio after lifting or ≥3 h apart; before leg days bike/walk, not running."),
    _pr("activity_guidelines", "high", ["who2020", "donnelly2009_weightloss", "paluch2022_steps", "garber2011_acsm"],
        ["goal:loss", "goal:tone", "goal:endurance"],
        "ВОЗ: 150–300 мин умеренной или 75–150 мин интенсивной активности в неделю плюс силовые на все крупные мышцы "
        "≥2 дней; 65+ — баланс и сила ≥3 дней. Для заметного похудения нужно >250 мин в неделю. Шаги: смертность "
        "снижается до плато на 8–10 тыс. в день до 60 лет и 6–8 тыс. после.",
        "WHO: 150–300 min moderate or 75–150 min vigorous activity per week plus strength work for all major muscles on "
        "≥2 days; 65+ add balance and strength ≥3 days. Meaningful weight loss needs >250 min/week. Steps: mortality "
        "falls until a plateau at 8–10k/day under 60 and 6–8k after.",
        "Кардио 150–300 мин умеренно в неделю (для похудения >250), силовые ≥2 дней, 8–10 тыс. шагов.",
        "Cardio 150–300 min moderate per week (>250 for weight loss), strength ≥2 days, 8–10k steps."),
    _pr("fat_loss_strength", "high", ["murphy2022_deficit", "sardeli2018_restriction", "helms2014_nutrition", "bickel2011_maintenance"],
        ["goal:loss"],
        "На дефиците силовые не заменяем кардио: они сохраняют мышцы (у пожилых с ожирением предотвращали ~93% потери). "
        "Дефицит больше ~500 ккал/сут мешает набору мышц, но не силе; темп −0,5–1% массы в неделю. Интенсивность "
        "(рабочие веса) держим, объём можно снизить — у молодых ~1/3 объёма сохраняет мышцы.",
        "In a deficit keep lifting rather than replacing it with cardio: it preserves muscle (prevented ~93% of loss in "
        "older adults with obesity). A deficit above ~500 kcal/day blunts muscle gain but not strength; aim for −0.5–1% "
        "body mass per week. Keep the working loads; volume may drop — in young people ~1/3 of volume maintains muscle.",
        "Похудение: силовые с прежними весами 2–4 раза, объём у нижней границы, дефицит ≤500 ккал, −0,5–1% веса в неделю.",
        "Fat loss: lift 2–4×/week with the same loads, volume at the low end, deficit ≤500 kcal, −0.5–1% body mass/week."),
    _pr("protein", "high", ["morton2018_protein", "helms2014_nutrition"],
        ["goal:muscle", "goal:loss", "goal:strength", "goal:tone"],
        "Белок 1,6–2,2 г/кг в сутки: прирост массы выходит на плато около 1,62 г/кг, верх доверительного интервала 2,2. "
        "Сухим атлетам на сушке — 2,3–3,1 г/кг сухой массы.",
        "Protein 1.6–2.2 g/kg/day: gains plateau around 1.62 g/kg, the upper confidence bound is 2.2. Lean athletes "
        "dieting — 2.3–3.1 g/kg of fat-free mass.",
        "Белок 1,6–2,2 г/кг в сутки.",
        "Protein 1.6–2.2 g/kg/day."),
    _pr("supersets_time", "high", ["zhang2025_superset", "iversen2021_time"],
        ["all"],
        "Суперсеты мышц-антагонистов (жим + тяга, бицепс + трицепс) сохраняют объём (повторов даже больше, SMD 0,68) и "
        "сокращают время почти вдвое; долгосрочно результаты те же (3 исследования). Суперсеты на одну мышцу снижают "
        "объём (SMD −1,08). Не для тяжёлых базовых со штангой, новичков в первые 2 недели и при давлении/сердце.",
        "Antagonist supersets (press + row, biceps + triceps) keep volume (even more reps, SMD 0.68) and nearly halve "
        "session time; long-term results are the same (3 studies). Same-muscle supersets reduce volume (SMD −1.08). Not "
        "for heavy barbell compounds, beginners in their first 2 weeks, or blood pressure/heart conditions.",
        "Мало времени — укоротить разминку, убрать изоляцию, вспомогательные объединить в суперсеты антагонистов; базовые сохранить.",
        "Short on time — shorten the warm-up, drop isolation, pair accessories as antagonist supersets; keep compounds."),
    _pr("time_efficient_minimum", "high", ["iversen2021_time", "currier2023_nma"],
        ["all"],
        "Минимальная действенная программа: многосуставные упражнения с полной амплитудой — ноги (присед/жим ногами), "
        "тяга и жим; ≥4 рабочих подходов на мышцу в неделю (10+ при наличии времени), 6–15ПМ. Недельный объём важнее "
        "частоты. При нехватке времени первыми сокращаем общую разминку и растяжку, затем изоляцию, затем подходы.",
        "Minimum effective program: full-range compound lifts — legs (squat/leg press), a pull and a press; ≥4 working "
        "sets per muscle per week (10+ if time allows), 6–15RM. Weekly volume matters more than frequency. When short on "
        "time, trim general warm-up and stretching first, then isolation, then sets.",
        "Ядро тренировки — ноги, тяга, жим; при нехватке времени режем разминку/растяжку → изоляцию → подходы.",
        "Core of a session — legs, pull, press; when short on time cut warm-up/stretching → isolation → sets."),
    _pr("sex_differences", "high", ["roberts2020_sex", "colenso2023_cycle"],
        ["goal:tone", "all"],
        "Мужчины и женщины при одной программе одинаково прибавляют в массе (разница 0,07, незначимо); у женщин даже "
        "больше относительный прирост силы верха. «Тонус» — это рост мышц плюс снижение жира, а не «лёгкие веса». "
        "Строить программу по фазам цикла оснований нет — корректируем по самочувствию.",
        "Men and women on the same program gain muscle equally (difference 0.07, not significant); women even gain more "
        "relative upper-body strength. \"Toning\" means building muscle plus losing fat, not \"light weights\". There is no "
        "basis for programming by menstrual phase — adjust by how you feel.",
        "Отдельной «женской» методики нет; тонус = прогрессия нагрузки в 6–15 повт. + дефицит калорий.",
        "No separate \"female\" method; toning = progressive overload at 6–15 reps + calorie deficit."),
    _pr("home_training", "medium", ["kikuchi2017_pushup", "lopes2019_bands", "schoenfeld2021_continuum", "prog_bwf_rr"],
        ["all"],
        "Отжимания при подходящей нагрузке растят мышцы как жим лёжа на 40% 1ПМ; резинки не уступают свободным весам "
        "по силе. Дома прогрессируем повторами до близкого отказа (до ~25–30), темпом, работой одной рукой/ногой и "
        "усложнением варианта: когда 3×8 выполнено — следующая ступень с 3×5.",
        "Push-ups under adequate loading build muscle like a 40% 1RM bench press; bands match free weights for strength. "
        "At home progress with reps close to failure (up to ~25–30), tempo, single-limb work and harder variations: once "
        "3×8 is done, move to the next step at 3×5.",
        "Дома: повторы до близкого отказа (до 25), темп, одна рука/нога, более сложный вариант упражнения.",
        "At home: reps near failure (up to 25), tempo, single-limb work, harder variations."),
    _pr("glute_training", "medium", ["plotkin2023_hipthrust", "contreras_glute_frequency", "rp_glutes"],
        ["goal:tone"],
        "Хип-траст и присед при равных подходах дали сходный рост ягодиц; присед сильнее растит квадрицепс. Сессия на "
        "ягодицы: мост/хип-траст + присед или выпад + наклон + отведение. Хип-траст 2–3 раза в неделю, глубокие "
        "приседы и выпады 1–2 раза, лёгкие «накачки» с резинкой — чаще.",
        "Hip thrusts and squats with equal sets grew the glutes similarly; squats grow the quads more. A glute session: "
        "bridge/hip thrust + squat or lunge + hinge + abduction. Hip thrust 2–3×/week, deep squats and lunges 1–2×, light "
        "band \"pump\" work more often.",
        "Ягодицы: хип-траст 2–3 раза в неделю + присед/выпад + наклон + отведение.",
        "Glutes: hip thrust 2–3×/week + squat/lunge + hinge + abduction."),
    _pr("lengthened_range", "medium", ["maeo2023_overhead", "schoenfeld2015_tempo"],
        ["goal:muscle", "goal:tone"],
        "Полная амплитуда не хуже частичной, а работа в растянутом положении мышцы может давать больше роста: разгибания "
        "из-за головы дали всему трицепсу в 1,4 раза больше прироста (+19,9% против +13,9%), длинной головке — в 1,5 раза. Темп 0,5–8 с на повтор даёт "
        "сходный рост — контролируемый 1–2 с вверх и 2–3 с вниз.",
        "Full range is at least as good as partial, and training in the lengthened position may grow more: overhead "
        "extensions grew the whole triceps 1.4× more (+19.9% vs +13.9%) and the long head 1.5× more. Rep durations of 0.5–8 s give similar growth — use a "
        "controlled 1–2 s up and 2–3 s down.",
        "Полная амплитуда, варианты с растяжением мышцы; темп контролируемый, не догма.",
        "Full range, stretch-position variations; controlled tempo, not dogma."),
    _pr("endurance_distribution", "medium", ["seiler2010_distribution", "ronnestad2014_endurance", "viada_hybrid", "storoschuk2025_zone2"],
        ["goal:endurance"],
        "Выносливость: ~80% кардио-сессий лёгкие (можно говорить фразами) и ~20% интервалы, интервалы не чаще 2 раз в "
        "неделю и не подряд. Силовые 2 раза в неделю (поддержание — раз в 7–10 дней): 3 подхода по 4–12ПМ, без отказа, "
        "отдых ~2 мин — улучшают экономичность бега и езды.",
        "Endurance: ~80% of cardio sessions easy (conversational) and ~20% intervals, intervals no more than twice a week "
        "and not on consecutive days. Strength twice a week (maintenance every 7–10 days): 3 sets of 4–12RM, no failure, "
        "~2 min rest — improves running and cycling economy.",
        "Выносливость: 80% кардио лёгкое, интервалы ≤2 в неделю; силовые 2× по 3×4–12, без отказа.",
        "Endurance: 80% easy cardio, intervals ≤2/week; strength 2× at 3×4–12, no failure."),
]
PRINCIPLES_BY_ID: dict[str, dict] = {p["id"]: p for p in PRINCIPLES}


# --------------------------------------------------------------------------- #
#  3. Параметры по цели
#  reps: heavy — тяжёлый день волны, main — базовые, volume — объёмный день,
#        accessory — изоляция/вспомогательные. Все в пределах REPS_RANGE.
#  sets: базовые подходы на упражнение по роли слота (до корректировки объёмом).
#  rir: (min, max, цель) для базовых и изоляции; rpe в программе = 10 − цель.
#  rest_sec: (min, max) — в неделю берём середину, округлённую к 15 с.
#  volume_mult: множитель недельного объёма уровня.
# --------------------------------------------------------------------------- #
GOAL_PARAMS: dict[str, dict] = {
    "muscle": {
        "name": ("набор мышц", "muscle gain"),
        "reps": {"heavy": (5, 8), "main": (6, 10), "volume": (8, 12), "accessory": (10, 15)},
        "sets": {"main": 3, "secondary": 3, "accessory": 3},
        "rir": {"compound": (1, 3, 2), "isolation": (0, 2, 1)},
        "rest_sec": {"compound": (120, 180), "isolation": (60, 90)},
        "volume_mult": 1.0,
        "cardio": {"sessions": (2, 3), "minutes": (20, 30), "kind": "steady",
                   "ru": "2–3 раза по 20–30 мин умеренно (велосипед, ходьба в горку) после силовой или в другой день",
                   "en": "2–3 × 20–30 min moderate (bike, incline walk) after lifting or on another day"},
        "notes": ("Прогрессия нагрузки в 6–15 повторах, изоляция ближе к отказу; белок 1,6–2,2 г/кг.",
                  "Progressive overload at 6–15 reps, isolation closer to failure; protein 1.6–2.2 g/kg."),
        "sources": ["schoenfeld2021_continuum", "refalo2023_failure", "singer2024_rest", "pelland2025_dose", "helms2015_training",
                    "who2020"],
    },
    "strength": {
        "name": ("сила", "strength"),
        "reps": {"heavy": (3, 5), "main": (4, 6), "volume": (6, 10), "accessory": (8, 12)},
        "sets": {"main": 4, "secondary": 3, "accessory": 2},
        "rir": {"compound": (1, 3, 2), "isolation": (1, 2, 2)},
        "rest_sec": {"compound": (180, 300), "isolation": (90, 120)},
        "volume_mult": 0.8,
        "cardio": {"sessions": (2, 2), "minutes": (20, 30), "kind": "steady",
                   "ru": "2 раза по 20–30 мин лёгкого кардио в дни отдыха; перед днём ног — не бег",
                   "en": "2 × 20–30 min easy cardio on rest days; no running before leg days"},
        "notes": ("Базовые движения 2–3 раза в неделю тяжело (≥80% 1ПМ), разминочные подходы по нарастающей; новичкам 5–8 повторов.",
                  "Main lifts 2–3×/week heavy (≥80% 1RM), ramped warm-up sets; beginners use 5–8 reps."),
        "sources": ["lopez2021_load", "acsm2009_progression", "pelland2025_dose", "grgic2018_freq_strength", "schoenfeld2016_rest"],
    },
    "loss": {
        "name": ("похудение", "fat loss"),
        "reps": {"heavy": (6, 8), "main": (6, 10), "volume": (10, 12), "accessory": (12, 15)},
        "sets": {"main": 3, "secondary": 3, "accessory": 2},
        "rir": {"compound": (1, 3, 2), "isolation": (0, 2, 1)},
        "rest_sec": {"compound": (90, 150), "isolation": (60, 75)},
        "volume_mult": 0.85,
        "cardio": {"sessions": (3, 4), "minutes": (25, 45), "kind": "steady",
                   "ru": "150–300 мин умеренного кардио в неделю (для заметной потери >250): ходьба, велосипед, эллипс; интервалы по желанию, не больше 1–2 раз (жира в кг уходит лишь немного больше); 8–10 тыс. шагов",
                   "en": "150–300 min moderate cardio per week (>250 for notable loss): walking, bike, elliptical; intervals optional, at most 1–2× (only slightly more fat loss in kg); 8–10k steps"},
        "notes": ("Силовые сохраняют мышцы на дефиците — веса не снижаем; дефицит ≤500 ккал, −0,5–1% массы в неделю.",
                  "Lifting preserves muscle in a deficit — keep the loads; deficit ≤500 kcal, −0.5–1% body mass per week."),
        "sources": ["murphy2022_deficit", "sardeli2018_restriction", "donnelly2009_weightloss", "who2020", "singer2024_rest",
                    "viana2019_hiit"],
    },
    "tone": {
        "name": ("тонус и здоровье", "tone and health"),
        "reps": {"heavy": (6, 10), "main": (8, 12), "volume": (10, 15), "accessory": (12, 15)},
        "sets": {"main": 3, "secondary": 3, "accessory": 2},
        "rir": {"compound": (1, 3, 2), "isolation": (0, 2, 1)},
        "rest_sec": {"compound": (90, 120), "isolation": (60, 75)},
        "volume_mult": 0.85,
        "cardio": {"sessions": (2, 3), "minutes": (20, 30), "kind": "steady",
                   "ru": "2–3 раза по 20–30 мин умеренно, итого ≥150 мин активности в неделю с ходьбой",
                   "en": "2–3 × 20–30 min moderate, ≥150 min of activity per week including walking"},
        "notes": ("«Тонус» = рост мышц + снижение жира: та же прогрессия нагрузки, не «лёгкие веса на много повторов».",
                  "\"Tone\" = muscle gain + fat loss: the same progressive overload, not \"light weights for many reps\"."),
        "sources": ["roberts2020_sex", "iversen2021_time", "who2020", "schoenfeld2021_continuum"],
    },
    "endurance": {
        "name": ("выносливость", "endurance"),
        "reps": {"heavy": (6, 10), "main": (8, 12), "volume": (12, 15), "accessory": (15, 20)},
        "sets": {"main": 3, "secondary": 3, "accessory": 2},
        "rir": {"compound": (2, 3, 3), "isolation": (1, 3, 2)},
        "rest_sec": {"compound": (60, 120), "isolation": (30, 60)},
        "volume_mult": 0.6,
        "cardio": {"sessions": (3, 4), "minutes": (25, 45), "kind": "mixed",
                   "ru": "3–4 кардио-сессии: ~80% лёгких (разговорный темп), ~20% интервалов, интервалы не чаще 2 раз и не подряд",
                   "en": "3–4 cardio sessions: ~80% easy (conversational), ~20% intervals, intervals at most twice and not back-to-back"},
        "notes": ("Силовые 2–3 раза без отказа поддерживают экономичность; в один день — сначала силовая, потом лёгкое кардио.",
                  "Strength 2–3× without failure supports economy; on the same day lift first, then easy cardio."),
        "sources": ["ronnestad2014_endurance", "seiler2010_distribution", "viada_hybrid", "acsm2009_progression", "schumann2022_concurrent"],
    },
}


# --------------------------------------------------------------------------- #
#  4. Параметры по уровню
#  weekly_sets: рабочие дробные подходы на крупную мышцу в неделю при
#  volume_mult = 1 (цель задаёт множитель); focus_max — потолок для акцента.
# --------------------------------------------------------------------------- #
LEVEL_PARAMS: dict[str, dict] = {
    "beginner": {
        "name": ("новичок", "beginner"),
        "weekly_sets": {"min": 6, "target": 8, "max": 10, "focus_max": 12},
        "session_sets_per_muscle_max": 6,
        "frequency_per_muscle": (2, 3),
        "progression": "linear",
        "deload_every_weeks": 8,          # плановая разгрузка не раньше 8 недель
        "max_accumulation_weeks": 7,
        "max_exercises": 6,
        "max_difficulty": 2,              # как trainer_logic._max_difficulty
        "rir_extra": 1,                   # первые недели +1 повтор в запасе к цели
        "notes": ("Вес растёт от тренировки к тренировке; 1–3 подхода на упражнение, не больше 5–6 упражнений; техника важнее отказа.",
                  "Load goes up session to session; 1–3 sets per exercise, no more than 5–6 exercises; technique over failure."),
        "sources": ["acsm2009_progression", "iversen2021_time", "garber2011_acsm", "prog_stronglifts", "robinson2024_rir"],
    },
    "intermediate": {
        "name": ("средний уровень", "intermediate"),
        "weekly_sets": {"min": 10, "target": 12, "max": 16, "focus_max": 20},
        "session_sets_per_muscle_max": 8,
        "frequency_per_muscle": (2, 3),
        "progression": "double_progression",
        "deload_every_weeks": 5,
        "max_accumulation_weeks": 5,
        "max_exercises": 7,
        "max_difficulty": 3,
        "rir_extra": 0,
        "notes": ("Прогресс раз в неделю или цикл: двойная прогрессия в диапазоне повторов, разгрузка раз в 4–6 недель.",
                  "Progress weekly or per cycle: double progression within a rep range, deload every 4–6 weeks."),
        "sources": ["acsm2009_progression", "plotkin2022_reps", "baz_valle2022_volume", "rogerson2024_deload"],
    },
    "advanced": {
        "name": ("продвинутый", "advanced"),
        "weekly_sets": {"min": 12, "target": 16, "max": 20, "focus_max": 24},
        "session_sets_per_muscle_max": 10,
        "frequency_per_muscle": (2, 4),
        "progression": "wave",
        "deload_every_weeks": 4,
        "max_accumulation_weeks": 4,
        "max_exercises": 8,
        "max_difficulty": 3,
        "rir_extra": 0,
        "notes": ("Волна тяжёлый/объёмный день, мезоцикл 3–5 недель с RIR 3→1 и разгрузкой; приоритет 1–3 мышцам.",
                  "Heavy/volume day wave, 3–5 week mesocycles with RIR 3→1 and a deload; prioritise 1–3 muscles."),
        "sources": ["moesgaard2022_period", "pelland2025_dose", "rp_landmarks", "bell2023_deload"],
    },
}


# --------------------------------------------------------------------------- #
#  5. Ориентиры объёма RP (практические оценки для лифтеров со стажем 3–7 лет,
#  НЕ данные экспериментов; у новичков ниже). Рабочие подходы в неделю,
#  пары (нижняя, верхняя граница). mav_p / mrv_p — при приоритете мышцы.
#  factor — доля «крупной» нормы уровня для этой группы: мелкие мышцы много
#  получают от базовых (дробный счёт), поэтому прямой работы им нужно меньше.
# --------------------------------------------------------------------------- #
VOLUME_LANDMARKS: dict[str, dict] = {
    "chest": {"mv": (2, 4), "mev": (4, 6), "mav": (6, 16), "mrv": (16, 24), "mav_p": (16, 24), "mrv_p": (24, 32),
              "freq": (2, 4), "factor": 1.0, "sources": ["rp_chest"]},
    "back": {"mv": (2, 6), "mev": (6, 8), "mav": (8, 20), "mrv": (20, 26), "mav_p": (20, 26), "mrv_p": (26, 34),
             "freq": (2, 4), "factor": 1.2, "sources": ["rp_back"],
             "note": ("Делить примерно поровну между горизонтальными и вертикальными тягами: по сути это две области "
                      "(широчайшие и середина спины), поэтому норма уровня для спины ×1,2.",
                      "Split roughly evenly between horizontal and vertical pulls: effectively two regions (lats and "
                      "mid-back), so the level norm for the back is ×1.2.")},
    "shoulders": {"mv": (2, 6), "mev": (6, 8), "mav": (8, 24), "mrv": (24, 30), "mav_p": (24, 30), "mrv_p": (30, 40),
                  "freq": (2, 6), "factor": 0.9, "sources": ["rp_side_delts", "rp_rear_delts", "rp_front_delts"],
                  "note": ("Ориентир средней дельты: передняя получает работу от жимов на грудь (MEV 0–2), задняя — от тяг (MEV 0–4).",
                           "Side-delt landmarks: front delts get work from chest presses (MEV 0–2), rear delts from rows (MEV 0–4).")},
    "biceps": {"mv": (6, 8), "mev": (8, 10), "mav": (14, 20), "mrv": (20, 26), "mav_p": (20, 26), "mrv_p": (26, 35),
               "freq": (3, 6), "factor": 0.8, "sources": ["rp_biceps"],
               "note": ("Значительную часть объёма дают тяги (0,5 подхода).", "Rows supply much of the volume (0.5 per set).")},
    "triceps": {"mv": (0, 4), "mev": (4, 6), "mav": (6, 16), "mrv": (16, 20), "mav_p": (16, 20), "mrv_p": (20, 26),
                "freq": (2, 4), "factor": 0.8, "sources": ["rp_triceps"],
                "note": ("При большом числе жимов прямая работа может быть минимальной.", "With plenty of pressing little direct work is needed.")},
    "quads": {"mv": (2, 4), "mev": (4, 6), "mav": (6, 14), "mrv": (14, 18), "mav_p": (10, 18), "mrv_p": (18, 24),
              "freq": (2, 5), "factor": 1.0, "sources": ["rp_quads"]},
    "hamstrings": {"mv": (0, 2), "mev": (2, 4), "mav": (2, 8), "mrv": (8, 14), "mav_p": (8, 14), "mrv_p": (14, 20),
                   "freq": (2, 3), "factor": 0.9, "sources": ["rp_hamstrings"],
                   "note": ("Числа MAV так указаны на странице RP.", "MAV figures are as published by RP.")},
    "glutes": {"mv": (2, 6), "mev": (6, 8), "mav": (8, 24), "mrv": (24, 30), "mav_p": (24, 30), "mrv_p": (30, 40),
               "freq": (2, 5), "factor": 0.9, "sources": ["rp_glutes"],
               "note": ("Приседы, выпады и тяги дают ягодицам много косвенной работы.", "Squats, lunges and hinges give the glutes plenty of indirect work.")},
    "calves": {"mv": (2, 4), "mev": (4, 6), "mav": (6, 16), "mrv": (16, 24), "mav_p": (16, 24), "mrv_p": (24, 32),
               "freq": (3, 6), "factor": 0.6, "sources": ["rp_calves"]},
    "core": {"mv": (0, 4), "mev": (0, 4), "mav": (4, 12), "mrv": (12, 20), "mav_p": (16, 24), "mrv_p": (24, 32),
             "freq": (3, 6), "factor": 0.6, "sources": ["rp_abs"]},
}


# --------------------------------------------------------------------------- #
#  6. Модели прогрессии и как они реализованы у нас
# --------------------------------------------------------------------------- #
PROGRESSION_MODELS: dict[str, dict] = {
    "linear": {
        "name": ("линейная", "linear"),
        "levels": ["beginner"],
        "ru": "Если все подходы выполнены — в следующей тренировке вес больше: верх тела +1–2,5 кг, ноги +2,5–5 кг. "
              "Не выполнил — повтори вес; после повторных неудач −10% и снова вверх.",
        "en": "If all sets are completed, add load next session: upper body +1–2.5 kg, legs +2.5–5 kg. Missed — repeat the "
              "load; after repeated misses −10% and climb again.",
        "switch_when": ("Сбросы веса повторяются 2–3 раза подряд или прошло ~3 месяца — переход на двойную прогрессию.",
                        "Resets repeat 2–3 times or ~3 months have passed — switch to double progression."),
        "implementation": ("trainer_logic.next_targets: success → +шаг веса (новичок, «легко», низ тела — +2 шага); "
                           "fail дважды → −10%. Диапазон повторов узкий (напр. 5–8).",
                           "trainer_logic.next_targets: success → +one load step (beginner, 'easy', lower body — +2 steps); "
                           "two fails → −10%. Narrow rep range (e.g. 5–8)."),
        "sources": ["prog_stronglifts", "prog_bbr", "prog_starting_strength", "acsm2009_progression"],
    },
    "double_progression": {
        "name": ("двойная прогрессия", "double progression"),
        "levels": ["beginner", "intermediate", "advanced"],
        "ru": "Работаем в диапазоне (например 3×8–12) при 1–2 RIR. Сначала добавляем повторы; когда все подходы дошли до "
              "верха диапазона — +2–10% веса и возврат к низу. Добавление повторов так же эффективно, как добавление веса.",
        "en": "Work within a range (e.g. 3×8–12) at 1–2 RIR. Add reps first; once every set hits the top of the range, "
              "add 2–10% load and drop back to the bottom. Adding reps works as well as adding load.",
        "switch_when": ("Недельный прогресс остановился на 3+ неделях в основных движениях — волна и мезоциклы.",
                        "Weekly progress stalls for 3+ weeks on main lifts — move to waves and mesocycles."),
        "implementation": ("next_targets: partial → +1 повтор в каждом подходе при том же весе; success (все ≥ reps_max) → +шаг "
                           "веса и цель = reps_min. Для упражнений с весом тела: +2 повтора до 25, потом «усложнить» "
                           "(progression_next_slug).",
                           "next_targets: partial → +1 rep per set at the same load; success (all ≥ reps_max) → +load step "
                           "and target = reps_min. Bodyweight: +2 reps up to 25, then a harder variation (progression_next_slug)."),
        "sources": ["plotkin2022_reps", "acsm2009_progression", "prog_reddit_ppl", "prog_nuckols"],
    },
    "wave": {
        "name": ("волна (тяжёлый/объёмный день)", "wave (heavy/volume days)"),
        "levels": ["intermediate", "advanced"],
        "ru": "Недельная волна: тяжёлый день (3–8 повторов) и объёмный день (8–15) на те же мышцы разными упражнениями "
              "(PHUL, PHAT). Мезоцикл 3–5 недель: RIR снижается 3 → 1, затем разгрузка.",
        "en": "Weekly wave: a heavy day (3–8 reps) and a volume day (8–15) for the same muscles with different exercises "
              "(PHUL, PHAT). 3–5 week mesocycle: RIR falls 3 → 1, then a deload.",
        "switch_when": ("Применяется, пока растут результаты; при застое — сменить упражнения тяжёлого дня.",
                        "Use while results improve; on a plateau rotate the heavy-day exercises."),
        "implementation": ("В шаблонах дни помечены intensity=heavy|volume; build_week берёт разные slug для тяжёлого и "
                           "объёмного дня, потому что TrainerExerciseState хранит одну цель на упражнение. Фазы "
                           "base/build/peak/deload задаёт periodization_for, вес по фазам не меняется (кроме deload), "
                           "меняется целевой RIR.",
                           "Templates tag days intensity=heavy|volume; build_week uses different slugs for heavy and volume "
                           "days because TrainerExerciseState keeps one target per exercise. periodization_for sets "
                           "base/build/peak/deload; load does not change by phase (except deload), the target RIR does."),
        "sources": ["moesgaard2022_period", "grgic2017_dup", "prog_phul", "prog_phat", "rp_landmarks"],
    },
}

# Фазы периодизации: целевой RIR для базовых (новичку +1 из LEVEL_PARAMS.rir_extra).
# weight_pct / sets_delta совпадают с trainer_logic (DELOAD_WEIGHT_PCT=85, DELOAD_SETS_DELTA=−1).
PHASE_GUIDE: dict[str, dict] = {
    "base": {"weight_pct": 100, "sets_delta": 0, "rir": 3,
             "ru": "база: освоить веса, 3 повтора в запасе", "en": "base: settle loads, 3 reps in reserve"},
    "build": {"weight_pct": 100, "sets_delta": 0, "rir": 2,
              "ru": "рост: прибавляем повторы и вес, 2 в запасе", "en": "build: add reps and load, 2 in reserve"},
    "peak": {"weight_pct": 100, "sets_delta": 0, "rir": 1,
             "ru": "пик: 1 повтор в запасе, изоляция до отказа", "en": "peak: 1 rep in reserve, isolation to failure"},
    "deload": {"weight_pct": 85, "sets_delta": -1, "rir": 4,
               "ru": "разгрузка: −15% веса и −1 подход, частота та же", "en": "deload: −15% load and −1 set, same frequency"},
}
PHASE_LABELS = {"base": ("База", "Base"), "build": ("Рост", "Build"), "peak": ("Пик", "Peak"), "deload": ("Разгрузка", "Deload")}


# --------------------------------------------------------------------------- #
#  7. Паттерны движений → упражнения каталога в порядке предпочтения
#  Порядок: сначала зал (штанга/тренажёры), затем гантели и резинки, затем вес
#  тела — pick_exercise отфильтрует недоступное. Новичкам (кроме цели «сила»)
#  среди подходящих сначала берутся упражнения сложности 1.
#  region: upper | lower | core | cardio — для разминки и акцента.
#  fallback: паттерны-заменители, если под профиль ничего не подошло.
#  first_by_goal: какие slug поднять в начало для цели.
# --------------------------------------------------------------------------- #
def _pt(muscle, region, kind, ru, en, options, fallback=(), first_by_goal=None, note=None):
    row = {"muscle": muscle, "region": region, "kind": kind, "ru": ru, "en": en,
           "options": list(options), "fallback": list(fallback), "first_by_goal": dict(first_by_goal or {})}
    if note:
        row["note"] = note
    return row


PATTERNS: dict[str, dict] = {
    "squat": _pt("quads", "lower", "compound", "присед", "squat",
                 ["bb_back_squat", "hack_squat", "leg_press", "goblet_squat", "bb_front_squat", "air_squat", "wall_sit",
                  # В конце списка: обычному профилю достаются полноценные приседания, а при
                  # ограничении knee основные варианты отсеиваются, и остаются эти.
                  "leg_press_partial", "box_squat_high"],
                 fallback=["lunge"]),
    # Мостов здесь нет: при больной пояснице или без гантелей наклон уходит в замену hip_thrust
    # (fallback), и конструктор недели видит, что движение «ягодичный мост» в дне уже занято —
    # иначе в одном дне стояли мост из слота наклона и hip thrust из своего слота.
    "hinge": _pt("hamstrings", "lower", "compound", "наклон (тяга)", "hinge",
                 ["bb_rdl", "bb_deadlift", "rdl_db", "single_leg_rdl", "kb_swing", "good_morning",
                  "hyperextension"],
                 fallback=["hip_thrust", "knee_flexion"], first_by_goal={"strength": ["bb_deadlift"]}),
    "lunge": _pt("quads", "lower", "compound", "выпад", "lunge",
                 ["bulgarian_split_squat", "db_lunge", "reverse_lunge", "walking_lunge", "step_up"],
                 fallback=["squat"]),
    "knee_extension": _pt("quads", "lower", "isolation", "разгибание ног", "knee extension",
                          ["leg_extension", "wall_sit", "wall_sit_high"]),
    "knee_flexion": _pt("hamstrings", "lower", "isolation", "сгибание ног", "knee flexion",
                        ["leg_curl", "nordic_curl"], fallback=["hinge"]),
    "hip_thrust": _pt("glutes", "lower", "compound", "ягодичный мост", "hip thrust",
                      ["hip_thrust", "db_hip_thrust", "glute_bridge", "single_leg_glute_bridge"],
                      fallback=["glute_abduction"]),
    "glute_abduction": _pt("glutes", "lower", "isolation", "отведение бедра", "hip abduction",
                           ["hip_abduction_machine", "cable_kickback", "band_lateral_walk", "band_glute_kickback"],
                           fallback=["hip_thrust"]),
    "horizontal_push": _pt("chest", "upper", "compound", "жим горизонтальный", "horizontal press",
                           ["bb_bench_press", "db_bench_press", "machine_chest_press", "pushup_decline", "pushup",
                            "pushup_incline", "pushup_knees", "dips_chest"],
                           fallback=["incline_push"]),
    "incline_push": _pt("chest", "upper", "compound", "жим под углом", "incline press",
                        ["db_incline_bench_press", "bb_incline_bench_press", "machine_chest_press", "pushup_decline",
                         "pushup", "pushup_incline", "pushup_knees"],
                        fallback=["horizontal_push"]),
    "chest_fly": _pt("chest", "upper", "isolation", "сведение рук", "chest fly",
                     ["cable_crossover", "pec_deck", "db_fly"]),
    "vertical_push": _pt("shoulders", "upper", "compound", "жим вверх", "overhead press",
                         ["bb_overhead_press", "db_shoulder_press", "machine_shoulder_press", "arnold_press", "pike_pushup"]),
    "lateral_delt": _pt("shoulders", "upper", "isolation", "махи в стороны", "lateral raise",
                        ["cable_lateral_raise", "db_lateral_raise", "band_lateral_raise"],
                        fallback=["vertical_push"]),
    "rear_delt": _pt("shoulders", "upper", "isolation", "задняя дельта", "rear delts",
                     ["face_pull", "db_rear_delt_fly", "band_face_pull"],
                     fallback=["horizontal_pull"]),
    "horizontal_pull": _pt("back", "upper", "compound", "тяга горизонтальная", "horizontal pull",
                           ["bb_row", "seated_cable_row", "db_row", "machine_row", "tbar_row", "inverted_row", "band_row"],
                           fallback=["vertical_pull"]),
    "vertical_pull": _pt("back", "upper", "compound", "тяга вертикальная", "vertical pull",
                         ["lat_pulldown", "pullup", "chinup", "pullup_negative", "pullup_band_assisted", "band_pulldown"],
                         fallback=["horizontal_pull"]),
    "elbow_flexion": _pt("biceps", "upper", "isolation", "сгибание рук", "biceps curl",
                         ["db_curl", "cable_curl", "bb_curl", "db_hammer_curl", "incline_db_curl", "concentration_curl", "band_curl"]),
    # Разгибания из-за головы первыми: в растянутом положении трицепс рос в 1,4 раза больше (maeo2023_overhead).
    "elbow_extension": _pt("triceps", "upper", "isolation", "разгибание рук", "triceps extension",
                           ["db_overhead_extension", "cable_pushdown", "skull_crusher", "db_kickback", "band_pushdown",
                            "diamond_pushup", "bench_dips", "close_grip_bench_press"]),
    "calf": _pt("calves", "lower", "isolation", "икры", "calves",
                ["db_standing_calf_raise", "seated_calf_raise", "single_leg_calf_raise", "standing_calf_raise"]),
    "core_anti_extension": _pt("core", "core", "isolation", "кор: антиразгибание", "core anti-extension",
                               ["plank", "dead_bug", "hollow_hold", "plank_shoulder_tap", "bird_dog"],
                               fallback=["core_rotation"]),
    "core_flexion": _pt("core", "core", "isolation", "кор: скручивание", "core flexion",
                        ["cable_crunch", "hanging_knee_raise", "reverse_crunch", "leg_raise", "crunch", "bicycle_crunch"],
                        fallback=["core_anti_extension"]),
    "core_rotation": _pt("core", "core", "isolation", "кор: антиротация", "core anti-rotation",
                         ["pallof_press", "side_plank", "bird_dog", "russian_twist"],
                         fallback=["core_anti_extension"]),
    "carry": _pt("core", "core", "compound", "переноска", "carry", [],
                 note=("В каталоге пока нет переноски (farmer's walk) — паттерн зарезервирован и в шаблонах не используется.",
                       "The catalog has no carry (farmer's walk) yet — pattern reserved, not used in templates.")),
    # Кардио: сначала низкоударное — бег мешает силовым сильнее велосипеда (wilson2012_concurrent).
    "cardio_steady": _pt("cardio", "cardio", "cardio", "кардио ровное", "steady cardio",
                         ["stationary_bike", "elliptical", "treadmill_walk", "rowing_machine", "brisk_walk",
                          "shadow_boxing", "jogging_outdoor", "treadmill_run"]),
    "conditioning": _pt("cardio", "cardio", "cardio", "интервалы / круг", "intervals / circuit",
                        ["rowing_machine", "stationary_bike", "burpee", "jump_rope", "mountain_climber", "high_knees",
                         "jumping_jack", "skater_jump", "shadow_boxing", "bear_crawl"],
                        fallback=["cardio_steady"]),
}

PUSH_PATTERNS = frozenset({"horizontal_push", "incline_push", "vertical_push", "chest_fly"})
PULL_PATTERNS = frozenset({"horizontal_pull", "vertical_pull", "rear_delt"})
CARDIO_PATTERNS = frozenset({"cardio_steady", "conditioning"})

# Обратный индекс slug → паттерн (первое вхождение). Для упражнений, которых нет
# в паттернах (или созданных ИИ), audit_week угадывает паттерн по мышце/категории.
SLUG_PATTERN: dict[str, str] = {}
for _pname, _prow in PATTERNS.items():
    for _slug in _prow["options"]:
        SLUG_PATTERN.setdefault(_slug, _pname)
del _pname, _prow, _slug
# Уточнения: мосты — ягодичный мост (hip_thrust); «алмаз» и обратные отжимания — разгибание.
SLUG_PATTERN.update({"glute_bridge": "hip_thrust", "single_leg_glute_bridge": "hip_thrust",
                     "sumo_deadlift_db": "hinge", "straight_arm_pulldown": "vertical_pull",
                     "db_front_raise": "vertical_push", "arnold_press": "vertical_push",
                     "thruster_db": "conditioning", "kb_clean_press": "conditioning", "jump_squat": "conditioning",
                     "jump_lunge": "conditioning", "superman": "core_anti_extension", "treadmill_run": "cardio_steady",
                     "stair_climber": "cardio_steady", "jogging_outdoor": "cardio_steady"})

# Лёгкая специфическая часть разминки по зоне дня (после warmup_general_5min).
WARMUP_DRILLS = {
    "lower": ["leg_swings", "hip_circles", "knee_hug_walk", "ankle_circles"],
    "upper": ["arm_circles", "band_pull_apart", "shoulder_rolls", "thoracic_rotation"],
    "core": ["cat_cow", "torso_twists", "hip_circles"],
    "cardio": ["leg_swings", "ankle_circles", "arm_circles"],
}


# --------------------------------------------------------------------------- #
#  8. Безопасность по ограничениям анкеты
#  Противопоказания каталога (contraindications) исключаются всегда — так же
#  делает trainer_logic.filter_catalog. Здесь — ДОПОЛНИТЕЛЬНО к ним:
#    exclude_slugs     — не назначать (в каталоге не помечены, но источники советуют избегать);
#    avoid_patterns    — паттерны, которые не ставим совсем;
#    pattern_fallback  — чем заменить паттерн, если под ограничение ничего не нашлось;
#    prefer            — slug, которые поднимаем в начало паттерна;
#    overrides         — пределы интенсивности (min_reps, max_rpe, min_rest_sec, max_hold_sec, no_heavy_day, cardio_kind);
#    requires_doctor   — программа только после согласования с врачом.
#  Замены упражнений при боли — уровень практиков (confidence low/medium).
# --------------------------------------------------------------------------- #
SAFETY_GENERAL: dict = {
    "pain_model": {
        "ru": "Контроль боли (модель Silbernagel, консервативно): 0–3 из 10 — продолжать; 4–5 — снизить вес или амплитуду; "
              "больше 5, острая или простреливающая боль, либо утром на следующий день хуже — убрать упражнение и заменить. "
              "Боль не должна нарастать от недели к неделе. Умеренная боль при упражнениях не ухудшает исходы.",
        "en": "Pain monitoring (Silbernagel model, conservative): 0–3 out of 10 — continue; 4–5 — reduce load or range; above 5, "
              "sharp or shooting pain, or worse the next morning — remove the exercise and substitute. Pain must not build week "
              "to week. Mild pain during exercise does not worsen outcomes.",
        "thresholds": {"continue_max": 3, "modify_max": 5},
        "sources": ["silbernagel2007_pain", "smith2017_painful_ex"],
    },
    "red_flags": [
        {"ru": "Боль или давление в груди (в том числе в шею, челюсть, руку), одышка не по нагрузке, обморок или предобморок, "
               "перебои сердца, тошнота с холодным потом — немедленно остановиться; не проходит за несколько минут — скорая.",
         "en": "Chest pain or pressure (including to the neck, jaw, arm), breathlessness out of proportion, fainting or near-fainting, "
               "palpitations, nausea with cold sweat — stop immediately; not settling within minutes — call an ambulance.",
         "sources": ["pelliccia2020_esc", "riebe2015_screening"]},
        {"ru": "Спутанность, странное поведение, падение, сильная головная боль в жару — признаки теплового удара: прекратить, охлаждать, скорая.",
         "en": "Confusion, odd behaviour, collapse, severe headache in the heat — signs of heat stroke: stop, cool down, ambulance.",
         "sources": ["roberts2023_heat"]},
        {"ru": "Моча цвета чая или колы, боль в мышцах намного сильнее ожидаемой, слабость через часы–дни после тренировки — к врачу (рабдомиолиз).",
         "en": "Tea- or cola-coloured urine, muscle pain far beyond expected, weakness hours to days after training — see a doctor (rhabdomyolysis).",
         "sources": ["cdc_rhabdo"]},
        {"ru": "Щелчок с болью и быстрым отёком, невозможно опереться на ногу, сустав заклинивает, онемение или слабость в руке или ноге — прекратить и к врачу.",
         "en": "A pop with pain and fast swelling, unable to bear weight, joint locking, numbness or weakness in an arm or leg — stop and see a doctor.",
         "sources": ["stiell1997_ottawa", "finucane2020_redflags"]},
    ],
    "beginner": {
        "ru": "Первые тренировки без «ударного» объёма до отказа (риск рабдомиолиза); первые 4–8 недель 2–4 повтора в запасе.",
        "en": "No shock-volume sessions to failure at the start (rhabdomyolysis risk); 2–4 reps in reserve for the first 4–8 weeks.",
        "sources": ["cdc_rhabdo", "robinson2024_rir"],
    },
    "older_adults": {
        "ru": "65+: баланс и сила ≥3 дней в неделю (комбинация снижает падения на 34%), силовые 2–3 дня без отказа, тренажёры и резинки на старте.",
        "en": "65+: balance and strength ≥3 days a week (combined training cuts falls by 34%), strength 2–3 days without failure, machines and bands to start.",
        "sources": ["who2020", "sherrington2019_falls", "fragala2019_nsca_older"],
    },
    "screening": {
        "ru": "Симптомы со стороны сердца, давление ≥180/110, диагноз болезни сердца, почек или диабет у неактивного человека — сначала разрешение врача.",
        "en": "Heart symptoms, blood pressure ≥180/110, a diagnosed heart or kidney disease or diabetes in an inactive person — get medical clearance first.",
        "sources": ["riebe2015_screening", "ghadieh2015_htn"],
    },
}

SAFETY: dict[str, dict] = {
    "knee": {
        "name": ("колени", "knees"),
        "exclude_slugs": [],
        "avoid_patterns": [],
        # Сначала сам паттерн: в каталоге есть варианты с неполной амплитудой, и только если
        # их нет под оборудование профиля — замена на ягодичный мост и наклон.
        "pattern_fallback": {"squat": ["hip_thrust", "hinge"], "lunge": ["squat", "hip_thrust", "glute_abduction"],
                             "knee_extension": ["glute_abduction"], "conditioning": ["cardio_steady"]},
        "prefer": {"cardio_steady": ["stationary_bike", "elliptical", "brisk_walk"]},
        "overrides": {},
        "requires_doctor": False,
        "rules": [
            ("Квадрицепс не выключаем: полные приседания и выпады заменяем на присед на высокую опору, жим ногами "
             "в неполной амплитуде и высокий «стульчик»; глубину наращиваем по ощущениям.",
             "Quads are not switched off: full squats and lunges are replaced with a high box squat, partial-range leg "
             "press and a high wall sit; depth increases as pain allows."),
            ("Контроль боли: во время упражнения до 3 из 10 и к утру не хуже, чем было, — можно продолжать; "
             "сильнее — уменьшить амплитуду или вес.",
             "Pain monitoring: up to 3 out of 10 during the exercise and no worse by next morning is acceptable; "
             "more than that — reduce range or load."),
            ("После согласования со специалистом безопасные варианты: жим ногами и присед на ящик в диапазоне 0–45°, "
             "разгибание в тренажёре 90–45°, глубину наращивать по боли.",
             "With specialist approval: leg press and box squat within 0–45°, leg extension 90–45°, increase depth guided by pain."),
            ("Добавлять отводящие и ягодичные — основа лечения передней боли в колене; бандажи не помогают.",
             "Add hip abductor and glute work — the core of anterior knee pain care; braces do not help."),
        ],
        "red_flags": [
            ("После травмы: возраст 55+, болезненность головки малоберцовой кости или надколенника, не сгибается до 90°, "
             "не получается сделать 4 шага — к врачу (Оттавские правила).",
             "After an injury: age 55+, tenderness at the fibular head or kneecap, cannot bend to 90°, cannot take 4 steps — "
             "see a doctor (Ottawa rules)."),
            ("Быстрый отёк, щелчок в момент травмы, заклинивание или подкашивание колена.",
             "Rapid swelling, a pop at injury, locking or giving way."),
        ],
        "sources": ["silbernagel2007_pain", "powers2014_pfj", "willy2019_pfp", "oarsi2019", "stiell1997_ottawa"],
    },
    "lower_back": {
        "name": ("поясница", "lower back"),
        "exclude_slugs": [],
        "avoid_patterns": [],
        "pattern_fallback": {"hinge": ["hip_thrust", "knee_flexion"], "core_flexion": ["core_rotation", "core_anti_extension"]},
        "prefer": {"core_anti_extension": ["bird_dog", "dead_bug"], "core_rotation": ["side_plank", "bird_dog", "pallof_press"],
                   "horizontal_pull": ["seated_cable_row", "machine_row", "db_row"], "squat": ["leg_press", "goblet_squat"]},
        "overrides": {},
        "requires_doctor": False,
        "rules": [
            ("При боли временно без осевой нагрузки и скручиваний с весом: без приседа со штангой, становой с пола, "
             "«доброго утра», тяги штанги в наклоне; замены — жим ногами, гоблет-присед, ягодичный мост, тяги с опорой.",
             "While painful avoid axial loading and loaded twisting: no back squat, floor deadlift, good morning or bent-over "
             "barbell row; use leg press, goblet squat, hip thrust, supported rows."),
            ("Кор — «большая тройка» McGill: скручивание McGill, боковая планка, «птица-собака», удержание 8–10 с, пирамида 8-6-4.",
             "Core — McGill's big three: curl-up, side plank, bird dog, 8–10 s holds, 8-6-4 pyramid."),
            ("Полный покой не нужен: упражнения снижают хроническую боль (−15 из 100). Нет улучшения 4 недели — к врачу.",
             "Rest is not required: exercise reduces chronic pain (−15/100). No improvement in 4 weeks — see a doctor."),
        ],
        "red_flags": [
            ("Онемение в промежности, нарушение мочеиспускания или стула, нарастающая слабость в ногах — срочно к врачу.",
             "Saddle numbness, bladder or bowel changes, progressive leg weakness — urgent medical care."),
            ("Температура с болью в спине, необъяснимое похудение, рак в анамнезе, боль после падения, постоянная ночная боль.",
             "Fever with back pain, unexplained weight loss, history of cancer, pain after a fall, constant night pain."),
        ],
        "sources": ["hayden2021_lbp", "oliveira2018_lbp", "mcgill1998_back", "swinton2011_trapbar", "finucane2020_redflags",
                    "giangregorio2014_osteo"],
    },
    "shoulder": {
        "name": ("плечи", "shoulders"),
        "exclude_slugs": ["band_dislocates"],
        "avoid_patterns": [],
        "pattern_fallback": {"vertical_push": ["rear_delt"], "lateral_delt": ["rear_delt"], "incline_push": ["horizontal_push"],
                             "chest_fly": ["rear_delt"]},
        "prefer": {"horizontal_push": ["machine_chest_press", "pushup_incline", "pushup"], "rear_delt": ["face_pull", "band_face_pull"],
                   "vertical_pull": ["lat_pulldown", "band_pulldown"]},
        "overrides": {},
        "requires_doctor": False,
        "rules": [
            ("Махи и тяги к подбородку — только до уровня плеч (90°); жим и тяга из-за головы не назначаются.",
             "Raises and upright rows only up to shoulder height (90°); no behind-the-neck presses or pulldowns."),
            ("Нейтральный хват, неполная амплитуда внизу жимов; вместо жима над головой — тяги к лицу и работа наружных ротаторов.",
             "Neutral grip, partial bottom range on presses; replace overhead pressing with face pulls and external rotation work."),
        ],
        "red_flags": [
            ("Внезапная острая боль со щелчком, слабость при подъёме руки, онемение или покалывание в руке.",
             "Sudden sharp pain with a pop, weakness lifting the arm, numbness or tingling in the arm."),
        ],
        "sources": ["kolber2014_shoulder", "fees1998_upper", "silbernagel2007_pain"],
    },
    "wrist": {
        "name": ("запястья", "wrists"),
        "exclude_slugs": [],
        "avoid_patterns": [],
        "pattern_fallback": {"horizontal_push": ["incline_push"], "core_anti_extension": ["core_rotation", "core_flexion"],
                             "conditioning": ["cardio_steady"]},
        "prefer": {"core_anti_extension": ["dead_bug"], "elbow_flexion": ["db_hammer_curl", "db_curl"]},
        "overrides": {},
        "requires_doctor": False,
        "rules": [
            ("Запястье ровное, гантели за середину рукояти, в тягах и сгибаниях — нейтральный хват; отжимания — на упорах "
             "или гантелях, планка — на предплечьях.",
             "Keep the wrist straight, grip dumbbells mid-handle, neutral grip for rows and curls; push-ups on handles or "
             "dumbbells, planks on the forearms."),
        ],
        "red_flags": [
            ("Отёк или деформация после падения, онемение пальцев, невозможность сжать кисть.",
             "Swelling or deformity after a fall, numb fingers, inability to grip."),
        ],
        "sources": ["e3_wrist", "silbernagel2007_pain"],
    },
    "neck": {
        "name": ("шея", "neck"),
        "exclude_slugs": [],
        "avoid_patterns": [],
        "pattern_fallback": {"core_flexion": ["core_anti_extension", "core_rotation"]},
        "prefer": {"core_anti_extension": ["plank", "bird_dog"]},
        "overrides": {},
        "requires_doctor": False,
        "rules": [
            ("Без нагрузки на шею и рывков, голова нейтрально в жимах и тягах; укреплять верх спины, заднюю дельту и глубокие сгибатели шеи.",
             "No neck loading or jerks, head neutral in presses and rows; strengthen the upper back, rear delts and deep neck flexors."),
        ],
        "red_flags": [
            ("Боль в шее или голове вместе с головокружением, двоением, нарушением речи или глотания, внезапными падениями, "
             "онемением лица, слабостью одной половины тела, внезапной сильнейшей головной болью — остановиться и вызвать скорую.",
             "Neck or head pain with dizziness, double vision, slurred speech or swallowing trouble, drop attacks, facial "
             "numbness, one-sided weakness, sudden worst headache — stop and call an ambulance."),
            ("Слабость или неловкость в руках, нарушение походки — к врачу до продолжения тренировок.",
             "Weakness or clumsiness in the hands, gait disturbance — see a doctor before training further."),
        ],
        "sources": ["blanpied2017_neck", "rushton2023_cervical"],
    },
    "hip": {
        "name": ("тазобедренные суставы", "hips"),
        # Глубокое сгибание бедра с приведением и ротацией провоцирует боль при импинджменте:
        # становая с пола и сумо → румынская тяга, глубокие удержания убираем (e3_fai, уровень практиков).
        "exclude_slugs": ["bb_deadlift", "sumo_deadlift_db", "good_morning", "deep_squat_hold", "bb_front_squat"],
        "avoid_patterns": [],
        "pattern_fallback": {},
        "prefer": {"hinge": ["bb_rdl", "rdl_db"], "squat": ["leg_press", "goblet_squat"]},
        "overrides": {"pain_max": 2},
        "requires_doctor": False,
        "rules": [
            ("Присед только в безболезненной глубине (на ящик), без глубокого сгибания бедра с поворотом внутрь; румынская "
             "тяга вместо становой с пола; боль при упражнении не выше 2/10.",
             "Squat only to a pain-free depth (to a box), avoid deep hip flexion with inward rotation; Romanian deadlift "
             "instead of floor pulls; pain during exercise at most 2/10."),
            ("Укреплять мышцы бедра и баланс на одной ноге 2–3 раза в неделю не меньше 3 месяцев; при артрозе упражнения — основа лечения.",
             "Strengthen hip muscles and single-leg balance 2–3×/week for at least 3 months; with osteoarthritis exercise is first-line care."),
        ],
        "red_flags": [
            ("Боль в паху после падения (особенно при остеопорозе), невозможность опереться на ногу, ночная боль, температура.",
             "Groin pain after a fall (especially with osteoporosis), inability to bear weight, night pain, fever."),
        ],
        "sources": ["e3_fai", "oarsi2019", "silbernagel2007_pain"],
    },
    "pregnancy": {
        "name": ("беременность / после родов", "pregnancy / postpartum"),
        # Без положения лёжа на спине после 1 триместра, без максимальных весов и натуживания,
        # без интенсивного пресса, прыжков и упражнений с риском падения (acog804_2020, rudin2021_pregnancy).
        # leg_curl: в каталоге не сказано, сидя он или лёжа, а в большинстве залов это тренажёр лёжа на
        # животе — со второго триместра так лежать нельзя; сгибание бедра даёт румынская тяга.
        "exclude_slugs": ["bb_back_squat", "bb_front_squat", "bb_overhead_press", "hack_squat", "leg_press",
                          "pushup_decline", "hanging_knee_raise", "cable_crunch", "plank_shoulder_tap", "thruster_db",
                          "kb_clean_press", "bear_crawl", "treadmill_run", "jogging_outdoor", "incline_db_curl",
                          "leg_curl"],
        "avoid_patterns": ["conditioning", "core_flexion"],
        "pattern_fallback": {"hip_thrust": ["glute_abduction"], "hinge": ["glute_abduction"],
                             "horizontal_push": ["incline_push"], "core_flexion": ["core_rotation"],
                             "conditioning": ["cardio_steady"], "squat": ["lunge"]},
        "prefer": {"incline_push": ["machine_chest_press", "db_incline_bench_press"],
                   "core_rotation": ["pallof_press", "bird_dog", "side_plank"],
                   "cardio_steady": ["stationary_bike", "elliptical", "treadmill_walk", "brisk_walk"]},
        "overrides": {"min_reps": 10, "max_rpe": 7, "min_rest_sec": 90, "max_hold_sec": 30, "no_heavy_day": True,
                      "cardio_kind": "steady"},
        "requires_doctor": True,
        "rules": [
            ("Программа только после разрешения врача. ≥150 мин умеренной активности в неделю минимум на 3 дня; интенсивность "
             "по разговорному тесту (Борг 12–14 из 20).",
             "Only with a doctor's clearance. ≥150 min of moderate activity a week over at least 3 days; intensity by the talk "
             "test (Borg 12–14 of 20)."),
            ("После 1 триместра без упражнений лёжа на спине; без задержки дыхания и максимальных весов (≥3 повтора в запасе); "
             "без прыжков, контакта и риска падения; избегать перегрева.",
             "After the 1st trimester no lying on the back; no breath holding or maximal loads (≥3 reps in reserve); no "
             "jumping, contact or fall risk; avoid overheating."),
            ("Тазовое дно — ежедневно. После родов бег и прыжки не раньше 12 недель и после теста нагрузки без боли, тяжести и подтекания.",
             "Pelvic floor work daily. Postpartum, no running or jumping before 12 weeks and a pain-, heaviness- and leak-free load test."),
        ],
        "red_flags": [
            ("Кровотечение, регулярные болезненные схватки, подтекание вод, одышка до нагрузки, головокружение, головная боль, "
             "боль в груди, слабость с потерей равновесия, боль или отёк икры, уменьшение шевелений — прекратить и связаться с врачом.",
             "Bleeding, regular painful contractions, fluid leakage, breathlessness before exertion, dizziness, headache, chest "
             "pain, weakness affecting balance, calf pain or swelling, reduced fetal movement — stop and contact a doctor."),
        ],
        "sources": ["acog804_2020", "rudin2021_pregnancy", "mottola2019_canada", "goom2019_postnatal"],
    },
    "heart_bp": {
        "name": ("давление / сердце", "blood pressure / heart"),
        # Тяжёлые упражнения с натуживанием и интервалы — только после допуска (pelliccia2020_esc, acsm_hypertension).
        "exclude_slugs": ["bb_front_squat", "bb_overhead_press", "good_morning", "kb_clean_press", "hack_squat",
                          "mountain_climber", "bear_crawl"],
        "avoid_patterns": ["conditioning"],
        "pattern_fallback": {"conditioning": ["cardio_steady"]},
        "prefer": {"cardio_steady": ["stationary_bike", "treadmill_walk", "elliptical", "brisk_walk"]},
        "overrides": {"min_reps": 8, "max_rpe": 7, "min_rest_sec": 90, "max_hold_sec": 30, "no_heavy_day": True,
                      "cardio_kind": "steady"},
        "requires_doctor": True,
        "rules": [
            ("Согласовать нагрузку с врачом. При давлении ≥180/110 или симптомах тренировки не начинать; систолическое >160 — без высокой интенсивности.",
             "Clear the plan with a doctor. With blood pressure ≥180/110 or symptoms do not train; systolic >160 — no high intensity."),
            ("Силовые 2–3 дня: 2–4 подхода по 8–12 повторов (60–80% 1ПМ), ≥2–3 повтора в запасе, без отказа; выдох на усилии, "
             "без задержки дыхания. Аэробные 30–60 мин 5–7 дней умеренно, заминка 3–5 мин.",
             "Strength 2–3 days: 2–4 sets of 8–12 reps (60–80% 1RM), ≥2–3 reps in reserve, no failure; exhale on effort, no "
             "breath holding. Aerobic 30–60 min on 5–7 days at moderate effort, 3–5 min cool-down."),
            ("Изометрия (планка, «стульчик») снижает давление, но кратко его поднимает — только при контролируемом давлении и допуске, до 30 с, дыхание ровное.",
             "Isometrics (plank, wall sit) lower blood pressure but raise it briefly — only with controlled BP and clearance, up to 30 s, steady breathing."),
        ],
        "red_flags": [
            ("Боль или давление в груди, одышка не по нагрузке, головокружение, обморок, перебои, тошнота с холодным потом — "
             "немедленно остановиться; не проходит за несколько минут — скорая.",
             "Chest pain or pressure, disproportionate breathlessness, dizziness, fainting, palpitations, nausea with cold "
             "sweat — stop at once; not settling within minutes — call an ambulance."),
        ],
        "sources": ["pelliccia2020_esc", "paluch2023_aha", "ghadieh2015_htn", "acsm_hypertension", "edwards2023_bp",
                    "riebe2015_screening"],
    },
}


# --------------------------------------------------------------------------- #
#  9. Бюджет времени тренировки
#  Расчёт, а не данные исследования (evidence=practitioner): подход 8–12
#  повторов в темпе ACSM ≈ 30–50 с, отдых по цели, переход ≈1 мин, разминочные
#  подходы перед тяжёлым базовым ≈2 мин. Ориентиры числа упражнений и подходов
#  сверены с расчётами исследователей (30 мин → 3–4 упр. / 9–12 подходов … 90 мин → 8–9 / 24–30).
# --------------------------------------------------------------------------- #
TIME_MODEL = {
    "sec_per_rep": 3.5,          # 1–2 с вверх + 1–2 с вниз (acsm2009_progression)
    "work_sec_min": 25,
    "work_sec_max": 60,
    "transition_sec": 60,        # переход и настройка снаряда
    "warmup_sets_heavy_sec": 120,  # 2–3 разминочных подхода перед ≤8 повторами (ribeiro2020_warmup)
    "warmup_sets_moderate_sec": 60,  # 0–1 подход при 8–12 повторах (enes2025_warmup)
    "tolerance": 1.05,           # допуск 5% при подгонке под длительность
    "sources": ["acsm2009_progression", "iversen2021_time", "singer2024_rest", "ribeiro2020_warmup", "enes2025_warmup"],
}

SESSION_BUDGET: dict[int, dict] = {
    20: {"warmup_sec": 180, "cooldown_sec": 120, "max_exercises": 3, "sets": (6, 9)},
    30: {"warmup_sec": 240, "cooldown_sec": 180, "max_exercises": 4, "sets": (9, 12)},
    45: {"warmup_sec": 300, "cooldown_sec": 240, "max_exercises": 5, "sets": (12, 16)},
    60: {"warmup_sec": 300, "cooldown_sec": 300, "max_exercises": 7, "sets": (15, 20)},
    75: {"warmup_sec": 300, "cooldown_sec": 300, "max_exercises": 8, "sets": (20, 24)},
    90: {"warmup_sec": 300, "cooldown_sec": 300, "max_exercises": 9, "sets": (24, 30)},
}
# Что сокращать первым, когда не укладываемся (iversen2021_time): базовые многосуставные не трогаем.
TRIM_ORDER = [
    ("общую разминку и растяжку укоротить до 2–4 минут", "shorten the general warm-up and stretching to 2–4 minutes"),
    ("убрать изолирующие упражнения с наименьшим приоритетом", "drop the lowest-priority isolation exercises"),
    ("вспомогательным оставить по 2 подхода", "cut accessories to 2 sets"),
    ("объединить вспомогательные в суперсеты антагонистов (≈ вдвое быстрее)", "pair accessories as antagonist supersets (≈ half the time)"),
    ("в крайнем случае — 2 подхода в базовых, но упражнение не убирать", "as a last resort 2 sets on compounds, but keep the exercise"),
]


# --------------------------------------------------------------------------- #
#  10. Известные программы, из которых взяты схемы и правила
# --------------------------------------------------------------------------- #
def _fp(pid, name, author, source, levels, days, who_ru, who_en, structure_ru, borrow_ru, borrow_en):
    return pid, {"id": pid, "name": name, "author": author, "source": source, "levels": list(levels), "days": days,
                 "who_for_ru": who_ru, "who_for_en": who_en, "structure_ru": structure_ru,
                 "borrow_ru": borrow_ru, "borrow_en": borrow_en}


FAMOUS_PROGRAMS: dict[str, dict] = dict([
    _fp("starting_strength", "Starting Strength", "Mark Rippetoe", "prog_starting_strength", ["beginner"], "3",
        "Абсолютный новичок со штангой, цель — сила", "Absolute barbell beginner, goal — strength",
        "A: присед 3×5, жим стоя 3×5, становая 1×5; B: присед 3×5, жим лёжа 3×5, становая/взятие. Вес растёт каждую тренировку.",
        "Правило «+вес каждую тренировку», сброс −10% после 3 провалов; мало объёма на руки и ягодицы — для цели «сила».",
        "\"Add load every session\", −10% reset after 3 misses; little arm/glute volume — for the strength goal."),
    _fp("stronglifts_5x5", "StrongLifts 5×5", "Mehdi Hadim", "prog_stronglifts", ["beginner"], "2–3",
        "Новичок в зале", "Gym beginner",
        "A: присед, жим лёжа, тяга в наклоне 5×5; B: присед, жим стоя 5×5, становая 1×5; отдых ~3 мин.",
        "Алгоритм провал → повтор веса → −10% → меньше подходов (5×5 → 3×5 → 1×5); вариант на 2 дня.",
        "Failure algorithm: miss → repeat → −10% → fewer sets (5×5 → 3×5 → 1×5); a 2-day variant."),
    _fp("r_fitness_bbr", "r/Fitness Basic Beginner Routine", "r/Fitness wiki (GSLP)", "prog_bbr", ["beginner"], "3",
        "Полный новичок, первые до 3 месяцев", "Complete beginner, first ~3 months",
        "A/B по очереди: тяга, жим, присед / подтягивания, жим стоя, становая, 3×5+ с последним подходом на максимум без отказа; кардио ≥2 раз.",
        "Шаблон «всё тело A/B» 3 раза, последний подход с остановкой по технике, фаза не дольше ~3 месяцев.",
        "Full-body A/B template 3×, last set stopped by technique, phase capped at ~3 months."),
    _fp("gzclp", "GZCLP", "Cody LeFever", "prog_gzclp", ["beginner", "intermediate"], "3–4",
        "Новичок → ранний средний уровень, сила с массой", "Beginner → early intermediate, strength plus size",
        "T1 тяжёлое базовое 5×3+ (отдых 3–5 мин), T2 объёмное базовое 3×10 (2–3 мин), T3 изоляция 3×15+ (1–1,5 мин); 4 тренировки по кругу.",
        "Роли слотов main/secondary/accessory с разными повторами и отдыхом; ступени при провале вместо простого сброса.",
        "Slot roles main/secondary/accessory with different reps and rest; failure stages instead of a plain reset."),
    _fp("wendler_531", "5/3/1 и 5/3/1 for Beginners", "Jim Wendler", "prog_531", ["beginner", "intermediate"], "3–4",
        "Поздний новичок и средний уровень, долгий рост силы", "Late beginner and intermediate, long-term strength",
        "Рабочий максимум 90% 1ПМ; недели 5/3/5-3-1 с последним подходом на максимум; FSL 5×5; подсобка 50–100 повторов на категорию; разгрузка каждая 4-я неделя.",
        "Консервативный рабочий максимум, 3-недельная волна + разгрузка, подсобка по категориям «толкай/тяни/нога-кор».",
        "Conservative training max, 3-week wave + deload, accessories by push/pull/single-leg-core categories."),
    _fp("wendler_bbb", "5/3/1 Boring But Big", "Jim Wendler", "prog_bbb", ["intermediate"], "4",
        "Средний уровень, сила и масса", "Intermediate, strength and size",
        "Основное движение по 5/3/1, затем 5×10 при 50–60% рабочего максимума, широчайшие 5×10, пресс.",
        "Связка «тяжёлое основное + объёмное дополнительное»; ограничение утомления ног.",
        "\"Heavy main + volume supplemental\" pairing; cap on leg fatigue."),
    _fp("phul", "PHUL", "Brandon Campbell", "prog_phul", ["intermediate"], "4",
        "Средний уровень, сила и масса", "Intermediate, strength and size",
        "Верх-сила, низ-сила (3–5 повт. в базовых), верх-гипертрофия, низ-гипертрофия (8–15).",
        "Шаблон «верх/низ» 4 дня с тяжёлым и объёмным днём на каждую половину тела.",
        "4-day upper/lower template with a heavy and a volume day for each half."),
    _fp("phat", "PHAT", "Layne Norton", "prog_phat", ["intermediate", "advanced"], "5",
        "Средний и продвинутый уровень, привычные к объёму", "Intermediate/advanced lifters used to volume",
        "Силовые верх и низ (3–5 повт.), затем объёмные спина+плечи, ноги, грудь+руки (8–20); скоростные 6×3 при 65–70%.",
        "Недельная волна силовой/объёмный день на те же мышцы; первые 3–6 недель без отказа.",
        "Weekly power/volume wave for the same muscles; no failure in the first 3–6 weeks."),
    _fp("reddit_ppl", "Reddit PPL (Metallicadpa)", "u/Metallicadpa", "prog_reddit_ppl", ["beginner", "intermediate"], "6",
        "Новичок, готовый к 6 дням и желающий больше рук и плеч", "Beginner ready for 6 days wanting more arms/shoulders",
        "Тяни/толкай/ноги ×2: основное 5×5+ с последним на максимум, подсобка 3×8–12 и 15–20, суперсеты трицепс + махи.",
        "Сплит PPL×2, двойная прогрессия подсобки (3×12 → +вес), правило 3 провалов → −10%.",
        "PPL×2 split, double progression on accessories (3×12 → +load), 3 misses → −10%."),
    _fp("nippard_fundamentals", "Fundamentals Hypertrophy Program", "Jeff Nippard", "prog_nippard_fundamentals",
        ["beginner", "intermediate"], "3–5",
        "Первые 1–2 года, мужчины и женщины", "First 1–2 years, men and women",
        "Фулбади 3 / верх-низ 4 / сплит 5 дней: база при RPE 7, изоляция RPE 8–9, 6–8 упражнений.",
        "Шаблон по умолчанию для массы и тонуса: база + изоляция с первого дня, RPE по роли упражнения.",
        "Default template for size and tone: compounds + isolation from day one, RPE by exercise role."),
    _fp("nippard_minmax", "Min-Max / PPL", "Jeff Nippard", "prog_nippard_minmax", ["intermediate", "advanced"], "4–6",
        "Мало времени или сушка (Min-Max); опыт 2–5 лет (PPL)", "Short on time or cutting (Min-Max); 2–5 years (PPL)",
        "Min-Max: 1–2 тяжёлых подхода на упражнение близко к отказу, ~45 мин; PPL: 6 дней, RPE + %1ПМ.",
        "Режим нехватки времени: меньше подходов, но ближе к отказу в изоляции.",
        "Time-crunch mode: fewer sets taken closer to failure on isolation."),
    _fp("rp_mesocycle", "RP-мезоцикл гипертрофии", "Mike Israetel / RP", "rp_landmarks", ["intermediate", "advanced"], "3–6",
        "Средний и продвинутый уровень, масса", "Intermediate and advanced, hypertrophy",
        "Старт с MEV при 3–4 RIR, +1–3 подхода в неделю по восстановлению до MRV, RIR к 0–1, затем разгрузка; повторы 25% в 5–10, 50% в 10–20, 25% в 20–30.",
        "Ориентиры объёма по мышцам и схема RIR 3 → 1 по фазам мезоцикла.",
        "Per-muscle volume landmarks and the RIR 3 → 1 progression across mesocycle phases."),
    _fp("helms_pyramid", "Novice/Intermediate Bodybuilding", "Eric Helms", "prog_helms", ["beginner", "intermediate"], "4–5",
        "Новичок (4 дня) и средний уровень 6–18 мес. (5 дней)", "Beginner (4 days) and 6–18 month intermediate (5 days)",
        "Верх/низ ×2 или низ/верх/низ/толкай/тяни; RPE 8; волна 3 недели (+вес, −1 повтор); изоляция до 3×15; разгрузка −1/3 объёма каждую 4-ю неделю.",
        "RPE-ограничитель прогрессии, пары антагонистов для экономии времени, разгрузка через объём.",
        "RPE-capped progression, antagonist pairs to save time, deloads via volume."),
    _fp("nuckols_beginner", "Beginner Program / SBS", "Greg Nuckols", "prog_nuckols", ["beginner"], "3",
        "Новичок с залом", "Beginner with a gym",
        "Три фулбади; базовые — пирамида 3–5 / 5–8 / 8–10, изоляция 8–10 / 10–12 / 12–15; превысил верх диапазона — +вес.",
        "Двойная прогрессия в каждом диапазоне отдельно; тяжёлый, средний и лёгкий диапазоны в одной тренировке.",
        "Double progression within each range separately; heavy, medium and light ranges in one session."),
    _fp("arnold_split", "Сплит Арнольда", "Arnold Schwarzenegger", "prog_arnold", ["advanced"], "6",
        "Соревнующийся профессионал с отличным восстановлением", "Competitive pro with exceptional recovery",
        "Грудь+спина, плечи+руки, ноги дважды в неделю, до 9 сессий и 15–20 ч в неделю.",
        "Только пары групп при ≤20 подходах на мышцу и одной сессии в день; по умолчанию не предлагаем.",
        "Only the muscle pairings with ≤20 sets per muscle and one session a day; never the default."),
    _fp("contreras_glutes", "Ягодичный акцент", "Bret Contreras", "prog_contreras", ["beginner", "intermediate", "advanced"], "3–5",
        "Цель «тонус/ягодицы», дом и зал", "Tone/glute goal, home or gym",
        "Сессия: хип-траст (3–6 или 8–12) → присед или выпад → наклон → отведение 15–30; хип-траст 2–3 раза, накачки до 4–6.",
        "4 слота на ягодичную сессию и частота по типу упражнения.",
        "4 slots per glute session and frequency by exercise type."),
    _fp("bwf_rr", "Recommended Routine", "r/bodyweightfitness", "prog_bwf_rr", ["beginner", "intermediate"], "3",
        "Новичок без инвентаря (турник, брусья)", "Beginner without equipment (bar, dip bars)",
        "Пары 3×5–8: подтягивания + присед, брусья + наклон, тяга + отжимания; кор 3×8–12; цепочки прогрессий.",
        "Цепочки усложнения (3×8 → следующая ступень с 3×5) и пары для экономии времени.",
        "Progression ladders (3×8 → next step at 3×5) and pairs to save time."),
])
# Гибридная неделя Viada описана в GOAL_PARAMS["endurance"] и шаблонах endurance_* (источник viada_hybrid).


# --------------------------------------------------------------------------- #
#  11. Шаблоны недели
#  Слот: pattern, role (main|secondary|accessory|cardio), sets, reps_key
#  (heavy|main|volume|accessory), priority (1 — не трогать при нехватке времени,
#  больше — сокращается раньше), variant (n-й подходящий вариант — чтобы в
#  разные дни были разные упражнения). Порядок слотов = порядок выполнения:
#  многосуставные первыми. День: intensity normal|heavy|volume — в тяжёлый день
#  ключ main превращается в heavy, в объёмный — в volume.
#  Тяга ≥ жим по подходам недели, ноги в каждой схеме.
# --------------------------------------------------------------------------- #
def _s(pattern, role, sets, reps_key=None, priority=None, variant=0):
    if reps_key is None:
        kind = PATTERNS[pattern]["kind"]
        reps_key = "accessory" if kind == "isolation" or role == "accessory" else "main"
    if priority is None:
        priority = {"main": 1, "secondary": 2, "accessory": 3, "cardio": 5}[role]
    return {"pattern": pattern, "role": role, "sets": sets, "reps_key": reps_key, "priority": priority, "variant": variant}


def _day(title_ru, title_en, slots, session_type="strength", intensity="normal"):
    return {"title_ru": title_ru, "title_en": title_en, "session_type": session_type, "intensity": intensity, "slots": list(slots)}


def _with_cardio(day, kind="cardio_steady"):
    """Копия силового дня с кардио-блоком в конце (кардио после силовой — eddens2018_sequence)."""
    slots = list(day["slots"]) + [_s(kind, "cardio", 1)]
    return dict(day, slots=slots, session_type="mixed",
                title_ru=day["title_ru"] + " + кардио", title_en=day["title_en"] + " + cardio")


# --- Всё тело (новичок): Nippard Fundamentals FB, Nuckols, r/Fitness ---
_FB2_A = _day("Всё тело A", "Full body A", [
    _s("squat", "main", 3), _s("horizontal_push", "main", 3), _s("vertical_pull", "secondary", 3),
    _s("hip_thrust", "secondary", 2, priority=3), _s("lateral_delt", "accessory", 2, priority=4),
    _s("core_anti_extension", "accessory", 2, priority=4)])
_FB2_B = _day("Всё тело B", "Full body B", [
    _s("hinge", "main", 3), _s("horizontal_pull", "main", 3), _s("incline_push", "secondary", 3),
    _s("lunge", "secondary", 2, priority=3), _s("elbow_flexion", "accessory", 2, priority=4),
    _s("elbow_extension", "accessory", 2, priority=4), _s("calf", "accessory", 2, priority=5)])

_FB3_A = _day("Всё тело A", "Full body A", [
    _s("squat", "main", 3), _s("horizontal_push", "main", 3), _s("horizontal_pull", "secondary", 3),
    _s("hinge", "secondary", 2, priority=3), _s("elbow_flexion", "accessory", 2, priority=4),
    _s("elbow_extension", "accessory", 2, priority=4)])
_FB3_B = _day("Всё тело B", "Full body B", [
    _s("hip_thrust", "main", 3), _s("vertical_push", "main", 3), _s("vertical_pull", "secondary", 3),
    _s("lunge", "secondary", 2, priority=3), _s("lateral_delt", "accessory", 2, priority=4),
    _s("core_anti_extension", "accessory", 2, priority=4)])
_FB3_C = _day("Всё тело C", "Full body C", [
    _s("squat", "main", 3, variant=1), _s("incline_push", "secondary", 3), _s("horizontal_pull", "secondary", 3, variant=1),
    _s("knee_flexion", "accessory", 2), _s("core_rotation", "accessory", 2, priority=4),
    _s("calf", "accessory", 2, priority=5)])

# --- Сила для новичка: Starting Strength, StrongLifts, GZCLP, 5/3/1 for Beginners ---
_FBS_A = _day("Сила A", "Strength A", [
    _s("squat", "main", 4), _s("horizontal_push", "main", 4), _s("horizontal_pull", "secondary", 4),
    _s("core_anti_extension", "accessory", 2, priority=4)])
_FBS_B = _day("Сила B", "Strength B", [
    _s("hinge", "main", 4), _s("vertical_push", "main", 4), _s("vertical_pull", "secondary", 4),
    _s("elbow_flexion", "accessory", 2, priority=4)])
_FBS_C = _day("Сила C", "Strength C", [
    _s("squat", "main", 4), _s("horizontal_push", "main", 4), _s("horizontal_pull", "secondary", 4, variant=1),
    _s("calf", "accessory", 2, priority=4), _s("core_rotation", "accessory", 2, priority=4)])

# --- Верх/низ: Helms Novice, Nippard U/L ---
_UL_UA = _day("Верх A", "Upper A", [
    _s("horizontal_push", "main", 3), _s("horizontal_pull", "main", 3), _s("vertical_push", "secondary", 3),
    _s("vertical_pull", "secondary", 3), _s("elbow_flexion", "accessory", 2), _s("elbow_extension", "accessory", 2)])
_UL_LA = _day("Низ A", "Lower A", [
    _s("squat", "main", 3), _s("hinge", "secondary", 3), _s("knee_flexion", "accessory", 2),
    _s("calf", "accessory", 2, priority=4), _s("core_anti_extension", "accessory", 2, priority=4)])
_UL_UB = _day("Верх B", "Upper B", [
    _s("incline_push", "main", 3), _s("vertical_pull", "main", 3, variant=1), _s("horizontal_pull", "secondary", 3, variant=1),
    _s("lateral_delt", "accessory", 3), _s("elbow_flexion", "accessory", 2, priority=4, variant=1)])
_UL_LB = _day("Низ B", "Lower B", [
    _s("hip_thrust", "main", 3), _s("lunge", "secondary", 3), _s("knee_extension", "accessory", 2),
    _s("glute_abduction", "accessory", 2, priority=4), _s("core_rotation", "accessory", 2, priority=4)])

# --- PHUL: тяжёлый и объёмный день на каждую половину тела ---
_PHUL_UP = _day("Верх — сила", "Upper — power", [
    _s("horizontal_push", "main", 4), _s("horizontal_pull", "main", 4), _s("vertical_push", "secondary", 3),
    _s("vertical_pull", "secondary", 3), _s("elbow_flexion", "accessory", 2), _s("elbow_extension", "accessory", 2)],
    intensity="heavy")
_PHUL_LP = _day("Низ — сила", "Lower — power", [
    _s("squat", "main", 4), _s("hinge", "main", 3), _s("squat", "secondary", 3, reps_key="volume", variant=1),
    _s("knee_flexion", "accessory", 3), _s("calf", "accessory", 3, priority=4)], intensity="heavy")
_PHUL_UH = _day("Верх — объём", "Upper — hypertrophy", [
    _s("incline_push", "main", 3), _s("horizontal_pull", "main", 3, variant=1), _s("vertical_pull", "secondary", 3, variant=1),
    _s("chest_fly", "accessory", 3), _s("lateral_delt", "accessory", 3),
    _s("elbow_flexion", "accessory", 2, priority=4, variant=1), _s("elbow_extension", "accessory", 2, priority=4, variant=1)],
    intensity="volume")
_PHUL_LH = _day("Низ — объём", "Lower — hypertrophy", [
    _s("lunge", "main", 3), _s("hip_thrust", "secondary", 3), _s("knee_extension", "accessory", 3),
    _s("knee_flexion", "accessory", 3, variant=1), _s("calf", "accessory", 3, priority=4, variant=1),
    _s("core_flexion", "accessory", 2, priority=4)], intensity="volume")

# --- PPL: Reddit PPL, Nippard PPL ---
_PPL_PULL_A = _day("Тяни A", "Pull A", [
    _s("vertical_pull", "main", 4), _s("horizontal_pull", "main", 3), _s("rear_delt", "accessory", 3),
    _s("elbow_flexion", "accessory", 3), _s("elbow_flexion", "accessory", 2, priority=4, variant=1)])
_PPL_PUSH_A = _day("Толкай A", "Push A", [
    _s("horizontal_push", "main", 4), _s("vertical_push", "secondary", 3), _s("incline_push", "secondary", 3, variant=1),
    _s("lateral_delt", "accessory", 3), _s("elbow_extension", "accessory", 3)])
_PPL_LEGS_A = _day("Ноги A", "Legs A", [
    _s("squat", "main", 4), _s("hinge", "secondary", 3), _s("squat", "secondary", 3, reps_key="volume", variant=1),
    _s("knee_flexion", "accessory", 3), _s("calf", "accessory", 3, priority=4)])
_PPL_PULL_B = _day("Тяни B", "Pull B", [
    _s("horizontal_pull", "main", 4, variant=1), _s("vertical_pull", "secondary", 3, variant=1),
    _s("rear_delt", "accessory", 3, variant=1), _s("elbow_flexion", "accessory", 3, variant=2),
    _s("core_flexion", "accessory", 2, priority=4)])
_PPL_PUSH_B = _day("Толкай B", "Push B", [
    _s("vertical_push", "main", 4), _s("horizontal_push", "secondary", 3, variant=1), _s("chest_fly", "accessory", 3),
    _s("lateral_delt", "accessory", 3, variant=1), _s("elbow_extension", "accessory", 3, variant=1)])
_PPL_LEGS_B = _day("Ноги B", "Legs B", [
    _s("hinge", "main", 3), _s("lunge", "secondary", 3), _s("hip_thrust", "secondary", 3),
    _s("knee_extension", "accessory", 3), _s("calf", "accessory", 3, priority=4, variant=1)])

# --- Верх / низ / всё тело (3 дня) ---
_ULF_U = _day("Верх", "Upper", [
    _s("horizontal_push", "main", 3), _s("horizontal_pull", "main", 3), _s("vertical_push", "secondary", 3),
    _s("vertical_pull", "secondary", 3), _s("lateral_delt", "accessory", 2), _s("elbow_flexion", "accessory", 2, priority=4)])
_ULF_L = _day("Низ", "Lower", [
    _s("squat", "main", 3), _s("hinge", "main", 3), _s("lunge", "secondary", 2),
    _s("knee_flexion", "accessory", 2), _s("calf", "accessory", 2, priority=4), _s("core_anti_extension", "accessory", 2, priority=4)])
_ULF_F = _day("Всё тело", "Full body", [
    _s("hip_thrust", "main", 3), _s("vertical_pull", "main", 3, variant=1), _s("incline_push", "secondary", 3),
    _s("squat", "secondary", 3, variant=1), _s("elbow_extension", "accessory", 2), _s("core_rotation", "accessory", 2, priority=4)])

# --- PHAT (продвинутый) ---
_PHAT_UP = _day("Верх — сила", "Upper — power", [
    _s("horizontal_pull", "main", 4), _s("vertical_pull", "secondary", 3, reps_key="volume"), _s("horizontal_push", "main", 4),
    _s("vertical_push", "secondary", 3, reps_key="volume"), _s("elbow_flexion", "accessory", 2),
    _s("elbow_extension", "accessory", 2)], intensity="heavy")
_PHAT_LP = _day("Низ — сила", "Lower — power", [
    _s("squat", "main", 4), _s("hinge", "secondary", 3), _s("squat", "secondary", 2, reps_key="volume", variant=1),
    _s("knee_extension", "accessory", 2), _s("knee_flexion", "accessory", 2), _s("calf", "accessory", 3, priority=4)],
    intensity="heavy")
_PHAT_BS = _day("Спина и плечи — объём", "Back & shoulders — hypertrophy", [
    _s("horizontal_pull", "main", 3, variant=1), _s("vertical_pull", "secondary", 3, variant=1),
    _s("horizontal_pull", "secondary", 3, variant=2), _s("vertical_push", "secondary", 3, variant=1),
    _s("lateral_delt", "accessory", 3), _s("rear_delt", "accessory", 3)], intensity="volume")
_PHAT_LH = _day("Ноги — объём", "Legs — hypertrophy", [
    _s("squat", "main", 3, variant=1), _s("hinge", "secondary", 3, variant=1), _s("knee_extension", "accessory", 3),
    _s("knee_flexion", "accessory", 3, variant=1), _s("calf", "accessory", 3, priority=4, variant=1)], intensity="volume")
_PHAT_CA = _day("Грудь и руки — объём", "Chest & arms — hypertrophy", [
    _s("incline_push", "main", 3), _s("horizontal_push", "secondary", 3, variant=1), _s("chest_fly", "accessory", 3),
    _s("elbow_flexion", "accessory", 3, variant=1), _s("elbow_extension", "accessory", 3, variant=1)], intensity="volume")

# --- Верх / низ / толкай / тяни / ноги (5 дней, Helms Intermediate, таблица «продвинутый 5 дней») ---
_U5_PUSH = _day("Толкай — объём", "Push — hypertrophy", [
    _s("incline_push", "main", 3), _s("vertical_push", "secondary", 3, variant=1), _s("chest_fly", "accessory", 3),
    _s("lateral_delt", "accessory", 3), _s("elbow_extension", "accessory", 3)], intensity="volume")
_U5_PULL = _day("Тяни — объём", "Pull — hypertrophy", [
    _s("vertical_pull", "main", 3, variant=1), _s("horizontal_pull", "main", 3, variant=1), _s("rear_delt", "accessory", 3),
    _s("elbow_flexion", "accessory", 3, variant=1)], intensity="volume")
_U5_LEGS = _day("Ноги — объём", "Legs — hypertrophy", [
    _s("lunge", "main", 3), _s("hip_thrust", "secondary", 3), _s("knee_extension", "accessory", 3),
    _s("knee_flexion", "accessory", 3, variant=1), _s("calf", "accessory", 2, priority=4, variant=1)], intensity="volume")
_U5_UPPER = _day("Верх — сила", "Upper — heavy", [
    _s("horizontal_push", "main", 4), _s("horizontal_pull", "main", 4), _s("vertical_push", "secondary", 3),
    _s("vertical_pull", "secondary", 3), _s("elbow_flexion", "accessory", 2, priority=4)], intensity="heavy")
_U5_LOWER = _day("Низ — сила", "Lower — heavy", [
    _s("squat", "main", 4), _s("hinge", "main", 3), _s("knee_flexion", "accessory", 3),
    _s("calf", "accessory", 3, priority=4), _s("core_anti_extension", "accessory", 2, priority=4)], intensity="heavy")

# --- Дом с гантелями и резинками ---
_HOME_A = _day("Дом: всё тело A", "Home: full body A", [
    _s("squat", "main", 3), _s("horizontal_push", "main", 3), _s("horizontal_pull", "main", 3),
    _s("hip_thrust", "secondary", 3), _s("lateral_delt", "accessory", 2), _s("core_anti_extension", "accessory", 2, priority=4)])
_HOME_B = _day("Дом: всё тело B", "Home: full body B", [
    _s("hinge", "main", 3), _s("vertical_push", "main", 3), _s("vertical_pull", "secondary", 3),
    _s("lunge", "secondary", 3), _s("elbow_flexion", "accessory", 2), _s("calf", "accessory", 2, priority=4)])
_HOME_C = _day("Дом: всё тело C", "Home: full body C", [
    _s("lunge", "main", 3, variant=1), _s("incline_push", "secondary", 3), _s("horizontal_pull", "secondary", 3, variant=1),
    _s("hip_thrust", "secondary", 2, variant=1), _s("elbow_extension", "accessory", 2), _s("core_rotation", "accessory", 2, priority=4)])

# --- Только вес тела: r/bodyweightfitness Recommended Routine ---
_BW_A = _day("Вес тела A", "Bodyweight A", [
    _s("vertical_pull", "main", 3), _s("squat", "main", 3), _s("horizontal_push", "main", 3),
    _s("horizontal_pull", "main", 3), _s("hinge", "secondary", 3), _s("core_anti_extension", "accessory", 3),
    _s("core_rotation", "accessory", 2, priority=4)])
_BW_B = _day("Вес тела B", "Bodyweight B", [
    _s("vertical_pull", "main", 3), _s("lunge", "main", 3), _s("incline_push", "main", 3, variant=1),
    _s("horizontal_pull", "main", 3), _s("hip_thrust", "secondary", 3), _s("core_flexion", "accessory", 2),
    _s("calf", "accessory", 2, priority=4)])
_BW_C = _day("Вес тела C", "Bodyweight C", [
    _s("vertical_pull", "main", 3), _s("squat", "main", 3, variant=1), _s("horizontal_push", "main", 3, variant=1),
    _s("horizontal_pull", "main", 3), _s("knee_flexion", "secondary", 3), _s("core_rotation", "accessory", 3),
    _s("core_anti_extension", "accessory", 2, priority=4, variant=1)])
_BW_PULL_LEGS = _day("Тяни + ноги", "Pull + legs", [
    _s("vertical_pull", "main", 3), _s("horizontal_pull", "main", 3), _s("squat", "main", 3),
    _s("lunge", "secondary", 3), _s("hinge", "secondary", 3), _s("calf", "accessory", 2, priority=4)])
_BW_PUSH_CORE = _day("Толкай + кор", "Push + core", [
    _s("horizontal_push", "main", 3), _s("vertical_push", "secondary", 3), _s("hip_thrust", "secondary", 3),
    _s("core_anti_extension", "accessory", 3), _s("core_rotation", "accessory", 2), _s("elbow_extension", "accessory", 2, priority=4)])

# --- Кардио-дни (похудение, выносливость, «не 6 силовых подряд») ---
_CARDIO_EASY = _day("Кардио лёгкое", "Easy cardio", [
    _s("cardio_steady", "cardio", 1, priority=1), _s("core_rotation", "accessory", 2, priority=4)], session_type="cardio")
_CARDIO_INTERVALS = _day("Интервалы", "Intervals", [
    _s("conditioning", "cardio", 4, priority=1), _s("cardio_steady", "cardio", 1, priority=3),
    _s("core_anti_extension", "accessory", 2, priority=4)], session_type="cardio")

# --- Похудение: силовые с прежними весами + кардио после ---
_LOSS_A = _day("Всё тело A + кардио", "Full body A + cardio", [
    _s("squat", "main", 3), _s("horizontal_push", "main", 3), _s("horizontal_pull", "secondary", 3),
    _s("hip_thrust", "accessory", 2, reps_key="accessory"), _s("core_anti_extension", "accessory", 2, priority=4),
    _s("cardio_steady", "cardio", 1)], session_type="mixed")
_LOSS_B = _day("Всё тело B + кардио", "Full body B + cardio", [
    _s("hinge", "main", 3), _s("vertical_pull", "main", 3), _s("incline_push", "secondary", 3),
    _s("lunge", "secondary", 2, priority=3), _s("lateral_delt", "accessory", 2, priority=4),
    _s("cardio_steady", "cardio", 1)], session_type="mixed")
_LOSS_C = _day("Всё тело C + кардио", "Full body C + cardio", [
    _s("squat", "main", 3, variant=1), _s("horizontal_pull", "main", 3, variant=1), _s("vertical_push", "secondary", 3),
    _s("knee_flexion", "accessory", 2), _s("core_rotation", "accessory", 2, priority=4),
    _s("cardio_steady", "cardio", 1)], session_type="mixed")

# --- Выносливость: силовые 2–3 раза без отказа + 80/20 кардио (Viada, Seiler, Rønnestad) ---
_END_A = _day("Сила + лёгкое кардио", "Strength + easy cardio", [
    _s("squat", "main", 3), _s("horizontal_pull", "main", 3), _s("horizontal_push", "secondary", 3),
    _s("calf", "accessory", 2), _s("core_anti_extension", "accessory", 2, priority=4),
    _s("cardio_steady", "cardio", 1)], session_type="mixed")
_END_B = _day("Сила + интервалы", "Strength + intervals", [
    _s("hinge", "main", 3), _s("vertical_pull", "main", 3), _s("vertical_push", "secondary", 3),
    _s("core_rotation", "accessory", 2, priority=4), _s("conditioning", "cardio", 3)], session_type="mixed")
_END_C = _day("Сила на одной ноге + кардио", "Single-leg strength + cardio", [
    _s("lunge", "main", 3), _s("horizontal_pull", "secondary", 3, variant=1), _s("incline_push", "secondary", 3),
    _s("hip_thrust", "secondary", 2, priority=3), _s("core_anti_extension", "accessory", 2, priority=4),
    _s("cardio_steady", "cardio", 1)], session_type="mixed")

# --- Ягодицы (Contreras): хип-траст + присед/выпад + наклон + отведение ---
_GL_A = _day("Ягодицы A + верх", "Glutes A + upper", [
    _s("hip_thrust", "main", 4), _s("squat", "main", 3), _s("horizontal_pull", "secondary", 3),
    _s("horizontal_push", "secondary", 3), _s("glute_abduction", "accessory", 3), _s("core_anti_extension", "accessory", 2, priority=4)])
_GL_B = _day("Ягодицы B + верх", "Glutes B + upper", [
    _s("hinge", "main", 3), _s("lunge", "secondary", 3), _s("vertical_pull", "secondary", 3),
    _s("vertical_push", "secondary", 2, priority=3), _s("glute_abduction", "accessory", 3, variant=1),
    _s("core_rotation", "accessory", 2, priority=4)])
_GL_C = _day("Ягодицы C + верх", "Glutes C + upper", [
    _s("hip_thrust", "main", 3, reps_key="volume", variant=1), _s("lunge", "secondary", 3, variant=1),
    _s("horizontal_pull", "secondary", 3, variant=1), _s("incline_push", "secondary", 2, priority=3),
    _s("knee_flexion", "accessory", 3), _s("glute_abduction", "accessory", 2, priority=4)])
_GL_LA = _day("Низ: ягодицы тяжело", "Lower: heavy glutes", [
    _s("hip_thrust", "main", 4), _s("squat", "secondary", 3), _s("knee_flexion", "accessory", 3),
    _s("glute_abduction", "accessory", 3), _s("core_anti_extension", "accessory", 2, priority=4)])
_GL_LB = _day("Низ: наклон и выпады", "Lower: hinge and lunges", [
    _s("hinge", "main", 3), _s("lunge", "secondary", 3), _s("hip_thrust", "secondary", 3, reps_key="volume", variant=1),
    _s("glute_abduction", "accessory", 3, variant=1), _s("calf", "accessory", 2, priority=4)])
_GL_PUMP = _day("Ягодицы: лёгкая накачка + кардио", "Glute pump + cardio", [
    _s("hip_thrust", "secondary", 3, reps_key="accessory", variant=2), _s("glute_abduction", "accessory", 3),
    _s("rear_delt", "accessory", 2), _s("core_rotation", "accessory", 2, priority=4), _s("cardio_steady", "cardio", 1)],
    session_type="mixed")

_ALL_GOALS = list(GOALS)
_ALL_LEVELS = list(LEVELS)


def _split(sid, name_ru, name_en, split_type, days, levels, goals, equipment=("any",), inspired_by=(), priority=1,
           tags=(), about_ru="", about_en=""):
    return {"id": sid, "name_ru": name_ru, "name_en": name_en, "split_type": split_type, "days": list(days),
            "days_per_week": len(days), "levels": list(levels), "goals": list(goals), "equipment": list(equipment),
            "inspired_by": list(inspired_by), "priority": priority, "tags": list(tags),
            "about_ru": about_ru, "about_en": about_en}


SPLITS: list[dict] = [
    # 2 дня
    _split("fb2", "Всё тело 2 дня", "Full body, 2 days", "full_body", [_FB2_A, _FB2_B], _ALL_LEVELS,
           ["muscle", "tone", "strength"], inspired_by=["stronglifts_5x5", "nippard_fundamentals"],
           about_ru="каждая мышца дважды в неделю — минимум ВОЗ и Schoenfeld", about_en="each muscle twice a week — the WHO/Schoenfeld minimum"),
    _split("loss_fb2", "Всё тело 2 дня + кардио", "Full body 2 days + cardio", "full_body",
           [_with_cardio(_FB2_A), _with_cardio(_FB2_B)], _ALL_LEVELS, ["loss"], inspired_by=["nippard_minmax"],
           about_ru="силовые сохраняют мышцы на дефиците, кардио после силовой", about_en="lifting keeps muscle in a deficit, cardio after lifting"),
    _split("endurance_2", "Сила + кардио 2 дня", "Strength + cardio, 2 days", "full_body", [_END_A, _END_B], _ALL_LEVELS,
           ["endurance"], inspired_by=[], about_ru="2 силовые без отказа поддерживают экономичность, 80/20 по кардио",
           about_en="2 strength sessions without failure support economy, 80/20 cardio"),
    _split("bw_fb2", "Вес тела 2 дня", "Bodyweight, 2 days", "full_body", [_BW_A, _BW_B], _ALL_LEVELS,
           ["muscle", "tone", "strength"], equipment=["bodyweight"], inspired_by=["bwf_rr"],
           about_ru="цепочки прогрессий: 3×8 — следующая ступень", about_en="progression ladders: 3×8 — next step"),
    # 3 дня
    _split("fb3", "Всё тело 3 дня", "Full body, 3 days", "full_body", [_FB3_A, _FB3_B, _FB3_C], ["beginner", "intermediate"],
           ["muscle", "tone"], inspired_by=["nippard_fundamentals", "nuckols_beginner", "r_fitness_bbr"],
           about_ru="каждая мышца 2–3 раза в неделю, база + изоляция", about_en="each muscle 2–3× a week, compounds + isolation"),
    _split("fb3_strength", "Сила: всё тело 3 дня", "Strength: full body, 3 days", "full_body", [_FBS_A, _FBS_B, _FBS_C],
           ["beginner", "intermediate"], ["strength"], inspired_by=["starting_strength", "stronglifts_5x5", "gzclp", "wendler_531"],
           tags=["heavy"], about_ru="основные движения 2–3 раза в неделю, вес растёт от тренировки к тренировке",
           about_en="main lifts 2–3× a week, load rises session to session"),
    _split("ulf3", "Верх / низ / всё тело", "Upper / lower / full body", "custom", [_ULF_U, _ULF_L, _ULF_F],
           ["intermediate", "advanced"], ["muscle", "tone", "strength"], inspired_by=["helms_pyramid", "rp_mesocycle"], priority=2,
           about_ru="больше объёма за тренировку при трёх днях", about_en="more volume per session on three days"),
    _split("ppl3", "Тяни / толкай / ноги 3 дня", "Push / pull / legs, 3 days", "ppl",
           [_PPL_PUSH_A, _PPL_PULL_A, _PPL_LEGS_A], ["intermediate", "advanced"], ["muscle", "strength"],
           inspired_by=["reddit_ppl"], priority=4,
           about_ru="каждая мышца раз в неделю — только по желанию, при равном объёме частота мало влияет",
           about_en="each muscle once a week — only on request; frequency matters little at equal volume"),
    _split("home_fb3", "Дом: всё тело 3 дня", "Home: full body, 3 days", "full_body", [_HOME_A, _HOME_B, _HOME_C], _ALL_LEVELS,
           ["muscle", "tone", "strength"], equipment=["home_dumbbells"], inspired_by=["nuckols_beginner", "contreras_glutes"],
           about_ru="гантели и резинки: повторы до близкого отказа, темп, работа одной рукой/ногой",
           about_en="dumbbells and bands: reps near failure, tempo, single-limb work"),
    _split("bw_fb3", "Вес тела 3 дня", "Bodyweight, 3 days", "full_body", [_BW_A, _BW_B, _BW_C], _ALL_LEVELS,
           ["muscle", "tone", "strength"], equipment=["bodyweight"], inspired_by=["bwf_rr"],
           about_ru="пары упражнений и цепочки прогрессий Recommended Routine", about_en="Recommended Routine pairs and progression ladders"),
    _split("loss_fb3", "Всё тело 3 дня + кардио", "Full body 3 days + cardio", "full_body", [_LOSS_A, _LOSS_B, _LOSS_C],
           _ALL_LEVELS, ["loss"], inspired_by=["nippard_fundamentals"],
           about_ru="силовые с прежними весами, кардио после силовой, 150–300 мин в неделю",
           about_en="lifting with the same loads, cardio after lifting, 150–300 min a week"),
    _split("endurance_3", "Сила + кардио 3 дня", "Strength + cardio, 3 days", "full_body", [_END_A, _END_B, _END_C],
           _ALL_LEVELS, ["endurance"], about_ru="силовые без отказа, в один день — сначала силовая", about_en="no-failure strength, lift first on shared days"),
    _split("glutes_3", "Ягодицы 3 дня", "Glutes, 3 days", "full_body", [_GL_A, _GL_B, _GL_C], _ALL_LEVELS,
           ["tone", "muscle", "loss"], inspired_by=["contreras_glutes"], tags=["glutes"],
           about_ru="хип-траст 2 раза, присед/выпад, наклон и отведение каждую неделю", about_en="hip thrust twice, squat/lunge, hinge and abduction weekly"),
    # 4 дня
    _split("ul4", "Верх / низ 4 дня", "Upper / lower, 4 days", "upper_lower", [_UL_UA, _UL_LA, _UL_UB, _UL_LB],
           ["beginner"], ["muscle", "tone", "strength"], inspired_by=["helms_pyramid", "nippard_fundamentals"],
           about_ru="каждая мышца дважды в неделю при коротких тренировках", about_en="each muscle twice a week with shorter sessions"),
    _split("ul4_phul", "Верх / низ 4 дня (PHUL)", "Upper / lower, 4 days (PHUL)", "upper_lower",
           [_PHUL_UP, _PHUL_LP, _PHUL_UH, _PHUL_LH], ["intermediate", "advanced"], ["muscle", "strength", "tone"],
           inspired_by=["phul", "wendler_bbb"], tags=["heavy"],
           about_ru="тяжёлый и объёмный день на каждую половину тела", about_en="a heavy and a volume day for each half of the body"),
    _split("loss_ul4", "Верх / низ 4 дня + кардио", "Upper / lower 4 days + cardio", "upper_lower",
           [_with_cardio(_UL_UA), _with_cardio(_UL_LA), _with_cardio(_UL_UB), _with_cardio(_UL_LB)], _ALL_LEVELS, ["loss"],
           inspired_by=["helms_pyramid"], about_ru="4 силовые с кардио после, веса не снижаем", about_en="4 lifting days with cardio after, loads kept"),
    _split("endurance_4", "Сила 2 дня + кардио 2 дня", "Strength 2 days + cardio 2 days", "custom",
           [_END_A, _CARDIO_EASY, _END_B, _CARDIO_EASY], _ALL_LEVELS, ["endurance"],
           about_ru="2 силовые и лёгкое кардио, интервалы один раз", about_en="2 strength days and easy cardio, intervals once"),
    _split("glutes_4", "Ягодицы: верх / низ 4 дня", "Glutes: upper / lower, 4 days", "upper_lower",
           [_GL_LA, _UL_UA, _GL_LB, _UL_UB], _ALL_LEVELS, ["tone", "muscle", "loss"], inspired_by=["contreras_glutes"],
           tags=["glutes"], about_ru="два дня низа с акцентом на ягодицы", about_en="two lower days emphasising the glutes"),
    _split("bw_ul4", "Вес тела: тяни+ноги / толкай+кор", "Bodyweight: pull+legs / push+core", "custom",
           [_BW_PULL_LEGS, _BW_PUSH_CORE, dict(_BW_PULL_LEGS, title_ru="Тяни + ноги B", title_en="Pull + legs B"),
            dict(_BW_PUSH_CORE, title_ru="Толкай + кор B", title_en="Push + core B")],
           _ALL_LEVELS, ["muscle", "tone", "strength"], equipment=["bodyweight"], inspired_by=["bwf_rr"],
           about_ru="Recommended Routine, разбитая на 2×2", about_en="Recommended Routine split 2×2"),
    # 5 дней
    _split("ul5_beginner", "Верх / низ ×2 + лёгкий день", "Upper / lower ×2 + light day", "upper_lower",
           [_UL_UA, _UL_LA, _UL_UB, _UL_LB, _GL_PUMP], ["beginner"], ["muscle", "tone", "strength"],
           inspired_by=["helms_pyramid"], about_ru="у новичка предел — восстановление, пятый день лёгкий",
           about_en="recovery is the beginner's limit, so day five is light"),
    _split("ulppl5", "Верх / низ / толкай / тяни / ноги", "Upper / lower / push / pull / legs", "custom",
           [_U5_UPPER, _U5_LOWER, _U5_PUSH, _U5_PULL, _U5_LEGS], ["intermediate", "advanced"], ["muscle", "tone", "strength"],
           inspired_by=["helms_pyramid", "phul"], priority=2, tags=["heavy"],
           about_ru="тяжёлые верх и низ + объёмные толкай/тяни/ноги", about_en="heavy upper and lower + volume push/pull/legs"),
    _split("phat5", "PHAT 5 дней", "PHAT, 5 days", "custom", [_PHAT_UP, _PHAT_LP, _PHAT_BS, _PHAT_LH, _PHAT_CA],
           ["advanced"], ["muscle", "strength"], inspired_by=["phat"], tags=["heavy"],
           about_ru="силовые дни + объёмные дни по группам, каждая мышца дважды", about_en="power days + volume days by muscle group, each muscle twice"),
    _split("loss_5", "Похудение: 3 силовые + 2 кардио", "Fat loss: 3 strength + 2 cardio", "full_body",
           [_LOSS_A, _CARDIO_EASY, _LOSS_B, _CARDIO_INTERVALS, _LOSS_C], _ALL_LEVELS, ["loss", "tone"],
           inspired_by=["nippard_fundamentals"], about_ru="силовые 3 раза, кардио до 150–300 мин, интервалы раз в неделю",
           about_en="lifting 3×, cardio up to 150–300 min, intervals once a week"),
    _split("endurance_5", "Сила 3 дня + кардио 2 дня", "Strength 3 days + cardio 2 days", "custom",
           [_END_A, _CARDIO_EASY, _END_B, _CARDIO_EASY, _END_C], _ALL_LEVELS, ["endurance"],
           about_ru="80% кардио лёгкое, силовые не накануне интервалов", about_en="80% easy cardio, no heavy legs before intervals"),
    _split("glutes_5", "Ягодицы 5 дней", "Glutes, 5 days", "upper_lower", [_GL_LA, _UL_UA, _GL_LB, _UL_UB, _GL_PUMP],
           _ALL_LEVELS, ["tone", "muscle", "loss"], inspired_by=["contreras_glutes"], tags=["glutes"],
           about_ru="лёгкие «накачки» ягодиц восстанавливаются быстро — можно чаще", about_en="light glute pump work recovers fast — can be frequent"),
    _split("bw5", "Вес тела 3 дня + кардио 2 дня", "Bodyweight 3 days + cardio 2 days", "full_body",
           [_BW_A, _CARDIO_EASY, _BW_B, _CARDIO_EASY, _BW_C], _ALL_LEVELS, ["muscle", "tone", "strength"],
           equipment=["bodyweight"], inspired_by=["bwf_rr"], about_ru="RR 3 раза + кардио, а не 5 силовых", about_en="RR 3× + cardio instead of 5 strength days"),
    # 6 дней
    _split("ppl6", "Тяни / толкай / ноги ×2", "Push / pull / legs ×2", "ppl",
           [_PPL_PULL_A, _PPL_PUSH_A, _PPL_LEGS_A, _PPL_PULL_B, _PPL_PUSH_B, _PPL_LEGS_B], _ALL_LEVELS, ["muscle", "strength"],
           inspired_by=["reddit_ppl", "nippard_minmax"], tags=["heavy"],
           about_ru="каждая мышца дважды в неделю; 6 дней требуют сна и восстановления", about_en="each muscle twice a week; 6 days demand sleep and recovery"),
    _split("loss_6", "Верх / низ ×2 + 2 кардио", "Upper / lower ×2 + 2 cardio", "upper_lower",
           [_UL_UA, _UL_LA, _CARDIO_EASY, _UL_UB, _UL_LB, _CARDIO_INTERVALS], _ALL_LEVELS, ["loss", "tone"],
           inspired_by=["helms_pyramid"], about_ru="4 силовые + 2 кардио вместо 6 силовых", about_en="4 strength + 2 cardio instead of 6 strength days"),
    _split("endurance_6", "Сила 3 дня + кардио 3 дня", "Strength 3 days + cardio 3 days", "custom",
           [_END_A, _CARDIO_EASY, _END_B, _CARDIO_EASY, _END_C, _CARDIO_INTERVALS], _ALL_LEVELS, ["endurance"],
           about_ru="интервалы не чаще 2 раз и не подряд", about_en="intervals at most twice and not back-to-back"),
    _split("bw6", "Вес тела 3 дня + кардио 3 дня", "Bodyweight 3 days + cardio 3 days", "full_body",
           [_BW_A, _CARDIO_EASY, _BW_B, _CARDIO_EASY, _BW_C, _CARDIO_EASY], _ALL_LEVELS, ["muscle", "tone", "strength"],
           equipment=["bodyweight"], inspired_by=["bwf_rr"], about_ru="RR 3 раза + шаги и кардио, а не 6 силовых", about_en="RR 3× + steps and cardio, not 6 strength days"),
]
SPLITS_BY_ID: dict[str, dict] = {s["id"]: s for s in SPLITS}


# Короткие правила безопасности для промпта ИИ (полные — в SAFETY[...]["rules"]).
SAFETY_BRIEF: dict[str, tuple[str, str]] = {
    "knee": ("колени: без приседов, выпадов и прыжков из каталога; ноги через ягодичный мост, наклоны, сгибания ног; отводящие мышцы обязательно",
             "knees: no catalog squats, lunges or jumps; train legs via hip thrusts, hinges, leg curls; include hip abductors"),
    "lower_back": ("поясница: без становой с пола, приседа со штангой, тяги в наклоне и скручиваний с весом; тяги с опорой, жим ногами, «большая тройка» McGill",
                   "lower back: no floor deadlifts, back squats, bent-over rows or loaded twists; supported rows, leg press, McGill big three"),
    "shoulder": ("плечи: без жимов над головой, махов выше плеч и брусьев; жим в тренажёре, тяги к лицу, нейтральный хват",
                 "shoulders: no overhead presses, raises above shoulder height or dips; machine press, face pulls, neutral grip"),
    "wrist": ("запястья: без опоры на кисти под нагрузкой; нейтральный хват, планка на предплечьях",
              "wrists: no loaded weight on the hands; neutral grip, forearm planks"),
    "neck": ("шея: без скручиваний с нагрузкой на шею и рывков; голова нейтрально, укреплять верх спины",
             "neck: no crunches that load the neck, no jerks; neutral head, strengthen the upper back"),
    "hip": ("тазобедренные: без становой с пола и сумо, присед в безболезненной глубине, боль ≤2/10",
            "hips: no floor or sumo deadlifts, squat to a pain-free depth, pain ≤2/10"),
    "pregnancy": ("беременность: ТОЛЬКО с разрешения врача; ≥10 повторов, ≥3 в запасе, без натуживания, без упражнений лёжа на спине после 1 триместра, без прыжков и интервалов",
                  "pregnancy: ONLY with a doctor's clearance; ≥10 reps, ≥3 in reserve, no straining, no lying supine after the 1st trimester, no jumps or intervals"),
    "heart_bp": ("давление/сердце: согласовать с врачом; 8–12 повторов, ≥3 в запасе, без отказа и задержки дыхания, кардио ровное, без интервалов",
                 "blood pressure/heart: clear with a doctor; 8–12 reps, ≥3 in reserve, no failure or breath holding, steady cardio, no intervals"),
}

# Ограничения, при которых программа только после врача (requires_doctor): врач + красные флаги
# одной строкой. Пользователь видит её в советах программы сразу после метода — даже если ИИ
# своих советов о враче не дал; поэтому длина ≤ 200 символов, как у советов после normalize_program.
# Полные списки — SAFETY[...]["red_flags"] (acog804_2020, pelliccia2020_esc).
DOCTOR_TIPS: dict[str, tuple[str, str]] = {
    "pregnancy": ("Тренируйся только с разрешения врача. Стоп и к врачу: кровотечение, схватки, подтекание вод, "
                  "головокружение, боль в груди, отёк икры, меньше шевелений.",
                  "Train only with your doctor's clearance. Stop and call a doctor: bleeding, contractions, fluid "
                  "leakage, dizziness, chest pain, calf swelling, less fetal movement."),
    "heart_bp": ("Нагрузку согласуй с врачом. Стоп сразу при боли или давлении в груди, одышке не по нагрузке, "
                 "головокружении, перебоях сердца; не проходит — скорая.",
                 "Clear the load with your doctor. Stop at once for chest pain or pressure, unusual breathlessness, "
                 "dizziness or palpitations; if it persists, call an ambulance."),
}
# Те же красные флаги короче — для промпта ИИ (место в выжимке ограничено).
RED_FLAGS_BRIEF: dict[str, tuple[str, str]] = {
    "pregnancy": ("красные флаги беременности — прекратить и к врачу: кровотечение, схватки, подтекание вод, одышка до "
                  "нагрузки, головокружение, боль в груди, отёк икры, меньше шевелений",
                  "pregnancy red flags — stop and call a doctor: bleeding, contractions, fluid leakage, breathlessness "
                  "before exertion, dizziness, chest pain, calf swelling, reduced fetal movement"),
    "heart_bp": ("красные флаги сердца — прекратить: боль или давление в груди, одышка не по нагрузке, обморок, "
                 "перебои, тошнота с холодным потом; не проходит за минуты — скорая",
                 "heart red flags — stop: chest pain or pressure, disproportionate breathlessness, fainting, "
                 "palpitations, nausea with cold sweat; not settling in minutes — ambulance"),
}


def safety_tips(profile, lang="ru") -> list:
    """Советы «врач + красные флаги» для ограничений, при которых программа только после врача."""
    p = _norm_profile(profile)
    return [_t(DOCTOR_TIPS[code], lang) for code in p["limitations"]
            if code in DOCTOR_TIPS and SAFETY.get(code, {}).get("requires_doctor")]


# Дополнительные слоты под акцент анкеты: (паттерн, зона дня).
FOCUS_EXTRA_SLOTS: dict[str, list[tuple[str, str]]] = {
    "arms": [("elbow_flexion", "upper"), ("elbow_extension", "upper")],
    "shoulders": [("lateral_delt", "upper")],
    "chest": [("chest_fly", "upper")],
    "back": [("vertical_pull", "upper")],
    "glutes": [("glute_abduction", "lower")],
    "legs": [("knee_flexion", "lower")],
    "core": [("core_anti_extension", "any")],
}

# Чем добирать силовой день, если ограничения и оборудование убрали почти все его слоты.
FILLER_PATTERNS = ("hip_thrust", "squat", "core_rotation", "core_anti_extension", "core_flexion", "calf")

_ROLE_ORDER = {"accessory": 0, "secondary": 1, "main": 2}
# Доля верхней границы MAV (RP), ниже которой не опускаем максимум объёма уровня.
_MAV_SHARE = {"beginner": 0.5, "intermediate": 0.75, "advanced": 1.0}


# --------------------------------------------------------------------------- #
#  12. Функции: профиль, каталог, безопасность
# --------------------------------------------------------------------------- #
def _norm_profile(profile) -> dict:
    """Профиль анкеты (dict/ORM, поля *_json) → нормализованный dict с умолчаниями."""
    if isinstance(profile, dict) and profile.get("_normalized"):
        return profile

    def code(name, allowed):
        value = str(_get(profile, name) or "").strip().lower()
        return value if value in allowed else DEFAULT_PROFILE[name]

    minutes = _to_int(_get(profile, "session_minutes"), DEFAULT_PROFILE["session_minutes"]) or DEFAULT_PROFILE["session_minutes"]
    minutes = min(SESSION_MINUTES, key=lambda m: (abs(m - minutes), m))
    weeks = _clamp(_to_int(_get(profile, "program_weeks"), DEFAULT_PROFILE["program_weeks"]) or DEFAULT_PROFILE["program_weeks"], 1, 16)
    days = _clamp(_to_int(_get(profile, "days_per_week"), DEFAULT_PROFILE["days_per_week"]) or DEFAULT_PROFILE["days_per_week"],
                  DAYS_RANGE[0], DAYS_RANGE[1])
    extras = []
    for item in _list_field(profile, "equipment_extra"):
        text = str(item).strip().lower()
        if text and text not in extras:
            extras.append(text)
    limits = [x for x in LIMITATIONS if x in {str(v).strip().lower() for v in _list_field(profile, "limitations")}]
    focus = []
    for item in _list_field(profile, "focus"):
        text = str(item).strip().lower()
        if text in FOCUS_CODES and text not in focus:
            focus.append(text)
    split_pref = str(_get(profile, "split_type") or _get(profile, "split") or "").strip().lower() or None
    return {
        "_normalized": True,
        "goal": code("goal", GOALS),
        "level": code("level", LEVELS),
        "equipment": code("equipment", EQUIPMENT_PROFILES),
        "equipment_extra": extras,
        "days_per_week": days,
        "session_minutes": minutes,
        "program_weeks": weeks,
        "limitations": limits,
        "focus": focus,
        "split_type": split_pref,
    }


def available_equipment(profile) -> set:
    """Оборудование пользователя: базовый набор варианта + дополнительные чипы (как в trainer_logic)."""
    p = _norm_profile(profile)
    allowed = set(EQUIPMENT_BY_PROFILE[p["equipment"]])
    for item in p["equipment_extra"]:
        mapped = EXTRA_TO_EQUIPMENT.get(item)
        if mapped:
            allowed.add(mapped)
    return allowed


def focus_muscles(profile) -> list:
    """Группы мышц каталога из акцента анкеты."""
    out = []
    for code in _norm_profile(profile)["focus"]:
        for muscle in FOCUS_MUSCLES.get(code, ()):
            if muscle not in out:
                out.append(muscle)
    return out


def _catalog_index(catalog_map) -> dict:
    """Каталог любого вида (dict slug→запись, список записей/ORM, trainer_ai._Catalog) → dict slug→запись.

    Без каталога используется встроенный seed — функции работают и в тестах, и в промпте.
    """
    if catalog_map is None:
        return _SEED_BY_SLUG
    entries = getattr(catalog_map, "entries", None)
    if isinstance(entries, dict):
        return dict(entries)
    out: dict = {}
    if isinstance(catalog_map, dict):
        for key, value in catalog_map.items():
            if value is None or isinstance(value, (str, int, float)):
                continue
            out[str(_get(value, "slug") or key)] = value
        return out or _SEED_BY_SLUG
    if isinstance(catalog_map, (list, tuple, set)):
        for item in catalog_map:
            slug = _get(item, "slug") if not isinstance(item, str) else None
            if slug:
                out[str(slug)] = item
        return out or _SEED_BY_SLUG
    return _SEED_BY_SLUG


def _contra(entry) -> set:
    return {str(x).strip().lower() for x in _list_field(entry, "contraindications") if str(x).strip()}


def _secondary(entry) -> list:
    return [str(x).strip() for x in _list_field(entry, "secondary_muscles") if str(x).strip()]


def _difficulty(entry) -> int:
    return _to_int(_get(entry, "difficulty"), 1) or 1


def _safety_for(p) -> dict:
    """Слить правила SAFETY для всех ограничений профиля в одну структуру."""
    merged = {"exclude": set(), "avoid": set(), "fallback": {}, "prefer": {}, "overrides": {}, "requires_doctor": False}
    ov = merged["overrides"]
    for lim in p["limitations"]:
        rule = SAFETY.get(lim)
        if not rule:
            continue
        merged["exclude"] |= set(rule["exclude_slugs"])
        merged["avoid"] |= set(rule["avoid_patterns"])
        merged["requires_doctor"] = merged["requires_doctor"] or rule["requires_doctor"]
        for key in ("fallback", "prefer"):
            source = rule["pattern_fallback"] if key == "fallback" else rule["prefer"]
            for pattern, values in source.items():
                bucket = merged[key].setdefault(pattern, [])
                bucket.extend(v for v in values if v not in bucket)
        for key, value in rule["overrides"].items():
            if key in ("min_reps", "min_rest_sec"):
                ov[key] = max(ov.get(key, 0), value)
            elif key in ("max_rpe", "max_hold_sec", "pain_max"):
                ov[key] = min(ov.get(key, value), value)
            elif key == "no_heavy_day":
                ov[key] = ov.get(key, False) or bool(value)
            elif key == "cardio_kind":
                ov[key] = "steady"
    return merged


def _allowed(slug, cat, p, safety, equipment, max_difficulty) -> bool:
    entry = cat.get(slug)
    if entry is None or _get(entry, "is_active", True) is False:
        return False
    if _get(entry, "equipment") not in equipment:
        return False
    if _contra(entry) & set(p["limitations"]):
        return False
    if slug in safety["exclude"]:
        return False
    if safety["overrides"].get("min_reps") and _get(entry, "measure_type") == "reps" and _difficulty(entry) >= 3:
        # При ограничении «не меньше N повторов» не берём трудные упражнения с весом тела (подтягивания,
        # скандинавские сгибания): их почти никто не сделает N раз подряд.
        return False
    return _difficulty(entry) <= max_difficulty


def _candidates(pattern, p, cat, used, safety, equipment, avoid=()) -> list:
    row = PATTERNS.get(pattern)
    if row is None or pattern in safety["avoid"]:
        return []
    prefer = safety["prefer"].get(pattern, [])
    ordered = []
    for slug in list(prefer) + row["first_by_goal"].get(p["goal"], []) + row["options"]:
        if slug not in ordered:
            ordered.append(slug)
    max_diff = LEVEL_PARAMS[p["level"]]["max_difficulty"]
    heavy_used = bool(set(used) & HEAVY_LOWER_BACK)
    valid = [
        s for s in ordered
        if s not in used and s not in avoid and _allowed(s, cat, p, safety, equipment, max_diff)
        and not (heavy_used and s in HEAVY_LOWER_BACK)
    ]
    if p["level"] == "beginner" and p["goal"] != "strength":
        # Новичку сначала простые варианты (техника важнее), но предпочтения безопасности — впереди.
        valid.sort(key=lambda s: (0 if s in prefer else 1, _difficulty(cat[s])))
    return valid


def _pick(pattern, p, cat, used, variant, safety, equipment, avoid=(), skip_patterns=()):
    """(slug, паттерн) с учётом заменителей: сначала сам паттерн, затем замены безопасности, затем обычные.

    skip_patterns — движения, которые в этом дне уже заняты сверх задуманного шаблоном. Без этого
    замена «присед → ягодичный мост» при больных коленях ставила в один день два моста подряд, а
    «жим вверх → задняя дельта» при больных плечах — тягу к лицу на блоке и тягу резинки к лицу.
    """
    tried = list(skip_patterns)
    chain = [pattern] + safety["fallback"].get(pattern, []) + PATTERNS.get(pattern, {}).get("fallback", [])
    for name in chain:
        if name in tried:
            continue
        tried.append(name)
        valid = _candidates(name, p, cat, used, safety, equipment, avoid)
        if valid:
            return valid[(variant if name == pattern else 0) % len(valid)], name
    return None, None


def pick_exercise(pattern, profile, catalog_map=None, used=None, variant=0, with_fallback=True):
    """Упражнение каталога для паттерна под профиль или None.

    Учитывает оборудование, противопоказания каталога и SAFETY, сложность уровня,
    не повторяет упражнения из `used` (внутри дня) и не ставит второе тяжёлое
    упражнение на поясницу. variant — n-й подходящий вариант (для разных дней).
    """
    p = _norm_profile(profile)
    cat = _catalog_index(catalog_map)
    safety = _safety_for(p)
    equipment = available_equipment(p)
    used = set(used or ())
    if with_fallback:
        return _pick(pattern, p, cat, used, _to_int(variant, 0) or 0, safety, equipment)[0]
    valid = _candidates(pattern, p, cat, used, safety, equipment)
    return valid[(_to_int(variant, 0) or 0) % len(valid)] if valid else None


def _trainable(muscle, p, cat, safety, equipment) -> bool:
    """Есть ли в каталоге хоть одно разрешённое профилю силовое упражнение на эту мышцу."""
    max_diff = LEVEL_PARAMS[p["level"]]["max_difficulty"]
    for slug, entry in cat.items():
        if _get(entry, "muscle_group") != muscle or _get(entry, "category") not in ("compound", "isolation"):
            continue
        if _allowed(slug, cat, p, safety, equipment, max_diff):
            return True
    return False


# --------------------------------------------------------------------------- #
#  Выбор схемы, объём, периодизация
# --------------------------------------------------------------------------- #
def select_split(profile) -> dict:
    """Шаблон недели под профиль (копия из SPLITS). Всегда что-то возвращает.

    Оценка кандидатов с тем же числом дней: несовпадение цели +20, уровня +10 за
    ступень, специализированное оборудование не того профиля +15 (своё — −3),
    «ягодичный» шаблон без акцента на ягодицы +6 (с акцентом −4), тяжёлые схемы при
    беременности/давлении +8, желаемый тип сплита −5; дальше priority и id.
    """
    p = _norm_profile(profile)
    days = p["days_per_week"]
    pool = [s for s in SPLITS if s["days_per_week"] == days]
    if not pool:
        nearest = min({s["days_per_week"] for s in SPLITS}, key=lambda d: (abs(d - days), d))
        pool = [s for s in SPLITS if s["days_per_week"] == nearest]
    risky = bool({"pregnancy", "heart_bp"} & set(p["limitations"]))

    def score(split):
        value = 0
        if p["goal"] not in split["goals"]:
            value += 20
        if p["level"] not in split["levels"]:
            idx = LEVELS.index(p["level"])
            value += 10 * min(abs(idx - LEVELS.index(lv)) for lv in split["levels"])
        if split["equipment"] != ["any"]:
            value += -3 if p["equipment"] in split["equipment"] else 15
        if "glutes" in split["tags"]:
            value += -4 if "glutes" in p["focus"] else 6
        if risky and "heavy" in split["tags"]:
            value += 8
        if p["split_type"] and p["split_type"] == split["split_type"]:
            value -= 5
        return value + split["priority"], split["id"]

    return copy.deepcopy(min(pool, key=score))


def volume_targets(profile) -> dict:
    """Рабочие подходы в неделю на мышцу: {muscle: {"min", "target", "max"}}.

    Норма уровня × множитель цели × доля мышцы (VOLUME_LANDMARKS.factor);
    мышцы из акцента получают верх диапазона (target = max до focus_max уровня);
    потолок — верхняя граница MRV (для акцента — MRV при приоритете, но не выше 30).
    """
    p = _norm_profile(profile)
    base = LEVEL_PARAMS[p["level"]]["weekly_sets"]
    mult = GOAL_PARAMS[p["goal"]]["volume_mult"]
    focus = set(focus_muscles(p))
    out = {}
    for muscle in MUSCLES:
        lm = VOLUME_LANDMARKS[muscle]
        k = mult * lm["factor"]
        # Минимальная доза: ~4 подхода для роста мышц; при цели «выносливость» силовые нужны для
        # поддержания и экономичности — хватает 1–2 подходов в неделю (pelland2025_dose, ronnestad2014_endurance).
        floor = 4 if muscle in MAJOR_MUSCLES and p["goal"] != "endurance" else 2
        mn = max(floor, _rnd(base["min"] * k))
        tg = max(mn, _rnd(base["target"] * k))
        mx = max(tg, _rnd(base["max"] * k))
        # Верхняя граница не ниже части MAV по RP: иначе при малом множителе цели мышцы, которые много
        # получают от базовых (кор, икры, ягодицы), «перебирают» объём от одних только синергистов.
        share = _MAV_SHARE[p["level"]] * max(1.0, lm["factor"])
        mx = max(mx, _rnd(lm["mav"][1] * share))
        if muscle in focus:
            # Акцент: цель — верх диапазона, а сам верх поднимается до потолка приоритетной мышцы
            # уровня (focus_max: новичок 12, средний 20, продвинутый 24 — принцип volume_by_level), но
            # не ниже обычного максимума. Верхнюю границу MAV при приоритете по RP (до 30) не берём: она
            # выше собственного правила базы и давала 27–29 прямых подходов на ягодицы даже при похудении.
            mx = max(mx, base["focus_max"])
            tg = mx
            cap = min(lm["mrv_p"][1], 30)
        else:
            cap = lm["mrv"][1]
        mx = min(mx, cap)
        tg = min(tg, mx)
        mn = min(mn, tg)
        out[muscle] = {"min": mn, "target": tg, "max": mx}
    return out


def _phases(length) -> list:
    if length <= 0:
        return []
    if length == 1:
        return ["build"]
    n_base = max(1, _rnd(length * 0.3))
    n_peak = 1 if length >= 3 else 0
    n_build = max(0, length - n_base - n_peak)
    return ["base"] * n_base + ["build"] * n_build + ["peak"] * n_peak


def periodization_for(profile) -> list:
    """Фазы по неделям: [{week, phase, weight_pct, sets_delta, label_ru, label_en}].

    Разгрузка в конце каждого блока накопления; длина накопления не больше
    max_accumulation_weeks уровня и не меньше 3 недель. Новичку плановая разгрузка
    не нужна до 8 недель (bell2023_deload, prog_stronglifts) — у него только
    base/build/peak. Формат совпадает с trainer_ai._normalize_periodization.
    """
    p = _norm_profile(profile)
    weeks = p["program_weeks"]
    lvl = LEVEL_PARAMS[p["level"]]
    sequence: list[str] = []
    if p["level"] == "beginner" and weeks < lvl["deload_every_weeks"]:
        sequence = _phases(weeks)
    else:
        size = lvl["max_accumulation_weeks"] + 1
        blocks = max(1, math.ceil(weeks / size))
        while blocks > 1 and weeks / blocks - 1 < 3:
            blocks -= 1
        lengths = [weeks // blocks] * blocks
        for i in range(weeks - sum(lengths)):
            lengths[i] += 1
        for length in lengths:
            sequence += _phases(length - 1) + ["deload"]
    out = []
    for week, phase in enumerate(sequence[:weeks], start=1):
        guide = PHASE_GUIDE[phase]
        out.append({"week": week, "phase": phase, "weight_pct": guide["weight_pct"], "sets_delta": guide["sets_delta"],
                    "label_ru": PHASE_LABELS[phase][0], "label_en": PHASE_LABELS[phase][1]})
    return out


# --------------------------------------------------------------------------- #
#  Время тренировки (TIME_MODEL) — одна формула для build_week и audit_week,
#  чтобы собственные недели не получали замечаний «не укладывается во время».
# --------------------------------------------------------------------------- #
def _exercise_seconds(item, entry, first_of_pattern) -> float:
    sets = max(1, _to_int(_get(item, "sets"), 1) or 1)
    rest = _to_int(_get(item, "rest_sec"), 90) or 90
    category = _get(entry, "category") or "compound"
    measure = _get(entry, "measure_type") or "reps_weight"
    time_sec = _to_int(_get(item, "time_sec"))
    if category == "cardio" and sets == 1:
        return (time_sec or 600) + TIME_MODEL["transition_sec"]
    if time_sec:
        work = time_sec
    else:
        lo = _to_int(_get(item, "reps_min"), 8) or 8
        hi = _to_int(_get(item, "reps_max"), lo) or lo
        work = _clamp((lo + hi) / 2 * TIME_MODEL["sec_per_rep"], TIME_MODEL["work_sec_min"], TIME_MODEL["work_sec_max"])
    total = sets * work + (sets - 1) * rest + TIME_MODEL["transition_sec"]
    if first_of_pattern and category == "compound" and measure == "reps_weight":
        heavy = (_to_int(_get(item, "reps_max"), 99) or 99) <= 8
        total += TIME_MODEL["warmup_sets_heavy_sec"] if heavy else TIME_MODEL["warmup_sets_moderate_sec"]
    return total


def _aux_seconds(items) -> float:
    total = 0.0
    for item in _as_list(items):
        if not isinstance(item, dict):
            continue
        sets = max(1, _to_int(item.get("sets"), 1) or 1)
        if item.get("time_sec"):
            total += sets * (_to_int(item.get("time_sec"), 60) or 60)
        else:
            total += sets * max(30, (_to_int(item.get("reps"), 10) or 10) * 3)
    return total


def estimate_day_seconds(day, catalog_map=None) -> float:
    """Оценка длительности дня в секундах: разминка + упражнения + заминка."""
    cat = _catalog_index(catalog_map)
    total = _aux_seconds(_get(day, "warmup")) + _aux_seconds(_get(day, "cooldown"))
    seen_patterns = set()
    for item in _as_list(_get(day, "exercises")):
        if not isinstance(item, dict):
            continue
        entry = cat.get(item.get("slug")) or item
        pattern = _guess_pattern(item.get("slug"), entry)
        first = pattern not in seen_patterns
        seen_patterns.add(pattern)
        total += _exercise_seconds(item, entry, first)
    return total


def _guess_pattern(slug, entry) -> str:
    """Паттерн упражнения: из индекса, иначе по мышце/категории/slug (для упражнений от ИИ)."""
    if slug in SLUG_PATTERN:
        return SLUG_PATTERN[slug]
    muscle = _get(entry, "muscle_group") or ""
    category = _get(entry, "category") or ""
    text = str(slug or "")
    if category == "cardio" or muscle in ("cardio", "full_body"):
        return "conditioning"
    if muscle == "back":
        return "vertical_pull" if any(k in text for k in ("pulldown", "pullup", "chin", "pull_up")) else "horizontal_pull"
    table = {
        "chest": "horizontal_push" if category == "compound" else "chest_fly",
        "shoulders": "vertical_push" if category == "compound" else "lateral_delt",
        "biceps": "elbow_flexion", "triceps": "elbow_extension",
        "quads": "squat" if category == "compound" else "knee_extension",
        "hamstrings": "hinge" if category == "compound" else "knee_flexion",
        "glutes": "hip_thrust" if category == "compound" else "glute_abduction",
        "calves": "calf", "core": "core_anti_extension",
    }
    return table.get(muscle, "other")


# --------------------------------------------------------------------------- #
#  Сборка недели без ИИ
# --------------------------------------------------------------------------- #
def _rules(p) -> dict:
    """Действующие диапазоны под цель, уровень и ограничения."""
    goal = GOAL_PARAMS[p["goal"]]
    safety = _safety_for(p)
    ov = safety["overrides"]
    reps = {key: tuple(value) for key, value in goal["reps"].items()}
    if p["level"] == "beginner" and p["goal"] == "strength":
        # Новичку в силе 5–8 повторов: техника и умеренная нагрузка (GOAL_PARAMS.strength.notes).
        reps["heavy"] = reps["main"] = (5, 8)
    if ov.get("no_heavy_day"):
        reps["heavy"] = reps["main"]
    if ov.get("min_reps"):
        for key, (lo, hi) in list(reps.items()):
            lo = max(lo, ov["min_reps"])
            reps[key] = (lo, min(REPS_RANGE[1], max(hi, lo + 2)))
    return {"goal": goal, "level": LEVEL_PARAMS[p["level"]], "reps": reps, "safety": safety, "overrides": ov}


def _rest_for(rules, category, lower=False) -> int:
    key = "isolation" if category == "isolation" else "compound"
    lo, hi = rules["goal"]["rest_sec"][key]
    value = lo if lower else int(round((lo + hi) / 2 / 15.0) * 15)
    value = max(value, rules["overrides"].get("min_rest_sec", 0))
    return _clamp(value, REST_RANGE[0], REST_RANGE[1])


def _rpe_for(rules, category) -> int:
    key = "isolation" if category == "isolation" else "compound"
    rir = rules["goal"]["rir"][key][2] + rules["level"]["rir_extra"]
    rpe = 10 - rir
    if rules["overrides"].get("max_rpe"):
        rpe = min(rpe, rules["overrides"]["max_rpe"])
    return _clamp(rpe, RPE_RANGE[0], RPE_RANGE[1])


def _note(kind, lang, rir=None, heavy=False):
    en = _is_en(lang)
    if kind == "strength":
        if heavy:
            return (f"Warm-up ~50%×8, ~70%×4, then work sets; {rir} reps in reserve" if en
                    else f"Разминка ~50%×8, ~70%×4, затем рабочие; {rir} повт. в запасе")
        return f"Leave {rir} reps in reserve" if en else f"Оставляй {rir} повт. в запасе"
    if kind == "steady":
        return "Moderate pace: you can talk in sentences" if en else "Умеренно: можно говорить фразами"
    if kind == "intervals":
        return "Work hard, then easy until breathing settles" if en else "Интервал — интенсивно, отдых — до восстановления дыхания"
    return None


def _make_strength_item(slug, entry, slot, intensity, rules, p, lang) -> dict:
    measure = _get(entry, "measure_type") or "reps_weight"
    category = _get(entry, "category") or "compound"
    key = slot["reps_key"]
    if key == "main" and intensity in ("heavy", "volume"):
        key = intensity
    if category == "isolation" and key in ("heavy", "main"):
        key = "accessory"
    lo, hi = rules["reps"][key]
    reps_min = reps_max = time_sec = None
    if measure in ("time", "distance"):
        time_sec = 45 if p["goal"] == "endurance" else 30
        if rules["overrides"].get("max_hold_sec"):
            time_sec = min(time_sec, rules["overrides"]["max_hold_sec"])
    elif measure == "reps" and _difficulty(entry) >= 3:
        # Самые трудные упражнения с весом тела (подтягивания, брусья, скандинавские сгибания)
        # мало кто сделает больше 8–10 раз: диапазон ниже, прогрессия — повторами.
        reps_min, reps_max = (5, 10) if category == "compound" else (4, 8)
    elif measure == "reps":
        # Вес тела: нагрузку добираем повторами до близкого отказа (kikuchi2017_pushup), потолок как в trainer_logic (25).
        reps_min = max(lo, 6)
        reps_max = min(25, max(hi + 4, reps_min + 4))
    else:
        reps_min, reps_max = lo, hi
    rpe = _rpe_for(rules, category)
    heavy = bool(reps_max and reps_max <= 8 and category == "compound" and measure == "reps_weight")
    rest = _rest_for(rules, category)
    if measure in ("reps", "time"):
        # Вес тела и удержания: нагрузка далека от тяжёлого подхода со штангой, поэтому отдых цели «сила»
        # (3–5 мин) тут не нужен и только съедает время тренировки — хватает 1–2 минут (acsm2009_progression).
        # Нижний предел ограничений (беременность, давление — не меньше 90 с) сохраняется.
        rest = max(min(rest, 120), rules["overrides"].get("min_rest_sec", 0))
    return {
        "slug": slug,
        "exercise_id": _get(entry, "id"),
        "muscle_group": _get(entry, "muscle_group"),
        "sets": _clamp(_to_int(slot["sets"], 3) or 3, SETS_RANGE[0], SETS_RANGE[1]),
        "reps_min": reps_min,
        "reps_max": reps_max,
        "time_sec": time_sec,
        "rest_sec": rest,
        "start_weight_kg": None,
        "rpe": rpe,
        "tempo": None,
        "note": _note("strength", lang, rir=10 - rpe, heavy=heavy),
        "order": 0,
    }


def _day_region(slots) -> str:
    regions = {PATTERNS[s["pattern"]]["region"] for s in slots if s["pattern"] in PATTERNS}
    if "upper" in regions and "lower" in regions:
        return "full"
    for name in ("lower", "upper", "core"):
        if name in regions:
            return name
    return "cardio"


def _rows_seconds(rows) -> float:
    total = 0.0
    seen = set()
    for row in rows:
        first = row["pattern"] not in seen
        seen.add(row["pattern"])
        total += _exercise_seconds(row["item"], row["entry"], first)
    return total


def _fit_day_to_time(rows, limit_sec, max_exercises, rules):
    """Сократить силовую часть дня под бюджет времени по TRIM_ORDER.

    Порядок (iversen2021_time): подходы изоляции до 2 → убрать изоляцию → отдых к
    нижней границе → подходы вспомогательных и базовых до 2 (упражнение остаётся) →
    только потом убирать многосуставные, начиная с наименее приоритетных.
    """
    def over():
        return _rows_seconds(rows) > limit_sec

    def worst(role, need_sets=None):
        cands = [r for r in rows if r["role"] == role and (need_sets is None or r["item"]["sets"] > need_sets)]
        if not cands:
            return None
        # При равном приоритете тяги сокращаем последними: тяга не должна отставать от жима.
        return max(cands, key=lambda r: (r["priority"], 0 if r["pattern"] in PULL_PATTERNS else 1, rows.index(r)))

    while len(rows) > max_exercises:
        victim = worst("accessory") or worst("secondary") or (worst("main") if len(rows) > 1 else None)
        if victim is None:
            break
        rows.remove(victim)
    guard = 0
    while over() and guard < 200:
        guard += 1
        row = worst("accessory", 2)
        if row:
            row["item"]["sets"] -= 1
            continue
        row = worst("accessory")
        if row and len(rows) > 1:
            rows.remove(row)
            continue
        shortened = False
        for r in rows:
            low = _rest_for(rules, _get(r["entry"], "category"), lower=True)
            if r["item"]["rest_sec"] > low:
                r["item"]["rest_sec"] = low
                shortened = True
        if shortened:
            continue
        row = worst("secondary", 2) or worst("main", 2)
        if row:
            row["item"]["sets"] -= 1
            continue
        row = worst("secondary")
        if row and len(rows) > 1:
            rows.remove(row)
            continue
        mains = [r for r in rows if r["role"] == "main"]
        if len(rows) > 1 and mains:
            rows.remove(mains[-1])
            continue
        break


def _rows_by_muscle(plans) -> dict:
    totals = {m: 0.0 for m in MUSCLES}
    for plan in plans:
        for row in plan["rows"]:
            _add_sets(totals, row["entry"], row["item"]["sets"])
    return totals


def _add_sets(totals, entry, sets):
    if _get(entry, "category") not in ("compound", "isolation"):
        return
    sets = _to_int(sets, 0) or 0
    primary = _get(entry, "muscle_group")
    if primary in totals:
        totals[primary] += sets
    for muscle in _secondary(entry):
        if muscle in totals and muscle != primary and muscle not in STABILIZER_MUSCLES:
            totals[muscle] += 0.5 * sets


def _set_cap(role, level) -> int:
    # ACSM 2009: новичку 1–3 подхода на упражнение; остальным базовые до 5, прочие до 4.
    if level == "beginner":
        return 3
    return 5 if role == "main" else 4


def _balance_week(plans, p, rules, targets, cat, safety, equipment, lang):
    """Подогнать неделю: объём в пределах volume_targets, каждая крупная мышца
    получает нагрузку, тяга не меньше жима — не выходя за время дня."""
    level = p["level"]
    session_cap = rules["level"]["session_sets_per_muscle_max"]
    focus = set(focus_muscles(p))
    strength_plans = [plan for plan in plans if not plan["is_cardio_day"]]

    def fits(plan, extra_row=None, extra_sets=0, row=None):
        if row is not None:
            row["item"]["sets"] += extra_sets
        rows = plan["rows"] + ([extra_row] if extra_row else [])
        ok = _rows_seconds(rows) <= plan["limit_sec"]
        if row is not None:
            row["item"]["sets"] -= extra_sets
        return ok

    def direct_sets(plan, muscle):
        return sum(r["item"]["sets"] for r in plan["rows"] if _get(r["entry"], "muscle_group") == muscle)

    def pattern_sets(names):
        return sum(r["item"]["sets"] for plan in plans for r in plan["rows"] if r["pattern"] in names)

    def reduce_over(keep_balance=False):
        totals = _rows_by_muscle(plans)
        for muscle in MUSCLES:
            guard = 0
            while totals[muscle] > targets[muscle]["max"] + 0.01 and guard < 80:
                guard += 1
                blocked = keep_balance and pattern_sets(PULL_PATTERNS) - 1 < pattern_sets(PUSH_PATTERNS)
                rows = [(plan, r) for plan in plans for r in plan["rows"]
                        if _get(r["entry"], "muscle_group") == muscle and not r.get("protected")
                        and not (blocked and r["pattern"] in PULL_PATTERNS)]
                # Сначала изоляция (до 1 подхода — новичку и 1 подход работает, acsm2009_progression),
                # затем вспомогательные и базовые (не ниже 2): базовые многосуставные сохраняем дольше всего.
                cands = [(plan, r) for plan, r in rows if r["role"] == "accessory" and r["item"]["sets"] > 1]
                if not cands:
                    cands = [(plan, r) for plan, r in rows if r["role"] == "secondary" and r["item"]["sets"] > 2]
                if not cands:
                    cands = [(plan, r) for plan, r in rows if r["item"]["sets"] > 2]
                if cands:
                    plan, row = max(cands, key=lambda pr: (pr[1]["item"]["sets"], pr[1]["priority"]))
                    row["item"]["sets"] -= 1
                else:
                    # Сильно выше максимума из-за множества мелких упражнений: убираем вспомогательное.
                    removable = [(plan, r) for plan, r in rows if r["role"] == "accessory" and len(plan["rows"]) > 1]
                    if not removable:
                        break
                    plan, row = max(removable, key=lambda pr: pr[1]["priority"])
                    plan["rows"].remove(row)
                totals = _rows_by_muscle(plans)

    reduce_over()

    # Ниже минимума (акцент — ниже цели): добавляем подходы, если есть время и не превышен лимит за сессию.
    totals = _rows_by_muscle(plans)
    for muscle in MUSCLES:
        if not _trainable(muscle, p, cat, safety, equipment):
            continue
        wanted = targets[muscle]["target"] if muscle in focus else targets[muscle]["min"]
        guard = 0
        while totals[muscle] < wanted - 0.01 and guard < 60:
            guard += 1
            cands = [(plan, r) for plan in plans for r in plan["rows"]
                     if _get(r["entry"], "muscle_group") == muscle
                     and r["item"]["sets"] < _set_cap(r["role"], level)
                     and direct_sets(plan, muscle) < session_cap
                     and fits(plan, extra_sets=1, row=r)]
            if not cands:
                break
            plan, row = min(cands, key=lambda pr: (pr[1]["item"]["sets"], -_ROLE_ORDER.get(pr[1]["role"], 0)))
            row["item"]["sets"] += 1
            totals = _rows_by_muscle(plans)
            if totals[muscle] > targets[muscle]["max"]:
                row["item"]["sets"] -= 1
                totals = _rows_by_muscle(plans)
                break

    # Каждая крупная мышца, которую можно тренировать, получает хотя бы одно упражнение
    # (минимальная действенная доза ~4 подхода в неделю — iversen2021_time).
    # Если мышца ниже минимума, а подходы уже упёрлись в потолок на упражнение, добавляем ещё одно
    # упражнение в день, где есть свободное время (без вытеснения других).
    totals = _rows_by_muscle(plans)
    for muscle in sorted(MAJOR_MUSCLES):
        missing = totals[muscle] <= 0
        below = totals[muscle] < targets[muscle]["min"] - 0.01
        if not (missing or below) or not strength_plans or not _trainable(muscle, p, cat, safety, equipment):
            continue
        names = [n for n, row in PATTERNS.items() if row["muscle"] == muscle]
        names.sort(key=lambda n: 0 if PATTERNS[n]["kind"] == "compound" else 1)
        added = False
        for plan in sorted(strength_plans, key=lambda pl: _rows_seconds(pl["rows"]) - pl["limit_sec"]):
            if not missing and (any(_get(r["entry"], "muscle_group") == muscle for r in plan["rows"])
                                or len(plan["rows"]) >= rules["level"]["max_exercises"]):
                continue
            options = []
            for name in names:
                for slug in _candidates(name, p, cat, {r["slug"] for r in plan["rows"]}, safety, equipment):
                    options.append((slug, name))
            # Сначала упражнения, где эта мышца основная: иначе подход засчитается ей только наполовину.
            options.sort(key=lambda sn: 0 if _get(cat[sn[0]], "muscle_group") == muscle else 1)
            for slug, name in options[:3]:
                slot = _s(name, "accessory", 2, priority=2)
                row = {"slug": slug, "entry": cat[slug], "slot": slot, "pattern": name, "role": "accessory", "priority": 2,
                       "protected": True, "item": _make_strength_item(slug, cat[slug], slot, "normal", rules, p, lang)}
                if not fits(plan, extra_row=row) and not missing:
                    continue
                if not fits(plan, extra_row=row):
                    # Освобождаем место (откатываем, если не вышло): подходы до 2 → убрать изоляцию →
                    # убрать упражнение мышцы с наибольшим запасом над минимумом, если она останется в неделе.
                    saved_rows = list(plan["rows"])
                    saved_sets = [(r, r["item"]["sets"]) for r in plan["rows"]]
                    for r in sorted(plan["rows"], key=lambda r: _ROLE_ORDER.get(r["role"], 0)):
                        while r["item"]["sets"] > 2 and not fits(plan, extra_row=row):
                            r["item"]["sets"] -= 1
                    while not fits(plan, extra_row=row):
                        spare = [r for r in plan["rows"] if r["role"] == "accessory" and not r.get("protected")]
                        if spare:
                            plan["rows"].remove(max(spare, key=lambda r: r["priority"]))
                            continue
                        now = _rows_by_muscle(plans)
                        spare = [r for r in plan["rows"]
                                 if _get(r["entry"], "muscle_group") in now and _get(r["entry"], "muscle_group") != muscle
                                 and now[_get(r["entry"], "muscle_group")] - r["item"]["sets"] > 0]
                        if not spare or len(plan["rows"]) <= 1:
                            break
                        victim = max(spare, key=lambda r: (now[_get(r["entry"], "muscle_group")]
                                                           - targets[_get(r["entry"], "muscle_group")]["min"],
                                                           -_ROLE_ORDER.get(r["role"], 0)))
                        plan["rows"].remove(victim)
                    if not fits(plan, extra_row=row):
                        plan["rows"][:] = saved_rows
                        for r, sets in saved_sets:
                            r["item"]["sets"] = sets
                        continue
                insert_at = len([r for r in plan["rows"] if _get(r["entry"], "category") == "compound"]) \
                    if _get(cat[slug], "category") == "compound" else len(plan["rows"])
                plan["rows"].insert(insert_at, row)
                added = True
                break
            if added:
                break
        totals = _rows_by_muscle(plans)

    # Тяга не меньше жима (баланс плечевого пояса; kolber2014_shoulder — укрепление задней поверхности).
    guard = 0
    while pattern_sets(PULL_PATTERNS) < pattern_sets(PUSH_PATTERNS) and guard < 60:
        guard += 1
        totals = _rows_by_muscle(plans)
        # Добавляем тягу, только если мышца не выйдет за максимум — иначе снимаем подход с жима.
        ups = [(plan, r) for plan in plans for r in plan["rows"]
               if r["pattern"] in PULL_PATTERNS and r["item"]["sets"] < _set_cap(r["role"], level)
               and totals.get(_get(r["entry"], "muscle_group"), 0) + 1 <= targets.get(_get(r["entry"], "muscle_group"), {}).get("max", 99)
               and fits(plan, extra_sets=1, row=r)]
        if ups:
            plan, row = min(ups, key=lambda pr: pr[1]["item"]["sets"])
            row["item"]["sets"] += 1
            continue
        downs = [(plan, r) for plan in plans for r in plan["rows"]
                 if r["pattern"] in PUSH_PATTERNS and r["item"]["sets"] > 2]
        if not downs:
            downs = [(plan, r) for plan in plans for r in plan["rows"]
                     if r["pattern"] in PUSH_PATTERNS and r["role"] != "main" and r["item"]["sets"] > 1]
        if not downs:
            break
        plan, row = min(downs, key=lambda pr: (_ROLE_ORDER.get(pr[1]["role"], 0), -pr[1]["item"]["sets"]))
        row["item"]["sets"] -= 1
    reduce_over(keep_balance=True)


# Хвост названия дня про кардио: «+ кардио», «+ лёгкое кардио», «+ интервалы» (и английские).
_CARDIO_SUFFIX = re.compile(r"\s*\+\s*(?:лёгкое |easy )?(?:кардио|интервалы|cardio|intervals)\s*$", re.IGNORECASE)


def _aux_item(slug, cat, time_sec=None, reps=None):
    return {"slug": slug, "exercise_id": _get(cat.get(slug), "id"), "sets": 1, "reps": reps,
            "time_sec": time_sec, "note": None}


def build_week(profile, catalog_map=None, lang="ru") -> dict:
    """Неделя без ИИ по базе знаний в формате week_template после normalize_program.

    Возвращает {"split_id", "split_type", "title", "days": [{day_index, title,
    session_type, focus_muscles, duration_min, warmup, exercises, cooldown}]}.
    Порядок: схема (select_split) → упражнения по паттернам (pick_exercise, разные
    slug для тяжёлого и объёмного дня) → подгонка под session_minutes → объём
    по volume_targets и тяга ≥ жим → кардио на остаток времени → разминка/заминка.
    """
    p = _norm_profile(profile)
    cat = _catalog_index(catalog_map)
    en = _is_en(lang)
    split = select_split(p)
    rules = _rules(p)
    safety = rules["safety"]
    equipment = available_equipment(p)
    targets = volume_targets(p)
    budget = SESSION_BUDGET[p["session_minutes"]]
    max_exercises = min(rules["level"]["max_exercises"], budget["max_exercises"], MAX_MAIN_EXERCISES)
    focus_set = set(focus_muscles(p))
    aux_sec = budget["warmup_sec"] + budget["cooldown_sec"] + 60  # + специфическое движение разминки
    week_reps: dict[str, tuple] = {}

    plans = []
    for index, day in enumerate(split["days"], start=1):
        slots = list(day["slots"])
        region = _day_region(slots)
        is_cardio_day = day["session_type"] == "cardio"
        if not is_cardio_day:
            present = {s["pattern"] for s in slots}
            for code in p["focus"]:
                for pattern, zone in FOCUS_EXTRA_SLOTS.get(code, []):
                    if pattern in present or (zone != "any" and region not in (zone, "full")):
                        continue
                    role = "secondary" if PATTERNS[pattern]["kind"] == "compound" else "accessory"
                    slots.insert(len([s for s in slots if s["role"] != "cardio"]), _s(pattern, role, 3, priority=2))
                    present.add(pattern)
            if p["goal"] in ("loss", "endurance") and not any(s["role"] == "cardio" for s in slots):
                slots.append(_s("cardio_steady", "cardio", 1))

        used: set = set()
        rows, cardio_slots = [], []
        # Сколько раз шаблон дня сам ставит движение (PPL: два приседа в день ног — задумано).
        planned = {}
        for s in slots:
            if s["role"] != "cardio":
                planned[s["pattern"]] = planned.get(s["pattern"], 0) + 1
        filled: dict[str, int] = {}
        for slot in slots:
            if slot["role"] == "cardio":
                cardio_slots.append(slot)
                continue
            key = slot["reps_key"]
            if key == "main" and day["intensity"] in ("heavy", "volume"):
                key = day["intensity"]
            wanted = rules["reps"].get(key)
            conflict = {s for s, rng in week_reps.items() if rng != wanted}
            # Движение, уже занятое заменой сверх плана шаблона, второй раз в день не ставим:
            # слот берёт следующую замену, а если её нет — пропускается (объём доберёт _balance_week).
            busy = {name for name, count in filled.items() if count >= max(1, planned.get(name, 0))}
            slug, pattern = _pick(slot["pattern"], p, cat, used, slot["variant"], safety, equipment, avoid=conflict,
                                  skip_patterns=busy)
            if slug is None and conflict:
                slug, pattern = _pick(slot["pattern"], p, cat, used, slot["variant"], safety, equipment, skip_patterns=busy)
            if slug is None:
                continue
            filled[pattern] = filled.get(pattern, 0) + 1
            used.add(slug)
            entry = cat[slug]
            item = _make_strength_item(slug, entry, slot, day["intensity"], rules, p, lang)
            week_reps.setdefault(slug, wanted)
            rows.append({"slug": slug, "entry": entry, "slot": slot, "pattern": pattern, "item": item,
                         "role": slot["role"], "priority": slot["priority"]})

        if not is_cardio_day and len(rows) < 3:
            # Анкета оставила дню почти ничего из задуманного (вес тела без турника и резинок при больных
            # запястьях: от «Верха» остаются одни австралийские подтягивания). Силовая из одного упражнения
            # бессмысленна — добираем доступными движениями на кор и ноги.
            for name in FILLER_PATTERNS:
                if len(rows) >= 3:
                    break
                busy = {n for n, count in filled.items() if count >= max(1, planned.get(n, 0))}
                slug, pattern = _pick(name, p, cat, used, 0, safety, equipment, skip_patterns=busy)
                if slug is None:
                    continue
                slot = _s(pattern, "secondary", 2, priority=3)
                filled[pattern] = filled.get(pattern, 0) + 1
                used.add(slug)
                rows.append({"slug": slug, "entry": cat[slug], "slot": slot, "pattern": pattern,
                             "item": _make_strength_item(slug, cat[slug], slot, "normal", rules, p, lang),
                             "role": "secondary", "priority": 3})

        # Многосуставные раньше изолирующих; упражнение на мышцу из акцента — первым среди базовых (nunes2021_order).
        compounds = [r for r in rows if _get(r["entry"], "category") == "compound"]
        others = [r for r in rows if _get(r["entry"], "category") != "compound"]
        for r in compounds:
            if _get(r["entry"], "muscle_group") in focus_set and r["role"] in ("main", "secondary"):
                compounds.remove(r)
                compounds.insert(0, r)
                break
        rows = compounds + others

        cardio_reserve = 0
        if cardio_slots and not is_cardio_day and p["session_minutes"] >= 45:
            cardio_reserve = 600
        if is_cardio_day:
            cardio_reserve = max(0, p["session_minutes"] * 60 - aux_sec - 300)
        limit_sec = p["session_minutes"] * 60 * TIME_MODEL["tolerance"] - aux_sec - cardio_reserve
        _fit_day_to_time(rows, limit_sec, max_exercises, rules)
        plans.append({"index": index, "day": day, "rows": rows, "cardio_slots": cardio_slots, "used": used,
                      "region": region, "limit_sec": limit_sec, "is_cardio_day": is_cardio_day})

    _balance_week(plans, p, rules, targets, cat, safety, equipment, lang)

    days_out = []
    for plan in plans:
        day = plan["day"]
        used = plan["used"]
        items = [r["item"] for r in plan["rows"]]
        strength_sec = _rows_seconds(plan["rows"])
        remaining = p["session_minutes"] * 60 * TIME_MODEL["tolerance"] - aux_sec - strength_sec
        cardio_items = []
        for slot in plan["cardio_slots"]:
            slug, pattern = _pick(slot["pattern"], p, cat, used, slot["variant"], safety, equipment)
            if slug is None:
                continue
            entry = cat[slug]
            measure = _get(entry, "measure_type") or "time"
            if pattern == "conditioning":
                if remaining < 360:
                    continue
                rounds = _clamp(int(remaining // 150), 3, 6)
                if p["level"] == "beginner":
                    rounds = min(rounds, 4)
                item = {"slug": slug, "exercise_id": _get(entry, "id"), "muscle_group": _get(entry, "muscle_group"),
                        "sets": rounds, "reps_min": None, "reps_max": None, "time_sec": None, "rest_sec": 90,
                        "start_weight_kg": None, "rpe": None, "tempo": None, "note": _note("intervals", lang), "order": 0}
                if measure in ("time", "distance"):
                    item["time_sec"] = 30
                else:
                    item["reps_min"], item["reps_max"] = (8, 12) if p["level"] == "beginner" else (10, 15)
                used_sec = _exercise_seconds(item, entry, False)
            else:
                cap = 3600 if plan["is_cardio_day"] else 2700  # аэробная сессия 30–60 мин (acsm_hypertension, who2020)
                seconds = int(min(cap, remaining - TIME_MODEL["transition_sec"]) // 60 * 60)
                minimum = 600 if plan["is_cardio_day"] else 480
                if seconds < minimum:
                    continue
                item = {"slug": slug, "exercise_id": _get(entry, "id"), "muscle_group": _get(entry, "muscle_group"),
                        "sets": 1, "reps_min": None, "reps_max": None, "time_sec": seconds, "rest_sec": 60,
                        "start_weight_kg": None, "rpe": None, "tempo": None, "note": _note("steady", lang), "order": 0}
                used_sec = seconds + TIME_MODEL["transition_sec"]
            used.add(slug)
            cardio_items.append(item)
            remaining -= used_sec
        items = items + cardio_items
        if not items:
            # Всегда хотя бы одно упражнение: быстрая ходьба доступна без оборудования и без противопоказаний.
            seconds = max(600, (p["session_minutes"] * 60 - aux_sec) // 60 * 60)
            items = [{"slug": "brisk_walk", "exercise_id": _get(cat.get("brisk_walk"), "id"), "muscle_group": "cardio",
                      "sets": 1, "reps_min": None, "reps_max": None, "time_sec": seconds, "rest_sec": 60,
                      "start_weight_kg": None, "rpe": None, "tempo": None, "note": _note("steady", lang), "order": 0}]
        for order, item in enumerate(items, start=1):
            item["order"] = order

        drill_region = "lower" if plan["region"] in ("lower", "full") else plan["region"]
        warmup = [_aux_item(WARMUP_SLUG, cat, time_sec=budget["warmup_sec"])]
        max_diff = LEVEL_PARAMS[p["level"]]["max_difficulty"]
        for slug in WARMUP_DRILLS.get(drill_region, []):
            if _allowed(slug, cat, p, safety, equipment, max_diff):
                measure = _get(cat[slug], "measure_type")
                warmup.append(_aux_item(slug, cat, time_sec=30 if measure == "time" else None,
                                        reps=None if measure == "time" else 10))
                break
        cooldown = [_aux_item(COOLDOWN_SLUG, cat, time_sec=budget["cooldown_sec"])]

        strength_items = [i for i in items if (i.get("muscle_group") or "") in MUSCLES]
        has_cardio = len(strength_items) < len(items)
        session_type = day["session_type"]
        title = day["title_en"] if en else day["title_ru"]
        if not strength_items:
            session_type = "cardio"
        elif session_type == "mixed" and not has_cardio:
            session_type = "strength"
            # На 30 минутах кардио в день не помещается — не обещаем его в названии («Всё тело A + кардио»).
            title = _CARDIO_SUFFIX.sub("", title) or title
        elif session_type == "strength" and has_cardio:
            session_type = "mixed"
        focus = []
        for item in items:
            muscle = item.get("muscle_group")
            if muscle and muscle not in focus and muscle not in ("mobility", "full_body"):
                focus.append(muscle)
        days_out.append({
            "day_index": plan["index"],
            "title": title,
            "session_type": session_type,
            "focus_muscles": focus[:5],
            "duration_min": p["session_minutes"],
            "warmup": warmup,
            "exercises": items,
            "cooldown": cooldown,
        })
    return {
        "split_id": split["id"],
        "split_type": split["split_type"],
        "title": split["name_en"] if en else split["name_ru"],
        "days": days_out,
    }


# --------------------------------------------------------------------------- #
#  Подсчёт объёма и аудит недели (своей или от ИИ)
# --------------------------------------------------------------------------- #
def _week_days(week) -> list:
    if isinstance(week, dict):
        template = week.get("week_template")
        days = template.get("days") if isinstance(template, dict) else week.get("days")
    else:
        days = week
    return [d for d in _as_list(days) if isinstance(d, dict)]


def weekly_sets_by_muscle(week, catalog_map=None) -> dict:
    """Рабочие подходы по мышцам за неделю: основная мышца = 1, вторичные = 0,5 (pelland2025_dose).

    Кардио, разминка и заминка не считаются. Неизвестные каталогу slug берут
    muscle_group из самого пункта (без вторичных мышц).
    """
    cat = _catalog_index(catalog_map)
    totals = {m: 0.0 for m in MUSCLES}
    for day in _week_days(week):
        for item in _as_list(day.get("exercises")):
            if not isinstance(item, dict):
                continue
            entry = cat.get(item.get("slug"))
            if entry is None:
                entry = {"muscle_group": item.get("muscle_group"), "category": "compound"}
            _add_sets(totals, entry, item.get("sets"))
    return {m: round(v, 1) for m, v in totals.items()}


def _issue(code, severity, ru, en, **extra) -> dict:
    row = {"code": code, "severity": severity, "detail_ru": ru, "detail_en": en}
    row.update({k: v for k, v in extra.items() if v is not None})
    return row


def audit_week(week, profile, catalog_map=None) -> list:
    """Проблемы недели: [{code, severity: high|medium|low, detail_ru, detail_en, muscle?, day_index?, slug?}].

    high — противопоказанное упражнение, крупная мышца без нагрузки при
    возможности её тренировать, тяга меньше половины жима, время больше плана на
    35%+, объём выше MRV при приоритете. medium — вне диапазона объёма, нет тяги
    нужного направления, изоляция раньше базовых, два тяжёлых упражнения на
    поясницу, нарушение пределов интенсивности ограничения, время +10%.
    """
    p = _norm_profile(profile)
    cat = _catalog_index(catalog_map)
    safety = _safety_for(p)
    equipment = available_equipment(p)
    limits = set(p["limitations"])
    ov = safety["overrides"]
    lvl = LEVEL_PARAMS[p["level"]]
    budget = SESSION_BUDGET[p["session_minutes"]]
    issues: list[dict] = []
    days = _week_days(week)

    muscle_days = {m: 0 for m in MUSCLES}
    push = pull = 0
    patterns_seen = set()
    strength_days = 0
    full_days = 0
    for day_no, day in enumerate(days, start=1):
        d_index = _to_int(day.get("day_index"), day_no) or day_no
        exercises = [i for i in _as_list(day.get("exercises")) if isinstance(i, dict)]
        seen_isolation = False
        order_flagged = False
        heavy_back = []
        per_muscle = {m: 0 for m in MUSCLES}
        day_frac = {m: 0.0 for m in MUSCLES}
        day_strength = 0
        for item in exercises:
            slug = item.get("slug")
            entry = cat.get(slug)
            if entry is None:
                issues.append(_issue("unknown_slug", "medium", f"Упражнения «{slug}» нет в каталоге",
                                     f"Exercise '{slug}' is not in the catalog", day_index=d_index, slug=slug))
                continue
            category = _get(entry, "category")
            muscle = _get(entry, "muscle_group")
            pattern = _guess_pattern(slug, entry)
            name_ru = _get(entry, "name_ru") or slug
            name_en = _get(entry, "name_en") or slug
            bad = _contra(entry) & limits
            if bad:
                issues.append(_issue("contraindicated", "high",
                                     f"«{name_ru}» противопоказано при ограничении: {', '.join(SAFETY[b]['name'][0] for b in sorted(bad))}",
                                     f"'{name_en}' is contraindicated for: {', '.join(SAFETY[b]['name'][1] for b in sorted(bad))}",
                                     day_index=d_index, slug=slug))
            elif slug in safety["exclude"] or pattern in safety["avoid"]:
                issues.append(_issue("not_recommended", "medium", f"«{name_ru}» не рекомендуется при ограничениях анкеты",
                                     f"'{name_en}' is not recommended with the profile's limitations", day_index=d_index, slug=slug))
            if _get(entry, "equipment") not in equipment:
                issues.append(_issue("equipment_unavailable", "medium", f"Для «{name_ru}» нет оборудования",
                                     f"No equipment for '{name_en}'", day_index=d_index, slug=slug))
            if _difficulty(entry) > lvl["max_difficulty"]:
                issues.append(_issue("too_difficult", "low", f"«{name_ru}» сложнее уровня анкеты",
                                     f"'{name_en}' is harder than the profile level", day_index=d_index, slug=slug))
            if category in ("compound", "isolation") and muscle in MUSCLES:
                day_strength += 1
                if ov.get("min_reps") and item.get("reps_min") and _to_int(item.get("reps_min"), 0) < ov["min_reps"]:
                    issues.append(_issue("intensity_limit", "medium",
                                         f"«{name_ru}»: при ограничениях анкеты не меньше {ov['min_reps']} повторов",
                                         f"'{name_en}': at least {ov['min_reps']} reps with the profile's limitations",
                                         day_index=d_index, slug=slug))
                if ov.get("max_rpe") and item.get("rpe") and _to_int(item.get("rpe"), 0) > ov["max_rpe"]:
                    issues.append(_issue("intensity_limit", "medium",
                                         f"«{name_ru}»: RPE не выше {ov['max_rpe']} (≥{10 - ov['max_rpe']} повт. в запасе)",
                                         f"'{name_en}': RPE at most {ov['max_rpe']} (≥{10 - ov['max_rpe']} reps in reserve)",
                                         day_index=d_index, slug=slug))
                if ov.get("max_hold_sec") and item.get("time_sec") and _to_int(item.get("time_sec"), 0) > ov["max_hold_sec"]:
                    issues.append(_issue("intensity_limit", "low", f"«{name_ru}»: удержание до {ov['max_hold_sec']} с",
                                         f"'{name_en}': hold up to {ov['max_hold_sec']} s", day_index=d_index, slug=slug))
                if category == "isolation" and muscle not in ("core", "calves"):
                    seen_isolation = True
                elif category == "compound" and seen_isolation and not order_flagged:
                    order_flagged = True
                    issues.append(_issue("isolation_before_compound", "medium",
                                         f"День {d_index}: изолирующее упражнение стоит раньше базового",
                                         f"Day {d_index}: an isolation exercise comes before a compound", day_index=d_index))
                sets = _to_int(item.get("sets"), 0) or 0
                per_muscle[muscle] += sets
                if pattern in PUSH_PATTERNS:
                    push += sets
                if pattern in PULL_PATTERNS:
                    pull += sets
                patterns_seen.add(pattern)
                if slug in HEAVY_LOWER_BACK:
                    heavy_back.append(slug)
                _add_sets(day_frac, entry, sets)
        for m, sets in per_muscle.items():
            # Частота: день засчитывается мышце, если она получила хотя бы 1 дробный подход.
            if day_frac[m] >= 1:
                muscle_days[m] += 1
            cap = lvl["session_sets_per_muscle_max"]
            if sets > cap + 4:
                issues.append(_issue("session_volume_high", "medium",
                                     f"День {d_index}: {MUSCLE_NAMES[m][0]} — {sets} подходов за тренировку (больше ~{cap} почти не добавляет)",
                                     f"Day {d_index}: {MUSCLE_NAMES[m][1]} — {sets} sets in one session (beyond ~{cap} adds little)",
                                     day_index=d_index, muscle=m))
            elif sets > cap:
                issues.append(_issue("session_volume_high", "low",
                                     f"День {d_index}: {MUSCLE_NAMES[m][0]} — {sets} подходов за тренировку",
                                     f"Day {d_index}: {MUSCLE_NAMES[m][1]} — {sets} sets in one session", day_index=d_index, muscle=m))
        if day_strength:
            strength_days += 1
        if len(heavy_back) >= 2:
            issues.append(_issue("lower_back_overload", "medium",
                                 f"День {d_index}: два тяжёлых упражнения на поясницу ({', '.join(heavy_back)})",
                                 f"Day {d_index}: two heavy lower-back exercises ({', '.join(heavy_back)})", day_index=d_index))
        limit_count = min(lvl["max_exercises"], budget["max_exercises"])
        if day_strength > limit_count + 3:
            issues.append(_issue("too_many_exercises", "medium", f"День {d_index}: {day_strength} упражнений — много для уровня и времени",
                                 f"Day {d_index}: {day_strength} exercises — too many for the level and time", day_index=d_index))
        elif day_strength > limit_count + 1:
            issues.append(_issue("too_many_exercises", "low", f"День {d_index}: {day_strength} упражнений",
                                 f"Day {d_index}: {day_strength} exercises", day_index=d_index))
        planned = (_to_int(day.get("duration_min"), p["session_minutes"]) or p["session_minutes"]) * 60
        planned = min(planned, p["session_minutes"] * 60) if p["session_minutes"] else planned
        est = estimate_day_seconds(day, cat)
        if day_strength and (est >= planned * 0.85 or day_strength >= limit_count):
            full_days += 1
        if est > planned * 1.35:
            sev = "high"
        elif est > planned * 1.10:
            sev = "medium"
        else:
            sev = None
        if sev:
            issues.append(_issue("time_over", sev, f"День {d_index}: ~{_rnd(est / 60)} мин вместо {_rnd(planned / 60)}",
                                 f"Day {d_index}: ~{_rnd(est / 60)} min instead of {_rnd(planned / 60)}", day_index=d_index))
        if not _as_list(day.get("warmup")):
            issues.append(_issue("missing_warmup", "low", f"День {d_index}: нет разминки", f"Day {d_index}: no warm-up", day_index=d_index))
        if not _as_list(day.get("cooldown")):
            issues.append(_issue("missing_cooldown", "low", f"День {d_index}: нет заминки", f"Day {d_index}: no cool-down", day_index=d_index))

    targets = volume_targets(p)
    totals = weekly_sets_by_muscle(days, cat)
    # Если все силовые дни уже заняты на ≥85% выбранного времени, нехватка объёма — следствие
    # времени или числа дней, а не ошибка схемы: такие замечания понижаем до low.
    time_limited = strength_days > 0 and full_days >= strength_days
    for m in MUSCLES:
        if not _trainable(m, p, cat, safety, equipment):
            continue
        sets = totals[m]
        name_ru, name_en = MUSCLE_NAMES[m]
        t = targets[m]
        if sets <= 0 and m in MAJOR_MUSCLES and strength_days:
            issues.append(_issue("volume_missing", "high", f"{name_ru.capitalize()}: нет ни одного подхода за неделю",
                                 f"{name_en.capitalize()}: no sets in the week", muscle=m))
        elif sets < t["min"]:
            limited_ru = " — не хватает времени или дней" if time_limited else ""
            limited_en = " — limited by time or days" if time_limited else ""
            issues.append(_issue("volume_low", "medium" if m in MAJOR_MUSCLES and not time_limited else "low",
                                 f"{name_ru.capitalize()}: {sets:g} подх./нед при минимуме {t['min']}{limited_ru}",
                                 f"{name_en.capitalize()}: {sets:g} sets/week, minimum {t['min']}{limited_en}", muscle=m))
        elif sets > VOLUME_LANDMARKS[m]["mrv_p"][1]:
            issues.append(_issue("volume_high", "high", f"{name_ru.capitalize()}: {sets:g} подх./нед — выше предела восстановления",
                                 f"{name_en.capitalize()}: {sets:g} sets/week — beyond recoverable volume", muscle=m))
        elif sets > t["max"] * 1.25:
            issues.append(_issue("volume_high", "medium", f"{name_ru.capitalize()}: {sets:g} подх./нед при максимуме {t['max']}",
                                 f"{name_en.capitalize()}: {sets:g} sets/week, maximum {t['max']}", muscle=m))
        elif sets > t["max"]:
            issues.append(_issue("volume_high", "low", f"{name_ru.capitalize()}: {sets:g} подх./нед при максимуме {t['max']}",
                                 f"{name_en.capitalize()}: {sets:g} sets/week, maximum {t['max']}", muscle=m))
        if sets > 0 and m in MAJOR_MUSCLES and strength_days >= 2 and muscle_days[m] == 1:
            issues.append(_issue("frequency_low", "low", f"{name_ru.capitalize()}: 1 раз в неделю — лучше дважды",
                                 f"{name_en.capitalize()}: once a week — twice is better", muscle=m))

    if strength_days:
        can_pull = {name: bool(_candidates(name, p, cat, set(), safety, equipment))
                    for name in ("horizontal_pull", "vertical_pull")}
        for name, ru, en in (("vertical_pull", "вертикальной тяги (подтягивания, тяга сверху)", "vertical pull (pull-ups, pulldowns)"),
                             ("horizontal_pull", "горизонтальной тяги (тяги к поясу)", "horizontal pull (rows)")):
            if name not in patterns_seen:
                sev = "medium" if can_pull[name] else "low"
                extra_ru = "" if can_pull[name] else " — нужен турник или резинка"
                extra_en = "" if can_pull[name] else " — needs a pull-up bar or band"
                issues.append(_issue(f"no_{name}", sev, f"Нет {ru}{extra_ru}", f"No {en}{extra_en}"))
        if push > 0 and pull < push:
            ratio = pull / push
            sev = "high" if ratio < 0.5 and any(can_pull.values()) else ("medium" if any(can_pull.values()) else "low")
            issues.append(_issue("pull_push_ratio", sev, f"Тяги {pull} подх. против жимов {push} — тяга должна быть не меньше",
                                 f"Pulling {pull} sets vs pressing {push} — pulling should be at least equal"))
    order = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda i: order.get(i["severity"], 3))
    return issues


# --------------------------------------------------------------------------- #
#  Тексты для ИИ и пользователя
# --------------------------------------------------------------------------- #
PROMPT_BRIEF_LIMIT = 3500


# Принципы, которые противоречат ограничению: при беременности хип-траст и мосты лёжа на спине
# исключены, и совет «хип-траст 2–3 раза в неделю» в промпте только сбивал бы модель.
PRINCIPLE_CONFLICTS = {"glute_training": {"pregnancy"}}


def _relevant_principles(p, limit=7) -> list:
    tags = {"all", f"goal:{p['goal']}", f"level:{p['level']}"} | {f"limit:{x}" for x in p["limitations"]}
    scored = []
    for index, row in enumerate(PRINCIPLES):
        if PRINCIPLE_CONFLICTS.get(row["id"], set()) & set(p["limitations"]):
            continue
        score = 0
        for tag in row["tags"]:
            if tag in tags:
                score += 1 if tag == "all" else 3
        if score:
            scored.append((-score, index, row))
    scored.sort()
    return [row for _, _, row in scored[:limit]]


def _range_text(pair) -> str:
    lo, hi = pair
    return f"{lo}–{hi}" if lo != hi else str(lo)


def _program_names(split) -> str:
    return ", ".join(FAMOUS_PROGRAMS[x]["name"] for x in split["inspired_by"][:3] if x in FAMOUS_PROGRAMS)


def prompt_brief(profile, lang="ru", catalog_map=None) -> str:
    """Компактная выжимка базы знаний для промпта генерации программы (≤ 3500 символов).

    Схема и пример недели под анкету, повторы/RIR/отдых цели, объём на мышцу,
    прогрессия и разгрузка, 6–8 принципов, безопасность ограничений.

    catalog_map — каталог, который ИИ видит в промпте (без исключённых пользователем
    упражнений). Пример недели собирается из него: иначе в примере могли бы оказаться
    упражнения, которых нет в списке «используй ТОЛЬКО эти slug». Без каталога —
    встроенный seed. У каждого упражнения примера в скобках — движение (паттерн
    слота): ИИ может заменить упражнение другим из каталога, но на то же движение.
    """
    p = _norm_profile(profile)
    en = _is_en(lang)
    split = select_split(p)
    rules = _rules(p)
    goal = GOAL_PARAMS[p["goal"]]
    lvl = LEVEL_PARAMS[p["level"]]
    targets = volume_targets(p)
    cat = _catalog_index(catalog_map)
    week = build_week(p, cat, lang)
    periodization = periodization_for(p)

    head = [("KNOWLEDGE BASE — follow it; deviate only for the catalog and limitations."
             if en else "БАЗА ЗНАНИЙ ТРЕНЕРА — следуй ей; отступай только из-за каталога и ограничений.")]
    names = _program_names(split)
    about = split["about_en"] if en else split["about_ru"]
    if en:
        head.append(f"Scheme: {split['name_en']} ({split['split_type']}, {split['days_per_week']} days)"
                    + (f", in the spirit of {names}" if names else "") + (f" — {about}." if about else "."))
    else:
        head.append(f"Схема: {split['name_ru']} ({split['split_type']}, {split['days_per_week']} дн.)"
                    + (f", в духе {names}" if names else "") + (f" — {about}." if about else "."))

    week_lines = [("Sample week for this profile; the movement pattern of each slot is in brackets — swap an exercise "
                   "only for another catalog exercise of the same pattern:") if en
                  else ("Пример недели под анкету; в скобках — движение (паттерн слота): упражнение можно заменить "
                        "только другим из каталога на то же движение:")]
    for day in week["days"]:
        parts = []
        for item in day["exercises"]:
            if item.get("time_sec") and not item.get("reps_min"):
                amount = f"{item['sets']}×{item['time_sec']}s" if en else f"{item['sets']}×{item['time_sec']}с"
                if item["sets"] == 1:
                    amount = f"{item['time_sec'] // 60} min" if en else f"{item['time_sec'] // 60} мин"
            else:
                amount = f"{item['sets']}×{item['reps_min']}–{item['reps_max']}"
            pattern = _guess_pattern(item["slug"], cat.get(item["slug"]) or item)
            parts.append(f"{item['slug']} ({pattern}) {amount}")
        label = "D" if en else "Д"
        week_lines.append(f"{label}{day['day_index']} {day['title']}: " + ", ".join(parts))

    reps = rules["reps"]
    rest = goal["rest_sec"]
    rir = goal["rir"]
    if en:
        goal_line = (f"Goal «{goal['name'][1]}»: compounds {_range_text(reps['main'])} reps (heavy day {_range_text(reps['heavy'])}, "
                     f"volume day {_range_text(reps['volume'])}), isolation {_range_text(reps['accessory'])}; reps in reserve: "
                     f"compounds {rir['compound'][0]}–{rir['compound'][1]}, isolation {rir['isolation'][0]}–{rir['isolation'][1]}"
                     f"{' +1 for beginners' if lvl['rir_extra'] else ''}; rest {_range_text(rest['compound'])} s / "
                     f"{_range_text(rest['isolation'])} s. Cardio: {goal['cardio']['en']}.")
    else:
        goal_line = (f"Цель «{goal['name'][0]}»: базовые {_range_text(reps['main'])} повт. (тяжёлый день {_range_text(reps['heavy'])}, "
                     f"объёмный {_range_text(reps['volume'])}), изоляция {_range_text(reps['accessory'])}; в запасе: базовые "
                     f"{rir['compound'][0]}–{rir['compound'][1]}, изоляция {rir['isolation'][0]}–{rir['isolation'][1]}"
                     f"{' (+1 новичку)' if lvl['rir_extra'] else ''}; отдых {_range_text(rest['compound'])} с / "
                     f"{_range_text(rest['isolation'])} с. Кардио: {goal['cardio']['ru']}.")

    vol_parts = []
    for m in MUSCLES:
        t = targets[m]
        vol_parts.append(f"{MUSCLE_NAMES[m][1 if en else 0]} {t['min']}–{t['max']}")
    volume_line = (("Working sets per muscle per week (direct 1, synergist 0.5): " if en
                    else "Рабочих подходов на мышцу в неделю (прямой 1, синергист 0,5): ") + ", ".join(vol_parts)
                   + (f"; per session ≤{lvl['session_sets_per_muscle_max']} direct sets per muscle." if en
                      else f"; за тренировку ≤{lvl['session_sets_per_muscle_max']} прямых подходов на мышцу."))

    model = PROGRESSION_MODELS[lvl["progression"]]
    phase_bits = []
    for row in periodization:
        phase_bits.append(f"{row['week']} {row['label_en'] if en else row['label_ru']}")
    deload = PHASE_GUIDE["deload"]
    if en:
        prog_line = (f"Progression: {model['name'][1]} — {model['en']} Periodization: {', '.join(phase_bits)}; "
                     f"deload = {deload['weight_pct']}% load, {deload['sets_delta']} set; RIR by phase base 3 → build 2 → peak 1.")
    else:
        prog_line = (f"Прогрессия: {model['name'][0]} — {model['ru']} Периодизация: {', '.join(phase_bits)}; "
                     f"разгрузка = {deload['weight_pct']}% веса, {deload['sets_delta']} подход; запас по фазам: база 3 → рост 2 → пик 1.")

    safety_lines = []
    if p["limitations"]:
        safety_lines.append("Limitations:" if en else "Ограничения:")
        for lim in p["limitations"]:
            safety_lines.append("- " + SAFETY_BRIEF[lim][1 if en else 0])
        doctor = [lim for lim in p["limitations"] if lim in RED_FLAGS_BRIEF and SAFETY[lim]["requires_doctor"]]
        for lim in doctor:
            safety_lines.append("- " + RED_FLAGS_BRIEF[lim][1 if en else 0])
        if doctor:
            safety_lines.append(("- In tips: remind to get the doctor's clearance and to stop on these red flags."
                                 if en else "- В советах напомни согласовать нагрузку с врачом и прекращать тренировку при этих признаках."))
    safety_lines.append(("Pain: 0–3/10 continue, 4–5 reduce load or range, >5 or sharp or worse next morning — replace the exercise."
                         if en else "Боль: 0–3/10 продолжать, 4–5 снизить вес или амплитуду, >5, острая или хуже наутро — заменить упражнение."))

    principle_lines = ["Principles:" if en else "Принципы:"]
    for row in _relevant_principles(p):
        principle_lines.append("- " + (row["brief_en"] if en else row["brief_ru"]))

    def assemble(principles, week_part):
        blocks = head + [goal_line, volume_line, prog_line] + safety_lines + week_part + principles
        return "\n".join(blocks)

    text = assemble(principle_lines, week_lines)
    while len(text) > PROMPT_BRIEF_LIMIT and len(principle_lines) > 4:
        principle_lines.pop()
        text = assemble(principle_lines, week_lines)
    while len(text) > PROMPT_BRIEF_LIMIT and len(week_lines) > 1:
        week_lines.pop()
        text = assemble(principle_lines, week_lines)
    if len(text) > PROMPT_BRIEF_LIMIT:
        text = text[:PROMPT_BRIEF_LIMIT].rsplit("\n", 1)[0]
    return text


# Как назвать источник схемы в строке для пользователя («в духе …»).
METHOD_NAMES = {
    "contreras_glutes": ("программ Бретта Контрераса", "Bret Contreras's programs"),
    "helms_pyramid": ("программ Эрика Хелмса", "Eric Helms's programs"),
    "nippard_fundamentals": ("Fundamentals Джеффа Ниппарда", "Jeff Nippard's Fundamentals"),
    "nippard_minmax": ("Min-Max Джеффа Ниппарда", "Jeff Nippard's Min-Max"),
    "rp_mesocycle": ("мезоциклов RP", "RP mesocycles"),
    "reddit_ppl": ("Reddit PPL", "Reddit PPL"),
    "bwf_rr": ("Recommended Routine", "the Recommended Routine"),
    "wendler_531": ("5/3/1 Джима Вендлера", "Jim Wendler's 5/3/1"),
    "nuckols_beginner": ("программы Грега Наколса", "Greg Nuckols's program"),
}


def _days_word_ru(n) -> str:
    return "день" if n % 10 == 1 and n % 100 != 11 else ("дня" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "дней")


def _split_frequency(split) -> tuple:
    counts = []
    for muscle in ("chest", "back", "quads"):
        n = sum(1 for day in split["days"]
                if any(PATTERNS.get(s["pattern"], {}).get("muscle") == muscle for s in day["slots"]))
        if n:
            counts.append(n)
    return (min(counts), max(counts)) if counts else (0, 0)


def method_tip(profile, lang="ru") -> str:
    """Одна строка для пользователя: на чём основана программа."""
    p = _norm_profile(profile)
    en = _is_en(lang)
    split = select_split(p)
    goal = GOAL_PARAMS[p["goal"]]
    lvl = LEVEL_PARAMS[p["level"]]["weekly_sets"]
    mult = goal["volume_mult"]
    lo, hi = _rnd(lvl["min"] * mult), _rnd(lvl["max"] * mult)
    reps = _rules(p)["reps"]["main"]
    rir = goal["rir"]["compound"]
    f_lo, f_hi = _split_frequency(split)
    days = split["days_per_week"]
    first = split["inspired_by"][0] if split["inspired_by"] else None
    first_name = ""
    if first:
        first_name = _t(METHOD_NAMES[first], lang) if first in METHOD_NAMES else FAMOUS_PROGRAMS[first]["name"]
    cardio = p["goal"] in ("loss", "endurance")
    if en:
        freq = ("each muscle once a week" if f_hi <= 1 else "each muscle twice a week" if f_lo == f_hi == 2
                else f"each muscle {f_hi}× a week" if f_lo == f_hi else f"each muscle {max(1, f_lo)}–{f_hi}× a week")
        text = (f"“{split['name_en']}”, {days} days" + (f" in the spirit of {first_name}" if first_name else "")
                + f": {freq}, {lo}–{hi} working sets per muscle, {reps[0]}–{reps[1]} reps on compounds with {rir[0]}–{rir[1]} in reserve"
                + ("; plus cardio 150–300 min a week" if cardio else "") + ".")
    else:
        times = lambda n: "раза" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "раз"
        freq = ("каждая мышца раз в неделю" if f_hi <= 1 else "каждая мышца дважды в неделю" if f_lo == f_hi == 2
                else f"каждая мышца {f_hi} {times(f_hi)} в неделю" if f_lo == f_hi
                else f"каждая мышца {max(1, f_lo)}–{f_hi} {times(f_hi)} в неделю")
        text = (f"Схема «{split['name_ru']}», {days} {_days_word_ru(days)}" + (f" в духе {first_name}" if first_name else "")
                + f": {freq}, {lo}–{hi} рабочих подходов на мышцу, {reps[0]}–{reps[1]} повторов в базовых, {rir[0]}–{rir[1]} в запасе"
                + ("; плюс кардио 150–300 мин в неделю" if cardio else "") + ".")
    return text
