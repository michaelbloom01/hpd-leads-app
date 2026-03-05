"""Lead reconciliation task entrypoint."""

from __future__ import annotations

from typing import Any, Optional

try:
    from src.worker import app as celery_app
except ImportError:
    class _FakeCelery:
        @staticmethod
        def task(*args, **kwargs):
            return lambda fn: fn
    celery_app = _FakeCelery()


def run_reconciliation(job_id: Optional[int] = None) -> dict[str, Any]:
    from src.tasks.lead_materialization import reconcile_lead_coverage

    fn = reconcile_lead_coverage.run if hasattr(reconcile_lead_coverage, "run") else reconcile_lead_coverage
    return fn(job_id=job_id)


@celery_app.task(bind=True, name="src.tasks.reconcile.run_reconciliation_task")
def run_reconciliation_task(self, job_id: Optional[int] = None) -> dict[str, Any]:
    return run_reconciliation(job_id=job_id)

