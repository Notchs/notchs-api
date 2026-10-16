"""Global exception handlers: every error leaves the API as `{"success": false, "error": {...}}`.

The exception classes themselves live in `app/common/exceptions.py`.
"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.exceptions import AppException
from app.common.schemas import ErrorBody, ErrorResponse
from app.core.logging import get_logger

logger = get_logger(__name__)


def _error_response(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(status_code=status_code, content=jsonable_encoder(body))


async def _app_exception_handler(_: Request, exc: AppException) -> JSONResponse:
    return _error_response(exc.status_code, exc.error_code, exc.message, exc.details)


async def _http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _error_response(exc.status_code, "http_error", str(exc.detail))


def _validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Where and why each field failed. Leaves out `input` (it could echo a password back) and
    `ctx` (it can hold the raised exception object, which is not JSON)."""
    return [
        {"loc": list(error.get("loc", ())), "msg": error.get("msg", ""), "type": error.get("type")}
        for error in exc.errors()
    ]


async def _validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "validation_error",
        "Request validation failed.",
        _validation_details(exc),
    )


async def _unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception", exc_info=exc)
    return _error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR, AppException.error_code, AppException.message
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppException, _app_exception_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
