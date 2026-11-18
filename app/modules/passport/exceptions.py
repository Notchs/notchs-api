from app.common.exceptions import (
    ServiceUnavailableException,
    TooManyRequestsException,
    UnprocessableException,
)


class PassportAreaInvalidException(UnprocessableException):
    error_code = "passport.area_invalid"
    message = "Pick an area from the list."


class PhoneChangeRateLimitedException(TooManyRequestsException):
    error_code = "passport.phone_change_rate_limited"
    message = "Too many codes. Please try again in 15 minutes."


class PassportBirthDateInvalidException(UnprocessableException):
    error_code = "passport.birth_date_invalid"
    message = "Enter your real date of birth. Notchs is for people aged 18 and over."


class PhotoInvalidException(UnprocessableException):
    error_code = "passport.photo_invalid"
    message = "Upload a JPG, PNG or WebP picture of up to 5 MB."


class PhotoRateLimitedException(TooManyRequestsException):
    error_code = "passport.photo_rate_limited"
    message = "Too many new photos. Please try again in an hour."


class PhotoStorageNotConfiguredException(ServiceUnavailableException):
    error_code = "passport.photo_storage_not_configured"
    message = "Profile pictures aren't available yet."


class PhotoStorageFailedException(ServiceUnavailableException):
    error_code = "passport.photo_storage_failed"
    message = "We couldn't save your picture. Please try again."
