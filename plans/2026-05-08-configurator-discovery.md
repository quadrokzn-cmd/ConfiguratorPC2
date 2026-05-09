# ConfiguratorPC2 — discovery для оркестратора QuadroTech

Дата: 2026-05-08
Источник: `d:\ProjectsClaudeCode\ConfiguratorPC2\` (HEAD: `e857cca` Этап 12.4-РМ-1).

Цель документа — собрать факты о ConfiguratorPC2, чтобы оркестратор-чат
QuadroTech принял решение по слиянию двух проектов. Никаких выводов и
рекомендаций — только цифры и код-локации.

---

## 1. Стек

### Зависимости (`requirements.txt`)

`pyproject.toml` отсутствует. Полный список:

```
fastapi>=0.110
uvicorn[standard]>=0.27
sqlalchemy>=2.0
psycopg2-binary>=2.9
pydantic>=2.6
python-dotenv>=1.0
openai>=1.12
openpyxl>=3.1
python-docx>=1.1
jinja2>=3.1
bcrypt>=4.0
itsdangerous>=2.1
python-multipart>=0.0.9
httpx>=0.27
pytest>=8.0
pytest-xdist>=3.5
apscheduler>=3.10
boto3>=1.34,<2.0
sentry-sdk[fastapi]>=2.0
zeep>=4.2          # SOAP-клиент (Resurs Media)
requests>=2.31
```

Frontend-`package.json` отдельно (Node-зависимости только для билда CSS):

```json
{
  "name": "kvadro-tech-configurator-ui",
  "version": "0.1.0",
  "scripts": {
    "build:css": "tailwindcss -i ./static/src/main.css -o ./static/dist/main.css --minify",
    "watch:css": "tailwindcss -i ./static/src/main.css -o ./static/dist/main.css --watch"
  },
  "devDependencies": {
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.49",
    "tailwindcss": "^3.4.17"
  }
}
```

### Версия Python

- Локально установлено: **Python 3.12.13** (видно по `__pycache__/*.cpython-312.pyc` и активному интерпретатору).
- `README.md`: «Python 3.10+».
- `Dockerfile`/`Procfile` существуют для Railway-деплоя (отдельные образы для конфигуратора и портала).

### Фреймворки и библиотеки

| Слой | Что используется |
|---|---|
| Web | **FastAPI** + Uvicorn (ASGI). Два независимых FastAPI-приложения: `app/main.py` (конфигуратор) и `portal/main.py` (портал). |
| Шаблоны | **Jinja2**, серверный SSR. |
| JS-интерактив | **Vanilla JS только.** Папка `static/js/` содержит `common.js`, `portal-dialog.js`, `project.js`. **HTMX и Alpine.js отсутствуют** — `grep -rn "x-data\|hx-get\|hx-post\|alpine"` по `app/templates` и `portal/templates` не находит ни одного совпадения. |
| CSS | **Tailwind CSS через Node.js-билд**, не CDN. Исходник — `static/src/main.css` (1 698 строк), компилируется в `static/dist/main.css`. Конфиг — `tailwind.config.js`, `postcss.config.js`. Палитры портала и конфигуратора — CSS-переменные `body.app-theme` / `body.portal-theme`. |
| ORM | **SQLAlchemy 2.0**, но **только `text()`** с параметрами. ORM-моделей нет; есть пустая папка `app/models/`. Запросы пишутся как обычный SQL и обёрнуты в `text(...)`. |
| Формы | python-multipart, ручной CSRF (заголовок `X-CSRF-Token`). |
| Авторизация | bcrypt + Starlette `SessionMiddleware` + itsdangerous (подписанные сессионные cookie). Один общий cookie `kt_session` шарится между конфигуратором и порталом. |
| Фоновые задачи | **APScheduler** (`BackgroundScheduler`) внутри процесса FastAPI; два независимых scheduler'а — в конфигураторе и в портале. |
| LLM | **OpenAI API** (`openai>=1.12`). Модели: `gpt-4o-mini` (NLU-парсер), `gpt-4o-mini-search-preview` (enrichment с web search). Anthropic Claude **через API не используется** (папка `enrichment/claude_code/` — это импорт/экспорт CSV для ручного обогащения через локальный Claude Code, не SDK-вызовы). |
| Бэкапы | boto3 (S3-совместимый клиент для Backblaze B2). |
| Excel/Word | openpyxl, python-docx. |
| SOAP | zeep + requests (для Resurs Media — Этап 12.4). |
| Sentry | `sentry-sdk[fastapi]`, инициализация в `app/main.py:17-19` и `portal/main.py:21-23`. |

### БД

- **PostgreSQL 16.13** (показано через `SHOW server_version` на `localhost:5432`).
- Имя БД: **`kvadro_tech`**.
- Размер на диске: **28 MB** (`pg_database_size`).
- Тестовая БД: `configurator_pc_test_<worker_id>` создаётся `tests/conftest.py` для каждого pytest-xdist worker'а.

---

## 2. Структура папок верхнего уровня

```
ConfiguratorPC2/
├── Dockerfile                       # образ конфигуратора
├── Dockerfile.portal                # отдельный образ портала
├── Procfile
├── README.md
├── pytest.ini
├── railway.json
├── railway.portal.json              # два сервиса в Railway
├── package.json                     # tailwind build pipeline
├── package-lock.json
├── postcss.config.js
├── tailwind.config.js
├── requirements.txt
│
├── app/                             # FastAPI-приложение «Конфигуратор»
│   ├── main.py                      # точка входа FastAPI (uvicorn app.main:app)
│   ├── auth.py                      # bcrypt, current_user, require_login/require_admin
│   ├── config.py                    # Settings из .env
│   ├── database.py                  # engine, SessionLocal
│   ├── scheduler.py                 # APScheduler: курс ЦБ × 5 раз в день
│   ├── models/                      # пустая (нет ORM)
│   ├── schemas/
│   ├── routers/                     # /admin, /project, /export, /mapping, /
│   ├── services/                    # бизнес-логика (см. §6)
│   ├── templates/                   # Jinja2 (конфигуратор)
│   └── templating.py
│
├── portal/                          # FastAPI-приложение «Портал»
│   ├── main.py                      # точка входа (uvicorn portal.main:app)
│   ├── scheduler.py                 # APScheduler: бекапы + автозагрузка прайсов 7:00
│   ├── routers/                     # auth, home, admin_*
│   ├── services/                    # backup_service, dashboard
│   ├── templates/                   # Jinja2 (портал)
│   └── templating.py
│
├── shared/                          # общий код двух приложений
│   ├── auth.py                      # build_session_cookie_kwargs, LoginRequiredRedirect
│   ├── audit.py, audit_actions.py
│   ├── component_filters.py
│   ├── db.py                        # engine, SessionLocal (общий)
│   ├── permissions.py               # has_permission(role, perms, key)
│   ├── sentry_init.py
│   ├── user_repo.py
│   └── templates/_partials          # fx_widget.html
│
├── migrations/                      # 29 SQL-файлов (001..029), голый PostgreSQL
├── scripts/                         # CLI: load_price, create_admin, бэкфиллы, диагностики
├── tests/                           # 98 файлов, 1307 тестов (pytest-xdist)
├── static/                          # JS, CSS-исходник, dist, шрифты, картинки
├── docs/                            # деплой-доки и т.п.
├── design_references/               # макеты
├── visual_samples/                  # скриншоты
├── business/                        # бизнес-контекст (analog .business в QT)
├── data/                            # CSV/JSON фикстуры
├── db_dumps/                        # бэкапы локальной БД
├── enrichment/                      # workflow-папки для ручного enrichment (pending/done/archive)
├── reference_prices/                # эталонные прайсы для тестов
├── logs/
└── node_modules/                    # tailwind dev-deps
```

---

## 3. Точки входа и расписание

### Запуск

Два процесса:

```
uvicorn app.main:app    --reload   # конфигуратор, локально :8080
uvicorn portal.main:app --reload   # портал,        локально :8081
```

На Railway — два отдельных сервиса (`Dockerfile` + `Dockerfile.portal`,
`railway.json` + `railway.portal.json`).

CLI: `python scripts/load_price.py`, `python scripts/create_admin.py`,
`python scripts/bootstrap_admin.py` (для Railway), `python scripts/apply_migrations.py`.

### APScheduler-задачи

#### `app/scheduler.py` (конфигуратор)

Активация: `RUN_SCHEDULER=1` в `.env`.

| Когда (МСК) | Задача | Что делает |
|---|---|---|
| 08:30, 13:00, 16:00, 17:00, 18:15 | `cbr_fetch_*` | Качает курс USD/RUB с ЦБ и пишет в `exchange_rates`. См. `app/services/export/exchange_rate.py`. |

#### `portal/scheduler.py` (портал) — вот где автозагрузка прайсов

Активация: `APP_ENV=production` ИЛИ `RUN_BACKUP_SCHEDULER=1`.

| Когда (МСК) | Задача | Slug / комментарий |
|---|---|---|
| 03:00 ежедневно | `daily_backup` | `pg_dump` → Backblaze B2 (`portal/services/backup_service.py`) |
| Вс 04:00 | `audit_retention` | `DELETE FROM audit_log WHERE created_at < NOW() - 180 days` |
| **07:00** | `auto_price_loads_treolan` | REST API |
| **07:10** | `auto_price_loads_ocs` | IMAP |
| **07:20** | `auto_price_loads_merlion` | IMAP |
| **07:30** | `auto_price_loads_netlab` | HTTP |
| **07:40** | `auto_price_loads_resurs_media` | SOAP (Этап 12.4) |
| **07:50** | `auto_price_loads_green_place` | пока без fetcher'а (no-op при OFF) |

Каждый cron-job читает `auto_price_loads.enabled` для своего slug и
вызывает `app/services/auto_price/runner.py::run_auto_load(slug,
'scheduled')` только при `enabled=TRUE` (UI-тумблер на
`/admin/auto-price-loads` мгновенно отключает поставщика). Конфигурация
расписания — `portal/scheduler.py:55-62` (`_AUTO_PRICE_SCHEDULE`).

### Worker-процессы

Отдельных worker'ов (Celery/ARQ/Redis) **нет**. Всё — внутри двух FastAPI
процессов через APScheduler `BackgroundScheduler` (пул потоков). Заметка
в `portal/scheduler.py` объясняет, что при появлении реплик
`run_scheduler` оставят включённым только на одном инстансе.

---

## 4. БД-схема

### Миграции

29 файлов на диске (`migrations/001_init.sql` ... `migrations/029_auto_price_load_runs_source_ref.sql`):

```
001_init.sql                             016_specification_items_parsed_query.sql
002_add_currency_and_relax_nullability   017_add_user_permissions.sql
003_widen_model_column.sql               018_audit_log.sql
004_add_component_field_sources.sql      019_add_new_suppliers.sql
005_add_source_url_to_component_field..  020_supplier_emails.sql
006_add_api_usage_log.sql                021_price_uploads_report_json.sql
007_web_service.sql                      022_supplier_prices_raw_name.sql
008_project_specification.sql            023_component_field_sources_source_detail
009_multi_supplier_and_gtin.sql          024_psu_misclassification.sql
010_unmapped_score.sql                   025_storage_misclassification.sql
011_email_support.sql                    026_storage_misclassification_kingston_..
012_supplier_contact_person.sql          027_fix_storage_data_bugs.sql
013_components_is_hidden.sql             028_auto_price_loads.sql
014_specification_recalculated_at.sql    029_auto_price_load_runs_source_ref.sql
015_exchange_rates_table.sql
```

Журнал применения: таблица `schema_migrations(filename, applied_at)`. На
локальной БД применены 001..027 (27 строк) — миграции 028, 029
(auto_price_loads) ещё не накатывались локально.

`migrations/` суммарно — **1 235 строк SQL**. Применяет их
`scripts/apply_migrations.py`. Это «голый SQL», без Alembic, без Django,
без `python-migrate` — точно как в QuadroTech.

### Таблицы и количество строк (локальная БД `kvadro_tech`, 23 таблицы)

| Таблица | Строк | Назначение |
|---|---|---|
| `users` | **2** | login, password_hash, role (`admin`/`manager`), name, is_active, permissions JSONB |
| `suppliers` | **6** | OCS, Merlion, Treolan, Netlab, Ресурс Медиа, Green Place |
| `supplier_prices` | **13 010** | (supplier_id, category, component_id) → price, stock, transit |
| `price_uploads` | 9 | история загрузок прайсов (filename, rows_total/matched/unmatched, status) |
| `unmapped_supplier_items` | 6 381 | очередь ручного маппинга |
| `cpus` | 228 | |
| `motherboards` | 957 | |
| `rams` | 1 030 | |
| `gpus` | 790 | |
| `storages` | 1 175 | |
| `cases` | 1 876 | |
| `psus` | 1 494 | |
| `coolers` | 1 934 | (всего 8 категорий = ~9 484 компонента) |
| `component_field_sources` | **23 123** | трейс источников полей (этап 2.5) |
| `projects` | 18 | проекты пользователей |
| `queries` | 21 | history of NLU-запросов с parsed_json/build_result_json |
| `specification_items` | 13 | позиции в спецификациях |
| `exchange_rates` | 1 | USD/RUB от ЦБ |
| `api_usage_log` | 47 | OpenAI usage |
| `daily_budget_log` | 2 | агрегаты дневного бюджета |
| `audit_log` | 10 | действия пользователей |
| `sent_emails` | 1 | архив исходящей почты |
| `schema_migrations` | 27 | бухгалтерия миграций |

Размер БД: **28 MB**.

Таблицы `auto_price_loads`, `auto_price_load_runs` (миграции 028/029)
существуют в коде, но локально ещё не накатаны.

### Пересечения с QuadroTech-схемой

QuadroTech-таблицы (по списку из брифа): `users`, `suppliers`,
`nomenclature`, `supplier_prices`, `price_uploads`, `ktru_catalog`,
`ktru_watchlist`, `tenders`, `tender_items`, `matches`, `tender_status`,
`settings`, `excluded_regions`.

| QuadroTech | ConfiguratorPC2 | Тип пересечения |
|---|---|---|
| `users` | `users` | **прямое имя**. Колонки: ConfiguratorPC2 — `id, login, password_hash, role, name, is_active, permissions(JSONB), created_at`. role: `'admin'`/`'manager'`. |
| `suppliers` | `suppliers` | **прямое имя**. Колонки: `id, name, contact_email, contact_phone, is_active, created_at` (миграция 020 добавляет email-поля). |
| `supplier_prices` | `supplier_prices` | **прямое имя**. Колонки: `id, supplier_id, category, component_id, supplier_sku, price, stock_qty, transit_qty, updated_at, currency, raw_name`. UNIQUE: `(supplier_id, category, component_id)`. |
| `price_uploads` | `price_uploads` | **прямое имя**. Колонки: `id, supplier_id, filename, uploaded_at, rows_total, rows_matched, rows_unmatched, status, notes, report_json`. |
| `nomenclature` | **отсутствует** | ConfiguratorPC2 хранит компоненты в **8 отдельных таблицах** (`cpus`, `motherboards`, `rams`, `gpus`, `storages`, `cases`, `psus`, `coolers`) с разной схемой полей. Связь с `supplier_prices` — через пару `(category, component_id)`. |
| `ktru_*`, `tenders*`, `matches`, `tender_status`, `excluded_regions`, `settings` | отсутствуют | в ConfiguratorPC2 этих таблиц нет |

**Критичные пересечения по именам**: `users`, `suppliers`,
`supplier_prices`, `price_uploads`. Колоночная схема и набор полей в
этих 4 таблицах в двух проектах могут не совпадать — детальное
сравнение схем здесь не делается.

---

## 5. UI

### Где шаблоны

- `app/templates/` — конфигуратор (10 файлов в корне + `admin/`, `_macros/`, `export/`).
- `portal/templates/` — портал (`base.html`, `home.html`, `login.html`, `admin/{audit, auto_price_loads, backups, price_uploads, users}.html`, `_macros/icons.html`).
- `shared/templates/_partials/fx_widget.html` — общий виджет курса ЦБ для сайдбаров.

Всего HTML — **6 469 строк**.

### Layout портала (главный «портал сотрудника»)

Файл `portal/templates/base.html` (133 строки) задаёт **flex-shell**:

```html
<div class="kt-app-shell">
  <aside class="kt-sidebar bg-surface-1 border-r border-line-subtle">
    <!-- логотип, надпись «П О Р Т А Л» -->
    <nav class="kt-sidebar-nav px-3" aria-label="Главная навигация портала">
      {{ _portal_nav_link('/',                         'Главная',         'layout-grid', ...) }}
      {% if user.is_admin %}
      {{ _portal_nav_link('/admin/users',              'Пользователи',    'users', ...) }}
      {{ _portal_nav_link('/admin/price-uploads',      'Прайс-листы',     'truck', ...) }}
      {{ _portal_nav_link('/admin/auto-price-loads',   'Автозагрузка',    'refresh-cw', ...) }}
      {{ _portal_nav_link('/admin/backups',            'Бекапы',          'database', ...) }}
      {{ _portal_nav_link('/admin/audit',              'Журнал действий', 'list', ...) }}
      {% endif %}
    </nav>
    <div class="kt-sidebar-footer">
      <!-- ссылка на конфигуратор + fx_widget + карточка пользователя + logout -->
    </div>
  </aside>
  <main class="kt-main">{% block content %}{% endblock %}</main>
</div>
```

Сайдбар фиксированной ширины 248px (на ≤1024px — 72px), скролл — только
у `.kt-main` (`overflow-y:auto`). Конфигуратор использует **тот же
shell** (`app/templates/base.html`) с дополнительным `.kt-topbar`
(хлебные крошки) и собственной навигацией.

### Дашборд портала (главная)

Файл `portal/templates/home.html` (197 строк) — 5 виджетов в
grid'е `.portal-grid`:

```html
<section class="portal-grid mb-10">
  <article class="portal-widget portal-col-4" data-testid="widget-active-projects">
    <div class="portal-widget-head">
      <span class="portal-widget-title">{{ dashboard.active_projects.label }}</span>
      <span class="portal-widget-icon">{{ icon('folder', 'w-5 h-5') }}</span>
    </div>
    <div class="portal-widget-bignum">{{ dashboard.active_projects.total }}</div>
  </article>
  <!-- managers, exchange_rate, suppliers_freshness, components_breakdown -->
</section>

<section>
  {% if show_configurator %}
  <a href="{{ configurator_url }}/" class="portal-module-tile" data-testid="tile-configurator">
    <span class="portal-module-icon">{{ icon('cpu', 'w-7 h-7') }}</span>
    <div class="portal-module-text">
      <div class="portal-module-title">Конфигуратор ПК</div>
      <div class="portal-module-sub">Подбор комплектующих по запросу клиента и формирование КП.</div>
    </div>
  </a>
  {% endif %}
</section>
```

Источник данных для виджетов — `portal/services/dashboard.py`
(`get_dashboard_data(db)` возвращает dict с ключами `active_projects`,
`managers`, `exchange_rate`, `suppliers_freshness`,
`components_breakdown`).

### Иконки

`portal/templates/_macros/icons.html` — макрос `icon(name, class)`,
рендерит inline-SVG из набора Lucide-подобных иконок.
Используется и в портале, и в конфигураторе через
`{% from "_macros/icons.html" import icon %}`. Для добавления иконки
надо дописать SVG в этот макрос.

### Как добавить новую «иконку» (модуль) на дашборде

Сейчас на дашборде портала **только одна плитка модуля** — «Конфигуратор
ПК». Чтобы добавить новый модуль:

1. Добавить пункт в навигацию сайдбара — `_portal_nav_link(...)` в
   `portal/templates/base.html`.
2. (Опционально) Добавить виджет на дашборде —
   `<article class="portal-widget">...` в `portal/templates/home.html`.
3. (Опционально) Добавить плитку модуля в секции
   `portal-module-tile`.
4. Прокинуть данные в шаблон — расширить
   `portal/services/dashboard.py::get_dashboard_data()`.
5. Если модуль — отдельный сервис: настроить permissions через
   `shared/permissions.py::has_permission()` (ключи в
   `users.permissions JSONB`).

### Список модулей в портале (текущий)

В сайдбаре портала (admin):

- Главная (`/`)
- Пользователи (`/admin/users`)
- Прайс-листы (`/admin/price-uploads`)
- Автозагрузка (`/admin/auto-price-loads`)
- Бекапы (`/admin/backups`)
- Журнал действий (`/admin/audit`)
- (отдельная ссылка) Конфигуратор → `${CONFIGURATOR_URL}/`

В конфигураторе (admin):

- Новый запрос (`/`)
- Проекты (`/projects`, `/project/{id}`)
- История запросов (`/history`)
- Дашборд (`/admin`)
- Поставщики (`/admin/suppliers`)
- Компоненты (`/admin/components`)
- Очередь маппинга (`/admin/mapping`)
- Бюджет OpenAI (`/admin/budget`)
- (внешняя ссылка) Пользователи → `${PORTAL_URL}/admin/users`

---

## 6. Сервисы и логика

### `app/services/`

```
app/services/
├── auto_price/
│   ├── runner.py          # run_auto_load(slug, triggered_by) — единая точка запуска
│   ├── base.py            # реестр fetcher'ов
│   └── fetchers/
│       ├── base_imap.py
│       ├── ocs_imap.py
│       ├── merlion_imap.py
│       ├── netlab_http.py
│       ├── treolan.py
│       └── resurs_media.py
├── budget_guard.py        # дневной лимит OpenAI
├── compatibility/rules.py # правила совместимости компонентов
├── component_service.py
├── configurator/
│   ├── builder.py         # подбор сборки
│   ├── candidates.py
│   ├── pretty.py
│   ├── prices.py
│   ├── schema.py
│   ├── selector.py
│   └── warnings.py
├── enrichment/
│   ├── runner.py
│   ├── persistence.py
│   ├── report.py
│   ├── raw_name_runner.py
│   ├── derived_rules.py
│   ├── claude_code/       # CSV import/export для ручного enrichment через Claude Code
│   │   ├── derive.py
│   │   ├── exporter.py
│   │   ├── importer.py
│   │   ├── schema.py
│   │   └── validators.py
│   ├── openai_search/     # web search через gpt-4o-mini-search-preview
│   │   ├── client.py
│   │   ├── cost_guard.py
│   │   ├── fx.py
│   │   ├── hooks.py
│   │   ├── runner.py
│   │   ├── schema.py
│   │   └── skip_rules.py
│   └── regex_sources/     # regex-обогащение из raw_name
├── export/
│   ├── excel_builder.py   # КП в Excel (openpyxl)
│   ├── kp_builder.py      # КП в Word (python-docx)
│   ├── exchange_rate.py   # ЦБ-фетчер
│   ├── email_composer.py
│   └── email_sender.py
├── manual_edit/           # ручное редактирование компонентов через CSV
├── mapping_service.py     # ручной маппинг unmapped_supplier_items
├── nlu/
│   ├── parser.py          # OpenAI gpt-4o-mini → BuildRequest
│   ├── pipeline.py
│   ├── formatter.py
│   ├── fuzzy_lookup.py
│   ├── profiles.py
│   ├── prompts/
│   │   ├── parser_system.txt
│   │   └── commentator_system.txt
│   ├── request_builder.py
│   ├── schema.py
│   └── commentator.py
├── openai_service.py      # тонкая обёртка над openai SDK (заглушка-комментарий)
├── price_loader.py        # тонкая обёртка load_ocs_price для совместимости
├── price_loaders/
│   ├── orchestrator.py    # единый раннер: parse → match → write
│   ├── base.py
│   ├── candidates.py
│   ├── matching.py
│   ├── models.py
│   ├── _qual_stock.py
│   ├── ocs.py
│   ├── merlion.py
│   ├── treolan.py
│   ├── netlab.py          # новый
│   ├── resurs_media.py    # новый (12.4)
│   └── green_place.py     # новый
├── spec_naming.py
├── spec_recalc.py
├── spec_service.py        # CRUD спецификаций
├── supplier_service.py
├── web_result_view.py     # обогащение specs_short/raw_specs для UI
└── web_service.py         # бизнес-логика веб-роутов
```

### `app/services/price_loaders/`

Это тот пакет, который, по словам брифа, копировался в QuadroTech.
Сейчас он содержит **6 поставщиков** + общий каркас:

- `orchestrator.py` — общий раннер: парсит файл, делает match по
  `MPN`/`GTIN`, пишет в `supplier_prices`, кладёт несматченное в
  `unmapped_supplier_items`.
- `matching.py`, `candidates.py`, `models.py`, `base.py`,
  `_qual_stock.py` — общие утилиты.
- `ocs.py`, `merlion.py`, `treolan.py` — исходные три (этап 7).
- `netlab.py`, `resurs_media.py`, `green_place.py` — новые (этап 12.x).

Расхождение версий с копией в QuadroTech на уровне файлов здесь не
проверялось — у меня нет доступа к QuadroTech-копии. Но факт:
ConfiguratorPC2 `price_loaders` сейчас активно правится (последние 4
коммита — про Treolan ID-маппинг и Resurs Media).

### Модули обогащения — `app/services/enrichment/`

Три параллельных подхода:

1. **`regex_sources/`** — извлечение характеристик из `raw_name`
   регулярками. Дёшево, no LLM.
2. **`openai_search/`** — `gpt-4o-mini-search-preview` с web search,
   cost guard. Используется когда regex не справляется.
3. **`claude_code/`** — workflow через CSV. Экспортирует пачку строк
   в `enrichment/pending/`, человек прогоняет их через Claude Code
   локально, итог импортируется обратно. **Это не SDK-вызовы Claude
   API**, а оффлайн-workflow с CSV.

Общий runner — `app/services/enrichment/runner.py`. Источники полей
сохраняются в `component_field_sources` (23 123 строки в БД) с
указанием, какой подход поле заполнил.

### Модули LLM / агенты

- **OpenAI**: `app/services/nlu/parser.py` (gpt-4o-mini),
  `app/services/nlu/commentator.py`,
  `app/services/enrichment/openai_search/client.py`
  (gpt-4o-mini-search-preview). Контроль расходов —
  `app/services/budget_guard.py` (дневной лимит из
  `DAILY_OPENAI_BUDGET_RUB`, по умолчанию 100 ₽).
- **Anthropic Claude SDK**: **не используется**. `enrichment/claude_code`
  — это CSV-workflow, а не вызовы Anthropic API.
- **Агентные фреймворки** (LangChain, LlamaIndex и т.п.) — не
  используются.

---

## 7. Тесты

- Файлов тестов: **98** (`tests/test_*.py` + поддиректории).
- Собрано pytest'ом: **1 307 тестов** (`pytest --collect-only -q` → «1307 tests collected in 1.39s»).
- Структура `tests/`:
  - `test_auto_price/`
  - `test_configurator/`
  - `test_enrichment/`
  - `test_export/`
  - `test_nlu/`
  - `test_portal/`
  - `test_price_loaders/`
  - `test_shared/`
  - `test_web/`
  - `scripts/`
  - + одиночные файлы в корне `tests/` (test_backfill_video_outputs.py,
    test_enrichment_*.py, test_manual_edit.py, test_mapping_*.py,
    test_openai_enrich.py, test_spec_naming.py).
- Конфиг: `pytest.ini` с `addopts = -n auto --dist=loadfile --durations=10`.
  Маркер `live` для тестов с реальными OpenAI-вызовами (skip без
  `RUN_LIVE_TESTS=1`).
- Каждому pytest-xdist worker'у создаётся своя БД
  `configurator_pc_test_<worker_id>`, миграции применяются автоматически
  (`tests/conftest.py`).

**Полный прогон не запускался** (требует `TEST_DATABASE_URL`, в этом
окружении переменная не задана). Сбор тестов прошёл успешно — синтаксических
ошибок в тестовом коде нет.

---

## 8. Что не совпадает с QuadroTech-стеком

QuadroTech (со слов `CLAUDE.md` и `README.md` в `d:\ProjectsClaudeCode\KVADRO_TEX\`):

> Backend: Python 3.10+, FastAPI + Uvicorn, SQLAlchemy 2.0 (text, без ORM), psycopg2-binary, Pydantic 2.6+.
> Frontend (без Node.js): Jinja2 SSR, Tailwind CSS (CDN), HTMX + Alpine.js (CDN), Chart.js / ECharts, Vanilla JS.
> БД: PostgreSQL, голый SQL в migrations/.
> Авторизация: bcrypt, itsdangerous + SessionMiddleware, python-multipart; RBAC через roles, user_roles, module_permissions.
> LLM: OpenAI API (gpt-4o-mini), Anthropic Claude API, budget_guard.py.
> Фоновые задачи: APScheduler (старт) → ARQ + Redis (позже).
> Скрапинг: httpx (async), lxml / BeautifulSoup, Playwright.

### Совпадает

- Python 3.10+ (у ConfiguratorPC2 локально 3.12, требование совместимо).
- FastAPI + Uvicorn.
- SQLAlchemy 2.0 «text-only», без ORM.
- psycopg2-binary, Pydantic 2.6+.
- PostgreSQL + голый SQL в `migrations/` (даже стиль файлов: `001_init.sql`).
- Jinja2 SSR.
- bcrypt + itsdangerous + SessionMiddleware + python-multipart.
- APScheduler в качестве стартового решения для фоновых задач.

### Расходится

| Что | QuadroTech | ConfiguratorPC2 |
|---|---|---|
| **Tailwind** | через CDN, без Node.js | через **Node.js билд** (`tailwindcss` CLI, `package.json`, `tailwind.config.js`, `postcss.config.js`). Это сознательный выбор: компилированный CSS + минификация + отсутствие зависимости от внешнего CDN в проде. |
| **HTMX** | используется | **не используется** (ни одного `hx-*` атрибута во всех шаблонах) |
| **Alpine.js** | используется | **не используется** (ни одного `x-data` в шаблонах) |
| **Интерактив** | HTMX + Alpine | Vanilla JS (`static/js/{common,portal-dialog,project}.js`, ~1 029 строк) + AJAX через `fetch` с `X-CSRF-Token` заголовком |
| **RBAC** | `roles`, `user_roles`, `module_permissions` (отдельные таблицы) | колонка `users.permissions JSONB` + `users.role` (`admin`/`manager`); проверки через `shared/permissions.py::has_permission(role, perms, key)` |
| **Каталог номенклатуры** | одна таблица `nomenclature` | **8 отдельных таблиц** (`cpus, motherboards, rams, gpus, storages, cases, psus, coolers`) с РАЗНЫМИ наборами колонок под каждый тип |
| **LLM** | OpenAI + Anthropic Claude API | OpenAI only; Anthropic SDK **не подключён** (`claude_code/` — CSV-workflow) |
| **Скрапинг** | httpx async, lxml, BS, Playwright | httpx (sync), zeep (SOAP), imaplib для IMAP. Playwright/BS — нет |
| **Деплой** | docker-compose, `make up`, `make migrate` | Railway (отдельные `Dockerfile` + `Dockerfile.portal`, `railway.json`/`railway.portal.json`); локально — два `uvicorn`-процесса |
| **Архитектура процессов** | один сервис | **два независимых FastAPI-сервиса**: `app/main.py` (конфигуратор) и `portal/main.py` (портал), с общим shared/ кодом и общими подписанными cookie |

---

## 9. Размеры кодовой базы

`wc -l` по исходникам (исключены `node_modules`, `.venv`, `__pycache__`,
скомпилированный `static/dist/main.css`):

| Тип | Строк |
|---|---|
| **Python** | **36 665** |
| **HTML (Jinja2)** | **6 469** |
| **CSS** (исходник `static/src/main.css`) | **1 698** |
| **JS** (исходник `static/js/`) | **1 029** |
| **SQL** (`migrations/`) | **1 235** |
| **Итого** (Py+HTML+CSS+JS+SQL) | **~47 100** |

Размер БД на диске: **28 MB** (`pg_database_size`, локальная БД
`kvadro_tech` на PG 16.13).

---

## 10. Резюме фактов для оркестратора

1. **Стек ядра совпадает** с QuadroTech: Python 3.10+, FastAPI,
   SQLAlchemy 2.0 (text-only), psycopg2, Jinja2 SSR, bcrypt +
   SessionMiddleware, APScheduler, голый SQL в `migrations/`,
   PostgreSQL 16. Версия Python локально — 3.12.
2. **Стек UI расходится**: ConfiguratorPC2 использует **Node.js +
   tailwindcss CLI** (билд CSS) и **vanilla JS без HTMX и без Alpine**.
   QuadroTech — Tailwind CDN + HTMX + Alpine. Все интерактивы
   ConfiguratorPC2 (галочки спецификации, AJAX, диалоги) сделаны
   ванильным `fetch`/`addEventListener`.
3. **Архитектурно ConfiguratorPC2 — два FastAPI-приложения**: `app/`
   (конфигуратор, /:8080) и `portal/` (портал, /:8081), общий код в
   `shared/`, общие сессионные cookie на домене `.quadro.tatar`. Это
   важно при слиянии: либо сохранять двойной деплой, либо мёрджить
   роутеры в один процесс.
4. **Прямые пересечения таблиц с QuadroTech**: `users`, `suppliers`,
   `supplier_prices`, `price_uploads` (все 4 — ровно те имена, что в
   QuadroTech). Колоночные схемы могут отличаться — детальное diff'ание
   в этом отчёте не делалось. ConfiguratorPC2 локально содержит
   13 010 строк `supplier_prices` и 6 поставщиков (OCS, Merlion,
   Treolan, Netlab, Ресурс Медиа, Green Place).
5. **`nomenclature` в QuadroTech vs 8 таблиц в ConfiguratorPC2** —
   принципиальное архитектурное расхождение. ConfiguratorPC2 хранит
   `cpus, motherboards, rams, gpus, storages, cases, psus, coolers` с
   разными колонками под каждую категорию (всего ~9 484 компонентов в
   локальной БД). Связь с `supplier_prices` — пара `(category,
   component_id)`.
6. **Автозагрузка прайсов 7:00 МСК** — работает через
   `portal/scheduler.py:55-62`: 6 cron-job'ов с интервалом 10 минут
   (07:00 treolan REST → 07:10 ocs IMAP → 07:20 merlion IMAP → 07:30
   netlab HTTP → 07:40 resurs_media SOAP → 07:50 green_place). Тумблер
   `auto_price_loads.enabled` в БД управляет каждым поставщиком
   независимо.
7. **LLM**: только OpenAI API (`gpt-4o-mini` для NLU,
   `gpt-4o-mini-search-preview` для enrichment), есть
   `app/services/budget_guard.py` с дневным лимитом в рублях. Anthropic
   Claude SDK не используется — папка `claude_code/` в enrichment'е это
   CSV-workflow, не SDK.
8. **Кодовая база крупная**: 36 665 строк Python, 6 469 строк HTML,
   1 235 строк SQL в 29 миграциях, 1 307 pytest-тестов в 98 файлах.
   Размер БД на диске — 28 MB (28-30 MB и в проде, судя по дампам в
   `db_dumps/`).
9. **Тестируемость**: pytest-xdist с автоматическим созданием БД
   per-worker. Тесты разнесены по 9 директориям, в т.ч.
   `test_portal/`, `test_price_loaders/`, `test_auto_price/`. Прогон
   тестов в этом окружении не запускался (нет `TEST_DATABASE_URL`), но
   collect прошёл чисто.
10. **Активная разработка**: последние 5 коммитов на master датируются
    последними этапами (Этап 12.4-РМ-1 ResursMediaApiFetcher, 12.5d/c/b/a
    про Treolan ID-mapping и admin UI). Это не «замороженный код» — на
    него регулярно накатываются новые модули.
