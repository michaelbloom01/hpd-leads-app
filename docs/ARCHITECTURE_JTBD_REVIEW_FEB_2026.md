# Double Edge Architecture Review (JTBD + Best Practices)

## Scope

This review evaluates whether the current architecture delivers the two core jobs to be done (JTBD) and where structural improvements are required for consistent, reliable behavior.

## JTBD Alignment Verdict

- **JTBD 1 (PE/Acquirer):** Identify, qualify, and prioritize PM businesses for acquisition.
  - **Current state:** Mostly achieved via Leads list/detail, enrichment, scoring, and Smart Lists.
  - **Risk:** Some fields are dynamically hydrated and can drift if data contracts regress.
- **JTBD 2 (PM Operator):** Identify high-churn buildings and execute outreach.
  - **Current state:** Mostly achieved via Buildings list/detail, churn scoring, and outreach tracking.
  - **Risk:** Some signal sources are represented only as aggregate score contributions, not first-class timeline data.

Overall: the product architecture is directionally strong and functionally useful, but reliability and observability need tighter source-to-UI contracts.

## Best Practices Assessment

Strengths:

- PostgreSQL-first async backend with job orchestration and health checks.
- Clear API layer separation for leads, buildings, jobs, quality, alerts.
- Frontend has robust UX error handling, loading states, and fallback behaviors.
- Test coverage exists for critical regression points and contracts.

Gaps:

- No canonical source integrity matrix exposed to users/operators.
- Configured source registry and runnable ingestion paths can drift.
- Record-level lineage/provenance is not surfaced, making missing-data diagnosis slow.
- Some sources are integrated only indirectly through churn scores.

## Changes Implemented in This PR

1. Added a backend **source integrity audit endpoint** (`/api/v1/quality/source-audit`) that reports:
   - configured source metadata (dataset, table, job, UI surface),
   - runnable job coverage,
   - table existence,
   - latest ingest evidence,
   - operational status (`operational`, `no_recent_ingest`, `not_wired`, `schema_missing`),
   - critical gaps list.
2. Added a frontend **Source Integrity Matrix** panel in Settings > Data Health:
   - summary counts,
   - highlighted critical gaps,
   - per-source matrix for operational transparency.

## Recommended Next Steps (Priority Order)

1. Add **record-level lineage endpoints** for lead and building detail (`/lineage`).
2. Close known source wiring gap for `dof_assessment` (implement ingest + schema) or explicitly deprecate.
3. Extend building detail timeline to include currently score-only sources where user value is high.
4. Add contract tests that fail on source drift (configured vs runnable vs surfaced).
5. Define source freshness SLAs and alerting thresholds per source category.

## Acceptance Criteria for “Holistically Reliable”

- Every configured source is either operational or explicitly deprecated.
- Every user-visible metric has traceable source lineage.
- Missing data states are explained in UI (not silent zeros).
- Source drift is detected automatically in tests/health checks before release.
