import os
import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.config import settings
from app.core.redis import get_redis
from app.db.models import Base
from app.db.session import get_db_session
from app.main import app

# Database tests run against this URL only (CI sets it to its throwaway Postgres). Each test run
# gets its own schema, dropped afterwards; without the variable they are skipped, so a local
# `pytest` never touches the shared Neon database.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=f"http://test{settings.API_V1_PREFIX}"
    ) as ac:
        yield ac


@pytest.fixture(scope="session")
async def db_engine() -> AsyncIterator[AsyncEngine]:
    if not TEST_DATABASE_URL:
        pytest.skip("Set TEST_DATABASE_URL to run database tests")
    schema = f"test_{uuid.uuid4().hex[:12]}"
    engine = create_async_engine(
        TEST_DATABASE_URL,
        poolclass=NullPool,
        execution_options={"schema_translate_map": {None: schema}},
    )
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await engine.dispose()


@pytest.fixture
async def db(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session whose work is rolled back after the test. `commit()` inside the code under
    test only releases a savepoint, so tests never see each other's rows."""
    async with db_engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    fake = FakeAsyncRedis(decode_responses=True)
    yield fake
    await fake.flushall()
    await fake.aclose()


@pytest.fixture
def session_factory(db: AsyncSession) -> Callable[[], AsyncSession]:
    """For code that opens its own sessions (jobs): each one is a savepoint on the test's
    connection, so its commits are rolled back with the test too."""
    return lambda: AsyncSession(
        bind=db.bind,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )


@pytest.fixture
async def api(db: AsyncSession, redis: FakeAsyncRedis) -> AsyncIterator[AsyncClient]:
    """The app over HTTP, on the test's database session and fake Redis."""

    async def _db() -> AsyncIterator[AsyncSession]:
        yield db
        await db.flush()

    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_redis] = lambda: redis
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=f"http://test{settings.API_V1_PREFIX}"
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
