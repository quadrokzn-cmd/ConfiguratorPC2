# Настройки приложения: читаем из переменных окружения (.env).
#
# Критично важные переменные (DATABASE_URL, OPENAI_API_KEY) — обязательные.
# Если их нет — падаем при старте приложения с ПОНЯТНОЙ ошибкой, а не
# молча используем опасный дефолт (например, `user:password` в БД-строке,
# из-за чего первый же запрос к БД падает с authentication failed).
#
# UI-5 (Путь B, 2026-05-11): модуль переехал из app/config.py в
# shared/config.py — единственный источник Settings для портала, скриптов,
# shared/db.py. Папка app/ удалена. Поле run_scheduler (RUN_SCHEDULER)
# выкинуто как dead-field — после UI-4.5 cron-задачи активирует только
# APP_ENV=production / RUN_BACKUP_SCHEDULER=1 в portal/scheduler.py.

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


def _resolve_database_url() -> str:
    """Читает DSN PostgreSQL c fallback'ом DATABASE_URL → DATABASE_PUBLIC_URL.

    Railway раздаёт два варианта URL: DATABASE_URL для внутренних
    подключений из сервиса (короткий host) и DATABASE_PUBLIC_URL для
    внешних подключений через прокси (`*.proxy.rlwy.net:port`). В
    dev-env-файлах для подключения снаружи к prod-БД исторически
    пишется именно DATABASE_PUBLIC_URL — fallback позволяет коду из
    shared.config работать с такими файлами без переименования
    переменной.

    Приоритет: DATABASE_URL → DATABASE_PUBLIC_URL. При срабатывании
    fallback'а — INFO-лог (без значения, только сам факт).
    """
    raw = os.getenv("DATABASE_URL", "").strip()
    if raw:
        return raw
    fallback = os.getenv("DATABASE_PUBLIC_URL", "").strip()
    if fallback:
        logger.info("database_url fallback: using DATABASE_PUBLIC_URL")
        return fallback
    raise RuntimeError(
        "Переменная DATABASE_URL не задана, DATABASE_PUBLIC_URL тоже. "
        "Проверьте файл .env в корне проекта: он должен существовать и "
        "содержать «DATABASE_URL=...» (или, для внешних подключений к "
        "Railway, «DATABASE_PUBLIC_URL=...»)."
    )


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
    `.quadro.tatar` для шеринга сессии с app.quadro.tatar."""
    raw = os.getenv("APP_COOKIE_DOMAIN", "").strip()
    return raw or None


def _split_csv(name: str, default: str) -> list[str]:
    """Парсит comma-separated env-переменную: 'a, b ,c' → ['a','b','c'].
    Пустые элементы выкидываются."""
    raw = os.getenv(name, default)
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


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
    database_url:   str = field(default_factory=_resolve_database_url)
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

    # --- Этап 9Б.1: межсервисные ссылки конфигуратор ↔ портал ---
    # PORTAL_URL — куда конфигуратор редиректит неавторизованных
    # пользователей (`${PORTAL_URL}/login?next=<encoded URL>`).
    # CONFIGURATOR_URL — куда портал ссылается с плитки «Конфигуратор ПК».
    # Локально по умолчанию: portal=8081, configurator=8080. В production
    # выставляются вручную в Railway через env-переменные сервисов
    # (см. docs/deployment.md, секция «Этап 9Б.1»).
    #
    # UI-5 (Путь B, 2026-05-11): configurator_url остался для совместимости
    # с шаблонами/sidebar, но реально config.quadro.tatar больше не
    # обслуживается — DNS-запись удаляется собственником после деплоя UI-5.
    portal_url: str = field(
        default_factory=lambda: os.getenv("PORTAL_URL", "http://localhost:8081").rstrip("/")
    )
    configurator_url: str = field(
        default_factory=lambda: os.getenv("CONFIGURATOR_URL", "http://localhost:8080").rstrip("/")
    )

    # ALLOWED_REDIRECT_HOSTS — whitelist хостов для безопасного
    # post-login redirect (?next=). Защита от open redirect: если
    # next-URL указывает на хост вне списка, портал отбросит его и
    # отправит пользователя на главную /. Хост сравнивается с netloc
    # (host:port), так что для локалки нужны именно "localhost:8080".
    allowed_redirect_hosts: list[str] = field(
        default_factory=lambda: _split_csv(
            "ALLOWED_REDIRECT_HOSTS", "localhost:8080,localhost:8081"
        )
    )

    # --- Этап 10.1: фоновые задачи ---
    # UI-4.5 (Путь B): фоновые задачи теперь живут только в portal/scheduler.py.
    # Активация — APP_ENV=production / RUN_BACKUP_SCHEDULER=1 (читается прямо
    # внутри scheduler'а через os.getenv, не через Settings). Поле
    # run_scheduler/RUN_SCHEDULER удалено как dead-field (UI-5).

    # Дневной лимит расходов OpenAI на всю систему (в рублях).
    daily_openai_budget_rub: float = field(
        default_factory=lambda: float(os.getenv("DAILY_OPENAI_BUDGET_RUB", "100"))
    )

    # --- Мини-этап 9e.4.2 (2026-05-12): kill-switch cron'а auctions_ingest ---
    # Регистрировать ли cron-задачу `auctions_ingest` внутри FastAPI-процесса
    # портала. По умолчанию True — это исторический режим (Railway сам делал
    # ingest каждые 2ч). На prod после cutover'а на офисный worker (9e.4.2)
    # выставляется AUCTIONS_INGEST_ENABLED=false: cron не регистрируется,
    # ingest выполняет внешняя Windows Task Scheduler задача (см. 9e.3).
    # Pre-prod продолжает работать с дефолтом True.
    #
    # Не путать с одноимённым ключом таблицы `settings` в БД (читается
    # `portal/scheduler.py::_is_auctions_ingest_enabled`): тот тумблер
    # гейтит per-tick выполнение уже зарегистрированной задачи, а этот
    # флаг определяет, регистрировать ли задачу вообще.
    auctions_ingest_enabled: bool = field(
        default_factory=lambda: _bool_env("AUCTIONS_INGEST_ENABLED", default=True)
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
