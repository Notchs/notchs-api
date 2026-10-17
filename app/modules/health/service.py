from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.health.schemas import HealthResponse, ReadinessResponse, Status

logger = get_logger(__name__)


class HealthService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.redis = redis

    @staticmethod
    def liveness() -> HealthResponse:
        return HealthResponse(
            status="ok",
            app=settings.APP_NAME,
            version=settings.APP_VERSION,
            environment=settings.ENVIRONMENT,
        )

    async def readiness(self) -> ReadinessResponse:
        database = await self._check_database()
        redis = await self._check_redis()
        status: Status = "ok" if database == "ok" and redis == "ok" else "error"
        return ReadinessResponse(status=status, database=database, redis=redis)

    async def _check_database(self) -> Status:
        try:
            await self.session.execute(text("SELECT 1"))
        except Exception:
            logger.exception("Database readiness check failed")
            return "error"
        return "ok"

    async def _check_redis(self) -> Status:
        try:
            await self.redis.ping()
        except Exception:
            logger.exception("Redis readiness check failed")
            return "error"
        return "ok"
