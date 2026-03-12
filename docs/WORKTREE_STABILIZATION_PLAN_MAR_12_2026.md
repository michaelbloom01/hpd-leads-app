# Worktree Stabilization Plan

## Goal

Undirty the current `hpd-leads-app` worktree from Mar 12, 2026 by turning today's mixed changes into a small number of coherent, validation-backed workstreams that can be reviewed, committed, and shipped safely without losing progress.

## Current Situation

The worktree currently mixes:

- Leads UX and routing changes
- batch action backend/API changes
- lead data contract fixes
- Smart Lists UX changes
- documentation/context updates

This is workable, but it is too broad to treat as one undifferentiated change set.

## Principles

- Do not use destructive git commands to "clean up" the tree.
- Preserve all current in-progress code until it has been grouped and validated.
- Split by behavior and blast radius, not by timestamp.
- Separate docs/meta changes from product/runtime changes.
- Avoid starting canonical entity work until the current runtime/UI tranches are stabilized.

## Proposed Workstreams

### 1. Leads Workspace UX

Files:

- `frontend/App.tsx`
- `frontend/components/LeadTable.tsx`
- `frontend/components/leads/LeadKanban.tsx`
- `frontend/components/LeadKanban.test.tsx`
- `frontend/hooks/useFilterUrl.ts`
- `frontend/hooks/useLeadFilters.ts`
- `frontend/services/api.ts`
- `frontend/components/Dashboard.tsx`
- `frontend/components/LeadDetail.tsx`

Scope:

- durable leads state
- route-backed lead detail and deep-link entry points
- kanban bulk-selection parity
- frontend batch action wiring

Validation:

- targeted `vitest` for `LeadKanban`
- frontend build
- manual browser pass on list -> detail -> back -> selection -> batch flow

### 2. Lead Runtime/Data Contract Hardening

Files:

- `backend/src/routers/leads.py`
- `backend/src/services/lead_generation.py`
- `backend/src/schemas/requests.py`
- `backend/src/tasks/enrich.py`
- `backend/tests/test_normalization_contract.py`
- `backend/tests/test_lead_batch_endpoints.py`

Scope:

- `building_types` materialization
- batch pipeline-stage endpoint hardening
- selected-enrichment dispatch hardening
- regression coverage for those paths

Validation:

- `backend/tests/test_normalization_contract.py`
- `backend/tests/test_lead_generation_runtime.py`
- `backend/tests/test_lead_batch_endpoints.py`

### 3. Smart Lists Product Surface

Files:

- `frontend/components/SmartListsPage.tsx`
- `frontend/services/api.ts`

Scope:

- direct Smart List authoring from `SmartListsPage`
- filter summary visibility
- reduced dependence on "create empty list, then go elsewhere"

Validation:

- frontend build
- manual create/open/evaluate/pin flow

### 4. Docs And Session Context

Files:

- `CHANGELOG.md`
- `CLAUDE_CONTEXT.md`
- `MEMO.md`
- `README.md`
- `backend/CLAUDE.md`
- `backend/README.md`
- `backend/tasks/lessons.md`
- `backend/tasks/todo.md`
- `docs/SESSION_NOTES.md`

Scope:

- notes
- context
- changelog/documentation updates

Validation:

- review for accuracy against actual shipped behavior

## Cleanup Sequence

### Step 1. Freeze risky new scope

Before starting canonical entity work or schema-heavy map work:

- stabilize the three runtime/product workstreams above
- keep any new work isolated from docs/meta churn

### Step 2. Validate each workstream independently

Run only the tests/builds relevant to that workstream:

- backend contract tests for runtime/data work
- frontend build and targeted UI tests for leads/list work
- manual sanity pass for Smart Lists behavior

### Step 3. Review diffs by workstream

Use path-based review buckets:

- Leads workspace UX
- lead runtime/data contract
- Smart Lists surface
- docs/meta

### Step 4. Commit in this order when requested

1. lead runtime/data contract hardening
2. Leads workspace UX and batch parity
3. Smart Lists product surface
4. docs/meta updates

This order minimizes rollback pain and keeps backend contracts ahead of frontend assumptions.

### Step 5. Only then start the next major tranche

After the current tree is stabilized, the next roadmap tranche should be:

- Phase 3 geospatial persistence and map provenance

Not yet:

- canonical entity / duplicate model overhaul

Reason:

- geospatial persistence is schema-heavy but conceptually local
- canonical entity work has the highest workflow-state and auditability risk

## Next Major Tranche Plan

### Phase 3A. Persist Building Coordinates

Add to `buildings`:

- `latitude`
- `longitude`
- `coordinate_source`
- `coordinate_precision`
- `coordinates_updated_at`

### Phase 3B. Ingest/Backfill Coordinates

Add a background backfill path that:

- geocodes from a stable NYC-first source
- stores provenance
- rate-limits and retries safely
- never blocks page render

### Phase 3C. Switch UI To Persisted Coordinates

Update:

- `frontend/components/PortfolioMap.tsx`
- any building/lead detail map surfaces

Behavior:

- prefer persisted coordinates
- visually distinguish exact vs approximate if approximation still exists
- stop browser-primary geocoding for normal display

### Phase 3D. Add Validation

- backend tests for coordinate persistence/provenance
- frontend build
- manual portfolio map checks on known buildings/leads

## Explicit Do-Not-Do List

- do not run dedupe or canonical merge jobs yet
- do not rewrite the current Leads route model again until the worktree is cleaner
- do not mix geospatial schema work with canonical entity work in one tranche
- do not bundle docs-only changes into runtime validation assumptions

## Exit Criteria For "Undirtied"

The Mar 12 worktree is considered stabilized when:

- each workstream above has passed its own validation
- each workstream is reviewable on its own
- docs/meta are separated from runtime behavior
- the next tranche can start without relying on unstated context from earlier today
