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

### Фаза 2. Сервис экспорта ⏳ (2026-05-17)

- [ ] `portal/services/auctions/excel_export.py`:
  - `@dataclass ExportReport` — `file_path, rows_count, rate_used, rate_date, rate_is_fallback, cap_reached, filter_summary`.
  - `@dataclass ExcelFilters` или переиспользовать `auctions_service.InboxFilters` — одинаковые поля.
  - `export_auctions(output_path, filters, *, db=None) -> ExportReport` — один SQL с JOIN-ами tenders ⋈ tender_items ⋈ tender_status ⋈ matches(primary) ⋈ printers_mfu, ORDER BY publish_date DESC, LIMIT 10001 (детект cap_reached).
  - openpyxl-builder: служебная строка курса, шапка autofilter, скрытая колонка id, hyperlink на zakupki URL, формулы маржа RUB / маржа %.
- [ ] Тесты — `tests/test_auctions/test_excel_export.py` (см. Фазу 3).

### Фаза 3. UI + endpoint ⏳ (2026-05-17)

- [ ] `portal/routers/auctions.py` — новый route `GET /auctions/excel` (рядом с `auctions_inbox`):
  - Парсит те же query-параметры, что и inbox.
  - Зовёт `export_auctions(...)` через временный файл (паттерн `BackgroundTask` из catalog-router'а).
  - Пишет audit_log запись.
  - Возвращает `FileResponse` xlsx с `Content-Disposition: attachment; filename="Аукционы_YYYY-MM-DD.xlsx"`.
- [ ] `portal/templates/auctions/inbox.html`:
  - Кнопка «Скачать Excel» в шапке справа от «Настройки» (или в filters-форме).
  - Кнопка — обычная ссылка `<a href="/auctions/excel?{текущие_query_params}">` (GET, без CSRF — read-only).
  - Иконка `download`.
- [ ] `shared/audit_actions.py` — `ACTION_AUCTIONS_EXCEL_EXPORT = "auctions_excel_export"`.

### Фаза 4. Тесты ⏳ (2026-05-17)

- [ ] `tests/test_auctions/test_excel_export.py` (юнит-тесты сервиса):
  - Пустая БД → файл с шапкой, 0 data-строк.
  - 1 tender + 2 items + primary match для 1 → 2 строки (одна с match, одна без).
  - Hyperlink на tenders.url активен.
  - Маржа % — формула `=(Price-Cost)/Cost` с числовым форматом 0.00%.
  - Сериализация TEXT[] (`ktru_codes_array` → через запятую).
  - Фильтр `status=['new']` → только лоты со статусом new.
  - Фильтр `nmck_min=50000` → лоты с НМЦК ≥ 50k.
  - Фильтр `q='Якутия'` → лоты с регионом Якутия (через ILIKE).
  - Фильтр `include_excluded_regions=False` (по умолчанию) → лоты со `flags.excluded_by_region=true` отфильтрованы.
  - Fallback курса 90.0 при пустой exchange_rates → flag `rate_is_fallback=True`.
  - Cap 10 000 строк → флаг `cap_reached=True`.
- [ ] `tests/test_portal/test_auctions_excel.py` (HTTP-тесты роута):
  - Manager: 200, Content-Disposition с именем файла.
  - Anonymous: 302 → /login.
  - Audit_log запись `auctions_excel_export` с payload.

### Фаза 5. Deploy + smoke ⏳ (2026-05-17)

- [ ] Commit + push в feature-branch → ff-only merge в master → Railway autodeploy на portal-сервисе.
- [ ] Smoke на app.quadro.tatar: скачать xlsx → открыть локально:
  - autofilter активен на строке 3.
  - формула маржи % работает (поменять Cost → Маржа % пересчитывается).
  - сортировка по марже % убывая работает.
  - hyperlink на zakupki URL открывается.
- [ ] SQL: `SELECT * FROM audit_log WHERE action='auctions_excel_export' ORDER BY id DESC LIMIT 1`.

### Фаза 6. Документация + рефлексия ⏳

- [ ] Рефлексия `.business/история/2026-05-17-auctions-excel-export.md`.
- [ ] Обновить `plans/2026-04-23-platforma-i-aukciony.md`: мини-этап + Backlog #12 → CLOSED.
- [ ] Обновить этот план: проставить `[x]` по всем фазам, итоговый блок «реализован целиком» с цифрами.

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

**Статус:** план актуализирован 2026-05-17 под Backlog #12 + smart-ingest. Фаза 1 ✅, Фазы 2-6 ⏳.

**Что осталось:** Фазы 2-6 — сервис экспорта, UI-endpoint, тесты, deploy, рефлексия.

**Артефакты после реализации:**
- `portal/services/auctions/excel_export.py` — сервис.
- `portal/routers/auctions.py` (расширение) — route `GET /auctions/excel`.
- `portal/templates/auctions/inbox.html` (расширение) — кнопка «Скачать Excel».
- `shared/audit_actions.py` (расширение) — `ACTION_AUCTIONS_EXCEL_EXPORT`.
- `tests/test_auctions/test_excel_export.py`, `tests/test_portal/test_auctions_excel.py` — новые тесты.
- `.business/история/2026-05-17-auctions-excel-export.md` — рефлексия.
