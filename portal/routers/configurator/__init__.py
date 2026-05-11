# UI-4 (Путь B, 2026-05-11): роутеры конфигуратора ПК.
#
# Префикс /configurator/. Раньше жили в app/routers/* на config.quadro.tatar,
# теперь подключаются в portal/main.py через app.include_router(...).
#
# Состав:
#   - main.py     — главная конфигуратора (/configurator/, /configurator/query, /configurator/result, /configurator/history)
#   - projects.py — проекты (/configurator/projects, /configurator/project/{id}/...)
#   - export.py   — экспорт КП (/configurator/project/{id}/export/...)
#
# RBAC: dependencies=[Depends(require_configurator_access)] на каждом роутере.
