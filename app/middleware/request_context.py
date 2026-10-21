"""Attaches a request ID to every request/response and logs request timing.

The request id and the caller's IP are also kept in a context variable for the rest of the
request (including background tasks it starts), so code deep inside, such as the audit log, can
read them without every function passing the request along.
"""

import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.common.http import client_ip
from app.core.logging import get_logger

REQUEST_ID_HEADER = "X-Request-ID"
MAX_REQUEST_ID_LENGTH = 32  # audit_logs.request_id; ours are 32-character hex UUIDs

logger = get_logger("app.request")


@dataclass(frozen=True, slots=True)
class RequestInfo:
    request_id: str
    ip: str | None


_current: ContextVar[RequestInfo | None] = ContextVar("request_info", default=None)


def current_request() -> RequestInfo | None:
    """The request being handled, or None outside a request (CLI, background sweeps)."""
    return _current.get()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        token = _current.set(
            RequestInfo(request_id=request_id[:MAX_REQUEST_ID_LENGTH], ip=client_ip(request))
        )

        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            _current.reset(token)
        duration_ms = (time.perf_counter() - start) * 1000

        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "%s %s -> %s (%.1fms) [%s]",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        return response
