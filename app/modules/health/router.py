from fastapi import APIRouter, Request, Response, status

from app.common.http import EDGE_CLIENT_IP, NETLIFY_CLIENT_IP, client_address, from_website
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


@router.get("/whoami", summary="The caller's own address as the API sees it (for rate limits)")
async def whoami(request: Request) -> dict[str, object]:
    """Only about the caller themselves: which address the rate limits would use for them and
    which proxy header it came from. Used to check the proxies in front of a new deploy."""
    address = client_address(request)
    forwarded = [p for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    return {
        "ip": address.ip,
        "source": address.source,
        "through_website": from_website(request),
        "headers_present": [
            name for name in (NETLIFY_CLIENT_IP, *EDGE_CLIENT_IP) if name in request.headers
        ],
        "forwarded_hops": len(forwarded),
    }
