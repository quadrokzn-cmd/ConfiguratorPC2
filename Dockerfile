# КВАДРО-ТЕХ Конфигуратор ПК — production образ для Railway.
# Node не ставим: Tailwind собирается локально, static/dist/main.css
# уже в репо (см. docs/design-decisions.md, решение №3).
# psycopg2-binary не требует gcc/libpq-dev — wheel.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt
# pre-deploy gate (этап 9d.1): ловит рассинхрон requirements.txt ↔
# реально установленных пакетов (как было словлено на 9c с beautifulsoup4
# и loguru). Падение здесь = починить requirements.txt, а не на старте.
RUN pip check

COPY . .

EXPOSE 8080

CMD python -m scripts.apply_migrations && \
    python -m scripts.bootstrap_admin && \
    uvicorn app.main:app \
        --host 0.0.0.0 \
        --port ${PORT:-8080} \
        --proxy-headers \
        --forwarded-allow-ips='*'
