from app.common.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
)


class RoleNotFoundException(NotFoundException):
    error_code = "roles.not_found"
    message = "Role not found."


class RoleNameTakenException(ConflictException):
    error_code = "roles.name_taken"
    message = "A role with this name already exists."


class RoleLockedException(ConflictException):
    error_code = "roles.locked"
    message = "The Owner role always has every permission and cannot be changed or deleted."


class RoleInUseException(ConflictException):
    error_code = "roles.in_use"
    message = "Admins still have this role. Give them another role first."


class PermissionNotGrantableException(ForbiddenException):
    error_code = "roles.permission_not_grantable"
    message = "You can only give a role permissions that you have yourself."
