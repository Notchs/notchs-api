from app.common.exceptions import ConflictException, NotFoundException


class UserNotFoundException(NotFoundException):
    error_code = "users.not_found"
    message = "Customer not found."


class UserDeletedException(ConflictException):
    error_code = "users.deleted"
    message = "This customer's account is closed."
