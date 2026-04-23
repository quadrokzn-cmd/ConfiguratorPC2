# Настройки приложения: читаем из переменных окружения (.env).
#
# Критично важные переменные (DATABASE_URL, OPENAI_API_KEY) — обязательные.
# Если их нет — падаем при старте приложения с ПОНЯТНОЙ ошибкой, а не
# молча используем опасный дефолт (например, `user:password` в БД-строке,
# из-за чего первый же запрос к БД падает с authentication failed).

import os
from dataclasses import dataclass, field


def _require_env(name: str) -> str:
    """Читает переменную окружения. Падает с понятной ошибкой, если пусто.
    В тестах tests/conftest.py подкладывает нужные значения до импорта
    этого модуля, так что тесты этот assert не ловят."""
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(
            f"Переменная {name} не задана. Проверьте файл .env в корне "
            f"проекта: он должен существовать и содержать «{name}=...»."
        )
    return val


@dataclass
class Settings:
    # --- Обязательные ---
    database_url:   str = field(default_factory=lambda: _require_env("DATABASE_URL"))
    openai_api_key: str = field(default_factory=lambda: _require_env("OPENAI_API_KEY"))

    # --- Этап 5: веб-сервис ---
    # Пароль, с которым впервые создаётся пользователь-админ
    # (см. scripts/create_admin.py). После создания больше не требуется.
    admin_initial_password: str = field(
        default_factory=lambda: os.getenv("ADMIN_INITIAL_PASSWORD", "")
    )

    # Секрет для подписи сессионных cookie. В проде ОБЯЗАТЕЛЬНО задавать
    # свой через .env; дефолт оставлен только чтобы импорт модуля не падал
    # при запуске тестов/скриптов, которым сессии не нужны.
    session_secret_key: str = field(
        default_factory=lambda: os.getenv(
            "SESSION_SECRET_KEY", "dev-secret-change-me"
        )
    )

    # Дневной лимит расходов OpenAI на всю систему (в рублях).
    daily_openai_budget_rub: float = field(
        default_factory=lambda: float(os.getenv("DAILY_OPENAI_BUDGET_RUB", "100"))
    )

    # URL тестовой БД; читается только в tests/conftest.py.
    test_database_url: str = field(
        default_factory=lambda: os.getenv(
            "TEST_DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/configurator_pc_test",
        )
    )


settings = Settings()
