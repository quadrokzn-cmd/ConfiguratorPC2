# Diff-отчёт `price_loaders/`: QT (staging) vs C-PC2 (active)

**Дата:** 2026-05-08
**Этап:** 3/9 слияния (landing zone)
**Цель:** инвентаризация перед Этапом 4 (слияние `price_loaders/`).
**Не делается на этом этапе:** правки кода, разрешение конфликтов.

## TL;DR

`price_loaders/` C-PC2 и QT — это **два разных пакета на общем ДНК**. Общее имя обманчиво: они решают разные задачи, читая один и тот же входной формат (Excel-прайсы дистрибьюторов).

- **C-PC2-версия** — для конфигуратора ПК. Работает в боевом режиме, активно развивается (последний коммит — Этап 12.5d, Treolan ID-mapping). 6 поставщиков-fetcher'ов, раздельные таблицы по категориям ПК (motherboard / ram / gpu / storage / case / psu / cooler / cpu), ambiguous-кейсы, disappeared-логика, report_json для UI.
- **QT-версия** — для аукционного матчинга по принтерам/МФУ. Заморожена 2026-05-08. 4 живых адаптера + 4 заглушки. Единая таблица `nomenclature` с `category ∈ {printer, mfu, ignore}`, упрощённый алгоритм (один результат, ORDER BY id LIMIT 1).

**Ключевой вывод:** прямое слияние «общих» файлов невозможно. На Этапе 4 правильная стратегия — взять C-PC2-версию как канон, расширить её на принтер-домен (новая таблица `printers_mfu` по плану Этапа 6 → миграция и категоризация), и подмешать QT-категоризацию как доп. карты `_PRINTER_*`-классификаторов.

---

## Соответствие путей и поставщиков

| | C-PC2 (active) | QT (staging) |
|---|---|---|
| Путь | `app/services/price_loaders/` | `auctions_staging/app/modules/auctions/price_loaders/` |
| Импорт engine | `app.database.SessionLocal` (ORM Session) | `app.core.db.get_engine()` (SQLAlchemy Connection) |
| Поиск supplier | `WHERE name = :name` (BasePriceLoader.supplier_name) | `WHERE code = :code` (BasePriceLoader.supplier_code) |
| Доменная таблица | `motherboard / ram / gpu / storage / case / psu / cooler` (см. `CATEGORY_TO_TABLE` в `app.services.enrichment.base`) | единая `nomenclature` (sku / mpn / gtin / brand / category) |
| Поставщики (живые) | 6: ocs, merlion, treolan, netlab, resurs_media, green_place | 4: ocs, merlion, treolan, resursmedia |
| Поставщики (заглушки) | — | 4: asbis, sandisk, marvel, a1tis (по 800 байт каждая, `NotImplementedError`) |
| Имя файла Resurs Media | `resurs_media.py` (snake_case) | `resursmedia.py` (без подчёркивания) |
| Класс адаптера | `OcsLoader / MerlionLoader / ...` | `OcsPriceLoader / MerlionPriceLoader / ...` |
| Ключ в `LOADERS` | `"ocs", "merlion", "treolan", "netlab", "resurs_media", "green_place"` | `"ocs", "merlion", "treolan", "resursmedia", "asbis", "sandisk", "marvel", "a1tis"` |

---

## Файл-за-файлом

Базовая линия diff — QT-версия. «Added/Removed lines» = что изменено в C-PC2 относительно QT (через `diff -u qt cpc`). Полные `diff_*.txt` лежат в `auctions_staging/_diff_reports/`.

### `__init__.py` — фабрика загрузчиков

| | C-PC2 | QT |
|---|---:|---:|
| Размер | 3 610 B | 1 682 B |
| Строк | 72 | 51 |
| diff vs QT | +45 / −26 |  |

**Структурно:** оба файла — реестр LOADERS (dict ключ→класс) + `get_loader(key)` + `detect_loader(filepath)`. Логика идентична. Различаются только наборами импортов и набором ключей. C-PC2 имеет шапку-комментарий с описанием поставщиков и архитектуры (этап 7+11.1); QT — без шапки.

### `base.py` — абстрактный интерфейс адаптера

| | C-PC2 | QT |
|---|---:|---:|
| Размер | 1 688 B | 411 B |
| Строк | 32 | 19 |
| diff vs QT | +16 / −4 |  |

**Структурно:** оба определяют ABC `BasePriceLoader` с `detect(filename) -> bool` и `iter_rows(filepath) -> Iterator[PriceRow]`. Различия:

- Атрибут класса: C-PC2 `supplier_name` (берётся из `suppliers.name`), QT `supplier_code` (берётся из `suppliers.code`).
- C-PC2 имеет docstring'и в шапке и у методов; QT — без них.

### `models.py` — `PriceRow`

| | C-PC2 | QT |
|---|---:|---:|
| Размер | 2 936 B | 470 B |
| Строк | 57 | 23 |
| diff vs QT | +28 / −3 |  |

**Структурно:** оба — `@dataclass PriceRow` с одинаковым набором полей (`supplier_sku, mpn, gtin, brand, raw_category, name, price, currency, stock, transit, our_category, row_number`). Поля совпадают по имени и порядку.

**Семантические различия:**

- **Тип `our_category`:**
  - QT: `OurCategory = Literal["printer", "mfu", "ignore"]`, default `"ignore"`. Закрытое перечисление под аукционы (печатная техника).
  - C-PC2: `our_category: str | None`, default отсутствует (обязательное поле). Открытое значение из `CATEGORY_TO_TABLE` (`cpu / motherboard / ram / gpu / storage / case / psu / cooler`). `None` означает «не нужно загружать».
- **Поле `raw_category`:** в QT обязательное `str` (default — `""`), в C-PC2 — `str` без default (видимо позиционное).
- **Шапка-документация:** C-PC2 содержит подробные комментарии к каждому полю (зачем оно, кто заполняет); QT — голый dataclass.

### `matching.py` — поиск компонента/SKU

| | C-PC2 | QT |
|---|---:|---:|
| Размер | 6 610 B | 2 481 B |
| Строк | 145 | 88 |
| diff vs QT | +111 / −58 |  |

**Структурно:** оба определяют константы источников (`EXISTING / MATCH_* / NO_MATCH`) + `MatchResult` dataclass + `resolve(...) -> MatchResult`. Архитектурно похожи, но логика и набор кейсов разные:

- **Источники сопоставления:**
  - QT: `EXISTING / MATCH_BRAND_MPN / MATCH_GTIN / NO_MATCH` (4 кейса). MATCH_BRAND_MPN в QT — это «бренд + MPN» через `LOWER(brand) = LOWER(:brand) AND mpn = :mpn`.
  - C-PC2: `EXISTING / MATCH_MPN / MATCH_GTIN / AMBIG_MPN / AMBIG_GTIN / NO_MATCH` (6 кейсов). C-PC2 различает «нашёл одного» vs «нашёл больше одного» — для UI это важный сигнал, идущий в `unmapped_supplier_items`.

- **Связь с доменом:**
  - QT ищет в одной таблице `nomenclature` по полям `mpn / gtin`.
  - C-PC2 выбирает таблицу через `_table_for(our_category)` (whitelist через `ALLOWED_TABLES` + `CATEGORY_TO_TABLE`), ищет по колонке `sku` или `gtin`. Защита от инъекции комментирована.

- **`MatchResult`:**
  - QT: `source, nomenclature_id`.
  - C-PC2: `source, component_id, ambiguous_ids: list[int]` (для ambiguous-кейсов).

- **Ключ существующей записи:**
  - QT: `supplier_id + supplier_sku → nomenclature_id`.
  - C-PC2: `supplier_id + supplier_sku → component_id`.

### `orchestrator.py` — главный раннер загрузки

| | C-PC2 | QT |
|---|---:|---:|
| Размер | 43 613 B | 8 625 B |
| Строк | 921 | 290 |
| diff vs QT | +753 / −177 |  |

C-PC2 в **5×** больше QT и реализует существенно больше функций. Структурно оба определяют `Counters` + `load_price(...)`, но дальше расходятся:

**QT (290 строк):**
- `_get_supplier(conn, supplier_code)` → `(int, str)` через `WHERE code = :c`.
- `_build_sku(row)` строит каноничный sku вида `brand:mpn` / `mpn` / `gtin:...` / `raw:...`.
- `_ensure_unique_sku` добавляет суффикс `#1, #2, ...` при коллизии.
- `_insert_nomenclature` — INSERT в единую таблицу `nomenclature`.
- `_upsert_supplier_price` — `INSERT … ON CONFLICT (supplier_id, nomenclature_id) DO UPDATE`.
- `_record_upload` пишет в `price_uploads` с минимальным `notes`-текстом.
- `_process_row`: skip при `category=ignore`, `currency!=RUB`, `price<=0`. Resolve → если EXISTING/MATCH_BRAND_MPN/MATCH_GTIN — upsert; если NO_MATCH — INSERT новой `nomenclature`-записи и upsert.
- `load_price`: SAVEPOINT на каждую строку (`conn.begin_nested()`), в конце `recompute_cost_base(supplier_id)` (контракт с агентом 1А-β).

**C-PC2 (921 строка) — расширения, которых нет в QT:**
1. **Раздельные таблицы по категориям** через `CATEGORY_TO_TABLE` (cpu→cpus, motherboard→motherboards и т.д.). Каждая категория имеет свою схему колонок (sku, gtin, manufacturer, …).
2. **Подбор кандидатов** через фильтры из `shared.component_filters` (`is_likely_case_fan`, `is_likely_pcie_riser`, `is_likely_drive_cage`, `is_likely_psu_adapter`, …) — выкидывает позиции, которые попали под общий MPN, но семантически не подходят (вентилятор в корпусах, переходник в БП и т.п.).
3. **Ambiguous-кейсы** (AMBIG_MPN / AMBIG_GTIN) → запись в `unmapped_supplier_items` с кандидатами, чтобы менеджер разобрал руками через `/admin/mapping`.
4. **Disappeared-логика** (Этап 11.4): позиции, которые были активны (stock+transit>0) до загрузки, но отсутствуют в новом прайсе, помечаются `stock=0, transit=0`. Ключ — защита от ошибочной загрузки: при `status='failed'` disappeared НЕ применяется.
5. **Расширенный отчёт** в `price_uploads.report_json` (JSONB) — для UI `/admin/price-uploads` с кнопкой «Подробности».
6. **Расширенные счётчики** в `Counters`: `updated, added, unmapped_created, unmapped_ambiguous, unmapped_new, disappeared, disappeared_skus[]`.
7. **`raw_name`** добавлен миграцией 022 — пишется в `supplier_prices` и обновляется при перезагрузке.

### `merlion.py` / `ocs.py` / `treolan.py` — адаптеры дистрибьюторов

| | C-PC2 | QT | diff |
|---|---:|---:|---:|
| `merlion.py` | 11 421 B / 232 L | 5 365 B / 187 L | +101 / −54 |
| `ocs.py` | 9 102 B / 200 L | 5 287 B / 168 L | +81 / −48 |
| `treolan.py` | 10 614 B / 240 L | 6 489 B / 218 L | +88 / −61 |

**Структурно:** Оба читают тот же Excel-формат у того же поставщика, общие константы колонок (например, у Merlion: `_COL_GROUP_1=0, _COL_NUMBER=4, _COL_MPN=6, _COL_NAME=7, _COL_PRICE_USD=9, _COL_PRICE_RUB=10`), общая хелпер-функция `_parse_price` / `_parse_int`, общая структура `iter_rows`. **Это видно невооружённым глазом — QT-адаптеры были портированы из ConfiguratorPC2 (это явно зафиксировано в плане: «Архитектура парсера прайсов: copy+adapt из ConfiguratorPC2 в auctions/price_loaders/»).**

**Семантические различия — категоризация:**

- **C-PC2 `merlion.py`:** `_CATEGORY_MAP: dict[tuple[str,str,str], str]` — 20+ маппингов вида `("Комплектующие для компьютеров", "Материнские Платы", "Socket-1700") → "motherboard"`. Распознаёт DDR3/4/5, PCIE/PCI-E, ATX/mATX, Socket-AM4/AM5/1700/1851, Видеокарты, Накопители SSD/Жесткие Диски, Корпуса, БП, Кулеры. Категории «Принтеры», «МФУ» в C-PC2-merlion **не упоминаются** (они для C-PC2 — out-of-scope).
- **QT `merlion.py`:** `_PRINTER_GROUPS: set[(str,str)] = {("Периферия и аксессуары", "Принтеры")}` + `_G3_CATEGORY_MAP: dict[str, OurCategory]` для G3-уровня: «МФУ лазерные → mfu», «Лазерные → printer», «МФУ струйные → mfu», «Струйные → printer», «Термопринтеры/Матричные/Мини-Фото-принтеры → ignore». Категории «Материнские Платы», «Видеокарты» и т.д. для QT-merlion **out-of-scope** (попадают в `ignore`).
- **Аналогично OCS / Treolan / Resurs Media** — каждая версия живёт в своей категориальной системе.

### Фичи, существующие только в C-PC2 (нет в QT)

- `netlab.py` (380 строк, 18.8 KB) — адаптер Netlab (Excel «DealerD.xlsx», лист «Цены»; принимается также `.zip`). В QT отсутствует.
- `resurs_media.py` (249 строк, 11.1 KB) — отдельный файл (в QT он называется `resursmedia.py` и весит почти вдвое меньше — 212 строк / 6.1 KB).
- `green_place.py` (221 строка, 10.4 KB) — адаптер Green Place (Excel «Price_GP_*.xlsx», лист «Worksheet», категории в трёх колонках). В QT отсутствует.
- `candidates.py` (123 строки, 5.8 KB) — подбор «похожих» кандидатов для UI `/admin/mapping`. В QT отсутствует.
- `_qual_stock.py` (25 строк) — служебная утилита. В QT отсутствует.
- **Treolan ID-mapping (Этап 12.5)** — категоризация Treolan через ID-mapping категорий (а не per-position substring), вынос ID-mapping метрик в `report_json`. Активная разработка последних коммитов C-PC2 (`4158a44`, `5cb6d48`).
- **Подключение к `app.database.SessionLocal` (ORM Session)** + `shared.component_filters` (8 фильтров `is_likely_*`).
- **Disappeared-логика, ambiguous-кейсы, report_json, расширенный `_record_upload`** (см. orchestrator выше).
- **Brand-нормализация** через `app.services.enrichment.base` / отдельный `shared/component_filters.py` (в QT тоже есть `app.modules.auctions.catalog.brand_normalizer`, но это **другой** модуль с другим словарём).

### Фичи, существующие только в QT (нет в C-PC2)

- **`OurCategory = Literal["printer", "mfu", "ignore"]`** — закрытый Literal, делает `unknown` физически невозможным. В C-PC2 `our_category: str | None` (открытый).
- **Категоризация по колонкам прайса** (Этап «доделка категоризации Волны 1А-α»), а не regex по `name`. У Merlion — через колонки G1/G2/G3, у OCS — через `CategoryName`, у Treolan — через категорию-сепаратор `«->»`, у Resurs Media — через двухуровневые разделители. Это решено для **printer/mfu**-домена; для C-PC2-домена аналогичная работа уже была сделана раньше (для motherboard/ram/...).
- **Поле `our_category="ignore"`** как явный сигнал «эту строку точно пропустить» (в C-PC2 — `None`).
- **Заглушки adapter'ов на 800 байт** (asbis/sandisk/marvel/a1tis) с `NotImplementedError` — место под будущие реализации, чтобы фабрика `LOADERS` могла отдать класс по ключу не падая.
- **`brand_normalizer`-словарь на 21 канон** (HP, HPE, Pantum, Canon, Kyocera, Konica Minolta, Xerox, Brother, Ricoh, Epson, Sharp, Lexmark, OKI, Toshiba, Samsung, Sindoh, Katusha IT, G&G, iRU, Cactus, Bulat) — заточен под печатные бренды (`app.modules.auctions.catalog.brand_normalizer`). У C-PC2 — другой словарь под ПК-бренды.
- **`_build_sku` каноникализация** для NO_MATCH-кейса (`brand:mpn` / `mpn` / `gtin:...` / `raw:...`). C-PC2 при no_match создаёт скелет компонента, но иначе (через категорию-таблицу).
- **SAVEPOINT (`conn.begin_nested()`) на каждую строку** — атомарность per-row. (В C-PC2 этот же паттерн заявлен в комментариях шапки orchestrator'а как «после этапа 6 SAVEPOINT на каждую строку», т.е. он **тоже есть в C-PC2**, но реализован в более сложном `_process_row`/`load_price`.)

### Файлы, которые отличаются именем (но решают одну задачу)

| C-PC2 | QT | Замечание |
|---|---|---|
| `resurs_media.py` (249 L) | `resursmedia.py` (212 L) | Один и тот же поставщик («Ресурс Медиа»), разные имена и реализации. На Этапе 4 надо принять одно имя и одну реализацию (вероятно, C-PC2 — она крупнее и активно в проде). |

---

## Что это значит для Этапа 4

Прямого «слияния файлов» не получится — это два разных пакета. Возможные стратегии:

1. **Канон C-PC2 + расширение printer/mfu (предпочтительно).** Берём `app/services/price_loaders/` C-PC2 как канон. На Этапе 6 (создание `printers_mfu` 9-й таблицы каталога) добавляем в `CATEGORY_TO_TABLE` запись `printer → printers_mfu` и `mfu → printers_mfu`. Из QT-адаптеров переносим только `_PRINTER_GROUPS` / `_G3_CATEGORY_MAP` и подобные (40-50 строк на адаптер). Заглушки QT (asbis/sandisk/marvel/a1tis) — отбрасываем или сохраняем как `# TODO`-комментарии. Преимущество: не теряем активную C-PC2 разработку (Treolan ID-mapping, disappeared-логику, report_json, ambiguous-кейсы, candidates).
2. **Параллельные пакеты.** Оставить `app/services/price_loaders/` для ПК, создать `app/modules/auctions/price_loaders/` для печати. Дублирование 80% кода (`base`, `models`, `matching`, `orchestrator`). На post-MVP вынести общее в пакет `quadro_price_loaders/` (этот вариант явно предусмотрен в плане как post-MVP).
3. **Слияние в одном пакете.** Расширить `app/services/price_loaders/` до универсального для обоих доменов: добавить `our_category in {printer, mfu}` маппинги в каждый адаптер; доменно-зависимое поведение в orchestrator (выбор таблицы) переключается через `our_category`. Самое инвазивное.

Решение — за этапом 4. Этот отчёт — фактологическая база.

---

## Артефакты в этой папке

- `price_loaders_diff_2026-05-08.md` (этот файл).
- `diff___init__.py.txt`
- `diff_base.py.txt`
- `diff_models.py.txt`
- `diff_matching.py.txt`
- `diff_orchestrator.py.txt`
- `diff_merlion.py.txt`
- `diff_ocs.py.txt`
- `diff_treolan.py.txt`

Файлы `diff_*.txt` — сырой `diff -u qt cpc` для аудита (можно открыть в IDE для построчного просмотра).
