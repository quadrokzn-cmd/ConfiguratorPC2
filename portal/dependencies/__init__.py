# UI-4 (Путь B, 2026-05-11): FastAPI Depends-зависимости для портала.
#
# Здесь живут scoped-проверки доступа, которые применяются точечно к
# конкретным роутерам (а не глобально через middleware). Раньше это
# было middleware _enforce_configurator_permission в app/main.py —
# теперь сделано через Depends + exception_handler.
