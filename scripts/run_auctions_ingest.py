"""CLI для запуска ingest аукционов из произвольного окружения.

Назначение (мини-этап 9e.2): тонкая обёртка над
``portal.services.auctions.ingest.orchestrator.run_ingest_once(engine)``,
без APScheduler и без FastAPI. Подхватывает .env-файл с ограниченным DSN
(роль ``ingest_writer`` из миграции 0035), создаёт SQLAlchemy-движок и
выполняет один цикл ингеста. Предназначена для запуска по расписанию
через Task Scheduler/systemd на офисном сервере (этап 9e.3) или вручную
с любой dev-машины.

Запуск (PowerShell, dev-проверка под pre-prod):

    python scripts/run_auctions_ingest.py \\
        --env-file .env.local.preprod.v2 \\
        --db-url-env INGEST_WRITER_DATABASE_URL_PREPROD

Коды выхода:
    0 — успех
    1 — необработанное исключение во время ингеста
    2 — проблема конфигурации (нет env-файла или DSN-переменной)
  130 — прерывание по SIGINT

Совместимость: Python 3.10+ (используется в проекте; локально 3.12.13).
Никаких 3.11+-only фич нет.

Безопасность: DSN/пароль никогда не печатаются. В логи попадает только
имя env-переменной, из которой взят DSN.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# Чтобы запуск из корня репо видел пакет ``app``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def run_ingest(env_file: str, db_url_env: str, log_level: str = "INFO") -> int:
    """Загрузить .env, создать engine и выполнить один цикл ingest.

    Вынесено отдельной функцией для тестируемости (см. tests/test_auctions/
    test_run_auctions_ingest.py — monkeypatch на ``run_ingest_once``).
    """
    from dotenv import load_dotenv

    env_path = Path(env_file)
    if not env_path.is_file():
        print(
            f"Файл окружения не найден: {env_file}. "
            "Укажите путь через --env-file.",
            file=sys.stderr,
        )
        return 2

    load_dotenv(env_path, override=False)

    dsn = os.environ.get(db_url_env, "").strip()
    if not dsn:
        print(
            f"Переменная окружения {db_url_env} не задана или пуста "
            f"(env-файл: {env_file}). Проверьте --db-url-env.",
            file=sys.stderr,
        )
        return 2

    from loguru import logger
    from sqlalchemy import create_engine

    # Перенастраиваем loguru на нужный уровень. orchestrator пишет через
    # тот же logger, так что после remove()+add() уровень применится и к
    # его сообщениям.
    logger.remove()
    logger.add(sys.stderr, level=log_level)

    logger.info(
        "ingest CLI start: dsn-source-env={}, env-file={}",
        db_url_env,
        env_file,
    )

    engine = create_engine(dsn, pool_pre_ping=True)

    def _on_sigint(_signum, _frame):
        logger.warning("SIGINT received — disposing engine and exiting")
        try:
            engine.dispose()
        finally:
            sys.exit(130)

    signal.signal(signal.SIGINT, _on_sigint)

    from portal.services.auctions.ingest.orchestrator import run_ingest_once

    started_at = datetime.now()
    t0 = time.perf_counter()
    logger.info("ingest run started at {}", started_at.isoformat(timespec="seconds"))

    try:
        stats = run_ingest_once(engine)
    except Exception:
        logger.exception("ingest CLI failed with unhandled exception")
        engine.dispose()
        return 1

    elapsed = time.perf_counter() - t0
    engine.dispose()

    logger.info(
        "ingest CLI done: elapsed_sec={:.1f}, stats={}",
        elapsed,
        stats.as_dict(),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Однократный прогон ingest аукционов (zakupki.gov.ru → Postgres) "
            "из произвольного окружения, под ограниченной ролью ingest_writer."
        ),
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Путь к .env-файлу с DSN (default: .env в текущей директории).",
    )
    parser.add_argument(
        "--db-url-env",
        default="INGEST_WRITER_DATABASE_URL",
        help=(
            "Имя переменной окружения, содержащей DSN "
            "(default: INGEST_WRITER_DATABASE_URL)."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Уровень логирования loguru (default: INFO).",
    )
    args = parser.parse_args(argv)
    return run_ingest(args.env_file, args.db_url_env, args.log_level)


if __name__ == "__main__":
    raise SystemExit(main())
