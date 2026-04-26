# История этапов и план

История того, как проект пришёл в текущее состояние, и что планируется
дальше. Полезно для понимания «почему сейчас именно так» и для онбординга
в новых чатах.

## Завершённые этапы

### Этап 1 — структура БД ✅

Базовая схема: 8 таблиц компонентов (cpu, motherboard, ram, gpu,
storage, case_, psu, cooler) + suppliers + supplier_prices.
Миграция `001_init.sql`.

### Этап 2 — загрузка прайс-листов ✅

Адаптеры для **OCS**, **Merlion**, **Treolan** в
`services/price_loaders/`. Раннер `orchestrator.py` парсит Excel,
сопоставляет с компонентами по MPN/GTIN, пишет в `supplier_prices`.

### Этап 2.5 — обогащение характеристик ✅

`services/enrichment/` — наполнение специализированных полей
(max_tdp кулера, объём кэша CPU, скорость DDR и т.п.) из regex-правил
по описанию + AI-fallback.

Текущая заполненность: **~93% от 2207 компонентов**. Оставшийся
техдолг — в [enrichment_techdebt.md](enrichment_techdebt.md).

### Этап 3 — подбор конфигурации ✅

`services/configurator/` — выбор кандидатов, проверка совместимости
(сокет, DDR, форм-фактор, мощность БП, охват TDP кулера), минимизация
цены, сборка N лучших конфигураций.

### Этап 4 — NLU ✅

`services/nlu/` — превращение свободного текста в `BuildRequest`.
Pipeline: regex → fuzzy lookup → fallback на OpenAI gpt-4o-mini.
Контроль расходов через `services/budget_guard.py`.

### Этап 5 — веб-сервис ✅

FastAPI + Jinja2, авторизация (bcrypt + сессии), история запросов,
админка (бюджет, пользователи, все запросы). Миграция
`007_web_service.sql`.

### Этап 6.1 — карточная раскладка результата ✅

Отображение конфигурации в виде карточек с группами компонентов.

### Этап 6.2 — проекты с несколькими конфигурациями ✅

`projects` ↔ `configurations` ↔ `specification_items`. Чекбоксы
«в спецификацию», AJAX-обновление количества. Миграция
`008_project_specification.sql`.

### Этап 7 — Merlion и Treolan, GTIN, ручное сопоставление ✅

- Адаптеры для Merlion и Treolan.
- GTIN-сопоставление как fallback после MPN.
- Очередь `unmapped_supplier_items` и админ-страница `/admin/mapping`
  для разбора.
- Бэкфил GTIN для уже загруженных компонентов:
  `python scripts/backfill_gtin.py`.

Миграция `009_multi_supplier_and_gtin.sql`.

### Этап 8 — экспорт ✅

`services/export/` — Excel (openpyxl), Word/КП (python-docx) и email.
Финальная версия — после нескольких подэтапов 9А.2.4–9А.2.7
(см. ниже). Последние коммиты: a658132, e36b6d5.

### Этап 9А — редизайн UI на дизайн-систему ✅

Полный редизайн интерфейса под тёмную enterprise-палитру с
brand=#2052E8. **10 подэтапов** 9А.1 → 9А.2.7. Тестов: **576 → 721**
(+145 за этап 9А).

Хронология подэтапов:

- **9А.1** — локальная сборка Tailwind вместо CDN, локальный шрифт
  Inter, единый набор токенов (`tailwind.config.js`), компонентные
  классы (`@layer components`). См.
  [ui_design_system.md](ui_design_system.md).
- **9А.2** — стартовое наполнение дизайн-системы, рефакторинг
  существующих шаблонов под новые токены и классы.
- **9А.2.3** — курс ЦБ автообновлением через APScheduler (5 cron-задач:
  8:30, 13:00, 16:00, 17:00, 18:15 МСК), reoptimize конфигураций,
  фиксированный сайдбар, пагинация номерами. Миграция `015`.
- **9А.2.4** — фикс кэша CSS: kt-* классы перенесены из `@layer
  utilities` в `@layer components` + cache-busting.
- **9А.2.5** — компактный diff reoptimize, авто-обновление UI после
  пересчёта, поля страницы в Word, nbsp-неразрывные пробелы.
- **9А.2.6** — расширены колонки Цена/Сумма в Word-КП, фикс артефактов.
- **9А.2.7** ⭐ — **программная сборка КП-таблицы с нуля**, явный
  шрифт Calibri в Normal-стиле + rFonts на каждом run. Финальный
  фикс артефактов экспорта. См. [design-decisions.md](design-decisions.md).

## Текущий статус

**Этап 10.3 — перенос БД на Railway — завершён.** Состояние:

- Тесты: **727 passed + 2 skipped** локально.
- Миграции: **001–016** применены и на локальной dev-БД, и на
  Railway-БД.
- На Railway залит каталог: 5116 компонентов, ~60 скрытых,
  6434 supplier_prices, 2082 unmapped, история курсов ЦБ.
- Готов к продуктивной работе на `config.quadro.tatar`.

## Предстоящие этапы

### Этап 10 — деплой на Railway

#### Этап 10.1 — подготовка репозитория ✅

- `Procfile` и `railway.json` (Nixpacks builder, healthcheck `/healthz`).
- `scripts/apply_migrations.py` — идемпотентный раннер plain-SQL
  миграций (Alembic в проекте нет).
- `scripts/bootstrap_admin.py` — создание админа из `ADMIN_USERNAME`/
  `ADMIN_PASSWORD` при первом старте, без перезаписи существующего.
- `app.config.Settings`: новые поля `app_env`, `app_secret_key`,
  `cookie_domain`, `run_scheduler`, `admin_username`, `admin_password`.
  На production без `APP_SECRET_KEY` сервис падает на старте.
- `/healthz` расширен до `SELECT 1` к БД (200/503).
- APScheduler запускается только при `RUN_SCHEDULER=1`.
- `.env.example` переписан под новый набор переменных,
  [`docs/deployment.md`](deployment.md) описывает деплой.

#### Этап 10.1.1 — фикс Nixpacks ✅

Явный `providers = ["python", "node"]` + `nixPkgs = [...]` в
`nixpacks.toml`, чтобы сборка не падала на гибридной структуре
проекта (Python + Node для Tailwind).

#### Этап 10.1.2 — переход на Dockerfile ✅

Nixpacks конфликтовал с явным `nodejs_18` (дубль `bin/npx`,
exit 100). Перешли на собственный `Dockerfile` — полный контроль,
никакой автодетекции. Подробности — `docs/deployment.md` раздел
«Сборка через Dockerfile».

#### Этап 10.2 — первый деплой ✅

- `config.quadro.tatar` привязан (CNAME на Railway-инстанс).
- Прописаны секреты в Railway → Variables.
- Healthcheck `/healthz` зелёный.
- На пустую БД накатились миграции 001–016, `bootstrap_admin.py`
  создал учётку `admin`.

#### Этап 10.3 — перенос БД (вариант А) ✅

- `pg_dump --format=custom --no-owner --no-acl` локальной БД.
- TRUNCATE Railway-таблиц (кроме `schema_migrations`) с
  `RESTART IDENTITY CASCADE`.
- `pg_restore --data-only --disable-triggers` в Railway-Postgres.
- `scripts/reset_admin_password.py` — переиспользуемый скрипт-upsert
  админ-пароля из `ADMIN_USERNAME`/`ADMIN_PASSWORD`. После заливки
  пароль admin перебит на production-вариант.
- Sequences подтянулись автоматически из `SEQUENCE SET`-команд в
  TOC дампа, ручной `setval` не понадобился.
- Подробности — `docs/deployment.md` раздел «Перенос данных через
  pg_dump / pg_restore».

### Этап 9Б — портал app.quadro.tatar

Параллельный сервис на поддомене `app.quadro.tatar` — единый вход
в семейство внутренних инструментов КВАДРО-ТЕХ. Решение: монорепо
с двумя FastAPI-сервисами и общей БД (см. `docs/architecture.md`).

#### 9Б.1 — Архитектурный скелет ✅

- Вынесен общий код в `shared/` (auth, db, permissions, user_repo).
- Миграция 017: `users.permissions` JSONB с дефолтом
  `{"configurator": true}` для существующих пользователей.
- Создан `portal/` (минимальные шаблоны): `/login`, `/logout`, `/`
  с плитками модулей, `/admin/users` с чекбоксами прав.
- Login и `/admin/users` перенесены из конфигуратора в портал.
  Конфигуратор редиректит неавторизованных на
  `${PORTAL_URL}/login?next=...`.
- Защищённый next-redirect (whitelist `ALLOWED_REDIRECT_HOSTS`).
- Cookie общая через `APP_COOKIE_DOMAIN` (`.quadro.tatar`
  на production, пусто локально).
- `Dockerfile.portal` для второго Railway-сервиса.
- Тесты: 28 новых в `tests/test_portal/`, существующие test_web
  адаптированы под login-через-портал. Всего 741 passed.

#### 9Б.2 — Дизайн портала ⏳

Поживее главная страница, общий sidebar, плашка курса,
консистентность с конфигуратором. Дополнительные ключи
permissions (kp_form, auctions и т.д.) выводятся в UI
по мере появления соответствующих маршрутов.

#### 9Б.3 — Деплой портала на Railway ⏳

Создание второго Railway-сервиса по `Dockerfile.portal`,
выставление production env-переменных
(`PORTAL_URL=https://app.quadro.tatar` и т.д.), привязка домена
`app.quadro.tatar`. План — в `docs/deployment.md`.

## Принцип ведения этапов

- Один этап = одна логически связанная фича (или редизайн целого
  слоя).
- Подэтапы (9А.2.1, 9А.2.2, ...) — для длинных этапов с многими
  итерациями.
- Каждый коммит начинается с «Этап X.Y.Z:» и кратко описывает что
  сделано.
- Финальный коммит этапа — без подэтапа в имени, с обобщённым описанием.

## Журнал миграций — соответствие этапам

| Миграция                                        | Этап           |
|-------------------------------------------------|----------------|
| 001_init.sql                                    | Этап 1         |
| 002_add_currency_and_relax_nullability.sql      | Этап 2         |
| 003_widen_model_column.sql                      | Этап 2         |
| 004_add_component_field_sources.sql             | Этап 2.5       |
| 005_add_source_url_to_component_field_sources.sql | Этап 2.5     |
| 006_add_api_usage_log.sql                       | Этап 4         |
| 007_web_service.sql                             | Этап 5         |
| 008_project_specification.sql                   | Этап 6.2       |
| 009_multi_supplier_and_gtin.sql                 | Этап 7         |
| 010_unmapped_score.sql                          | Этап 7         |
| 011_email_support.sql                           | Этап 8         |
| 012_supplier_contact_person.sql                 | Этап 8         |
| 013_components_is_hidden.sql                    | Этап 8         |
| 014_specification_recalculated_at.sql           | Этап 8         |
| 015_exchange_rates_table.sql                    | Этап 9А.2.3    |
| 016_specification_items_parsed_query.sql        | Этап 9А.2.5    |
| 017_add_user_permissions.sql                    | Этап 9Б.1      |
