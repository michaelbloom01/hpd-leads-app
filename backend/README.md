# Double Edge - Backend

Python FastAPI backend for the Double Edge NYC housing intelligence platform.

> Context refresh (Mar 6, 2026): PostgreSQL-backed API + worker runtime are the production path. Some legacy SQLite compatibility code still exists, but lead generation, reconciliation, and quality checks now run through PostgreSQL + Celery on Railway.

## Quick Start

```bash
pip install -r requirements.txt
python -m uvicorn api:app --reload --port 8000
```

API docs at http://localhost:8000/docs

## Architecture (Current)

```
backend/
├── api.py                    # FastAPI app with all endpoints
├── start.py                  # Gunicorn startup script (Railway)
├── Dockerfile                # Railway deployment
├── requirements.txt          # Python dependencies
├── config/
│   └── scoring_weights.yaml  # Scoring V2 configuration
├── src/
│   ├── ingest/
│   │   ├── hpd_client.py     # HPD Buildings & Contacts API
│   │   ├── pluto_client.py   # PLUTO building classification
│   │   └── hpd_violations.py # HPD Violations API
│   ├── transform/
│   │   ├── normalize.py      # Building data normalization
│   │   └── aggregate.py      # Lead aggregation + entity classification
│   ├── score/
│   │   ├── scorer.py         # Scoring V2 (8 dimensions)
│   │   └── revenue.py        # Revenue estimation
│   ├── enrich/
│   │   ├── enricher.py       # Enrichment orchestrator
│   │   ├── contact_sources.py # Google Places, Hunter.io, MultiSourceEnricher
│   │   ├── ny_dos.py         # NY DOS corporation lookup (with SQLite cache)
│   │   └── web_crawl.py      # Web crawling for phone/email
│   ├── db/
│   │   └── session.py        # Async SQLAlchemy session (PostgreSQL)
│   ├── worker.py             # Celery app bootstrap (Redis broker/backend)
│   ├── tasks/
│   │   ├── ingest.py         # Ingestion tasks
│   │   ├── enrich.py         # Enrichment job lifecycle task
│   │   └── score.py          # Scoring tasks
│   └── storage/
│       └── database.py       # Legacy SQLite path (transitioning out of active runtime)
├── data/                     # SQLite database files (gitignored)
├── scripts/                  # Utility scripts
├── tasks/
│   ├── todo.md               # Task tracker
│   └── lessons.md            # Lessons learned
└── docs/                     # Architecture documentation
```

## Key API Endpoints

See root README.md for the full endpoint list.

## Database Schema

Primary runtime store is PostgreSQL via SQLAlchemy models and Alembic migrations.
Legacy SQLite tables remain for compatibility during architecture convergence.

Key entities include:

- `leads` - lead-level canonical read model for UI workflows
- `buildings` + management tables - building-level and ownership linkages
- `outreach_events` - pipeline activity history
- `smart_lists` - saved segment definitions and snapshots
- `ingestion_jobs` / jobs-related tables - operational tracking
- `truth_claims` / `truth_evidence` / `confidence_snapshots` - additive Data Truth & Confidence ledger introduced by migration `009_truth_confidence_program`
- `truth_materialization_manifest` - per-run rollback manifest for claim/evidence/snapshot materialization introduced by revision `010_truth_manifest`
- `truth_review_items` / `truth_validation_runs` / `golden_verification_cases` - review, validation, and benchmarking surfaces for confidence/audit workflows

## Data Truth & Confidence Operations

The truth-confidence program is intentionally activation-gated. These commands are read-only and safe to run before or after the local truth schema is applied:

```bash
python scripts/truth_migration_preflight.py --indent 2
python scripts/truth_activation_packet.py --indent 2
python scripts/truth_health_report.py --materialization-limit 50 --validation-sample-limit 10
python scripts/truth_adjudication_preview.py --limit 20 --indent 2
python scripts/truth_adjudication_apply.py --limit 20 --indent 2
python scripts/truth_manual_evidence.py --subject-type lead --subject-id <lead_id> --predicate manages_building --object-type building --object-id <bbl> --claim-type building_management --support-status supports --source-name manual_evidence --note "preview only" --indent 2
python scripts/truth_manual_evidence.py --payload-file <reviewed-source-evidence-preview.json> --indent 2
python scripts/truth_completion_audit.py --include-runtime --include-production --indent 2
python scripts/truth_completion_audit.py --artifacts-only --indent 2
python scripts/truth_production_probe.py --indent 2
```

Expected local activation posture:

- `truth_completion_audit.py` exits nonzero with `completion_status=not_complete`.
- `truth_completion_audit.py --artifacts-only` is CI-safe and only verifies prompt-to-artifact coverage; it does not prove runtime readiness.
- Local activation packet should keep `business_use_allowed=false`.
- Production probe should not be treated as ready unless `production_business_use_allowed=true`.
- In the active local DB, `010_truth_manifest` has been applied with explicit approval; schema rollback review command is `python -m alembic downgrade 010_truth_manifest:008_lead_lineage --sql`.
- Pilot materialization run `truth-materialization-manual-20260514142022` wrote 2,063 claims, 2,063 evidence rows, and 1,088 confidence snapshots. Its data rollback preview is `python scripts/truth_materialization_rollback.py --run-id truth-materialization-manual-20260514142022`.
- Truth validation review-queue seeding is preview-first: `python scripts/truth_validation_run.py --sample-limit 20 --indent 2` is no-mutation, confirmed execution requires `--execute --confirm-execute`, and rollback preview is `python scripts/truth_validation_rollback.py --run-id <run_id>`.
- Approved validation run `truth-preview-manual-20260514144735` wrote 40 open review items and can be preview-rolled back with `python scripts/truth_validation_rollback.py --run-id truth-preview-manual-20260514144735`.
- Manual evidence capture is preview-first through `truth_manual_evidence.py` or `POST /api/v1/truth/manual-evidence`; the CLI can also replay reviewed source-evidence preview JSON with `--payload-file`; single-payload execution requires `--execute --confirm-execute` or `dry_run=false&confirm_execute=true`, multi-payload CLI replay also requires `--confirm-batch-execute`, and the response includes claim/evidence/snapshot rollback manifest entries.
- Claim adjudication apply is preview-first through `truth_adjudication_apply.py` or `POST /api/v1/truth/adjudication/apply`; execution requires `--execute --confirm-execute` or `dry_run=false&confirm_execute=true`, skips unsafe fact groups, and records before-snapshot rollback manifest entries for any updated claims.

Mutating truth operations require explicit approval:

- Applying migrations through `010_truth_manifest` creates additive truth-confidence tables and still requires explicit approval in any database where they have not already been applied.
- Source refresh jobs mutate source tables and data-quality logs.
- Truth materialization writes claim/evidence/snapshot rows and must be run only after dry-run review with `dry_run=false&confirm_execute=true`; the rollback manifest must be present before execution.

## Environment Variables

```bash
PORT=8000
DATABASE_URL=postgresql+asyncpg://...
JWT_SECRET=...
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=...
TEST_USER_EMAIL=test@example.com
TEST_USER_PASSWORD=...
ANTHROPIC_API_KEY=...
GOOGLE_PLACES_API_KEY=...
HUNTER_API_KEY=...
REDIS_URL=redis://...
CORS_ORIGINS=https://...,http://localhost:5173
```

Notes:
- Enrichment batch jobs are queued through `/api/v1/jobs/enrichment/start` and dispatched to Celery when available, with in-process async fallback for local reliability.
- Canonical job lifecycle statuses are `queued`, `running`, `succeeded`, `failed` (legacy `completed` is normalized for compatibility).
- Jobs API accepts source-style aliases for reruns (for example `energy_grades` -> `energy`, `aep_designations` -> `aep`).
- Worker and queue health can be inspected at `GET /api/v1/jobs/worker-health`.
- DB pool utilization is exposed at `GET /api/health/db-pool`.

## Execution Readiness Focus

The production-critical convergence slice is now live:

1. Canonical PostgreSQL runtime path for lead generation and read models
2. Durable async jobs through Railway worker + Redis
3. Delivery confidence via regression tests, job observability, and production verification runs

See root session notes and changelog for the latest post-deploy cleanup status.

## Operations Runbooks

- `docs/08-operations-runbook.md`
- `docs/09-postgres-backup-restore.md`
