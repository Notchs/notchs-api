"""Background jobs on Redis: work that must not run inside a request and must survive a restart.

queue.py      enqueue a job (Redis Streams)
worker.py     run jobs: retries, crash recovery, never twice (`python -m app.jobs.worker`)
email.py      the email job, and `EmailQueue`, which every email goes through
handlers.py   which function runs each kind of job
"""
