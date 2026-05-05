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
