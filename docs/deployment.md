# Деплой на Railway

Документ описывает, как ConfiguratorPC2 развёртывается на Railway
(этап 10 roadmap-а). Цель: после `git push` сервис поднимается без
ручных шагов.

## Обзор

- **Платформа**: [Railway](https://railway.com).
- **Поддомен**: `config.quadro.tatar` (CNAME на Railway-инстанс).
- **БД**: PostgreSQL 16 как Railway plugin (Add Database → PostgreSQL).
  Подключение проксируется через переменную `DATABASE_URL`, которую
  Railway проставляет автоматически.
- **Билдер**: `Dockerfile` в корне репо (см. `railway.json`). От
  Nixpacks отказались — он автодетектил Node по `package.json` и
  конфликтовал с явно прописанным `nodejs_18` (дубль `bin/npx`,
  exit 100). Подробнее — раздел «Сборка через Dockerfile».

Архитектурный прицел: на Railway один сервис-конфигуратор, но cookie
и SECRET_KEY уже учитывают будущую платформу `app.quadro.tatar`
(этап 9Б) — `APP_COOKIE_DOMAIN=.quadro.tatar` шарит сессию между
поддоменами.

## Переменные окружения

Все переменные настраиваются в Railway → Service → Variables. Реальные
значения см. в 1Password / закрытом канале команды; шаблон с дефолтами —
[`.env.example`](../.env.example) в корне репо.

### Обязательные на production

| Переменная | Описание |
|---|---|
| `APP_ENV` | Должно быть `production` — включает secure cookies и обязательную проверку секрета. |
| `APP_SECRET_KEY` | Подпись сессионных cookie. Сгенерировать: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. На production без него сервис падает на старте. |
| `DATABASE_URL` | Выдаёт плагин Postgres. Формат `postgresql://user:pass@host:port/db`. |
| `OPENAI_API_KEY` | Ключ от OpenAI. Без него сервис стартует, но NLU/enrichment не работают. |

### Желательные

| Переменная | Описание |
|---|---|
| `APP_COOKIE_DOMAIN` | На Railway: `.quadro.tatar` (с точкой). Шарит сессию между `config.quadro.tatar` и `app.quadro.tatar`. На локалке оставить пусто. |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Если оба заданы — `scripts/bootstrap_admin.py` создаст администратора при первом старте, если его ещё нет в БД. Не трогает уже существующего пользователя. |
| `RUN_SCHEDULER` | `1` на инстансе, где должен крутиться APScheduler (обновление курса ЦБ 5 раз в день). На репликах оставлять `0`/пусто. На сервисе портала всегда оставлять пустым — scheduler нужен только конфигуратору. |
| `SMTP_*` | Параметры SMTP для отправки писем поставщикам (этап 8.3). Без `SMTP_APP_PASSWORD` отправка падает, остальные функции работают. |
| `DAILY_OPENAI_BUDGET_RUB` | Дневной лимит расходов OpenAI. По умолчанию 100 ₽. |

### Этап 9В.2: Backblaze B2 для бекапов БД

Эти 4 переменные нужны **обоим сервисам** (портал создаёт бекапы, но
конфигуратор тоже импортирует `app.config`, который проходит через
тот же набор env). Application Key Backblaze ограничен Read+Write
только на единственный бакет `quadro-tech-db-backups`.

| Переменная | Значение |
|---|---|
| `B2_ENDPOINT` | `https://s3.us-east-005.backblazeb2.com` |
| `B2_BUCKET` | `quadro-tech-db-backups` |
| `B2_KEY_ID` | (Application Key ID из Backblaze UI) |
| `B2_APPLICATION_KEY` | (Application Key из Backblaze UI) |

Расписание ежедневного бекапа — 03:00 МСК, реализация в
`portal/scheduler.py`. Активируется автоматически при `APP_ENV=production`,
либо явно через `RUN_BACKUP_SCHEDULER=1` (для dev-окружения).
Подробности — [disaster_recovery.md](disaster_recovery.md).

### Этап 9Б.1: межсервисные ссылки конфигуратор ↔ портал

Эти переменные нужны обоим сервисам — и конфигуратору, и порталу.
На локалке достаточно дефолтов; в production выставляются вручную
в Railway → Service → Variables (см. ниже «Сервис портала»).

| Переменная | Локально | Production |
|---|---|---|
| `PORTAL_URL` | `http://localhost:8081` | `https://app.quadro.tatar` |
| `CONFIGURATOR_URL` | `http://localhost:8080` | `https://config.quadro.tatar` |
| `ALLOWED_REDIRECT_HOSTS` | `localhost:8080,localhost:8081` | `config.quadro.tatar,app.quadro.tatar` |

`PORTAL_URL` — куда конфигуратор редиректит неавторизованных
(`${PORTAL_URL}/login?next=<encoded URL>`). `CONFIGURATOR_URL` —
куда портал ссылается с плитки «Конфигуратор ПК».
`ALLOWED_REDIRECT_HOSTS` — whitelist для безопасного post-login
redirect: значения сравниваются с `netloc` URL (то есть `host:port`).
Если URL ?next=... указывает на хост вне списка, портал отбрасывает
его и отправляет пользователя на `/`. Это защита от open redirect.

### Вспомогательные / по умолчанию

| Переменная | Дефолт | Описание |
|---|---|---|
| `APP_PORT` | (Railway → `$PORT`) | Локально не обязателен. |
| `OPENAI_SEARCH_MODEL` | `gpt-4o-mini-search-preview` | Модель для enrichment. |
| `OPENAI_NLU_MODEL` | `gpt-4o-mini` | Модель для разбора запросов. |
| `OPENAI_ENRICH_AUTO_LIMIT` | `20` | Сколько SKU обогащаем без подтверждения. |
| `OPENAI_ENRICH_MAX` | `200` | Жёсткий потолок enrichment. |
| `OPENAI_ENRICH_COST_PER_CALL_USD` | `0.0275` | Учётная стоимость одного запроса. |
| `OPENAI_ENRICH_AUTO_HOOK` | `false` | Авто-обогащение после price_loader. |
| `OPENAI_ENRICH_USD_RUB_FALLBACK` | `95.0` | Резервный курс. |
| `ADMIN_INITIAL_PASSWORD` | пусто | Legacy для `scripts/create_admin.py`, на Railway не нужен. |

Полный список с расширенными комментариями — в [`.env.example`](../.env.example).

## Сборка через Dockerfile

### Почему не Nixpacks

Первая попытка использовала Nixpacks (этапы 10.1 / 10.1.1). Проект
гибридный: Python (FastAPI) + Node (Tailwind для разработки). Nixpacks
автоопределял Node по `package.json` и игнорировал Python — pip не
ставился, билд падал с exit 127. Явное `providers = ["python", "node"]`
+ `nixPkgs = ["python311", "nodejs_18", ...]` приводило к новому
конфликту: Nixpacks-автодетект и наш `nodejs_18` боролись за один
и тот же `bin/npx` в Nix-профиле, exit 100.

В этапе 10.1.2 перешли на `Dockerfile`. Это даёт полный контроль
над окружением и убирает магию автодетекта.

### Что делает Dockerfile

1. **`FROM python:3.11-slim`** — минимальный официальный образ Python
   3.11 на Debian. Версия зафиксирована (не `latest`, не `3.12`),
   чтобы совпадать с локальной разработкой.
2. **ENV-переменные**: `PYTHONDONTWRITEBYTECODE`, `PYTHONUNBUFFERED`,
   `PIP_NO_CACHE_DIR`, `PIP_DISABLE_PIP_VERSION_CHECK` — стандартные
   санитарные настройки для контейнерного Python.
3. **`COPY requirements.txt . && RUN pip install -r requirements.txt`**
   — отдельным слоем, чтобы Docker мог его кешировать. Если
   `requirements.txt` не менялся — слой переиспользуется и `pip install`
   на ребилде не запускается.
4. **`COPY . .`** — копируем остальной код. Этот слой инвалидируется
   при любом изменении кода, поэтому идёт после установки зависимостей.
5. **`CMD`** в shell-форме (а не exec) — чтобы `${PORT}` от Railway
   раскрылся при старте. Команда: миграции → bootstrap админа → uvicorn
   с `--proxy-headers --forwarded-allow-ips='*'` (Railway терминирует
   SSL на своём прокси, без флагов FastAPI генерит http-ссылки вместо
   https в `request.url_for`).

### Почему в образе нет Node

По решению №3 в [`design-decisions.md`](design-decisions.md):
скомпилированный Tailwind-CSS **коммитится** в `static/dist/main.css`.
То есть на билде CSS уже готов, его не нужно собирать. Это позволяет
не ставить `nodejs`/`npm` в образ — экономим размер и убираем целый
класс проблем (несовпадение версий node, конфликты Nix-провайдеров).

`package.json`, `package-lock.json` и `node_modules/` нужны только
на машине разработчика для `npm run watch:css` / `npm run build:css`
перед коммитом.

### psycopg2-binary без gcc

В `requirements.txt` указан `psycopg2-binary>=2.9` — это wheel со
встроенным libpq. Поэтому в образе НЕ нужны `gcc` и `libpq-dev`.
Если когда-нибудь перейдём на чистый `psycopg2` или `psycopg`, в
Dockerfile нужно будет добавить:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*
```

## Второй сервис на Railway: портал (этап 9Б.1)

С этапа 9Б.1 в репо два Dockerfile:

- `Dockerfile` — конфигуратор (`config.quadro.tatar`, порт 8080),
  команда: `uvicorn app.main:app`.
- `Dockerfile.portal` — портал (`app.quadro.tatar`, порт 8081),
  команда: `uvicorn portal.main:app`.

**Сам Railway-сервис портала разворачивается в подэтапе 9Б.3** — здесь
описана только готовность кодовой базы, чтобы развёртывание сводилось
к указанию config-файла и переменных окружения.

### postgresql-client-16 в Dockerfile.portal (этап 9В.2)

Образ портала (в отличие от конфигуратора) содержит `pg_dump` —
требуется для бекапов БД на Backblaze B2. Версия фиксирована **16**,
чтобы совпадать с серверной версией Postgres на Railway (custom-формат
дампа от pg_dump 15 не читается pg_restore 16 без warning'ов).

В стандартном репе Debian 12 Bookworm доступен только `postgresql-client-15`,
поэтому в Dockerfile.portal подключается официальная репа
[PGDG](https://www.postgresql.org/download/linux/debian/) через
современный `signed-by` keyring (вместо устаревшего `apt-key`).

В конфигураторе pg_dump не нужен — основной `Dockerfile` остаётся
без изменений, тоньше и меньше.

### Per-service config-as-code (этап 9Б.3.1)

Railway по умолчанию читает `railway.json` из корня репо для **каждого**
сервиса, который смотрит в этот репо. У нас два сервиса с разными
Dockerfile, поэтому держим **два** config-файла и в Settings каждого
сервиса указываем нужный путь:

| Файл                  | Сервис                   | Dockerfile           | healthcheckTimeout |
|-----------------------|--------------------------|----------------------|--------------------|
| `railway.json`        | ConfiguratorPC2 (`config.quadro.tatar`) | `Dockerfile`         | 30 с               |
| `railway.portal.json` | portal (`app.quadro.tatar`)             | `Dockerfile.portal`  | 300 с              |

В Railway dashboard для каждого сервиса:
**Settings → Config-as-code → Path** = соответствующий файл из таблицы.

Почему у портала `healthcheckTimeout=300`, а у конфигуратора `30`:
конфигуратор стабильно стартует за секунды (миграции 001-016 уже
применены), а портал на холодном старте применяет миграцию 017 и
загружает дашборд-запросы — 30 секунд может не хватить, и Railway
зарестартит контейнер посреди миграции. На конфигураторе значение
`30` оставляем как есть — он его «отрабатывает» уверенно.

**Важно при создании новых сервисов в этом проекте**: если когда-то
добавится третий сервис из этого же репо — нужно сразу создать для него
свой `railway.<name>.json` и указать его путь в Settings, иначе Railway
возьмёт дефолтный `railway.json` (конфиг конфигуратора) и попытается
собрать чужой сервис из основного `Dockerfile`.

### Создание сервиса портала на Railway (план для 9Б.3)

1. В том же Railway-проекте, где живёт конфигуратор, нажать
   «New service → Deploy from GitHub repo» — указать тот же репо.
2. Settings → Config-as-code → **Path: `railway.portal.json`**
   (Dockerfile и параметры healthcheck подтянутся из этого файла).
3. Settings → Networking → выдать публичный URL, привязать домен
   `app.quadro.tatar` (CNAME на новом Railway-URL).
4. В Variables прописать:
   - `DATABASE_URL` — берём ту же ссылку, что у конфигуратора
     (Railway → Variables → Reference variable → Postgres plugin).
   - `APP_ENV=production`.
   - `APP_SECRET_KEY` — **тот же**, что у конфигуратора (cookie
     подписаны одним секретом, иначе сессия портала не будет
     валидна на конфигураторе).
   - `APP_COOKIE_DOMAIN=.quadro.tatar`.
   - `PORTAL_URL=https://app.quadro.tatar`.
   - `CONFIGURATOR_URL=https://config.quadro.tatar`.
   - `ALLOWED_REDIRECT_HOSTS=config.quadro.tatar,app.quadro.tatar`.
   - `OPENAI_API_KEY` — нужен только для импорта `app.config`
     (валидируется на старте); портал OpenAI не вызывает, но
     обязателен.
   - `RUN_SCHEDULER` — **не задавать** (scheduler нужен только
     конфигуратору, иначе курсы будут писаться дважды).
5. После деплоя на конфигуратор тоже добавить `PORTAL_URL`,
   `CONFIGURATOR_URL`, `ALLOWED_REDIRECT_HOSTS` — без них
   конфигуратор будет редиректить неавторизованных на
   `http://localhost:8081`.

### Применение миграций — общий runner

Оба Dockerfile запускают `python -m scripts.apply_migrations` перед
uvicorn. Раннер идемпотентен (журнал в таблице `schema_migrations`),
поэтому неважно, кто из сервисов стартует первым. На уже накатанной
БД (миграции 001-016 от конфигуратора) первый старт портала просто
применит 017 и больше ничего.

### Что отрезает .dockerignore

Файл `.dockerignore` исключает из контекста сборки:

- `.env` и `.env.*` — **никогда не пускаем секреты в образ**;
- `.git`, IDE-конфиги (`.vscode`, `.idea`, `.claude`), `node_modules`,
  виртуальные окружения (`.venv*`) — экономия размера и времени COPY;
- `tests`, `docs`, `business`, `design_references`, `data`,
  `visual_samples`, `logs`, `scripts/reports` — в production-образе
  не нужны;
- `*.md` (кроме `README.md`) — документация в образе не нужна;
- `.mcp.json` — локальный MCP-конфиг разработчика.

ВАЖНО: исключаем именно `scripts/reports`, **не** `scripts/` целиком —
в `scripts/` лежат `apply_migrations.py` и `bootstrap_admin.py`,
которые запускаются в `CMD`.

## Healthcheck

Эндпоинт **`GET /healthz`** (без авторизации) возвращает:

- **200** `{"status": "ok", "db": "ok"}` — обычный случай.
- **503** `{"status": "error", "db": "error"}` — упал `SELECT 1` к БД.

Railway дёргает его как liveness probe (`healthcheckPath: /healthz`).
Таймаут зависит от сервиса: конфигуратор — 30 с (`railway.json`),
портал — 300 с (`railway.portal.json`, см. раздел про per-service
config-as-code выше). Перезапуск инстанса — `ON_FAILURE` с тремя
попытками.

## Сессии и cookie

`SessionMiddleware` (`starlette.middleware.sessions`) подписывает
cookie секретом из `APP_SECRET_KEY`. На production:

- `secure=True` (cookie уходит только по https);
- `samesite=lax`;
- `domain` берётся из `APP_COOKIE_DOMAIN` (например, `.quadro.tatar`).

На dev (`APP_ENV != production`) — `secure=False`, `domain` не
проставляется (cookie остаётся на текущем хосте).

## APScheduler

Фоновый планировщик (обновление курса ЦБ 5 раз в день) стартует только
при `RUN_SCHEDULER=1`. Сейчас инстанс один — переменная `1` на нём.
При появлении реплик переменную оставляем `1` ровно на одной из них,
чтобы cron-задачи не дублировались.

## Что делать после первого деплоя

После успешного деплоя сервиса (этап 10.2):

1. Привязать домен `config.quadro.tatar` (CNAME на Railway URL).
2. Перенести dev-БД в Railway-Postgres через `pg_dump`/`pg_restore`
   (этап 10.3, см. раздел ниже).
3. Прописать секреты в Railway → Variables.
4. Проверить healthcheck в Railway-дашборде.
5. Сделать smoke-логин в UI и сабмит тестового запроса.

## Перенос данных через pg_dump / pg_restore (этап 10.3)

После того как Railway-сервис поднялся на пустой БД (накатились
миграции 001-016, `bootstrap_admin.py` создал учётку), нужно перелить
содержимое локальной dev-БД (`kvadro_tech`) в Railway. Это разовая
операция; в дальнейшем источником истины будет именно Railway-БД.

### Что и почему мы делаем

- **Дамп — `--format=custom`**: бинарный формат `pg_dump`, гибкий для
  частичного восстановления (`pg_restore --list` даёт TOC, можно
  пересобрать порядок объектов или исключить таблицы). plain-SQL дамп
  проигрывает по контролю и по объёму.
- **Восстановление — `--data-only`**: схему НЕ трогаем. На Railway
  миграции уже применены раннером, повторный `CREATE TABLE` сломает
  всё. Нужны только данные.
- **`--disable-triggers`**: льём за один проход, порядок таблиц не
  контролируем — FK-триггеры временно отключаются, чтобы вставка
  родительских и дочерних строк не зависела от порядка.
- **`--no-owner --no-acl`**: на Railway свой `postgres`-пользователь,
  команды `ALTER OWNER ... TO postgres` и `GRANT` из дампа на нём не
  имеют смысла (и часто отвергаются).
- **TRUNCATE перед заливкой, кроме `schema_migrations`**: Railway-БД
  на момент переноса уже не была абсолютно пустой (`bootstrap_admin`
  создал admin, scheduler успел записать курс ЦБ, на UI могли
  появиться 1-2 supplier-а). TRUNCATE с `RESTART IDENTITY CASCADE`
  обнуляет всё, кроме журнала миграций — журнал должен остаться
  нетронутым, иначе раннер при следующем рестарте попытается
  переприменить миграции и упадёт на существующих таблицах.
- **`reset_admin_password.py` после восстановления**: в дампе у
  `users.admin` локальный bcrypt-хеш. На production нужен другой
  пароль — скрипт делает upsert по `ADMIN_USERNAME`/`ADMIN_PASSWORD`.

### Артефакты переноса

Все промежуточные файлы лежат в `db_dumps/` (она в `.gitignore` —
никогда не коммитим, содержит реальные данные пользователей):

- `kvadro_tech_<timestamp>.dump` — сам дамп (custom format).
- `dump_toc.txt` — оглавление дампа (`pg_restore --list`).
- `snapshot_local_before.txt` / `snapshot_railway_before.txt` /
  `snapshot_railway_after.txt` — слепки счётчиков строк.
- `restore_log.txt` — verbose-вывод `pg_restore`.
- `truncate_log.txt` — лог TRUNCATE-DO-блока.

### Команды (Windows / PowerShell)

```powershell
# 1. Дамп локальной БД
$env:PGPASSWORD = "postgres"
& "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" `
    --host=localhost --port=5432 --username=postgres `
    --format=custom --no-owner --no-acl `
    --file="db_dumps\kvadro_tech_$(Get-Date -Format 'yyyyMMdd_HHmmss').dump" `
    kvadro_tech

# 2. TRUNCATE на Railway (DO-блок, исключающий schema_migrations)
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" `
    --dbname=$env:DATABASE_PUBLIC_URL `
    --file=db_dumps\truncate_railway.sql

# 3. Восстановление данных
& "C:\Program Files\PostgreSQL\16\bin\pg_restore.exe" `
    --dbname=$env:DATABASE_PUBLIC_URL `
    --data-only --no-owner --no-acl --disable-triggers --verbose `
    db_dumps\kvadro_tech_<timestamp>.dump

# 4. Сброс пароля admin
$env:DATABASE_URL = $env:DATABASE_PUBLIC_URL
$env:ADMIN_USERNAME = "admin"
$env:ADMIN_PASSWORD = "<production-пароль>"
python -m scripts.reset_admin_password
```

### Sequences

`pg_dump --format=custom` сохраняет в TOC отдельные `SEQUENCE SET`-
команды (по одной на каждую auto-increment-колонку). При
`pg_restore --data-only` они выполняются автоматически, поэтому
после заливки `last_value` у sequence-ов сразу совпадает с MAX(id) из
данных. Ручной `setval(...)` не требуется — только убедиться по
снимку, что все sequences не пустые.

### Верификация

После заливки сравниваем счётчики строк по всем таблицам с локальным
снимком — должны совпасть один в один. Дополнительно: общее число
компонентов по 8 таблицам (`cpus + motherboards + rams + gpus +
storages + psus + cases + coolers`) должно быть ~5116, число скрытых
(`is_hidden = TRUE`) — порядка 60 (корпусные вентиляторы, миграция
013).

После всего — **перезапустить сервис ConfiguratorPC2 в Railway**,
чтобы приложение перечитало sequences и убедилось, что bootstrap-admin
не попытается ничего менять (admin уже существует с ролью admin).
