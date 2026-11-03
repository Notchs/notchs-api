"""Shared exception classes. Raise them from services; never build error responses in routers.

Each class sets the HTTP status, a machine-readable `error_code` and a default message, and
`app/core/exception_handlers.py` turns them into `{"success": false, "error": {...}}`.
Modules subclass these for their own codes, e.g. `class VenueNotFoundException(NotFoundException)`
with `error_code = "venues.not_found"`.
"""

from typing import Any

from fastapi import status


class AppException(Exception):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None, *, details: Any = None) -> None:
        self.message = message or self.message
        self.details = details
        super().__init__(self.message)


class BadRequestException(AppException):
    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "bad_request"
    message = "Bad request."


class UnauthorizedException(AppException):
    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "unauthorized"
    message = "Authentication required."


class ForbiddenException(AppException):
    status_code = status.HTTP_403_FORBIDDEN
    error_code = "forbidden"
    message = "You do not have permission to perform this action."


class NotFoundException(AppException):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "not_found"
    message = "Resource not found."


class ConflictException(AppException):
    status_code = status.HTTP_409_CONFLICT
    error_code = "conflict"
    message = "Resource already exists."


class UnprocessableException(AppException):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_code = "unprocessable"
    message = "The request could not be processed."


class ServiceUnavailableException(AppException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = "service_unavailable"
    message = "This is not available right now. Please try again in a few minutes."


class TooManyRequestsException(AppException):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    error_code = "rate_limited"
    message = "Too many requests. Please try again later."
