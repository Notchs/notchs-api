from app.common.exceptions import NotFoundException, TooManyRequestsException


class LeaderboardCityNotFoundException(NotFoundException):
    error_code = "leaderboards.city_not_found"
    message = "This city has no leaderboard."


class LeaderboardRateLimitedException(TooManyRequestsException):
    error_code = "leaderboards.rate_limited"
    message = "Too many requests. Please try again in a minute."
