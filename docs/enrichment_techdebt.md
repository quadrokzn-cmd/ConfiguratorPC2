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
| 11.6.2.4.1b | case | 2026-05-05 | AI-обогащение тех же 230 items через 11 параллельных subagent'ов + bulk-null процессор для 100 non-whitelist items. Локальный импорт: 165 полей у 92 компонентов, 0 отклонений. Прод-импорт: см. §14. |

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
