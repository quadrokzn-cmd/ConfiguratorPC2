# Подключение к PostgreSQL через SQLAlchemy.

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings


# ---------------------------------------------------------------------------
# Защита от UnicodeDecodeError на русской Windows.
#
# На русской локали Windows PostgreSQL по умолчанию отдаёт служебные
# сообщения (NOTICE/WARNING + ParameterStatus на старте соединения) в
# кодировке CP1251/CP1252. psycopg2 пытается декодировать их как UTF-8
# и падает ещё ДО того, как SQLAlchemy получит возможность выполнить
# любой запрос.
#
# Решение в два слоя:
#
#   1) connect_args={"client_encoding": "utf8"} — psycopg2 устанавливает
#      client_encoding ещё на рукопожатии, ДО чтения первых сообщений
#      сервера. Это главный фикс — именно он предотвращает падение
#      внутри psycopg2.connect().
#
#   2) event listener "connect" выставляет lc_messages='C' сразу после
#      того, как соединение открылось. Это вторая линия обороны: если
#      какой-то код позже вызовет запрос и сервер пришлёт NOTICE на
#      русском, он всё равно будет в ASCII.
#
# Такой же фикс нужен всем, кто создаёт SQLAlchemy-движок в этом
# проекте (тесты используют отдельный engine — см. tests/test_web/conftest.py).
# ---------------------------------------------------------------------------

engine = create_engine(
    settings.database_url,
    future=True,
    connect_args={"client_encoding": "utf8"},
)


@event.listens_for(engine, "connect")
def _set_english_messages(dbapi_connection, connection_record):
    """Принудительно переводит серверные сообщения на ASCII-локаль 'C'."""
    cur = dbapi_connection.cursor()
    try:
        cur.execute("SET lc_messages TO 'C'")
    finally:
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    """Зависимость FastAPI: выдаёт сессию БД на время запроса."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
