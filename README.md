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
    admin_router.py      /admin, /admin/users, /admin/budget, /admin/queries
  services/
    budget_guard.py      контроль дневного лимита OpenAI
    web_service.py       бизнес-логика веб-роутов
    configurator/        подбор сборки (этап 3)
    nlu/                 парсер запросов (этап 4)
    enrichment/          обогащение характеристик (этап 2.5)
  templates/             Jinja2 с наследованием от base.html
migrations/              SQL-миграции 001-007
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
```
На существующей БД с данными — только новую миграцию:
```
psql -U postgres -d <DBNAME> -f migrations/007_web_service.sql
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

`conftest.py` автоматически применяет все 7 миграций к тестовой БД и
чистит состояние перед каждым тестом.

## Этапы проекта

- Этап 1 — структура БД ✅
- Этап 2 — загрузка прайс-листов ✅
- Этап 2.5 — обогащение характеристик ✅
- Этап 3 — подбор конфигурации с проверкой совместимости ✅
- Этап 4 — NLU (свободный текст → BuildRequest) ✅
- **Этап 5 — веб-сервис: авторизация, история, админка** ✅
- Этап 6 — проекты с несколькими конфигурациями
- Этап 7 — экспорт / PDF
- Этап 8 — финальный дизайн
