# Корневой conftest тестов.
#
# Главная задача — до импорта app.database переключить DATABASE_URL на
# TEST_DATABASE_URL. Если этого не сделать, app.database создаст engine
# на основной БД (kvadro_tech), и тесты этапа 5 будут писать в неё.
#
# Тесты модулей NLU/configurator/manual_edit мокают всё — им реальная
# БД не нужна, поэтому переключение URL им не мешает.

import os

from dotenv import load_dotenv

# Сначала читаем .env (если есть), чтобы достать TEST_DATABASE_URL.
load_dotenv()

_TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/configurator_pc_test",
)
os.environ["DATABASE_URL"] = _TEST_DB

# Тестовый OPENAI_API_KEY: гарантируем, что ни один тест не уходит в сеть.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-stub")

# Тестовый секрет сессии.
os.environ.setdefault("SESSION_SECRET_KEY", "test-session-secret")
