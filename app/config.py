# Настройки приложения: читаем из переменных окружения (.env).
#
# Критично важные переменные (DATABASE_URL, OPENAI_API_KEY) — обязательные.
# Если их нет — падаем при старте приложения с ПОНЯТНОЙ ошибкой, а не
# молча используем опасный дефолт (например, `user:password` в БД-строке,
# из-за чего первый же запрос к БД падает с authentication failed).
#
# Этап 10.1: добавлены APP_ENV, APP_SECRET_KEY, APP_COOKIE_DOMAIN,
# ADMIN_USERNAME/ADMIN_PASSWORD, RUN_SCHEDULER. На production отсутствие
# APP_SECRET_KEY/SESSION_SECRET_KEY роняет старт.

import logging
import os
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)


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


def _is_production_env() -> bool:
    """True, если APP_ENV=production. Используется для production-проверок."""
    return os.getenv("APP_ENV", "").strip().lower() == "production"


def _resolve_session_secret() -> str:
    """Читает секрет для подписи сессионных cookie.

    Приоритет: APP_SECRET_KEY → SESSION_SECRET_KEY (старое имя для обратной
    совместимости). На production без секрета — RuntimeError на старте,
    чтобы случайно не уйти в прод с dev-ключом. На локалке — fallback
    на dev-значение с warning-ом в лог.
    """
    raw = (os.getenv("APP_SECRET_KEY", "") or os.getenv("SESSION_SECRET_KEY", "")).strip()
    if raw:
        return raw
    if _is_production_env():
        raise RuntimeError(
            "Переменная APP_SECRET_KEY не задана, а APP_ENV=production. "
            "Сгенерируйте секрет: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))" '
            "и пропишите его в окружение Railway/контейнера."
        )
    logger.warning(
        "APP_SECRET_KEY не задан — используется небезопасный dev-секрет. "
        "Для production обязательно задайте свой ключ."
    )
    return "dev-secret-change-me"


def _resolve_cookie_domain() -> str | None:
    """Читает домен сессионных cookie. Пусто/не задано → None
    (текущее однодоменное поведение). На Railway будем выставлять
    `.quadro.tatar` для шеринга сессии с будущим app.quadro.tatar."""
    raw = os.getenv("APP_COOKIE_DOMAIN", "").strip()
    return raw or None


def _bool_env(name: str, default: bool = False) -> bool:
    """Парсит булевы env-переменные: 1/true/yes/on → True, иначе False."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # --- Среда исполнения ---
    # APP_ENV: "production" включает строгие проверки (secure cookies,
    # обязательный APP_SECRET_KEY). Любое другое значение (или пусто) —
    # dev/test режим.
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))

    # --- Обязательные ---
    database_url:   str = field(default_factory=lambda: _require_env("DATABASE_URL"))
    openai_api_key: str = field(default_factory=lambda: _require_env("OPENAI_API_KEY"))

    # --- Этап 5: веб-сервис ---
    # Пароль, с которым впервые создаётся пользователь-админ
    # (см. scripts/create_admin.py). После создания больше не требуется.
    admin_initial_password: str = field(
        default_factory=lambda: os.getenv("ADMIN_INITIAL_PASSWORD", "")
    )

    # --- Этап 10.1: bootstrap-учётка для Railway ---
    # Пара ADMIN_USERNAME / ADMIN_PASSWORD. Если обе заданы — при старте
    # сервиса scripts/bootstrap_admin.py создаст такого пользователя
    # (если его ещё нет в БД).
    admin_username: str = field(
        default_factory=lambda: os.getenv("ADMIN_USERNAME", "")
    )
    admin_password: str = field(
        default_factory=lambda: os.getenv("ADMIN_PASSWORD", "")
    )

    # Секрет для подписи сессионных cookie. На production обязателен
    # (см. _resolve_session_secret). В коде продолжаем называть поле
    # session_secret_key — чтобы не ломать тесты и старые места;
    # читается оно из APP_SECRET_KEY (новый) или SESSION_SECRET_KEY (старый).
    session_secret_key: str = field(default_factory=_resolve_session_secret)

    # Домен для сессионных cookie. None — текущее однодоменное поведение.
    # На Railway: ".quadro.tatar" чтобы сессия шарилась с app.quadro.tatar.
    cookie_domain: str | None = field(default_factory=_resolve_cookie_domain)

    # --- Этап 10.1: фоновые задачи ---
    # APScheduler стартует только при RUN_SCHEDULER=1. На локалке и
    # на единственном инстансе Railway держим включённым; когда появятся
    # реплики — оставляем 1 только на одном инстансе, чтобы cron-задачи
    # не дублировались.
    run_scheduler: bool = field(
        default_factory=lambda: _bool_env("RUN_SCHEDULER", default=False)
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

    # --- Этап 8.3: SMTP для отправки писем поставщикам -------------
    # Дефолты настроены под mail.ru / quadro.tatar. Пароль — приложение-
    # пароль, создаётся в mail.ru ЛК → Безопасность → Пароли для внешних
    # приложений (обычный пароль от ящика не подойдёт). Если пусто —
    # отправка упадёт с понятной ошибкой, чтение/preview продолжат работать.
    smtp_host: str = field(
        default_factory=lambda: os.getenv("SMTP_HOST", "smtp.mail.ru")
    )
    smtp_port: int = field(
        default_factory=lambda: int(os.getenv("SMTP_PORT", "465"))
    )
    smtp_use_ssl: bool = field(
        default_factory=lambda: _bool_env("SMTP_USE_SSL", default=True)
    )
    smtp_user: str = field(
        default_factory=lambda: os.getenv("SMTP_USER", "quadro@quadro.tatar")
    )
    smtp_app_password: str = field(
        default_factory=lambda: os.getenv("SMTP_APP_PASSWORD", "")
    )
    # Архивная копия уходящих писем — чтобы в ящике отправителя оставался след.
    smtp_bcc: str = field(
        default_factory=lambda: os.getenv("SMTP_BCC", "quadro@quadro.tatar")
    )
    smtp_from_name: str = field(
        default_factory=lambda: os.getenv("SMTP_FROM_NAME", "КВАДРО-ТЕХ")
    )

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"


settings = Settings()
