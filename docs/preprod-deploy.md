# Деплой pre-prod на Railway (этап 9d.2)

**Цель:** запустить отдельный environment `pre-prod` в существующем
Railway-проекте, на subdomain `config-preprod.quadro.tatar` (конфигуратор)
и `app-preprod.quadro.tatar` (портал). Отдельная Railway-Postgres БД,
seed только справочника + конфига; tenders/items/matches наполнятся
через ингест за 24-48ч.

**Зачем:** валидируем UX модуля «Аукционы» (этап 9a) на свежих лотах
с zakupki, не задевая prod (`config.quadro.tatar`/`app.quadro.tatar`).

**Жёсткие рамки** (не нарушать без явного согласования собственника):

- На pre-prod **не запускаем** auto-price-loads — иначе будет
  конкурировать с prod за IMAP-почту и SOAP-API. Защита: дефолт
  `auto_price_loads.enabled = FALSE` в миграции 028 + дополнительный
  UPDATE в шаге И ниже.
- На pre-prod **не отправляем** письма поставщикам — `SMTP_APP_PASSWORD=""`
  пустая, попытка отправить даст ошибку SMTP-аутентификации, реальные
  письма не уйдут.
- На pre-prod **не запускаем** бекапы в Backblaze B2 (`RUN_BACKUP_SCHEDULER=0`):
  pre-prod БД и так seed-копия dev-данных, дополнительная страховка не нужна.
- Auctions ingest на pre-prod — **включён** (это main цель: наполнение
  свежими лотами для UX-теста). Тумблер `settings.auctions_ingest_enabled`.
- Уведомления Telegram/Max — НЕ деплоятся (этап 9b ещё не реализован).

---

## Перед стартом (на dev-машине собственника)

1. Сгенерировать свежий seed-дамп из локальной `kvadro_tech`:

   ```bash
   bash scripts/preprod_seed_dump.sh
   ```

   На выходе появится `scripts/preprod_seed.sql` (~5 МБ, 14 COPY-блоков,
   ~24 тыс. data-строк). В git **не коммитится** (см. `.gitignore`). Содержит:
   справочник печати (628 SKU) + 8 ПК-таблиц (cpus/gpus/motherboards/
   rams/storages/cases/psus/coolers, ~9.5 тыс. SKU суммарно) + suppliers
   (6) + supplier_prices (~14 тыс. строк) + конфиг аукционов (settings,
   excluded_regions, ktru_watchlist).

2. Сгенерировать `APP_SECRET_KEY` для pre-prod (отдельный от prod):

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Сохранить в блокноте — потом скопируешь в Railway Variables (шаг Д).

3. Подобрать `ADMIN_PASSWORD` для pre-prod (отдельный от prod). Длинная
   случайная строка, минимум 16 символов.

4. Открыть Railway-проект, в котором уже работает prod, и проверить:
   environment-selector сверху (там сейчас один `production`), есть ли
   у твоей роли права создать новый environment.

---

## Шаги в Railway UI

### Шаг А. Создать environment `pre-prod`

1. Открой Railway-проект, в котором работает prod (там уже есть сервисы
   `configurator` и `portal`).
2. Сверху, рядом с переключателем environment-а (`production`), нажми
   **«+ New Environment»**.
3. Имя: `pre-prod`.
4. Source: **«Empty environment»** (НЕ «Copy from production»! Иначе
   перенесутся реальные prod-секреты, а это не то, что мы хотим).
5. Нажми **Create**. Railway переключит UI на новый environment.

### Шаг Б. Добавить Postgres-сервис

1. В environment `pre-prod` → **«+ New»** → **«Database»** → **«PostgreSQL»**.
2. Имя сервиса: `postgres-preprod` (явно отличающееся от `postgres-production`).
3. Нажми **Add**. Railway за ~30 секунд поднимет БД мажор-версии 18.
4. Открой сервис → вкладка **«Variables»**:
   - `DATABASE_URL` — внутренний URL для railway-сервисов.
   - `DATABASE_PUBLIC_URL` — внешний URL для psql с твоей dev-машины.
   Запомни оба, понадобятся на шагах Д и З.

### Шаг В. Добавить service «конфигуратор»

1. **«+ New»** → **«GitHub Repo»** → выбери репо `ConfiguratorPC2`.
2. Имя service: `configurator-preprod`.
3. Settings → **Source**:
   - Branch: `master`
   - Root Directory: `/`
   - Watch Paths: оставь пустым (любой push в master триггерит pre-prod).
4. Settings → **Build**:
   - Builder: **Dockerfile**
   - Dockerfile Path: `Dockerfile`
   - **Не указывай config-as-code** (`railway.json`) — этот файл задаёт
     параметры prod (healthcheckTimeout=30). На pre-prod healthcheckTimeout
     лучше поставить пощедрее в Settings → Deploy → 60 с.
5. Settings → **Deploy**:
   - Healthcheck Path: `/healthz`
   - Healthcheck Timeout: `60`
   - Restart Policy: `ON_FAILURE`, max retries `3`.

### Шаг Г. Добавить service «портал»

1. **«+ New»** → **«GitHub Repo»** → тот же репо `ConfiguratorPC2`.
2. Имя: `portal-preprod`.
3. Settings → Source: branch `master`, Root `/`.
4. Settings → Build:
   - Builder: **Dockerfile**
   - Dockerfile Path: `Dockerfile.portal`
5. Settings → Deploy:
   - Healthcheck Path: `/healthz`
   - Healthcheck Timeout: `300` (на холодном старте портал применяет
     миграции и тянется дольше — как и в prod).
   - Restart Policy: `ON_FAILURE`, max retries `3`.

### Шаг Д. Variables и secrets для обоих сервисов

Открой Variables каждого из `configurator-preprod` и `portal-preprod`
и добавь переменные ниже. **Все 26 общих переменных одинаковые в обоих
сервисах** — иначе сломается общий cookie `kt_session` или DATABASE_URL
будет указывать на разные БД.

#### Общие (одинаковые для обоих сервисов)

| Переменная | Значение | Комментарий |
|---|---|---|
| `APP_ENV` | `production` | Включает secure cookies, обязательную проверку секрета и scheduler-ы. |
| `APP_SECRET_KEY` | `<строка из шага «Перед стартом 2»>` | НЕ копируй из prod! |
| `APP_COOKIE_DOMAIN` | `.quadro.tatar` | С точкой — для шаринга `kt_session` между `config-preprod` и `app-preprod`. |
| `DATABASE_URL` | `${{ postgres-preprod.DATABASE_URL }}` | Railway-reference; вставится автоматически. |
| `ADMIN_USERNAME` | `admin` | |
| `ADMIN_PASSWORD` | `<строка из шага «Перед стартом 3»>` | bootstrap_admin создаст этого пользователя при первом старте. |
| `OPENAI_API_KEY` | `<тот же что в prod, или новый ключ>` | На pre-prod лимит `DAILY_OPENAI_BUDGET_RUB=100` защитит бюджет. |
| `OPENAI_SEARCH_MODEL` | `gpt-4o-mini-search-preview` | |
| `OPENAI_NLU_MODEL` | `gpt-4o-mini` | |
| `OPENAI_ENRICH_AUTO_LIMIT` | `20` | |
| `OPENAI_ENRICH_MAX` | `200` | |
| `OPENAI_ENRICH_COST_PER_CALL_USD` | `0.0275` | |
| `OPENAI_ENRICH_AUTO_HOOK` | `false` | На pre-prod не дёргаем enrichment автоматически после price_loader. |
| `OPENAI_ENRICH_USD_RUB_FALLBACK` | `95.0` | |
| `DAILY_OPENAI_BUDGET_RUB` | `100` | Защита от расхода: на pre-prod пилотный запуск, не нужен большой бюджет. |
| `PORTAL_URL` | `https://app-preprod.quadro.tatar` | Куда конфигуратор редиректит неавторизованных. |
| `CONFIGURATOR_URL` | `https://config-preprod.quadro.tatar` | Куда портал ссылается с плитки «Конфигуратор ПК». |
| `ALLOWED_REDIRECT_HOSTS` | `app-preprod.quadro.tatar,config-preprod.quadro.tatar` | Whitelist для post-login redirect. |
| `RUN_SCHEDULER` | `1` | Для конфигуратора: курс ЦБ 5 раз в день. На портал не влияет (он смотрит другой флаг). |
| `RUN_BACKUP_SCHEDULER` | `0` | НЕ запускать бекапы в Backblaze B2 на pre-prod (см. рамки выше). |
| `SMTP_HOST` | `smtp.mail.ru` | |
| `SMTP_PORT` | `465` | |
| `SMTP_USE_SSL` | `true` | |
| `SMTP_USER` | `quadro@quadro.tatar` | |
| `SMTP_APP_PASSWORD` | (оставить **пустым**) | На pre-prod НЕ слать реальные письма поставщикам. |
| `SMTP_BCC` | (оставить пустым) | |
| `SMTP_FROM_NAME` | `КВАДРО-ТЕХ pre-prod` | Чтобы при случайной отправке письма было видно, что это тестовое окружение. |
| `TREOLAN_API_LOGIN` | (оставить **пустым**) | Auto-price-loads на pre-prod выключены через БД-тумблер (шаг И), но дополнительно убираем кред — на случай если кто-то включит тумблер вручную. |
| `TREOLAN_API_PASSWORD` | (оставить **пустым**) | Аналогично. |
| `TREOLAN_API_BASE_URL` | `https://api.treolan.ru/api` | URL не секрет; оставлен ради консистентности с prod. |

#### Только для портала (portal-preprod)

| Переменная | Значение | Комментарий |
|---|---|---|
| `B2_*` | НЕ выставлять | Бекапы pre-prod выключены через `RUN_BACKUP_SCHEDULER=0`. Если случайно выставишь — миграция auto-detect сработает только на shedule, но безопаснее не давать ключи вообще. |
| `SENTRY_DSN_PORTAL` | (опционально) | Если хочешь отдельный Sentry-проект для pre-prod — создай его сейчас и впиши DSN. Иначе оставь пустым: сервис стартует с `Sentry disabled`. |
| `AUDIT_RETENTION_DAYS` | (необязательно) | Дефолт 180 дней. Для pre-prod можно сократить до 30. |

#### Только для конфигуратора (configurator-preprod)

| Переменная | Значение | Комментарий |
|---|---|---|
| `SENTRY_DSN_CONFIGURATOR` | (опционально) | Аналогично порталу. |

### Шаг Е. Custom Domains

1. `configurator-preprod` → **Settings → Networking → Custom Domain**
   → нажми **Generate Domain**, чтобы сначала получить базовый
   `*.up.railway.app` (для smoke-теста). Потом → **«Add Custom Domain»**
   → введи `config-preprod.quadro.tatar`.
2. Railway покажет **CNAME-target** вида `config-preprod.up.railway.app`.
   Запиши его.
3. Открой панель регистратора домена `quadro.tatar` (или DNS-провайдера).
   Добавь CNAME-запись:
   ```
   config-preprod  CNAME  <CNAME-target из Railway>
   ```
4. То же самое для `portal-preprod` → `app-preprod.quadro.tatar`.
5. Подожди 1-5 минут, пока DNS пропагируется. Railway сам проверит
   привязку и выдаст автоматический Let's Encrypt SSL.

### Шаг Ж. Деплой

1. После создания сервисов на шагах В/Г Railway автоматически запустит
   первый деплой каждого. Открой Deployments каждого сервиса.
2. Жди, пока статус сменится на **Active** (зелёный) и healthcheck
   пройдёт. На холодном старте у портала это ~1-3 минуты.
3. Если упало — Logs → ищи `Traceback` или `ERROR`. Самые частые
   причины:
   - Пропущена обязательная env-var (`APP_SECRET_KEY` пуст на
     `APP_ENV=production`) → `ValidationError` на старте.
   - `DATABASE_URL` не достучался до postgres-preprod → проверь
     reference variable (должна быть `${{ postgres-preprod.DATABASE_URL }}`).
   - Миграция упала → редко; CREATE TABLE IF NOT EXISTS / INSERT ON
     CONFLICT защищают от частных случаев. Если упало — открой
     `migrations/NNN_*.sql` и посмотри, что в нём специфичного.
4. Когда оба сервиса зелёные, миграции 001-034 применены, и
   `bootstrap_admin.py` создал пользователя `admin` (см. логи
   `portal-preprod`: `bootstrap_admin: создан пользователь 'admin' …`).

### Шаг З. Залить seed-дамп с dev-машины

1. Открой терминал в репо на dev-машине. Убедись, что
   `scripts/preprod_seed.sql` сгенерирован свежим запуском
   `bash scripts/preprod_seed_dump.sh` (см. «Перед стартом 1»).

2. Получи **публичный** DATABASE_URL: Railway → `postgres-preprod` →
   Variables → `DATABASE_PUBLIC_URL`. Это URL вида
   `postgres://postgres:<password>@<host>.railway.app:<port>/railway`.

   ⚠️ Важно: **внутренний** `DATABASE_URL` доступен ТОЛЬКО из
   Railway-сервисов. С твоей машины подключиться можно только к
   `DATABASE_PUBLIC_URL`.

3. **Перед заливкой обязательно очисти 15 таблиц.** Миграции 001-034
   при первом старте контейнера засевают как минимум 6 таблиц
   (`cases`, `coolers`, `ktru_watchlist`, `excluded_regions`, `settings`,
   `auto_price_loads`) собственными data-INSERT-ами. Если залить seed
   поверх — упадёт по `duplicate key`. Чистим всё, что льёт seed-дамп
   + `auto_price_loads`:

   ```bash
   psql "<DATABASE_PUBLIC_URL>" -c "TRUNCATE
       auto_price_loads, cases, coolers, cpus, excluded_regions, gpus,
       ktru_watchlist, motherboards, printers_mfu, psus, rams, settings,
       storages, suppliers, supplier_prices
       RESTART IDENTITY CASCADE;"
   ```

4. Залей дамп:

   ```bash
   psql "<DATABASE_PUBLIC_URL>" -f scripts/preprod_seed.sql
   ```

   На выходе ожидание: 30-60 секунд, серия `COPY N` + `setval`-строк.
   Без ошибок. ВАЖНО: `scripts/preprod_seed_dump.sh` использует
   COPY-формат (не `--column-inserts`) — иначе 24 тыс. отдельных
   INSERT-ов через Railway-PG прокси рвут публичную сессию за 20+ минут
   (latency ~50-100 ms на каждый). Если используешь свой psql клиент
   ≤16 на seed-дампе из pg_dump 17+ — псевдо-команды `\restrict` /
   `\unrestrict` повесят psql; убери их `sed '/^\\\\\\(un\\\\\\)\\\\?restrict/d'`
   или поставь psql 17.

5. Проверка счётчиков (должны совпасть):

   ```bash
   psql "<DATABASE_PUBLIC_URL>" -c "SELECT COUNT(*) FROM printers_mfu;"
   # ожидается: 628

   psql "<DATABASE_PUBLIC_URL>" -c "SELECT COUNT(*) FROM cpus;"
   # ожидается: 228

   psql "<DATABASE_PUBLIC_URL>" -c "SELECT COUNT(*) FROM supplier_prices;"
   # ожидается: 13953

   psql "<DATABASE_PUBLIC_URL>" -c "SELECT COUNT(*) FROM ktru_watchlist WHERE is_active=TRUE;"
   # ожидается: 2

   psql "<DATABASE_PUBLIC_URL>" -c "SELECT COUNT(*) FROM excluded_regions WHERE excluded=TRUE;"
   # ожидается: 7

   psql "<DATABASE_PUBLIC_URL>" -c "SELECT key, value FROM settings ORDER BY key;"
   # ожидается 6 строк, включая auctions_ingest_enabled='true'
   ```

### Шаг И. Контрольные SQL-апдейты после seed

Гарантированно выключаем auto-price-loads. После TRUNCATE на шаге З
таблица `auto_price_loads` обычно пустая (миграция 028 не перезаливает
данные, если запись `schema_migrations` уже есть). UPDATE на пустой
таблице вернёт `UPDATE 0` — это нормально: пустая таблица = scheduler
не найдёт slug'ов = ни одна автозагрузка не запустится. Если же
seed-дамп унесёт `enabled=TRUE` с dev-БД (например при пересборке БД
с нуля) — UPDATE отработает и вернёт `UPDATE 6`:

```bash
psql "<DATABASE_PUBLIC_URL>" <<'SQL'
-- Безусловно выключаем все auto-price-loads на pre-prod, чтобы не
-- конкурировать с prod за IMAP-почту, SOAP-API и REST.
UPDATE auto_price_loads
SET enabled = FALSE
WHERE supplier_slug IN
    ('treolan','ocs','merlion','netlab','resurs_media','green_place');

-- Проверяем тумблер ингеста аукционов (это main цель pre-prod —
-- должен быть TRUE).
SELECT key, value FROM settings WHERE key = 'auctions_ingest_enabled';
SQL
```

Если последний SELECT показал `auctions_ingest_enabled=false` — включи
вручную:

```bash
psql "<DATABASE_PUBLIC_URL>" -c \
  "UPDATE settings SET value='true' WHERE key='auctions_ingest_enabled';"
```

### Шаг К. Финальная проверка в браузере

1. Открой `https://app-preprod.quadro.tatar/login`. Должна открыться
   форма входа (без редиректа на `app.quadro.tatar`!).
2. Войди как `admin` / `<ADMIN_PASSWORD из шага «Перед стартом 3»>`.
3. После логина — главная портала. Должен быть виден виджет «Аукционы»
   (нули — лотов пока нет, ингест ещё не отработал).
4. Перейди в `/auctions` → пусто (5 секций пустые: urgent / ready /
   new_low_margin / in_work / archive).
5. Перейди в `/nomenclature` → должно быть видно 628 SKU справочника
   печатной техники с фильтрами по brand/category.
6. Перейди в `/auctions/settings` → должны быть видны 6 settings
   (margin_threshold_pct=15, nmck_min_rub=30000, max_price_per_unit_rub=300000,
   contract_reminder_days=3, deadline_alert_hours=24, auctions_ingest_enabled=true)
   + 7 регионов excluded_regions + 2 активных KTRU-зонтика.
7. Открой `https://config-preprod.quadro.tatar` → конфигуратор должен
   работать (можно создать project, выбрать комплектующие).
8. Если всё зелёно — pre-prod живёт. Через 2 часа APScheduler-job
   `auctions_ingest` сходит в zakupki впервые. Можно ускорить:
   `/auctions/settings` → кнопка **«Запустить ингест сейчас»**
   (POST на `/admin/run-ingest`, требует прав `auctions_edit_settings`,
   которые admin имеет по дефолту).
9. За 24-48 часов inbox `/auctions` наполнится живыми лотами.

### Шаг Л. Создание роли `ingest_writer` для офисного worker'а (этап 9e.1)

**Зачем:** этап 9e (production-ingest архитектура, путь A) — офисный
сервер в РФ со статическим IP запускает ingest и пишет напрямую в
Railway-PG через `DATABASE_PUBLIC_URL`. Чтобы worker не ходил под
суперюзером `postgres`, заводим отдельную ограниченную PG-роль
`ingest_writer` с минимально необходимыми правами (см. миграцию
`migrations/0035_ingest_writer_role.sql` и инвентаризацию доступов
в её шапке).

**Что роль умеет:**

- `SELECT` на `settings`, `excluded_regions`, `ktru_watchlist` — читает
  фильтры платформы при старте ingest-цикла (`load_settings()`).
- `SELECT/INSERT/UPDATE/DELETE` на `tenders`, `tender_items`, `tender_status`
  — full upsert карточек лотов (`upsert_tender()` в
  `app/services/auctions/ingest/repository.py`).
- `USAGE, SELECT` на `tender_items_id_seq` — `BIGSERIAL` nextval при
  INSERT в `tender_items`.
- `CONNECT` на БД `railway` + `USAGE` на schema `public`.

**Чего роль не умеет** (smoke-проверки подтверждают `permission denied`):
`users`, `audit_log`, `printers_mfu`, ПК-таблицы (`cpus`, `gpus`,
`motherboards`, `rams`, `storages`, `cases`, `psus`, `coolers`),
`supplier_prices`, `suppliers`, `unmapped_supplier_items`,
`auto_price_loads`, `auto_price_load_runs`, `exchange_rates`, `matches`,
`ktru_catalog`, `schema_migrations`.

**Шаг Л.1. Применить миграцию 0035 на pre-prod-БД.** Раннер
`scripts/apply_migrations.py` смотрит на `DATABASE_URL` из `.env` (dev),
для pre-prod применяем напрямую psql'ом и руками фиксируем в журнале:

```bash
URL=$(python -c "
with open('.env.local.preprod.v2', encoding='utf-8-sig') as f:
    for line in f.read().splitlines():
        s=line.strip()
        if s.startswith('DATABASE_PUBLIC_URL='):
            print(s.split('=',1)[1].strip().strip(chr(34)).strip(chr(39)))
            break")

PGSSLMODE=require psql "$URL" <<'SQL'
\i migrations/0035_ingest_writer_role.sql
INSERT INTO schema_migrations (filename)
VALUES ('0035_ingest_writer_role.sql')
ON CONFLICT DO NOTHING;
SELECT filename, applied_at
FROM schema_migrations
WHERE filename='0035_ingest_writer_role.sql';
SQL
```

Ожидаемый вывод: `DO`, серия `GRANT`, `INSERT 0 1`, одна строка с
`applied_at`. Миграция идемпотентна — повтор не ломает ничего.

**Шаг Л.2. Сгенерировать пароль и выставить его роли.** Роль создаётся
`NOLOGIN` — пароль ВНЕ git, ВНЕ репо, ВНЕ LLM-чата:

```bash
# Пишем SQL во временный файл — пароль не попадает в bash-args,
# не светится в `ps`. На Windows-psql 16 используем stdin redirection
# (`-f <path>` иногда теряет аргумент).
TMP=$(python -c "
import secrets, tempfile, os
pwd = secrets.token_urlsafe(32)
sql = f\"ALTER ROLE ingest_writer WITH LOGIN PASSWORD '{pwd}';\\n\"
fd, p = tempfile.mkstemp(suffix='.sql', prefix='ingest_pwd_')
with os.fdopen(fd, 'w', encoding='utf-8') as f: f.write(sql)
print(p)
fd2, p2 = tempfile.mkstemp(suffix='.txt', prefix='ingest_pwd_val_')
with os.fdopen(fd2, 'w', encoding='utf-8') as f: f.write(pwd)
print(p2)")
SQL_FILE=$(echo "$TMP" | head -1)
PWD_FILE=$(echo "$TMP" | tail -1)

PGSSLMODE=require psql "$URL" < "$SQL_FILE"   # ALTER ROLE
rm -f "$SQL_FILE"                              # удаляем SQL с паролем

# Формируем INGEST_WRITER_DATABASE_URL_PREPROD и кладём в .env.local.preprod.v2
BASE_URL="$URL" PWD_FILE="$PWD_FILE" python - <<'PY'
import os, re, urllib.parse
url=os.environ['BASE_URL']
with open(os.environ['PWD_FILE'], 'r', encoding='utf-8') as f:
    pwd=f.read().strip()
m=re.match(r'^postgres(?:ql)?://([^:]+):([^@]+)@([^:/]+):(\d+)/(.+?)(?:\?.*)?$', url)
_u,_p,host,port,db=m.groups(); db=db.split('?')[0]
ingest_url=f'postgresql+psycopg2://ingest_writer:{urllib.parse.quote(pwd, safe="")}@{host}:{port}/{db}?sslmode=require'
path='.env.local.preprod.v2'
with open(path, 'r', encoding='utf-8-sig', newline='') as f: txt=f.read()
lines=[l for l in txt.splitlines() if not l.strip().startswith('INGEST_WRITER_DATABASE_URL_PREPROD=')]
new='\n'.join(lines).rstrip('\n')+'\n'+f'INGEST_WRITER_DATABASE_URL_PREPROD={ingest_url}\n'
with open(path, 'w', encoding='utf-8') as f: f.write(new)
PY

rm -f "$PWD_FILE"                              # удаляем txt с паролем
```

Файл `.env.local.preprod.v2` gitignored (паттерн `.env.local.preprod*`),
пароль остаётся только в нём + в БД Railway. Если ALTER ROLE упал —
роль остаётся `NOLOGIN`, повтор безопасен.

**Шаг Л.3. Smoke-проверки под `ingest_writer`.** Используем URL из
`.env.local.preprod.v2` (предварительно конвертируем SQLAlchemy-схему
`postgresql+psycopg2://` в psql-схему `postgresql://`):

```bash
INGEST_URL=$(python -c "
with open('.env.local.preprod.v2', encoding='utf-8-sig') as f:
    for line in f.read().splitlines():
        s=line.strip()
        if s.startswith('INGEST_WRITER_DATABASE_URL_PREPROD='):
            v=s.split('=',1)[1].strip().strip(chr(34)).strip(chr(39))
            print(v.replace('postgresql+psycopg2://', 'postgresql://'))
            break")

# Positive: подключение + SELECT на свои таблицы
PGSSLMODE=require psql "$INGEST_URL" <<'SQL'
SELECT current_user, current_database();
SELECT COUNT(*) FROM settings;
SELECT COUNT(*) FROM ktru_watchlist;
SELECT COUNT(*) FROM excluded_regions;
SELECT COUNT(*) FROM (SELECT 1 FROM tenders LIMIT 1) t;
SQL

# Positive: INSERT тестовой строки + DELETE (CASCADE подчистит items/status)
PGSSLMODE=require psql "$INGEST_URL" <<'SQL'
BEGIN;
INSERT INTO tenders (reg_number, customer, customer_region, nmck_total, url)
VALUES ('TEST-9E1-SMOKE-001','Smoke Test','Регион',100.00,'http://example.invalid');
INSERT INTO tender_items (tender_id, position_num, ktru_code, name, qty, unit, nmck_per_unit)
VALUES ('TEST-9E1-SMOKE-001',1,'26.20.16.120-00000013','Тест',1,'шт',100.00);
INSERT INTO tender_status (tender_id, status, changed_by)
VALUES ('TEST-9E1-SMOKE-001','new','smoke-test');
DELETE FROM tenders WHERE reg_number='TEST-9E1-SMOKE-001';
COMMIT;
SQL

# Negative: ВСЕ должны вернуть `permission denied for table <name>`
for tbl in users audit_log printers_mfu supplier_prices matches suppliers cpus auto_price_loads; do
  echo "--- $tbl ---"
  PGSSLMODE=require psql "$INGEST_URL" -tA <<SQL
SELECT 1 FROM $tbl LIMIT 1;
SQL
done
```

Ожидаемый итог smoke-проверки на 2026-05-11 (pre-prod): все positive —
успех; все 8 negative — `ERROR: permission denied for table <name>`.

**Шаг Л.4. Использование URL в офисном worker'е.** На сервере в РФ
переменная окружения `DATABASE_URL` worker-сервиса = значение
`INGEST_WRITER_DATABASE_URL_PREPROD` из `.env.local.preprod.v2`. Pre-prod
APScheduler-job `auctions_ingest` в `portal-preprod` (Railway) при этом
остаётся включённым — он использует внутренний `DATABASE_URL` под
postgres-суперюзером, для прод-режима с офисного worker'а он будет
выключен на этапе 9e.3 (правка `portal/scheduler.py`).

**Шаг Л.5. Повтор процедуры на prod-БД (этап 9e.4.1).** Та же
последовательность с одним отличием — окружение prod, отдельный пароль,
отдельный файл секретов:

1. `.gitignore` уже содержит паттерн `.env.local.prod*` (добавлен на
   9e.4.1). Создаём пустой `.env.local.prod.v1` и через notepad
   подставляем `DATABASE_PUBLIC_URL=postgresql://postgres:<pwd>@<host>:<port>/railway`
   — берётся в Railway: prod-environment → postgres service → вкладка
   Connect (или Settings → Public Networking) → «Public URL» с уже
   резолвленными `RAILWAY_TCP_PROXY_DOMAIN` и `RAILWAY_TCP_PROXY_PORT`.
   Сырое значение из Variables не подходит — оно содержит template-
   плейсхолдеры, Railway их раскрывает только при инжекте в контейнер.

2. На prod миграция `0035_ingest_writer_role.sql` обычно уже применена
   автоматически при последнем prod-deploy через `apply_migrations.py`
   (роль появилась `NOLOGIN`, GRANT-ы выданы, запись в
   `schema_migrations` есть). Повторный прогон миграции идемпотентен —
   `DO`-блок с `IF NOT EXISTS` пропустится, GRANT-ы перевыдадутся без
   эффекта, `INSERT INTO schema_migrations ... ON CONFLICT DO NOTHING`
   ничего не вставит. На 9e.4.1 (2026-05-11) запись в
   `schema_migrations` была от `2026-05-11 12:00:56 UTC`
   (автоматический Railway-deploy), `pg_roles.ingest_writer.canlogin`
   `= False` (пароля ещё не было).

3. Генерация пароля и `ALTER ROLE` — тот же скрипт что в шаге Л.2, но
   обращается к `.env.local.prod.v1` и пишет результат под ключом
   `INGEST_WRITER_DATABASE_URL_PROD` (не `_PREPROD`). Пароли pre-prod и
   prod — РАЗНЫЕ. На 9e.4.1 операция выполнена единым python-блоком
   через `psycopg2` (никаких `subprocess`, никаких temp .sql-файлов,
   пароль остаётся только в памяти процесса и в файле .env.local.prod.v1).

4. Smoke — те же 4 SELECT + транзакционный INSERT/UPDATE/DELETE цикл +
   8 negative-проверок. Используем `psycopg2.connect(DSN_без_префикса
   postgresql+psycopg2://)`, в catch ловим `psycopg2.errors.InsufficientPrivilege`.
   На 9e.4.1 (2026-05-11): positive — `current_user='ingest_writer'`,
   `settings=6 / ktru_watchlist=10 / excluded_regions=7 / tenders count=0`
   (prod пустой, prod-ingest не работал из-за Railway-блока), INSERT в
   tenders/tender_items/tender_status + UPDATE + DELETE прошли в одной
   транзакции, leftover везде 0; negative — 8/8 `permission denied for
   table <name>`.

5. Использование DSN на офисном worker'е — отдельный мини-этап 9e.4.2
   (заменяем `INGEST_WRITER_DATABASE_URL` в `D:\AuctionsIngest\ConfiguratorPC2\.env`
   на prod-значение, выключаем APScheduler-job `auctions_ingest` в
   `portal/scheduler.py` для prod-режима). До 9e.4.2 — prod-ingest не
   запускается (роль готова, никто её ещё не использует).

### Шаг М. Запуск ingest вручную с локальной машины под `ingest_writer` (этап 9e.2)

**Зачем:** проверить, что ограниченная роль из шага Л действительно
работает на полном цикле ingest (не только на smoke-`SELECT`/`INSERT`),
а заодно — наполнить pre-prod-БД свежими лотами с zakupki без участия
APScheduler. На офисном сервере этот же CLI будет запускаться по
расписанию (этап 9e.3 — Task Scheduler/systemd).

**Что используем:** `scripts/run_auctions_ingest.py` — самостоятельный
CLI без FastAPI/APScheduler. Подхватывает .env-файл с DSN, создаёт
SQLAlchemy-движок и вызывает `run_ingest_once(engine)` один раз.

**Команда** (PowerShell, из корня репо `ConfiguratorPC2`):

```powershell
python scripts/run_auctions_ingest.py `
    --env-file .env.local.preprod.v2 `
    --db-url-env INGEST_WRITER_DATABASE_URL_PREPROD
```

или bash-аналог:

```bash
python scripts/run_auctions_ingest.py \
    --env-file .env.local.preprod.v2 \
    --db-url-env INGEST_WRITER_DATABASE_URL_PREPROD
```

**Где взять env-файл:** `.env.local.preprod.v2` лежит на dev-машине
собственника, gitignored (паттерн `.env.local.preprod*`). Сам DSN
`INGEST_WRITER_DATABASE_URL_PREPROD` сформирован в шаге Л.2 (выше).

**Опции CLI:**

- `--env-file <path>` — путь к .env-файлу (default `.env`).
- `--db-url-env <name>` — имя переменной окружения с DSN
  (default `INGEST_WRITER_DATABASE_URL` — продовое имя; для pre-prod
  передаём `INGEST_WRITER_DATABASE_URL_PREPROD`).
- `--log-level DEBUG|INFO|WARNING|ERROR` (default `INFO`).

**Ожидаемое поведение в логе:**

```
ingest CLI start: dsn-source-env=INGEST_WRITER_DATABASE_URL_PREPROD, env-file=.env.local.preprod.v2
ingest run started at 2026-05-11T...
ingest plan: N unique reg_numbers to fetch
...
ingest done: {'cards_seen': N, 'cards_parsed': K, 'inserted': X, 'updated': Y, ...}
ingest CLI done: elapsed_sec=..., stats={...}
```

Exit code: `0` при успехе, `1` при unhandled exception (например,
`permission denied for table` — это значит, что миграция 0035 что-то не
поймала, см. блок «Если что-то пошло не так»), `2` при ошибках
конфигурации (нет env-файла, пустая DSN-переменная).

**Что должно появиться в БД после прогона:**

```sql
-- под ingest_writer (DSN из того же .env.local.preprod.v2)
SELECT current_user;                                   -- ингест-роль
SELECT reg_number, updated_at FROM tenders
ORDER BY updated_at DESC LIMIT 5;                      -- свежие лоты
SELECT COUNT(*) FROM tenders;                          -- общее кол-во
```

Колонка `updated_at` обновляется в каждом `upsert_tender`, поэтому
после прогона топ-5 будет иметь timestamp ≈ времени прогона
(`updated=N` в `IngestStats` ↔ те же N записей в топе). Если zakupki не
вернул новых лотов за окно поиска (`ktru_watchlist` × дата публикации)
— `inserted=0`, но `updated_at` всё равно подскочит для уже известных
карточек; это нормально, главное — отсутствие `permission denied` в
логе CLI.

На Windows-psql 16 флаг `-c "SELECT ..."` иногда теряет аргумент (см.
Шаг Л.2 — тот же нюанс). Рабочий способ — stdin redirection: записать
SQL в `tempfile.mkstemp().sql`, вызвать `psql "$URL" -tA < file.sql`,
удалить файл.

**Опционально**: убедиться, что портал жив:

```bash
curl -s -o NUL -w "%{http_code}\n" https://app-preprod.quadro.tatar/healthz
# ожидаем: 200
```

CLI работает в обход APScheduler-job `auctions_ingest` в
`portal-preprod` — оба могут писать в `tenders` параллельно. На pre-prod
конфликта нет (upsert по `reg_number`), но если внезапно увидишь
двойные `updated` в близкие моменты времени — значит сработал и
портовый job, и CLI; это не ошибка, просто избыточная работа.

---

## Если что-то пошло не так

| Симптом | Куда смотреть | Частая причина |
|---|---|---|
| Healthcheck red (Railway → Deployments → red) | Logs последнего деплоя | Пропущена обязательная env-var (`APP_SECRET_KEY` на `APP_ENV=production` — обязательно). |
| `psycopg2.OperationalError: could not translate host name` | Logs | DATABASE_URL не реferences-подхвачен. Проверь: `${{ postgres-preprod.DATABASE_URL }}` (с двойными фигурными скобками и точкой). |
| После логина в `app-preprod` редирект на `config-preprod`, а тот сразу обратно — **петля редиректов** | DevTools → Application → Cookies | `APP_COOKIE_DOMAIN` отличается между сервисами. Должен быть `.quadro.tatar` в обоих. |
| `pip check` падает на сборке | Build Logs | Рассинхрон `requirements.txt` ↔ установленных пакетов (gate этапа 9d.1). Чини `requirements.txt` и пушь снова. |
| Ингест не наполняет БД через 2-4 часа | Logs `portal-preprod`, поиск по `auctions_ingest` | (а) тумблер `settings.auctions_ingest_enabled` ≠ 'true' — UPDATE-ом из шага И исправь; (б) zakupki вернул 5xx — повторит через 2 часа сам; (в) кончились retry — посмотри `cards_failed` в логе. |
| `psql -f scripts/preprod_seed.sql` упал на `duplicate key` | Сам psql-стдаут | Не сделал TRUNCATE из шага З.3. Сделай его и перезалей. ВНИМАНИЕ: TRUNCATE уничтожит все накопленные данные (включая ингест) — допустимо только на свежей или ещё-не-используемой pre-prod. |
| `psql -f` тихо виснет, файл лога едва растёт | `tasklist | grep psql.exe` (Windows) | seed сгенерирован pg_dump 17+ с `\restrict`/`\unrestrict`-командами, локальный psql ≤16 на них зависает. Стрипни их перед заливкой (`scripts/_strip_restrict.py`) или поставь psql 17. |
| `psql -f` рвётся посреди заливки через 10-20 мин | Лог psql, счётчики таблиц — половина пустая | seed сгенерирован с `--column-inserts` (24 тыс. round-trip'ов через прокси Railway). Убери флаг из `scripts/preprod_seed_dump.sh`, перегенерируй (получится COPY-формат, льётся 30-60 сек). |
| Bash на Windows не убивает зависший psql.exe | `taskkill //F //IM psql.exe` | Bash bridge для Windows не отслеживает Windows-процессы; зомби-psql могут параллельно конкурировать за PK при следующих попытках. Чисти tasklist'ом перед каждым ретраем. |

---

## Roll-back

Если всё пошло не так и нужно откатиться:

1. Railway UI → **правый клик по environment `pre-prod`** → **Delete environment**.
2. Подтверди удаление. Railway удалит:
   - Сервисы `configurator-preprod`, `portal-preprod`, `postgres-preprod`.
   - Все volumes (БД).
   - Domain-привязки (DNS-записи в quadro.tatar остаются — можешь
     удалить руками или оставить, они не указывают на rогда живой
     CNAME-target).

3. Roll-back полный. Prod environment не задет.

После roll-back-а можно повторить шаги А-К с самого начала — никакой
state на dev-машине не сломан.

---

## Что дальше

После 24-48ч живого ингеста:

- Открой `/auctions` на pre-prod → проверь, что лоты приходят, секции
  заполняются, фильтры/state-machine/настройки работают как ожидалось.
- Прогони UX-сценарии менеджера (просмотр лота, смена статуса,
  редактирование cost_base в `/nomenclature`).
- Найденные баги/UX-замечания — задокументируй и заведи на их основе
  следующие этапы (9b — уведомления, post-MVP — расширение функционала).

Когда pre-prod подтвердит работоспособность UX — этап 9d.2 закрыт,
можно переходить к этапу 9b или к пилоту с менеджером на prod.
