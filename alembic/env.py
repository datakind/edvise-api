"""Alembic environment for edvise-api.

Manages API-owned tables only. Excludes `users` (Laravel / edvise-ui DDL)
and any `*_backup` tables that may exist on shared Cloud SQL instances.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, create_engine
from sqlalchemy.engine.url import URL

from webapp.database import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Tables Alembic must never create/drop/alter via autogenerate or baseline.
EXCLUDED_TABLES = frozenset({"users"})


def include_object(object, name, type_, reflected, compare_to):  # noqa: A002, ANN001
    if type_ == "table":
        if name in EXCLUDED_TABLES:
            return False
        if name and name.endswith("_backup"):
            return False
    return True


def _database_url() -> str:
    """Build SQLAlchemy URL from the same env vars as the webapp (or override)."""
    override = os.environ.get("ALEMBIC_DATABASE_URL")
    if override:
        return override

    env = os.environ.get("ENV", "LOCAL").upper()
    if env == "LOCAL":
        # File-backed SQLite for local alembic commands (in-memory loses state).
        return os.environ.get("ALEMBIC_SQLITE_URL", "sqlite:///./alembic_local.db")

    return URL.create(
        drivername="mysql+pymysql",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        host=os.environ["INSTANCE_HOST"],
        port=int(os.environ.get("DB_PORT", "3306")),
        database=os.environ["DB_NAME"],
    ).render_as_string(hide_password=False)


def _connect_args() -> dict:
    env = os.environ.get("ENV", "LOCAL").upper()
    if env == "LOCAL" or os.environ.get("ALEMBIC_DATABASE_URL"):
        return {}
    return {
        "ssl_ca": os.environ["DB_ROOT_CERT"],
        "ssl_cert": os.environ["DB_CERT"],
        "ssl_key": os.environ["DB_KEY"],
    }


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connect_args = _connect_args()
    url = _database_url()

    connectable = create_engine(
        url,
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
