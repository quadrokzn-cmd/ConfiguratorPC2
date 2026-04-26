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
| Тесты             | pytest 8.x, httpx TestClient                                    |

## База данных

| Что               | Чем                                                             |
|-------------------|-----------------------------------------------------------------|
| СУБД              | PostgreSQL 16                                                   |
| psql              | `C:\Program Files\PostgreSQL\16\bin\psql.exe`                   |
| Миграции          | Чистый SQL в [`../migrations/`](../migrations/) (001–016)       |
| Тестовая БД       | `configurator_pc_test`, `conftest.py` накатывает все миграции   |

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

## Версии (на момент Этапа 9А.2.7)

- 721 passed + 2 skipped тестов
- 16 миграций
- ~2207 компонентов в БД, обогащение ~93%
