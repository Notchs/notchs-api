from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.health.schemas import HealthResponse, ReadinessResponse

logger = get_logger(__name__)


class HealthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def liveness() -> HealthResponse:
        return HealthResponse(
            status="ok",
            app=settings.APP_NAME,
            version=settings.APP_VERSION,
            environment=settings.ENVIRONMENT,
        )

    async def readiness(self) -> ReadinessResponse:
        try:
            await self.session.execute(text("SELECT 1"))
        except Exception:
            logger.exception("Database readiness check failed")
            return ReadinessResponse(status="error", database="error")
        return ReadinessResponse(status="ok", database="ok")
