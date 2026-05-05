# Технический долг обогащения характеристик

**Статус на:** 2026-04-30 (после этапа 11.6.1 — regex по `supplier_prices.raw_name`).
**Заполненность по категориям:** см. §4 и §11.

Документ фиксирует принципиальные ограничения whitelist-подхода,
кандидатов на «скрытие» от NLU, оставшиеся NULL-поля по системным
характеристикам и предлагаемые пути добора.

## 1. COOLER.max_tdp_watts — не публикуется на оф.сайтах

**Проблема:** производители процессорных кулеров (Thermalright, Lian-Li,
Zalman, ID-Cooling, Deepcool, Noctua, Corsair, PcCooler) **намеренно не
публикуют Max TDP Rating** на официальных сайтах. Проверено в Этапах 2.5Б
и 2.5В на 40 + ≈160 позициях — ни одна не получила значения.
На оф.сайтах публикуются размеры радиатора, RPM/CFM/dB вентиляторов,
supported sockets и количество heat pipes. TDP-рейтинги (245W Peerless
Assassin, 250W Dark Rock и т.п.) публикуются ритейлерами и обзорщиками
(techpowerup, guru3d, 3dnews) — источники, запрещённые whitelist-правилом.

**Остаток NULL:** 228 позиций cooler.max_tdp_watts.

**Пути добора:**
- **Принятое решение (2.5А):** derived-оценка по размеру радиатора AIO
  (`app/services/enrichment/regex_sources/cooler.py`):
  120mm→150W, 140mm→180W, 240mm→200W, 280mm→250W, 360mm→300W, 420mm→400W.
  Работает только для AIO с явным размером радиатора.
  source='derived', confidence=0.7.
- **Ручная разметка** через `/admin/components/<id>` (планируется на Этапе 9
  UX-улучшений).
- **Запрос дистрибьюторам** (Merlion/Treolan) — они иногда присылают CSV
  с TDP-колонкой в расширенных прайсах.
- **Расширить whitelist на техобзорщиков** (techpowerup.com) — требует
  пересмотра принципа «только оф.сайты», согласия пользователя.

## 2. Netac Z9 / Z Slim / ZX — внешние USB-C SSD, не подходят под схему

**Статус:** частично закрыт (разовая чистка в этапе 9Г.1).

5 позиций storage в БД — портативные SSD USB-C с форм-фактором 1.8":

| id | model |
|----|---|
| 215 | Netac NT01ZX20-001T-32BL ZX 1TB USB-C |
| 216 | Netac NT01Z9-001T-32BK Z9 1.8" 1TB USB-C |
| 217 | Netac NT01ZSLIM-001T-32BK Z Slim 1.8" 1TB USB-C |
| 218 | Netac NT01ZSLIM-002T-32BK Z Slim 1.8" 2TB USB-C |
| 219 | Netac NT01Z9-002T-32BK Z9 1.8" 2TB USB-C |

В этапе 9Г.1 все 5 id помечены `is_hidden=TRUE` через прямой UPDATE
в проде (Railway DB UI). Системный фикс на уровне price_loaders
отложен — реализовать при появлении 10+ внешних USB-C SSD в каталоге.

**Проблема:** `storages`-схема валидирует form_factor в enum
{2.5", 3.5", M.2, mSATA} и interface в {SATA, NVMe, SAS}. USB-C и 1.8"
portable enclosure — не внутренние накопители, не совместимы ни с
материнской платой, ни с рейд-корзинами.

**Варианты системного решения (на будущее, когда объём оправдает):**
- Расширить `shared/component_filters.is_likely_external_storage`
  (сейчас заглушка, возвращает False) и подключить её в
  `app/services/price_loaders/orchestrator.py` тем же способом,
  что `is_likely_case_fan` — новые скелеты сразу с `is_hidden=TRUE`.
- Добавить колонку `is_internal BOOL` в `storages` (отдельная миграция)
  и фильтровать по ней в `/api/search` + NLU.
- Помечать их через `component_field_sources` как source='excluded',
  field_name='visibility', value='external_usb' и фильтровать во view.
- Переклассифицировать как accessory (отдельная таблица).

## 3. GPU — остаточные NULL после 2.5В по системным полям

**Финальное состояние на 2026-04-24, после добивающего раунда GPU batches 4+5
(+216 полей записано):**

| Поле | NULL | Всего | % покрытия |
|------|------|-------|------------|
| gpu.tdp_watts         |  66 | 495 | **86.7%** |
| gpu.needs_extra_power |  66 | 495 | **86.7%** |
| gpu.video_outputs     | 126 | 495 | 74.5% |
| gpu.core_clock_mhz    |  97 | 495 | 80.4% |
| gpu.memory_clock_mhz  |  70 | 495 | 85.9% |
| gpu.vram_gb           |   3 | 495 | 99.4% |
| gpu.vram_type         |   6 | 495 | 98.8% |

Большинство оставшихся NULL-полей по `video_outputs` — это AIB-карты,
где агенты строго соблюдали правило «не выводить из наименования
без URL оф.сайта». Детальные страницы AIB-партнёров (MSI, Palit, ASUS
techspec) в значительной части отдают 403 / пустую страницу.

**Распределение NULL-gpu.tdp_watts по производителям (на 2026-04-24):**

| Производитель | NULL | Причина |
|---|---|---|
| AFOX CORPORATION / AFOX | 43 + 3 = 46 | SPA без SSR на afox-corp.com, карточки отдельных старых GT/RX моделей недоступны |
| MSI | 40 | Бот-защита 403 у AIB-сайта msi.com для старых GT/GTX-серий |
| ASUS | 39 | Бот-защита 403 + редиректы на локальные домены |
| PALIT / Palit | 17 + 4 = 21 | Сайт активный, но карточки EOL-моделей убраны |
| MAXSUN | 13 | maxsun.com.cn часто с частичной инфой для RF-линеек |
| SAPPHIRE | 9 | EOL-модели — нет datasheet |
| INNO3D | 8 | EOL-модели Twin X2 — нет datasheet |
| Biostar | 6 + 2 = 8 | EOL GT-серия |
| GIGABYTE | 6 | Бот-защита |
| PNY / Matrox / ASROCK / ZOTAC | 3+3+2+1 | Единичные, в большинстве EOL |

**Пути добора:**
- **Ручная разметка** через `/admin/` (самый точный путь).
- **Прайсы дистрибьюторов** с расширенной спецификацией (Merlion иногда
  присылает CSV с TDP для GPU в рамках маркетинговых рассылок).
- **Расширить whitelist на techpowerup** — архив спеков практически для
  всех когда-либо выпущенных GPU, но противоречит принципу этапа 2.5.

## 4. Общая статистика обогащения (2.5А + 2.5Б + 2.5В)

**Исходно (перед 2.5А):** 2207 скелетов Merlion/Treolan, созданных при
загрузке прайсов (до regex-обогащения).

**После 2.5А (regex, коммит 46eaed1):**
- 1557 скелетов получили ≥1 значение.
- 650 остаточных скелетов (29.5%).

**После 2.5Б (AI-whitelist, коммит 4909d5d):**
- +134 поля записано в БД (GPU 120, STORAGE 7, CASE 4, PSU 3).
- Остаточных скелетов: 544 (позиций с ≥1 NULL в TARGET_FIELDS).

**После 2.5В (все раунды, включая добивающий):**
- +31 поле video_outputs как `derived_from_name` (backfill).
- +2 новых домена в whitelist (afox-corp.com, gamerstorm.com).
- +384 полей AI-агентами в первом раунде (GPU 2+3, MB+PSU retry).
- +216 полей AI-агентом в добивающем раунде (GPU 4+5).
- **Итого 2.5В: 631 поле записано в БД.**

**Финальное покрытие (после 2.5А + 2.5Б + 2.5В):**

| Категория | Скелетов было | Закрыто | Осталось | % покрытия |
|---|---|---|---|---|
| psu         | 307 | 306 |   1 | 99.7% |
| storage     | 243 | 237 |   6 | 97.5% |
| case        | 839 | 774 |  65 | 92.3% |
| motherboard |   2 |   2 |   0 | 100% (retry через afox-corp.com) |
| gpu         | 255 |  96 | 159 | 37.6% по позициям, но по полям 80-87% покрытия |
| cooler      | 561 | 425 | 136 | 75.8% (max_tdp_watts не публикуется на оф.сайтах) |

## 5. Источник-метки (source в `component_field_sources`)

Актуальный регистр значений колонки `source`:

| source | Смысл | Confidence |
|---|---|---|
| `regex` | Извлечено regex-правилами из имени компонента | 1.0 |
| `derived` | Выведено правилом (например, has_psu=FALSE для DIY-корпусов) | 0.7–0.9 |
| `claude_code` | Извлечено AI-агентом с официального сайта производителя | 0.9 |
| `derived_from_name` | Выведено из поля `model`, когда данные есть в имени, но URL-источник невозможен (Этап 2.5В) | 0.85 |

**Подметка `source_detail` (этап 11.6.1):** различает два regex-пайплайна:

| source | source_detail | Смысл |
|---|---|---|
| `regex` | NULL | Старый прогон `scripts/enrich_regex.py` по полю `model` таблицы компонентов (этап 2.5А) |
| `regex` | `from_raw_name` | Новый прогон `scripts/enrich_regex_from_raw_names.py` по `supplier_prices.raw_name` (этап 11.6.1) |
| `derived` | `from_raw_name` | Производное правило, сработавшее на raw_name (например, derived form_factor для HDD по типу) |

Этот же файл — единственный источник правды по истории заполнения полей.
Запросы `SELECT source, COUNT(*) FROM component_field_sources WHERE field_name=...`
дают распределение.

## 6. Что не является техдолгом — нормальное поведение

- **Корпуса без has_psu_included (7 позиций):** современные DIY-корпуса
  почти всегда без БП; regex с confidence 0.7 ставит FALSE в 90% случаев.
  Остальные — редкие конфигурации (OEM-корпусы для серверов).
- **CPU-категория 0 NULL:** полная покрытость базовых характеристик,
  дальнейшее обогащение не требуется.
- **RAM-категория 0 скелетов:** после Этапа 2.5А regex закрыл всё.

## 7. История изменений whitelist

- **Перед 2.5Б:** 54 домена.
- **После 2.5Б:** 70 доменов (+16, см. `scripts/reports/ai_enrichment_whitelist_recon.md`).
- **После 2.5В:** 72 домена (+2: afox-corp.com, gamerstorm.com).

## 8. План работ на Этап 9 (UX-улучшения)

1. Ручная разметка остаточных GPU через `/admin/components/<id>` —
   самый значимый объём (~450 позиций по 5 полей).
2. Миграция 012: `is_internal` для storages — фильтрация Netac USB-C из NLU.
3. Ручной справочник «модель кулера → TDP» для топовых моделей
   (Peerless Assassin 245W, Dark Rock 250W и т.п.).
4. Периодическая (раз в квартал) переповалка AI-агентов по новым позициям
   из прайсов — ловим новые релизы когда у AIB-партнёров появляются
   datasheet.

## 9. Корпусные вентиляторы в категории cooler (этап 9А.2.1, доработан в 9Г.1)

**Проблема:** при загрузке прайсов в категорию `cooler` могут попадать
корпусные вентиляторы (case fans). Они не используются в подборе CPU-кулеров
(нет `supported_sockets`, нет `max_tdp_watts`), но засоряют выдачу.

**Решение (9Г.1, системное):** эвристика
[`shared/component_filters.is_likely_case_fan`](../shared/component_filters.py)
вызывается в `app/services/price_loaders/orchestrator.py` при создании
скелета компонента. Если позиция в категории `cooler` похожа на корпусный
вентилятор (regex по name/manufacturer, без CPU-маркеров), она создаётся
сразу с `is_hidden=TRUE`. При следующих загрузках свежих прайсов новые
корпусные вентиляторы не попадают в подбор автоматически.

**Если правило ложит компонент, который скрывать НЕ нужно** — расширьте
исключения в `shared/component_filters.py`. Не плодите новые ad-hoc
скрипты в `scripts/` — это анти-паттерн, который мы только что закрыли.

**Историческое (до 9Г.1):** разовая массовая чистка существующих позиций
через `scripts/hide_case_fans.py` — отчёт
`scripts/reports/case_fans_hidden_report.md`, SQL отката
`scripts/reports/case_fans_backup_YYYYMMDD.sql`. Скрипт оставлен как
ручной аварийный override; запускать обычно не нужно.

## 10. Внешние Netac USB-C SSD (этап 9Г.1)

**Проблема:** в каталоге висели 4 портативных Netac USB-C SSD
(NT01Z9 / NT01ZSLIM, 1TB / 2TB), которые не подходят под схему
`storages` (interface USB-C, form_factor 1.8" portable) и попадают
в fuzzy-поиск NLU как кандидаты внутреннего накопителя.

**Решение (9Г.1):** разовая чистка скриптом
`scripts/hide_external_netac_ssd.py` (idempotent, dry-run по умолчанию).
В `shared/component_filters.is_likely_external_storage` оставлена
заготовка — пока возвращает False; при появлении заметного количества
новых внешних накопителей расширить и подключить в `orchestrator`
тем же способом, что `is_likely_case_fan`.

**Запуск на проде:** одна ручная операция админа после деплоя
(`python scripts/hide_external_netac_ssd.py --apply`). Не привязан к
APScheduler.

## 11. Regex по supplier_prices.raw_name (этап 11.6.1)

**Что добавлено:** второй regex-пайплайн обогащения —
`app/services/enrichment/raw_name_runner.py` +
`scripts/enrich_regex_from_raw_names.py`. Прогоняет regex-экстракторы
из `regex_sources/*.py` не только по `model` компонента, но по всем
`supplier_prices.raw_name`, привязанным к этому компоненту, и
агрегирует результат. Это закрывает кейс новых поставщиков (Netlab,
Ресурс Медиа, Green Place — этап 11.1), у которых короткий `model`
скелета не содержит характеристик, а длинная исходная строка прайса
теперь живёт в `supplier_prices.raw_name` (миграция 022).

**Локальный замер 2026-04-30** (после backfill `raw_name = model`,
который имитирует «следующая загрузка прайсов донесла те же имена» —
реалистичный нижний потолок, на проде raw_name будет длиннее):

| Категория | Кандидатов | Полей записано | Осталось с NULL |
|---|---|---|---|
| cpu         |    0 |    0 |    0 |
| motherboard |    2 |    0 |    2 |
| ram         |    0 |    0 |    0 |
| gpu         |  453 |  458 |  453 |
| storage     |  368 | 1232 |  177 |
| case        | 1805 | 1586 | 1771 |
| psu         |  860 |  624 |  236 |
| cooler      | 1447 |  458 | 1288 |
| **Итого**   | **2358 компонентов / 4358 полей записано** | | **3927** |

NULL-ячеек в обязательных полях: было 11409, стало 7051 (-4358).

«Осталось с NULL» — это компоненты, у которых после прогона regex
осталось ≥1 NULL обязательное поле. Это input для **этапа 11.6.2**
(Claude Code-обогащение точечно по этим скелетам).

**Идемпотентность:** второй прогон ничего не записывает, потому что
все NOT NULL поля защищены политикой `persistence.apply_enrichment`
(не перезатираем). UNIQUE-ключ `(category, component_id, field_name)`
в `component_field_sources` гарантирует отсутствие дубликатов записи
источника.

**Конфликты:** если разные raw_name дают разные значения одного поля,
берётся значение из САМОГО ДЛИННОГО raw_name (длинное обычно
содержит больше характеристик). Конфликты логируются и попадают
в отчёт прогона.

**Особенности на текущий момент:**
- Локальная БД ещё не получала загрузок прайсов с миграцией 022,
  поэтому до backfill все `supplier_prices.raw_name` были NULL.
  На проде колонка наполнится при следующих загрузках прайсов.
- 11.6.1 не запускается на проде автоматически. UI-кнопка из
  `/admin/price-uploads` ожидается на этапе 11.6.2.

## 12. Workflow выгрузки/импорта batch'ей (после 11.6.2.3.3)

**Контекст.** До 11.6.2.3.3 выгрузка batch-файлов делалась с локальной
БД, AI-агенты заполняли поля, импорт шёл на прод. Скелеты на локали и
проде создаются разными загрузками прайсов в разное время, поэтому
`components.id` расходятся, и при импорте на прод 25–30% items
отбраковывались с `unknown_component`. Этап 11.6.2.3.3 устраняет
ID-перекос, перенося выгрузку прямо на прод.

### Старый workflow (deprecated, c ID-перекосом)

```powershell
# Локально, выгрузка против ЛОКАЛЬНОЙ БД (id'шники локальные!):
python scripts\enrich_export.py --category cooler --batch-size 30

# Чаты Claude Code заполняют batch-файлы в enrichment\done\cooler\.

# Импорт на прод через railway ssh:
railway ssh -s ConfiguratorPC2 -i $HOME\.ssh\id_ed25519_railway -- `
    python -m scripts.enrich_import --category cooler
# ↑ часть items падает с unknown_component из-за разных id.
```

### Новый workflow (через `enrich_export_prod.py` + `--keep-source`)

```powershell
# 1. Выгружаем прямо из ПРОД-БД через railway ssh
#    (TCP-проксирование БД не нужно; всё внутри контейнера, наружу только JSON):
python scripts\enrich_export_prod.py --category cooler --batch-size 30

# 2. Чаты Claude Code заполняют batch-файлы в enrichment\done\cooler\.

# 3. Smoke-импорт на ЛОКАЛИ с --keep-source — файлы остаются в done/:
python scripts\enrich_import.py --category cooler --keep-source

# 4. Финальный импорт на ПРОД теми же файлами через railway ssh.
#    Файлы остаются в done/ локально → доступны для повторного импорта,
#    в т.ч. если прод-импорт прервался и нужно перезапустить:
railway ssh -s ConfiguratorPC2 -i $HOME\.ssh\id_ed25519_railway -- `
    python -m scripts.enrich_import --category cooler
```

**Дополнительные флаги enrich_export_prod.py:**

| Флаг | Назначение |
|------|------------|
| `--limit N`  | Ограничение по числу позиций суммарно (smoke-тест workflow). |
| `--force`    | Не падать, если `enrichment/pending/<category>/` уже не пуст. |

**Обработка ошибок wrapper'а:** `railway` CLI не найден → понятное
сообщение + exit 1; non-zero exit удалённого процесса → пробрасывается
stderr и exit 1; невалидный JSON в stdout → exit 1 с фрагментом
полученных данных (помогает диагностировать SSH-баннеры,
prepended-логи и т.п.).

**Когда не использовать новый workflow:** разовая ручная разметка
одной-двух позиций — проще через `/admin/components/<id>` или прямой
UPDATE в проде, без batch-инфраструктуры.

**Категории, переведённые на новый workflow:**

| Этап | Категория | Дата | Заметка |
|------|-----------|------|---------|
| 11.6.2.3.3 | cooler | 2026-04-30 | Пилотный — первый прогон через `enrich_export_prod.py`. |
| 11.6.2.4.1a | case | 2026-05-01 | 8 batch'ей с прода (230 items). Прод оказался намного чище, чем ожидалось (288 кандидатов вместо ~1840 — derived/regex закрыли больше полей). Также пофикшен Windows-баг wrapper'а: `subprocess.run(["railway", ...])` не находил `railway.CMD`; теперь имя бинаря резолвится через `shutil.which`. |
| 11.6.2.4.1b | case | 2026-05-05 | AI-обогащение тех же 230 items через 11 параллельных subagent'ов + bulk-null процессор для 100 non-whitelist items. Локальный импорт: 165 полей у 92 компонентов. Прод-импорт: 203 поля у 112 компонентов (на проде больше NULL-baseline → больше записей). 0 отклонений. Coverage form_factors поднялась 85.3% → 91.0%, has_psu_included 90.4% → 95.1%. |

## 13. Аудит и переклассификация мусора в категории Case (этап 11.6.2.4.0)

**Контекст.** Перед AI-обогащением Case (1771 NULL-полей в локальной БД,
из них 289 в `supported_form_factors`, 190 в `has_psu_included`, 1757 в
`included_psu_watts`) повторили опыт Cooler: сначала вычистить мусор,
чтобы не тратить токены AI на не-корпуса.

**Главный сюрприз диагностики.** В отличие от Cooler (там 80% выборки
оказались корпусные вентиляторы / термопасты / mounting kits), категория
Case на локальной БД kvadro_tech практически чистая. На 1876 видимых
cases выявлен только **1 реальный кейс мусора**:

| ID   | Кластер | Производитель | Имя |
|-----:|---------|---------------|-----|
| 1065 | loose_case_fan | unknown | «Устройство охлаждения(кулер) Aerocool Core Plus, 120мм, Ret» |

Все остальные подозрительные совпадения по raw_name (drive cage / dust
filter / side panel / pcie riser / tempered glass) при ближайшем
рассмотрении оказались **описаниями полноценных корпусов**: серверные
JBOD-шасси AIC J2024/RSC-4BT, корпуса Lian Li с предустановленным
PCIe Riser Cable (SUP01X) или Bottom Dust Filter (A3-mATX),
JONSBO MOD 5 / Deepcool MACUBE с tempered glass-панелью и т. п.

**Новые детекторы в `shared/component_filters.py`** (5 шт.):

| Имя | Назначение | Реальные / профилактика |
|-----|-----------|--------------------------|
| `is_likely_loose_case_fan` | Самостоятельный 120-мм вентилятор / кулер в категории case | 1 реальный (id=1065) |
| `is_likely_drive_cage` | Отдельная корзина 3.5"/2.5" / mobile rack / drive cage | 0 — профилактика |
| `is_likely_pcie_riser` | Отдельный PCIe-райзер (cable/card/extender) | 0 — профилактика |
| `is_likely_case_panel_or_filter` | Отдельная сменная боковая панель / стекло / пылевой фильтр | 0 — профилактика |
| `is_likely_gpu_support_bracket` | Отдельный антипровисной кронштейн для GPU | 0 — профилактика |

**Ключевой защитный слой.** Все 5 эвристик уважают общий regex
`_CASE_HOUSING_HINTS`: если в имени присутствует «midi tower» / «full
tower» / «корпус ПК» / «JBOD» / «rack-mount» / «PC case» / «ATX case»
/ «Mod Gaming» / «Tempered Glass Edition» — детектор возвращает False,
даже если сработал положительный триггер. Защита проверена тестами
`tests/test_shared/test_case_trash_detectors.py::TestHousingHintBlocksAllDetectors`.

**Upstream-подключение.** В `app/services/price_loaders/orchestrator.py::_create_skeleton`
для `table == "cases"` детекторы прогоняются по `row.name + row.brand`
ДО записи: при положительном срабатывании скелет создаётся с
`is_hidden=True` и сразу выпадает из подбора. Это закрывает поток
будущего мусора с прайсов.

**Итоги локального --apply (kvadro_tech, 2026-05-01).** Скрытие 1
позиции (id=1065). NULL после: 1770 видимых cases с любым NULL
(было 1771). Backup-rollback —
`scripts/reports/reclassify_cases_trash_backup_20260501.sql`.

**Итоги прод-прогона.** См. конец файла после деплоя
(`scripts/reclassify_cases_trash.py --apply` через railway ssh).

**Whitelist `OFFICIAL_DOMAINS`** (схема обогащения) расширен на
6 case-вендоров, реально присутствующих в БД, но отсутствовавших ранее:
`gamemax.com`, `raijintek.com`, `xpg.com`, `powerman-pc.ru`,
`digma.ru`, `hiper.ru`. AI-обогащение 11.6.2.4.1 сможет ходить на эти
домены при выявлении бренда.

**Кандидаты в техдолг (не закрываются этим этапом).**

1. **SBC-корпуса в `cases`.** Локально найдены id=492 (ACD XG387 IP65
   для Raspberry/Orange Pi), id=562/564 (Raspberry Pi 5 cases с активным
   вентилятором), 6 позиций RockPi, 4 позиции Raspberry Pi Foundation.
   Это **формально корпуса** (закрывают плату), но не подходят под
   ATX/mATX-сборку: их `supported_form_factors` всегда NULL и
   AI-обогащение бессмысленно. Решение — отдельная категория
   `sbc_case` или флаг `is_sbc=True` на cases — отложено в новый этап
   (потенциальный 11.6.2.6.x).

2. **HDD/SSD в cases.** В диагностике не обнаружено (паттерн
   `storage_in_cases` дал 0 матчей). Если такие появятся — фиксировать
   как отдельный техдолг апстрима, не переносить в storages
   автоматически.

**Артефакты этапа.**
* `scripts/reclassify_cases_trash.py` — идемпотентный скрипт
  (`--dry-run` по умолчанию, `--confirm --confirm-yes` для apply).
* `scripts/audit_cases_local.py` — локальная диагностика, не входит в
  pipeline (не закоммичена; нужна была для понимания состава).
* `scripts/reports/reclassify_cases_trash_report.md` —
  отчёт последнего прогона (gitignored).
* `scripts/reports/reclassify_cases_trash_backup_YYYYMMDD.sql` —
  rollback (gitignored).

## 14. AI-обогащение Case (этап 11.6.2.4.1b) — итоги

**Контекст.** 230 видимых cases с NULL в `supported_form_factors`
и/или `has_psu_included` после прошлого этапа. Сценарий: 11
параллельных subagent'ов Claude Code, по одному на бренд-кластер,
плюс bulk-null процессор для items с брендом вне whitelist.

**Распределение по результату (130 whitelist + 100 bulk-null = 230):**

| Категория | items | Результат | Причина |
|-----------|------:|-----------|---------|
| Whitelist brand, данные найдены | 116 | success | — |
| Whitelist brand, honest-null | 14 | null + reason | см. ниже |
| Bulk-null: бренд не в whitelist | 88 | null + reason | Ginzzu 73, ExeGate/Crown/Zircon/PowerCool/1stPlayer 12, Thermalright LCD 4 (LCD-дисплей, не корпус) |
| Bulk-null: SBC-защитный слой | 0 | null + reason | детектор не сработал в этом батче (Ginzzu и пр. — не SBC) |
| Bulk-null: аксессуары | 5 | null + reason | InWin направляющие/ручки (ID 953-955), ExeGate-рельсы 1914 |
| Bulk-null: бренд не определён | 6 | null + reason | EK303BK / EL555BK / EC046U3 / BA831BK / S345-450W (POWERMAN-серии без явного бренда в имени) |

**Локальный импорт:** 165 полей принято (92 form_factors + 73 psu) у
92 компонентов, 0 отклонено.

**Honest-null breakdown (14 items с whitelist-брендом, но без данных):**

| Бренд | items | Причина |
|-------|------:|---------|
| GameMax | 7 | Реальный домен — `gamemaxpc.com`, не `gamemax.com` (в whitelist). Все 7 → null. |
| Powerman | 3 | `powerman-pc.ru` недоступен (ECONNREFUSED во время AI-прохода). Все 3 → null. |
| Formula | 2 | "Formula" в БД — это **Formula V Line** (`formulav-line.com`), не Formula PC. |
| Accord | 1 | `accord-pc.ru` не индексируется поиском, через WebFetch недоступен. |
| HPE (XASTRA) | 1 | XASTRA A700 — российский OEM, не имеет отношения к HPE. На `hpe.com` отсутствует. |
| InWin | 2 | Модели ENR708 и BM677 не найдены на `in-win.com` (вероятно EOL/региональные). |
| Aerocool | 1 | id=1066 ARCT Core Plus 120мм — это CPU-кулер, не корпус (попал в cases ошибочно — пере-кандидат для is_likely_loose_case_fan). |
| XPG | 1 | id=1737 LEVANTE II 360 — AIO СЖО, не корпус (мисс-классификация). |

**Кандидаты в техдолг по итогам этапа:**

1. **GameMax-домен в whitelist.** Поменять `gamemax.com` →
   `gamemaxpc.com` в `OFFICIAL_DOMAINS` (или добавить оба) — это
   разблокирует 7 items на следующем прогоне.
2. **Powerman повторить.** 3 POWERMAN-items оставлены null с reason
   "domain unreachable". Запустить разовый AI-прогон по powerman-pc.ru
   когда домен поднимется.
3. **Формат-баги subagent'ов** (см. также §12). InWin вернул `source`
   вместо `source_url`, Thermaltake — bare values без обёртки. Оба
   починены руками. **Действие**: уточнить пример output-формата в
   `enrichment/prompts/_общие_правила.md` (более жёсткий пример +
   negative-пример).
4. **Re-classify Aerocool 1066 / XPG 1737.** Это CPU-кулер и AIO,
   попавшие в cases. Расширить детекторы из 11.6.2.4.0 (либо
   обновить `is_likely_loose_case_fan` чтобы ловил формулировку "Core
   Plus 120мм" с производителем Aerocool).
5. **Formula-mapping.** "Formula" в нашей БД на 99% — это Formula V
   Line. Подумать о добавлении `formulav-line.com` в whitelist.

## 15. PSU audit (11.6.2.5.0a/b)

**Контекст.** На начало этапа 5.0 в категории `psus` было 234 видимых
строки с `power_watts IS NULL`, из которых 232 — в bucket
`manufacturer='unknown'`. AI-обогащение не может заполнить мощность,
пока бренд неизвестен (нечего искать в whitelist-доменах). Аудит
[`scripts/_psu_audit.py`](../scripts/_psu_audit.py) на проде
(этап 5.0a) показал три класса проблем.

| # | Проблема | Объём | Стратегия |
|---|---|---:|---|
| 1 | Адаптеры/POE/charger/USB-PD/dock-station, ошибочно классифицированные как PSU. | ~70 (Gembird NPA-AC, KS-is, ORIENT PU-C/SAP-, BURO BUM-*, Ubiquiti POE, FSP FSP040, ББП Бастион РАПАН и т. д.) | Детектор `is_likely_psu_adapter` в [`shared/component_filters.py`](../shared/component_filters.py), upstream-классификация в `orchestrator.py`, разовая чистка — `scripts/reclassify_psu_misclassified.py`. |
| 2 | 7 настоящих PSU PcCooler/Aerocool в `coolers` (PCCooler P5-YK850, Aerocool Mirage Gold, ...). | 7 + 2 case-дубля (PcCooler C3B310/C3D510 уже есть в `cases`). | Миграция [`migrations/024_psu_misclassification.sql`](../migrations/024_psu_misclassification.sql): INSERT в psus + UPDATE coolers SET is_hidden=TRUE. |
| 3 | bucket `manufacturer='unknown'` с реальными ATX/SFX-PSU (CBR/Exegate UN/Ginzzu CB-PC/XPG KYBER/Zalman ZM/...). | ~150 | `scripts/recover_psu_manufacturer.py` — regex по `supplier_prices.raw_name`, 25 PSU-брендов с приоритетом от длинных к коротким. |

**Закрыто этапом 5.0b:**

- ~~#2: 7 PSU из coolers перенесены~~ — миграция 024 идемпотентна, на проде применяется автоматически через `scripts/apply_migrations.py` при ближайшем редеплое.
- ~~#3: детектор PSU-adapter работает~~ — `is_likely_psu_adapter` подключён в orchestrator (новые прайсы скрывают адаптеры сразу при создании скелета), reclassify-скрипт идемпотентно вычистил уже существующие.

**Открытое (отложено в 5.0c):**

1. Нормализация регистра `psus.manufacturer` (DEEPCOOL/Deepcool, ZALMAN/Zalman, CHIEFTEC/Chieftec). По текущей БД дубли:
   `Deepcool` 61 vs `DEEPCOOL` 12, `CHIEFTEC` 41 vs `Chieftec` 2,
   `Zalman` 28 vs `ZALMAN` 10, `Thermaltake` 26 vs `THERMALTAKE` 3,
   `Formula` 12 vs `FORMULA` 11 vs `Formula V` 28.
2. Расширение whitelist под бренды без оф. домена в наборе:
   HSPD, Formula V Line, Super Flower, BLOODY, SAMA, Gooxi, Foxconn —
   требует web-research официальных доменов и спеков.
3. Misc-категория «cases/coolers внутри psus»: ~25 строк
   (`Корпус Thermaltake`, `Cooler Master MasterBox`, `Кулер DeepCool`,
   `Вентилятор Thermaltake CT120`) попали в категорию psus при
   первичной загрузке из-за слов «PCCOOLER»/«Cooler Master» в
   raw_name. Детектор `is_likely_psu_adapter` их не ловит (нет маркеров
   адаптера). Решение: либо отдельный детектор `is_likely_misc_in_psu`,
   либо ручная переклассификация на 5.1.

**Метрики этапа (локальная БД, 5.0b apply):**

- Помечено `is_hidden=TRUE` детектором PSU-adapter: **79** (Ubiquiti POE 5, Cisco POE 1, FSP GROUP 1, и 72 unknown-bucket — Gembird NPA-AC*, KS-is, ORIENT, BURO, ББП Бастион).
- Восстановлено `manufacturer` regex'ом по supplier_prices.raw_name:
  **662** (топ: ExeGate 292, Deepcool 51, Thermaltake 51, 1STPLAYER 43,
  Aerocool 43, CHIEFTEC 36, Ginzzu 22, XPG 22, CBR 21, Cooler Master 18).

Прод-метрики будут добавлены сюда после ШАГ 9 (применение через railway ssh).

**Закрыто этапом 5.0c (2026-05-05).**

- ~~#3: Misc-категория «cases/coolers внутри psus»~~ — детектор
  `is_likely_non_psu_in_psus` в
  [`shared/component_filters.py`](../shared/component_filters.py)
  ловит позиции с leading-маркером «Корпус …» / «Кулер …» /
  «Вентилятор …» / «Устройство охлажд …» (жёсткое True, никакая
  защита не спасает) либо с маркерами `MasterBox` / `AIO` /
  `PC Cooling Fan` / «к корпусам» в середине строки (тогда применяются
  защитные слои: «Блок питания»/`Power Supply`, серия настоящего PSU
  из whitelist, явная мощность ≥200W). Защита по форм-фактору
  (ATX/SFX) намеренно НЕ применяется — у корпусов это атрибут
  совместимости и она дала бы ложно-отрицательные. Подключён в
  `scripts/reclassify_psu_misclassified.py` через OR с
  `is_likely_psu_adapter`. **Локально помечено 26** (19 Thermaltake +
  3 Cooler Master + 2 unknown + 1 CHIEFTEC + 1 Deepcool).
- ~~#2: расширение PSU-whitelist~~ — частично. **+5 доменов** в
  `OFFICIAL_DOMAINS`, верифицированы WebFetch'ем:
  `exegate.ru`, `crown-micro.com`, `gamemaxpc.com`, `formulav-line.com`,
  `super-flower.com.tw`. По остальным (HSPD/BLOODY/SAMA/Gooxi/Foxconn)
  спеков либо нет, либо это OEM/B2B без отдельных datasheet —
  оставлены до возникновения реальной потребности. Большинство
  топ-PSU-вендоров (thermaltake/deepcool/aerocool/coolermaster/corsair/
  bequiet/evga/xpg/silverstonetek/raijintek/lian-li/asus/msi/gigabyte/
  powerman-pc.ru/hiper.ru/digma.ru/accord-pc.ru/formula-pc.ru/
  fox-line.ru/acd-group.com) уже были в whitelist'е до 5.0c —
  они доступны для PSU-обогащения без дополнительных изменений.
- **Whitelist matching укреплён**: явный
  `_OFFICIAL_DOMAINS_LOWER = frozenset(d.lower() for d in OFFICIAL_DOMAINS)`
  в [`validators.py`](../app/services/enrichment/claude_code/validators.py)
  — страховка от регрессий, если в будущем кто-то добавит домен с
  заглавной буквы. Тест `test_url_host_case_insensitive` проверяет
  4 варианта регистра для `deepcool.com`.
- ~~Открытое #1 (нормализация регистра psus.manufacturer)~~ — НЕ
  закрыто, отложено в 5.1. Обоснование: case-insensitive matching
  whitelist'а в URL-валидаторе (см. выше) уже снимает практическую
  проблему — AI ходит на правильные домены вне зависимости от регистра
  manufacturer'а в БД. UPDATE на унификацию регистра — косметика, не
  блокирует AI-обогащение.

**Закрыто этапом 5.1b (2026-05-05).**

- ~~Exporter не фильтрует `is_hidden=TRUE`~~ — обнаружено на 5.1a:
  `enrich_export_prod.py --category psu` выгрузил 240 items вместо
  ожидаемых 144 (плюс 97 уже скрытых на 5.0a/b/c позиций — адаптеры,
  не-PSU, mining-PSU). Защитные слои в `psu.md` корректно вернули бы
  им honest-null, но AI потратил бы тулколлы впустую и pending/
  раздулся бы в 1,67×. На 5.1b в
  [`exporter._build_select_sql`](../app/services/enrichment/claude_code/exporter.py)
  добавлен жёсткий префикс `WHERE is_hidden = FALSE AND (...)` —
  работает для всех 8 категорий, не только PSU. Покрыто тестом
  `test_export_skips_hidden_components`.

## 16. Storage audit (11.6.2.6.0a/b)

**Контекст.** На начало этапа 6.0 в категории `storages` было 1187
видимых строк. NULL по обязательным/важным полям: `interface` 97,
`form_factor` 95, `storage_type` 9, `capacity_gb` 4. Аудит
[`scripts/_storage_audit.py`](../scripts/_storage_audit.py) на проде
(этап 6.0a) показал четыре класса проблем.

| # | Проблема | Объём | Стратегия |
|---|---|---:|---|
| 1 | Аксессуары, ошибочно классифицированные как storage. | 2 (`id 782` Kingston SNA-BR2/35, `id 1133` Digma DGBRT2535 — рамки 2.5"→3.5"). | Детектор `is_likely_non_storage` в [`shared/component_filters.py`](../shared/component_filters.py), upstream-классификация в `orchestrator.py`, разовая чистка — `scripts/reclassify_storage_misclassified.py`. |
| 2 | Чужие в `motherboards`. | 3 (`id 794` ASUS E5402WVAK моноблок, `id 805` ESD-S1CL enclosure, `id 811` ESD-S1C enclosure). | Миграция [`migrations/025_storage_misclassification.sql`](../migrations/025_storage_misclassification.sql): только UPDATE → `is_hidden=TRUE`, без INSERT (ни в storages, ни куда-либо ещё). |
| 3 | bucket `manufacturer='unknown'`. | ~315 (в основном ExeGate M.2 Next/NextPro/NextPro+, плюс отдельные Apacer/Silicon Power/Netac/WD/Patriot/Crucial и др.). | `scripts/fix_storage_manufacturer.py --recover` — regex по `model + supplier_prices.raw_name`, 30+ storage-брендов с приоритетом от длинных к коротким. |
| 4 | Регистровый разнобой 14 канонических брендов. | 354 локально (топ: WD→Western Digital 165, ADATA→A-DATA 49, Samsung Electronics→Samsung 27, SEAGATE→Seagate 19, PATRIOT→Patriot 19, SHENZHEN KINGSPEC ELECTRONICS TECHNOLOGY CO LTD→KingSpec 18, NETAC→Netac 17, TOSHIBA→Toshiba 11, AGI TECHNOLOGY CO., LTD→AGI 9). | `scripts/fix_storage_manufacturer.py --normalize` с маппингом по lower-case ключам и prefix-match для длинных корпоративных форм («Samsung Electronics Co., Ltd.» и т. п.). |

**Закрыто этапом 6.0b (2026-05-05).**

- ~~#1: детектор non-storage~~ —
  [`is_likely_non_storage`](../shared/component_filters.py) ловит
  узким regex'ом фактический мусор (крепления для SSD/HDD, переходники
  2.5" без контекста GB, конверсия 2.5"→3.5", card-reader/USB-hub) +
  профилактически card-reader / кардридер / USB-hub /
  USB-концентратор. Защитные слои: `capacity_gb ≥ 32`, непустой
  `storage_type`, форм-факторные/технологические маркеры NVMe / M.2 /
  2280 / mSATA / U.2 в имени. Слова «SSD»/«HDD» намеренно НЕ включены
  в защиту — они появляются в самих триггер-фразах вида «крепления
  для SSD/HDD» и заблокировали бы основной кейс id=782/1133. 26
  юнит-тестов в `tests/test_shared/test_non_storage_detector.py`.
- ~~#1: upstream-классификация~~ — в
  [`orchestrator.py::_create_skeleton`](../app/services/price_loaders/orchestrator.py)
  при `table == "storages"` детектор вызывается на стадии создания
  скелета: рамка 2.5"→3.5" / card-reader / USB-hub из новых прайсов
  скрывается сразу, AI-обогащение 6.1 не тратит тулколлы.
- ~~#1: разовая чистка~~ — `scripts/reclassify_storage_misclassified.py`
  идемпотентен (один audit-event, общий backup-rollback).
- ~~#2: миграция misclassified в motherboards~~ — миграция 025
  идемпотентна (UPDATE с `AND is_hidden = FALSE`), на проде применяется
  через `apply_migrations.py` при ближайшем редеплое. Сохранение
  `supplier_prices`-связок намеренно: при следующей загрузке прайсов
  компонент остаётся скрытым, но ссылки не ломаются.
- ~~#3 + #4: recover + normalize manufacturer~~ — единый скрипт
  `scripts/fix_storage_manufacturer.py` с режимами `--recover` /
  `--normalize` / `--apply` (запуск обоих режимов последовательно
  recover → normalize). По умолчанию dry-run. Локально (`--apply`):
  **212 recovered + 354 normalized** за один прогон, повторный запуск
  даёт **0 + 0** (идемпотентность).
- ~~Расширение storage-whitelist~~: **+10 доменов** в
  `OFFICIAL_DOMAINS`, верифицированы WebFetch / WebSearch:
  `crucial.com`, `samsung.com`, `transcend-info.com`, `adata.com`,
  `solidigm.com`, `silicon-power.com`, `patriotmemory.com`,
  `sandisk.com`, `synology.com`, `kioxia.com`. До 6.0b в
  storage-секции было только 5 доменов (`kingston.com`,
  `westerndigital.com`, `seagate.com`, `netac.com`, `apacer.com`),
  AI-обогащение 6.1 без расширения отказывалось ходить на
  datasheet'ы Crucial MX/BX, Samsung 980, Transcend SSD220/MTE220,
  Patriot P210, A-DATA SU650 и т. д.

**Метрики этапа (локальная БД, 6.0b apply):**

- Помечено `is_hidden=TRUE` детектором non-storage: **1** (`id 1099`
  Digma DGBRT2535 локально; на проде их два — id 782 + id 1133).
- Восстановлено `manufacturer` (recover) regex'ом по
  supplier_prices.raw_name: **212** (топ: ExeGate 25, Apacer 23,
  Silicon Power 23, Netac 22, Western Digital 21, Patriot 13,
  Crucial 11, MSI 11, A-DATA 10, Kingston 10).
- Нормализовано `manufacturer` (normalize) к каноническим формам:
  **354** (топ: WD→Western Digital 165, ADATA→A-DATA 49, Samsung
  Electronics→Samsung 27, SEAGATE→Seagate 19, PATRIOT→Patriot 19,
  SHENZHEN KINGSPEC ELECTRONICS TECHNOLOGY CO LTD→KingSpec 18).

**Прод-метрики (apply 6.0b → before 6.1b → after 6.1b):**

| Поле          | До 6.0b | До 6.1b (= после 6.0b) | После 6.1b |
|---------------|--------:|------------------------:|-----------:|
| total_visible |    1187 |                  1185   |      1185  |
| interface     |    1090 |                  1089   |      1156  |
| form_factor   |    1092 |                  1091   |      1137  |
| storage_type  |    1178 |                  1177   |      1178  |
| capacity_gb   |    1183 |                  1183   |      1184  |

После 6.0b видимость снизилась на 2 (id 782 Kingston SNA-BR2/35 и id
1133 Digma DGBRT2535-2.5"→3.5" frame), что и было целью миграций 025
+ 026. После 6.1b interface закрыт на +67, form_factor на +46
(см. §17 / roadmap §11.6.2.6.1b).

## 17. Storage AI-обогащение (11.6.2.6.1b) — итоги

**AI-блок Storage закрыт.** Все 6 batch'ей (160 items) обработаны
через Claude Code WebSearch/WebFetch с применением 5 защитных слоёв
из [`storage.md`](../enrichment/prompts/storage.md). Результаты в
`enrichment/done/storage/batch_001..006_*.json`.

Импорт локально (Win11 dev, `enrich_import.py --keep-source`):
**35 items / 37 полей** (interface 22, form_factor 15), 0 ошибок
валидации. Подробности — в roadmap §11.6.2.6.1b. Прод-метрики
(до/после) — там же после ШАГ 5–6.

**Honest-null breakdown** (~62 полей null с reason):
- 26 полей: External USB-SSD (валидатор-ENUM, см. §18 ниже).
- 9 полей: U.2/U.3 form factor (валидатор-ENUM, §18).
- 13 полей: AMD Radeon R5 — datasheet вне whitelist-доменов
  (Galaxy/AMD-OEM, EOL, amd.com не публикует spec-страницы).
- 12 полей: QUMO Novation — `qumo.ru` вне whitelist.
- 18 полей: не-storage в категории (5 DDR-RAM Silicon Power/AGI/Digma
  + 1 кулер Digma D-CPC95-PWM2, 4 поля null × 6 items = частично 18
  с учётом разной длины to_fill).
- 3 поля: СЭМПЛ-позиции без производителя (SCY/MS/CBR-test).
- 1 поле: Hikvision — `hikvision.com` вне whitelist.
- 2 поля: Micron Enterprise (5300 PRO / 7450 PRO) — `micron.com`
  вне whitelist; Crucial-консумерская ветка не включает DC-серии.

## 18. Validator storage не поддерживает USB / External / U.2 — расширить при потребности

`_v_storage_form_factor` принимает только `2.5"/3.5"/M.2/mSATA`,
`_v_storage_interface` — только `SATA/NVMe/SAS`. Это намеренное
ограничение под текущую матрицу совместимости конфигуратора (внешние
SSD не входят в сборку ПК). Реально продаваемые форм-факторы и
интерфейсы, **не покрытые** валидатором, на 11.6.2.6.1b ушли в
honest-null с reason «вне enum валидатора, техдолг расширения»:

- **External USB-SSD** (~13 items): A-DATA SC740/SC750/SD620/SD810/
  SE880, Silicon Power DS72. У них `interface ∈ {USB 3.2 Gen 1/2,
  USB-C, Thunderbolt}`, `form_factor = External` (нет 2.5"/M.2).
  `storage_type=SSD` и `capacity_gb` корректно пишутся в БД из
  `current` или regex'а.
- **U.2/U.3 enterprise SSD** (~9 items): Samsung PM1733, Intel/
  Solidigm P4510, P4610, D7-P5510, D7-P5520, D5-P5530, P5620.
  `form_factor = U.2` (физически 2.5", но электрически SFF-8639/U.2),
  `interface = NVMe` (валидатор это принимает), `storage_type = SSD`
  и `capacity_gb` штатно.

**Объём пробела суммарно**: ~22 видимых items. Решение: оставить как
есть до момента, когда конфигуратор начнёт поддерживать сборку под
портативные SSD (внешние корпуса) или серверные шасси с U.2-слотами
(SFF-8639). Тогда:

- Расширить enum `_v_storage_form_factor` на `External` / `U.2` / `U.3`
  / `E1.S` / `E3.S` (минимум — `External` и `U.2`).
- Расширить enum `_v_storage_interface` на `USB 3.2 Gen 1` /
  `USB 3.2 Gen 2` / `USB-C` / `Thunderbolt 3` / `Thunderbolt 4`.
- Миграция БД: на текущей схеме `storages.form_factor` и
  `storages.interface` — `TEXT` без CHECK constraint, поэтому миграция
  чисто на уровне валидатора + повторный AI-проход по этим items без
  правки таблицы.
- Конфигуратор: добавить флаг «External SSD» в матрицу совместимости
  (всегда совместим, не занимает SATA/M.2-слот мат.платы).

Альтернатива (если внешние/U.2 не нужны): пометить эти 22 items
`is_hidden=TRUE` через миграцию вида 026 (ExeGate Kingston SNA), как
сделано на 6.0b для misclassified. Тогда они исчезнут из видимого
каталога, и AI 6.1b честно их не трогал бы.

## 19. Закрытие AI-блока 11.6.2.x — финальная сводка покрытия

На 2026-05-05 после этапа 11.6.2.7 AI-блок 11.6.2.x **закрыт**:
прогрев валидаторов / whitelist / детекторов мусора / промптов
завершён, выполнены прогоны AI по 6 категориям (cooler 11.6.2.1-2,
cpu 11.6.2.3, case 11.6.2.4, psu 11.6.2.5, storage 11.6.2.6,
motherboard 11.6.2.7), остаточные NULL зафиксированы как
known-unknowns ниже.

### Покрытие ключевых полей по 6 категориям (прод)

Сводка через
[`scripts/_ai_block_coverage_prod.py`](../scripts/_ai_block_coverage_prod.py),
запросы по `is_hidden = FALSE`. % filled — доля строк с `IS NOT
NULL` (для array-полей дополнительно `array_length>0`).

| Категория     | Total visible | Поле                          | % filled |
|---------------|--------------:|-------------------------------|---------:|
| gpus          |          798  | tdp_watts                     |   74.4 % |
| gpus          |          798  | video_outputs                 |   76.9 % |
| gpus          |          798  | vram_gb                       |   98.0 % |
| gpus          |          798  | vram_type                     |   97.9 % |
| coolers       |         1076  | max_tdp_watts                 |   64.4 % |
| coolers       |         1076  | supported_sockets             |   82.3 % |
| cases         |         1946  | has_psu_included              |   95.1 % |
| cases         |         1946  | supported_form_factors        |   91.1 % |
| cases         |         1946  | included_psu_watts (when has) |   96.7 % |
| psus          |         1415  | power_watts                   |   95.7 % |
| storages      |         1185  | interface                     |   97.6 % |
| storages      |         1185  | form_factor                   |   95.9 % |
| storages      |         1185  | storage_type                  |   99.4 % |
| storages      |         1185  | capacity_gb                   |   99.9 % |
| motherboards  |          963  | chipset                       |  100.0 % |
| motherboards  |          963  | socket                        |   99.9 % |
| motherboards  |          963  | memory_type                   |  100.0 % |
| motherboards  |          963  | has_m2_slot                   |  100.0 % |

### Известные пробелы (NOT решаются на 11.6.2.x — известные техдолги)

Каждый пункт ниже остаётся **открытым** и НЕ закрывается на финале
блока. Их закрытие — отдельные этапы 11.6.3.x / 12.x.

- **§18 — Validator storage USB/External/U.2** (~22 items): расширение
  enum `_v_storage_form_factor` / `_v_storage_interface` зависит от
  решения «нужны ли внешние SSD и U.2 в матрице совместимости
  конфигуратора». Если нет — закрыть `is_hidden=TRUE` вторым проходом
  миграции 028 в стиле 026.
- **Ginzzu PSU оф.сайт офлайн** (22 items): `ginzzu.com` офлайн на
  весь период 11.6.2.5.x. AI делал honest-null без обращения к
  WebSearch. Закроется самостоятельно, если сайт когда-нибудь
  вернётся; альтернативно — миграция-скрытие.
- **PowerMan серверные / OEM** (4 items, 11.6.2.5.1): часть моделей
  есть только на `powerman-pc.ru` под нестандартными MPN (PMP-серия
  для серверов). На whitelist-домен попадают, но карточка отсутствует.
- **AMD Radeon R5 SSD EOL** (~13 items, 11.6.2.6.1b): линейка снята
  с производства, datasheet'ы не публикуются на `amd.com` (нет
  раздела SSD). Galaxy/AMD-OEM, без whitelist-источника.
- **СЭМПЛ-позиции без MPN** (3 storage items): тестовые образцы
  поставщика SCY/MS/CBR, без публичной карточки.
- **Cooler `max_tdp_watts` 64 %** — самый большой остаток. Профильные
  low-profile / half-height кулеры (Akasa AK-CC7108EP01, ID-Cooling
  IS-30 / IS-40X / IS-50X, Thermalright AXP-90, SilverStone NT07/AR05)
  у вендоров часто не публикуют TDP в datasheet вообще («fits Intel
  stock-class TDP»). Расширение валидатора на «max_tdp ≤ 65W
  fallback by socket» — отдельная инициатива; пока остаётся NULL.
- **GPU `tdp_watts` 74 %** — entry-level / passive cards (GT 710,
  Quadro K420, Matrox C-серия, многие Radeon HD/R5/R7) и старые OEM
  без datasheet. Расширение через power-supply-recommendation
  (сводная таблица семейств) — отдельный этап.

### Что было сделано на этом блоке

- **11.6.2.0/1** — orchestrator, base infrastructure
  (`enrich_export.py` / `enrich_export_prod.py` с `--keep-source`,
  `enrich_import.py`), validators, whitelist `OFFICIAL_DOMAINS`.
- **11.6.2.1-2** — cooler `supported_sockets` (~1.1k items),
  `max_tdp_watts` (частично).
- **11.6.2.3** — CPU `base_clock_ghz`, `turbo_clock_ghz`,
  `package_type` (хвост из 5+ items).
- **11.6.2.4** — case `has_psu_included` /
  `supported_form_factors` / `included_psu_watts` (двухпроходный
  подход — first has_psu_included, потом для TRUE — ватты).
- **11.6.2.5** — psu `power_watts` (144 видимых items, +5 whitelist
  доменов: exegate.ru, crown-micro.com, gamemaxpc.com, formulav-line.com,
  super-flower.com.tw).
- **11.6.2.6** — storage `interface` / `form_factor` / `storage_type`
  / `capacity_gb` (160 items, +13 whitelist доменов суммарно через
  два подэтапа: 6.0b добавил 10, 7 добавил 3).
- **11.6.2.7** — финал: чистка хвостов storage (5 misclassified RAM/
  cooler через расширенный детектор `is_likely_non_storage`,
  миграция 027 для interface SAS→SATA bug у WD Red WDS100T1R0A,
  +3 whitelist для qumo/micron/hikvision), AI-обогащение
  motherboards (chipset HM65 для AFOX AFHM65-ETH8EX и B250 для
  AFOX AFB250-BTC12EX, inline без batch-pipeline из-за объёма
  2 платы), сводная статистика этого блока.

