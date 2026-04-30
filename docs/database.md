# База данных

PostgreSQL 18 на Railway (продакшн), 16+ для локальной разработки;
чистый SQL в миграциях (без ORM-моделей). Применённые миграции —
**001 → 018** (см. [`../migrations/`](../migrations/)).

## Список таблиц

### Компоненты (8 категорий)

| Таблица        | Что хранит                                                |
|----------------|-----------------------------------------------------------|
| `cpu`          | Процессоры (модель, сокет, ядра/потоки, TDP, чипсет)      |
| `motherboard`  | Материнские платы (сокет, чипсет, форм-фактор, тип ОЗУ)   |
| `ram`          | Оперативная память (объём, тип DDR4/5, частота)           |
| `gpu`          | Видеокарты (модель, VRAM, длина, TDP)                     |
| `storage`      | Накопители (тип SSD/HDD, объём, интерфейс)                |
| `case_`        | Корпуса (форм-фактор, поддержка GPU/cooler по габаритам)  |
| `psu`          | Блоки питания (мощность, сертификация, длина)             |
| `cooler`       | Кулеры (тип AIO/air, max_tdp_watts, supported sockets)    |

Все 8 таблиц имеют:

- `id` (PK), `brand`, `model`, `sku` (MPN), `gtin` (штрихкод),
  `is_hidden` (фильтр от NLU), `created_at`, `updated_at`.
- Раздел специализированных полей под категорию.
- `specs_short` (jsonb) — краткое представление для UI.
- `raw_specs` (jsonb) — сырые характеристики из источника.

### Цены и поставщики

| Таблица                  | Что хранит                                              |
|--------------------------|---------------------------------------------------------|
| `suppliers`              | Поставщики (id, name, **is_active**, contact_person)    |
| `supplier_prices`        | (supplier_id × component) → цена, stock/transit, **raw_name**, updated_at |
| `unmapped_supplier_items`| Строки прайса, не сопоставленные автоматически          |
| `price_uploads`          | Журнал загрузок прайсов (когда, что, сколько строк, **report_json**) |
| `exchange_rates`         | Курс USD→RUB с ЦБ (последняя строка — актуальный)       |

В таблице `suppliers` сейчас шесть записей (мигр. 001 + 009 + 019):
**OCS**, **Merlion**, **Treolan**, **Netlab**, **Ресурс Медиа**,
**Green Place**. Имена — точно в этом написании, регистр важен:
загрузчики `app/services/price_loaders/*.py` ищут `supplier_id`
по `name = supplier_name`. Email-адреса всех шести проставлены
(миграции 011 и 020).

### Пользователи и проекты

| Таблица                 | Что хранит                                                |
|-------------------------|-----------------------------------------------------------|
| `users`                 | Менеджеры и админ (логин, bcrypt-хэш, role, **permissions** JSONB) |
| `projects`              | Проекты пользователя (имя, дата, владелец)                |
| `queries`               | Запросы NLU (свободный текст, BuildRequest, project_id)   |
| `configurations`        | Конфигурации в проекте (компоненты, цена, query_id)       |
| `specification_items`   | Позиции спецификации (configuration_id, component, qty)   |
| `field_sources`         | Источники полей (regex/derived/openai/manual)             |
| `mapping_queue`         | Очередь ручного сопоставления админа                      |
| `api_usage_log`         | Журнал расходов на OpenAI (для бюджета)                   |
| `audit_log`             | Журнал значимых действий пользователей (Этап 9В.4)        |

## Ключевые инварианты

### USD как единственная валюта в БД

Цены в `supplier_prices` хранятся **в долларах США**. Рубли вычисляются
на лету через jinja-фильтры `to_rub` / `fmt_rub` по актуальному курсу
из `exchange_rates`.

**Почему:** курс ЦБ меняется ежедневно, фиксировать рублёвые цены
в БД означало бы либо постоянные пересчёты на каждый запрос, либо
устаревшие данные. Доллар — стабильная единица закупочного учёта
дистрибьюторов оргтехники.

См. [design-decisions.md](design-decisions.md) для подробностей.

### Фильтры активности везде в подборе

В каждом запросе подбора по умолчанию применяются:

- `suppliers.is_active = TRUE` — отключённый поставщик не участвует.
- `<component>.is_hidden = FALSE` — компоненты, скрытые от NLU
  (например, корпусные вентиляторы, не предлагаемые отдельно), не
  показываются в кандидатах.

Это дисциплина: любой новый запрос подбора должен включать оба условия.

### supplier_prices: обновление и «исчезнувшие» позиции (этап 11.4)

Структура `supplier_prices` (миграции 001 + 002 + 022):

- Идентификатор позиции — `(supplier_id, supplier_sku)`. Если поставщик
  меняет `supplier_sku` — это считается новой позицией, не та же.
- `raw_name` (TEXT, миграция 022) — название из текущей строки прайса.
  Обновляется при каждой загрузке, даже если новое короче или беднее.
  Полагаемся на `components.model` и имена от других поставщиков; regex-
  обогащение по raw_name — отдельный шаг enrichment (этап 11.6).
- `updated_at` ставится `NOW()` при каждом UPSERT и при пометке
  «исчезла».

При повторной загрузке поставщика:

- **Существующая позиция** (`supplier_id` + `supplier_sku` совпали с
  одной из строк прайса): UPDATE `price`, `currency`, `stock_qty`,
  `transit_qty`, `raw_name`, `updated_at`. Запись та же.
- **Новая позиция** (нового SKU): обычный matching MPN → GTIN; либо
  привязка к существующему компоненту, либо создание скелета и записи
  в `unmapped_supplier_items` со статусом `created_new`.
- **Исчезнувшая позиция**: SKU был активен (`stock_qty + transit_qty > 0`)
  до загрузки, но в текущем прайсе его нет — выполняем
  `UPDATE supplier_prices SET stock_qty=0, transit_qty=0, updated_at=NOW()`.
  Запись не удаляется: если завтра поставщик вернёт позицию, обычный
  UPSERT поднимет её обратно. Подбор кандидатов фильтрует по `stock_qty > 0`,
  поэтому исчезнувшие позиции автоматически выпадают из конфигуратора.

Disappeared-логика выполняется только при статусе загрузки `success`
или `partial`. При `failed` (rows_matched=0 при непустом прайсе или
исключение в loader) обнуление не применяется — иначе кривая загрузка
повредит остатки. См. `app/services/price_loaders/orchestrator.py`
(`_load_active_skus`, `_mark_disappeared`).

### Сопоставление прайса с компонентом

При загрузке прайса каждая строка матчится с компонентом БД по двум
ключам подряд:

1. `MPN` (каталожный номер производителя → колонка `sku` в БД)
2. **Если не сработало** — по `GTIN` (штрихкод)

Кейс Intel CPU через Treolan: артикул там — 5-символьный S-Spec
(`SRMBG`), а у OCS/Merlion — Order Code (`CM8071512400F`). Match
по MPN не сработает, но если у OCS-компонента заполнен `gtin` —
match найдётся по штрихкоду. Backfill GTIN для уже загруженных
компонентов: `python scripts/backfill_gtin.py --file path/to/OCS_price.xlsx`.

## users.permissions JSONB (этап 9Б.1)

Миграция 017 добавляет в таблицу `users` колонку `permissions JSONB
NOT NULL DEFAULT '{}'::jsonb`. Назначение — гибкие права на модули
портала (`app.quadro.tatar`).

Формат: словарь вида `{"configurator": true, "kp_form": false, ...}`.
Список ключей живёт в `shared/permissions.py:MODULE_KEYS` и сейчас
включает: `configurator`, `kp_form`, `auctions`, `mail_agent`,
`dashboard`. В этапе 9Б.1 в UI портала отображается только
`configurator` — остальные модули зарезервированы для 9Б.2.

Семантика (`shared/permissions.has_permission`):

- `role = 'admin'` → доступ ко всем модулям независимо от
  `permissions`. У администраторов `permissions` обычно `{}`.
- `role = 'manager'` → доступ если `permissions[<module_key>]` равно
  `true`. Отсутствие ключа = отказ.

Существующих пользователей миграция 017 переводит на
`{"configurator": true}` — пускает в текущий продуктовый модуль.
Новые менеджеры из формы `/admin/users` создаются с тем же дефолтом
(см. `shared/user_repo.py:_default_manager_permissions`).

## audit_log (этап 9В.4)

Миграция 018 заводит таблицу `audit_log` — журнал значимых действий
пользователей (login/logout, создание/удаление проектов, экспорт КП,
отправка писем поставщикам, изменения ролей и прав, ручной запуск
бекапа, скачивание дампа).

Колонки: `id BIGSERIAL`, `created_at TIMESTAMPTZ`, `user_id INTEGER` (с
ON DELETE SET NULL — действия удалённого пользователя остаются в логе),
`user_login TEXT` (денормализуем для читаемости после удаления),
`action TEXT`, `target_type TEXT`, `target_id TEXT`, `payload JSONB`,
`ip INET`, `user_agent TEXT`, `service TEXT`.

Индексы: `created_at DESC`, `user_id`, `action`, `(target_type, target_id)`.

Ретенция — 180 дней (APScheduler в портале, по воскресеньям 04:00 МСК).
Подробности — [audit_log.md](audit_log.md).

## price_uploads.report_json (этап 11.2)

Миграция 021 добавляет колонку `report_json JSONB` в `price_uploads`.
Туда orchestrator (`app/services/price_loaders/orchestrator.py:load_price`)
пишет полный отчёт после каждой загрузки прайса:

```jsonc
{
  "supplier":           "Merlion",
  "filename":           "Прайслист_Мерлион.xlsm",
  "total_rows":         12345,
  "processed":          678,        // прошло фильтр our_category
  "updated":            550,
  "added":              78,         // новых компонентов-скелетов
  "skipped":            50,
  "errors":             0,
  "unmapped_ambiguous": 12,
  "unmapped_new":       66,
  "by_source":          {"match_mpn": 540, "no_match": 78},
  "duration_seconds":   23.5,
  "status":             "success",
  "upload_id":          42
}
```

Используется UI `/admin/price-uploads` (этап 11.2): кнопка
«Подробности» в журнале запрашивает `GET /admin/price-uploads/{id}/details`
и показывает report_json целиком в модалке. Старые записи до 21
миграции имеют `report_json IS NULL` — UI показывает «—».

При критическом фейле orchestrator всё равно пишет report со
`status='failed'` и `error_message: "<ExcType>: <text>"`.

## Миграции

Применяются по порядку 001 → 021. Список:

| #   | Файл                                              | Что делает                                          |
|-----|---------------------------------------------------|-----------------------------------------------------|
| 001 | `001_init.sql`                                    | Базовая схема (8 категорий + suppliers/prices)      |
| 002 | `002_add_currency_and_relax_nullability.sql`      | Валюта, послабление nullability                     |
| 003 | `003_widen_model_column.sql`                      | Расширение колонки model                            |
| 004 | `004_add_component_field_sources.sql`             | `field_sources` для трекинга источников полей       |
| 005 | `005_add_source_url_to_component_field_sources.sql`| URL источника                                       |
| 006 | `006_add_api_usage_log.sql`                       | Журнал расходов OpenAI                              |
| 007 | `007_web_service.sql`                             | users, projects, queries, configurations            |
| 008 | `008_project_specification.sql`                   | specification_items (Этап 6.2)                      |
| 009 | `009_multi_supplier_and_gtin.sql`                 | GTIN, мульти-поставщик (Этап 7)                     |
| 010 | `010_unmapped_score.sql`                          | Скоринг для unmapped_supplier_items                 |
| 011 | `011_email_support.sql`                           | Поддержка email-отправки КП                         |
| 012 | `012_supplier_contact_person.sql`                 | contact_person в suppliers                          |
| 013 | `013_components_is_hidden.sql`                    | `is_hidden` на всех 8 категориях компонентов        |
| 014 | `014_specification_recalculated_at.sql`           | Фиксация момента пересчёта спецификации             |
| 015 | `015_exchange_rates_table.sql`                    | Таблица курсов ЦБ (Этап 9А.2.3)                     |
| 016 | `016_specification_items_parsed_query.sql`        | Контекст исходного запроса в позициях спецификации  |
| 017 | `017_add_user_permissions.sql`                    | `users.permissions` JSONB — права на модули портала (Этап 9Б.1) |
| 018 | `018_audit_log.sql`                               | Таблица `audit_log` для журнала действий (Этап 9В.4)            |
| 019 | `019_add_new_suppliers.sql`                       | Поставщики Netlab / Ресурс Медиа / Green Place (Этап 11.1)      |
| 020 | `020_supplier_emails.sql`                         | Email-контакты Netlab / Ресурс Медиа / Green Place (Этап 11.1.1)|
| 021 | `021_price_uploads_report_json.sql`               | `price_uploads.report_json JSONB` — детальный отчёт загрузки (Этап 11.2) |

### Применение миграций

**Новое окружение** — все по порядку:

```bash
for f in migrations/0*.sql; do
  psql -U postgres -d configurator_pc -f "$f"
done
```

**Существующая БД** — только новые. `conftest.py` для тестовой БД
накатывает все миграции автоматически.

### Тестовая БД

Создаётся один раз:

```bash
psql -U postgres -c "CREATE DATABASE configurator_pc_test \
  ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0"
```

`tests/conftest.py` применяет все миграции и чистит состояние перед
каждым тестом (`TRUNCATE ... CASCADE`).
