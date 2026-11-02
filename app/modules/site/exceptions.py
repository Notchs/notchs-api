from app.common.exceptions import BadRequestException, NotFoundException, TooManyRequestsException


class CityNotFoundException(NotFoundException):
    error_code = "site.city_not_found"
    message = "This place isn't on the map."


class CategoryNotFoundException(NotFoundException):
    error_code = "site.category_not_found"
    message = "No ranking for that kind of place."


class WeekInvalidException(BadRequestException):
    error_code = "site.week_invalid"
    message = "Use the Monday of a finished week, e.g. 2026-11-09."


class JoinRateLimitedException(TooManyRequestsException):
    error_code = "site.join_rate_limited"
    message = "Too many sign-ups from here. Please try again in an hour."
