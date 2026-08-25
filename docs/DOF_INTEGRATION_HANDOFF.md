# DOF Assessment Roll Integration — Handoff

**Date:** 2026-07-26
**Status:** Build complete and tested. **463 tests pass** (129 new, 334 pre-existing, zero regressions).

Migration `011_dof_assessment` is **written but NOT APPLIED**. It is additive only —
no drops, no retypes, every statement idempotent — and a contract test enforces that.
Applying it, backfilling, and any lead merge remain separate gated operations.
Production is untouched.

Local dev requires **Python 3.12** (`runtime.txt`). The system Python on this Mac is
3.9, which cannot even collect the suite — the codebase uses `X | None` union syntax
throughout. `brew install python@3.12` then rebuild `.venv`.

This is one of two threads split out of a single session. The other — acquisition
sourcing for appraisal firms — is at `~/brain/threads/appraisal-search/handoff-2026-07-26.md`
and is unrelated to this codebase.

---

## What this adds

The app ingests HPD Buildings, HPD Contacts, HPD Violations and PLUTO. It did
not touch the **NYC DOF property assessment roll** — a 139-field, per-lot annual
record covering all ~1.18M NYC tax lots, FY2023–FY2027. `Building.assessed_value`
was the only DOF-derived field in the schema: one float where 139 columns exist.

### Dataset selection — read this before touching anything

Six near-identically named assessment datasets exist on NYC Open Data. **Five are
abandoned and carry a thinner column set.**

| Dataset | Status |
|---|---|
| **`8y4t-faws`** | **USE THIS.** Full 139-field layout, refreshed 2026-06-15 (FY2027 final roll) |
| `yjxr-fw8i` | Stale — last data refresh 2020 |
| `cqds-77ys`, `qpsp-bm9z`, `kevu-8hby`, `m8p6-tp4b` | Stale 2022 |
| `rgy2-tti8` | Stale 2018 |

Roll periods: `period='1'` tentative (published January), `period='3'` final
(published May). Value prefixes: `PY` prior year, `TEN` tentative, `CBN`
change-by-notice, `FIN` final, `CUR` current.

Other datasets used:

| Dataset | Purpose | Freshness |
|---|---|---|
| `9ck6-2jew` | Condominium Comparable Rental Income | latest `report_year` 2023 |
| `myei-c3fa` | Cooperative Comparable Rental Income | latest `report_year` 2022 |
| `aht6-vxai` | Open Article 7 Petitions — carries `attorney_name` | 2026-05-19 |

Note: nyc.gov `/assets/finance/downloads/` returns **403 to default user agents**.
A browser UA header gets through. The Socrata API needs no such workaround.

---

## Files added

### `backend/src/ingest/dof_client.py`
Socrata client following the `pluto_client.py` conventions. `AssessmentRecord`
dataclass exposes derived signals rather than raw columns: `protest_reduction_pct`,
`exemption_share`, `value_change_pct`, `market_value_per_unit/sqft`,
`implied_noi(cap_rate)`, `rollup_key`. Batch fetch at ~400 BBLs per request;
`iter_roll()` pages the full roll in ~24 requests. Smoke-tested against live data.

### `backend/src/transform/portfolio_dedup.py`
Pure functions, no DB mutation. `portfolio_signature()` hashes a lead's BBL set;
`find_duplicate_leads()` groups collisions; `collapse_portfolio()` rolls condo/co-op
unit lots up via `coop_num` / `condo_number`; `merge_plan()` produces a dry-run plan.

### `backend/src/score/revenue_dof.py`
Reworked to the correct shape. Estimates **management company fee revenue**:

    revenue = doors x fee per door x 12

Two fee models, because there are two businesses. RENTAL: percentage of collected
rent (4-8%), so the per-door rate scales with rent. CONDO/CO-OP: the agent collects
no rent, so fees are per door ($50-150/unit/month) with a bounded quality modifier.
The earlier version applied a percentage-of-rent model to condo/co-op and overstated
by 2-3x.

Calibration round-trips: a building priced at its borough median value-per-unit
reproduces that borough's median rent — $3,699/mo Manhattan, $2,400 Brooklyn,
$1,826 Queens, $1,488 Bronx. Tested.

### `backend/alembic/versions/011_dof_assessment.py`
`building_assessments` table (PK `bbl, tax_year, period`) plus
`leads.portfolio_signature`. The signature index is deliberately **not unique** — a
collision is the signal to detect, not an error to reject at write time.

### `backend/src/models/assessment.py`
`BuildingAssessment` ORM model, registered in `src/models/__init__.py`. A contract
test asserts every model column exists in the migration DDL.

---

## Findings that justify the work

**Lead list is inflated ~17%.** Of the top 60 leads by portfolio size, 10 are exact
duplicates — byte-identical BBL sets under two `lead_id` rows each: Douglas Elliman
(307 lots), Andrews Organization (234), C&C (212), Guardian (195), Nieuw Amsterdam
(193), Choice NY (190), GPG (148), Bronstein (139), PHH (137), Rose (128). Name
matching misses these because the HPD corporation names genuinely differ. Set
hashing catches all ten exactly, no threshold.

**BBL join is sound.** 5,675 of 5,677 lead BBLs matched the DOF roll — 99.96%.

**Condo/co-op fragmentation is real.** DOF assigns each condo and co-op unit its own
tax lot, so a 200-unit building can appear as 200 BBLs. This biases every
portfolio-size filter and score in the app — overstating condo/co-op-heavy managers,
understating walk-up managers.

**Representation data exists and is dense.** 271,065 lots filed a Tax Commission
protest in FY2027; 270,241 carry a cert-attorney ID. `aht6-vxai` resolves those IDs
to firm names. Attorney group IDs are stable across years, so they work as an
entity-resolution fingerprint that survives LLC name obfuscation — potentially
useful to the truth layer, which is currently at `trust_posture=not_ready` with
0 verified claims.

**A correction to an earlier claim.** Tax Commission reductions land on **assessed**
value, not market value. Citywide FY2027 market value is essentially unchanged
tentative→final; 6,686 lots saw assessed value fall, $2.49B removed. And even that
understates it — the Tax Commission's 2025 report shows $3.95B granted, so most
settlements conclude after the final roll publishes and never appear. **Score on
`protest_1` presence (dense), not on the reduction delta (sparse).**

---

## Runbook — decisions locked 2026-07-26

Decisions taken: apply the migration; `portfolio_size` switches to the true
building count; duplicates are **flagged only, never merged**; per-door fee
constants stay as estimates for now.

Run in order. Everything is dry-run by default and needs `--execute
--confirm-execute` to write.

### 1. Apply the migration

```bash
cd backend && .venv/bin/python -m alembic upgrade 010_truth_manifest:011_dof_assessment --sql   # review
cd backend && .venv/bin/python -m alembic upgrade 011_dof_assessment                            # apply
```

Adds `building_assessments`, plus four nullable columns on `leads`:
`portfolio_signature`, `portfolio_signature_at`, `portfolio_size_raw`,
`true_building_count`. Additive only; contract-tested.

### 2. Backfill DOF assessments

```bash
export NYC_OPEN_DATA_APP_TOKEN=...
cd backend && .venv/bin/python scripts/dof_backfill.py --indent 2                       # dry run
cd backend && .venv/bin/python scripts/dof_backfill.py --execute --confirm-execute
```

Defaults to `--scope portfolio` (only BBLs already in `buildings`). Use
`--scope citywide --tax-class 2` for the full class 2 stock. Upsert is
idempotent, so a rerun refreshes rather than duplicates.

**Must run before step 3** — without rollup keys, `true_building_count` equals
the raw lot count and the rollup silently looks like a no-op. The script warns
if it detects this.

### 3. Recompute signatures and portfolio sizes

```bash
cd backend && .venv/bin/python scripts/portfolio_recompute.py --indent 2                # dry run
cd backend && .venv/bin/python scripts/portfolio_recompute.py --execute --confirm-execute
```

Writes exactly five columns and nothing else, asserted by test. Never retires,
supersedes or deletes a lead.

**This step is user-visible.** `portfolio_size` becomes the true building count,
so condo/co-op-heavy managers drop sharply — a manager showing 200 buildings may
show 3. Every saved Smart List and bookmarked filter URL was built against the
raw count and will re-evaluate.

Reversible two ways: `portfolio_size_raw` holds the original on every row, and
the migration downgrade restores it before dropping the column.

Read the dry-run output first — `largest_collapses` shows which leads move most.

---

## Still open

- **Duplicates are flagged, not merged.** Signatures land in step 3; surfacing
  them in the UI is not built. `merge_plan()` exists as a pure function but is
  wired to nothing, by decision.
- **Per-door fee constants are estimates.** `CONDO_COOP_FEE_PER_DOOR` in
  `revenue_dof.py` is the weakest input in the model — no public dataset of real
  management fees exists. Replacing it with real numbers off any management
  agreement would improve accuracy more than anything else. Until then no
  estimate claims better than medium confidence, and a test enforces that.
- **Revenue is not wired into V2 scoring.** `revenue_dof.py` is standalone.
  Wiring it changes every lead's score and needs a rescore.

---

## Testing

```bash
cd backend && .venv/bin/python -m pytest tests/ -q
```

**492 passed** — 158 new, 334 pre-existing, zero regressions.

| File | Tests | Covers |
|---|---|---|
| `test_revenue_dof.py` | 37 | Calibration round-trip, non-linearity in value, the two fee models, refusal cases, confidence tiers, aggregation |
| `test_portfolio_dedup.py` | 38 | Signature properties, duplicate detection, survivor selection, rollup, the measured 10-pair regression |
| `test_dof_client.py` | 40 | Parsing, `"0"`-as-null, assessed-not-market reduction, BBL construction, stale-dataset guard, caching |
| `test_dof_migration_contract.py` | 18 | Migration chain and single head, model/DDL agreement, additive-only and idempotent upgrade |
| `test_portfolio_recompute.py` | 25 | Rollup arithmetic, plan building, never-merges guarantee, reversibility, dry-run gating |

Three bugs were found by writing these tests, not by review:

1. `portfolio_signature(None)` raised `TypeError`. A lead with unloaded relations
   would have hit it.
2. The `"high"` confidence tier was **unreachable** — every class with a fitted
   adjustment is a condo/co-op class, so the condo/co-op branch always matched
   first. Removed; the reason is documented and asserted.
3. Test fixtures using arbitrary market values produced implausible rents, which
   is what surfaced that the fee model was linear in value. Fixtures are now
   anchored to borough medians so drift shows up as a failure.

### Environment

Needs **Python 3.12**. System Python 3.9 cannot collect the suite at all —
`src/logging_config.py:13` and much of the codebase use `X | None` union syntax.

```bash
brew install python@3.12
cd backend && /opt/homebrew/bin/python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

`.venv/` is already gitignored.

---

## Reproducing the analysis

Working scripts are in the session scratchpad (ephemeral — copy out if needed):
`cluster.py` (leads → HPD registrations → BBL → DOF attorney), `analyze.py`
(dedup + attorney clustering + unrepresented-operator screen).

Join path: `feu5-w2e2.registrationid` → `tesw-yqqr.registrationid` →
`boroid/block/lot` → BBL → `8y4t-faws.parid`.
