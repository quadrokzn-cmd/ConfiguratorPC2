# QuadroTech-Suite

Внутренний продукт казанской компании QuadroTech: сервис-портал
сотрудника с двумя ключевыми модулями — конфигуратор ПК + аукционы.
Подробное описание проекта, стека и структуры — в [CLAUDE.md](CLAUDE.md).

## Стек (кратко)

- Python 3.10+ · FastAPI · Jinja2 · Tailwind CSS (Node-сборка)
- PostgreSQL + SQLAlchemy 2.0 (только `text()` + параметры, без ORM-моделей)
- OpenAI API (gpt-4o-mini / gpt-4o-mini-search-preview)
- APScheduler — фоновые задачи (бэкапы, автозагрузка прайсов, курс USD/RUB,
  ingest аукционов)

После **UI-5** (2026-05-11, Путь B) проект работает на одном
FastAPI-приложении `portal/main.py` (`app.quadro.tatar`). Папка `app/`
удалена, конфигуратор переехал в `portal/routers/configurator/*` под
префикс `/configurator/`, admin-страницы — в `portal/routers/admin.py`.

Структура папок и сервисов — см. CLAUDE.md, раздел «Структура репозитория».

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
psql -U postgres -d <DBNAME> -f migrations/009_multi_supplier_and_gtin.sql
```
На существующей БД с данными — только новую миграцию:
```
psql -U postgres -d <DBNAME> -f migrations/009_multi_supplier_and_gtin.sql
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
uvicorn portal.main:app --reload --port 8081
```
Откройте http://127.0.0.1:8081/login

## Тесты

Создать отдельную БД для тестов (один раз):
```
psql -U postgres -c "CREATE DATABASE configurator_pc_test ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0"
```

Прогнать все тесты:
```
pytest
```
или только тесты портала:
```
pytest tests/test_portal/
```

`conftest.py` автоматически применяет все миграции к тестовой БД и
чистит состояние перед каждым тестом.

## Прайс-листы поставщиков (этап 7)

Система принимает прайсы шести поставщиков: OCS, Merlion, Treolan,
Netlab, Resurs Media, Green Place. Каждый со своим Excel/REST/SOAP-форматом —
парсеры разнесены по модулям пакета
`portal/services/configurator/price_loaders/`, общий раннер
(`orchestrator.py`) делает сопоставление и запись в БД одинаково для всех.

### Загрузка прайса

```
python scripts/load_price.py --file path/to/OCS_price.xlsx        --supplier ocs
python scripts/load_price.py --file path/to/Прайслист_Мерлион.xlsm --supplier merlion
python scripts/load_price.py --file path/to/23_04_2026_catalog.xlsx --supplier treolan
```
Если `--supplier` не указан, скрипт определяет его по имени файла
(OCS, Merlion/Мерлион, Treolan/catalog).

### Сопоставление товаров между поставщиками

При загрузке каждая строка прайса сопоставляется с компонентами БД:
1. по `MPN` (каталожный номер производителя → колонка `sku`);
2. если не сработало — по `GTIN` (штрихкод).

Кейс Intel CPU через Treolan: артикул там — 5-символьный S-Spec (например
`SRMBG`), а у OCS/Merlion — Order Code (`CM8071512400F`). Match по MPN
не сработает, но если у OCS-компонента заполнен `gtin` — match найдётся
по штрихкоду. Для уже загруженных 3 040 компонентов OCS это заполняется
разовым скриптом:
```
python scripts/backfill_gtin.py --file path/to/OCS_price.xlsx
```
Скрипт не трогает цены/остатки, только перечитывает колонку EAN128 и
проставляет `gtin` там, где он ещё пуст.

### Ручное сопоставление (`/databases/mapping`)

Если автосопоставление неоднозначно (несколько компонентов по тому же
MPN/GTIN) или ничего не нашло — строка попадает в
`unmapped_supplier_items`. Админ разбирает очередь на странице
`/databases/mapping` (UI-2: переехало из `/admin/mapping`):

| Действие | Что делает |
|---|---|
| Объединить с выбранным | `supplier_prices` переезжает на указанный `component_id`, скелет-дубликат удаляется, статус → `merged` |
| Это точно новый товар | Статус → `confirmed_new`, компонент остаётся отдельным |
| Разобраться потом | Без изменений |

Страница закрыта `require_admin`. Менеджеру `/databases/mapping` возвращает 403.

## Страницы веб-сервиса (после UI-5)

| URL | Назначение |
|---|---|
| `/configurator/` | Форма нового запроса. После отправки создаётся новый проект + первая конфигурация, редирект на `/configurator/project/{pid}?highlight={qid}` |
| `/configurator/projects` | Список проектов пользователя (админ видит все) |
| `/configurator/project/{id}` | Детальная страница: конфигурации с чекбоксами + спецификация + экспорт |
| `/configurator/query/{id}` | Детальный просмотр одной конфигурации |
| `/configurator/history` | Плоская история всех запросов пользователя |
| `/databases/*` | Поставщики, Комплектующие для ПК, Очередь маппинга, Прайс-листы, Автозагрузка, Справочник оргтехники |
| `/settings/*` | Пользователи, Бэкапы, Журнал действий |
| `/auctions/*` | Аукционный модуль |
| `/admin`, `/admin/budget`, `/admin/queries` | Админ-обзор конфигуратора |

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
- Этап 6.2 — проекты с несколькими конфигурациями и спецификацией ✅
- **Этап 7 — Merlion и Treolan, GTIN, ручное сопоставление** ✅
- Этап 8 — экспорт / финальный дизайн
