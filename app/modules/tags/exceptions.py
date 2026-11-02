from app.common.exceptions import BadRequestException, ConflictException, NotFoundException


class TagNotFoundException(NotFoundException):
    error_code = "tags.not_found"
    message = "Tag not found."


class TagNameTakenException(ConflictException):
    error_code = "tags.name_taken"
    message = "A tag with this name already exists."


class TagInUseException(ConflictException):
    error_code = "tags.in_use"
    message = "Customers have picked this tag, so it cannot be deleted."


class TagKindLockedException(ConflictException):
    error_code = "tags.kind_locked"
    message = "This tag is in use, so its kind cannot change."


class TagSelectionInvalidException(BadRequestException):
    error_code = "tags.selection_invalid"
    message = "One or more tags do not exist or cannot be used here."
