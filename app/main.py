"""Application factory and ASGI entrypoint (`uvicorn app.main:app`)."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core import api_docs
from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.core.redis import check_redis, redis_client
from app.db.session import AsyncSessionLocal, check_connection, engine
from app.jobs.email import EmailQueue
from app.jobs.handlers import HANDLERS
from app.jobs.worker import Worker
from app.middleware.protection import ProtectionMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.modules.admin_users.bootstrap import ensure_first_admin
from app.modules.leaderboards.service import run_rebuilder
from app.modules.whatsapp.processor import get_inbound_processor, run_sweeper

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "Starting %s v%s (%s)", settings.APP_NAME, settings.APP_VERSION, settings.ENVIRONMENT
    )
    # Fail fast with a clear error instead of on the first request.
    try:
        db = await check_connection()
    except Exception:
        logger.exception("Could not connect to the database; check DB_* / DATABASE_URL in .env")
        await engine.dispose()
        raise
    logger.info(
        "Connected to database %s at %s as %s (schema %s, PostgreSQL %s)",
        db["database"],
        db["host"],
        db["user"],
        db["schema"],
        db["version"],
    )
    try:
        cache = await check_redis()
    except Exception:
        logger.exception("Could not connect to Redis; check REDIS_URL in .env")
        await redis_client.aclose()
        await engine.dispose()
        raise
    logger.info(
        "Connected to Redis at %s (db %s, version %s)", cache["host"], cache["db"], cache["version"]
    )
    if settings.FIRST_ADMIN_EMAIL:
        # Never stops the API from starting; the log says what happened.
        try:
            async with AsyncSessionLocal() as session:
                await ensure_first_admin(session, redis_client, EmailQueue(redis_client))
        except Exception:
            logger.exception("Could not create the first admin from FIRST_ADMIN_EMAIL")
    # The job worker (emails). Safe in every API server at once: each job goes to one worker.
    # Off when the worker runs as its own process (`python -m app.jobs.worker`).
    job_worker = None
    if settings.JOBS_WORKER_IN_API:
        job_worker = asyncio.create_task(
            Worker(redis_client, HANDLERS).run_forever(), name="job-worker"
        )
    sweeper = None
    if settings.WHATSAPP_SWEEP_INTERVAL_SECONDS > 0:
        sweeper = asyncio.create_task(
            run_sweeper(get_inbound_processor(), settings.WHATSAPP_SWEEP_INTERVAL_SECONDS),
            name="whatsapp-sweeper",
        )
    rebuilder = None
    if settings.LEADERBOARD_REBUILD_INTERVAL_SECONDS > 0:
        rebuilder = asyncio.create_task(
            run_rebuilder(
                AsyncSessionLocal, redis_client, settings.LEADERBOARD_REBUILD_INTERVAL_SECONDS
            ),
            name="leaderboard-rebuilder",
        )
    yield
    for task in (job_worker, sweeper, rebuilder):
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    await redis_client.aclose()
    await engine.dispose()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    setup_logging()

    docs_enabled = not settings.is_production
    openapi_url = f"{settings.API_V1_PREFIX}/openapi.json"
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=api_docs.DESCRIPTION,
        debug=settings.DEBUG,
        lifespan=lifespan,
        openapi_url=openapi_url if docs_enabled else None,
        docs_url=None,  # our own page with search and login help: app/core/api_docs.py
        redoc_url="/redoc" if docs_enabled else None,
    )
    if docs_enabled:
        api_docs.add_docs_page(app, title=settings.APP_NAME, openapi_url=openapi_url)

    app.add_middleware(ProtectionMiddleware)
    # Added last, so it runs first: every request is logged, refused ones included.
    app.add_middleware(RequestContextMiddleware)
    if settings.CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)
    return app


app = create_app()
