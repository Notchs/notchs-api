from typing import Annotated

from fastapi import Depends

from app.common.dependencies import DBSession, RedisClient
from app.modules.health.service import HealthService


def get_health_service(session: DBSession, redis: RedisClient) -> HealthService:
    return HealthService(session, redis)


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]
