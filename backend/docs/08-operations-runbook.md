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

## Monitoring and Alerting

- External uptime monitor (recommended):
  - Monitor `GET /api/health` every 5 minutes
  - Alert channel: email (minimum), Slack/PagerDuty (preferred)
- Sentry:
  - Ensure `SENTRY_DSN` is configured in production runtime env
  - Verify a test exception appears in Sentry after deploy
  - Configure alert rule for new unhandled exceptions
- DB pool watch:
  - Track `GET /api/health/db-pool`
  - Treat utilization ratio >= 0.9 as degraded and investigate long-running queries

## Lead Portfolio Sync

- Recompute lead building/unit snapshots from live links:
  - `POST /api/admin/recompute-lead-portfolio`
- Purpose:
  - Syncs `leads.portfolio_size` and `leads.total_units` from `building_management` + `buildings`
  - Fixes stale snapshot drift that can undercount results in Leads filters

## Deployment Checklist (Railway + Vercel)

1. Deploy backend and frontend.
2. Verify backend health:
   - `GET /api/health`
   - `GET /api/health/db-pool`
   - `GET /api/v1/jobs/worker-health`
3. Verify frontend critical flows:
   - Leads filtering and sorting
   - Building list CRUD/member actions
   - Smart List evaluate/pin behavior
4. Confirm Sentry receives errors and alert routing works.

## Incident Quick Response

- Worker stalled / stale running jobs:
  - Run `POST /api/v1/jobs/reconcile-stale-running?dry_run=true`
  - If results are correct, run with `dry_run=false`
- DB pool pressure:
  - Check `GET /api/health/db-pool`
  - Reduce concurrent heavy jobs, then inspect long query patterns
