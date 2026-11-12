"""Alembic async migration environment.

Uses `MIGRATION_DATABASE_URL` (direct, unpooled; falls back to `DATABASE_URL`) and
migrates the schema named by `DB_SCHEMA`, creating it if needed. The `alembic_version`
table lives in the same schema, so every `dev_<name>` schema has its own history.

    DB_SCHEMA=dev_harsh alembic upgrade head
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.core.config import settings
from app.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
schema = settings.DB_SCHEMA  # validated as a plain identifier in Settings
VERSION_TABLE = "alembic_version"


# Monthly partitions of audit_logs (audit_logs_2026_11, audit_logs_default, ...) are made by
# migrations and the partition job, not by models.
PARTITION_PREFIXES = ("audit_logs_",)


def _include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    # The version table lives in DB_SCHEMA next to our tables, so autogenerate would see it
    # as a table missing from the models and try to drop it. Same for partitions.
    if type_ != "table" or name is None:
        return True
    return name != VERSION_TABLE and not name.startswith(PARTITION_PREFIXES)


def _configure(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        version_table=VERSION_TABLE,
        version_table_schema=schema,
        include_name=_include_name,
        compare_type=True,
        compare_server_default=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(
        url=settings.migration_database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        context.execute(f'SET search_path TO "{schema}"')
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    # Unqualified names in migrations (tables, enums, indexes) resolve to DB_SCHEMA.
    connection.execute(text(f'SET search_path TO "{schema}"'))
    connection.commit()

    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(settings.migration_database_url, poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
