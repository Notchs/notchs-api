from app.common.exceptions import ConflictException, UnprocessableException


class ReferralCodeInvalidException(UnprocessableException):
    error_code = "referrals.code_invalid"
    message = "No Notchs member has that invite code."


class ReferralNotAllowedException(ConflictException):
    error_code = "referrals.not_allowed"
    message = "An inviter can only be added once, in your first 14 days on Notchs."
