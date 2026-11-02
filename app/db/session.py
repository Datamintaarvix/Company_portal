"""Database engine, session factory, and FastAPI dependency.

Provides connection pooling with pre-ping validation, proper transaction handling,
and safe session lifecycle cleanup.
"""

import logging
from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger("company_portal.db")
settings = get_settings()

# Create SQLAlchemy Engine with connection health checking (pool_pre_ping)
# pool_pre_ping tests connections before giving them out to catch dropped connections.
engine = create_engine(
    settings.SQLALCHEMY_DATABASE_URI,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    echo=settings.DEBUG and not settings.is_production,
)

# Session factory for generating scoped sessions
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session per request.

    Ensures the session is cleanly closed when the request lifecycle ends,
    even if an exception was raised.
    """
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db_connection() -> bool:
    """Execute a lightweight query (SELECT 1) to verify database connectivity.

    Returns:
        True if the database is reachable, False otherwise.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except (SQLAlchemyError, Exception) as exc:
        logger.error("Database connection check failed: %s", exc)
        return False
