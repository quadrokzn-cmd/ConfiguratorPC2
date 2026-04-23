# Настройки приложения: читаем из переменных окружения (.env).

import os
from dataclasses import dataclass


@dataclass
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://user:password@localhost:5432/kvadro_tech",
    )
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

    # --- Этап 5: веб-сервис ---
    # Пароль, с которым впервые создаётся пользователь-админ
    # (см. scripts/create_admin.py). После создания больше не требуется.
    admin_initial_password: str = os.getenv("ADMIN_INITIAL_PASSWORD", "")

    # Секрет для подписи сессионных cookie. SessionMiddleware читает его
    # при старте приложения; дефолт здесь — только защита от падения в
    # dev-режиме, в проде ОБЯЗАТЕЛЬНО задавать свой.
    session_secret_key: str = os.getenv(
        "SESSION_SECRET_KEY",
        "dev-secret-change-me",
    )

    # Дневной лимит расходов OpenAI на всю систему (в рублях).
    daily_openai_budget_rub: float = float(
        os.getenv("DAILY_OPENAI_BUDGET_RUB", "100")
    )

    # URL тестовой БД; читается только в tests/conftest.py.
    test_database_url: str = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/configurator_pc_test",
    )


settings = Settings()
