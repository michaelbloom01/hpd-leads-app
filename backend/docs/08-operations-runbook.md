# Double Edge Operations Runbook

## Scope

Production operations for the PostgreSQL + Redis + worker runtime.

## Health Endpoints

- `GET /api/health` - base API health
- `GET /api/health/db-pool` - DB pool utilization snapshot
- `GET /api/v1/jobs/summary` - queue throughput and failure summary (24h)
- `GET /api/v1/jobs/worker-health` - worker/broker liveness and stale-running count

## Job Hygiene

- Inspect potentially stuck jobs:
  - `GET /api/v1/jobs/worker-health`
- Dry-run stale job reconciliation:
  - `POST /api/v1/jobs/reconcile-stale-running?dry_run=true`
- Execute stale job reconciliation:
  - `POST /api/v1/jobs/reconcile-stale-running?dry_run=false`

Default stale threshold is 120 minutes for jobs in `running` with no `finished_at`.

## Smart List Auto-Evaluation

- Per-list scheduling is controlled by Smart List fields:
  - `auto_evaluate` (boolean)
  - `evaluation_interval_hours` (1-168)
  - `next_evaluation_at` (timestamp)
- Manually run due evaluations for current user:
  - `POST /api/smart-lists/auto-evaluate/run`
- Queue a background due-evaluation sweep:
  - `POST /api/v1/jobs/smart_lists_auto_evaluate/start`

## Notes

- Queue dispatch is Celery-first with in-process fallback.
- For predictable production scheduling, use platform cron to call queue-start endpoints.
