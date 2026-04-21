# Точка входа FastAPI-приложения.
# Полное наполнение будет добавлено на следующих этапах.

from fastapi import FastAPI

app = FastAPI(title="КВАДРО-ТЕХ: сервис-конфигуратор ПК")


@app.get("/")
def root():
    return {"status": "ok", "service": "КВАДРО-ТЕХ конфигуратор"}
