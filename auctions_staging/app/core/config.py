from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_host: str
    app_port: int
    app_env: str
    log_level: str
    database_url: str
    basic_auth_users: dict[str, str]
    llm_budget_enabled: bool
    llm_budget_daily_rub: int
    email_agent_url: str
    email_agent_token: str


def _parse_users(raw: str) -> dict[str, str]:
    users: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise ValueError(f"BASIC_AUTH_USERS entry '{pair}' must be 'login:password'")
        login, password = pair.split(":", 1)
        login = login.strip()
        password = password.strip()
        if not login or not password:
            raise ValueError(f"BASIC_AUTH_USERS entry '{pair}' has empty login or password")
        users[login] = password
    if not users:
        raise ValueError("BASIC_AUTH_USERS must contain at least one login:password pair")
    return users


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_host=os.getenv("APP_HOST", "0.0.0.0"),
        app_port=int(os.getenv("APP_PORT", "8000")),
        app_env=os.getenv("APP_ENV", "dev"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg2://quadrotech:quadrotech@postgres:5432/quadrotech",
        ),
        basic_auth_users=_parse_users(os.getenv("BASIC_AUTH_USERS", "manager:pwd1,owner:pwd2")),
        llm_budget_enabled=os.getenv("LLM_BUDGET_ENABLED", "false").lower() == "true",
        llm_budget_daily_rub=int(os.getenv("LLM_BUDGET_DAILY_RUB", "100")),
        email_agent_url=os.getenv("EMAIL_AGENT_URL", ""),
        email_agent_token=os.getenv("EMAIL_AGENT_TOKEN", ""),
    )
