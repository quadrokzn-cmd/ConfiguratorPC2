# Отчёт: regex-обогащение скелетов Merlion/Treolan

**Дата:** 2026-04-24  
**Микроэтап:** regex-обогащение ~2076 компонентов, созданных при загрузке
прайсов Merlion (supplier_id=5) и Treolan (supplier_id=6) после Этапа 7.  
**OpenAI не используется** (OPENAI_ENRICH_AUTO_HOOK=false) — только regex.

## 1. Сводная таблица

| Категория   | Всего | Скелетов | Обогащено (≥1 поле) | Осталось | % покрытия |
|-------------|-------|----------|---------------------|----------|------------|
| cpu         | 159   | 0        | 0                   | 0        | — (нет скелетов) |
| motherboard | 658   | 2        | 0                   | 2        | — (единичные) |
| ram         | 744   | 0        | 0                   | 0        | — (нет скелетов) |
| psu         | 635   | 307      | **302**             | 5        | 98.4%      |
| storage     | 813   | 243      | **234**             | 9        | 96.3%      |
| cooler      | 716   | 561      | **425**             | 136      | 75.8%      |
| gpu         | 495   | 255      | **189**             | 66       | 74.1%      |
| case        | 896   | 839      | **407**             | 432      | 48.5%      |
| **Итого**   | 5116  | **2207** | **1557**            | 650      | 70.6%      |

Число в «Осталось» = скелет, у которого после прогона ещё есть хотя бы
одно NULL-поле среди ключевых. Для cooler/gpu/case часть этих скелетов
всё же обогатились — частично, но не во всех полях.

## 2. Покрытие по отдельным полям (финальные цифры)

| Категория | Поле                      | Покрытие | Примечание                                  |
|-----------|---------------------------|----------|---------------------------------------------|
| psu       | power_watts               | 98.4%    | — |
| storage   | storage_type              | 95.5%    | — |
|           | form_factor               | 96.3%    | + «2,5"», «2.5 SATA», mSATA |
|           | interface                 | 95.5%    | — |
|           | capacity_gb               | 95.1%    | + русские Гб / Тб |
| cooler    | supported_sockets         | 69.3%    | + разбор «Soc-AM5/AM4/1200/…» и «LGA1851/1700/…» |
|           | max_tdp_watts             | 59.1%    | + derived по размеру радиатора AIO (+67 поз.) |
| gpu       | vram_gb                   | 73.7%    | + «4Gb» с малой b (Afox/Biostar/Gigabyte) |
|           | vram_type                 | 68.6%    | + сокращение «D6»/«D7»/«D6X» → GDDR6/GDDR7/… |
|           | tdp_watts и т.д. (5 полей)| 0.0%     | Системно отсутствуют в прайсе — уйдёт в 2.5Б |
| case      | supported_form_factors    | 44.9%    | Часть корпусов без формфактора в имени |
|           | has_psu_included          | 48.5%    | + derived=False для DIY-корпусов |
|           | included_psu_watts        | 1.7%     | Мощность БП в имени указывается редко |

## 3. Добавленные / расширенные regex-паттерны

### 3.1. `gpu.py`
1. **Объём VRAM с малой буквой «b» — `Gb`.**  
   Было: `\b(\d+)\s*GB\b` — только верхний регистр.  
   Стало: `\b(\d+)\s*G[Bb]\b` — поддержка Merlion-SKU типа «Afox 4Gb GDDR5».
2. **Сокращение типа VRAM «D6»/«D7»/«D6X» → GDDR6/GDDR7/GDDR6X.**  
   Новая регулярка `(?:,|\s)\s*D(\d)(X?)(?=[\s,;]|$)` срабатывает, когда
   в строке нет явного «GDDR6/GDDR7», но в конце идёт «…8G, D7» или
   «16G,D6» (формат MAXSUN/INNO3D/ASUS). Source = «regex», confidence = 0.9.

### 3.2. `cooler.py`
3. **Derived `max_tdp_watts` по размеру радиатора AIO.**  
   Маркеры: «Система водяного охлаждения», «AIO», «Liquid cooling», «pump»,
   «СВО». Размеры радиатора: 120 / 140 / 240 / 280 / 360 / 420 мм.  
   Derived-значения (консервативные):
   | Радиатор | TDP |
   |----------|-----|
   | 120 мм   | 150W |
   | 140 мм   | 180W |
   | 240 мм   | 200W |
   | 280 мм   | 250W |
   | 360 мм   | 300W |
   | 420 мм   | 400W |

   Для корректного ловления внутримодельных обозначений вроде «LM420»
   используется `(?<!\d)(120|140|…|420)\b` — lookbehind «не цифра»
   вместо `\b` слева (между буквой и цифрой Python `\b` не срабатывает).
4. **Merlion-разбор цепочки сокетов «Soc-AM5/AM4/1200/1700/1851».**  
   Старая регулярка `Soc-(\d{3,4}(?:/\d{3,4})*)` работала только для
   чисто числовых цепочек («Soc-1151/1200»). Новая
   `Soc-([A-Za-z0-9+/]+)` захватывает и смешанные AM/LGA цепочки.
5. **Разбор «LGA1851/1700/1200/115X/AM5/AM4» (без префикса LGA у
   продолжения цепочки).**  
   Новая регулярка `_LGA_CHAIN_RE` развёртывает цепочку, первый
   элемент префиксуется LGA по сериализатору, числовые продолжения тоже
   получают LGA через `_normalize_socket`.

### 3.3. `storage.py`
6. **Форм-фактор 2.5" с русской запятой «2,5"» и типографскими кавычками.**  
   Регулярка `2[.,]5\s*(?:"+|''|[”’″])` принимает точку/запятую и любую
   кавычку (прямая/типографская).
7. **Форм-фактор 2.5" без кавычек перед SATA.**  
   Паттерн `\b2[.,]5\s+SATA` — Netac пишет «128GB 2.5 SATAIII 3D NAND».
8. **Форм-фактор mSATA** — новая ветка `_FF_MSATA = r"\bmSATA\b"`,
   нормализуется как «mSATA».
9. **Русские единицы ёмкости «Гб/Тб/ГБ/ТБ».**  
   Regex capacity_gb теперь `(\d+(?:\.\d+)?)\s*(TB|GB|Тб|Гб|ТБ|ГБ)`.
   `unit.upper()` даёт одинаковое значение для кириллических и
   латинских единиц (`.upper()` работает и на кириллице).

### 3.4. `case.py`
10. **Derived `has_psu_included = False` для DIY-корпусов.**  
    Срабатывает, когда:
    - `supported_form_factors` определился (хотя бы один ATX/mATX/ITX/E-ATX);
    - в имени нет явных «без БП» / «с БП» / мощности БП в ваттах.  
    Обоснование: в Merlion-прайсе у Formula V Line, Ocypus, Zalman,
    Deepcool, Cooler Master современные mATX/ATX корпусы
    поставляются без БП в подавляющем большинстве случаев. Source =
    «derived», confidence = 0.7.

## 4. Примеры «до → после»

### PSU (power_watts)
| Name | До | После |
|------|----|-------|
| «Formula V FV-400LT, 400W, APFC, 12cm Fan» | NULL | 400 |
| «Seasonic ATX 1200W Vertex GX-1200» | NULL | 1200 |
| «PCCooler YS1200, 1200W, ATX3.1» | NULL | 1200 |
| «Ocypus Gamma P850, 850W» | NULL | 850 |
| «Deepcool PN850M (ATX 3.1, 850W…)» | NULL | 850 |

### STORAGE
| Name | До | После |
|------|----|-------|
| «GS Nanotech SSD GS027 512Гб PCIe 3 x4» | capacity=NULL | **512** |
| «WD Green SSD 2,5" SATA 1Tb» | form_factor=NULL | **2.5"** |
| «Netac SSD N600S 128GB 2.5 SATAIII» | form_factor=NULL | **2.5"** |
| «Netac N5M 2TB mSATA SATAIII» | form_factor=NULL | **mSATA** |
| «PC Pet PCIe 4.0 x4 4TB M.2 2280» | form_factor=NULL | **M.2**, capacity=4000 |

### COOLER (max_tdp_watts)
| Name | До | После |
|------|----|-------|
| «Система водяного охлаждения Deepcool LM420 ARGB…» | NULL | **400** (derived 420mm) |
| «Lian-Li Galahad II LCD 360 ARGB Soc-AM5/AM4/1700/1851…» | NULL | **300** (derived 360mm) |
| «Arctic Freezer II 240 AIO Liquid cooling» | NULL | **200** (derived 240mm) |
| «Thermaltake MAGFloe 420 Ultra Snow ARGB … 360W» | NULL | **360** (regex по W) |
| «ID-COOLING SE-214-XT V2 BLACK LGA1851/1700/1200/115X/AM5/AM4 (TDP 200W…)» | sockets=NULL | **{LGA1851, LGA1700, LGA1200, LGA115X, AM5, AM4}**, TDP=200 |

### GPU (vram_gb, vram_type)
| Name | До | После |
|------|----|-------|
| «MSI RTX 5060 Ti 16G INSPIRE 2X OC//RTX 5060Ti, …, 16G, D7» | vram_type=NULL | **GDDR7** |
| «INNO3D RTX 5060 Twin X2//RTX5060, …, 8G,D6» | vram_type=NULL | **GDDR6** |
| «Afox AF730-4096D3L6 NVIDIA GT 730 4Gb 128bit GDDR3» | vram_gb=NULL | **4** |
| «Asrock B570 CL 10GO INTEL ARC B570 10Gb GDDR6» | vram_gb=NULL | **10** |
| «ASUS PROART-RTX5070TI-O16G NVIDIA RTX 5070TI 16Gb» | vram_gb=NULL | **16** |

### CASE (has_psu_included)
| Name | До | После |
|------|----|-------|
| «Formula V Line CS-110-S mATX USB3.0x1/…» | has_psu=NULL | **False (derived)** |
| «Ocypus Gamma C50 BK, MATX, USB3.0*1+USB2.0*2» | has_psu=NULL | **False (derived)** |
| «ZALMAN T8, ATX, BLACK, …» | has_psu=NULL | **False (derived)** |
| «Bloody CC-121 белый без БП mATX» | has_psu=NULL | False (regex, уже было) |
| «InWin ENR022 Black 500W PM-500ATX mATX» | has_psu=NULL | True + 500W |

## 5. Что не обогатилось и почему

### PSU (5 осталось)
Редкие случаи: имя не содержит числа-мощности или мощность < 100W (PoE-
инжекторы, ошибочно попавшие в psus).

### STORAGE (9 осталось)
Короткие имена типа «Kingston Brackets and Screws 2.5" to 3.5" (кронштейн-
адаптер)» — это не диск, а монтажный аксессуар. Правильно, что он остался
с NULL.

### COOLER (136 осталось)
- Корпусные вентиляторы (Ocypus Delta EH10 BK — это хаб, а не CPU-кулер).
- Тауэр-кулеры без указания TDP в названии: Thermalright Peerless Assassin,
  Phantom Spirit, Royal Knight и др. — TDP определяется обзорами, а не
  прайсом. Derived по «120/140 мм» для воздушных кулеров не добавлял —
  слишком хрупко (размер относится к вентилятору, а не к радиатору).
- Серверные фан-киты (HPE, Dell) — сокеты здесь не применимы.

### GPU (66 осталось)
- 5 полей (tdp_watts, needs_extra_power, video_outputs, core_clock_mhz,
  memory_clock_mhz) системно отсутствуют в прайсовых наименованиях.
  Они уйдут в этап 2.5Б — AI-обогащение по чипу GPU.
- Единичные карты с ёмкостью в Mb (Afox G210 512Mb = 0.5 GB) — не
  пишем vram_gb=0 или 1.

### CASE (432 осталось)
- 60 корпусов не имеют формфактора в имени (редкие XL-ATX / серверные
  NAS/rackmount / корпусы типа «Mini Tower» без явного ATX/mATX).
  Без формфактора derived `has_psu_included` не срабатывает (по задумке).
- Оставшиеся имеют ATX/mATX, но мощность БП в имени указывается редко —
  это нормально, `included_psu_watts` без явной мощности мы не выдумываем.

### MOTHERBOARD (2 осталось)
Две материнки Afox (AFB250-BTC12EX, AFHM65-ETH8EX) — очень специфичные
плат для майнинга, с нестандартными чипсетами (HM65, B250). Для них
справочник чипсет→сокет не содержит записей.

## 6. Рекомендации по следующему подэтапу

1. **GPU 5 полей (tdp_watts, needs_extra_power, …)** — сразу идут в 2.5Б
   (AI-обогащение). Это **227–252 позиций на каждое поле** — основной
   фронт работы следующего этапа.
2. **CASE supported_form_factors** (60 позиций) + **included_psu_watts**
   (826 позиций) — перешлите в AI-этап или в ручное сопоставление через
   `/admin/mapping` (если позиции важны для каталога).
3. **COOLER max_tdp_watts** (232 позиции) — для воздушных тауэр-кулеров
   без TDP в имени; имеет смысл закинуть в AI-этап либо добавить
   справочник «модель → TDP» для топовых моделей (Peerless Assassin,
   Phantom Spirit — 245W, Dark Rock — 250W, и т. п.).
4. **Motherboard 2 позиции** — вручную через `/admin/components/<id>`.

## 7. Артефакты

| Файл | Назначение |
|------|------------|
| `scripts/reports/skeletons_survey.md` | Отчёт о разведке до обогащения |
| `scripts/reports/skeletons_backup_20260424.sql` | Бэкап NULL-значений полей (для отката) |
| `scripts/reports/skeletons_enrichment_report.md` | Этот отчёт |
| `scripts/diag_uncovered.py` | Диагностический скрипт: находит скелеты, для которых regex не дал значений |
| `scripts/backup_skeletons.py` | Генератор бэкапа (можно прогонять повторно перед будущими изменениями) |

## 8. Тесты

- **27 unit-тестов** на новые regex-паттерны → `tests/test_enrichment_regex.py`
- **5 интеграционных** на 20 реальных наименований прайса → `tests/test_enrichment_integration.py`
- **До:** 384 passed + 1 skipped  
- **После:** 416 passed + 1 skipped (+32 новых)
