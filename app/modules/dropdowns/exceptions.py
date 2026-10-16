from app.common.exceptions import NotFoundException, UnprocessableException


class DropdownNotFoundException(NotFoundException):
    error_code = "dropdowns.not_found"
    message = "There is no dropdown with this name."


class DropdownFilterInvalidException(UnprocessableException):
    error_code = "dropdowns.filter_invalid"
    message = "A filter this dropdown needs is missing or invalid."
