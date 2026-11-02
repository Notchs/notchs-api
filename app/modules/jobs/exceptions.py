from app.common.exceptions import ConflictException


class JobAlreadyRunningException(ConflictException):
    error_code = "jobs.running"
    message = "This job is running right now. Try again when it has finished."
