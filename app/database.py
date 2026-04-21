# Подключение к PostgreSQL через SQLAlchemy.

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, future=True)


# Принудительно переводим служебные сообщения сервера на английский.
# На русской Windows lc_messages по умолчанию = Russian_Russia.1251,
# из-за чего psycopg2 падает при декодировании NOTICE/WARNING как UTF-8.
# 'C' — это ASCII-локаль, безопасная для любого клиента.
@event.listens_for(engine, "connect")
def _set_english_messages(dbapi_connection, connection_record):
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
