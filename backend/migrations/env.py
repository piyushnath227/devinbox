"""Alembic environment: schema source of truth for non-SQLite databases."""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import Base  # noqa: E402  (imports every model so metadata is complete)
from app.models.database import normalize_database_url, is_sqlite  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        from app.config import get_settings
        url = get_settings().DATABASE_URL
    return normalize_database_url(url)


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"}, compare_type=True,
        render_as_batch=is_sqlite(url),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _database_url()
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = url
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True,
            render_as_batch=is_sqlite(url),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
