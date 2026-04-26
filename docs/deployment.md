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
| `APP_COOKIE_DOMAIN` | На Railway: `.quadro.tatar` (с точкой). Шарит сессию с будущим `app.quadro.tatar`. На локалке оставить пусто. |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Если оба заданы — `scripts/bootstrap_admin.py` создаст администратора при первом старте, если его ещё нет в БД. Не трогает уже существующего пользователя. |
| `RUN_SCHEDULER` | `1` на инстансе, где должен крутиться APScheduler (обновление курса ЦБ 5 раз в день). На репликах оставлять `0`/пусто. |
| `SMTP_*` | Параметры SMTP для отправки писем поставщикам (этап 8.3). Без `SMTP_APP_PASSWORD` отправка падает, остальные функции работают. |
| `DAILY_OPENAI_BUDGET_RUB` | Дневной лимит расходов OpenAI. По умолчанию 100 ₽. |

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

Railway дёргает его как liveness probe (`healthcheckPath: /healthz`,
таймаут 30 секунд, см. `railway.json`). Перезапуск инстанса —
`ON_FAILURE` с тремя попытками.

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

> Этот раздел заполнится в этапе 10.2 (первый успешный деплой).
> Сейчас в коде только подготовлена почва.

Заглушка для будущих шагов:

1. Привязать домен `config.quadro.tatar` (CNAME на Railway URL).
2. Перенести dev-БД в Railway-Postgres через `pg_dump`/`pg_restore`
   (этап 10.3, вариант А из roadmap-а).
3. Прописать секреты в Railway → Variables.
4. Проверить healthcheck в Railway-дашборде.
5. Сделать smoke-логин в UI и сабмит тестового запроса.
