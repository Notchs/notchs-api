from app.common.exceptions import ConflictException, NotFoundException


class AdminUserNotFoundException(NotFoundException):
    error_code = "admin_users.not_found"
    message = "Admin not found."


class AdminEmailTakenException(ConflictException):
    error_code = "admin_users.email_taken"
    message = "An admin with that email already exists. Send them a new link instead."


class AdminSelfChangeException(ConflictException):
    error_code = "admin_users.self_change"
    message = "You can't remove your own admin access. Ask another admin."


class AdminLastAdminException(ConflictException):
    error_code = "admin_users.last_admin"
    message = "There must always be at least one active admin."


class AdminInactiveException(ConflictException):
    error_code = "admin_users.inactive"
    message = "Turn this admin back on before sending a link."
