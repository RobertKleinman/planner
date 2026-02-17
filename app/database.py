"""
database.py — Database Connection & Session Management
========================================================
Sets up SQLite connection. Provides get_db() dependency for FastAPI endpoints.
See v1 README for detailed explanation of engine, sessions, and Base.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from typing import Generator
from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}  # SQLite + async compatibility
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """Provides a DB session per request. Auto-closes when done."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
