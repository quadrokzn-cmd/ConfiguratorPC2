# Подключение к PostgreSQL через SQLAlchemy.

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    """Зависимость FastAPI: выдаёт сессию БД на время запроса."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
