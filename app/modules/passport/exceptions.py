from app.common.exceptions import TooManyRequestsException, UnprocessableException


class PassportAreaInvalidException(UnprocessableException):
    error_code = "passport.area_invalid"
    message = "Pick an area from the list."


class PhoneChangeRateLimitedException(TooManyRequestsException):
    error_code = "passport.phone_change_rate_limited"
    message = "Too many codes. Please try again in 15 minutes."
