"""Protection applied to every request, before any route runs.

    body size     a request that declares a body over MAX_REQUEST_BODY_BYTES gets 413
    global limit  one visitor (IP) gets GLOBAL_RATE_LIMIT_PER_MINUTE requests a minute over the
                  whole API, then 429 until the minute ends. Health checks and Meta's webhook
                  are never counted. If Redis is down the limit is skipped, not the request.
    headers       responses are never guessed to be another type (nosniff), and are never stored
                  by browsers or CDNs unless the route itself says so (so one person's data can
                  never be served to another from a cache)

Written as a plain ASGI middleware: no per-request task, so it costs almost nothing.
"""

from redis.exceptions import RedisError
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.common.http import client_ip
from app.core import rate_limit
from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import redis_client

logger = get_logger(__name__)

WINDOW_SECONDS = 60
# Never counted: uptime checks, and Meta's servers (one address delivers many customers' messages).
UNCOUNTED = (
    f"{settings.API_V1_PREFIX}/health",
    f"{settings.API_V1_PREFIX}/webhooks/",
)


def _error(status: int, code: str, message: str, headers: dict[str, str] | None = None):
    body = {"success": False, "error": {"code": code, "message": message}}
    return JSONResponse(body, status_code=status, headers=headers)


class ProtectionMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)

        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > settings.MAX_REQUEST_BODY_BYTES:
            response = _error(413, "request_too_large", "That request is too large.")
            await response(scope, receive, send)
            return

        if await self._over_the_limit(request):
            response = _error(
                429,
                "rate_limited",
                "Too many requests. Please try again in a minute.",
                {"Retry-After": str(WINDOW_SECONDS)},
            )
            await response(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Cache-Control", "no-store")
            await send(message)

        await self.app(scope, receive, send_with_headers)

    @staticmethod
    async def _over_the_limit(request: Request) -> bool:
        limit = settings.GLOBAL_RATE_LIMIT_PER_MINUTE
        if limit <= 0 or request.method == "OPTIONS":
            return False
        if request.url.path.startswith(UNCOUNTED):
            return False
        ip = client_ip(request)
        if ip is None:
            return False
        try:
            allowed = await rate_limit.hit(
                redis_client, f"rl:all:{ip}", limit=limit, window_seconds=WINDOW_SECONDS
            )
        except RedisError:
            logger.warning("Global rate limit skipped: Redis did not answer")
            return False
        return not allowed
