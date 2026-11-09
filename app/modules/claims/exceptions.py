from app.common.exceptions import (
    AppException,
    ConflictException,
    NotFoundException,
    TooManyRequestsException,
)


class ClaimVenueNotFoundException(NotFoundException):
    error_code = "claims.venue_not_found"
    message = "That place isn't on Notchs yet, or it isn't live."


class ClaimAlreadyNotchedException(ConflictException):
    error_code = "claims.already_notched"
    message = "You already have a Notch here today."


class ClaimNicheLimitException(ConflictException):
    error_code = "claims.niche_limit"
    message = "You already have a Notch at a place of this kind today: one per kind each day."


class ClaimExistsException(ConflictException):
    error_code = "claims.exists"
    message = "You already sent a bill for this place today. We'll check it soon."


class ClaimLimitException(TooManyRequestsException):
    error_code = "claims.limit"
    message = "You can start up to 3 claims a day. Please try again tomorrow."


class ClaimNotFoundException(NotFoundException):
    error_code = "claims.not_found"
    message = "Claim not found."


class ClaimNotInReviewException(ConflictException):
    error_code = "claims.not_in_review"
    message = "This claim isn't waiting for a review."


class ClaimCannotApproveException(ConflictException):
    """The message says why (already notched that day, customer blocked...)."""

    error_code = "claims.cannot_approve"
    message = "This claim can't be approved."


class ClaimPhotoUnavailableException(AppException):
    status_code = 410
    error_code = "claims.photo_unavailable"
    message = "The photo isn't available from WhatsApp any more (it keeps photos about 30 days)."


class ClaimPhotoFetchFailedException(AppException):
    status_code = 502
    error_code = "claims.photo_fetch_failed"
    message = "WhatsApp didn't send the photo. Please try again in a minute."
