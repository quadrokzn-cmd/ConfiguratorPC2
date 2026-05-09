# QuadroTech

Платформа QuadroTech — модуль «Аукционы»: ингест извещений 44-ФЗ по целевым КТРУ, матчинг с справочником оборудования, расчёт маржи и дашборд для менеджера.

Стек: FastAPI + Uvicorn, PostgreSQL 16, Jinja2 + HTMX + Alpine + Tailwind (CDN), docker-compose. Node.js не используется.

Ссылки: навигация по проекту — `CLAUDE.md`, бизнес-контекст — `.business/INDEX.md`, полный план реализации — `plans/2026-04-23-platforma-i-aukciony.md`.

---

## Первый запуск

1. Установи Docker Desktop (Windows) или docker + docker-compose (Linux). Больше ничего ставить не нужно — Python ставится внутри контейнера.
2. Скопируй переменные окружения:
   ```bash
   cp .env.example .env
   ```
   При необходимости скорректируй `BASIC_AUTH_USERS`, пароль Postgres и пр.
3. Подними стек:
   ```bash
   make up
   ```
   Команда собирает образ `app`, запускает Postgres 16 и FastAPI-сервис на `http://localhost:8000`.
4. Примени миграции:
   ```bash
   make migrate
   ```
   Скрипт применит все файлы из `migrations/*.sql` по порядку и запишет их имена в таблицу `_migrations`. Повторный запуск пропускает уже применённые миграции.
5. Проверь health-check (Basic Auth обязателен):
   ```bash
   curl -u manager:pwd1 http://localhost:8000/healthz
   ```
   Ожидаемый ответ: `OK`.

UI-страницы (требуют Basic Auth, пользователь задаётся в `.env`):
- `http://localhost:8000/auctions` — дашборд аукционов (заглушка, наполняется в Волне 3)
- `http://localhost:8000/nomenclature` — справочник (заглушка, наполняется в Волне 1А)
- `http://localhost:8000/settings` — настройки (заглушка, наполняется в Волне 3)

---

## Команды Makefile

| Команда        | Что делает |
|----------------|------------|
| `make up`      | Собирает образ и поднимает docker-compose (Postgres + app) |
| `make down`    | Останавливает и удаляет контейнеры |
| `make restart` | Перезапускает только контейнер `app` |
| `make build`   | Пересборка образа без запуска |
| `make migrate` | Применяет SQL-миграции внутри контейнера |
| `make test`    | Запускает `pytest` внутри контейнера (тесты появятся с Волны 1+) |
| `make logs`    | `tail -f` логов приложения |
| `make ps`      | Статус контейнеров |
| `make shell`   | Bash внутри контейнера `app` |
| `make psql`    | `psql` в базе `quadrotech` внутри контейнера Postgres |

---

## Как добавить нового пользователя

Пользователи Basic Auth задаются в `.env` через `BASIC_AUTH_USERS` как список `login:password`, разделённых запятой:

```
BASIC_AUTH_USERS=manager:pwd1,owner:pwd2,admin:secret3
```

После правки `.env`:

```bash
make restart
```

Настройки уведомлений пользователя (Telegram/Max chat_id, время дайджеста) хранятся в таблице `users`. Добавить получателя уведомлений можно через `make psql`:

```sql
INSERT INTO users (email, role, notify_telegram_chat_id, digest_time_msk, digest_period)
VALUES ('new.user@quadrotech.local', 'manager', 123456789, '09:00', 'yesterday');
```

---

## Как применить новую миграцию

1. Создай файл `migrations/00XX_название.sql`, где `XX` — следующий по порядку номер.
2. Используй идемпотентные DDL: `CREATE TABLE IF NOT EXISTS`, `INSERT ... ON CONFLICT DO NOTHING`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.
3. Запусти:
   ```bash
   make migrate
   ```
   Скрипт увидит новый файл, применит его и зафиксирует в `_migrations` вместе с SHA-256 содержимого.

---

## Структура репозитория

```
app/
  core/            — config, auth (Basic Auth), db engine, jinja2 templates
  shared/
    notifications/ — сервис уведомлений (наполняется в Волне 3Б)
    llm/           — budget_guard.py (заглушка, ENABLED=False)
  modules/
    auctions/
      ingest/        — скрейп zakupki.gov.ru (Волна 1Б)
      match/         — SQL-матчинг по КТРУ + атрибутам (Волна 2)
      api/           — FastAPI-роутеры (Волна 3А)
      ui/            — Jinja2-роутеры (Волна 3А)
      catalog/       — парсер DNS + страница /nomenclature (Волна 1А-β)
      price_loaders/ — адаптеры 8 дистрибьюторов (Волна 1А-α)
  templates/       — Jinja2-шаблоны
  main.py          — FastAPI app
migrations/        — 0001-0006_*.sql
scripts/migrate.py — раннер миграций
tests/             — pytest (заполняется с Волны 1+)
```

---

## Безопасность и ограничения MVP

- Basic Auth через plain-comparison (без bcrypt). Подходит только для узкого внутреннего круга за VPN/белым списком IP.
- `.env` не коммитится (см. `.gitignore`).
- `app/shared/llm/budget_guard.py` — заглушка с флагом `enabled=False`. Активация вынесена в post-MVP LLM-fallback-проект.
- Все бизнес-пороги и фильтры (маржа, НМЦК, регионы, KTRU-watchlist, каналы уведомлений) хранятся в БД и редактируются из UI — не в `.env` и не в коде.
