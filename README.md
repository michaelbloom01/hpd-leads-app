# Double Edge

NYC property management intelligence platform with dual purpose: **PE acquisition sourcing** (identify PM companies as acquisition targets) and **PM operator lead generation** (find buildings ripe for outreach). Built on HPD public data covering 200k+ buildings.

**Live App:** https://frontend-nine-psi-58.vercel.app  
**Backend API:** https://hpd-leads-app-production.up.railway.app  
**GitHub:** https://github.com/michaelbloom01/hpd-leads-app

## What It Does

1. **Fetches ALL buildings** from NYC HPD database (200k+ buildings)
2. **Joins with PLUTO data** for building classification (condo, coop, rental, etc.)
3. **Materializes lead entities from building contacts** to create the current live lead surface
4. **Classifies entities** as Company, Individual Agent, or Owner-Operator
5. **Estimates revenue** per lead based on units, borough, and building type
6. **Integrates HPD violations** as distress/opportunity signals
7. **Scores leads (V2)** using 8 dimensions: portfolio, units, professional, contact, concentration, revenue, distress, deal fit
8. **Enriches contacts** using 4-tier cascade: Google Places -> NY DOS -> Web Crawl -> Hunter.io
9. **Smart Lists** — saved filter segments that track lead changes over time
10. **Building Lists** — saved collections of buildings for outreach workflows
11. **Full sourcing UI** with server-side filtering, pipeline tracking, follow-ups, and bookmarkable filter URLs

## Current Status (June 2026)

| Metric | Value |
|--------|-------|
| Production Leads | 314,723 |
| Production Buildings | 181,307 |
| Production Entity Coverage Ratio | 96.9% |
| Production Zero-Link Leads | 55,804 |
| Production Buildings With Multiple Current PM Links | 106,759 |
| Production Stale Leads | 314,719 |
| Production Contact Coverage | 0.0% phone / 0.0% email / 0.0% website |
| Local Stabilization Branch Leads | 38,495 |
| Local Stabilization Branch Buildings | 179,985 |
| Local Truth-Confidence Posture | Draft PR #5 packages the read-only truth/confidence workbench; schema applied at `010_truth_manifest`; current local posture is 2,115 claims, 2,078 fact groups, 15 multi-source/source-ready groups, 0 verified claims, 40 open review items, and `trust_posture=not_ready`; business use remains blocked until verified-claim, review, source freshness, activation, and production truth-surface gates pass |
| Entity Classification | Company / Individual Agent / Owner-Operator |
| Building Type Coverage | 100% (PLUTO data) |
| Scoring | V2: 8-dimension |
| Enrichment Sources | Google Places, NY DOS, Web Crawl, Hunter.io |
| Revenue Estimation | Borough/type-adjusted, 5% mgmt fee |
| Violations Data | HPD Violations (Class A/B/C, per-unit normalized) |
| Pipeline Stages | Research -> First Contact -> Follow-Up -> Meeting -> LOI -> DD -> Closed |
| Smart Lists | Saved filter segments with change detection |

Production note: the normal Railway worker path for `lead_generation`, `lead_reconciliation`, and `quality_checks` is working, but the Data Truth & Confidence API surface from the active stabilization branch is not deployed to production yet. Production currently returns `404` for `/api/v1/truth/schema-status` and `/api/v1/truth/activation-packet`; local truth schema is applied, but activation remains approval-gated and should not be used for business decisions until materialization, review, and source-refresh gates clear.

## Data Truth & Confidence Program

The active stabilization branch adds a Data Truth & Confidence foundation so Double Edge can move from lead rows as raw truth to evidence-backed beliefs over canonical entities, buildings, contacts, relationships, and outreach feedback.

Core additions:

- Canonical truth tables for claims, evidence, confidence snapshots, review items, validation runs, golden verification cases, and materialization rollback manifests.
- Claim/evidence summaries for leads, buildings, canonical entities, entities, contacts, HPD contacts, and people.
- Confidence scoring that accounts for source quality, agreement, contradiction, freshness, and claim risk.
- Actionability thresholds for broad discovery, ranked sourcing, automated enrichment, recommended outreach, acquisition-quality diligence, and do-not-act states.
- Source audit and activation packet APIs that expose schema readiness, stale/missing evidence, refreshable/blocked jobs, rollback guidance, and business-use gates.
- Human review queues and dry-run review decisions that show support, contradiction, rationale, and proposed database changes before execution.
- Outreach feedback conversion so bounces, wrong numbers, bad emails, "does not manage," confirmations, and referrals become support or contradiction evidence.
- Golden benchmark seeds for co-op boards, owner LLC shells, stale agents, legal suffix variants, shared addresses, false splits, and outreach contradictions.

Activation is intentionally conservative:

```bash
cd backend
python scripts/truth_migration_preflight.py
python scripts/truth_activation_packet.py
python scripts/truth_health_report.py --materialization-limit 50 --validation-sample-limit 10
python scripts/truth_adjudication_preview.py --limit 20 --indent 2
python scripts/truth_production_probe.py
python scripts/truth_completion_audit.py --include-runtime --include-production
python scripts/truth_completion_audit.py --artifacts-only
```

Applying migrations through `010_truth_manifest` in any new database or environment, refreshing stale sources, and executing truth materialization are mutating operations and require explicit approval with the documented dry-run/confirm gates. In the active local database, `009` and `010` were applied with approval on 2026-05-14; the schema rollback protocol is `python -m alembic downgrade 010_truth_manifest:008_lead_lineage --sql` for review, then the matching Alembic downgrade if activation must be abandoned before business use.

The active local DB was then advanced to `010_truth_manifest` to add `truth_materialization_manifest`, a per-run rollback manifest for claim/evidence/snapshot upserts. Pilot run `truth-materialization-manual-20260514142022` wrote 2,063 claims, 2,063 evidence rows, and 1,088 confidence snapshots; rollback dry-run is `python scripts/truth_materialization_rollback.py --run-id truth-materialization-manual-20260514142022`.

Truth validation dry-runs are no-mutation previews. To seed human review queues from adversarial validation, first run `python scripts/truth_validation_run.py --sample-limit 20 --indent 2`, then execute only after review with `--execute --confirm-execute`. Validation review-queue rollback is run-scoped: `python scripts/truth_validation_rollback.py --run-id <run_id>` previews deletion of still-open review items and preserves reviewed decisions by default.

Approved local validation run `truth-preview-manual-20260514144735` seeded 40 open review items: 20 `insufficient_evidence` zero-link lead cases and 20 `needs_human_review` legal/mailbox-style address cases. Its rollback dry-run is `python scripts/truth_validation_rollback.py --run-id truth-preview-manual-20260514144735`; current preview would delete 40 open review items and the validation-run envelope, with 0 reviewed items at risk.

## Architecture Execution Readiness (Feb 24, 2026)

Before major feature expansion, the project is executing a holistic architecture convergence program:

- Runtime convergence to one canonical PostgreSQL path
- Durable async processing via queue + worker for long-running jobs
- Delivery confidence baseline (migration safety + critical-path tests + CI gates)

This is an architecture simplification effort and does not reduce product JTBD scope.

## Key Features

### Dashboard
- Key metrics at a glance (total leads, enriched count, top scores)
- "Ready to Contact" quick-access card
- Portfolio size and units/building charts
- Entity type distribution
- Change alerts and follow-up reminders

### Lead Table
- Filter by borough, score, portfolio size, units, units/building, entity type, pipeline stage, building type
- Multi-borough selection, phone/email/website filters
- URL-persisted filters (bookmarkable, shareable)
- "Save as Smart List" to track filter segments over time
- Bulk selection and export to CSV
- Revenue and violations columns
- Server-side sorting including computed columns (units/building)

### Lead Detail Modal
- Contact info front and center — phone, email, website with one-click actions
- Revenue estimate and violation summary
- Pipeline stage management with follow-up dates
- Portfolio composition (condos, coops, rentals breakdown)
- Quick Risk Snapshot on Due Diligence tab
- AI-generated company summaries
- Outreach event logging with email templates
- Keyboard accessible: ESC to close, Tab focus trapping, ARIA labels

### Smart Lists
- Save any filter combination as a named Smart List
- Evaluate to detect which leads entered/exited since last run
- Pin favorites to keep them at the top
- Open in Leads page to apply saved filters instantly
- Change alerts when list composition shifts

### Building Lists
- Create named building collections from the Buildings table
- Add/remove buildings by BBL
- Manage lists from dedicated `/building-lists` page
- Open building detail directly from list members

### Buildings Tab
- Building-level search and filtering
- Churn score and outreach pipeline per building
- CSV export

### AI Agent
- Natural language query interface (Cmd+K)
- Lead lookups, script generation, briefing emails
- Conversation history

## Architecture

```
hpd-leads-app/
├── backend/              # Python FastAPI (Railway)
│   ├── api.py            # REST API entry point
│   ├── src/
│   │   ├── routers/      # leads, buildings, smart_lists, admin, etc.
│   │   ├── ingest/       # HPD, PLUTO, Violations API clients
│   │   ├── transform/    # Normalize & aggregate to leads
│   │   ├── score/        # Scoring V2 + revenue estimation
│   │   ├── enrich/       # Google Places, NY DOS, Hunter, Web Crawl
│   │   ├── db/           # Async SQLAlchemy session (PostgreSQL)
│   │   └── agent/        # AI Agent with tools
│   └── config/           # Scoring weights YAML
├── frontend/             # React + TypeScript (Vercel)
│   ├── components/       # Dashboard, LeadTable, LeadDetail, SmartListsPage
│   ├── hooks/            # useLeadFilters, useFilterUrl
│   └── services/         # API client with retry + error classification
└── docs/                 # Archived reviews
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/leads` | GET | Get leads with filtering + pagination |
| `/api/leads/{id}` | GET | Get single lead |
| `/api/leads/{id}` | PATCH | Update status, pipeline, follow-up, priority |
| `/api/leads/{id}/estimate-revenue` | POST | Estimate and persist revenue for a lead |
| `/api/leads/{id}/enrich-all` | POST | Unified enrichment (contacts + research + AI) |
| `/api/leads/{id}/outreach-event` | POST | Log outreach event |
| `/api/stats` | GET | Detailed statistics |
| `/api/follow-ups` | GET | Leads with follow-ups due |
| `/api/alerts` | GET | Change detection alerts |
| `/api/smart-lists` | GET/POST | List or create Smart Lists |
| `/api/smart-lists/{id}` | GET/PATCH/DELETE | CRUD for a Smart List |
| `/api/smart-lists/{id}/evaluate` | POST | Re-run filters, detect changes |
| `/api/v1/building-lists` | GET/POST | List or create Building Lists |
| `/api/v1/building-lists/{id}` | PATCH/DELETE | Rename or delete a Building List |
| `/api/v1/building-lists/{id}/buildings/{bbl}` | POST/DELETE | Add or remove a building from list |
| `/api/v1/building-lists/{id}/buildings` | GET | List buildings in a Building List |
| `/api/enrich/batch` | POST | Start batch enrichment |
| `/api/estimate-revenue` | POST | Bulk revenue estimation |
| `/api/violations/refresh` | POST | Fetch HPD violations |
| `/api/rescore` | POST | Re-score all leads (V2) |
| `/api/v1/export/leads/csv` | GET | Export leads to CSV |
| `/api/v1/export/buildings/csv` | GET | Export buildings to CSV |
| `/api/v1/truth/schema-status` | GET | Read-only truth schema readiness and Alembic revision status |
| `/api/v1/truth/leads/{lead_id}/summary` | GET | Evidence-backed lead belief, confidence, contradiction, freshness, and safe-action summary |
| `/api/v1/truth/subjects/{subject_type}/{subject_id}/summary` | GET | Evidence-backed truth summary for lead, canonical entity, entity, building, contact, HPD contact, or person subjects |
| `/api/v1/truth/dashboard` | GET | Claim, confidence, review, actionability, and threshold dashboard |
| `/api/v1/truth/health-report` | GET | Read-only truth-health report with activation checklist and trust gaps |
| `/api/v1/truth/activation-packet` | GET | Compact no-mutation approval packet for schema/materialization/source-refresh activation |
| `/api/v1/truth/adjudication-preview` | GET | Read-only claim-fact adjudication preview showing verification candidates and blockers |
| `/api/v1/truth/materialize/preview` | POST | Dry-run claim/evidence materialization preview |
| `/api/v1/truth/validate/preview` | POST | Dry-run adversarial validation preview |
| `/api/v1/truth/review-queue` | GET | Human review queue for suggested merges, conflicts, insufficient evidence, and do-not-merge cases |
| `/api/v1/truth/golden-benchmark` | GET | Golden-set precision/recall/false-merge/false-split/link/contact/freshness benchmark |

## Local Development

### Backend

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn api:app --reload --port 8000
# API at http://localhost:8000
# Docs at http://localhost:8000/api/docs
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# UI at http://localhost:5173
```

## Environment Variables

### Backend (Railway)

```
DATABASE_URL=postgresql+asyncpg://...     # PostgreSQL connection
REDIS_URL=redis://...                      # Redis broker/result backend for Celery
JWT_SECRET=...                             # JWT signing secret
ADMIN_EMAIL=admin@example.com              # bootstrap admin user (optional)
ADMIN_PASSWORD=...                         # bootstrap admin password
TEST_USER_EMAIL=test@example.com           # bootstrap shared test user (optional)
TEST_USER_PASSWORD=...                     # bootstrap shared test password
ANTHROPIC_API_KEY=sk-ant-...              # AI summaries
GOOGLE_PLACES_API_KEY=AIza...             # Google Places enrichment
HUNTER_API_KEY=...                        # Hunter.io email finder
CORS_ORIGINS=https://your-frontend.vercel.app,http://localhost:5173
```

### Worker Service (Railway)

- Service name: `hpd-leads-worker`
- Uses same backend image with `WORKER_MODE=1`
- Requires: `REDIS_URL`, `DATABASE_URL`, and shared API keys as needed by task modules

### Frontend (Vercel)

```
VITE_API_URL=https://hpd-leads-app-production.up.railway.app
```

## Data Sources

- **HPD Buildings:** `https://data.cityofnewyork.us/resource/tesw-yqqr.json`
- **HPD Contacts:** `https://data.cityofnewyork.us/resource/feu5-w2e2.json`
- **HPD Violations:** `https://data.cityofnewyork.us/resource/wvxf-dwi5.json`
- **PLUTO (Building Classes):** `https://data.cityofnewyork.us/resource/64uk-42ks.json`
- **NY DOS Corporations:** `https://data.ny.gov/resource/n9v6-gdp6.json`

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for release history. Session notes are in [docs/SESSION_NOTES.md](docs/SESSION_NOTES.md).

## License

Private - Michael Bloom
