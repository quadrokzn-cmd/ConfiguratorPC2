# Стек технологий

## Backend

| Слой              | Что используется                                                |
|-------------------|-----------------------------------------------------------------|
| Язык              | Python 3.10+                                                    |
| Web-фреймворк     | FastAPI + Uvicorn                                               |
| ORM/SQL           | SQLAlchemy 2.0, **только `text()` с параметрами** (без ORM)     |
| Драйвер БД        | psycopg2-binary                                                 |
| Pydantic          | 2.x — модели запросов/ответов                                    |
| Авторизация       | bcrypt, itsdangerous + SessionMiddleware, python-multipart      |
| LLM               | OpenAI API, модель **gpt-4o-mini**                              |
| Контроль расходов | `services/budget_guard.py` — дневной лимит в рублях             |
| Фоновые задачи    | APScheduler (cron-расписание для курса ЦБ)                      |
| Логи/ошибки       | стандартный logging (уходит в `logs/`)                          |
| Тесты             | pytest 8.x + pytest-xdist (параллельный прогон), httpx TestClient |

## База данных

| Что               | Чем                                                             |
|-------------------|-----------------------------------------------------------------|
| СУБД              | PostgreSQL 18 (Railway, прод); локально 16+                     |
| psql              | `C:\Program Files\PostgreSQL\16\bin\psql.exe` (локально)        |
| Миграции          | Чистый SQL в [`../migrations/`](../migrations/) (001–016)       |
| Тестовая БД       | `configurator_pc_test_<worker_id>` (gw0/gw1/…), `tests/conftest.py` — единая точка: один раз на сессию pytest DROP всех таблиц + накат всех миграций; CREATE DATABASE для worker'а делается автоматически |

См. [database.md](database.md) для схемы и инвариантов.

## Frontend

| Что               | Чем                                                             |
|-------------------|-----------------------------------------------------------------|
| Шаблоны           | Jinja2 SSR, наследование от `base.html`                         |
| CSS-фреймворк     | **Tailwind CSS** (локальная сборка через npm)                   |
| Сборка            | npm + tailwindcss + postcss + autoprefixer                      |
| Шрифт             | Inter (локально, по `@font-face` из `static/fonts/inter/`)      |
| Иконки            | inline SVG в шаблонах                                           |
| JS                | Vanilla JS (нет фреймворков), AJAX через fetch                  |

Workflow CSS:

- `npm install` — один раз, поднимает dev-зависимости
  (`node_modules/` в `.gitignore`).
- `npm run build:css` — собирает `static/dist/main.css` для прода.
  **Собранный CSS коммитится** — на хостинге Node.js не нужен.
- `npm run watch:css` — пересборка при изменении шаблонов и `main.css`
  во время разработки.

Tailwind сканирует:

- `app/templates/**/*.html` — все шаблоны и макросы.
- `static/js/**/*.js` — на случай классов, формируемых строкой в JS
  (`project.js` так делает для строк спецификации).

См. [ui_design_system.md](ui_design_system.md) и [ui_components.md](ui_components.md).

## Экспорт

| Формат            | Библиотека                                                       |
|-------------------|------------------------------------------------------------------|
| Excel (.xlsx)     | openpyxl                                                          |
| Word (.docx)      | python-docx, **программная сборка таблицы с явным rFonts** на каждом run (Этап 9А.2.7) |
| Email             | стандартный `email` + SMTP (см. `services/export/email_*.py`)    |

## Внешние сервисы

| Сервис            | Что используется                                                 |
|-------------------|------------------------------------------------------------------|
| OpenAI API        | gpt-4o-mini для NLU fallback                                     |
| ЦБ РФ             | XML-эндпоинт `cbr.ru/scripts/XML_daily.asp` для курса USD→RUB    |

Курс обновляется APScheduler: 8:30, 13:00, 16:00, 17:00, 18:15 МСК
(хранится в таблице `exchange_rates`, актуальный курс — последняя строка).

## Хостинг (планируется на Этапе 10)

- Платформа: **Railway**.
- Поддомены:
  - `config.quadro.tatar` — Конфигуратор ПК (этот проект)
  - `app.quadro.tatar` — портал КВАДРО-ТЕХ (Этап 9Б, отдельный проект)
- Сборка: Dockerfile (Python 3.11, requirements.txt, собранный
  `static/dist/main.css` коммитится — Node.js на проде не нужен).
- Postgres-плагин Railway: мажор **18** (на момент апреля 2026 Railway
  поднял дефолт с 16 до 18). Образ портала ставит `postgresql-client-18`
  через PGDG — клиент `pg_dump` обязан совпадать с сервером, иначе
  «server version mismatch». Для локального восстановления прод-бекапов
  тоже нужен PostgreSQL 18; для повседневной разработки на локальной
  БД достаточно 16+.

## Локальная разработка

```bash
# Зависимости
pip install -r requirements.txt
npm install   # один раз — для tailwind

# Окружение — скопировать .env.example → .env, заполнить:
#   DATABASE_URL, OPENAI_API_KEY, ADMIN_INITIAL_PASSWORD,
#   SESSION_SECRET_KEY, DAILY_OPENAI_BUDGET_RUB, TEST_DATABASE_URL

# Миграции (по очереди)
psql -U postgres -d configurator_pc -f migrations/001_init.sql
# ... 002 — 016

# Админ
python scripts/create_admin.py

# Сервер
uvicorn app.main:app --reload
# http://127.0.0.1:8000/login

# Tailwind (отдельным процессом при разработке)
npm run watch:css
```

## Тесты

Запуск:

```bash
pytest tests/                  # весь набор — основной режим (параллельно)
pytest tests/test_web/         # только конфигуратор
pytest tests/test_portal/      # только портал
pytest tests/test_export/      # только экспорт
```

Этап 9Г.2 унифицировал DB-инфраструктуру тестов — теперь
`pytest tests/` отрабатывает целиком за один прогон, отдельные папки
больше не нужно прогонять по очереди. Источник истины для тестовой
БД (engine, миграции, db_session) — корневой `tests/conftest.py`;
локальные conftest'ы подкаталогов поднимают только свои фикстуры
(TestClient, mock_process_query, фабрики Excel и autouse-чистку
своих таблиц).

### Параллельный прогон (этап 11.7)

- `pytest tests/` по умолчанию запускает `-n auto --dist=loadfile`
  (см. `pytest.ini`). На N-ядерной машине ускорение ~×N/2…×N (в зависимости
  от пропорции CPU-bound и БД-bound тестов).
- Каждому xdist-worker'у автоматически создаётся отдельная тестовая БД
  `configurator_pc_test_<worker_id>` (gw0, gw1, …). Первый прогон
  немного медленнее (миграции применяются N раз), последующие — быстрые
  (CREATE DATABASE пропускается, если БД уже есть).
- `--dist=loadfile` (а не `loadscheduling`) гарантирует, что все тесты
  одного файла попадут на один worker'а — это нужно, потому что некоторые
  тесты `test_web/test_stage9a_2_2.py` полагаются на данные, заведённые
  более ранними тестами того же файла (таблицы компонентов
  cpus/motherboards/… не truncate'ятся между тестами одного модуля).
- Для отладки одного теста с print'ами/pdb отключайте параллельность
  через `pytest <path> -n0` — xdist остаётся подключённым, но
  работает в один поток (тогда stdout/pdb идут напрямую в терминал).
  Вариант `-p no:xdist` не подойдёт, потому что в `pytest.ini`
  закреплён `-n auto` и pytest упадёт на разборе аргументов; для
  принудительного отключения xdist нужно сбрасывать addopts:
  `pytest -o addopts= -p no:xdist <path>`.
- bcrypt в тестах принудительно работает на rounds=4 (~5 мс) вместо
  прод-rounds=12 (~150 мс) — этот патч в `tests/conftest.py` снимает
  львиную долю времени setup'а (фикстуры admin_client/manager_client
  каждый раз делают hash + verify). На безопасности теста это не
  сказывается: rounds зашиваются в сам хеш, hash/verify-пара корректна
  на любом значении.

## Версии (на момент Этапа 9Г.2)

- 897 passed + 2 skipped тестов
- 18 миграций
- ~5116 компонентов в БД (Railway), обогащение ~93%
