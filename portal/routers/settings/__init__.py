# Пакет роутеров раздела «Настройки» портала (этап UI-3 Пути B,
# 2026-05-11). Подмодули:
#   - users      : /settings/users — список менеджеров, права, роли
#   - backups    : /settings/backups — резервные копии БД (Backblaze B2)
#   - audit_log  : /settings/audit-log — журнал действий + CSV-экспорт
# Файлы переехали из portal/routers/admin_{users,backups,audit}.py;
# логика и шаблоны не меняются — только префиксы URL.
