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

**Этап 9Б закрыт полностью** (9Б.1 → 9Б.4):

- Портал в проде на `app.quadro.tatar`, конфигуратор на
  `config.quadro.tatar`, единая сессия через cookie на `.quadro.tatar`.
- Permission enforcement на двух уровнях: UI портала + middleware
  конфигуратора (см. `docs/architecture.md`).
- Страница логина портала редизайнена (лого UADRO, крупная «ПОРТАЛ»,
  центральное brand-свечение, squircle-карточка формы).

- Тесты: **764 passed + 2 skipped** локально (+7 за 9Б.2.1).
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

#### 9Б.2 — Дашборд портала + обновление палитры ✅

- Палитра: конфигуратор +1 ступень светлее (#0E121C base),
  портал +3 ступени светлее (#181E2C base) — обе остаются тёмными.
  Brand `#2052E8` не меняется. Реализация — через CSS-переменные
  и `themed()` хелпер в `tailwind.config.js`, переключение
  body-классом `app-theme` / `portal-theme`. Один общий
  `static/dist/main.css`.
- Дашборд портала: 5 виджетов (активные проекты, менеджеры,
  курс доллара ЦБ, свежесть прайсов OCS/Merlion/Treolan,
  компоненты в БД с разбивкой по 8 категориям) и большая
  squircle-плитка «Конфигуратор ПК». Виджеты доступны всем
  авторизованным; плитка модуля — только тем, у кого есть
  `permissions["configurator"]`.
- Сервис данных: `portal/services/dashboard.py:get_dashboard_data(db)`.
  Все запросы — text-SQL, на пустой БД безопасно отдаёт нули.
- Топбар портала: лого QUADRO + caption «Портал» + nav-ссылки
  («Главная», для admin — «Пользователи»), активный пункт
  подсвечен brand-полоской. Справа — имя/роль + аватар + кнопка
  выхода.
- В сайдбар конфигуратора добавлен пункт «← Портал» над плашкой
  курса ЦБ, ссылается на `${PORTAL_URL}/`.
- Тесты: 16 новых в `tests/test_portal/test_dashboard.py`.
  Всего 757 passed (+16 за этап).

#### 9Б.2.1 — Унификация структуры портала с конфигуратором ✅

- Портал переехал с собственного топбара на тот же `kt-app-shell`/
  `kt-sidebar`/`kt-main`-каркас, что и конфигуратор: лого UADRO +
  caption «ПОРТАЛ» в шапке, навигация (Главная, Пользователи только
  для admin), курс ЦБ + back-link «← Конфигуратор» + карточка
  пользователя в подвале. Адаптив — тот же ≤1024px → 72px.
- Виджет курса ЦБ вынесен в общий партиал
  `shared/templates/_partials/fx_widget.html`. Оба `Jinja2Templates`
  получают список директорий `[<свои шаблоны>, shared/templates]`,
  благодаря чему партиал виден обоим сервисам без копирования.
- Усилены градиенты/свечения на иконках виджетов (30% brand с
  inset-glow), плитке «Конфигуратор ПК» (диагональный gradient к
  brand 500, hover усиливает фон и свечение границы), больших
  числах виджетов (text-fill градиент от ink.primary в ink.brand-200).
- `/admin/users` переехал в новый kt-content; форма создания и
  таблица — в squircle-карточках (.card / .kt-table).
- Фикс: лейбл «Бюджет OpenAI» в Сводке главной конфигуратора
  переименован в «Бюджет OpenAI · сегодня» — отражает суть (потрачено
  сегодня / лимит на день).
- Фикс: виджет «Свежесть прайсов» учитывал только `status='success'`,
  но реальный orchestrator пишет `status='partial'` (часть сматчилась,
  часть нет). Теперь учитываем `IN ('success','partial')` — на
  локальной БД виджет ожил (OCS «свежий», Merlion/Treolan «свежие»).
- Тесты: 7 новых в `tests/test_portal/test_dashboard.py` — структура
  kt-app-shell/kt-sidebar, наличие/отсутствие ссылок, fx-партиал
  отрисовывается, partial-загрузка засчитывается. Всего **764 passed**.

#### 9Б.3 — Деплой портала на Railway ✅

Создание второго Railway-сервиса по `Dockerfile.portal`,
выставление production env-переменных
(`PORTAL_URL=https://app.quadro.tatar` и т.д.), привязка домена
`app.quadro.tatar`. Описание сетапа — в `docs/deployment.md`.

##### 9Б.3.1 — Per-service Railway config ✅

Подэтап в рамках 9Б.3. Изначально Railway применял к новому сервису
портала корневой `railway.json` (конфиг конфигуратора, `Dockerfile` +
healthcheckTimeout=30) — поэтому портал собирался не из своего
Dockerfile.

Решение: добавлен `railway.portal.json` (`Dockerfile.portal`,
healthcheckTimeout=300 — нужно для холодного старта с миграцией 017).
В Railway → Settings сервиса портала указывается путь к нему
через Config-as-code. `railway.json` не тронут — продолжает обслуживать
ConfiguratorPC2. Двух-сервисный сетап описан в `docs/deployment.md`.

#### 9Б.4 — Permission enforcement + редизайн логина ✅

Закрывающий подэтап 9Б, по итогам ручного теста на проде.

- **Конфигуратор**: HTTP-middleware в `app/main.py`. После
  `SessionMiddleware` проверяет, что у залогиненного пользователя
  стоит `permissions["configurator"]=true` (admin всегда True).
  Без права — 302 на `${PORTAL_URL}/?denied=configurator` (для HTML)
  или 403 JSON (для API). Служебные пути (`/static`, `/healthz`,
  `/logout`) пропускаются. Раньше менеджер без права мог зайти прямо
  по URL `config.quadro.tatar/` и обойти UI-фильтр.
- **Портал**: ссылка «← Конфигуратор» в подвале сайдбара
  оборачивается `{% if has_permission(...) %}` — без права просто
  не рендерится. `has_permission` зарегистрирован Jinja-global'ом
  в `portal/templating.py`.
- **Главная портала**: `?denied=<module_key>` превращается во
  flash-баннер «У вас нет доступа к модулю …» (semantic-warning,
  с крестиком-закрытием на клиенте).
- **Страница логина**: редизайн под дизайн-систему портала.
  Лого UADRO + крупная «ПОРТАЛ» с расперекладкой букв (как в
  сайдбаре, но крупнее) + таглайн «Внутренний сервис КВАДРО-ТЕХ» +
  squircle-карточка формы. Центральное brand-свечение под формой
  (большой radial-gradient с blur), brand-обрамление карточки,
  фокус-стейт с усиленным свечением. Адаптив <480px: лого/каплейн
  меньше, карточка занимает почти всю ширину. CSRF, POST и
  `?next=`-логика не тронуты.
- **Тесты**: 8 в `tests/test_web/test_permission_middleware.py` +
  7 в `tests/test_portal/test_permission_ui.py`.

#### 9Б.5 — Управление ролями пользователей в портале ✅

По следам реального онбординга нового пользователя (партнёра по бизнесу):
до 9Б.5 в `/admin/users` был только один путь смены роли — править БД
руками. Теперь админ может менять роль (admin/manager) через UI.

- **Создание пользователя**: к форме на `/admin/users` добавлен селект
  «Роль» (Менеджер по умолчанию / Администратор). При создании
  администратора `permissions={}` (admin видит всё и без них), у
  менеджера остаётся дефолт `{"configurator": True}`.
- **Смена роли существующего пользователя**: в столбце «Роль» таблицы —
  селект + кнопка OK. POST на `/admin/users/{id}/role` с `role` и
  `csrf_token`. Эндпоинт защищён `require_admin` (manager получает 403,
  аноним — 302 на /login).
- **Защита от «нет админов»**: сервер считает `count_admins()` перед
  понижением; если в БД ровно один admin и его пытаются понизить — 400
  с сообщением «Нельзя понизить последнего администратора. Сначала
  повысьте другого пользователя до администратора.»
- **Самопонижение**: POST требует `confirm_self_demotion=true`
  (без флага — 400 с пояснением). UI добавляет JS-`confirm()` перед
  отправкой формы для своего пользователя; скрытое поле с флагом
  включено в форму. Тонкая защита: даже с флагом сервер сначала
  проверяет «последний админ», поэтому случайно остаться без админов
  невозможно.
- **No-op**: смена `admin → admin` или `manager → manager` — 302 без
  записи в БД и без INFO-лога, чтобы не плодить пустые апдейты.
- **Валидация**: `role` ∉ {admin, manager} → 422; несуществующий
  target → 404.
- **Логирование**: на каждое успешное изменение — `logger.info` с
  actor_id/login, target_id и переходом from→to.
- **Shared/user_repo**: добавлены `count_admins()`, `get_role()`,
  `set_role()`. У `create_manager()` появился параметр `role` (дефолт
  `'manager'`); сигнатура для существующих вызовов сохранена.
- **Тесты**: 11 новых в `tests/test_portal/test_admin_role_change.py`
  (promote, demote с другим админом, manager→403, аноним→302,
  последний админ→400, self-demotion с флагом и без, 422, 404, no-op,
  создание admin через форму, наличие селекта в UI). Один тест в
  `test_admin_users.py` обновлён под новый заголовок формы.
  Всего **800 passed**.

### Этап 9В — операционная устойчивость

#### 9В.1 — Hobby Railway, резервный админ ✅

(Закрыт до 9В.2; здесь по контексту — добавлен второй администратор и
переведён аккаунт Railway на Hobby-план для прод-нагрузок.)

#### 9В.2 — Бекапы БД на Backblaze B2 с ротацией ✅

Автоматические резервные копии PostgreSQL вынесены за пределы Railway:
если плагин Postgres откажет (или Railway упадёт целиком), данные
остаются доступны на стороне B2.

- **portal/services/backup_service.py**: `make_pg_dump` вызывает
  `pg_dump --format=custom --no-owner --no-acl`, `upload_to_b2` льёт
  через boto3 (S3-совместимый API B2), `rotate_backups` применяет
  политику 7 daily / 4 weekly / 6 monthly. Главная функция —
  `perform_backup`: dump → загрузка в `daily/` всегда, в `weekly/` по
  воскресеньям МСК, в `monthly/` 1-го числа МСК → ротация.
- **portal/scheduler.py**: APScheduler с одной cron-задачей
  `daily_backup` на 03:00 МСК (`misfire_grace_time=3600`,
  `max_instances=1`). Активируется при `APP_ENV=production` или
  `RUN_BACKUP_SCHEDULER=1`, чтобы тесты и dev-окружение не дёргали
  реальный B2-бакет.
- **/admin/backups** (admin only): список всех бекапов с группировкой
  по уровням (daily/weekly/monthly), кнопка «Создать бекап сейчас»
  (через `BackgroundTasks` — UI не висит на 30+ секунд pg_dump'а),
  стриминг загрузки `.dump` файлов с защитой от path traversal
  (regex на имя, whitelist на tier).
- **Безопасность секретов**: `mask_db_url()` маскирует пароль в
  `DATABASE_URL` перед логированием; stderr pg_dump'а пропускается через
  ту же маску плюс scrub голого пароля, чтобы он не утёк через ошибки.
  `B2_APPLICATION_KEY` нигде не логируется.
- **Dockerfile.portal**: добавлен `postgresql-client-16` через
  официальную репу PGDG (signed-by keyring, без устаревшего apt-key) —
  стандартный Debian 12 даёт только pg_dump 15, который несовместим с
  custom-форматом 16-й серверной версии.
- **requirements.txt**: добавлен `boto3>=1.34,<2.0`.
- **Env vars (Railway)** — на обоих сервисах: `B2_ENDPOINT`,
  `B2_BUCKET`, `B2_KEY_ID`, `B2_APPLICATION_KEY`. Application Key
  ограничен Read+Write только на `quadro-tech-db-backups`.
- **docs/disaster_recovery.md**: процедура восстановления (скачать
  дамп, поднять пустую БД, `pg_restore --clean --if-exists`, прописать
  env vars, бутстрап админа, smoke-тест), контакты на случай катастрофы,
  рекомендация раз в квартал делать тестовый restore локально.
- **Тесты**: 25 в `tests/test_portal/test_backups.py` (ротация по
  трём уровням, изоляция между tier'ами, 4 кейса perform_backup в
  разные дни МСК включая редкий «1-е число + воскресенье», UI-доступы
  admin/manager/anonymous, 6 параметризованных кейсов path traversal,
  команда pg_dump, обработка падения, mask_db_url). Всего после этапа
  — **819 passed** локально (94 в test_portal/, без регрессий).

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
