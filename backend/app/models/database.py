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


def normalize_database_url(url: str) -> str:
    """Accept the URL forms providers hand out (Supabase, Heroku-style
    `postgres://`, plain `postgresql://`) and pin them to the psycopg 3
    driver that is in requirements.txt."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _create_engine(database_url: str):
    connect_args = {}
    pool_kwargs = {}

    database_url = normalize_database_url(database_url)
    if is_sqlite(database_url):
        connect_args = {"check_same_thread": False}
        pool_kwargs = {"poolclass": StaticPool} if ":memory:" in database_url else {}
    else:
        # Pool sized for several app/worker containers sharing one Postgres.
        pool_kwargs = {"pool_pre_ping": True, "pool_recycle": 1800, "pool_size": 5, "max_overflow": 5}

    engine = create_engine(database_url, connect_args=connect_args, echo=False, **pool_kwargs)

    if is_sqlite(database_url):
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
    if is_sqlite(database_url):
        # Zero-config local dev / contributors. Production (Postgres) schema is
        # owned by Alembic: run `alembic upgrade head` (the Docker image does).
        Base.metadata.create_all(bind=_engine)
    logger.info("database_initialized", dialect=_engine.dialect.name)


def get_engine():
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine


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
