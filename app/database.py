"""
database.py — Database Connection & Session Management
========================================================
Works with both SQLite (local dev) and PostgreSQL (Railway).
Detects which one based on the DATABASE_URL.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from typing import Generator
from app.config import settings

database_url = settings.database_url

# PostgreSQL fix: Railway gives "postgres://" but SQLAlchemy needs "postgresql://"
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

# SQLite needs check_same_thread=False; PostgreSQL does not
if database_url.startswith("sqlite"):
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(database_url)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """Provides a DB session per request. Auto-closes when done."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
