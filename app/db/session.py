"""Async engine, session factory and the per-request session dependency."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


def build_connect_args(*, use_pooler: bool) -> dict[str, Any]:
    """asyncpg connect args for the configured schema and connection mode.

    Direct connections get `search_path`, `statement_timeout` and UTC set at connect time.
    Transaction-mode poolers (PgBouncer/Supavisor) reject or drop startup parameters and
    can't keep prepared statements, so there we disable the statement cache instead; set
    `statement_timeout` on the DB role (`ALTER ROLE notchs_app SET statement_timeout = ...`).
    """
    if use_pooler:
        return {
            "statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid.uuid4().hex}__",
        }
    return {
        "server_settings": {
            "search_path": settings.DB_SCHEMA,
            "statement_timeout": str(settings.DB_STATEMENT_TIMEOUT_MS),
            "timezone": "UTC",
            "application_name": settings.APP_NAME,
        }
    }


def create_engine() -> AsyncEngine:
    url = make_url(settings.DATABASE_URL)
    if settings.DB_USE_POOLER:
        url = url.update_query_dict({"prepared_statement_cache_size": "0"})

    return create_async_engine(
        url,
        echo=settings.DB_ECHO,
        pool_pre_ping=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        pool_recycle=settings.DB_POOL_RECYCLE,
        connect_args=build_connect_args(use_pooler=settings.DB_USE_POOLER),
        # Unqualified tables/enums go to DB_SCHEMA even when search_path can't be set (pooler).
        execution_options={"schema_translate_map": {None: settings.DB_SCHEMA}},
    )


engine = create_engine()

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def check_connection() -> dict[str, str]:
    """Open one connection and report where it landed. Raises if the DB is unreachable."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT current_database() AS database, current_user AS user, "
                    "current_setting('server_version') AS version"
                )
            )
        ).one()
    return {
        "host": f"{engine.url.host}:{engine.url.port or 5432}",
        "database": row.database,
        "user": row.user,
        "schema": settings.DB_SCHEMA,
        "version": row.version,
    }


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """One session per request; commits on success, rolls back on error."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
