"""Integration tests on a real Postgres schema (docs/WORKPLAN §0.8).

Opt in with RUN_DB_TESTS=1. The run creates a `test_<random>` schema with
`alembic upgrade head`, gives every test a session inside a transaction that is rolled
back afterwards, and drops the schema at the end. `public` and `dev_<name>` are never touched.
"""

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.clients.email import EmailMessage, EmailSendError, get_email_client
from app.clients.turnstile import get_turnstile_client
from app.core.config import settings
from app.core.redis import get_redis
from app.db.session import build_connect_args, get_db_session
from app.main import app
from app.modules.whatsapp.models import WhatsAppMessage
from app.modules.whatsapp.processor import InboundProcessor, get_inbound_processor

if os.environ.get("RUN_DB_TESTS") != "1":
    pytest.skip("database tests: set RUN_DB_TESTS=1", allow_module_level=True)


@pytest.fixture(scope="session")
def db_schema() -> Iterator[str]:
    schema = f"test_{uuid.uuid4().hex[:10]}"
    env = os.environ | {"DB_SCHEMA": schema}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        check=True,
        capture_output=True,
    )
    yield schema
    subprocess.run(
        [sys.executable, "-c", _DROP_SCHEMA, schema], env=env, check=True, capture_output=True
    )


_DROP_SCHEMA = """
import asyncio, sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings

async def main(schema):
    engine = create_async_engine(settings.migration_database_url)
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    await engine.dispose()

asyncio.run(main(sys.argv[1]))
"""


@pytest.fixture(scope="session")
async def db_engine(db_schema: str):
    url = make_url(settings.DATABASE_URL)
    if settings.DB_USE_POOLER:
        url = url.update_query_dict({"prepared_statement_cache_size": "0"})
    engine = create_async_engine(
        url,
        pool_size=2,
        connect_args=build_connect_args(use_pooler=settings.DB_USE_POOLER),
        execution_options={"schema_translate_map": {None: db_schema}},
    )
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(db_engine, db_schema: str) -> AsyncIterator[AsyncSession]:
    """A session whose commits are savepoints inside one transaction that is rolled back."""
    async with db_engine.connect() as conn:
        outer = await conn.begin()
        # Raw SQL needs the schema too; the translate map only covers ORM/Core statements.
        await conn.execute(text(f'SET LOCAL search_path TO "{db_schema}"'))
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


class Outbox:
    """Stands in for EmailClient: keeps what would have been sent. Set `fails` to act like the
    email provider refusing every email (wrong sender, outage)."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.fails = False

    async def send(self, message: EmailMessage) -> None:
        if self.fails:
            raise EmailSendError("Resend returned 403: not authorized to send from gmail.com")
        self.sent.append(message)


class FakeTurnstile:
    def __init__(self) -> None:
        self.passes = True

    async def verify(self, token: str, *, ip: str | None = None) -> bool:
        return self.passes


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


@pytest.fixture
def turnstile() -> FakeTurnstile:
    return FakeTurnstile()


class RecordingBot:
    """Stands in for the WhatsApp bot: keeps which messages it was given, can be made to fail."""

    def __init__(self) -> None:
        self.handled: list[uuid.UUID] = []
        self.fails = False

    async def __call__(self, session: AsyncSession, message: WhatsAppMessage) -> None:
        if self.fails:
            raise RuntimeError("bot failed")
        self.handled.append(message.id)


@pytest.fixture
def bot() -> RecordingBot:
    return RecordingBot()


@pytest.fixture
def processor(db: AsyncSession, redis: FakeAsyncRedis, bot: RecordingBot) -> InboundProcessor:
    """The real processor, with sessions on the test connection (so it sees, and rolls back
    with, everything the test did) and the recording bot."""

    def sessions() -> AsyncSession:
        return AsyncSession(
            bind=db.bind,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )

    return InboundProcessor(sessions, redis, bot)


@pytest.fixture
async def api(
    db: AsyncSession,
    redis: FakeAsyncRedis,
    outbox: Outbox,
    turnstile: FakeTurnstile,
    processor: InboundProcessor,
) -> AsyncIterator[AsyncClient]:
    """HTTP client whose requests use the test session (committing like the real dependency),
    an in-memory Redis, a recording email client, a switchable CAPTCHA and a recording bot."""

    async def _session() -> AsyncIterator[AsyncSession]:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    overrides = {
        get_db_session: _session,
        get_redis: lambda: redis,
        get_email_client: lambda: outbox,
        get_turnstile_client: lambda: turnstile,
        get_inbound_processor: lambda: processor,
    }
    app.dependency_overrides.update(overrides)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.7", 50000)),
        base_url=f"http://test{settings.API_V1_PREFIX}",
    ) as client:
        yield client
    for dependency in overrides:
        app.dependency_overrides.pop(dependency, None)
