import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("sigma17")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


# Daily output-retention sweeper. Runs at 03:15 UTC every day.
# Idempotent and bounded by batch_size; safe to invoke ad-hoc as well.
app.conf.beat_schedule = {
    "purge-expired-outputs-daily": {
        "task": "processing.tasks.purge_expired_outputs_task",
        "schedule": crontab(hour=3, minute=15),
        "kwargs": {"batch_size": 500},
    },
}
