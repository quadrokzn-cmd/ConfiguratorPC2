# План: выгрузка списка аукционов в Excel с фильтрами UI

**Дата:** 2026-05-13 (исходный план), актуализирован 2026-05-17 под smart-ingest (0039) и Backlog #12.
**Название функции:** Excel-экспорт лотов аукционов с применением активных фильтров UI.
**Владелец:** Собственник-1 (продукт).
**Пользователи:** менеджер-тендерщик (главный консьюмер — сортирует по марже % в Excel), собственник (ad-hoc отчёты).

**Цель:** в дашборде `/auctions` менеджер настраивает фильтры (статус / НМЦК / поиск / urgent / print_only / стоп-регионы), нажимает «Скачать Excel» — получает файл с теми лотами, которые сейчас видит на экране. Главный сценарий — менеджер сортирует строки по «Маржа %» убывая, фильтрует жирные тендеры. Read-only экспорт, обратной загрузки нет.

---

## Архитектурные решения (фиксированы — AskUserQuestion не нужен)

### Гранулярность строк (B — решение собственника от 2026-05-16)

**1 строка = 1 `tender_item`**, дополненный primary match (если есть).

- Tender-meta (reg_number, заказчик, регион, статус, даты) дублируется в каждой строке лота — менеджер при сортировке по марже % видит, какому лоту принадлежит позиция, без перекрёстных ссылок.
- Если у tender_item нет primary match (нет cost_base / нет SKU-кандидата) — строка всё равно пишется, match-колонки пустые. Это сигнал «лот есть, но не подобрали SKU».
- Альтернативные matches (match_type='alternative') в Excel не выгружаются — Excel становится не-нагляден при 5-10 строках per позиция. Менеджер видит primary, для деталей идёт на карточку лота.

Альтернатива A (1 строка = 1 tender, primary'и склеены через ";") отклонена: невозможно сортировать по марже % — менеджер не сможет найти жирные позиции внутри тендера.

### Структура листа

```
Лист «Аукционы»
Строка 1: A1 = «Курс ЦБ (USD→RUB)», B1 = число (LATEST из exchange_rates,
           fallback 90.0 если таблица пуста; flag rate_is_fallback в audit)
Строка 2: пусто
Строка 3: Заголовки (autofilter)
Строка 4..N: Данные, по одной строке на (tender × tender_item)
```

Курс декоративный (все cost/price/margin_rub в БД уже в RUB), но воспроизводим паттерн Backlog #11 — даёт визуальную консистентность с каталог-экспортом и оставляет место для будущего расширения, если cheapest_supplier USD-цены попадут в выгрузку.

### Колонки

Фиксированный набор; категория `hidden` — скрытая колонка, `data` — обычная,
`formula` — формула:

| # | Колонка | Категория | Источник |
|---|---|---|---|
| 1 | `id` | hidden | tender_items.id (стабильный ключ строки для дебага) |
| 2 | № извещения | data | tenders.reg_number |
| 3 | Карточка zakupki | data (hyperlink) | tenders.url (через openpyxl Hyperlink) |
| 4 | Заказчик | data | tenders.customer |
| 5 | Регион | data | tenders.customer_region |
| 6 | Статус | data | tender_status.status → метка кириллицей через `STATUS_LABELS` |
| 7 | Дата публикации | data | tenders.publish_date (МСК) |
| 8 | Дедлайн подачи | data | tenders.submit_deadline (МСК) |
| 9 | Дата поставки | data | tenders.delivery_deadline (МСК) |
| 10 | НМЦК лота, ₽ | data | tenders.nmck_total |
| 11 | KTRU лота | data | tenders.ktru_codes_array (через запятую) |
| 12 | № позиции | data | tender_items.position_num |
| 13 | Название позиции | data | tender_items.name |
| 14 | KTRU позиции | data | tender_items.ktru_code |
| 15 | Количество | data | tender_items.qty |
| 16 | Цена за единицу, ₽ | data | tender_items.nmck_per_unit |
| 17 | Бренд SKU | data | printers_mfu.brand (primary match) |
| 18 | Артикул SKU | data | printers_mfu.sku (primary match) |
| 19 | Название SKU | data | printers_mfu.name (primary match) |
| 20 | Cost base, ₽ | data | printers_mfu.cost_base_rub (primary match) |
| 21 | Поставщик (cheapest) | data | имя поставщика с min ценой при stock_qty>0 (subquery — паттерн `get_tender_items_with_matches`) |
| 22 | Маржа, ₽ | formula | `=Price*Qty - Cost*Qty` (ссылки на ячейки в той же строке), при отсутствии cost — пусто |
| 23 | Маржа, % | formula | `=(Price - Cost)/Cost` с числовым форматом %; при отсутствии cost — пусто |
| 24 | Флаги | data | tenders.flags_jsonb keys через запятую (только `true`-флаги) |
| 25 | Дата ингеста | data | tenders.ingested_at (МСК) |
| 26 | Изменён | data | tenders.last_modified_at (МСК, появилось в 0039) |

Маржа RUB и % — формулы, а не статика. Это позволяет:
- Менеджеру вручную править cost (если знает свежий supplier price) и сразу видеть, как изменится маржа.
- Excel-сортировке по марже % работать на формулах (Excel вычисляет перед сортировкой).
- Воспроизводимости с паттерном #11 (формула, не статика).

### Курс ЦБ и fallback

Берётся LATEST из `exchange_rates`. Если таблица пуста — fallback `90.0` (как в #11), флаг `rate_is_fallback=True` в `ExportReport`, WARNING в лог, в audit payload `rate_fallback: true`.

### Лимит строк

Cap **10 000** tender_items за один экспорт. Если фильтр шире — урезаем (ORDER BY publish_date DESC), флаг `cap_reached=True` в report. UI-предупреждение менеджеру (alert или статус-строка в самом файле) — опционально, пока пишем флаг в audit_log.

### Фильтры

Те же query-параметры, что у `/auctions` (один `InboxFilters`-объект):

- `status[]` — массив; пусто → все статусы.
- `nmck_min`, `nmck_max` — диапазон НМЦК.
- `q` — поиск по reg_number / customer / customer_region (ILIKE).
- `urgent_only` — лоты с дедлайном в ближайшие `deadline_alert_hours`.
- `print_only` — только лоты, где **все** позиции — печатная техника.
- `include_excluded_regions` — показать стоп-регионы (по умолчанию скрыты).

Дополнительно (опционально, если будет полезно):

- `min_margin_pct` — фильтр по марже primary match — отложен; менеджер фильтрует в Excel.

### Доступ и audit

- Доступ — `require_permission('auctions')` (любой, кто видит inbox).
- Каждое скачивание → запись в `audit_log` с `action='auctions_excel_export'`,
  `target_type='auctions_excel'`, payload `{rows_count, sheet_count, filter_summary, rate_used, rate_fallback, cap_reached}`. Constant `ACTION_AUCTIONS_EXCEL_EXPORT` в `shared/audit_actions.py`.

Это переопределяет старое решение 2026-05-13 «без audit_log». Промт собственника 2026-05-17 явно требует audit (важно: модуль аукционов — операционный, нужно знать, кто и когда выгружал, чтобы при разборах инцидентов сопоставить с действиями менеджера).

---

## Фазы реализации

### Фаза 1. Discovery — структура схемы и фильтры ✅ (2026-05-17)

- [x] Прочитать `portal/routers/auctions.py` + `portal/services/auctions_service.py` — зафиксировать 7 фильтров inbox + `InboxFilters` dataclass.
- [x] Прочитать миграцию 030 (создание tenders/tender_items/tender_status/matches) и 0039 (smart-ingest, добавление content_hash и last_modified_at).
- [x] Прочитать `portal/services/catalog/excel_export.py` — паттерн для воспроизведения.
- [x] Зафиксировать колонки экспорта (см. секцию выше).

### Фаза 2. Сервис экспорта ✅ (2026-05-17)

- [x] `portal/services/auctions/excel_export.py` (~440 строк):
  - `@dataclass ExportReport` — `file_path, rows_count, rate_used, rate_date, rate_is_fallback, cap_reached, filter_summary`.
  - Переиспользует `auctions_service.InboxFilters` (один и тот же объект фильтров используется и инбоксом, и Excel-роутом).
  - `export_auctions(output_path, filters, *, deadline_alert_hours=24, db=None) -> ExportReport` — один SQL с `WITH items_breakdown AS (...)` для фильтра print_only (паттерн `auctions_service._INBOX_SQL`), JOIN-ами tenders ⋈ tender_items ⋈ tender_status ⋈ matches(primary) ⋈ printers_mfu, ORDER BY publish_date DESC, LIMIT 10001 (cap_reached детектится сравнением длины с `_ROW_CAP`).
  - openpyxl-builder: служебная строка курса, шапка autofilter, freeze_panes, скрытая колонка `tender_items.id`, hyperlink на zakupki URL, формулы маржа RUB / маржа %.
  - Декларативное описание 26 колонок через `@dataclass _Col`; индексы через `_col_index()`/`_col_letter()` — формулы маржи опираются на ячейки той же строки по имени колонки, что устойчиво к перестановкам.

### Фаза 3. UI + endpoint ✅ (2026-05-17)

- [x] `portal/routers/auctions.py` — новый route `GET /auctions/excel`:
  - Объявлен ДО `/{reg_number}` (чтобы wildcard не перехватил «excel»).
  - Парсит те же 7 query-параметров, что и inbox (status[], nmck_min/max, q, urgent_only, print_only, include_excluded_regions).
  - Зовёт `export_auctions(...)` через `tempfile.mkstemp` + `BackgroundTask` для удаления (паттерн catalog-router'а).
  - Пишет audit_log запись с payload `{rows_count, filter_summary, rate_used, rate_fallback, cap_reached}`.
  - Возвращает `FileResponse` xlsx с `Content-Disposition: attachment; filename="Аукционы_YYYY-MM-DD.xlsx"`.
- [x] `portal/templates/auctions/inbox.html`:
  - Header переделан под flex с двумя кнопками: «Скачать Excel» (всегда видна, иконка `download`, `data-testid="download-auctions-excel"`) и «Настройки» (под permission `auctions_edit_settings`).
  - Ссылка наследует текущие фильтры через `request.query_params`.
- [x] `shared/audit_actions.py` — `ACTION_AUCTIONS_EXCEL_EXPORT = "auctions_excel_export"`.

### Фаза 4. Тесты ✅ (2026-05-17)

- [x] `tests/test_auctions/test_excel_export.py` (18 юнит-тестов сервиса):
  - Пустая БД → файл с шапкой и autofilter, 0 data-строк.
  - 1 tender + 2 items + primary match для одной → 2 строки (одна с match, одна без; match-колонки пустые у второй).
  - Hyperlink на tenders.url активен на колонке «Карточка zakupki».
  - Маржа % — формула `=(Price-Cost)/Cost` с форматом 0.00%; Маржа RUB — формула `=(Price-Cost)*Qty` с форматом 0.00.
  - Маржа пустая, когда нет primary (cost) или нет price.
  - Сериализация TEXT[] (`ktru_codes_array` → через запятую без пробелов).
  - Сериализация flags_jsonb truthy-keys через запятую.
  - Фильтры status / nmck_min / search / excluded_regions (default hides + override show).
  - Fallback курса 90.0 при пустой exchange_rates → flag `rate_is_fallback=True`; не-fallback когда строка есть.
  - Cap reached через monkeypatch `_ROW_CAP=2` → `cap_reached=True`, rows_count=2.
  - `default_filename` ISO-дата + сегодня.
  - filter_summary содержит все 7 фильтров.
- [x] `tests/test_portal/test_auctions_excel.py` (10 HTTP-тестов роута):
  - Anonymous → 302 /login; manager без auctions → 403; auctions_viewer → 200 + MIME xlsx + Content-Disposition; admin → 200.
  - Filter passthrough: `?status=skipped` пробрасывается в фильтры — только skipped-лоты в файле.
  - Audit_log запись `auctions_excel_export` с payload (rows_count, filter_summary с 7 ключами, rate_used, rate_fallback, cap_reached).
  - Кнопка «Скачать Excel» на inbox-странице: с `data-testid="download-auctions-excel"`, href наследует текущие фильтры.

### Фаза 5. Deploy + smoke ⏳ (2026-05-17)

- [x] Commit + push feature-branch → ff-only merge в master.
- [ ] Railway autodeploy на portal-сервисе (стартует автоматически после push в master; собственник смотрит deploy logs).
- [ ] Smoke на app.quadro.tatar (собственник): скачать xlsx → открыть локально, проверить autofilter + формула маржи % + сортировка + hyperlink + SQL audit_log.

### Фаза 6. Документация + рефлексия ✅

- [x] Рефлексия `.business/история/2026-05-17-auctions-excel-export.md`.
- [x] `plans/2026-04-23-platforma-i-aukciony.md`: мини-этап «2026-05-17 — Excel-выгрузка аукционов (#12 закрыт)» добавлен, Backlog #12 → CLOSED.
- [x] Этот план: проставлены `[x]` по фазам 1-4 и 6, фаза 5 deploy/smoke — на стороне собственника.

---

## Что НЕ входит в этот план (вынесено)

- CSV-экспорт — отдельный мини-этап если попросят.
- Импорт обратно — read-only фича, изменения статусов / заметок делаются через UI карточки лота.
- Отдельный лист «Позиции» с FK на лот — пока всё в одной таблице, гранулярность B покрывает запрос.
- Background-job для очень больших экспортов (>10 000) — на текущем prod-объёме 800 tender_items cap не достигается, синхронный экспорт укладывается в секунды.
- Экспорт через email — пока только скачивание.
- Фильтр по «марже от/до %» в UI — менеджер фильтрует в Excel; если потребуется UI-фильтр, добавим мини-этапом.

---

## Итоговый блок

**Статус:** **реализован целиком 2026-05-17.** Фазы 1-4 и 6 закрыты в feature-ветке; Фаза 5 (Railway autodeploy + ручной smoke) — на стороне собственника после push в master.

**Цифры:**
- Сервис `portal/services/auctions/excel_export.py` — ~440 строк, 26 колонок, один SQL c CTE + 3 LEFT JOIN + коррелированный subquery для cheapest_supplier.
- Route `GET /auctions/excel` — 84 строки в `portal/routers/auctions.py`.
- UI-кнопка «Скачать Excel» в `inbox.html` + `data-testid="download-auctions-excel"`.
- **pytest полный прогон: 2096 passed, 2 skipped, 0 failed** (baseline 2068 → +28 за счёт 18 unit + 10 HTTP-тестов).

**Артефакты:**
- `portal/services/auctions/excel_export.py` — сервис (новый).
- `portal/routers/auctions.py` — расширение (+import + route).
- `portal/templates/auctions/inbox.html` — header-блок переделан под flex с двумя кнопками.
- `shared/audit_actions.py` — `ACTION_AUCTIONS_EXCEL_EXPORT`.
- `tests/test_auctions/test_excel_export.py` — 18 unit-тестов сервиса.
- `tests/test_portal/test_auctions_excel.py` — 10 HTTP-тестов роута.
- `.business/история/2026-05-17-auctions-excel-export.md` — рефлексия.
- `plans/2026-04-23-platforma-i-aukciony.md` — мини-этап «2026-05-17 — Excel-выгрузка аукционов (#12 закрыт)», Backlog #12 → CLOSED.

**Архитектурные решения (приняты исполнителем без AskUserQuestion):**
- Гранулярность B (1 строка = 1 tender_item + primary match): даёт менеджеру сортировку по марже % per позиция.
- Маржа RUB/% — формулы Excel, не статика (ручная правка cost/price пересчитывает margin).
- Курс ЦБ декоративный (margin_rub в БД в RUB), но `$B$1` присутствует для консистентности с #11 и будущего расширения; fallback 90.0 при пустой exchange_rates.
- Audit_log обязателен (переопределяет старое 2026-05-13 «без audit»).
- Cap 10 000 строк через LIMIT N+1.

**Open follow-up'ы:** нет. Фича закрыта целиком.
