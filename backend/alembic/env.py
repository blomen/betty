"""Alembic Environment Configuration for Arnold."""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool
from alembic import context

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.models import Base

config = context.config

# Use DATABASE_URL env var if set, otherwise fall back to alembic.ini value
db_url = os.environ.get("DATABASE_URL")
if db_url:
    # Convert async URL to sync for Alembic
    sync_url = db_url.replace("+asyncpg", "+psycopg2")
    config.set_main_option("sqlalchemy.url", sync_url)
else:
    from src.db.models import DB_PATH
    config.set_main_option("sqlalchemy.url", f"sqlite:///{DB_PATH}")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
