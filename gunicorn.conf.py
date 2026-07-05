import os


def post_fork(server, worker):
    """Only the first gunicorn worker runs background schedulers (reminders, calendar sync)."""
    os.environ["MEMORY_SCHEDULER_ENABLED"] = "true" if worker.age == 1 else "false"
