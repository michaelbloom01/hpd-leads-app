"""Celery application configuration.

Workers run ingestion, scoring, and enrichment tasks in isolation
from the API server process. Redis serves as both broker and result backend.
"""
import os

from celery import Celery

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

app = Celery(
    "hpd_leads",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "src.tasks.ingest",
        "src.tasks.score",
        "src.tasks.enrich",
        "src.tasks.entity_resolution",
        "src.tasks.quality_checks",
    ],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/New_York",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,
    beat_schedule={
        "weekly-hpd-complaints": {
            "task": "src.tasks.ingest.ingest_hpd_complaints",
            "schedule": 604800.0,
        },
        "monthly-acris": {
            "task": "src.tasks.ingest.ingest_acris_transactions",
            "schedule": 2592000.0,
        },
        "monthly-dob-permits": {
            "task": "src.tasks.ingest.ingest_dob_permits",
            "schedule": 2592000.0,
        },
        "monthly-hpd-litigation": {
            "task": "src.tasks.ingest.ingest_hpd_litigation",
            "schedule": 2592000.0,
        },
        "monthly-emergency-repairs": {
            "task": "src.tasks.ingest.ingest_emergency_repairs",
            "schedule": 2592000.0,
        },
        "monthly-eviction-filings": {
            "task": "src.tasks.ingest.ingest_eviction_filings",
            "schedule": 2592000.0,
        },
        "quarterly-facade-inspections": {
            "task": "src.tasks.ingest.ingest_facade_inspections",
            "schedule": 7776000.0,
        },
        "quarterly-pad": {
            "task": "src.tasks.ingest.ingest_pad_addresses",
            "schedule": 7776000.0,
        },
        "daily-quality-checks": {
            "task": "src.tasks.quality_checks.run_quality_checks",
            "schedule": 86400.0,
        },
        "weekly-entity-resolution": {
            "task": "src.tasks.entity_resolution.resolve_entities",
            "schedule": 604800.0,
        },
        "weekly-scoring": {
            "task": "src.tasks.score.compute_churn_scores",
            "schedule": 604800.0,
        },
    },
)
