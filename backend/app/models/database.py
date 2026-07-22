"""SQLAlchemy setup with connection pooling and session management."""

from typing import Generator
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import StaticPool
import structlog

logger = structlog.get_logger()

Base = declarative_base()

_engine = None
_SessionLocal = None


def _create_engine(database_url: str):
    connect_args = {}
    pool_kwargs = {}

    if "sqlite" in database_url:
        connect_args = {"check_same_thread": False}
        pool_kwargs = {"poolclass": StaticPool}
    else:
        pool_kwargs = {"pool_pre_ping": True, "pool_recycle": 3600}

    engine = create_engine(database_url, connect_args=connect_args, echo=False, **pool_kwargs)

    if "sqlite" in database_url:
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def init_db(database_url: str):
    """Initialize the database engine, session factory, and create tables."""
    global _engine, _SessionLocal
    _engine = _create_engine(database_url)
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    Base.metadata.create_all(bind=_engine)
    logger.info("database_initialized")


def get_db() -> Generator:
    """FastAPI dependency that provides a database session."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_session():
    """Create a standalone DB session for use outside FastAPI's request
    cycle -- e.g. the RQ worker process, which has no request to hang a
    dependency off of. Caller is responsible for closing it."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _SessionLocal()
