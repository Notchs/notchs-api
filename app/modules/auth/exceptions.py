from app.common.exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    TooManyRequestsException,
    UnauthorizedException,
    UnprocessableException,
)


class InvalidCredentialsException(UnauthorizedException):
    # Same error for a wrong email, a wrong password and a locked account.
    error_code = "auth.invalid_credentials"
    message = "Email or password is incorrect."


class NotAuthenticatedException(UnauthorizedException):
    error_code = "auth.not_authenticated"
    message = "Please log in."


class TokenInvalidException(BadRequestException):
    error_code = "auth.token_invalid"
    message = "This link is invalid or has expired."


class CodeInvalidException(BadRequestException):
    error_code = "auth.code_invalid"
    message = "This code is wrong or has expired."


class AuthRateLimitedException(TooManyRequestsException):
    error_code = "auth.rate_limited"


class AccountInactiveException(ForbiddenException):
    error_code = "auth.account_inactive"
    message = "This account is not active."


class CurrentPasswordInvalidException(BadRequestException):
    # 400, not 401: the person is logged in, they only mistyped their current password.
    error_code = "auth.current_password_invalid"
    message = "Your current password is not correct."


class EmailTakenException(ConflictException):
    error_code = "auth.email_taken"
    message = "This email is already used by another Notchs login."


class EmailUnchangedException(UnprocessableException):
    error_code = "auth.email_unchanged"
    message = "This is already your login email."


class AdminPermissionRequiredException(ForbiddenException):
    error_code = "auth.permission_required"
    message = "Your role does not allow this. Ask an Owner to change your role."


class PasswordInvalidException(UnprocessableException):
    error_code = "auth.password_invalid"
    message = "This password does not meet the requirements."


class EmailNotAllowedException(UnprocessableException):
    error_code = "auth.email_not_allowed"
    message = "Please use a permanent email address."


class CaptchaFailedException(BadRequestException):
    error_code = "captcha.failed"
    message = "Please complete the security check again."
