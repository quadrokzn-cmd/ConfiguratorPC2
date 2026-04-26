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
- **Билдер**: Nixpacks (см. `railway.json` и `nixpacks.toml`). Гибрид
  Python + Node явно описан в `nixpacks.toml` — без него Nixpacks
  по `package.json` определял бы проект только как Node и не ставил
  Python (билд падал с `pip: command not found`, exit 127).

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

## Как работает билд на Railway

Билдером выступает **Nixpacks**. Он читает `nixpacks.toml` в корне репо
и понимает, что нужны и Python, и Node.

Фазы билда:

1. **setup** — из `phases.setup` в `nixpacks.toml` ставятся пакеты:
   `python311`, `nodejs_18`, `gcc` (последний нужен `psycopg2-binary`
   и прочим C-расширениям).
2. **install** — Nixpacks автоматически:
   - видит `requirements.txt` (python-провайдер) → `pip install -r requirements.txt`;
   - видит `package.json` + `package-lock.json` (node-провайдер) → `npm ci`.
   В `nixpacks.toml` install-команды НЕ переопределены, дефолты подходят.
3. **build** — `npm run build:css` из `phases.build` (компиляция Tailwind
   в `static/dist/main.css`). На случай, если в репо забыли закоммитить
   актуальный CSS.
4. **start** — команда из `railway.json/deploy.startCommand` (см. ниже).
   В `Procfile` дублируется идентичная команда — это легаси для других
   PaaS, на Railway приоритет у `railway.json`.

В `railway.json` секция `build` намеренно минимальна (только `builder:
NIXPACKS`). Раньше там был `buildCommand: "pip install ... && npm ci
&& npm run build:css"`, но это дублировало то, что Nixpacks делает сам,
и при этом обходило фазу setup → `pip` ещё не существовал → exit 127.

## Как стартует сервис

`Procfile` и `railway.json` описывают одинаковую команду запуска:

```
python -m scripts.apply_migrations \
  && python -m scripts.bootstrap_admin \
  && uvicorn app.main:app --host 0.0.0.0 --port $PORT \
       --proxy-headers --forwarded-allow-ips='*'
```

Что происходит:

1. **`python -m scripts.apply_migrations`** — идемпотентный раннер
   plain-SQL миграций (этап 10.1). В проекте нет Alembic; миграции
   лежат как `migrations/NNN_*.sql`. Раннер заводит служебную таблицу
   `schema_migrations(filename, applied_at)` и применяет только новые
   файлы. На существующей БД (где уже есть `suppliers`) — помечает
   все текущие файлы как применённые без повторного `CREATE TABLE`.
2. **`python -m scripts.bootstrap_admin`** — создаёт пользователя
   с логином `ADMIN_USERNAME` и паролем `ADMIN_PASSWORD`, если его
   ещё нет. Идемпотентен; пароль существующего пользователя НЕ
   меняет. Если переменные пусты — молча выходит.
3. **`uvicorn app.main:app`** — основной HTTP-сервер.
   Флаги `--proxy-headers --forwarded-allow-ips='*'` нужны потому,
   что Railway терминирует SSL на своём прокси: без них FastAPI не
   видит правильный `scheme`/`host` и формирует http-ссылки вместо
   https в `request.url_for(...)`.

## Healthcheck

Эндпоинт **`GET /healthz`** (без авторизации) возвращает:

- **200** `{"status": "ok", "db": "ok"}` — обычный случай.
- **503** `{"status": "error", "db": "error"}` — упал `SELECT 1` к БД.

Railway дёргает его как liveness probe (`healthcheckPath: /healthz`,
таймаут 30 секунд, см. `railway.json`). Перезапуск инстанса —
`ON_FAILURE` с тремя попытками.

## Статика и Tailwind

Конвенция (см. [`design-decisions.md`](design-decisions.md), решение №3)
— скомпилированный CSS **коммитится** в `static/dist/main.css`. Это
страхует прод на случай, если на билде Tailwind что-то сломается.

При этом на билде Railway мы всё равно прогоняем `npm run build:css`
(через `tailwindcss -i ./static/src/main.css -o ./static/dist/main.css
--minify`) в `phases.build` `nixpacks.toml` — на случай, если в репо
забыли закоммитить актуальный CSS.

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
