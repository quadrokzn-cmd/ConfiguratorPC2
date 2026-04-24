# Этап 2.5Б — разведка whitelist и объёма работ

**Дата:** 2026-04-24
**Источник:** SQL-запросы к `kvadro_tech` + статический анализ кода

## 1. Где лежит whitelist

Единственный источник белого списка доменов в проекте:

```
app/services/enrichment/claude_code/schema.py
```

Константа `OFFICIAL_DOMAINS: frozenset[str]` (строки 79–143).
Дополнительно — справочник полей `TARGET_FIELDS` (строки 17–46).

Используется:
- `app/services/enrichment/claude_code/validators.py` — функция
  `_validate_source_url()` допускает URL только если хост = домен из whitelist
  или его поддомен.
- `app/services/enrichment/openai_search/client.py` — тот же whitelist
  передаётся в system-prompt OpenAI (поле `whitelist_domains`).
- `app/services/enrichment/openai_search/schema.py` — импорт для внутренних нужд.

Никакие `whitelist.py`, `brands.json`, `official_sites.yml` в проекте не
найдены — это единственный актуальный источник истины.

## 2. Whitelist как есть: 65 доменов

Группировка по роли (из комментариев в `schema.py`):

| Группа | Домены |
|---|---|
| GPU-чипмейкеры | nvidia.com, amd.com, intel.com |
| GPU AIB-партнёры | asus.com, msi.com, gigabyte.com, aorus.com, asrock.com, palit.com, zotac.com, pny.com, biostar.com.tw, matrox.com, afox.eu, afox.ru |
| Материнские платы | supermicro.com |
| Кулеры | thermalright.com, arctic.de, arctic.ac, noctua.at, corsair.com, deepcool.com, bequiet.com, coolermaster.com, alseye.com |
| Корпуса | jonsbo.com, fractal-design.com, lian-li.com, nzxt.com, phanteks.com, thermaltake.com, chenbro.com, aerocool.io, montechpc.com, azza.com.tw, aicipc.com |
| БП/PoE | seasonic.com, zalman.com, chieftec.com, chieftec.eu, ubnt.com, ui.com, ubiquiti.com, cisco.com |
| SBC | raspberrypi.com, radxa.com, orangepi.org |
| Прочее | hp.com, lenovo.com |
| РФ-сборщики | fox-line.ru, formula-pc.ru, accord-pc.ru, kingprice.ru, acd-group.com |

## 3. Фактические цифры по БД на 2026-04-24 (расходятся с ТЗ)

### 3.1. Позиции с ≥1 NULL-полем из TARGET_FIELDS категории

| Категория | Всего | С NULL-полями (скелет) | Пояснение |
|---|---|---|---|
| gpu         |  495 | **255** | ПЛАСТ 1 (66) + ПЛАСТ 2 слились, т.к. 5 системных полей NULL у большинства |
| cooler      |  716 | **228** | Только `max_tdp_watts` (ПЛАСТ 2); socket-поля в 2.5Б не входят |
| case        |  896 |  **61** | ПЛАСТ 1 (≈60) — поле `has_psu_included`=7 NULL, `supported_form_factors`=60 NULL |
| storage     |  813 |  **10** | |
| psu         |  635 |   **5** | |
| motherboard |  658 |   **2** | |
| cpu         |  159 |   **0** | |
| **Итого**   |      | **≈561** | |

### 3.2. Детализация по полям (ПЛАСТ 1 и ПЛАСТ 2 в сумме)

| Категория.поле | NULL | Всего |
|---|---|---|
| gpu.tdp_watts | 227 | 495 |
| gpu.needs_extra_power | 227 | 495 |
| gpu.video_outputs | 238 | 495 |
| gpu.core_clock_mhz | 252 | 495 |
| gpu.memory_clock_mhz | 228 | 495 |
| gpu.vram_gb | 3 | 495 |
| gpu.vram_type | 6 | 495 |
| case.has_psu_included | 7 | 896 |
| case.supported_form_factors | 60 | 896 |
| case.included_psu_watts (только has_psu=TRUE) | **3** | 79 |
| cooler.max_tdp_watts | 228 | 716 |
| motherboard.memory_type | 2 | 658 |
| motherboard.has_m2_slot | 2 | 658 |
| psu.power_watts | 5 | 635 |
| storage.form_factor | 5 | 813 |
| storage.interface | 8 | 813 |
| storage.capacity_gb | 4 | 813 |
| cpu.* | 0 | 159 |

**Итого полей на заполнение:** ≈1505 (GPU 1181 + CASE 70 + COOLER 228 +
MB 4 + PSU 5 + STORAGE 17).

### 3.3. Расхождение с ТЗ

| Цифра | ТЗ | Фактически |
|---|---|---|
| case остаточных скелетов (ПЛАСТ 1) | 432 | ~60 |
| case.included_psu_watts NULL (ПЛАСТ 2) | 820 | **3** (только среди has_psu=TRUE) |
| cooler max_tdp_watts NULL | 232 | 228 |
| gpu 5 системных полей NULL (каждое) | 227–252 | 227–252 ✅ |

Комментарий: `case.included_psu_watts=820` в ТЗ — это счёт по всей таблице
(включая has_psu=FALSE/NULL). По дизайну экспорта (`_build_select_sql` в
`exporter.py`) для `case_psu_pass` выбираются только позиции с
`has_psu_included=TRUE`. В БД таких всего 79, из них 3 с NULL в
`included_psu_watts` — объём работы по этому полю минимальный.

Для case ПЛАСТ 1 расхождение объясняется тем, что regex-этап уже заполнил
`has_psu_included` (остался 7 NULL) и `supported_form_factors` (остался
60 NULL) у большинства позиций (в т.ч. DIY-корпуса получили
`has_psu_included=FALSE` через derived-правило). Реально осталось
~60 корпусов к обогащению.

**Предлагаю не считать это блокирующим расхождением** (СТОП-ПОЙНТ №3):
системные поля GPU совпадают с ожиданиями и дают основной объём работ.

## 4. Распределение брендов по скелетам (по позициям с ≥1 NULL-полем)

Всего **51 уникальное написание бренда**, 14 отсутствуют в whitelist.
Дубликаты регистра (ASUS/Asus, PALIT/Palit, THERMALRIGHT/Thermalright)
покрывает общий whitelist (сверка по строчным буквам выполняется в
`_validate_source_url`), нормализация имени бренда не требуется.

### 4.1. Бренды в whitelist (покрыты)

| Бренд (все варианты) | Количество позиций | Домен из whitelist |
|---|---|---|
| ASUS | 45 | asus.com |
| MSI | 45 | msi.com |
| AFOX CORPORATION / AFOX | 45 + 6 = 51 | afox.eu / afox.ru |
| ACD Systems | 44 | acd-group.com (российский сборщик корпусов для Raspberry/Rock Pi) |
| Thermalright / THERMALRIGHT | 30 + 24 = 54 | thermalright.com |
| Zalman / ZALMAN | 25 + 4 = 29 | zalman.com |
| PALIT / Palit | 17 + 11 = 28 | palit.com |
| NVIDIA | 14 | nvidia.com |
| ARCTIC | 13 | arctic.de / arctic.ac |
| ASROCK | 11 | asrock.com |
| LIAN-LI / Lian Li | 10 + 1 = 11 | lian-li.com |
| Deepcool | 9 | deepcool.com |
| BIOSTAR / Biostar Microtech Netherlands B.V. | 9 + 6 = 15 | biostar.com.tw |
| GIGABYTE / Gigabyte | 8 + 3 = 11 | gigabyte.com |
| HP | 7 | hp.com (фан-киты HPE ProLiant — но сайт компании уже hpe.com) ⚠ |
| Raspberry Pi Foundation | 6 | raspberrypi.com |
| AIC | 6 | aicipc.com |
| Corsair | 5 | corsair.com |
| Chenbro | 3 | chenbro.com |
| PNY | 3 | pny.com |
| Matrox | 3 | matrox.com |
| Lenovo | 2 | lenovo.com |
| ALSEYE CORPORATION LIMITED | 2 | alseye.com |
| ZOTAC | 1 | zotac.com |
| Ubiquiti | 1 | ubiquiti.com / ubnt.com / ui.com |
| Cisco | 1 | cisco.com |
| Thermaltake / THERMALTAKE | 1 + 1 = 2 | thermaltake.com |

### 4.2. Бренды с неочевидным маппингом

| Бренд | Позиций | Комментарий |
|---|---|---|
| Formula V | 6 | «Formula V Line» — российский OEM. В whitelist есть `formula-pc.ru` — нужно подтвердить, что это их домен. Компоненты: корпусные вентиляторы AIR FUSION, кулер Air Frost Plus, Verkho 2. |
| RockPi | 6 | Rock Pi — линейка одноплатных компьютеров Radxa. Корпуса для них делают разные OEM. Для данных позиций `manufacturer=RockPi` фактически означает «корпус для Rock Pi» — характеристики относятся к корпусу, а не к плате. Официальный сайт серии — **radxa.com** (уже в whitelist). |
| HP (HPE) | 7 | Все 7 позиций — фан-киты HPE ProLiant (Gen10/11). Сайт HPE — `hpe.com`, в whitelist только `hp.com`. Стоит добавить **hpe.com** отдельно. |

### 4.3. Бренды ОТСУТСТВУЮТ в whitelist

Всего **14 брендов** / **72 + 13 + 11 + 9 + 8 + 4 + 3 + 2 + 2 + 1 + 1 + 1 = 127
позиций** не смогут быть обогащены без расширения whitelist.

| # | Бренд | Позиций | Предлагаемый домен | Обоснование |
|---|---|---|---|---|
| 1 | **Ocypus** | 72 | `ocypus.com` | Ocypus Gamma / Iota серии корпусов и БП. Крупнейший кейс (72 позиции). |
| 2 | **MAXSUN** | 13 | `maxsun.com` / `maxsun.com.cn` | AIB-партнёр NVIDIA (китайский). Компоненты из прайса Merlion. |
| 3 | **ID-Cooling** | 11 | `idcooling.com` | Производитель кулеров SE-214-XT, серия SE/AF. |
| 4 | **SAPPHIRE** | 9 | `sapphiretech.com` | AIB-партнёр AMD. Крупный производитель Radeon-карт. |
| 5 | **INNO3D** | 8 | `inno3d.com` | AIB-партнёр NVIDIA. Серии Twin X2, Ichill. |
| 6 | **Netac** | 4 | `netac.com` | SSD: N600S, N5M, Z9. Прайс Merlion. |
| 7 | **FSP GROUP** | 3 | `fsp-group.com` или `fsplifestyle.com` | БП и адаптеры FSP. Аутентичный сайт FSP Group — fsp-group.com (корпоративный) и fsplifestyle.com (потребительский). |
| 8 | **Seagate** | 2 | `seagate.com` | HDD Seagate. |
| 9 | **PCCOOLER / PCCooler** | 1 + 1 = 2 | `pccooler.com.cn` | БП KF550, YS1200. Китайский производитель. |
| 10 | **Apacer** | 1 | `apacer.com` | SSD. |
| 11 | **Kingston** | 1 | `kingston.com` | SSD, аксессуары. |
| 12 | **IN WIN** | 1 | `in-win.com` | Корпус InWin IW-RS436-07. |
| 13 | **WD** | 1 | `westerndigital.com` | HDD/SSD Western Digital. |
| 14 | **HPE** (hpe.com) | 7 (учтены выше) | `hpe.com` | Фан-киты для серверов HPE ProLiant. `hp.com` не покрывает субдомены `hpe.com`. |

## 5. Предложение для расширения whitelist

Добавить в `OFFICIAL_DOMAINS` в `app/services/enrichment/claude_code/schema.py`:

```python
# AIB-партнёры (дополнительно)
"maxsun.com",
"maxsun.com.cn",
"sapphiretech.com",
"inno3d.com",
# Кулеры (дополнительно)
"idcooling.com",
"pccooler.com.cn",
# Корпуса / серверы
"ocypus.com",
"in-win.com",
"hpe.com",
# БП
"fsp-group.com",
"fsplifestyle.com",
# Накопители
"netac.com",
"seagate.com",
"kingston.com",
"westerndigital.com",
"apacer.com",
```

После добавления whitelist станет **65 + 16 = 81 доменов**, покрытие
брендов скелетов вырастет с 37/51 до 51/51 (100%).

## 6. Подтверждение по «Formula V»

В whitelist уже есть `formula-pc.ru`. Позиции с `manufacturer='Formula V'`
в БД — это «Formula V Line» (вентиляторы, кулеры, корпусы). Домен
`formula-pc.ru` действительно является официальным сайтом бренда
Formula V Line (они позиционируют его именно так). Подтвердите,
что этот домен корректен для Formula V.

## 7. Что требую подтвердить

1. **Расширить whitelist до 81 домена** по списку в §5?
   Или скорректировать список (какие-то домены отклонить / заменить)?
2. **Formula V Line = formula-pc.ru** — подтверждаете?
3. **RockPi → radxa.com** — принять маппинг (radxa.com уже в whitelist,
   добавлять ничего не нужно)?
4. Сильное расхождение по case (ТЗ 432, фактически 60) — продолжать или
   остановиться и разобраться, в чём логическое различие?

Без подтверждения этих 4 пунктов я не могу начать шаг 1 (целевая выборка)
и запустить параллельных агентов — агент просто отклонит URL с доменом,
которого нет в whitelist, а 127 позиций без подтверждения останутся
unresolved по причине «whitelist miss».
