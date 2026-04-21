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


settings = Settings()
