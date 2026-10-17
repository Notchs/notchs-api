from fastapi import APIRouter, Response, status

from app.modules.health.dependencies import HealthServiceDep
from app.modules.health.schemas import HealthResponse, ReadinessResponse
from app.modules.health.service import HealthService

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", response_model=HealthResponse, summary="Liveness probe")
async def liveness() -> HealthResponse:
    return HealthService.liveness()


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe (DB + Redis)")
async def readiness(service: HealthServiceDep, response: Response) -> ReadinessResponse:
    result = await service.readiness()
    if result.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
