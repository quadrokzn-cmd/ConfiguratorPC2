# 2026-05-16 — Умный ingest аукционов (блокер Волны 3 закрыт)

## Задача

Переделать ingest аукционов с zakupki.gov.ru на «умный» режим (INSERT-new /
SKIP-unchanged / UPDATE-changed) и убрать DELETE + FK ON DELETE CASCADE.

**Контекст** (от собственника, после matching validation 2026-05-15):
до сегодня cron `auctions_ingest` каждые 2 часа делал безусловный
`DELETE FROM tender_items` при upsert каждого лота. FK
`matches.tender_item_id → tender_items` (CASCADE) каскадно убивал ВСЕ matches
каждые 2 часа, даже если лот не менялся. Из-за этого:
- уведомления Волны 3 (Telegram/Max) спамили бы дубликатами на одну и ту же
  позицию каждые 2 часа;
- до следующего ручного `run_matching` менеджер видел пустую сводку margin
  (matching validation 2026-05-15: ×11.7 matches появились ТОЛЬКО после
  ручного перематчинга — это был артефакт run_matching, не реальное состояние
  между ingest-тиками).

Это был блокер мини-этапа 9b — без него уведомления некорректны.

## Как решал

### Discovery (Фаза 1)

Спавнил Explore-агента для широкой карты `portal/services/auctions/ingest/`,
парсера zakupki, миграций по `auctions/matches`, scheduler-cron и
`scripts/run_matching.py`. Сам читал критичные файлы для уточнения деталей.

**Ключевые расхождения с промтом** (выправлял по ходу):
1. Таблицы называются **`tenders` / `tender_items` / `matches`**, а не
   `auctions`/`auction_items`. PK у `tenders` — `reg_number TEXT`.
2. Нет прямого FK `matches → auctions`. CASCADE-цепочка идёт через
   `tender_items`: `matches.tender_item_id → tender_items (CASCADE)` +
   `tender_items.tender_id → tenders (CASCADE)`. Поэтому корень
   FK-катастрофы — не `DELETE FROM tenders` (его никогда и не было), а
   `DELETE FROM tender_items` в `repository.upsert_tender:104` при каждом
   upsert.
3. zakupki НЕ отдаёт `last_modified` в HTML карточки. Решил детектить
   изменения через **content_hash** (SHA-256 от business-полей TenderCard,
   без `raw_html`) — это даже надёжнее, потому что фиксирует
   именно семантические изменения, а не косметику HTML.
4. Cron `auctions_ingest` не вызывал `run_matching` после upsert.
   matches появлялись только при ручном `scripts/run_matching.py`.
5. `match/repository.load_tender_items(engine, tender_id=...)` УЖЕ
   поддерживал фильтр по reg_number — `match_single_tender` собирался
   поверх существующей инфраструктуры без рефакторинга.

### Implementation (Фаза 2)

**Миграция `0039_auctions_smart_ingest.sql`**:
- `tenders.content_hash TEXT` (nullable для существующих строк, заполнится
  на первом ingest-тике после миграции),
- `tenders.last_modified_at TIMESTAMPTZ NOT NULL DEFAULT now()` (для
  audit-trail; заполнили `COALESCE(updated_at, ingested_at, now())` для
  legacy-строк),
- индекс `idx_tenders_last_modified_at` (для диагностических запросов
  «какие лоты менялись за последние N часов»),
- три FK переустановлены с CASCADE на NO ACTION через DO-блоки:
  `tender_items.tender_id → tenders`,
  `tender_status.tender_id → tenders`,
  `matches.tender_item_id → tender_items`.
- FK `matches.nomenclature_id → printers_mfu`
  (`fk_matches_nomenclature_id`, CASCADE из миграции 032) намеренно
  оставлен — это управление SKU-каталогом, не ingest'ом.

**Подводный камень с SQLAlchemy + миграциями**: текстовый парсер
`text()` парсит `:name` как bind-параметр даже внутри SQL-комментариев
и строковых литералов. Это сломало миграцию: `'tenders'::regclass`
парсилось как `:regclass` bind. Также `format('%I', ...)` не работал
из-за `%`-paramstyle psycopg2. Лечение:
- `to_regclass('name')` вместо `'name'::regclass`;
- `quote_ident(fk_name)` + конкатенация вместо `format('%I', fk_name)`;
- никаких `:` в комментариях и текстах миграции (даже в `RAISE EXCEPTION`).

**`repository.upsert_tender`** переписан на 3 ветки:
- `SELECT content_hash FROM tenders WHERE reg_number = :rn FOR UPDATE`
  (сериализует concurrent upsert'ы одного reg_number),
- `existing is None` → INSERT tender + items + status,
- `existing.content_hash == new_hash` → SKIP (tender_items нетронуты,
  matches живы — главная цель),
- иначе → UPDATE tender, `DELETE matches WHERE tender_item_id IN
  (...)`, DELETE/INSERT tender_items, обновить content_hash и
  last_modified_at = now().

`compute_content_hash(card, flags)` — детерминированная SHA-256
сериализация business-полей (customer, regions, contacts, nmck_total,
3 даты, ktru_codes отсортированные, items отсортированные по
position_num, flags), `raw_html` исключён.

**`match.service.match_single_tender(engine, reg_number)`** — новая
функция; не вызывает `clear_all_matches` (это full-run behavior) и
не делает aggregation. Идемпотентна через `save_matches`. Вызывает
`derive_single_position_nmck(engine, tender_id=reg_number)` (добавил
опциональный фильтр в repository.py) перед матчингом, чтобы single-item
лоты получили `nmck_per_unit` до загрузки items.

**`orchestrator.run_ingest_once`** — добавил `skipped`-счётчик в
`IngestStats` + `matches_inserted` для smoke-логов. После каждого
`upsert_tender`, если `inserted` или `updated` — вызывает
`match_single_tender(engine, reg_number)`. При SKIP не делает ничего.

**pg_advisory_lock внутри `run_ingest_once`** (`lock_id = 91234567`,
session-level, через `pg_try_advisory_lock`). Это защита от concurrent
запусков **между процессами**: portal FastAPI cron + офисный
ingest-worker (`scripts/run_auctions_ingest.py`) + локальные CLI.
В одном процессе FastAPI cron и `/admin/run-ingest{,-blocking}` уже
синхронизированы через `single_flight.ingest_lock` (threading.Lock).
advisory_lock покрывает межпроцессный case. Если lock занят — возвращаем
`IngestStats()` пустые и пишем WARN-лог.

Заодно обновил `scripts/reparse_cards.py` — добавил счётчик `skipped`
в его вывод (после моих изменений идемпотентный re-parse будет
говорить SKIP вместо UPDATE для неизменившихся карточек).

### Тесты (Фаза 3) — 13 новых

`tests/test_auctions/test_smart_ingest.py`:
- 4 теста на `compute_content_hash` (детерминизм, чувствительность к
  business-полям и items, нечувствительность к `raw_html`).
- 5 тестов на `upsert_tender`: INSERT-new, SKIP-unchanged,
  UPDATE-on-content-change, **regression: SKIP сохраняет matches**,
  **regression: UPDATE удаляет matches только этого reg_number**.
- 2 теста на `match_single_tender`: не трогает matches других лотов,
  не падает для несуществующего reg_number.
- 1 тест на FK NO ACTION: `DELETE FROM tenders WHERE reg_number = X`
  падает с foreign key violation (сторожевой — отловит, если кто-то
  откатит миграцию или вернёт CASCADE).
- 1 тест на pg_advisory_lock: занятый lock → `run_ingest_once`
  возвращает пустые IngestStats без обращения к ZakupkiClient.

### Deploy (Фаза 4)

- pytest полный прогон: **2068 passed, 4 skipped, 0 failed** (DoD: 2055+).
- auctions scope: **255 passed** (DoD: 242+; +13 за счёт smart-ingest).
- Миграция 0039 применена на dev `kvadro_tech` через
  `scripts/apply_migrations.py`. Применилась идемпотентно вместе с 0038
  (которая была backfill для supplier_prices_mfu, не была применена раньше).
- SQL-проверка на dev: `content_hash` + `last_modified_at` присутствуют,
  все 3 FK `confdeltype='a'` (NO ACTION). На dev сейчас 162 tenders, у
  всех `content_hash=NULL` — на первом ingest-тике после prod-деплоя они
  пройдут UPDATE-ветку и matches пересчитаются per-tender.
- Worktree-изоляция: `feature/smart-ingest-auctions` на базе `origin/master`.

## Решил ли — да

Полностью.
- DELETE убран из ingest. Единственный DELETE остаётся при UPDATE-ветке
  и затрагивает только конкретный лот, при этом matches удаляются явно
  ПЕРЕД items, чтобы NO ACTION не выкинул IntegrityError.
- FK NO ACTION на всех трёх кросс-табличных связях ingest-цепочки.
  Matches не теряются при ingest'е других лотов.
- matches пересчитываются per-tender сразу после INSERT/UPDATE.
- pg_advisory_lock закрывает межпроцессную race.

## Эффективно ли, что лучше

**Что сработало хорошо**:
- Explore-агент дал точную карту за один запрос, сэкономил 10+ Read/Grep.
- Discovery поймал расхождение «таблицы tenders, не auctions» — это могло
  бы стоить часа отладки, если бы я слепо повторял именования из промта.
- content_hash подход (вместо zakupki-side `last_modified`) оказался
  даже более надёжным: SHA от business-полей нечувствителен к
  HTML-разметке, ловит только реальные семантические изменения.
- `match/repository.load_tender_items(engine, tender_id=...)` уже
  поддерживал фильтр — `match_single_tender` оказался простой обёрткой,
  без рефакторинга.

**Где было больно**:
- SQLAlchemy `text()` парсит `:name` даже в SQL-комментариях и строках
  → понадобились две правки миграции (сначала `:rn` в комментарии,
  потом `::regclass`). На третьей попытке вышел `%I` через psycopg2
  paramstyle — пришлось перейти на `quote_ident()` + конкатенацию.
  **Урок**: при написании SQL-файлов для приложения через SQLAlchemy
  избегать `:` и `%` в литералах и комментариях.
- Worktree без `.env` — копировал руками. **Решение на будущее**:
  держать в репо `.env.example` (уже есть), и `git worktree` создаёт
  свой `.env` через `cp .env`.

**Что бы я сделал иначе**:
- Можно было сразу написать `to_regclass()`/`quote_ident()` без
  попыток с `::regclass`/`format('%I')` — если бы я заранее проверил
  стиль остальных миграций (миграция 032 уже использовала похожий
  DO-блок, но без regclass-cast).

## Как было — как стало

**Было** (до 2026-05-16):
- `auctions_ingest` каждые 2ч → `DELETE FROM tender_items` per лот → FK
  CASCADE убивает все matches → следующая 2-часовая дыра без matches.
- matches появлялись только при ручном `scripts/run_matching.py`.
- Концепция «Волны 3 — уведомления» теоретически работала, но в реальной
  жизни каждые 2 часа была бы дубликация на одну и ту же позицию.

**Стало**:
- `auctions_ingest` за тик: для каждого лота из ответа zakupki
  считаем `content_hash`. INSERT-new → match_single_tender → matches.
  SKIP-unchanged → НЕ трогаем tender_items, matches остаются на месте.
  UPDATE-changed → DELETE matches (явно) → DELETE/INSERT items → match.
- FK NO ACTION страхует от любых ошибочных DELETE на trinitarian
  ingest-цепочке.
- matches между ingest-тиками стабильны → уведомления Волны 3 будут
  слать только новые позиции.
- pg_advisory_lock защищает от race портал ↔ офисный worker ↔ CLI.

## Открытые задачи

- **Smoke на prod после деплоя** (собственнику): открыть Railway Deploy
  Logs portal-сервиса, увидеть «Применяю миграцию: 0039_auctions_smart_ingest.sql»
  и «Готово: применено 1 новых миграций». На следующем ingest-тике
  (07:00 МСК или вручную через `/admin/run-ingest-blocking`) — увидеть
  в логах `parsed=N inserted=M updated=K skipped=S matches_inserted=X`.
  На prod сейчас ingest идёт **с офисного сервера** (см. этап 9e.4.2),
  cron в portal scheduler отключён через `AUCTIONS_INGEST_ENABLED=false`.
  Поэтому smoke надо смотреть в логах офисного worker'а.
- **Однократный refill matches** после первого ingest-тика на prod:
  все 162 лота получат content_hash и пройдут UPDATE-ветку, matches
  пересчитаются. Если бы lot'ов было >10k и это вызвало бы проблемы —
  можно было бы пред-вычислить content_hash в миграции через PL/pgSQL,
  но на 162 строки overkill.
- **Backlog #18 (pytest-xdist DB contention)** — не закрывал, новые
  тесты в `test_smart_ingest.py` используют единый db_engine + autouse
  TRUNCATE, контеншена не добавляют. Pytest зелёный.
- **9b Telegram/Max-уведомления** теперь технически разблокирован.
  Следующий мини-этап Волны 3 — реализация Telegram-bot'а / Max-канала
  и UI-настроек подписок.

## Артефакты

- `migrations/0039_auctions_smart_ingest.sql` — миграция.
- `portal/services/auctions/ingest/repository.py` — `compute_content_hash`,
  переписанный `upsert_tender`, `UpsertResult.skipped`.
- `portal/services/auctions/ingest/orchestrator.py` — `IngestStats.skipped`
  и `.matches_inserted`, pg_advisory_lock в `run_ingest_once`,
  `match_single_tender` вызов в цикле.
- `portal/services/auctions/match/service.py` — `match_single_tender`.
- `portal/services/auctions/match/repository.py` — опциональный
  `tender_id` в `derive_single_position_nmck`.
- `tests/test_auctions/test_smart_ingest.py` — 13 новых тестов.
- `tests/conftest.py` — миграция 0039 добавлена в `_MIGRATIONS`.
- `scripts/reparse_cards.py` — счётчик `skipped` в выводе.
