# Canonical Prep Plan — Mar 12, 2026

## Objective
Prepare the repo for future canonical entity / duplicate-model work without running merge/dedupe jobs yet.

This tranche is intentionally narrower than full entity resolution. Its job is to reduce ambiguity, improve auditability, and define safe execution rules before any write-heavy canonical merge touches `building_management` or mutates lead/building relationships.

## Why This Is The Next Safe Step
The runtime/UI/geospatial slices are now stabilized and validated. The remaining high-risk problem is not "missing a dedupe button"; it is that the current ambiguous tail is still too noisy to merge safely.

Current repo signals:
- `backend/CLAUDE.md` still calls out the ambiguous zero-link + blank-name tail.
- `backend/tasks/todo.md` still calls for a higher-confidence classifier and a decision on whether to reduce the 314k surface or expose better audit controls.
- `backend/src/tasks/entity_resolution.py` currently updates live lead/building relationships and is therefore too risky to run broadly without tighter confidence and rollback guardrails.

## Scope

### In Scope
1. **Ambiguous-tail classification prep**
   - Define cohorts for:
     - zero active building links
     - blank display-name leads
     - suspicious duplicate siblings
   - Create explicit confidence buckets:
     - safe_keep
     - safe_retire
     - review_required
     - unresolved

2. **Audit / lineage surfacing**
   - Make it easier to inspect why a lead would be merged, retired, or preserved.
   - Expose the signals used to reach that conclusion.

3. **Merge safety contract**
   - Define what must be true before canonical writes are allowed.
   - Define rollback and dry-run expectations.

4. **Cohort-first execution design**
   - Limit the eventual first canonical run to a narrow cohort with clear keeper logic.

### Out Of Scope
- Running `entity_resolution` on the full dataset
- Bulk lead retirement/deletion
- Rewriting the current Leads route model again
- Mixing canonical work with new geospatial or UX feature work

## Proposed Deliverables

### 1. Classifier Spec
Target files:
- `backend/src/services/lead_cleanup.py`
- `backend/src/tasks/entity_resolution.py`
- `backend/src/routers/quality.py`
- `backend/tasks/todo.md`

Deliver:
- A shared classifier contract for ambiguous leads/sibling groups
- Explicit confidence scoring inputs, for example:
  - normalized-name agreement
  - same-entity multi-link patterns
  - contact/company evidence
  - shared portfolio overlap
  - blank-display-name severity
  - zero-link persistence

### 2. Auditability Surface
Target files:
- `backend/src/routers/quality.py`
- `backend/src/routers/leads.py`
- `backend/src/tasks/entity_resolution.py`

Deliver:
- Dry-run outputs that explain:
  - why a cluster is mergeable
  - why it is blocked
  - which records would be preserved vs retired
- A small set of summary metrics for:
  - safe merge candidates
  - review-required candidates
  - unresolved ambiguous tail

### 3. Safety And Rollback Rules
Deliver:
- No canonical write unless dry-run metrics are captured first
- No merge of clusters lacking a clear keeper
- No mutation without recording old/new relationship counts
- First live tranche must be cohort-bounded and reversible

## Suggested Implementation Order

### Phase A. Add Visibility Before Action
- Add dry-run classification outputs and quality metrics
- Make sure operators can inspect why a candidate is blocked or mergeable

### Phase B. Define Safe Cohorts
- Identify the narrowest possible "safe_retire" and "safe_merge" groups
- Keep `review_required` and `unresolved` fully out of write paths

### Phase C. Add Cohort-Limited Execution Guards
- Gate any future entity-resolution execution behind:
  - explicit dry-run confirmation
  - cohort filter
  - safety counters
  - rollback logging

## Validation Requirements
Before any future canonical-write tranche starts:
- Contract tests for confidence bucket assignment
- Tests for dry-run metrics and no-write behavior
- Tests proving ambiguous groups remain untouched
- Tests proving rollback metadata is captured for eligible cohorts

## Exit Criteria
Canonical-prep is complete when:
- the ambiguous tail is partitioned into named confidence buckets
- dry-run outputs are reviewable without reading raw tables
- the first canonical write tranche can target a safe cohort only
- unresolved ambiguity is explicitly preserved instead of silently merged

## Recommended First Write Tranche After Prep
Only after the prep above is implemented:
- run canonical writes on the smallest cohort with:
  - zero active links
  - clear keeper identity
  - no same-role conflict
  - no blank-name ambiguity

Everything else should remain read-only until the dry-run metrics prove it is safe.
