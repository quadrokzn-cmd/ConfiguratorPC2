# Совместимость со старыми импортами `from app.database import ...`.
#
# Этап 9Б.1: вся логика подключения переехала в shared/db.py — общий
# на конфигуратор и портал движок. Этот модуль остаётся, чтобы не
# трогать пол-репозитория (app/templating.py, app/scheduler.py,
# scripts/*) — они продолжают импортировать engine/SessionLocal/Base/
# get_db из привычного места.

from shared.db import Base, SessionLocal, engine, get_db


__all__ = ["Base", "SessionLocal", "engine", "get_db"]
