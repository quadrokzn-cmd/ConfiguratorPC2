#!/usr/bin/env bash
# =============================================================
# preprod_seed_dump.sh — генератор seed-дампа для pre-prod БД
# (этап 9d.1).
#
# Назначение:
#   После `bash scripts/preprod_seed_dump.sh` в репо появляется
#   scripts/preprod_seed.sql — `pg_dump --data-only` справочника
#   и конфига аукционов из локальной dev-БД kvadro_tech. Этот файл
#   потом заливается в Railway-Postgres pre-prod через
#   `psql "<DATABASE_PUBLIC_URL>" -f scripts/preprod_seed.sql`.
#
# Что попадает в seed (в одном файле, в этом порядке):
#   • Каталог печатной техники: printers_mfu (1 таблица).
#   • Каталог ПК-компонентов: cpus, gpus, motherboards, rams,
#     storages, cases, psus, coolers (8 таблиц).
#   • Цены: suppliers, supplier_prices.
#   • Конфиг аукционов: settings, excluded_regions, ktru_watchlist.
#
# Что НЕ попадает:
#   • users (пароли prod-окружения != pre-prod).
#   • price_uploads, auto_price_loads, auto_price_load_runs
#     (история загрузок не нужна, на pre-prod auto-loads выключены).
#   • tenders, tender_items, matches, tender_status
#     (наполнятся через ингест за 24-48ч — это main цель pre-prod).
#   • audit_log, exchange_rates, api_usage_log, projects,
#     specification_items, queries (workflow-данные, не справочник).
#
# Флаги pg_dump:
#   --data-only       — структуру БД создаёт apply_migrations.py
#                       при старте контейнера (миграции 001-034).
#   --column-inserts  — устойчивость к мелким schema-различиям
#                       (порядок колонок в INSERT-ах явный).
#   --no-owner --no-privileges — Railway-Postgres работает под
#                       другим юзером, чем локальный postgres.
#
# Идемпотентность:
#   Скрипт перезаписывает scripts/preprod_seed.sql при каждом
#   запуске. Файл сам по себе не идемпотентен (повторный psql -f
#   на ту же БД упадёт на UNIQUE-конфликтах) — это OK, заливаем
#   только один раз на свежую pre-prod БД.
#
# Запуск (Linux / Git Bash на Windows):
#   bash scripts/preprod_seed_dump.sh
# =============================================================

set -euo pipefail

# Позволяем переопределить из окружения, иначе локальные дефолты.
PG_HOST="${PGHOST:-localhost}"
PG_PORT="${PGPORT:-5432}"
PG_USER="${PGUSER:-postgres}"
PG_DB="${PGDATABASE:-kvadro_tech}"

OUT_FILE="$(dirname "$0")/preprod_seed.sql"

# Список таблиц для дампа. Порядок имеет значение для FK:
#   suppliers ← supplier_prices, settings/excluded_regions/ktru_watchlist
#   независимы. printers_mfu и ПК-таблицы независимы между собой.
TABLES=(
    # Справочник печатной техники
    "printers_mfu"
    # Справочник ПК-компонентов
    "cpus"
    "gpus"
    "motherboards"
    "rams"
    "storages"
    "cases"
    "psus"
    "coolers"
    # Цены
    "suppliers"
    "supplier_prices"
    # Конфиг аукционов
    "settings"
    "excluded_regions"
    "ktru_watchlist"
)

# Проверяем pg_dump в PATH.
if ! command -v pg_dump >/dev/null 2>&1; then
    echo "ОШИБКА: pg_dump не найден в PATH. Поставь postgresql-client или" >&2
    echo "        добавь PostgreSQL\\<ver>\\bin в PATH." >&2
    exit 1
fi

# Собираем флаги -t для каждой таблицы.
TABLE_FLAGS=()
for t in "${TABLES[@]}"; do
    TABLE_FLAGS+=("-t" "public.${t}")
done

# Заголовок файла. cat <<EOF, чтобы не плодить сотни echo-строк.
cat > "${OUT_FILE}" <<EOF
-- =============================================================
-- preprod_seed.sql — seed справочника + конфига для pre-prod БД
-- (этап 9d.1, объект — Railway-Postgres pre-prod environment).
--
-- Сгенерирован автоматически: bash scripts/preprod_seed_dump.sh
-- Дата генерации: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
-- Источник: ${PG_USER}@${PG_HOST}:${PG_PORT}/${PG_DB}
-- Таблиц: ${#TABLES[@]}
--
-- Структуру создаёт apply_migrations.py при старте контейнера
-- (миграции 001-034). Этот файл льёт ТОЛЬКО данные.
--
-- Залив (один раз на свежую pre-prod БД):
--   psql "<DATABASE_PUBLIC_URL>" -f scripts/preprod_seed.sql
-- =============================================================
EOF

# Сам дамп — append (>> ).
PGPASSWORD="${PGPASSWORD:-}" pg_dump \
    --host="${PG_HOST}" \
    --port="${PG_PORT}" \
    --username="${PG_USER}" \
    --dbname="${PG_DB}" \
    --data-only \
    --column-inserts \
    --no-owner \
    --no-privileges \
    "${TABLE_FLAGS[@]}" \
    >> "${OUT_FILE}"

# Sanity-check.
LINE_COUNT="$(wc -l < "${OUT_FILE}")"
SIZE_BYTES="$(wc -c < "${OUT_FILE}")"
SIZE_MB="$(awk "BEGIN { printf \"%.2f\", ${SIZE_BYTES}/1024/1024 }")"

echo ""
echo "=== preprod_seed.sql сгенерирован ==="
echo "Файл:    ${OUT_FILE}"
echo "Размер:  ${SIZE_MB} МБ (${SIZE_BYTES} байт)"
echo "Строк:   ${LINE_COUNT}"
echo ""
echo "Первые 20 строк (header):"
head -20 "${OUT_FILE}"
echo ""
echo "ВАЖНО: scripts/preprod_seed.sql НЕ коммитится в репо (см. .gitignore)."
