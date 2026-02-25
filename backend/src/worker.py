"""Celery app for background jobs."""
import os

from celery import Celery


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


app = Celery(
    "hpd_leads",
    broker=_redis_url(),
    backend=_redis_url(),
    include=[
        "src.tasks.enrich",
        "src.tasks.ingest",
        "src.tasks.score",
        "src.tasks.quality_checks",
        "src.tasks.entity_resolution",
    ],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
)

# Keep compatibility with Celery's module attribute discovery.
celery = app

