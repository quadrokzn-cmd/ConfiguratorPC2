# Auctions staging — landing zone

Содержит код QuadroTech (бывший репо `d:\ProjectsClaudeCode\KVADRO_TEX`) на момент freeze 2026-05-08.

Этот код **не подключён** к C-PC2-приложениям — он лежит как изолированная staging-зона. Не запускай отсюда `pytest`, `uvicorn`, `scripts/`.

## Поэтапный перенос в каноничные места C-PC2

| Этап | Что переносим | Куда |
|---|---|---|
| 4 | `price_loaders/` (diff + слияние с активной C-PC2-версией) | `app/services/price_loaders/` |
| 5 | БД-миграции (новые таблицы для аукционов, перенос данных QT БД) | `migrations/`, БД `kvadro_tech` |
| 6 | `nomenclature` → `printers_mfu` (9-я таблица каталога) | новая миграция + код |
| 7 | Permissions (RBAC QT убираем, расширяем `users.permissions JSONB`) | `shared/permissions.py` + миграция |
| 8 | APScheduler ингест аукционов | `portal/scheduler.py` |
| 9 | pytest + Railway pre-prod | `tests/`, CI/CD |

## Что лежит в этой папке

- `app/` — FastAPI-приложение QT (модули `auctions/catalog/`, `auctions/ingest/`, `auctions/match/`, `auctions/price_loaders/` и др.).
- `migrations/` — 9 SQL-миграций QT (`0001_init.sql … 0009_ktru_watchlist_zontics_only.sql`).
- `scripts/` — CLI-скрипты QT: `migrate.py`, `load_price.py`, `enrich_export.py`, `enrich_import.py`, `run_matching.py`, `reparse_cards.py`, `normalize_brands.py`, `_dump_raw_html.py`, `sniff_categories.py`.
- `tests/` — pytest-тесты QT (включая фикстуры `tests/fixtures/raw_html/`).
- `enrichment/` — JSON-батчи обогащения атрибутов (pending / done / archive / prompts).
- `pyproject.toml`, `Makefile`, `docker-compose.yml`, `Dockerfile`, `.env.example`, `README.md`, `FROZEN.md` — корневые файлы QT.
- `_diff_reports/` — артефакты Этапа 3 (см. ниже).

## Артефакты Этапа 3

- `_diff_reports/price_loaders_diff_2026-05-08.md` — детальный отчёт по сравнению `price_loaders/` C-PC2 vs QT (фактологическая база для Этапа 4).
- `_diff_reports/diff_*.txt` — сырые `diff -u` по 8 общим файлам.

## Что **не** скопировано из QT

- `.git/` — у QT-репо нет git-инициализации.
- `.business/`, `plans/` — уже скопированы Этапом 2 в корень C-PC2.
- `.claude/`, `.research/`, `.pytest_cache/`, `.venv/`, `node_modules/`, `__pycache__/` — служебное и нерелевантно.
- `HELLO.md` — тестовый артефакт из QT.

## Откат

Если что-то пошло не так — `auctions_staging/` можно удалить целиком за одну команду, не затрагивая C-PC2. Бэкапы QT (БД-дамп + snapshot auto-memory + zip методологии) лежат в `.business/_backups_2026-05-08-merge/`.
