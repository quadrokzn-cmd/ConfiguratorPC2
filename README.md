# КВАДРО-ТЕХ: сервис-конфигуратор ПК

Внутренний сервис для 4 менеджеров компании: свободным текстом описали
задачу — получили готовую сборку с проверкой совместимости и минимальной
ценой у поставщиков. Админ видит все запросы и расходы на OpenAI.

## Стек
- Python 3.10+ · FastAPI · Jinja2 · Tailwind CSS (CDN)
- PostgreSQL + SQLAlchemy (text() с параметрами)
- OpenAI API (gpt-4o-mini)

## Структура проекта
```
app/
  main.py                FastAPI, SessionMiddleware, роутеры
  auth.py                bcrypt, сессии, current_user/require_login/require_admin
  config.py              настройки из .env
  database.py            engine, SessionLocal, get_db
  routers/
    auth_router.py       /login, /logout
    main_router.py       /, /query, /query/{id}, /history
    project_router.py    /projects, /project/{id}, AJAX спецификации (этап 6.2)
    admin_router.py      /admin, /admin/users, /admin/budget, /admin/queries
  services/
    budget_guard.py      контроль дневного лимита OpenAI
    web_service.py       бизнес-логика веб-роутов
    spec_service.py      CRUD проектов и спецификации (этап 6.2)
    spec_naming.py       generate_auto_name (этап 6.2)
    web_result_view.py   обогащение компонентов specs_short/raw_specs
    configurator/        подбор сборки (этап 3)
    nlu/                 парсер запросов (этап 4)
    enrichment/          обогащение характеристик (этап 2.5)
  templates/             Jinja2 с наследованием от base.html
static/js/project.js     AJAX-клиент спецификации (этап 6.2)
migrations/              SQL-миграции 001-008
scripts/
  create_admin.py        создать пользователя admin
  query.py               CLI для NLU
  ...
tests/
  conftest.py            глобальные env (в т.ч. TEST_DATABASE_URL)
  test_web/              интеграционные тесты этапа 5 (31 тест)
  test_nlu/ test_configurator/ ...  существующие тесты
```

## Запуск локально

### 1. Зависимости
```
pip install -r requirements.txt
```

### 2. Переменные окружения (`.env`)
Скопировать `.env.example → .env` и заполнить:

| Переменная | Назначение |
|---|---|
| `DATABASE_URL` | Postgres основной БД |
| `OPENAI_API_KEY` | API-ключ OpenAI |
| `ADMIN_INITIAL_PASSWORD` | Пароль пользователя `admin` при первом создании |
| `SESSION_SECRET_KEY` | Секрет для подписи session-cookie (длинная рандомная строка) |
| `DAILY_OPENAI_BUDGET_RUB` | Дневной лимит расходов OpenAI (по умолчанию 100 ₽) |
| `TEST_DATABASE_URL` | Отдельная БД для pytest |
| `OPENAI_NLU_MODEL`, `OPENAI_SEARCH_MODEL`, … | См. `.env.example` (настройки предыдущих этапов) |

Сгенерировать `SESSION_SECRET_KEY`:
```
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 3. Миграции
Применить все миграции (для нового окружения):
```
psql -U postgres -d <DBNAME> -f migrations/001_init.sql
psql -U postgres -d <DBNAME> -f migrations/002_add_currency_and_relax_nullability.sql
psql -U postgres -d <DBNAME> -f migrations/003_widen_model_column.sql
psql -U postgres -d <DBNAME> -f migrations/004_add_component_field_sources.sql
psql -U postgres -d <DBNAME> -f migrations/005_add_source_url_to_component_field_sources.sql
psql -U postgres -d <DBNAME> -f migrations/006_add_api_usage_log.sql
psql -U postgres -d <DBNAME> -f migrations/007_web_service.sql
psql -U postgres -d <DBNAME> -f migrations/008_project_specification.sql
```
На существующей БД с данными — только новую миграцию:
```
psql -U postgres -d <DBNAME> -f migrations/008_project_specification.sql
```

### 4. Админ
```
python scripts/create_admin.py
```
Создаёт пользователя с логином `admin` и паролем из `ADMIN_INITIAL_PASSWORD`.
Скрипт идемпотентен — повторный запуск не перезаписывает. Менеджеров
админ добавляет через веб-интерфейс `/admin/users`.

### 5. Старт сервера
```
uvicorn app.main:app --reload
```
Откройте http://127.0.0.1:8000/login

## Тесты

Создать отдельную БД для тестов (один раз):
```
psql -U postgres -c "CREATE DATABASE configurator_pc_test ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0"
```

Прогнать все тесты:
```
pytest
```
или только веб-часть:
```
pytest tests/test_web/
```

`conftest.py` автоматически применяет все 8 миграций к тестовой БД и
чистит состояние перед каждым тестом.

## Страницы веб-сервиса

| URL | Назначение |
|---|---|
| `/` | Форма нового запроса. После отправки создаётся новый проект + первая конфигурация, редирект на `/project/{pid}?highlight={qid}` |
| `/projects` | Список проектов пользователя (админ видит все) с суммой спецификации по каждому |
| `/project/{id}` | Детальная страница: конфигурации с чекбоксами «в спецификацию» и полем количества; внизу — панель спецификации и кнопки-заглушки экспорта |
| `/project/{id}/new_query` | Форма добавления ещё одной конфигурации в проект |
| `/query/{id}` | Детальный просмотр одной конфигурации (кнопка «Открыть проект» ведёт в `/project/{pid}`) |
| `/history` | Плоская история всех запросов пользователя |
| `/admin/*` | Админка (бюджет, пользователи, все запросы) |

Галочка «В спецификацию» и поле количества работают через AJAX
(`/project/{id}/select`, `/deselect`, `/update_quantity`); CSRF
передаётся в заголовке `X-CSRF-Token`. Клик по галочке на `/query/{id}`
тоже попадает в спецификацию проекта.

## Этапы проекта

- Этап 1 — структура БД ✅
- Этап 2 — загрузка прайс-листов ✅
- Этап 2.5 — обогащение характеристик ✅
- Этап 3 — подбор конфигурации с проверкой совместимости ✅
- Этап 4 — NLU (свободный текст → BuildRequest) ✅
- Этап 5 — веб-сервис: авторизация, история, админка ✅
- Этап 6.1 — карточная раскладка результата ✅
- **Этап 6.2 — проекты с несколькими конфигурациями и спецификацией** ✅
- Этап 7 — экспорт (Excel / PDF / email)
- Этап 8 — финальный дизайн
