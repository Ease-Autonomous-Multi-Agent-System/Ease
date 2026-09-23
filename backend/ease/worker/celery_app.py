"""Celery application (components C9-C12): broker + result backend on Redis, three routing targets."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

from ease.config import get_settings
from ease.logs import configure_logging

_s = get_settings()

app = Celery("ease", broker=f"{_s.redis_url}/0", backend=f"{_s.redis_url}/1", include=["ease.worker.tasks"])
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],  # never pickle: a poisoned message could otherwise execute code
    task_acks_late=True,  # ack after the run: a killed worker's task is redelivered...
    task_reject_on_worker_lost=True,  # ...and resumes from its last LangGraph checkpoint
    worker_prefetch_multiplier=1,
    task_soft_time_limit=900,
    task_time_limit=1200,
    result_expires=24 * 3600,
    broker_transport_options={"visibility_timeout": 1500},
    task_routes={
        "ease.worker.tasks.run_workflow": {"queue": "agent"},
        "ease.worker.tasks.resume_workflow": {"queue": "agent"},
        "ease.worker.tasks.ingest_document": {"queue": "io"},
        "ease.worker.tasks.run_due_templates": {"queue": "io"},
    },
    beat_schedule={
        "scheduled-templates": {"task": "ease.worker.tasks.run_due_templates", "schedule": crontab(minute="*/5")},
    },
    timezone="UTC",
)


@worker_process_init.connect
def _init_worker(**_: object) -> None:
    configure_logging()
