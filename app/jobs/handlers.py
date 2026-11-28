"""Which function runs each kind of job. A new kind of job adds one line here."""

from app.jobs.email import SEND_EMAIL, send_email_job
from app.jobs.worker import Handler

HANDLERS: dict[str, Handler] = {
    SEND_EMAIL: send_email_job,
}
