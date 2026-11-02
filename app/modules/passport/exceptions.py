from app.common.exceptions import (
    BadRequestException,
    NotFoundException,
    TooManyRequestsException,
)


class NotchNotFoundException(NotFoundException):
    error_code = "notches.not_found"
    message = "Notch not found."


class MonthInvalidException(BadRequestException):
    error_code = "passport.month_invalid"
    message = "Use a month like 2026-10."


class AreaInvalidException(BadRequestException):
    error_code = "passport.area_invalid"
    message = "That area is not in your city."


class NoCityException(NotFoundException):
    error_code = "passport.no_city"
    message = "Notchs is not live in any city yet."


class ReportRateLimitedException(TooManyRequestsException):
    error_code = "passport.report_rate_limited"
    message = "You have sent a lot of reports today. Please try again tomorrow."
