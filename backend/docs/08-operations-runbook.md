# Double Edge Operations Runbook

## Scope

Production operations for the PostgreSQL + Redis + worker runtime.

## Health Endpoints

- `GET /api/health` - base API health
- `GET /api/health/db-pool` - DB pool utilization snapshot
- `GET /api/v1/jobs/summary` - queue throughput and failure summary (24h)
- `GET /api/v1/jobs/worker-health` - worker/broker liveness and stale-running count
- `GET /api/v1/quality/data-health` - aggregate data quality plus canonical materialization counts
- `GET /api/v1/canonical/entities` - materialized canonical entities
- `GET /api/v1/canonical/proposals` - materialized canonical proposals and review buckets
- `GET /api/v1/truth/schema-status` - truth-confidence table and Alembic readiness
- `GET /api/v1/truth/activation-packet` - compact truth activation/business-use gate
- `GET /api/v1/truth/health-report` - read-only truth health, source audit, benchmark, and activation checklist

## Data Truth & Confidence Activation

Run these checks before any truth-confidence migration, source refresh, or materialization:

```powershell
.\.venv-x64\Scripts\python.exe scripts\truth_migration_preflight.py --indent 2
.\.venv-x64\Scripts\python.exe scripts\truth_activation_packet.py --indent 2
.\.venv-x64\Scripts\python.exe scripts\truth_health_report.py --materialization-limit 50 --validation-sample-limit 10
.\.venv-x64\Scripts\python.exe scripts\truth_completion_audit.py --include-runtime --include-production --indent 2
.\.venv-x64\Scripts\python.exe scripts\truth_completion_audit.py --artifacts-only --indent 2
.\.venv-x64\Scripts\python.exe scripts\truth_production_probe.py --indent 2
```

Interpretation:

- `truth_completion_audit.py` must exit nonzero while the objective is not complete; this is expected before migration/materialization/source readiness.
- `truth_completion_audit.py --artifacts-only` is intended for CI and checks implementation/runbook coverage only; it does not evaluate local or production business-use readiness.
- `business_use_allowed=false` means Double Edge is not approved for sourcing, diligence, or outreach decisions from the truth layer.
- Any `truth_surface_status` other than `deployed` means production is not truth-confidence ready. `not_deployed`, `partial_or_auth_gated`, and `unreachable` all keep `production_business_use_allowed=false`.

Approval boundary:

- Applying migrations through `010_truth_manifest` mutates the configured database by adding truth-confidence and rollback-manifest tables.
- Source-refresh jobs mutate source tables and data-quality logs.
- Truth materialization mutates only truth claim/evidence/snapshot tables, but still requires dry-run review plus `dry_run=false&confirm_execute=true`.

## Job Hygiene

- Inspect potentially stuck jobs:
  - `GET /api/v1/jobs/worker-health`
- Dry-run stale job reconciliation:
  - `POST /api/v1/jobs/reconcile-stale-running?dry_run=true`
- Execute stale job reconciliation:
  - `POST /api/v1/jobs/reconcile-stale-running?dry_run=false`

Default stale threshold is 120 minutes for jobs in `running` with no `finished_at`.
Executing stale job reconciliation changes job rows. Review the dry-run output and get explicit operator approval before running with `dry_run=false`.

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

## Canonical Prep Observability

- Preview canonical-prep buckets without writes:
  - `POST /api/admin/canonical-prep/preview`
- Materialize additive canonical proposals without mutating workflow links:
  - `POST /api/admin/canonical-prep/materialize`
- Review materialized output:
  - `GET /api/v1/canonical/entities`
  - `GET /api/v1/canonical/proposals`
  - `GET /api/leads/{lead_id}/lineage`

## Truth Materialization Rollout

- Always start with a dry-run preview:
  - `POST /api/v1/truth/materialize/preview?limit=50`
- Prefer source-scoped batches for large ledgers:
  - `POST /api/v1/truth/materialize/preview?limit=50&source=building_management`
  - `POST /api/v1/jobs/truth_materialization/start?dry_run=false&confirm_execute=true&limit=50&source=building_management`
- Before execution, inspect `sample_materialized_claim_specs`, `planned_claims_by_source`, source freshness, and the activation packet.
- After execution, record the returned `run_id` and immediately dry-run rollback:
  - `python scripts/truth_materialization_rollback.py --run-id <run_id>`
- Execute rollback only if needed and only after confirming the run-specific manifest:
  - `python scripts/truth_materialization_rollback.py --run-id <run_id> --execute --confirm-execute`
- Do not use the system for business decisions until the activation packet reports `business_use_allowed=true`.

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
  - If results are correct, get explicit operator approval before running with `dry_run=false`
- DB pool pressure:
  - Check `GET /api/health/db-pool`
  - Reduce concurrent heavy jobs, then inspect long query patterns
