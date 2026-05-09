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

   На выходе появится `scripts/preprod_seed.sql` (~10 МБ, ~24 тыс. строк
   INSERT-ов). В git **не коммитится** (см. `.gitignore`). Содержит:
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

3. Залей дамп:

   ```bash
   psql "<DATABASE_PUBLIC_URL>" -f scripts/preprod_seed.sql
   ```

   На выходе ожидание: ~10-30 секунд, длинная серия `INSERT 0 1`-строк.
   Без ошибок. Всего ~24 тыс. INSERT-ов.

4. Проверка счётчиков (должны совпасть):

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

Гарантированно выключаем auto-price-loads (двойная защита: дефолт
миграции 028 — `enabled=FALSE`, но если seed-дамп унесёт `enabled=TRUE`
с dev-БД — нам нужен явный UPDATE):

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

---

## Если что-то пошло не так

| Симптом | Куда смотреть | Частая причина |
|---|---|---|
| Healthcheck red (Railway → Deployments → red) | Logs последнего деплоя | Пропущена обязательная env-var (`APP_SECRET_KEY` на `APP_ENV=production` — обязательно). |
| `psycopg2.OperationalError: could not translate host name` | Logs | DATABASE_URL не реferences-подхвачен. Проверь: `${{ postgres-preprod.DATABASE_URL }}` (с двойными фигурными скобками и точкой). |
| После логина в `app-preprod` редирект на `config-preprod`, а тот сразу обратно — **петля редиректов** | DevTools → Application → Cookies | `APP_COOKIE_DOMAIN` отличается между сервисами. Должен быть `.quadro.tatar` в обоих. |
| `pip check` падает на сборке | Build Logs | Рассинхрон `requirements.txt` ↔ установленных пакетов (gate этапа 9d.1). Чини `requirements.txt` и пушь снова. |
| Ингест не наполняет БД через 2-4 часа | Logs `portal-preprod`, поиск по `auctions_ingest` | (а) тумблер `settings.auctions_ingest_enabled` ≠ 'true' — UPDATE-ом из шага И исправь; (б) zakupki вернул 5xx — повторит через 2 часа сам; (в) кончились retry — посмотри `cards_failed` в логе. |
| `psql -f scripts/preprod_seed.sql` упал на полпути | Сам psql-стдаут | Скорее всего, на `INSERT … ON CONFLICT` — попробуй сначала очистить таблицу: `TRUNCATE printers_mfu CASCADE;` и т.д. ВНИМАНИЕ: это уничтожит все накопленные данные, включая ингест. Делать только если pre-prod пустой. |

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
