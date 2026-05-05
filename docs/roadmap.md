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
  custom-форматом 16-й серверной версии. *(В этапе 9В.2.1 версия
  поднята до 18 — Railway апгрейднул Postgres до мажора 18.)*
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

#### 9В.2.1 — Фикс: pg_dump 16 → 18 для совместимости с Railway-Postgres 18 ✅

Боевая проверка 9В.2 через UI `/admin/backups` упала на проде с
ошибкой `pg_dump: error: aborting because of server version mismatch`
(server 18.3, client 16.13). Railway незаметно поднял дефолтный мажор
своего Postgres-плагина с 16 до 18, документация проекта оставалась на
16. pg_dump жёстко отказывается дампить сервер новее своего мажора —
нужно совпадение клиента и сервера.

- **Dockerfile.portal**: `postgresql-client-16` → `postgresql-client-18`.
  PGDG-репа поддерживает мажоры 12-18 одновременно, никаких других
  правок не потребовалось.
- **portal/services/backup_service.py**: текст `RuntimeError`'а при
  отсутствии pg_dump теперь упоминает PostgreSQL 18 (логика поиска
  бинаря и флаги pg_dump не менялись — `--format=custom`,
  `--no-owner`, `--no-acl` стабильны и в 18-м мажоре).
- **Документация**: в `stack.md`, `database.md`, `deployment.md`,
  `disaster_recovery.md` упоминания PostgreSQL 16 заменены на 18 в
  частях про прод; локальная разработка может продолжать жить на 16+
  (бекапы локально не запускаются), но для **локального восстановления
  прод-бекапа** нужен PostgreSQL 18 — клиент должен совпадать с мажором
  сервера.
- **Тесты**: без изменений (мокают subprocess, от версии pg_dump не
  зависят) — 819 passed.

#### 9В.3 — Sentry SDK для мониторинга ошибок ✅

К обоим сервисам подключён Sentry, чтобы 5xx и неперехваченные
исключения видеть в одном месте, а не выкапывать руками из Railway
logs. Подробности — в [monitoring.md](monitoring.md).

- **shared/sentry_init.py**: общий `init_sentry(service_name)` с
  контрактом «нет DSN → False, не падаем». Подключает FastAPI-,
  Starlette- и LoggingIntegration; `traces_sample_rate=0.1` глобально,
  для `/healthz` — 0.01 через `traces_sampler` (иначе healthcheck сам
  сжирает квоту). `send_default_pii=False` — IP/cookies/headers в
  события не попадают.
- **before_send**: фильтрует HTTPException 4xx (401/403/404/валидация —
  пользовательские, не баги) и `asyncio.CancelledError` (нормальное
  поведение на shutdown'е).
- **app/main.py / portal/main.py**: вызов `init_sentry` сразу после
  `load_dotenv()` и до импорта роутеров — чтобы FastAPI-интеграция
  перехватывала исключения с самого старта.
- **shared/auth.py: current_user**: после идентификации зовёт
  `sentry_sdk.set_user({"id": user.id, "username": user.login})` —
  email не кладём (пока нет в `users`), IP не нужен.
- **portal/routers/admin_diagnostics.py**: `/admin/sentry-test` (бросает
  RuntimeError для проверки что 5xx долетает) и `/admin/sentry-message`
  (шлёт `capture_message("info")` без 500-ки), оба за `require_admin`.
- **Per-service DSN**: `SENTRY_DSN_PORTAL` и `SENTRY_DSN_CONFIGURATOR` в
  Railway указывают на разные Sentry-проекты; fallback на общий
  `SENTRY_DSN`. Локально без переменных Sentry просто выключен.
- **requirements.txt**: добавлен `sentry-sdk[fastapi]>=2.0`.
- **Тесты**: 21 новых (15 в `tests/test_shared/test_sentry_init.py` —
  mask_dsn, resolve_dsn, before_send для 4xx/5xx/CancelledError/обычных
  исключений; 6 в `tests/test_portal/test_admin_diagnostics.py` —
  доступ admin/manager/anonymous, capture_message с правильными
  аргументами). Sentry мокается, реальных событий тесты не шлют.
  Всего после этапа — **836 passed**.

#### 9В.4 — Аудит-лог действий пользователей ✅

Внутренний журнал значимых действий: входы в систему, создание/удаление
проектов, экспорт КП, отправка писем поставщикам, изменения ролей и прав.
Sentry ловит ошибки, audit_log фиксирует **нормальные действия** —
закрывает направление №6 (продакшен-готовность). Подробности —
[audit_log.md](audit_log.md).

- **migrations/018_audit_log.sql**: таблица `audit_log` с колонками
  `id`, `created_at`, `user_id` (ON DELETE SET NULL — действия удалённого
  пользователя остаются в логе), `user_login` (денормализуем), `action`,
  `target_type`, `target_id` TEXT, `payload` JSONB, `ip` INET, `user_agent`,
  `service`. Индексы на `created_at DESC`, `user_id`, `action`,
  `(target_type, target_id)`.
- **shared/audit.py**: `write_audit(...)` пишет одну строку в отдельной
  транзакции (`engine.begin()`), любую ошибку БД проглатывает с WARNING
  (не ERROR — иначе Sentry начнёт шуметь). `extract_request_meta(request)`
  возвращает `(ip, user_agent)` с учётом `X-Forwarded-For` от Railway-прокси.
  `AUDIT_DISABLED=1` отключает запись для тест-фикстур без БД.
- **shared/audit_actions.py**: каталог констант (`ACTION_LOGIN_SUCCESS`,
  `ACTION_PROJECT_CREATE`, ...). Чтобы опечатки в строках action
  ловились pylint/импортом, а не глазами на проде.
- **Интеграции**: `portal/routers/auth.py` (login success/failed/logout),
  `portal/routers/admin_users.py` (create/toggle/role_change/permissions),
  `portal/routers/admin_backups.py` (manual_run, download),
  `app/routers/main_router.py` (project.create + build.create),
  `app/routers/project_router.py` (project.create/update/delete + build.reoptimize),
  `app/routers/export_router.py` (export.excel/kp_word + supplier.email_sent),
  `app/routers/admin_router.py` (component.hide/show/update). Принцип:
  пишем **после** успешного коммита основного действия.
- **portal/routers/admin_audit.py + templates/admin/audit.html**:
  `/admin/audit` для admin'ов с фильтрами (пользователь, action,
  target_type, service, диапазон дат МСК), пагинацией по 50 записей
  и CSV-экспортом (StreamingResponse, UTF-8 + BOM для Excel). Открытие
  страницы тоже пишется в лог — `audit.view` с активными фильтрами.
- **portal/scheduler.py**: APScheduler-задача `audit_retention` —
  каждое воскресенье 04:00 МСК `DELETE FROM audit_log WHERE created_at <
  NOW() - INTERVAL '180 days'`. Под тем же флагом `RUN_BACKUP_SCHEDULER`,
  что и бекапы. `AUDIT_RETENTION_DAYS` переопределяет 180.
- **Тесты**: новые в `tests/test_shared/test_audit.py` (write_audit,
  extract_request_meta, обрезка UA до 500 символов, AUDIT_DISABLED,
  swallow DB errors) и `tests/test_portal/test_admin_audit.py`
  (доступы, фильтры, пагинация, CSV-экспорт, audit.view, login
  success/failed, user.create, role_change, backup.manual_run).
  conftest'ы (test_web, test_portal, test_shared) подгружают миграцию
  018 и чистят `audit_log` между тестами. Всего после этапа —
  **~857 passed**.

#### 9В.4.1 — Фикс UX-бага в /admin/audit ✅

Боевая проверка после деплоя 9В.4 показала: при нажатии «Применить» с
пустыми фильтрами форма отправляла `?user_id=&action=&...`, и FastAPI/
Pydantic возвращал 422 при попытке распарсить пустую строку как `int`.

- **portal/routers/admin_audit.py**: опциональные числовые/датовые
  параметры объявлены как `str | None` и парсятся вручную через
  `_parse_optional_int` / `_parse_date`. Пустая строка и невалидное
  значение → фильтр не применяется (вместо 422). То же — в
  `/admin/audit/export`.
- **portal/templates/admin/audit.html**: на форме фильтров — id и
  inline-JS, который перед submit'ом помечает пустые `input/select`
  как `disabled`, чтобы они не попадали в querystring. Чистый URL
  и чистый payload `audit.view`-записи.
- **Тесты**: 5 новых в `tests/test_portal/test_admin_audit.py`
  (пустой user_id, пустые даты, пустой page, невалидный user_id=abc,
  отсутствие пустых ключей в payload audit.view). Всего после
  подэтапа — **868 passed**.

#### 9В.4.2 — Кнопка «Удалить навсегда» в /admin/users ✅

В `/admin/users` появилась физическая возможность удалить пользователя
из БД, в дополнение к существующему soft-delete («Отключить»,
`is_active=false`). Зачем: отключённые тестовые/уволенные учётки
накапливались мусором, чистого способа их убрать не было.

- **Backend**: `POST /admin/users/{id}/delete-permanent` в
  [`portal/routers/admin_users.py`](../portal/routers/admin_users.py).
  Требует CSRF и `confirm_login` (клиент шлёт login пользователя,
  которого собирается удалить — защита от случайного клика по соседней
  строке). Проверки в порядке: 404 → confirm_login mismatch → последний
  admin (`count_admins() ≤ 1`) → self-id → `is_active=true` (надо
  сначала отключить) → есть `sent_emails` (нельзя ломать историю
  переписки с поставщиками). Каждая ветка — 400 с понятным сообщением.
- **Зависимые FK**: `projects.user_id` и `queries.user_id` каскадятся
  (`ON DELETE CASCADE` миграции 007). `unmapped_supplier_items.resolved_by`
  обнуляется явным `UPDATE ... SET NULL` перед `DELETE` (вариант (b)
  из брифа — таблица одна, логика очевидна, миграция не требуется).
  `sent_emails.sent_by_user_id` `NOT NULL` без `ON DELETE` — `SET NULL`
  невозможен без новой миграции, поэтому отказываем 400 (вариант (c)).
  `audit_log.user_id` `ON DELETE SET NULL` (миграция 018) — старые
  записи остаются с `user_id=NULL`, denormalized `user_login` сохраняется.
- **UI**: красная кнопка `btn-danger` «Удалить навсегда» появляется в
  [`portal/templates/admin/users.html`](../portal/templates/admin/users.html)
  только для отключённых, не-собственных и не-последних-admin строк.
  JS-обработчик `ktConfirmHardDelete`: сначала `confirm()` с предупреждением,
  затем `prompt()` с просьбой ввести login. Только при совпадении вводимого
  login с `data-login` кнопки форма реально отправляется. Никаких
  UI-библиотек.
- **Каталог action**: добавлена константа `ACTION_USER_DELETE_PERMANENT
  = "user.delete_permanent"` в [`shared/audit_actions.py`](../shared/audit_actions.py).
  Неиспользуемая `ACTION_USER_DELETE` удалена.
- **Тесты**: 13 новых в `tests/test_portal/test_admin_user_delete.py`
  (права admin/manager/anon, CSRF, 404, confirm_login mismatch, target
  активен, self-удаление, последний admin, успешное удаление disabled
  manager, успешное удаление disabled admin при наличии второго admin,
  payload-аудит, audit_log.user_id→NULL после DELETE, блокировка по
  sent_emails). Всего после подэтапа — **881 passed**.

### Этап 9Г — техдолг

#### 9Г.1 — Срочный техдолг каталога и корреспонденции ✅

Точечные правки накопленного техдолга, упрятанные в один коммит вместо
четырёх микро-PR:

- **Системный фикс корпусных вентиляторов**:
  [`shared/component_filters.py`](../shared/component_filters.py)
  c функциями `is_likely_case_fan` (regex по name/manufacturer + защита
  от CPU-маркеров) и заглушкой `is_likely_external_storage`. Подключено
  в [`app/services/price_loaders/orchestrator.py`](../app/services/price_loaders/orchestrator.py)
  на этапе создания скелета: при категории `cooler` и положительной
  детекции скелет создаётся с `is_hidden=TRUE`. Раньше при следующих
  загрузках свежих прайсов ранее скрытые корпусные вентиляторы вновь
  появлялись как видимые — теперь они отлавливаются автоматически.
  Скрипт [`scripts/hide_case_fans.py`](../scripts/hide_case_fans.py)
  оставлен как ручной аварийный override (запускать обычно не нужно).
- **Внешние Netac USB-C SSD**: разовая чистка через
  [`scripts/hide_external_netac_ssd.py`](../scripts/hide_external_netac_ssd.py).
  Идемпотентно, dry-run по умолчанию. На прод запускается как ручная
  операция админа после деплоя. Заготовка для системного фикса
  оставлена в `is_likely_external_storage` (пока всегда False).
- **Регрессия на http:// в письмах поставщикам**: добавлен тест
  `test_supplier_email_no_hardcoded_http`, ловящий случаи, когда в
  тело письма закладывается ссылка вида `http://config…` /
  `http://app…` / `http://localhost…`. Текущая реализация уже чистая
  (только `https://www.quadro.tatar` в подписи), межсервисные ссылки
  на проде идут через [`settings.configurator_url`](../app/config.py)
  (на Railway = `https://config.quadro.tatar`).
- **`print(..., flush=True)` в `init_sentry`**:
  [`shared/sentry_init.py`](../shared/sentry_init.py). Стартовые
  сообщения «Sentry initialized for …» / «Sentry disabled for …»
  раньше шли через `logger.info`, но не попадали в Railway Deploy Logs
  (uvicorn-handler'ы ещё не были настроены к моменту вызова). Теперь
  оба пишутся через `print(flush=True)`. Остальные логи модуля не
  тронуты, контракт `init_sentry → bool` не меняется.
- **Тесты**: 8 новых в `tests/test_shared/test_component_filters.py`
  (5 кейсов is_likely_case_fan + смешанные + защитная заглушка
  is_likely_external_storage), 3 в `tests/test_shared/test_hide_external_netac_ssd_script.py`,
  2 в `tests/test_price_loaders/test_orchestrator.py` (case fan
  скрывается автоматически, CPU-кулер остаётся видимым), 1 в
  `tests/test_export/test_email_composer.py` (регрессия на http://).
- **Документация**: `docs/enrichment_techdebt.md` обновлён —
  секция 9 (вентиляторы) переписана под системное решение, добавлена
  секция 10 (Netac SSD); `docs/architecture.md` упоминает
  `shared/component_filters.py` как точку расширения для фильтров
  каталога.

#### 9Г.2 — Разработческий комфорт ✅

Три разноплановые правки удобства разработки одним коммитом:

- **Унификация pytest-фикстур**. До 9Г.2 каждая папка тестов
  (`test_web`, `test_portal`, `test_export`, `test_shared`,
  `test_price_loaders`) объявляла свой session-scoped `db_engine` со
  своим списком миграций (`test_export` — 001..014, остальные —
  001..018) и своим набором DROP. При прогоне нескольких папок
  подряд (напр. `pytest tests/test_export/ tests/test_web/`) второй
  conftest применял миграции поверх таблиц первого, и часть тестов
  test_web падала. Теперь источник истины один — корневой
  [`tests/conftest.py`](../tests/conftest.py): session-scoped
  `db_engine` один раз за прогон делает DROP всех известных таблиц
  + накат миграций 001..018; `db_session` тоже корневая. Локальные
  conftest'ы оставляют только свою autouse-чистку таблиц и
  специфичные фикстуры (TestClient, `mock_process_query`, фабрики
  Excel-моков). `pytest tests/` теперь проходит без обходных путей.
- **`openai.RateLimitError` через isinstance**. В
  [`app/routers/main_router.py`](../app/routers/main_router.py) и
  [`app/routers/project_router.py`](../app/routers/project_router.py)
  ветка для дружелюбного сообщения «сервис временно перегружен»
  раньше определялась через сравнение `type(exc).__name__ ==
  "RateLimitError"` — хрупко при переименованиях в SDK. Теперь
  явный `except RateLimitError` после импорта `from openai import
  RateLimitError`. Версия openai в `requirements.txt` уже
  `>=1.12`, изменений не потребовалось.
- **`docs/wordpress_visitcard.md`**. Документация по правкам в
  кастомной WordPress-теме quadro на сайте-визитке
  [quadro.tatar](https://quadro.tatar/) (отдельная сущность мимо
  репо ConfiguratorPC2). Описаны три правки темы (footer.php,
  footer-content.php, пункт меню «Портал сотрудника»), процедура
  восстановления при перезаливе темы, доступы и контакты подрядчика
  aquilamedia.ru.
- **Тесты**: 2 новых в
  [`tests/test_web/test_query_flow.py`](../tests/test_web/test_query_flow.py)
  на ветку RateLimitError (дружелюбный текст) vs прочее исключение
  (generic-сообщение). Всего после этапа — 897 passed + 2 skipped.

### Этап 11.1 — три новых поставщика (Netlab, Ресурс Медиа, Green Place) ✅

Расширение пула дистрибьюторов: к существующим OCS / Merlion / Treolan
добавлены три новых, у каждого свой формат прайс-листа.

- **Миграция 019** (`019_add_new_suppliers.sql`): записи в `suppliers`
  для имён `Netlab`, `Ресурс Медиа`, `Green Place` с `is_active=TRUE`
  и `email=NULL` (контакты пришлёт руководитель отдельным апдейтом —
  понадобятся в этапе 8 для рассылок «есть в наличии?»).
- **Адаптер Netlab** (`app/services/price_loaders/netlab.py`): прайс
  «DealerD.xlsx» / «dealerd.zip», лист «Цены», заголовки в строке 21,
  внутри листа повторяющиеся подсекции с собственной строкой
  заголовков (обнаруживаются по литералу «PartNumber» в col C и
  пропускаются), категории — однострочные разделители в col E
  («Видеокарты ASUS», «SSD Kingston», ...). Маппинг по ключевым
  словам с явным исключением серверной/внешней номенклатуры (HPE,
  Dell, IBM, Lenovo, Huawei, Supermicro и пр.). Бинарные маркеры
  остатка `+`/`-` → 5/0. Цена — тариф D (USD), fallback на
  РРЦ(Руб.). GTIN в прайсе нет. Поддерживает `.zip`-обёртку:
  если путь оканчивается на `.zip`, единственный `.xlsx` внутри
  распаковывается во временный каталог, после загрузки удаляется.
- **Адаптер «Ресурс Медиа»** (`app/services/price_loaders/resurs_media.py`):
  прайс «price_struct.xlsx», лист «Price», заголовки в строке 2,
  двухуровневые разделители категорий: верхний раздел в col A
  («Комплектующие и компоненты»), подкатегория в col B
  («Видеокарты»). В строках данных col B превращается в бренд —
  отличаем по наличию Артикула в col C. Маппинг по парам
  (раздел, подкатегория). Качественные маркеры остатка
  Мало / Средне / Много / Нет → 5 / 20 / 100 / 0. Цена — приоритет
  RUB, fallback USD. GTIN в прайсе нет.
- **Адаптер Green Place** (`app/services/price_loaders/green_place.py`):
  прайс «Price_GP_<номер>_<дата>.xlsx», лист «Worksheet», заголовки
  в строке 1. Категории в трёх колонках Группа 1/2/3 (Merlion-style).
  Прайс в основном про серверное и сетевое железо, в наш каталог
  попадают только две тройки потребительских CPU. Цена — приоритет
  RUB, fallback USD. GTIN в прайсе нет. Зарегистрирован как
  отдельный supplier_id, не как дополнение к Merlion (юридически
  связан с Merlion, но в нашей системе самостоятелен — со своим
  прайс-листом и в перспективе своим email).
- **Регистрация**: новые ключи CLI `netlab`, `resurs_media`,
  `green_place` в `app/services/price_loaders/__init__.py:LOADERS`;
  scripts/load_price.py автоматически подхватывает новые ключи
  (choices берётся из LOADERS.keys()). Авто-детект по имени файла
  поддерживает варианты `dealerd*.{xlsx,zip}` / `netlab*` /
  `price_struct*` / `Price_GP_*`.
- **Тесты**: 32 новых (12 Netlab + 11 Ресурс Медиа + 9 Green Place)
  в `tests/test_price_loaders/test_{netlab,resurs_media,green_place}.py`.
  Excel-моки строятся программно через openpyxl — реальные прайсы
  в фикстуры не утаскиваем. Покрыто: чтение заголовков, маппинг
  категорий (включая исключения серверной номенклатуры), бинарные
  и качественные маркеры остатка, fallback цены USD↔RUB, отказ
  на пустом артикуле/без цены, авто-детект имён файлов, .zip-обёртка
  у Netlab. Всего после этапа — **929 passed + 2 skipped**.
- **Документация**: `docs/database.md` обновлён (раздел про
  suppliers и журнал миграций); этот раздел в roadmap; докстринги
  всех трёх новых loader-ов содержат полную структуру прайса.

#### 11.1.1 — Локальная валидация + email-контакты ✅

После 11.1 прогнали загрузку всех трёх прайсов на локальной БД,
исправили найденные баги парсеров, добавили email-контакты
поставщиков и проаудитили реальный ассортимент Green Place.

- **Фикс openpyxl-режима для Netlab** (`netlab.py`): реальный
  DealerD.xlsx (~77k строк) внутри XML листа имеет повреждённый
  элемент `<dimension ref="A1:A1">`. В режиме read_only openpyxl
  этому верит и возвращает 0 строк. Парсер форсирует
  `ws.reset_dimensions()` перед `iter_rows`, после чего весь
  лист корректно сканируется. Регрессионный тест программно
  воспроизводит квирк (правит sheet1.xml в готовом .xlsx) и
  убеждается, что парсер всё равно вычитывает данные.
- **Нормализация числовых SKU**: Excel часто хранит чисто
  цифровой Артикул как float, и openpyxl возвращает его как
  `11051003.0`. Без нормализации повторная загрузка плодит
  «дубликаты» SKU с разным написанием (`11051003` vs
  `11051003.0`). Хелпер `_normalize` во всех трёх новых
  loader-ах теперь сворачивает целочисленные float к int-строке.
- **Аудит ассортимента Green Place**: из 1320 строк / 44 уникальных
  троек категорий **только 111 строк (две тройки) — потребительские
  CPU**. Остальное — серверы/СХД (~430), сетевое оборудование
  (~190), софт ИБ (~80), телефония (~14), системы безопасности
  (~9), NONAME-комплектующие проектной сборки (364), enterprise
  GPU Tesla A100/H100 (5). `_CATEGORY_MAP` в `green_place.py`
  оставлен в исходном виде (две тройки CPU), решения
  задокументированы как полный аудит в docstring; добавлен
  отдельный тест, фиксирующий все пять основных «отбраковочных»
  кейсов, чтобы случайное расширение карты их не зацепило.
- **Миграция 020** (`020_supplier_emails.sql`): идемпотентные
  UPDATE `email = ...` для Netlab / Ресурс Медиа / Green Place,
  сработают только пока email IS NULL.
- **Локальная загрузка** (на dev-БД kvadro_tech, не продакшн):
  Netlab 73242 строк → 6287 в нашем сегменте (2031 updated +
  4256 added); Ресурс Медиа 14960 → 185 (142 + 43); Green Place
  555 → 111 (42 + 69). Всего по supplier_prices: Netlab 6280,
  Ресурс Медиа 185, Green Place 111. Ambiguous=0 у всех.
- **Тесты**: +4 кейса (2 в test_netlab.py, 2 в test_green_place.py).
  Всего после этапа — **933 passed + 2 skipped**.

### Этап 11.2 — Веб-UI ручной загрузки прайсов в портале ✅

Менеджер компании заливает прайс-лист новых поставщиков (Netlab, Ресурс
Медиа, Green Place) через веб-страницу в портале — без CLI и без
доступа к серверу. Парсинг и матчинг используют те же loader-ы из
`app/services/price_loaders/`, что и `scripts/load_price.py`, поэтому
бизнес-логика не дублируется.

- **Роутер** `portal/routers/admin_price_uploads.py` (require_admin):
  - `GET /admin/price-uploads` — таблица 6 поставщиков (последняя
    загрузка, бейдж свежести ≤24ч/24-72ч/>72ч/none, число цен в БД),
    форма выбора файла + поставщика, журнал последних 30 загрузок.
  - `POST /admin/price-uploads/run` — multipart, валидация (расширение
    из {.xlsx, .xlsm, .xls, .csv, .zip}, размер ≤ 100 МБ), запись в
    audit_log (`price_upload.start`), фоновый запуск
    `orchestrator.load_price` через `BackgroundTasks`. На завершение —
    `price_upload.complete` или `price_upload.failed` с traceback.
  - `GET /admin/price-uploads/{id}/details` — JSON с `report_json` для
    модалки «Подробности».
- **Шаблон** `portal/templates/admin/price_uploads.html` — наследник
  `base.html`, использует существующие `kt-table`/`badge-*`/`btn-*`
  классы. Кнопка «Загрузить прайс» в строке поставщика автоселектит
  его в форме ниже. Модалка JSON-отчёта, ESC-закрытие.
- **Sidebar**: новый пункт «Прайс-листы» (иконка `truck`) в админ-
  секции, рядом с «Бекапы» и «Журнал действий».
- **Виджет дашборда «Свежесть прайсов»** теперь кликабельный для
  админа — вся карточка ведёт на `/admin/price-uploads`. Список
  поставщиков расширен до всех 6 (был только 3).
- **Миграция 021** (`021_price_uploads_report_json.sql`): добавляет
  `price_uploads.report_json JSONB`. Орхестратор пишет в неё детальный
  отчёт (processed/added/updated/skipped/errors/by_source/duration);
  при критическом фейле — со статусом `failed` и `error_message`.
- **Audit constants** `ACTION_PRICE_UPLOAD_VIEW/START/COMPLETE/FAILED`
  в `shared/audit_actions.py`.
- **Тесты**: `tests/test_portal/test_admin_price_uploads.py` — 14
  кейсов (доступы, бейджи, валидация, audit, журнал, JSON-детали).
  Всего после этапа — **947 passed + 2 skipped**.
- **Архитектурное решение**: portal импортирует
  `app.services.price_loaders.orchestrator` напрямую. Альтернатива
  «вынести в shared/price_loaders/» отвергнута — это монорепо с
  одной БД и одним Python-окружением; перенос ради чистоты
  архитектуры породил бы 6 файлов перемещений без выигрыша.

### Этап 11.4 — Корректное обновление цен/остатков и детекция disappeared ✅

Бизнес-кейс: ежедневная перезагрузка одного и того же прайса должна
быть идемпотентной по существующим позициям и явно помечать те, что
исчезли из файла поставщика — иначе вчерашние остатки «зависают»
актуальными после реального ухода SKU со склада.

- **Идентификация позиции** — `(supplier_id, supplier_sku)`. При
  совпадении ключа обновляются `price`, `currency`, `stock_qty`,
  `transit_qty`, `raw_name`, `updated_at`. При смене supplier_sku
  это считается новой позицией (обычный matching MPN→GTIN→скелет).
- **`raw_name` в `supplier_prices`** (миграция 022) — оригинальное
  название из строки прайса. Обновляется ВСЕГДА, даже если новое
  короче или беднее предыдущего: подбор использует `components.model`,
  агрегацию по raw_name делает enrichment (этап 11.6).
- **Disappeared-детекция** — в начале загрузки фиксируется множество
  активных SKU поставщика (`stock_qty + transit_qty > 0`), в процессе
  собираются supplier_sku, упомянутые в текущем прайсе. Не упомянутые
  активные SKU помечаются `stock_qty=0, transit_qty=0, updated_at=NOW()`.
  Запись не удаляется — поставщик завтра может вернуть позицию, и
  обычный UPSERT поднимет наличие. Подбор кандидатов в
  `configurator/candidates.py` фильтрует `stock_qty > 0` (или
  `stock+transit > 0` в режиме `allow_transit`), поэтому
  «исчезнувшие» позиции автоматически выпадают из конфигуратора.
- **Защита от failed-загрузок** — disappeared-обнуление выполняется
  ТОЛЬКО при `status='success'/'partial'`. При `status='failed'`
  (rows_matched=0 при непустом прайсе или исключение в loader) логика
  пропускается; `report_json.disappeared = 0`. Если бы кривой парсинг
  обнулил все остатки поставщика — это был бы инцидент.
- **`report_json` расширен** полями `disappeared`, `disappeared_skus`
  (до 50 первых SKU), `disappeared_truncated` (булев флаг, если
  фактическое число disappeared > 50).
- **UI `/admin/price-uploads`** — новая колонка «Пропали» в журнале
  загрузок: 0 — серый, 1–100 — жёлтый бейдж, >100 — красный (сигнал
  «возможно битый прайс, проверь»).
- **Тесты** — 6 новых сценариев в `tests/test_price_loaders/test_orchestrator.py`:
  обновление существующей цены, обновление raw_name, базовая
  disappeared-детекция, защита от failed, явный stock=0 в файле как
  не-disappeared, truncate disappeared_skus при >50.
- Всего после этапа — **953 passed + 2 skipped**.

### Этап 11.6.1 — Regex-обогащение по supplier_prices.raw_name ✅

После 11.4 поставщики Netlab/Ресурс Медиа/Green Place создают
скелеты, у которых `model` короткий (часто — обрезанный), а полное
название позиции лежит в `supplier_prices.raw_name`. Старый
`scripts/enrich_regex.py` работал по `model` и для таких скелетов
уже не давал ничего.

- **Новый пайплайн** — `app/services/enrichment/raw_name_runner.py`
  и CLI `scripts/enrich_regex_from_raw_names.py`. Прогоняет regex
  по всем raw_name, привязанным к компоненту (до 6 от разных
  поставщиков), и по `model` как fallback. Конфликты значений из
  разных raw_name разрешаются в пользу САМОГО ДЛИННОГО (длинное
  обычно содержит больше характеристик); конфликты логируются и
  попадают в отчёт.
- **Не перезатирает** уже заполненные поля: та же политика, что
  в `persistence.apply_enrichment` — UPDATE только там, где сейчас
  NULL. Идемпотентно.
- **Миграция 023** — `component_field_sources.source_detail TEXT`,
  подметка regex-источника. Для нового пайплайна пишется
  `source='regex', source_detail='from_raw_name'`. Старый прогон
  оставляет `source_detail` NULL; так в аналитике покрытия два
  источника различимы.
- **CLI-флаги** — `--category cpu|...|all`, `--supplier merlion|netlab|...|all`,
  `--component-id <id>`, `--dry-run`, `--batch-size 500`.
- **Локальный замер**: dry-run и боевой прогон на локальной БД
  (после backfill `raw_name = model` как реалистичный нижний потолок)
  записал **4358 полей** в **2358 компонентов**; «осталось с NULL»
  — input для **этапа 11.6.2** (Claude Code-обогащение). Подробности
  и таблица по категориям — в [enrichment_techdebt.md §11](enrichment_techdebt.md).
- **Тесты** — 8 новых в `tests/test_enrichment/test_regex_from_raw_names.py`:
  извлечение из одного raw_name; агрегация из нескольких; не-перезапись
  существующего; dry-run без записи; фильтр по supplier; фильтр по
  category; идемпотентность; конфликт между raw_name → берётся самый
  длинный. Всего после этапа — **961 passed + 2 skipped**.

### Этап 11.6.2.3.3 — Workflow-улучшения AI-обогащения ✅

После 11.6.2.3.x (Cooler) накопилось два пункта техдолга в инфраструктуре
обогащения. Этап закрывает оба, чтобы стартовать категорию Case без
повторного «зануления» 25–30% items на финальном импорте.

- **`scripts/enrich_export.py --stdout`** — не пишет файлы в
  `enrichment/pending/`, а сериализует все batch'и одним JSON-документом
  в stdout (логи и progress в stderr, чтобы не ломать pipe). Параметр
  `--limit N` дополнительно ограничивает число позиций суммарно
  (smoke-тест).
- **`scripts/enrich_export_prod.py`** — wrapper, запускающий
  `enrich_export.py --stdout` прямо в прод-контейнере через
  `railway ssh -s ConfiguratorPC2 -i ~/.ssh/id_ed25519_railway --`.
  Захватывает stdout (JSON), раскладывает batch'и локально в
  `enrichment/pending/<category>/` с прод-id'шниками. Закрывает
  ID-перекос локали и прода — main-источник `unknown_component` при
  импорте на прод. TCP-проксирование БД не открывается; наружу из
  контейнера выходит только JSON.
- **`scripts/enrich_import.py --keep-source`** — после успешного импорта
  файлы остаются в `enrichment/done/`, не переезжают в `archive/`.
  Use case: smoke-импорт на локали, затем повторный импорт на проде
  теми же файлами через railway ssh (раньше делалось ручным
  копированием `archive/ → done/`).
- **Тесты** — 16 новых в `tests/scripts/`:
  `test_enrich_export_stdout.py` (5 — валидность JSON, чистота stdout,
  отсутствие файлов в pending/, контракт `--stdout`+`--all`,
  спецификация формата),
  `test_enrich_import_keep_source.py` (4 — default-перенос в archive/,
  `--keep-source` оставляет в done/, поддержка `--file`, dry-run),
  `test_enrich_export_prod_wrapper.py` (7 — happy-path, non-zero exit,
  битый JSON, защита pending/, `--force`, проброс `--limit`,
  отсутствие railway CLI).
- **Документация** — `docs/enrichment_techdebt.md §12` со сравнением
  старого и нового workflow и PowerShell-командами.

### Этап 11.6.2.4.0 — Аудит и переклассификация мусора в категории Case ✅

Перед AI-обогащением Case (1771 NULL-полей в локальной БД) повторили
опыт Cooler: сначала вычистить мусор, чтобы AI не тратил токены на
не-корпуса. **Главный сюрприз диагностики:** в отличие от Cooler
(там 80% выборки были вентиляторы / термопасты / mounting kits),
категория Case на kvadro_tech уже относительно чистая — на 1876
видимых cases выявлен только 1 реальный кейс мусора (id=1065
Aerocool Core Plus 120мм, попал в категорию ошибочно).

- **5 новых детекторов** в `shared/component_filters.py`
  (`is_likely_loose_case_fan`, `is_likely_drive_cage`,
  `is_likely_pcie_riser`, `is_likely_case_panel_or_filter`,
  `is_likely_gpu_support_bracket`). Бо́льшая часть работает
  профилактически — реальных совпадений в БД нет, но при поступлении
  будущих прайсов они защитят upstream от потенциального мусора.
- **Защитный слой `_CASE_HOUSING_HINTS`** — общий для всех 5
  детекторов. При наличии маркеров корпуса в имени («midi tower»,
  «корпус ПК», «JBOD», «rack-mount», «PC case», «ATX case»,
  «Tempered Glass Edition» и т. п.) ни один детектор не срабатывает.
  Это защищает Lian Li SUP01X (корпус с PCIe Riser в комплекте),
  AIC J2024 JBOD-шасси, Lian Li A3-mATX с Bottom Dust Filter и т. д.
- **Upstream-подключение** в
  `app/services/price_loaders/orchestrator.py::_create_skeleton`:
  для `table == "cases"` детекторы прогоняются ДО вставки и при
  положительном срабатывании скелет создаётся с `is_hidden=True`.
- **Скрипт переклассификации** —
  `scripts/reclassify_cases_trash.py` (dry-run по умолчанию,
  `--confirm --confirm-yes` для apply). Один общий audit-event на
  массовое обновление, отчёт в `scripts/reports/`, SQL-rollback в
  отдельном файле.
- **Whitelist `OFFICIAL_DOMAINS`** в
  `app/services/enrichment/claude_code/schema.py` расширен на 6
  case-вендоров: `gamemax.com`, `raijintek.com`, `xpg.com`,
  `powerman-pc.ru`, `digma.ru`, `hiper.ru` (присутствуют в БД,
  но AI отказывался ходить).
- **Тесты** — 25 новых в
  `tests/test_shared/test_case_trash_detectors.py`: положительные
  кейсы (включая реальный id=1065), отрицательные (полноценные
  корпуса с предустановленными аксессуарами), параметризованные
  housing-hint-проверки. Полный pytest-сьют — **1080 passed + 2 skipped**.
- **Кандидаты в техдолг** (не закрываются этим этапом):
  SBC-корпуса в `cases` (Raspberry Pi, RockPi, ACD Systems IP65) —
  формально корпуса, но не подходят под ATX/mATX-сборку. Решение
  отложено в потенциальный 11.6.2.6.x. Подробности —
  `docs/enrichment_techdebt.md §13`.

### Этап 11.6.2.4.1a — Подготовка AI-обогащения категории Case ✅

Подготовительный этап перед параллельным AI-обогащением Case через
несколько чатов Claude Code. Состоит из верификации upstream'а,
обновления промпт-файла и выгрузки batch'ей с прод-БД через workflow
из 11.6.2.3.3.

- **Верификация upstream-classifier** —
  `app/services/price_loaders/orchestrator.py::_create_skeleton`
  (строки 209-216) уже содержит вызов всех 5 детекторов из 11.6.2.4.0
  (`is_likely_loose_case_fan` / `drive_cage` / `pcie_riser` /
  `case_panel_or_filter` / `gpu_support_bracket`); при срабатывании
  скелет помечается `is_hidden=True`. Дополнительных правок в код
  загрузки не потребовалось.
- **Обновлён `enrichment/prompts/case.md`** по образцу `cooler.md` /
  `gpu.md`. В новом промпте: целевые поля и валидаторы строго из
  `claude_code/schema.py` (`has_psu_included`, `supported_form_factors`,
  `included_psu_watts`); whitelist 25 доменов синхронизирован с
  `OFFICIAL_DOMAINS` (включая 6 case-вендоров, добавленных в 11.6.2.4.0);
  жёсткое правило honest-null; защитный слой для SBC (Raspberry/Orange/
  Rock Pi, Pico, Arduino, SBC) — все три поля `null`; формат ответа с
  `sources_used` в корне; 3 примера input/output (Define 7 без БП,
  POWERMAN ST-2202 с встроенным БП 450 Вт, Raspberry Pi case).
- **`.gitignore` расширен**: `enrichment/pending/` теперь
  игнорируется как runtime-артефакт. Старые tracked-batch'и из
  pending/ остаются в истории; новые batch'и от
  `enrich_export_prod.py` в индекс не попадают.
- **Workflow-фикс для Windows** —
  `scripts/enrich_export_prod.py::_build_railway_cmd`: на Windows
  `subprocess.run` не дополняет литерал `"railway"` до
  `railway.CMD`; теперь имя бинаря резолвится через
  `shutil.which("railway")`.
- **Выгрузка с прода**:
  `python scripts/enrich_export_prod.py --category case --batch-size 30`
  → 8 batch'ей в `enrichment/pending/case/` (230 items суммарно).
  **Сюрприз**: на прод-БД оказалось не ~1840 NULL-кандидатов, а 288
  (58 уже представлены в `done/`/`archive/` — пропущены, к экспорту
  пошли 230). Скорее всего, регекс- и derived-правила из 11.6.2.x.x
  закрыли больше полей, чем учитывала первоначальная оценка.
- **Артефакт**: AI-обогащение в этом этапе НЕ запускается — это фаза
  для параллельных пользовательских чатов в фоне.

### Этап 11.6.2.4.1b — AI-обогащение категории Case ✅

Параллельные AI-чаты Claude Code обработали 8 batch'ей с прода
(230 items, бренды разложены через классификатор по 25 доменам
whitelist + bulk-null fallback).

- **Стратегия обработки**:
  - Перед запуском AI прогнан скрипт-классификатор
    `scripts/reports/process_case_bulk_null.py`: 100 items с
    брендами вне OFFICIAL_DOMAINS / SBC / accessory / unknown сразу
    помечены `null` с reason без web-поиска (Ginzzu 73, ExeGate/Crown/
    Zircon/PowerCool/1stPlayer 12, Thermalright LCD-дисплеи 4,
    InWin-аксессуары 5, ExeGate-рельсы 1, прочее 6).
  - Оставшиеся 130 items разделены на 15 brand-кластеров и переданы
    параллельным AI-агентам (general-purpose subagents) по
    1 file/brand с обязательным ограничением источника на конкретный
    whitelist-домен.
- **AI-исследование** дало 116 items с реальными данными и 14
  honest-null:
  - **С данными**: Deepcool 36/36, Zalman 20/20, XPG 19/20, InWin
    11/13, Lian Li 11/11, Phanteks 5/5, Thermaltake 5/5, Aerocool
    2/3, Foxline 2/2, Chieftec 1/1.
  - **Honest-null**: GameMax 0/7 (реальный домен — `gamemaxpc.com`,
    не `gamemax.com` из whitelist), Powerman 0/3 (powerman-pc.ru
    недоступен — ECONNREFUSED), Formula 0/2 (реальный бренд
    Formula V Line на formulav-line.com), Accord 0/1, HPE 0/1
    (XASTRA — российский OEM, не HPE).
- **Локальный импорт**:
  `python scripts/enrich_import.py --category case --keep-source` →
  92 компонента обновлено, **165 полей записано** (92
  `supported_form_factors` + 73 `has_psu_included`), 0 отклонено.
- **Прод-импорт** через `railway ssh`: 112 компонентов обновлено,
  **203 поля записано** (112 `supported_form_factors` + 91
  `has_psu_included`), 0 отклонено, 0 ошибок. Прод даёт больше
  записей чем локаль (203 vs 165), потому что на проде до этого
  этапа было больше NULL-baseline.
- **SQL ДО/ПОСЛЕ (prod)**:
  | Метрика | ДО | ПОСЛЕ | Δ |
  |--|--:|--:|--:|
  | total_visible | 1946 | 1946 | 0 |
  | supported_form_factors заполнено | 1660 (85.3%) | 1772 (91.0%) | +112 |
  | has_psu_included заполнено | 1759 (90.4%) | 1850 (95.1%) | +91 |
  | watts_resolved (psu=false ИЛИ watts заполнен) | 1757 (90.3%) | 1846 (94.9%) | +89 |
- **Сюрпризы**:
  - **GameMax-домен**: в whitelist `gamemax.com`, но реальный сайт
    производителя — `gamemaxpc.com`. Все 7 GameMax → honest-null.
    Кандидат на правку whitelist в следующей итерации (см.
    `docs/enrichment_techdebt.md §13`).
  - **powerman-pc.ru**: домен недоступен (ECONNREFUSED) во время
    AI-прохода. 3 POWERMAN-items получили honest-null. Можно
    повторить когда домен поднимется.
  - **Формат-баги у 2 субагентов**: InWin вернул `source` вместо
    `source_url` (18 полей), Thermaltake — bare bool/list вместо
    обёртки `{"value": ..., "source_url": "..."}` (10 полей).
    Оба исправлены вручную (для Thermaltake заново найдены URL
    через web_search). Это будущий пункт в `_общие_правила.md` —
    более жёсткий пример output-формата.
- **Артефакты в коммите**: `enrichment/done/case/batch_*.json`
  (8 файлов с результатами AI). Вспомогательные скрипты
  (`scripts/reports/process_case_bulk_null.py`, `split_case_manifest.py`,
  `merge_case_research.py`, `stats_case_enrichment.py`) лежат в
  gitignored `scripts/reports/`; решение по их продвижению в `scripts/`
  proper отложено в техдолг.

### Этап 11.6.2.5.0a — Аудит мусора и unknown-bucket в категории PSU ✅

Запущен через `railway ssh`-обёртку
[`scripts/_psu_audit.py`](../scripts/_psu_audit.py) на проде.
Раскрыл три класса проблем:

1. **234 NULL `psus.power_watts`** в видимом каталоге, из которых
   232 — в bucket `manufacturer='unknown'`. AI-обогащение (этап 11.6.2.5.1)
   без бренда не сможет искать спеку в whitelist-доменах.
2. **9 элементов в `coolers` с PSU-маркерами**: 7 настоящих PSU
   (Aerocool Mirage Gold 650W, PCCOOLER P5-YN750, PcCooler P5-YK850/
   YN1000/YS850/YS1000/P3-F450) попали в coolers потому что у поставщика
   в raw_name есть слово «PCCOOLER»/«Aerocool» в окружении кулерных
   маркеров; ещё 2 — case-дубли уже существующих корпусов в `cases`
   (PcCooler C3B310/C3D510).
3. **120 «PSU-маркеров» в `cases`** — все валидные (корпуса с PSU
   в комплекте, например, Chieftec Hawk «PSU bottom», 1STPLAYER
   1300W в наличии). Ни один из них не подлежит миграции.

Подключённая колонка PSU.brand в БД называется `psus.manufacturer`
(не `brand`); supplier-side таблица `supplier_prices` НЕ имеет
отдельной колонки бренда — есть только `raw_name`. Восстановление
бренда возможно только regex'ом по raw_name.

### Этап 11.6.2.5.0b — Действия на основе диагностики 5.0a ✅

Системный фикс трёх проблем + закрытие пунктов техдолга #2 и #3
(см. `docs/enrichment_techdebt.md §15`).

- **Детектор `is_likely_psu_adapter`** в
  [`shared/component_filters.py`](../shared/component_filters.py).
  Узкие маркеры (адаптер, переходник, зарядное, charger, POE,
  USB-PD, powerbank, dock-station, «блок питания для ноутбука»,
  ББП) + бренд-серии гарантированно-адаптерных позиций (Gembird
  NPA-AC/DC, KS-is, BURO BUM-*/BU-PA, ORIENT PU-C/SAP-/PA-,
  GOPOWER, WAVLINK, FSP FSP040, Ubiquiti POE, Бастион РАПАН).
  Три защитных слоя предотвращают ложные срабатывания на настоящих
  ATX/SFX-PSU: (1) форм-фактор в имени (ATX/SFX/TFX/EPS/80+/модульн),
  (2) явная мощность ≥200W, (3) серия настоящего PSU из whitelist
  (CBR ATX, Exegate UN/PPH/PPX, Ginzzu CB/PC, XPG KYBER/CORE REACTOR,
  Zalman ZM, Aerocool Mirage/Cylon/KCAS, Powerman PM, 1STPLAYER NGDP,
  Thermaltake Smart, Formula VX/KCAS).
- **33 теста** в
  [`tests/test_shared/test_psu_adapter_detector.py`](../tests/test_shared/test_psu_adapter_detector.py)
  на реальных raw_name из БД (positives + negatives + защитные слои).
- **Upstream-классификация в orchestrator**: в
  [`app/services/price_loaders/orchestrator.py`](../app/services/price_loaders/orchestrator.py)
  по образцу case-блока (стр. 209) добавлен psu-блок — новые скелеты
  с `table='psus'` сразу помечаются `is_hidden=TRUE`, если совпал
  детектор. Это фиксит проблему апстрима: «исчезнувший» адаптер не
  появится снова при следующей загрузке прайса.
- **Скрипт `scripts/recover_psu_manufacturer.py`** — восстанавливает
  `psus.manufacturer` для bucket 'unknown' regex-паттернами по
  `supplier_prices.raw_name`. 25 PSU-брендов с приоритетом от длинных
  (Cooler Master, 1STPLAYER, PCCooler, be quiet!) к коротким
  (CBR, FSP, ACD), очистка префикса «Повреждение упаковки»/«Поврежденная
  упаковка»/«Повреждение упраковки» (типичная опечатка поставщика)
  перед матчингом, аудит-event на массовое обновление.
- **Скрипт `scripts/reclassify_psu_misclassified.py`** — идемпотентно
  прогоняет `is_likely_psu_adapter` по `psus.is_hidden=false` и помечает
  кандидатов `is_hidden=TRUE`.
- **Миграция [024_psu_misclassification.sql](../migrations/024_psu_misclassification.sql)**:
  INSERT в psus 7 настоящих PSU из coolers (с правильным manufacturer
  Aerocool/PCCOOLER/PcCooler), UPDATE coolers SET is_hidden=TRUE для
  тех же 7 + 2 case-дублей. Идемпотентна (NOT EXISTS, проверка
  is_hidden=FALSE перед UPDATE), на проде применяется автоматически
  через `scripts/apply_migrations.py` при ближайшем редеплое.
- **Локальные метрики (apply)**:
  - reclassify_psu_misclassified: помечено `is_hidden=TRUE` — 79
    (Ubiquiti POE 5, Cisco POE 1, FSP GROUP 1, и 72 unknown-bucket).
  - recover_psu_manufacturer: восстановлено бренда — 662
    (ExeGate 292, Deepcool 51, Thermaltake 51, 1STPLAYER 43,
    Aerocool 43, CHIEFTEC 36, Ginzzu 22, XPG 22, CBR 21, Cooler Master 18,
    Zalman 18, FSP 14, Powercase 9, Formula 9 и др.).
- **Отложено в 5.0c**: нормализация регистра `psus.manufacturer`
  (Deepcool/DEEPCOOL, ZALMAN/Zalman); расширение whitelist под HSPD,
  Formula V Line, Super Flower, BLOODY, SAMA, Gooxi, Foxconn (нужен
  web-research официальных доменов); ~25 строк cases/coolers,
  ошибочно попавших в psus (Корпус Thermaltake, Cooler Master MasterBox,
  Кулер DeepCool — отдельный детектор `is_likely_misc_in_psu` или
  ручной разбор на 5.1).

### Этап 11.6.2.5.0c — Финальная прокладка перед AI-обогащением PSU ✅

Закрывает два пункта техдолга 5.0b (см. `docs/enrichment_techdebt.md §15`,
секция «Закрыто этапом 5.0c») перед AI-обогащением 11.6.2.5.1.

- **Детектор `is_likely_non_psu_in_psus`** в
  [`shared/component_filters.py`](../shared/component_filters.py) —
  ловит корпуса/кулеры/вентиляторы, ошибочно попавшие в `psus` при
  первичной загрузке прайсов (детектор `is_likely_psu_adapter` их не
  цеплял — нет маркеров адаптера). Жёсткий триггер по leading-маркеру
  («Корпус …», «Кулер …», «Вентилятор …», «Устройство охлажд …»,
  «Chassis», «Mid/Mini/Full-tower») плюс позитивный маркер
  («MasterBox», «AIO», «PC Cooling Fan», «к корпусам») в середине
  строки с защитными слоями: `«Блок питания»`/`Power Supply`, серия
  настоящего PSU из whitelist (`_PSU_REAL_SERIES`), явная мощность
  ≥200W (`_PSU_REAL_WATTAGE`). Защита по форм-фактору (ATX/SFX) НЕ
  применяется — у корпусов это атрибут совместимости и она бы дала
  ложно-отрицательные.
- **Подключение в [`scripts/reclassify_psu_misclassified.py`](../scripts/reclassify_psu_misclassified.py)**:
  оба детектора (`is_likely_psu_adapter` + `is_likely_non_psu_in_psus`)
  работают через OR. Скрипт не дублируется, остаётся идемпотентным,
  audit-event помечен `stage="11.6.2.5.0c"`.
- **8 новых юнит-тестов** в
  [`tests/test_shared/test_component_filters.py::TestIsLikelyNonPsuInPsus`](../tests/test_shared/test_component_filters.py)
  на реальных model-строках из БД (positives + negatives + защитные слои).
- **+5 доменов в OFFICIAL_DOMAINS** ([`schema.py`](../app/services/enrichment/claude_code/schema.py)),
  верифицированы WebFetch'ем: `exegate.ru` (топ-1 PSU-вендор по NULL),
  `crown-micro.com` (CM-PS серия), `gamemaxpc.com` (исправление
  пробела `gamemax.com` → `gamemaxpc.com` из техдолга 14.1),
  `formulav-line.com` (исправление пробела из техдолга 14.5),
  `super-flower.com.tw` (LEADEX серия, топ-OEM PSU). Большинство
  топ-PSU-вендоров (thermaltake/deepcool/aerocool/coolermaster/corsair/
  bequiet/evga/xpg/silverstonetek/raijintek/lian-li/msi/asus/gigabyte/
  powerman-pc.ru/hiper.ru/digma.ru/accord-pc.ru/formula-pc.ru/
  fox-line.ru/acd-group.com) уже были в whitelist до 5.0c.
- **Whitelist matching case-insensitive (страховка)** в
  [`validators.py`](../app/services/enrichment/claude_code/validators.py):
  явный `_OFFICIAL_DOMAINS_LOWER = frozenset(d.lower() for d in OFFICIAL_DOMAINS)`,
  вычисляется при импорте. URL-host уже приводился к lower, теперь
  и whitelist гарантированно сравнивается в lowercase — защита от
  регрессии, если кто-то добавит «Aerocool.com» с заглавной буквы.
  Покрыто `test_url_host_case_insensitive` (4 варианта регистра).
- **Локальные метрики (apply)**: помечено `is_hidden=TRUE` — **26**
  (19 Thermaltake корпуса+кулеры+вентиляторы CT120/CT140/Astria/AX700,
  3 Cooler Master MasterBox, 2 unknown «Корпус MSI Forge»+«Кулер
  для Thermaltake CT120», 1 CHIEFTEC «Корпус Hawk», 1 Deepcool
  «Кулер AN400»). 0 ложно-положительных по 8 настоящим PSU из той же
  выборки SQL (Aerocool SX400/VX-700, Cooler Master Elite NEX,
  Crown CM-PS500W, FSP FSP550, Zircon ATX 400W/450W, INWIN 400W).
- **Полный pytest -n auto: 1129 passed, 2 skipped** (без регрессий
  относительно baseline 11.7).

### Этап 11.6.2.5.1a — Подготовка AI-обогащения PSU ✅

Перед AI-обогащением 144 видимых PSU без `power_watts` (после
аудита/прокладки 11.6.2.5.0a/b/c) выгружаем batch'и с прода и
финализируем upstream-классификатор.

- **Аудит NULL-распределения по 5 полям psus** (через
  `railway ssh -- python -` + psycopg2 на проде, `psql` в Railway-shell
  недоступен): `total_visible=1415`. NULL: `power_watts=144`,
  `form_factor=1415` (100%), `efficiency_rating=1415`,
  `modularity=1415`, `has_12vhpwr=1415`. **Решение**: на этап
  11.6.2.5.1a берём только `power_watts` (целевое поле в
  `TARGET_FIELDS["psu"]` — оно одно). Остальные 4 поля никогда не
  заполнялись (100% NULL у всех 1415 PSU) — это самостоятельная
  инициатива на 11.6.2.5.1b+; включать их сейчас раздуло бы выгрузку с
  ~5 batch'ей до ~47 (1415 / 30).
- **Upstream-классификатор: подключение
  `is_likely_non_psu_in_psus` в [`orchestrator.py`](../app/services/price_loaders/orchestrator.py)**
  (упустили в 5.0c — детектор был добавлен только в reclassify-скрипт
  пост-фактум). Теперь оба детектора работают через OR на стадии
  `_create_skeleton`: `is_likely_psu_adapter(...) OR
  is_likely_non_psu_in_psus(...)` → `is_hidden=TRUE`. Любой адаптер/
  PoE-инжектор/корпус/кулер/вентилятор, попавший в категорию `psu` при
  загрузке прайса, сразу скрывается — AI-обогащение 5.1 не тратит
  тулколлы на его поиск.
- **Промпт [`enrichment/prompts/psu.md`](../enrichment/prompts/psu.md)**
  — переписан с нуля по образцу `case.md`/`cooler.md` (313 строк):
  - 4 защитных слоя: PSU-адаптеры (бренд-серии Gembird NPA-AC*,
    KS-is, BURO BUM-*, ORIENT PU-C/SAP-/PA-, GOPOWER, WAVLINK,
    Ubiquiti POE-, Бастион РАПАН), не-PSU позиции (Корпус/Кулер/
    Вентилятор/MasterBox/AIO/Mid-tower), Ginzzu (домен `ginzzu.com`
    офлайн → honest-null **без обращения к WebSearch**, экономия
    тулколлов), `manufacturer="unknown"` (попытка извлечь бренд из
    raw_name перед поиском);
  - 25+ доменов whitelist'а (PSU-секция + кросс-категорийные вендоры,
    тоже выпускающие PSU: thermaltake, corsair, deepcool, coolermaster,
    aerocool, evga, silverstonetek, bequiet, xpg, raijintek, gamemax,
    pccooler, lian-li, powerman-pc.ru, formula-pc.ru, accord-pc.ru,
    kingprice.ru), синхронизирован с
    [`schema.py::OFFICIAL_DOMAINS`](../app/services/enrichment/claude_code/schema.py)
    (после расширения 5.0c +5 доменов: exegate.ru, crown-micro.com,
    gamemaxpc.com, formulav-line.com, super-flower.com.tw);
  - подсказки по 12 топ-NULL брендам (ExeGate PPH/PPX, Aerocool/
    Formula KCAS «(ex Aerocool)» → formulav-line.com, Deepcool/
    GamerStorm, CHIEFTEC SteelPower/Polaris, Thermaltake Smart/
    Toughpower, Zalman ZM/TX/MegaMax, PcCooler KF/P5, POWERMAN PMP,
    Crown CM-PS, XPG PROBE/PYMCORE/KYBER, Ubiquiti POE-);
  - 3 примера input/output на реальных кейсах БД (ExeGate 650PPH,
    Ginzzu SA400 → null, Gembird NPA-AC4 → null с двойным защитным
    слоем).
- **Выгрузка batch'ей с прода** через
  [`scripts/enrich_export_prod.py`](../scripts/enrich_export_prod.py)
  `--category psu --batch-size 30 --force`: **8 файлов в
  enrichment/pending/psu/, 240 items**. Сюрприз: больше ожидаемых 5
  batch'ей (144 / 30), потому что
  [`exporter._build_select_sql`](../app/services/enrichment/claude_code/exporter.py)
  не фильтрует по `is_hidden` — выгружает все 241 строки с
  `power_watts IS NULL` (144 visible + 97 hidden, минус 1 уже в
  pending/batch_001.json). 97 hidden — адаптеры/корпуса/кулеры,
  скрытые на 5.0a/b/c; защитные слои в `psu.md` корректно вернут им
  null, AI потратит тулколлы впустую — но это безопасный wasted-cost.
  Считать самостоятельным техдолгом: заинженерить `is_hidden=false`
  фильтр в exporter.

### Этап 11.6.2.5.1b — Фикс exporter (is_hidden) и AI-обогащение PSU ✅

Закрывает техдолг exporter, обнаруженный на 5.1a (240 items в pending/
вместо ожидаемых 144 из-за выгрузки скрытых PSU), и проводит
AI-обогащение оставшихся 143 видимых PSU без `power_watts`.

- **Фикс
  [`exporter._build_select_sql`](../app/services/enrichment/claude_code/exporter.py):
  WHERE is_hidden = FALSE AND (...)** — теперь скрытые компоненты
  любой категории не попадают в pending-batch'и. Покрыто тестом
  `test_export_skips_hidden_components` в
  [`tests/test_enrichment/test_export_v2.py`](../tests/test_enrichment/test_export_v2.py).
- **Фильтрация уже выгруженных pending-batch'ей** (одноразово, через
  railway ssh + получение списка hidden PSU id с прода): из 240
  выгруженных items отфильтровано 97 скрытых, осталось 143 в 7
  файлах. После пуша 5.1b сам exporter уже корректно фильтрует, эта
  процедура больше не понадобится.
- **AI-обогащение 143 items** (7 batch-файлов, ~30 web_search/
  web_fetch тулколлов вместо «по 1-2 на каждый item», за счёт
  батч-поиска по сериям):
  - **batch_001** — 20 Gembird NPA-AC*/NPA-DC* — все honest-null
    через защитный слой 1 (PSU-адаптеры), без обращения к WebSearch.
  - **batch_002** — 1 BURO BUM-* (автомобильный адаптер 12-20V) —
    null через защитный слой 1.
  - **batch_004** — 28 ExeGate (PPH-LT, XP, PPX, AA, AB, CP, AAA,
    UNS, UN, PPE, NPX) — все 28 подтверждены через каталоги серий
    на `exegate.ru/catalogue/power/<series>/`.
  - **batch_005** — 27 items: 2 ExeGate (700NPXE, 700PPX), 1
    Aerocool VX Plus 800, 10 CHIEFTEC (BDK/PPS/BDF/BBS/GPX/BFX/BPX),
    9 Deepcool GamerStorm (PF*L → deepcool.com PF*D, PN850M/PN750M
    Gen.5 ATX 3.1, PQ650G/PQ750G/PQ850G/PQ1200G WH → gamerstorm.com),
    5 Deepcool PS650G/PS750G/PS850G/WH → null (на whitelist-доменах
    PS-серия не найдена; вероятная опечатка PS↔PQ в прайсе вендора,
    по аналогии не оцениваем).
  - **batch_006** — 27 items: 1 CoolerMaster Elite NEX W700, 21
    Ginzzu (защитный слой 3, без WebSearch), 2 GIGABYTE GP-UD850GM/
    UD750GM, 3 POWERMAN PMP/PM (powerman-pc.ru возвращает
    ECONNREFUSED — datasheet на whitelist-домене недоступен → null).
  - **batch_007** — 11 items: 1 POWERMAN pm-300sfx (null,
    powerman-pc.ru недоступен), 6 Thermaltake (Smart BM3 750, Smart
    W3 600/700, Smart BX1 SE 550, TR2 S 550), 2 Thermaltake TH240 V2
    Ultra (АИО, защитный слой 2 — не PSU), 3 Zalman (ZM500-XE II,
    ZM600-XE II, ZM500-TX II MegaMax).
  - **batch_008** — 29 items: 1 Zalman ZM750-TMX2 TeraMax II Gold,
    2 Crown CM-PS400/CM-PS450W smart, 11 Aerocool Formula VX «(ex
    Aerocool)» 350-750 → aerocool.io vx-plus-NNN, 4 Aerocool Formula
    KCAS PLUS 500-800 → aerocool.io kcas-plus-NNNw, 1 Aerocool
    Mirage Gold 650W, 2 XPG PROBE600B/PYMCORE750G, 2 Zalman ZM600-XE
    II/ZM400-XEII (повреждение упаковки — те же URL что в основном
    каталоге), 1 Ginzzu PC700 → null, 6 PcCooler P5-YK/YS/YN/F →
    null (pccooler.com.cn whitelist-домен не индексируется в
    поиске; pccooler.com без .cn в whitelist не входит).
- **Локальный sanity-import** (`scripts/enrich_import.py --category
  psu --keep-source`): **89 items совпали с локальной БД, 82 поля
  принято** (все power_watts), 0 reject, 54 honest-null
  зарегистрированы как «AI отказался — поле пустое». Расхождение с
  прод-id (7 items: 1517–1523, 1485, 1480 + другие) — ожидаемое:
  PcCooler/Aerocool Mirage и часть Ginzzu/Aerocool появились на
  проде после последнего sync прайсов в локальную БД.
- **Прод-импорт** (после деплоя 5.1b → e6/f9eebdd на проде, через
  `railway ssh -- python scripts/enrich_import.py --category psu`):
  **143 items в работе, 83 поля принято у 83 компонентов**, 0
  отклонено, 60 honest-null зарегистрированы. На проде на 1 item
  больше совпало с БД, чем локально (83 vs 82 на местной БД), —
  Aerocool Mirage Gold 650W (id=1520) и/или Aerocool VX 550 PLUS DP
  (id=1480) присутствуют только на проде.
- **SQL-статистика прода `psus WHERE is_hidden=FALSE`**:
  - **ДО 5.1b:** total=1415, power_filled=1271, power_null=144.
  - **ПОСЛЕ 5.1b:** total=1415, power_filled=1354, power_null=61.
  - Δ = +83 заполнения, точно совпадает с принятыми импортом.
- **Honest-null breakdown по причинам** (60 items на проде):
  - 21 Gembird NPA-AC*/NPA-DC* + 1 BURO BUM-* — адаптеры (защитный
    слой 1).
  - 22 Ginzzu — `ginzzu.com` офлайн, datasheet на whitelist
    недоступен (защитный слой 3, без WebSearch).
  - 4 POWERMAN — `powerman-pc.ru` ECONNREFUSED.
  - 6 PcCooler P5-YK/YS/YN/F — `pccooler.com.cn` (whitelist) не
    индексируется в поиске; pccooler.com без .cn не в whitelist.
  - 5 Deepcool PS650G/PS750G/PS850G + WH — модель не найдена на
    whitelist-доменах (вероятная опечатка PS↔PQ).
  - 2 Thermaltake TH240 V2 Ultra — система водяного охлаждения, не
    PSU (защитный слой 2).
  - 0 EOL — все остальные модели находятся в активных каталогах.

### Этап 11.6.2.6.0b — Действия по итогам аудита storages ✅

Закрывает 4 класса проблем, обнаруженных аудитом 6.0a в категории
`storages` (диагностика — `scripts/_storage_audit.py`). Подготовка
перед AI-обогащением 11.6.2.6.1 (заполнение NULL по `interface`,
`form_factor`, `storage_type`, `capacity_gb`).

- **Детектор `is_likely_non_storage`** в
  [`shared/component_filters.py`](../shared/component_filters.py).
  Узкий regex по фактическому мусору («крепления для (твердотельного
  диска|HDD|SSD)», «переходник/адаптер/рамка/кронштейн 2.5» БЕЗ
  контекста GB/ГБ, конверсия 2.5"→3.5") + профилактически card-reader
  / кардридер / USB-hub / USB-концентратор. Защитные слои:
  `capacity_gb≥32`, непустой `storage_type`, форм-факторные маркеры
  NVMe / M.2 / 2280 / mSATA / U.2 в имени. Слова «SSD»/«HDD» намеренно
  НЕ включены в защиту — они появляются в самих триггер-фразах.
  **26 юнит-тестов** в
  [`tests/test_shared/test_non_storage_detector.py`](../tests/test_shared/test_non_storage_detector.py)
  (положительные id 782 / 1133 + синтетика card-reader/USB-hub;
  отрицательные — Samsung 980, WD Blue, Kingston A2000, Crucial MX,
  ExeGate Next/NextPro+, Toshiba MQ, Netac N600S, Transcend mSATA).
- **Upstream-классификатор в
  [`orchestrator.py::_create_skeleton`](../app/services/price_loaders/orchestrator.py)**.
  При `table == "storages"` детектор вызывается на стадии создания
  скелета: рамка / card-reader / USB-hub из новых прайсов скрывается
  сразу, AI-обогащение 6.1 не тратит тулколлы.
- **`scripts/reclassify_storage_misclassified.py`** —
  идемпотентный скрипт для разовой чистки уже существующих storages
  (по образцу `reclassify_psu_misclassified.py`). Один audit-event,
  общий backup-rollback, `--dry-run` по умолчанию.
- **Миграция
  [`migrations/025_storage_misclassification.sql`](../migrations/025_storage_misclassification.sql)**.
  UPDATE `motherboards` SET `is_hidden=TRUE` для 3 строк (id 794
  ASUS E5402WVAK моноблок, id 805 ESD-S1CL enclosure, id 811 ESD-S1C
  enclosure). INSERT в storages не делается — enclosures и моноблок
  не подходят под схему storages, достаточно скрыть. Идемпотентно
  через `AND is_hidden = FALSE`.
- **`scripts/fix_storage_manufacturer.py`** — единый скрипт
  recover + normalize в одном файле (поскольку оба правят одно поле
  `storages.manufacturer` и разводить в два файла бессмысленно).
  Режимы:
  - `--recover` — для bucket `'unknown'` regex по `model +
    supplier_prices.raw_name`, 30+ паттернов с приоритетом от длинных
    к коротким (Western Digital раньше WD, KingSpec раньше KING-SPEC и
    т. п.). Не-накопители (по `is_likely_non_storage`) пропускаются —
    бренд им не нужен.
  - `--normalize` — маппинг 14 канонических форм брендов к
    каноническим (`WD`/`Western Digital` → `Western Digital`,
    `ADATA`/`A-DATA` → `A-DATA`, `Samsung Electronics` → `Samsung`,
    `SHENZHEN KINGSPEC ELECTRONICS TECHNOLOGY CO LTD` → `KingSpec`
    через prefix-match для длинных корпоративных форм, и т. д.).
  - `--apply` — запускает оба режима последовательно (recover →
    normalize) и применяет в БД. По умолчанию `--dry-run`.
- **Whitelist-расширение под storage** в
  [`schema.py::OFFICIAL_DOMAINS`](../app/services/enrichment/claude_code/schema.py):
  **+10 доменов**, верифицированы WebFetch / WebSearch:
  `crucial.com`, `samsung.com`, `transcend-info.com`, `adata.com`,
  `solidigm.com`, `silicon-power.com`, `patriotmemory.com`,
  `sandisk.com`, `synology.com`, `kioxia.com`. До 6.0b в
  storage-секции whitelist было только 5 доменов
  (`kingston.com`, `westerndigital.com`, `seagate.com`, `netac.com`,
  `apacer.com`).
- **Локальные метрики (apply)**: 1 помечено `is_hidden=TRUE`
  (`id 1099` Digma DGBRT2535; на проде их два — id 782 + id 1133),
  **212 recovered + 354 normalized** за один прогон,
  повторный — 0 + 0 (идемпотентность).
- **pytest -n auto**: **1156 passed + 2 skipped** (~59 сек), 0
  регрессий.

### Этап 11.6.2.6.1a — Подготовка AI-обогащения storage ✅

После прокладки 11.6.2.6.0b (детектор `is_likely_non_storage`,
recover/normalize manufacturer, +10 storage-доменов в whitelist,
скрытие 1 misclassified Digma + 2 Kingston SNA-BR2/35, миграции 025
и 026) — выгружаем batch'и storages с прода для финального AI-прохода.

- **Аудит NULL-распределения по 4 целевым полям storages** на проде
  (через `railway ssh -- python -` + psycopg2; `psql` в Railway-shell
  недоступен): `total_visible=1185`. NULL: `interface=96`,
  `form_factor=94`, `storage_type=8`, `capacity_gb=2`. Топ-NULL по
  брендам: `unknown` 45, ExeGate 22, Silicon Power 21, Apacer 14,
  A-DATA 12, Transcend 9, WD 7, Samsung 6, Digma 5, Patriot 4,
  Crucial 4, MSI 3, Netac 3, Kingston 2, Hikvision 1.
- **Промпт [`enrichment/prompts/storage.md`](../enrichment/prompts/storage.md)**
  — переписан с нуля по образцу `psu.md`/`case.md` (366 строк, было 60
  строк-заглушки 2.5Б):
  - 5 защитных слоёв: External/USB-SSD (валидатор не принимает USB и
    External — `interface`/`form_factor` → null с reason, `storage_type`
    и `capacity_gb` заполняются нормально), U.2/U.3/E1.S enterprise SSD
    (form_factor → null), M.2 SATA vs M.2 NVMe (явное различение —
    валидатор интерпретирует PCIe-only без SATA как NVMe),
    `manufacturer="unknown"` (попытка извлечь бренд из raw_name; CBR и
    другие бренды вне whitelist → honest-null), не-storage в категории
    (после 6.0b детектор должен спрятать на upstream, но защита от
    случайных проскоков остаётся);
  - whitelist 15 storage-доменов синхронизирован с
    [`schema.py::OFFICIAL_DOMAINS`](../app/services/enrichment/claude_code/schema.py)
    (5 исходных + 10 добавленных на 11.6.2.6.0b: `crucial.com`,
    `samsung.com`, `transcend-info.com`, `adata.com`, `solidigm.com`,
    `silicon-power.com`, `patriotmemory.com`, `sandisk.com`,
    `synology.com`, `kioxia.com`) + кросс-категорийные `exegate.ru`
    (Next/NextPro/NextPro+ SSD) и `xpg.com` (XPG SX/Atom);
  - подсказки по 16 топ-NULL брендам (Kingston, WD, Seagate, Samsung
    `semiconductor.samsung.com/consumer-storage`, Crucial, Transcend
    SSD220S/MTE220S/MSA452T, A-DATA SU/Legend/SC/SD-серии, XPG, Solidigm
    P41 Plus / D7-D5 enterprise, Silicon Power A55/A60/UD/PA/PX10/PC60,
    Patriot P210/P310/P400/Viper, SanDisk Plus/Ultra/Extreme,
    Synology SAT5210/SNV3410, KIOXIA EXCERIA G2/G3, Netac, Apacer,
    ExeGate Next/NextPro);
  - явные правила нормализации значений с указанием на нормализатор
    в валидаторе (M.2 длины 2280/2230/2242/22110 → `M.2`; PCIe-only
    без SATA → `NVMe`; маркетинговая десятичная нотация для
    `capacity_gb`);
  - 3 примера input/output на реальных кейсах БД (Samsung 980 PRO M.2
    NVMe — типичный M.2 PCIe → NVMe, ExeGate Next 2.5" SATA — топ-кластер
    NULL.form_factor, A-DATA SC740 External USB-SSD — защитный слой 1
    с двойным null + reason на form_factor и interface).
- **Техдолг ENUM-расширения валидатора**: текущий `_v_storage_form_factor`
  принимает только `2.5"/3.5"/M.2/mSATA`, `_v_storage_interface` —
  только `SATA/NVMe/SAS`. Реально продаваемые форм-факторы `External`,
  `U.2`, `U.3`, `E1.S` и интерфейс `USB 3.x` пока возвращаются как null
  с reason — собираются в честный techdebt-список в `storage.md`
  (защитные слои 1 и 2). Расширение enum'а валидатора + миграция БД —
  отдельный этап 11.6.2.6.2 / 11.6.3.x по итогам.
- **Выгрузка batch'ей с прода** через
  [`scripts/enrich_export_prod.py`](../scripts/enrich_export_prod.py)
  `--category storage --batch-size 30 --force`: **6 файлов в
  enrichment/pending/storage/, 160 items**. Чуть больше ожидаемых
  ~100-110, потому что exporter выгружает строки с **любым** NULL из 4
  целевых полей (an OR-фильтр), а не только видимый «иссечённый» хвост
  (96+94+8+2 даёт пересечения; например, ExeGate Next имеет NULL только
  по form_factor, а Samsung 980 PRO — только по interface). Сэмплы
  раскладки по batch'ам подтверждают разнообразие: batch_001 — A-DATA
  External USB, batch_002 — ExeGate Next, batch_003 — KIOXIA Enterprise
  SAS, batch_004 — Silicon Power S55, batch_005 — WD M.2, batch_006 —
  MSI M.2.

### Этап 11.6.2.6.1b — AI-обогащение storage и импорт ✅

Завершающий AI-проход по 6 batch'ам / 160 items, импорт локально и на
прод. Защитные слои промпта 6.1a сработали по плану: ровно те бренды/
форм-факторы, которые валидатор не поддерживает, ушли в honest-null
(External USB / U.2 / unknown-without-whitelist).

- **Обработка batch'ей**: 6 файлов в `enrichment/done/storage/`, 160
  items. Заполнено 4 поля по правилам валидатора (`storage_type` ∈
  {SSD,HDD}, `form_factor` ∈ {2.5",3.5",M.2,mSATA}, `interface` ∈
  {SATA,NVMe,SAS}, `capacity_gb` 1..256000) с обязательным `source_url`
  с whitelist-доменов (15 storage + ExeGate + XPG).
- **Honest-null breakdown** (≈49 items со всеми null-полями + 13
  частичных null по form_factor для U.2):
  - **External USB-SSD** (защитный слой 1): 13 items
    (A-DATA SC740/SC750/SD620/SD810/SE880, Silicon Power DS72), оба
    поля `form_factor`/`interface` → null + reason «USB/External вне
    enum валидатора».
  - **U.2/U.3 enterprise SSD** (защитный слой 2): 9 items
    (Samsung PM1733, Intel/Solidigm P4510, P4610, D7-P5510, D7-P5520,
    D5-P5530, P5620): `form_factor=null` + reason «U.2 вне enum
    валидатора», `interface=NVMe` заполняется штатно (если в to_fill).
  - **AMD Radeon R5 SSD**: 13 items — Galaxy/AMD-OEM, datasheet
    отсутствует на amd.com (линейка EOL).
  - **QUMO Novation**: 12 items — оф. сайт `qumo.ru` вне whitelist.
  - **Не-storage в категории** (защитный слой 5, проскочившие через
    детектор 6.0b): 6 items (5 DDR-RAM Silicon Power/AGI/Digma + 1
    кулер Digma D-CPC95-PWM2). Все 4 поля null + reason «не storage».
  - **СЭМПЛ-позиции без производителя**: 3 items (СЭМПЛ SCY/MS/CBR,
    тестовые образцы без MPN).
  - **Hikvision** (1 item) — `hikvision.com` вне whitelist.
  - **Micron Enterprise** (2 items, 5300 PRO + 7450 PRO) —
    `micron.com` вне whitelist; Crucial-консумерская ветка не покрывает
    DC-серии.
- **Заполнено успешно** (с whitelist-источником) — ≈110 полей у ≈98
  items, ключевые семейства:
  - **22 ExeGate**: form_factor=2.5" для Next/NextPro/NextPro+ серии,
    interface=SATA/NVMe для M.2-вариантов — пересортировано по реальной
    категории на сайте: `/ssd25/` для 2.5" SATA, `/ssdm2/` для M.2
    SATA, `/ssdm2p/` для M.2 NVMe. Pro+ M.2 c MPN EX2823xx (KC2000TP)
    — NVMe; EX280464-66/71-73 (M2UV500TS) — SATA; EX280467/469/470
    (A2000TS Next) — SATA.
  - **A-DATA**: SU750 2.5" SATA.
  - **Apacer**: AS340/AS340X 2.5" SATA, AST280 M.2 SATA,
    AS2280P4/P4U/P4U Pro/P4X/Q4U/Q4L/F4 M.2 NVMe.
  - **Silicon Power**: S55/A55/A56/S56 2.5" SATA, A60/UD80/UD85/UD90/
    XS70 M.2 NVMe.
  - **Transcend**: 220S/225S/230S/370S 2.5" SATA,
    MTE300S/MTE400S M.2 NVMe.
  - **WD**: Blue SA510 2.5", Red SA500 2.5"; Black SN850 /
    Red SN700 / Blue SN570 / Green SN3000(=SN350) M.2 NVMe.
  - **Samsung**: 980/980 PRO/990 PRO M.2 NVMe; 870 EVO 2.5" SATA.
  - **Crucial**: E100/P3/P310 M.2 NVMe.
  - **Patriot**: Burst Elite 2.5"; P300/VP4100/VP4300 M.2 NVMe.
  - **MSI**: Spatium S270 2.5"; M580 M.2 NVMe.
  - **Gigabyte**: GP-GSTFS31 2.5".
  - **Netac**: N5M mSATA SATA, NV3000 RGB 2TB.
  - **Kingston**: KC600 mSATA SATA.
  - **KIOXIA**: PM7-V 2.5" SAS.
  - **Intel/Solidigm**: D3-S4520 M.2 SATA, D3-S4510 4TB 2.5" SATA.
  - **Digma**: Mega S3 M.2 NVMe (source `digma.ru/catalog/...` —
    whitelist домен, добавлен на 11.6.2.4.0 для cases).
- **Локальный импорт** через
  [`scripts/enrich_import.py`](../scripts/enrich_import.py)
  `--category storage --keep-source`: **35 items / 37 полей** принято
  (interface 22, form_factor 15); 17 null отсеяно валидатором как
  null_value; 144 «уже есть» (локальная БД уже синхронизирована с
  prod-фиксами 6.0b — recover/normalize manufacturer); 1
  unknown_component (id 1185, не в локальной БД).
- **Прод-импорт** через `railway ssh -s ConfiguratorPC2 -- python
  scripts/enrich_import.py --category storage`: **105 items / 115
  полей** принято (interface 67, form_factor 46, storage_type 1,
  capacity_gb 1); 84 null отсеяно валидатором как null_value;
  0 «уже есть»; 0 ошибок валидации. Подтверждает, что pending была
  построена точно по реальным NULL-пробелам прода (в отличие от
  локали, где БД уже была частично заполнена 6.0b-фиксами).
- **Прод-метрики БД storages** (WHERE `is_hidden = false`) через
  [`scripts/_storage_stats_prod.py`](../scripts/_storage_stats_prod.py):
  | Поле          | ДО (6.0b) | ПОСЛЕ (6.1b) | Δ    | Покрытие ПОСЛЕ |
  |---------------|----------:|-------------:|-----:|---------------:|
  | total_visible |     1185  |        1185  |   0  |          100 % |
  | interface     |     1089  |        1156  |  +67 |         97.6 % |
  | form_factor   |     1091  |        1137  |  +46 |         95.9 % |
  | storage_type  |     1177  |        1178  |   +1 |         99.4 % |
  | capacity_gb   |     1183  |        1184  |   +1 |         99.9 % |
- **Остаточные NULL после 6.1b** (см. техдолг §17/§18):
  - interface NULL = 29: ~9 AMD R5 (datasheet вне whitelist),
    ~12 QUMO (qumo.ru вне whitelist), 1 Micron 7450 PRO, 3 СЭМПЛ
    (SCY/MS/CBR), не-storage RAM/cooler с already-null current.
  - form_factor NULL = 48: 9 U.2 enterprise (validator-ENUM, §18),
    13 External USB-SSD (validator-ENUM, §18), ~13 AMD R5,
    ~9 QUMO 2.5", 1 Hikvision, ~3 СЭМПЛ.
  - storage_type NULL = 7: 6 RAM/cooler items не-storage (защитный
    слой 5) + 1 Micron Enterprise.
  - capacity_gb NULL = 1: 1 СЭМПЛ-позиция без указанной ёмкости.
- **Техдолг расширения валидатора** зафиксирован в
  [`enrichment_techdebt.md` §18](enrichment_techdebt.md): ~22 items
  honest-null исключительно из-за ограниченного enum'а валидатора
  (USB/External + U.2/U.3 form factors). Чтобы их закрыть, нужно либо
  расширить enum + переобогатить, либо помечать `is_hidden=TRUE`.

### Этап 11.6.2.7 — Финал AI-блока: чистка storage + AI motherboard + сводная статистика ✅

Замыкающий подэтап AI-блока 11.6.2.x: убирает остаточные хвосты
после 11.6.2.6.1b (5 misclassified items в storages, 1 single-row
data bug, 3 whitelist gaps) и закрывает последнюю «маленькую»
категорию по AI — motherboards (3 NULL ячейки в 2 mining-платах
AFOX). Дополнительно собирает финальную сводку покрытия по 6
категориям AI-блока на проде.

- **Расширение `is_likely_non_storage`**
  ([`shared/component_filters.py`](../shared/component_filters.py))
  на этапе 11.6.2.7: новые позитивные маркеры — `DDR3/4/5`,
  `DIMM`/`UDIMM`/`SO-DIMM`, «оперативная память», `\bкулер\b` /
  `\bcooler\b`, «вентилятор для CPU/процессора». Защитные слои
  (capacity≥32, storage_type, NVMe/M.2/2280/mSATA в имени) — без
  изменений. **+9 тестов** в
  [`tests/test_shared/test_non_storage_detector.py`](../tests/test_shared/test_non_storage_detector.py)
  (Silicon Power DDR4, AGI DDR4, Digma DDR3 SO-DIMM, Digma D-CPC95
  CPU-кулер, оперативная память Kingston, CPU-вентилятор; +
  негативный кейс на «Samsung 980 PRO with DRAM cache» — защита по
  NVMe/M.2 спасает). Всего 33 теста, все зелёные.
- **Прогон [`scripts/reclassify_storage_misclassified.py`](../scripts/reclassify_storage_misclassified.py)**:
  - Локально: dry → 5 кандидатов (4 RAM Silicon Power/AGI/Digma + 1
    кулер Digma D-CPC95) → apply, помечено `is_hidden=TRUE`.
  - На проде после redeploy 11.6.2.7 — `railway ssh -- python
    scripts/reclassify_storage_misclassified.py --dry-run` сначала,
    затем `--apply`.
- **Миграция [`027_fix_storage_data_bugs.sql`](../migrations/027_fix_storage_data_bugs.sql)**:
  точечный фикс ошибочного `interface='SAS'` у `storages.id=1059`
  (WD Red WDS100T1R0A — SATA-SSD серии Red SA500, не SAS).
  Идемпотентно через `AND interface='SAS'`. Применено локально,
  на прод приедет автоматически apply_migrations.py при redeploy.
- **Whitelist expansion** в
  [`schema.py::OFFICIAL_DOMAINS`](../app/services/enrichment/claude_code/schema.py)
  (storage-секция 11.6.2.7) — три домена, проверенные через
  WebFetch / WebSearch:
  - `qumo.ru` (verified) — раздел `/catalog/ssd/`, потребительские
    SSD QUMO Novation/Forsage/Compass.
  - `micron.com` (verified) — client/data-center/auto SSD, родительский
    бренд Crucial, datasheet'ы DC-серий 5300/7450 PRO только тут.
  - `hikvision.com` (verified) — собственные SSD под маркой Hikvision
    (D210pro, T100 Portable, E1000), datasheet'ы есть в `/content/dam/`.
  Закрывают 15 honest-null из 11.6.2.6.1b (qumo 12, micron 2,
  hikvision 1). Технологически разблокирует возможный re-run
  обогащения по этим брендам, но повторный прогон AI на этом этапе
  НЕ выполняется (объём не оправдан).
- **AI-обогащение motherboards (inline)**: на проде осталось 3
  NULL-ячейки в 2 строках (chipset_null=2, socket_null=1):
  - `id=378 AFOX AFHM65-ETH8EX` — chipset NULL, socket NULL.
  - `id=379 AFOX AFB250-BTC12EX` — chipset NULL (socket=LGA1151
    уже корректен из regex).

  Из-за малого объёма (2 платы) AI-обогащение выполнено инлайн,
  без batch-pipeline:
  - Минималистичный промпт-документ
    [`enrichment/prompts/motherboard.md`](../enrichment/prompts/motherboard.md)
    переписан по образцу `storage.md` / `psu.md`: целевые поля,
    защитные слои (mining AFOX hard-coded факты, BGA-as-no-socket,
    whitelist), нормализация значений, honest-null, чек-лист.
  - Inline-скрипт [`scripts/_motherboard_inline_enrich.py`](../scripts/_motherboard_inline_enrich.py)
    с hard-coded findings от WebSearch+WebFetch на `afox-corp.com`,
    прогоном через `validate_field('motherboard', ...)` и прямой
    записью в `motherboards` + upsert в `component_field_sources`
    с `source='claude_code'`, `source_detail='from_web_search'`.
    Запуск: локальный dry → apply, прод — `cat ... | railway ssh
    -- python - --apply` (стандартное поведение «нет файла на
    проде, передаём через stdin»).
  - Результат на проде: chipset=`HM65` для id=378 (источник
    `https://www.afox-corp.com/show-105-413-1.html`), chipset=`B250`
    для id=379 (источник близнеца AFB250-ETH12EX
    `https://www.afox-corp.com/index.php?...&catid=105&id=434` —
    серия AFB250 в номенклатуре AFOX жёстко закодирована = Intel
    B250, BTC и ETH-варианты с одним PCB и чипсетом).
  - id=378 socket остался NULL (honest-null): на оф. странице
    указано «CPU ON-BOARD, embedded Intel Celeron Sandy Bridge /
    Ivy Bridge Processor on-Board», конкретного BGA-кода в spec'е
    нет, защитный слой 2 промпта возвращает null с reason.
- **SQL motherboards до/после** (на проде, через
  [`scripts/_motherboard_null_audit.py`](../scripts/_motherboard_null_audit.py)):
  | Поле          | ДО (6.1b) | ПОСЛЕ (7) | Покрытие ПОСЛЕ |
  |---------------|----------:|----------:|---------------:|
  | total_visible |      963  |      963  |        100.0 % |
  | chipset       |      961  |      963  |        100.0 % |
  | socket        |      962  |      962  |         99.9 % |
  | memory_type   |      963  |      963  |        100.0 % |
  | has_m2_slot   |      963  |      963  |        100.0 % |
- **Сводная статистика AI-блока 11.6.2.x на проде** (через
  [`scripts/_ai_block_coverage_prod.py`](../scripts/_ai_block_coverage_prod.py),
  ключевые поля):

  | Категория     | Total visible | Поле                          | % filled |
  |---------------|--------------:|-------------------------------|---------:|
  | gpus          |          798  | tdp_watts                     |   74.4 % |
  | gpus          |          798  | video_outputs                 |   76.9 % |
  | gpus          |          798  | vram_gb                       |   98.0 % |
  | gpus          |          798  | vram_type                     |   97.9 % |
  | coolers       |         1076  | max_tdp_watts                 |   64.4 % |
  | coolers       |         1076  | supported_sockets             |   82.3 % |
  | cases         |         1946  | has_psu_included              |   95.1 % |
  | cases         |         1946  | supported_form_factors        |   91.1 % |
  | cases         |         1946  | included_psu_watts (when has) |   96.7 % |
  | psus          |         1415  | power_watts                   |   95.7 % |
  | storages      |         1185  | interface                     |   97.6 % |
  | storages      |         1185  | form_factor                   |   95.9 % |
  | storages      |         1185  | storage_type                  |   99.4 % |
  | storages      |         1185  | capacity_gb                   |   99.9 % |
  | motherboards  |          963  | chipset                       |  100.0 % |
  | motherboards  |          963  | socket                        |   99.9 % |
  | motherboards  |          963  | memory_type                   |  100.0 % |
  | motherboards  |          963  | has_m2_slot                   |  100.0 % |

### Сводный итог блока 11.6.2.x — AI-обогащение характеристик ✅

Полугодовой блок 11.6.2.x закрыл переход от единичных regex-правил
к системному AI-обогащению характеристик через Claude Code и
WebSearch/WebFetch. Архитектурно — общий orchestrator (exporter →
manual prompts → importer → validators), цепочка детекторов
мусора в `shared/component_filters.py` (case-fan, thermal-paste,
PSU-adapter, non-storage, и т. д.) с защитными слоями, и
whitelist оф. доменов в `schema.py::OFFICIAL_DOMAINS` (~70 доменов
с прохождением WebFetch-верификации). По 6 категориям выходные
покрытия (см. таблицу выше) — от 64 % (cooler max_tdp_watts, где
half-height/profile позиции принципиально отсутствуют в
datasheet'ах) до 99-100 % (motherboard chipset/socket/memory_type/
has_m2_slot, storage capacity_gb/storage_type). Остаточные NULL
зафиксированы в [`enrichment_techdebt.md`](enrichment_techdebt.md)
как known-unknowns: §17 расширение валидатора (USB/External + U.2),
§14/§15 EOL-бренды без datasheet (AMD R5 OEM, Ginzzu offline),
§14 (PowerMan серверные с поправкой названия) и т. п.

### Этап 11.7 — pytest-xdist + ускорение топ-10 медленных тестов ✅

Полный прогон тестов с этапа 11.2 занимал ~6:47 — заметно тормозил
итерации разработки. Этап ускоряет тесты до ~1:24 (×~5).

- **pytest-xdist** добавлен в `requirements.txt` и настроен в
  `pytest.ini`: `addopts = -n auto --dist=loadfile --durations=10`.
  `loadfile` (а не дефолтный `loadscheduling`) выбран потому, что в
  `tests/test_web/test_stage9a_2_2.py` есть тесты, опирающиеся на
  данные, заведённые более ранними тестами того же файла; разъезд
  по разным worker'ам ломал бы их.
- **Worker-aware тестовая БД**: `tests/conftest.py` читает
  `PYTEST_XDIST_WORKER` и для gw0/gw1/… подставляет имя БД
  `configurator_pc_test_<worker_id>`. Если БД не существует —
  создаётся на лету (CREATE DATABASE с шаблоном `template0` и
  `LC_COLLATE='C'`). Сессионная фикстура `db_engine` накатывает все
  миграции отдельно для каждого worker'а; первый прогон чуть
  медленнее (миграции применяются N раз), повторные — быстрые.
- **Глобальное ускорение bcrypt в тестах**: `tests/conftest.py`
  принудительно понижает `shared.auth._BCRYPT_ROUNDS` с 12 (~150 мс)
  до 4 (~5 мс). Это снимает основной вклад в setup-время большинства
  «медленных» тестов: фикстуры `admin_client`/`manager_client` каждый
  раз делают hash + verify, а в некоторых тестах ещё и каскад из 2-3
  пользователей. На безопасности не сказывается: rounds зашиваются
  в сам хеш, hash/verify-пара корректна на любых rounds.
- **Документация**: `docs/stack.md` — раздел «Параллельный прогон»
  с инструкциями по `-n0`/`-p no:xdist` для отладки одного теста.
- Всего после этапа — **947 passed + 2 skipped**, баг-в-баг с baseline.

### Этап 12.3 — Автозагрузка прайса Treolan через REST API + общий каркас auto_price_loads ✅

Первый подэтап блока 12.x — переход от ручной загрузки прайсов
(`/admin/price-uploads`, этап 11.2) к ежедневной автоматической
тяге через APScheduler. На 12.3 подключён один канал — Treolan REST API
(JWT Bearer, `POST /v1/Catalog/Get` со всем складом). Заодно заложен
общий каркас:

- **Таблицы** `auto_price_loads` (state по 6 поставщикам) и
  `auto_price_load_runs` (журнал каждого запуска). Миграция
  `028_auto_price_loads.sql`. Seed по 6 slug'ам, `enabled=FALSE`.
- **Сервис-слой** в `app/services/auto_price/`:
  - `base.BaseAutoFetcher` + регистр `@register_fetcher` —
    добавление нового канала = новый класс-наследник.
  - `runner.run_auto_load(slug, triggered_by)` — оркестратор:
    пишет run-row, ловит ошибки, пушит в Sentry, обновляет state.
  - `MANUAL_THROTTLE_SECONDS = 300` — защита кнопки «Запустить»
    в UI от случайного даблклика и rate-limit поставщика.
- **TreolanFetcher** в `fetchers/treolan.py`:
  - кеш JWT-токена в process memory (sub-1ч до exp);
  - fallback с `/v1/auth/token` на `/v1/auth/login`;
  - retry 3× с backoff 5/15/45 на 5xx и сетевых ошибках;
  - 401 → один сброс кеша и повтор;
  - конвертация USD→RUB через свежий `exchange_rates`.
- **Общий save-pipeline**: `orchestrator.save_price_rows()` — внутренний
  фасад, через который XLSX-loader и API-fetcher идут в одну и ту же
  pipeline (upsert supplier_prices, mapping, disappeared, price_uploads).
  Старый `load_price(filepath, ...)` не тронут — существующие тесты
  адаптеров продолжают работать.
- **APScheduler** в `portal/scheduler.py`: новый job
  `auto_price_loads_daily` cron 04:00 МСК (после daily_backup в 03:00 —
  если что-то сломает supplier_prices, есть свежий снимок). Активация
  под тем же `RUN_BACKUP_SCHEDULER`/`APP_ENV=production`.
- **UI** `/admin/auto-price-loads` (admin-only): таблица 6 поставщиков
  с переключателем «Авто», кнопкой «Запустить», статусом и описанием
  последней ошибки; журнал последних 20 запусков. Audit-actions
  `auto_price.view`/`auto_price.run`/`auto_price.toggle`.
- **Env-переменные** (Railway portal-сервис): `TREOLAN_API_LOGIN`,
  `TREOLAN_API_PASSWORD`, опционально `TREOLAN_API_BASE_URL`.

Что **отложено** для 12.1 / 12.2 / 12.4:
- IMAP-канал (OCS, Merlion-почта).
- Прямые URL-каналы (Netlab, Ресурс Медиа, Green Place).
- UI-конфиг кред в портале (сейчас — только через env).

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
| 018_audit_log.sql                               | Этап 9В.4      |
| 019_add_new_suppliers.sql                       | Этап 11.1      |
| 020_supplier_emails.sql                         | Этап 11.1.1    |
| 021_price_uploads_report_json.sql               | Этап 11.2      |
| 022_supplier_prices_raw_name.sql                | Этап 11.4      |
| 023_component_field_sources_source_detail.sql   | Этап 11.6.1    |
| 024_psu_misclassification.sql                   | Этап 11.6.2.5.0b |
| 025_storage_misclassification.sql               | Этап 11.6.2.6.0b |
| 026_storage_misclassification_kingston_bracket.sql | Этап 11.6.2.6.0b |
| 027_fix_storage_data_bugs.sql                   | Этап 11.6.2.7    |
| 028_auto_price_loads.sql                        | Этап 12.3        |
