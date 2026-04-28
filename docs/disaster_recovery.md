# Disaster recovery

Что делать, если БД проекта повреждена/удалена/недоступна. Документ
живой — после первой реальной проверки восстановления (рекомендуется
раз в квартал) обновлять разделы, где обнаружились пробелы.

## Что бекапим

- **БД целиком** (PostgreSQL 16 на Railway, имя `kvadro_tech` / то, что
  отдаёт `DATABASE_URL`).
- В дамп попадают: схема (миграции 001..N), все таблицы, sequence-ы.
  pg_dump c `--format=custom --no-owner --no-acl` — данные + структура
  без owner/grant команд.
- **НЕ бекапим**:
  - `.env` и переменные окружения Railway — резервируются вручную
    (см. ниже «Где хранятся креды»);
  - артефакты сборки (`static/dist/main.css`) — лежат в git;
  - локальные прайс-листы (`data/`) — это рабочий вход, а не выход
    системы;
  - сам бакет Backblaze B2 — он и есть наш архив.

## Где хранятся бекапы

- **Сервис**: [Backblaze B2](https://www.backblaze.com/cloud-storage)
  (S3-совместимый API).
- **Бакет**: `quadro-tech-db-backups`, регион `us-east-005`,
  endpoint `https://s3.us-east-005.backblaze.com`.
- **Структура объектов**:
  - `daily/kvadro_tech_<YYYY-MM-DDTHH-MM-SS>.dump` — ежедневные;
  - `weekly/kvadro_tech_<...>.dump` — еженедельные (по воскресеньям МСК);
  - `monthly/kvadro_tech_<...>.dump` — ежемесячные (1-го числа МСК).
- **Политика хранения**: 7 daily / 4 weekly / 6 monthly (всего ~17
  файлов; на ~30 МБ дампа это около 500 МБ потолка хранилища). Лишние
  удаляются автоматически после каждого успешного бекапа в
  `backup_service.rotate_backups()`.
- **Метаданные объектов**: каждый файл имеет
  `Metadata={created-at-utc, source=kvadro-tech-portal}` — используется
  при разборе аномалий.

## Где хранятся креды

- **Railway env vars** — рабочая копия для прода. Сервисы
  ConfiguratorPC2 и portal имеют по 4 переменные:
  - `B2_ENDPOINT = https://s3.us-east-005.backblazeb2.com`
  - `B2_BUCKET = quadro-tech-db-backups`
  - `B2_KEY_ID = <secret>`
  - `B2_APPLICATION_KEY = <secret>`
- **Application Key Backblaze**: ограниченный (Read+Write на единственный
  бакет `quadro-tech-db-backups`). Если ключ утечёт — отзываем в
  Backblaze UI и генерируем новый, обновляем переменные в Railway.
- **Master-копия креденшелов** — в менеджере паролей руководителя
  компании (см. **business/recovery_contacts.md**, gitignored). Если
  Railway-аккаунт недоступен — креды берутся оттуда.
- **DATABASE_URL** — выдаёт Postgres-плагин Railway, копируется в
  переменные конфигуратора и портала. Локальная dev-БД использует свой
  URL, прописанный в `.env`.

## Расписание

- **Ежедневный бекап**: 03:00 МСК (наименьшая нагрузка на БД).
  Регистрируется APScheduler'ом в `portal/scheduler.py` при старте
  процесса портала. Job id: `daily_backup`.
- **misfire_grace_time = 3600 сек**: если контейнер был выключен в
  момент 03:00, у scheduler'а есть час, чтобы догнать пропущенный запуск.
- **max_instances = 1**: одновременных бекапов не бывает (защита от
  перекрытия при долгом дампе или ручном запуске рядом с плановым).
- **Ручной запуск** доступен админам в `/admin/backups` (кнопка
  «Создать бекап сейчас»). Запускается через `BackgroundTasks`, не
  блокирует UI.

## Как восстановить (пошагово)

### 1. Получить бекап

**Вариант А — портал жив** (типичный сценарий, нужен «откатить таблицу»):
1. Зайти под админом на `https://app.quadro.tatar/admin/backups`.
2. Скачать нужный `.dump` (свежий из daily/, либо более старый из
   weekly/monthly).

**Вариант Б — портал лежит** (catastrophic):
1. Установить [b2-cli](https://www.backblaze.com/docs/cloud-storage-command-line-tools)
   или AWS CLI.
2. Скачать через S3-совместимый API:
   ```bash
   aws --endpoint-url https://s3.us-east-005.backblazeb2.com \
       s3 cp s3://quadro-tech-db-backups/daily/kvadro_tech_<...>.dump ./
   ```
   Креды: `B2_KEY_ID` / `B2_APPLICATION_KEY` (`AWS_ACCESS_KEY_ID` /
   `AWS_SECRET_ACCESS_KEY` соответственно в env-переменных AWS CLI).
3. Альтернатива — UI Backblaze (`backblaze.com` → Buckets → Browse
   files), скачивание через браузер.

### 2. Подготовить пустую БД

**На Railway:**
1. Создать новый Postgres-плагин или восстановить существующий — UI
   Railway (Add → Database → PostgreSQL).
2. Скопировать `DATABASE_URL` из переменных нового плагина.

**Локально (для проверки целостности дампа):**
```bash
psql -U postgres -c "DROP DATABASE IF EXISTS kvadro_tech_restore_test"
psql -U postgres -c "CREATE DATABASE kvadro_tech_restore_test ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0"
```

### 3. Восстановить дамп

```bash
# Linux / macOS:
pg_restore --clean --if-exists --no-owner --no-acl --verbose \
    --dbname="$DATABASE_URL" \
    kvadro_tech_<YYYY-MM-DDTHH-MM-SS>.dump

# Windows / PowerShell:
& "C:\Program Files\PostgreSQL\16\bin\pg_restore.exe" `
    --clean --if-exists --no-owner --no-acl --verbose `
    --dbname=$env:DATABASE_URL `
    kvadro_tech_<YYYY-MM-DDTHH-MM-SS>.dump
```

`--clean --if-exists` — pg_restore сам сделает DROP перед CREATE.
Подходит для восстановления поверх «грязной» БД (после неудачной
миграции, например). Для полностью пустой БД флаги тоже безвредны.

### 4. Прописать env vars

Если поднимаем новые сервисы — заполнить в Railway все обязательные
переменные. Список — `docs/deployment.md`:
- `APP_ENV=production`
- `APP_SECRET_KEY` (тот же, что был — иначе все сессии инвалидируются)
- `DATABASE_URL` (новый плагин)
- `OPENAI_API_KEY`
- `B2_ENDPOINT`, `B2_BUCKET`, `B2_KEY_ID`, `B2_APPLICATION_KEY`
- `PORTAL_URL`, `CONFIGURATOR_URL`, `ALLOWED_REDIRECT_HOSTS`
- `APP_COOKIE_DOMAIN=.quadro.tatar`
- `RUN_SCHEDULER=1` (только конфигуратор) — обновление курса ЦБ.

### 5. Бутстрап админа (если нужен)

Если в дампе уже была таблица `users` с админом — пропускаем. Если БД
была чистая или нужен запасной админ:

```bash
ADMIN_USERNAME=admin ADMIN_PASSWORD=<temporary-pass> \
    python -m scripts.bootstrap_admin
```

Скрипт идемпотентный: создаёт пользователя, если его нет; если есть —
ничего не трогает. Сменить пароль существующему админу:
`scripts/reset_admin_password.py` (через те же env vars).

### 6. Деплой и проверка

1. Перезапустить сервисы `ConfiguratorPC2` и `portal` в Railway.
2. Открыть `https://config.quadro.tatar/healthz` —
   `{"status":"ok","db":"ok"}`.
3. Открыть `https://app.quadro.tatar/healthz` — то же.
4. Залогиниться, открыть один проект, убедиться что список конфигураций
   виден.
5. Зайти на `/admin/backups`, нажать «Создать бекап сейчас», убедиться
   что в B2 появляется свежий файл (через 1-2 минуты).

## Тестирование процедуры восстановления

**Раз в квартал** (вне рабочих часов): pg_restore последнего бекапа в
локальную тестовую БД. Цель — убедиться что бекап вообще валиден и
актуален. Без этой проверки можно полгода жить с побитыми бекапами и
узнать об этом только в момент катастрофы.

Минимальная проверка:
```bash
psql -U postgres -c "DROP DATABASE IF EXISTS kvadro_tech_dr_test"
psql -U postgres -c "CREATE DATABASE kvadro_tech_dr_test ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0"
pg_restore --no-owner --no-acl --dbname=postgresql://postgres:postgres@localhost:5432/kvadro_tech_dr_test latest.dump
psql -U postgres -d kvadro_tech_dr_test -c "SELECT count(*) FROM users; SELECT count(*) FROM projects;"
```

Если счётчики ненулевые и схема накатилась без ошибок — бекап
жизнеспособен. Запись о прохождении ставится в `docs/roadmap.md` или
журнал «business/operations/».

## Контакты на случай катастрофы

| Сторона        | Кто / Где                                                       |
|----------------|-----------------------------------------------------------------|
| Руководитель   | См. `business/recovery_contacts.md` (gitignored)                |
| Разработчик    | См. `business/recovery_contacts.md`                             |
| Reg.ru         | DNS / поддомены `*.quadro.tatar` — ЛК reg.ru                    |
| Railway        | <https://help.railway.app/> или Discord-сообщество              |
| Backblaze B2   | <https://www.backblaze.com/help> — обычно отвечают за сутки     |

Если Railway упал целиком — есть сценарий миграции на другую платформу
(Render, Fly.io). Дамп из B2 прикладывается к новой БД, env-переменные
переносятся, домены перепривязываются на новые DNS-записи.
