# QuadroTech-Suite — навигация по проекту

Этот документ — карта объединённого проекта QuadroTech-Suite. Читай его в начале каждой сессии, чтобы понимать, где что лежит и как с этим работать.

## Что это за проект

**QuadroTech-Suite** — внутренний продукт казанской компании QuadroTech (оптовые и тендерные поставки компьютерной и печатной техники, ядро — Pantum и видеосерверы Skyros). Цель — увеличить чистую прибыль собственников за счёт автоматизации рутинных процессов, в первую очередь:

1. **Поиск и обработка аукционов** по всей РФ (B2G).
2. **Конфигурирование ПК** под входящие запросы и формирование коммерческих предложений (B2B + менеджеры).
3. **Сервис-портал сотрудника** — единая точка входа для менеджеров (прайсы, бекапы, журнал действий, дашборд, ссылки на модули).

Бизнес-контекст — `.business/INDEX.md`.

### История репозитория

До 2026-05-08 проект существовал в двух репо:

- **QuadroTech** (`d:\ProjectsClaudeCode\KVADRO_TEX\`) — аукционы (парсер, KTRU-каталог, матчинг). **Заморожен 2026-05-08** (см. `FROZEN.md` в его корне). БД и auto-memory снимок — в `.business/_backups_2026-05-08-merge/`.
- **ConfiguratorPC2** (`d:\ProjectsClaudeCode\ConfiguratorPC2\`) — конфигуратор ПК + портал сотрудника + автозагрузка прайсов. **Главный репо после слияния** (этот, в котором ты сейчас находишься).

Слияние идёт в 9 этапов (план — `plans/2026-04-23-platforma-i-aukciony.md`). На 2026-05-08 завершены этапы 1 (бэкапы и freeze QT) и 2 (перенос методологии).

## Ключевые принципы проекта

1. **Чистая прибыль выше всего.** Главный критерий любого решения — влияние на чистую прибыль собственников при контролируемом риске. Рост выручки, охваты, имидж — вторично.
2. **Решения принимаются на цифрах.** Опора на факты и юнит-экономику, без «ощущений». Если цифр нет — сначала считаем, потом действуем.
3. **Репутация надёжного исполнителя не ломается.** >1000 исполненных госконтрактов — это капитал, которым не рискуем ради разовой выгоды. Никаких срывов, просрочек, штрафов.

Подробнее — `.business/company/values.md`.

## Стек (объединённый, реальный)

### Backend

- **Python** 3.10+ (локально 3.12.13).
- **FastAPI** + Uvicorn. Два независимых FastAPI-приложения: `app/main.py` (конфигуратор, :8080) и `portal/main.py` (портал, :8081). Общий код — в `shared/`.
- **SQLAlchemy 2.0** только через `text()` и параметры `:name`, без ORM-моделей (папка `app/models/` пустая).
- **psycopg2-binary**, **Pydantic** 2.6+, **python-multipart**.
- **APScheduler** (`BackgroundScheduler`) — фоновые задачи внутри процессов FastAPI (см. «Расписание» ниже).
- **OpenAI** (`gpt-4o-mini` для NLU, `gpt-4o-mini-search-preview` для enrichment); контроль расходов — `app/services/budget_guard.py`. Anthropic Claude SDK не используется (папка `enrichment/claude_code/` — это CSV-workflow для ручного обогащения через локальный Claude Code, не SDK-вызовы).
- **boto3** — Backblaze B2 для бэкапов.
- **zeep** + requests — SOAP (Resurs Media).
- **httpx**, imaplib — IMAP-загрузка прайсов.
- **openpyxl**, python-docx — Excel/Word экспорт КП.
- **bcrypt** + Starlette `SessionMiddleware` + itsdangerous — авторизация. Общий cookie `kt_session` шарится между конфигуратором и порталом.
- **Sentry** — `sentry-sdk[fastapi]`, инициализация в `app/main.py` и `portal/main.py`.

### БД

- **PostgreSQL** 16 (локально 16.13). Имя БД — `kvadro_tech`.
- Миграции — голый SQL в `migrations/NNN_*.sql`, журнал применения — таблица `schema_migrations`. Применяет `scripts/apply_migrations.py`.
- Тестовая БД создаётся per-pytest-xdist-worker (`configurator_pc_test_<worker_id>`).

### Frontend

- **Jinja2 SSR** (папки `app/templates/`, `portal/templates/`, `shared/templates/_partials/`).
- **Tailwind CSS через Node.js-билд** (`tailwindcss` CLI, `package.json`, `tailwind.config.js`, `postcss.config.js`). Исходник — `static/src/main.css`, компилируется в `static/dist/main.css`. Не CDN.
- **Vanilla JS** (`static/js/{common,portal-dialog,project}.js`). **HTMX и Alpine.js не используются**. AJAX — `fetch` с заголовком `X-CSRF-Token`.
- Иконки — inline-SVG через макрос `icon()` в `portal/templates/_macros/icons.html` (Lucide-подобный набор).

### Авторизация и RBAC

- `users.permissions JSONB` + `users.role` (`'admin'` / `'manager'`). Проверки — `shared/permissions.py::has_permission(role, perms, key)`.
- CSRF: hidden input `csrf_token` в формах + заголовок `X-CSRF-Token` для AJAX.

### Инфраструктура

- **Docker + Railway**: два отдельных сервиса (`Dockerfile` + `Dockerfile.portal`, `railway.json` + `railway.portal.json`).
- **Бэкапы**: ежедневный `pg_dump` в Backblaze B2, расписание — `portal/scheduler.py`.

### Тесты

- **pytest 8+** + httpx TestClient. Конфиг — `pytest.ini` (`-n auto --dist=loadfile`).
- Маркер `live` для тестов с реальными OpenAI-вызовами (skip без `RUN_LIVE_TESTS=1`).

## Структура репозитория

```
ConfiguratorPC2/
├── CLAUDE.md                  ← этот файл
├── MEMORY.md                  ← индекс auto-memory (зеркало)
├── README.md
├── FROZEN.md                  ← (нет; маркер есть в KVADRO_TEX/)
├── .business/                 ← бизнес-контекст и история
│   ├── INDEX.md               ← оглавление
│   ├── company/, products/, audience/, goals/, economics/, marketing/, assets/, seed/
│   ├── история/               ← рефлексии чатов (формат — см. ниже)
│   ├── _backups_2026-05-08-merge/   ← дампы БД QT и snapshot auto-memory (Этап 1 слияния)
│   └── _archive_2026-05-08/   ← архив старого C-PC2 business/ (на случай отката)
├── plans/                     ← технические планы (один план = одна функция)
│   ├── 2026-04-23-platforma-i-aukciony.md   ← канонический план модуля аукционов (бывший QT)
│   ├── 2026-05-08-configurator-discovery.md ← discovery C-PC2 для оркестратора
│   └── README.md
├── app/                       ← FastAPI «Конфигуратор» (:8080)
│   ├── main.py, auth.py, config.py, database.py, scheduler.py
│   ├── routers/               ← /admin, /project, /export, /mapping, /
│   ├── services/              ← бизнес-логика (см. «Сервисы» ниже)
│   ├── templates/             ← Jinja2
│   └── ...
├── portal/                    ← FastAPI «Портал сотрудника» (:8081)
│   ├── main.py, scheduler.py
│   ├── routers/               ← auth, home, admin_*
│   ├── services/              ← backup_service, dashboard
│   ├── templates/             ← Jinja2
│   └── ...
├── shared/                    ← общий код двух приложений
│   ├── auth.py, audit.py, audit_actions.py, db.py, permissions.py
│   ├── component_filters.py, sentry_init.py, user_repo.py
│   └── templates/_partials/fx_widget.html
├── auctions/                  ← (планируемое место под QT-модули, появится на Этапе 3+)
├── migrations/                ← 29 SQL-файлов, голый PostgreSQL
├── scripts/                   ← CLI: load_price, create_admin, apply_migrations, бэкфиллы, диагностики
├── tests/                     ← 98 файлов, ~1300 тестов (pytest-xdist)
├── static/                    ← JS, CSS-исходник, dist, шрифты
├── data/, db_dumps/, enrichment/, reference_prices/, logs/
├── design_references/, visual_samples/
├── docs/                      ← деплой-доки
├── package.json, package-lock.json, tailwind.config.js, postcss.config.js, node_modules/
├── Dockerfile, Dockerfile.portal, Procfile, railway.json, railway.portal.json
├── requirements.txt, pytest.ini, .env.example
└── tailwind.config.js
```

### Где что искать

| Что нужно | Куда смотреть |
|---|---|
| Зачем делаем проект, для кого, бизнес-логика | `.business/INDEX.md` |
| Целевой пользователь, боли, желания | `.business/audience/` |
| Что за продукт, тарифы, цены | `.business/products/` |
| Цели и метрики | `.business/goals/` |
| Экономика (доход/расход/юнит) | `.business/economics/` |
| Каналы продвижения, активы, контент | `.business/marketing/` |
| Брендинг (лого, цвета, шрифты) | `.business/assets/` |
| История чатов и рефлексии | `.business/история/` |
| Бэкапы БД QT (этап 1 слияния) | `.business/_backups_2026-05-08-merge/` |
| Технические планы реализации | `plans/` |
| План аукционов (бывший QT) | `plans/2026-04-23-platforma-i-aukciony.md` |
| Discovery конфигуратора | `plans/2026-05-08-configurator-discovery.md` |
| Конфигуратор ПК | `app/` |
| Портал сотрудника | `portal/` |
| Общий код двух приложений | `shared/` |
| Аукционный модуль (после слияния) | `auctions/` (появится на Этапе 3+) |
| Сервисы конфигуратора | `app/services/` (см. блок ниже) |
| Сервисы портала | `portal/services/` |
| SQL-миграции | `migrations/` |
| Тесты | `tests/` |
| Скрипты CLI | `scripts/` |

## Сервисы конфигуратора (`app/services/`)

```
app/services/
├── auto_price/             ← runner.py + fetchers/ (treolan REST, ocs/merlion IMAP, netlab HTTP, resurs_media SOAP)
├── budget_guard.py         ← дневной лимит OpenAI, читает курс ЦБ
├── compatibility/          ← rules.py — совместимость компонентов сборки
├── component_service.py
├── configurator/           ← builder, candidates, pretty, prices, schema, selector, warnings
├── enrichment/
│   ├── runner.py, persistence.py, report.py
│   ├── claude_code/        ← CSV-workflow для ручного enrichment (не SDK)
│   ├── openai_search/      ← gpt-4o-mini-search-preview
│   └── regex_sources/      ← regex по raw_name
├── export/                 ← excel_builder, kp_builder (Word), exchange_rate, email_composer, email_sender
├── manual_edit/            ← ручное редактирование компонентов через CSV
├── mapping_service.py      ← ручной маппинг unmapped_supplier_items
├── nlu/                    ← parser (gpt-4o-mini), pipeline, formatter, fuzzy_lookup, profiles, prompts/, request_builder, schema, commentator
├── openai_service.py
├── price_loader.py         ← тонкая обёртка load_ocs_price для совместимости
├── price_loaders/          ← orchestrator + 6 поставщиков (ocs, merlion, treolan, netlab, resurs_media, green_place)
├── spec_naming.py, spec_recalc.py, spec_service.py
├── supplier_service.py
├── web_result_view.py
└── web_service.py
```

## Расписание APScheduler

**Конфигуратор** (`app/scheduler.py`, активация через `RUN_SCHEDULER=1`):

| Время МСК | Что делает |
|---|---|
| 08:30, 13:00, 16:00, 17:00, 18:15 | Курс USD/RUB с ЦБ → таблица `exchange_rates` |

**Портал** (`portal/scheduler.py`, активация через `APP_ENV=production` или `RUN_BACKUP_SCHEDULER=1`):

| Время МСК | Slug | Что делает |
|---|---|---|
| 03:00 | `daily_backup` | `pg_dump` → Backblaze B2 |
| Вс 04:00 | `audit_retention` | Чистка `audit_log > 180 days` |
| 07:00 | `auto_price_loads_treolan` | REST API |
| 07:10 | `auto_price_loads_ocs` | IMAP |
| 07:20 | `auto_price_loads_merlion` | IMAP |
| 07:30 | `auto_price_loads_netlab` | HTTP |
| 07:40 | `auto_price_loads_resurs_media` | SOAP |
| 07:50 | `auto_price_loads_green_place` | (no-op до появления fetcher'а) |

Каждый cron-job смотрит `auto_price_loads.enabled` для своего slug и вызывает `app/services/auto_price/runner.py::run_auto_load(slug, 'scheduled')`.

## Папка `.business/`

Скрытая папка с бизнес-контекстом проекта. Здесь живёт информация **зачем** мы делаем проект.

- `INDEX.md` — оглавление (структура: company / products / audience / goals / economics / marketing / assets).
- `история/` — рефлексии чатов в формате `YYYY-MM-DD-краткое-название.md`.
- `_backups_2026-05-08-merge/` — дампы БД QT и snapshot auto-memory (артефакты Этапа 1 слияния).
- `_archive_2026-05-08/` — архив старого C-PC2 `business/` (на случай отката).

## Папка `plans/`

Технические планы реализации. Здесь живёт информация **как** мы делаем проект. Один план = одна функция.

## Как работать с проектом

- Перед любой задачей — прочитай этот файл, затем нужные документы из `.business/` и `plans/`.
- Если меняется бизнес-логика — обнови `.business/INDEX.md` и нужные файлы.
- Если меняется технический план — обнови соответствующий план в `plans/`.
- Если появляется новая важная папка или документ — обнови этот `CLAUDE.md`.

## ВАЖНО: план для каждой новой функции

Любая функция, которую мы создаём в любом чате, всегда оформляется планом в папке `plans/`.

Правила:

1. Один план = одна функция. Если план уже есть — работаем с ним.
2. Имя файла: `YYYY-MM-DD-название-функции.md`.
3. План делится на фазы. У каждой фазы статус `[ ]` или `[x]`.
4. В конце плана — итоговый блок: реализован целиком или нет, что осталось.
5. Любой агент обязан актуализировать план после каждой сессии.

## ВАЖНО: завершение каждого чата

В конце каждой сессии записывай рефлексию в файл `.business/история/YYYY-MM-DD-краткое-название.md` (создавай папку при необходимости).

Формат:

1. Какая задача была поставлена.
2. Как я её решал.
3. Решил ли — да / нет / частично.
4. Эффективно ли решение, что можно было лучше.
5. Как было и как стало.

## ВАЖНО: репо QuadroTech заморожен

Соседний репо `d:\ProjectsClaudeCode\KVADRO_TEX\` помечен `FROZEN.md` 2026-05-08 и больше не редактируется. Все его данные (БД-дампы, snapshot auto-memory) скопированы в `.business/_backups_2026-05-08-merge/`. Методология (CLAUDE.md, plans/, .business/) перенесена в C-PC2 на Этапе 2 слияния. Перенос кода — Этап 3+, см. `plans/2026-04-23-platforma-i-aukciony.md`.

## Язык

Всегда отвечай по-русски. Идентификаторы (имена функций, переменных, колонок БД) — на английском, по правилам проекта. Комментарии в коде, сообщения коммитов, CLI-подсказки — по-русски.
