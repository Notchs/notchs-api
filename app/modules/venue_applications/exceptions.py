from app.common.exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
    TooManyRequestsException,
    UnprocessableException,
)


class ApplicationDuplicateException(ConflictException):
    error_code = "applications.duplicate"
    message = (
        "This email already has an application waiting for review or a Notchs venue login. "
        "Log in, or use Forgot password."
    )


class ApplicationNotFoundException(NotFoundException):
    error_code = "applications.not_found"
    message = "Application not found."


class ApplicationInvalidStateException(ConflictException):
    error_code = "applications.invalid_state"
    message = "This application has already been reviewed."


class ApplicationAreaInvalidException(UnprocessableException):
    error_code = "applications.area_invalid"
    message = "This area is not in the application's city."


class ApplicationCityInvalidException(UnprocessableException):
    error_code = "applications.city_invalid"
    message = "Notchs is not open in this city yet."


class VerificationExpiredException(BadRequestException):
    error_code = "applications.email_not_verified"
    message = "Please verify your email again: the verification has expired."


class ApplicationRateLimitedException(TooManyRequestsException):
    error_code = "applications.rate_limited"
    message = "Too many applications from this connection today. Please try again tomorrow."
