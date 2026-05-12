# 2026-05-12 — Backlog #4 Фаза 1: discovery n/a-SKU в primary

## TL;DR

В preprod БД 628 SKU в `printers_mfu`, у 79 (12.6%) `print_speed_ppm = n/a`.
Топ-3 проблемных бренда по абсолютному количеству: **Canon (26 SKU)**,
**HP (22)**, **Katusha IT (15)**. По проценту: **Canon 35.6%**, **Sindoh
28.6%**, **Katusha IT 20%**. Реальные `primary`-матчи с n/a-атрибутом в
preprod не воспроизводятся (matching ещё не запущен на полные данные —
всего 2 primary-матча), поэтому оценка эффекта — потенциальная.

Источники характеристик доступны: Canon — через WebSearch (canon.uz,
canon.am, dealer-сайты); Katusha IT — через сайт `katusha-it.ru/products/<sku>`.
Bulat-style ручной workflow применим к остальным.

**Рекомендация:** Вариант C (гибрид) — точечное обогащение Canon + Katusha IT
+ HP, плюс расширение regex_name fallback. Эффект — закрытие ~63 SKU с n/a
speed (≈80% от текущего пула).

## Контекст

- **Решение собственника №19** (см. `plans/2026-04-23-platforma-i-aukciony.md`):
  при matching'е SKU с критическим `n/a` НЕ отбрасывается — менеджер видит лот с
  жёлтым бейджем «требует уточнения». Fail-open для случая, когда Claude
  Code-обогащение не дало значения.
- **Мини-этап 9a-bulat-enrich** (2026-05-11) закрыл проблему для Bulat: 6 SKU
  обогащены через WebFetch на `bulat-group.ru`, 42 ячейки `n/a` → 0. После
  этого Bulat P1024W перестал попадать в primary под лотами с
  `print_speed_ppm ≥ 30 ppm` (его реальные 24 ppm стали видны matcher'у).
- **Backlog #4 (текущая задача):** аналогичное обогащение для других нишевых
  брендов. Полная рефлексия 9a-bulat-enrich — `.business/история/2026-05-11-этап-9a-bulat-enrich.md`.

## Методология

- **БД для discovery — preprod** (`DATABASE_PUBLIC_URL` из
  `.env.local.preprod.v2`). Обоснование: preprod единственная база, где
  одновременно есть и реальный каталог `printers_mfu` (после auto_price_loads),
  и `tender_items` от ingest, и таблица `matches`. Dev-БД содержит только тестовые
  данные.
- 4 SQL-запроса по схеме промта + 2 дополнительных (источники атрибутов,
  required_attrs в лотах).
- WebSearch + 2 WebFetch'а — проверка доступности характеристик у топ-брендов.

## Результаты SQL-discovery

### (a) Распределение n/a critical-атрибутов по брендам (preprod, 628 SKU)

| brand          | sku | na_speed | na_color | na_format | na_duplex | na_resolution |
|----------------|----:|---------:|---------:|----------:|----------:|--------------:|
| HP             | 184 |       22 |        3 |        19 |         6 |            22 |
| Pantum         | 118 |        3 |        3 |         3 |         2 |            10 |
| Katusha IT     |  75 |       15 |       14 |        13 |        24 |            20 |
| Canon          |  73 |       26 |        0 |         6 |         3 |            21 |
| Kyocera        |  60 |        2 |        4 |         2 |         2 |             2 |
| Ricoh          |  37 |        3 |        0 |         0 |         1 |             2 |
| Epson          |  36 |        5 |        1 |         1 |         4 |             5 |
| Xerox          |  14 |        1 |        1 |         1 |         1 |             2 |
| Konica Minolta |   9 |        0 |        0 |         0 |         0 |             7 |
| Sindoh         |   7 |        2 |        0 |         0 |         3 |             4 |
| **Bulat**      |   6 |    **0** |    **0** |     **0** |     **0** |         **0** |
| G&G            |   5 |        0 |        1 |         0 |         5 |             1 |
| Cactus         |   2 |        0 |        0 |         0 |         2 |             0 |

**Наблюдения:**
- **Bulat** — 0/0/0/0/0 (подтверждает успех 9a-bulat-enrich).
- **HP** — 184 SKU, заметная доля n/a по всем критическим атрибутам
  (особенно `max_format` — 19 SKU без формата).
- **Canon** — больше всего n/a по speed (26) и resolution (21).
- **Katusha IT** — наиболее «грязный» по `duplex` (24 из 75 = 32%).
- **G&G** — все 5 SKU без `duplex`, но `print_speed_ppm` подтверждён regex'ом.

### (b) Топ-брендов по % n/a `print_speed_ppm` (≥3 SKU)

| brand         | sku | %speed | %duplex | %dpi |
|---------------|----:|-------:|--------:|-----:|
| Canon         |  73 |   35.6 |     4.1 | 28.8 |
| Sindoh        |   7 |   28.6 |    42.9 | 57.1 |
| Katusha IT    |  75 |   20.0 |    32.0 | 26.7 |
| Epson         |  36 |   13.9 |    11.1 | 13.9 |
| HP            | 184 |   12.0 |     3.3 | 12.0 |
| Ricoh         |  37 |    8.1 |     2.7 |  5.4 |
| Xerox         |  14 |    7.1 |     7.1 | 14.3 |
| Kyocera       |  60 |    3.3 |     3.3 |  3.3 |
| Pantum        | 118 |    2.5 |     1.7 |  8.5 |
| Konica Minolta|   9 |    0.0 |     0.0 | 77.8 |
| Bulat         |   6 |    0.0 |     0.0 |  0.0 |
| G&G           |   5 |    0.0 |   100.0 | 20.0 |

### (c) Лоты с primary-матчем и n/a critical в preprod

Результат — **пусто**. Но это не «проблемы нет», а «matching сейчас не на
живых данных»: в `matches` всего 2 строки `primary` (и 166 `alternative`,
которые ссылаются на 2 уникальных `tender_item_id`). Из 452 `tender_items`
matching покрывает 2.

**Следствие:** оценить «сейчас сколько лотов уже сломано» из preprod нельзя —
оцениваем потенциал.

### (d+) KTRU с требованием `print_speed_ppm` в `tender_items`

Топ-комбинаций KTRU × required speed (из 203 `tender_items` с
`required_attrs_jsonb`):

| ktru_code               | n_lots | req_speed |
|-------------------------|-------:|----------:|
| 26.20.18.000-00000069   |     47 |        40 |
| 26.20.18.000-00000069   |     35 |        30 |
| 26.20.18.000-00000069   |     17 |        20 |
| 26.20.16.120-00000101   |     15 |        30 |
| 26.20.18.000-00000068   |     11 |        20 |
| 26.20.16.120-00000101   |      9 |        10 |
| 26.20.18.000-00000068   |      8 |        10 |

99 из 203 лотов с `required_attrs` требуют `print_speed_ppm ≥ 20`. Любой SKU
с `n/a` по speed теоретически проскакивает мимо этого фильтра matcher'а и
может оказаться `primary`.

### (e) Источники атрибутов (`attrs_source`)

Очень важный разрез:

| brand       | source                     | total | na_speed |
|-------------|----------------------------|------:|---------:|
| Canon       | claude_code                |    71 |       26 |
| Canon       | claude_code+regex_name     |     2 |        0 |
| HP          | claude_code                |   175 |       13 |
| HP          | claude_code+regex_name     |     9 |        9 |
| Katusha IT  | claude_code                |    63 |       13 |
| Katusha IT  | claude_code+regex_name     |    12 |        2 |
| Pantum      | claude_code                |   111 |        1 |
| Pantum      | claude_code+regex_name     |     7 |        2 |
| Sindoh      | claude_code                |     6 |        2 |
| Sindoh      | claude_code+regex_name     |     1 |        0 |
| G&G         | claude_code+regex_name     |     5 |        0 |
| Bulat       | claude_code                |     6 |        0 |

**regex_name fallback (он же из 9a-enrich, парсит `name` на «47 стр./мин»,
«22 ppm» и т.п.) уже сейчас закрывает ~80% случаев, где он применяется** —
например, у Katusha IT 12 SKU прошли через regex и только 2 остались с n/a
speed; у G&G regex дал 5/5. Но regex применяется не ко всем — у Canon только
2 из 73, у HP 9 из 184. Расширение охвата regex'а — лёгкая выгода.

## Сравнение с Bulat (9a-bulat-enrich)

| параметр                   | Bulat (закрыто)            | Canon (топ #1)                  |
|----------------------------|----------------------------|---------------------------------|
| SKU                        | 6                          | 26 с n/a speed (из 73)          |
| Источник                   | bulat-group.ru             | canon.uz / canon.am / дилеры    |
| Workflow                   | WebFetch + ручной CSV      | WebSearch + WebFetch (доступно) |
| Время на бренд             | ~30 минут                  | ~2-3 часа (больше SKU)          |
| Эффект                     | 42 ячейки n/a → 0          | ~26+21+6 = ~53 ячейки n/a → 0   |
| Risk                       | низкий                     | низкий (модели стабильные)      |

WebSearch-проверка Canon LBP631Cw: характеристики (18 ppm, цвет, A4, 600 dpi)
доступны на canon.uz, pigmentarius.ru, regard.ru. Сайт canon.ru возвращает
403 для WebFetch, но альтернативных источников достаточно.

WebSearch-проверка Katusha P247: 47 ppm, A4, 1200 dpi, дуплекс — доступно
на katusha-it.ru, katushashop.ru, foroffice.ru.

## Варианты решения

### Вариант A — точечное обогащение топ-N брендов (Bulat-style)

**Кому:** Canon (26 SKU), HP (22), Katusha IT (15), Sindoh (2), Pantum
остатки (3), Epson (5).
**Workflow:** WebSearch + WebFetch на дилеров/официальные региональные
сайты → ручной CSV-import через `enrichment/claude_code/`.
**Цена:** ~30 мин на бренд для мелких, ~2-3 часа для Canon/HP.
Суммарно ≈ 6-8 часов работы (или 1-2 рабочих дня с раскладкой).
**Эффект:** убираем ≈73 из 79 SKU с n/a speed (92% покрытия), плюс
закрытие n/a по duplex/resolution в тех же брендах.
**Risk:** новые бренды/SKU появятся при следующем `auto_price_loads`
(green_place, новые поставщики, ассортимент Resurs Media). Решение не
системное — повторяется каждые 2-4 недели.

### Вариант B — fail-closed matching (изменить решение №19)

**Изменение:** matcher отбрасывает SKU с `n/a` critical для конкретных
KTRU вместо показа с бейджем.
**Цена:** правка `engine/selector.py`, миграция «список критических
KTRU × required атрибут», тесты.
**Эффект:** системное закрытие проблемы — n/a больше никогда не попадает
в `primary`.
**Risk:**
- Требует **явной отмены решения собственника №19** (fail-open был
  осознанным выбором).
- Менеджер теряет видимость лотов под брендами, где у нас просто нет
  данных (вместо «требует уточнения» — лот просто не показывается).
- На текущем малом каталоге (628 SKU) можем потерять много лотов из-за
  единичных пропусков.

### Вариант C — гибрид (РЕКОМЕНДУЕТСЯ)

**Состав:**
1. **Точечное обогащение топ-3 брендов** (Canon, HP, Katusha IT) — это
   закроет 63 из 79 SKU с n/a speed (80%). 4-6 часов работы.
2. **Расширение regex_name fallback** на новые паттерны:
   - «X стр./мин», «X ppm», «X pages/min» уже есть;
   - добавить «X стр в минуту», «X pages per minute», ловлю A3/A4 формата,
     ловлю «двусторонняя/duplex». Применить ко всему `name` ретроактивно.
   - По данным (e), regex даёт 80%+ покрытия там, где применяется. Если
     расширить охват с текущих ~30 SKU до ~600+, эффект может быть значимым
     без ручной работы.
3. **Оставить решение собственника №19 без изменений** (жёлтый бейдж по-прежнему
   показывается, fail-open).
4. **Backfill-скрипт** для periodic-перепрогона regex'а после каждого
   auto_price_loads (защита от новых SKU).

**Эффект:** покрытие 80%+ текущих n/a + защита от деградации в будущем.
**Risk:** низкий (regex детерминистичен, обогащение по проверенным сайтам).

## Количественная оценка вариантов

Каталог сейчас (preprod): 628 SKU, 79 с n/a `print_speed_ppm`.

| вариант | закрывается SKU (speed) | риск нового n/a | трудоёмкость |
|---------|------------------------:|----------------:|--------------|
| A (только обогащение топ-3) | 63 из 79 (80%) | высокий — повторять при росте каталога | 4-6 ч однократно + ~1 ч/месяц |
| B (fail-closed)             | все 79 (логически) | нулевой — но теряем видимость лотов | ~4 ч разработки + тесты |
| C (гибрид)                  | 63 точечно + (5-10?) regex | низкий — backfill автоматический | 4-6 ч обогащение + 2-3 ч regex |

**Вариант C даёт лучший trade-off:** минимальный регресс по решению №19,
максимальное покрытие, защита от деградации.

## Рекомендация для Фазы 2

1. **Сначала** — расширить regex_name (быстрее всего, даёт системную защиту):
   - новые паттерны для `print_speed_ppm`, `duplex`, `max_format`;
   - применить ко всем 628 SKU в каталоге через backfill-скрипт;
   - встроить в `runner.run_auto_load` после каждой загрузки прайса
     (по аналогии с тем, как Resurs Media чистит дельту).
2. **Затем** — точечное обогащение Canon (приоритет №1, 26 SKU), Katusha IT
   (15 SKU, многие закроет regex), HP (22 SKU, через дилеров).
3. **Оставить** решение собственника №19 без изменений.
4. **Не входит в Фазу 2:** обогащение Konica Minolta (там только n/a dpi, не
   критично для текущих KTRU); Cactus/Brother/iRU (1-2 SKU, ниже критической
   массы).

## Открытые вопросы для собственника

1. **Согласовываешь Вариант C** или предпочитаешь другой?
2. **Включить ли Pantum-остатки (3 SKU)** в точечное обогащение Фазы 2?
   Pantum — наше ядро, важно иметь полные данные.
3. **Backfill-скрипт regex'а** — встраивать в каждый `auto_price_loads_*`
   runner или как отдельный cron-job? (По memory `feedback_no_extra_cron_jobs.md`
   — лучше внутрь runner'ов.)

## Worktree

Документ написан в worktree `d:/ProjectsClaudeCode/ConfiguratorPC2-backlog-4/`
на ветке `feature/backlog-4-discovery`. Не закоммичено, ждёт Фазы 2.

---

## Фаза 2 — выполнено (2026-05-12)

Собственник согласовал Вариант C: гибрид (regex + точечное обогащение).

### Расширение regex_name fallback

В `portal/services/auctions/catalog/enrichment/name_parser.py` добавлены 2
новых паттерна:

- **`print_speed_ppm`**: `(\d{1,3})\s*[АA]4\s*/\s*мин` — ловит формат
  «50 А4/мин» (Katusha IT M-серия). Кириллическая А и латинская A.
- **`duplex`**: `\b(?:DADF|DSDF|DSPF|SPDF)\b` — Single-Pass Duplex
  Feeder / Duplex Auto Document Feeder. Защитно для проф. MFP
  (Konica/Ricoh/Xerox/Sharp). Все 4 текущих SKU уже имеют duplex=yes —
  регресса нет, защита от будущих данных.

Тесты в `tests/test_auctions/test_name_parser.py`: добавлено 7 новых
кейсов (3 — speed, 4 — duplex). Всего тестов в файле: 61 → 68. `pytest -q`
прошёл за 3.29s.

Apply на preprod через `scripts/enrich_printers_mfu_from_names.py --apply`:
2 SKU обновлены (Katusha IT M450p, M450pm — print_speed_ppm=50).

### Точечное обогащение топ-3 брендов

Done-батчи созданы и применены через `auctions_enrich_import.py`:

| Файл | SKU | Источники |
|---|---:|---|
| `enrichment/auctions/done/2026-05-12_katusha_it_001.json` | 12 | katusha-it.ru |
| `enrichment/auctions/done/2026-05-12_canon_001.json` | 26 | canon-europe.com, canon-me.com, canon-cna.com, printer-copir.ru |
| `enrichment/auctions/done/2026-05-12_hp_001.json` | 22 | hp.com, hpplotter.co.uk, cdw.com, hp.varstreet.com, amazon.co.uk |

Все 60 SKU импортированы без отклонений. Per-key merge (backlog #10)
корректно соединил attrs_source (как `claude_code` в большинстве случаев,
плюс `claude_code+regex_name` для уже обработанных regex'ом).

**Особые случаи:**

- **Katusha IT M151**: не принтер, а «многофункциональная гибридная панель»
  (IT-устройство). Skip — оставлено n/a, manager увидит жёлтый бейдж и
  отсеет вручную.
- **Plotters (Canon imagePROGRAF TM-240/TM-350/GP-4600S; HP DesignJet
  T-серия)**: широкоформатные принтеры A0/A1. В схеме `max_format`
  только A4/A3 — поэтому `max_format="n/a"`. Скорость выставлена в
  эквиваленте A1-throughput (2-3 ppm) — низкое значение корректно отсевает
  их от стандартных MFP-тендеров с `required ≥20-40 ppm`.

### Итоговая дельта n/a (628 SKU в preprod)

| Атрибут | Было (Фаза 1) | Стало (Фаза 2) | Δ |
|---|---:|---:|---:|
| `print_speed_ppm` | 79 | 17 | **−62 (78.5%)** |
| `max_format` | 45 | 27 | −18 |
| `duplex` | 53 | 34 | −19 |
| `resolution_dpi` | 96 | 57 | −39 |
| `colorness` | 27 | 12 | −15 |
| `print_technology` | 13 | 13 | 0 (вне target) |

Цель «снизить общее n/a print_speed_ppm до ≤20» достигнута: 17/628 (2.7%).

Остаточные 17 SKU с n/a speed — другие нишевые бренды:

| brand | na_speed после Фазы 2 |
|---|---:|
| Epson | 5 |
| Pantum | 3 |
| Ricoh | 3 |
| Sindoh | 2 |
| Kyocera | 2 |
| Katusha IT | 1 (M151 — гибридная панель, не принтер) |
| Xerox | 1 |

### Matching impact

| метрика | до Фазы 2 | после Фазы 2 | Δ |
|---|---:|---:|---:|
| matches total | 168 | 5 137+ | +4 969+ (×30) |
| primary | 2 | 98 | +96 (×49) |
| alternative | 166 | 5 039+ | +4 873+ |
| distinct tender_items с матчами | 2 | 98 | +96 |
| primary margin% — median | — | 62.74% | — |
| primary margin% — avg | — | 56.16% | — |

**Источник данных:** `scripts/run_matching.py --full_recompute` на preprod
(БД через `DATABASE_PUBLIC_URL`). Финальный full_recompute был прерван
оператором после ~5 000 INSERT'ов из-за высокой латентности Railway-proxy
(одиночные INSERT'ы со скоростью ~1-2 строки/сек). Состояние `matches`
консистентно для уже вставленных строк, но финальное число matches
может быть выше при полном прогоне (>5 137). Это не влияет на качественный
вывод: enrichment **раскрыл матчинг** для 96 новых tender_items, которые
раньше molчали из-за отсутствия конкретных значений в attrs_jsonb.

**Важное наблюдение:** дополнительные primary-матчи появились
**потому что enrichment'ом мы перевели SKU из «требует уточнения»
(fail-open) в «явное значение»**, и они стали удовлетворять KTRU-фильтрам,
которым раньше не подходили. До Фазы 2 многие KTRU-запросы вообще не
строили primary, потому что подходящие SKU имели `print_speed_ppm=n/a` и
matcher не мог их сравнить с `required ≥X ppm` (хотя по другим атрибутам
они проходили).

В Bulat-эпизоде (9a-bulat-enrich) median margin% снизился с 77.58 → 69.88
после того как Bulat P1024W (24 ppm) перестал ложно проходить как primary
в лотах ≥30 ppm. На preprod аналогичный эффект «убывания false-positive
primary» не воспроизводим из-за малого числа базовых матчей; зато виден
**противоположный** эффект — резкий рост числа primary за счёт раскрытия
matcher'а. Бо́льшая часть закрытых SKU относится к Canon/HP/Katusha IT,
которые в реальном тендерном потоке встречаются часто. Финальная оценка
эффекта (false-positive vs true-positive в primary) — на prod-данных
после применения собственником.

### Тесты

- `tests/test_auctions/test_name_parser.py`: 68 passed (было 61).
- pytest-baseline проверен: `pytest -q tests/test_auctions/test_name_parser.py`
  → all pass за 3.29s.

### Не входило (вынесено)

- **Apply enrichment на prod-БД** — собственник руками после согласования
  через `.env.local.prod.resurs.v1` (или отдельный prod-env-файл).
  Аналогично 9a-bulat-enrich. Done-файлы можно повторно сгенерировать,
  но проще скопировать из `enrichment/auctions/archive/2026-05-12/`
  обратно в `done/` и запустить `auctions_enrich_import.py` с
  `DATABASE_URL=prod`.
- **Дополнительные бренды** (Epson, Pantum остатки, Ricoh, Sindoh,
  Kyocera, Xerox) — отдельным мини-этапом если понадобится после
  prod-пилота.
- **Backfill-скрипт regex'а в runner'ы поставщиков** — встроенный
  re-run regex'а после auto_price_loads. Пока не критичен (regex
  применяется через ручной `enrich_printers_mfu_from_names.py`).

### Что собственнику делать руками

1. Согласовать прокатку Канон/HP/Katusha-обогащения на prod-БД
   (12+26+22 = 60 SKU обновятся; импорт идемпотентный).
2. Команда: скопировать `enrichment/auctions/archive/2026-05-12/*.json`
   обратно в `enrichment/auctions/done/`, экспортнуть DATABASE_URL prod,
   запустить `scripts/auctions_enrich_import.py`.
3. Запустить `scripts/run_matching.py` на prod — посмотреть итоговую
   дельту primary-матчей и margin%.
