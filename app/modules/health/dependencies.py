from typing import Annotated

from fastapi import Depends

from app.common.dependencies import DBSession
from app.modules.health.service import HealthService


def get_health_service(session: DBSession) -> HealthService:
    return HealthService(session)


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]
