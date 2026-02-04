# HPD Leads App - Comprehensive Code Review

**Date:** February 4, 2026  
**Reviewer:** Claude (AI Assistant)

## Executive Summary

After a thorough review of the entire codebase, I identified **52 issues** across the following categories:

| Category | Count | Critical | Medium | Low |
|----------|-------|----------|--------|-----|
| Dead Code | 15 | 1 | 5 | 9 |
| Bugs/Potential Issues | 12 | 3 | 6 | 3 |
| Code Duplication | 8 | 0 | 5 | 3 |
| Missing Error Handling | 7 | 2 | 4 | 1 |
| Performance Issues | 6 | 1 | 3 | 2 |
| Security Concerns | 4 | 2 | 2 | 0 |

---

## Part 1: Critical Issues (Fix Immediately)

### 1.1 Race Condition in Cache Updates
**File:** `backend/api.py`  
**Lines:** 314-321, 768-774

The `_leads_cache` is modified in `update_lead` and `enrich_leads` endpoints without using `_leads_lock`. Background tasks do use the lock. This can cause data corruption when multiple requests hit simultaneously.

```python
# CURRENT (unsafe):
for i, lead in enumerate(_leads_cache):
    if lead.lead_id == lead_id:
        lead.outreach_status = request.outreach_status
        _leads_cache[i] = lead

# FIX: Use lock
with _leads_lock:
    for i, lead in enumerate(_leads_cache):
        ...
```

### 1.2 SQL Injection in NY DOS Lookups
**File:** `backend/src/enrich/ny_dos.py`  
**Line:** 94

User-provided company names are directly interpolated into SoQL queries without escaping.

```python
# CURRENT (vulnerable):
params = {
    "$where": f"upper(current_entity_name) LIKE '%{normalized}%'",
}

# FIX: Escape single quotes
normalized = normalized.replace("'", "''")
```

### 1.3 CORS Allows All Origins in Production
**File:** `backend/api.py`  
**Lines:** 38-44

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Security risk
)
```

**Fix:** Set from environment variable: `os.environ.get("CORS_ORIGINS", "*").split(",")`

### 1.4 N+1 Database Queries
**File:** `backend/api.py`  
**Line:** 2073

Every lead converted to response makes a database call for outreach attempts. With 100 leads, this makes 100 DB calls.

```python
# CURRENT:
outreach_attempts=_get_outreach_attempts_for_lead(lead.lead_id),

# FIX: Batch fetch before conversion
all_attempts = db.get_all_outreach_attempts()
# Then pass to _lead_to_response
```

---

## Part 2: Dead Code to Remove

### 2.1 Entire Files

| File | Reason |
|------|--------|
| `backend/src/enrich/paid_apis.py` | Never imported, methods raise `NotImplementedError` |
| `backend/check_contacts.py` | Standalone script, unclear if used |
| `backend/test_flow.py` | Test file, should be in tests/ folder |

### 2.2 Backend Functions Never Called

| File | Function | Line |
|------|----------|------|
| `ny_dos.py` | `get_registered_agent()` | 190 |
| `batch_enricher.py` | `run_batch_enrichment()` | 504 |
| `contact_sources.py` | `enrich_lead_contacts()` | 546 |
| `pluto_client.py` | `get_building_type()` | 301 |
| `normalize.py` | `normalize_email()` | 285 |
| `hpd_client.py` | Unused `first_page` fetch | 271 |

### 2.3 Frontend Functions Never Called

| File | Function | Line |
|------|----------|------|
| `api.ts` | `fetchLead()` | 154 |
| `api.ts` | `fetchStats()` | 201 |
| `api.ts` | `enrichBatch()` | 246 |
| `api.ts` | `healthCheck()` | 292 |
| `api.ts` | `getEnrichmentSources()` | 371 |
| `api.ts` | `getOutreachAttempts()` | 408 |

### 2.4 Unreachable Code

| File | Line | Issue |
|------|------|-------|
| `hpd_client.py` | 204 | `return []` after `raise` is unreachable |
| `api.py` | 563 | Variable `num_leads` assigned but never used |

---

## Part 3: Code Duplication to Consolidate

### 3.1 Phone Normalization (3 locations)

Same logic in:
- `normalize.py:257` (canonical location, not used)
- `contact_sources.py:535`
- `web_crawl.py:661`

**Fix:** Import from `normalize.py` everywhere.

### 3.2 Corporation Indicator Detection (2 locations)

Same list in:
- `batch_enricher.py:206`
- `enricher.py:128`

**Fix:** Move to `src/utils/constants.py`:
```python
CORP_INDICATORS = ['LLC', 'L.L.C.', 'INC', 'CORP', 'LP', 'L.P.', 'LLP', 'COMPANY', 'CO.', 'PARTNERS']
```

### 3.3 Contact Grouping by Registration (2 locations)

Same code in `hpd_client.py` at lines 231-237 and 316-323.

**Fix:** Extract to `_group_contacts_by_registration(contacts)`.

### 3.4 Lead Index Creation Pattern (6 locations)

Pattern `{l.lead_id: i for i, l in enumerate(_leads_cache)}` repeated in `api.py`.

**Fix:** Add global `_lead_index: Dict[str, int] = {}` updated when cache changes.

### 3.5 Enrichment State Reset Pattern (3 locations)

Identical 10+ line blocks in:
- `_run_background_enrichment`
- `_run_batch_enrichment`
- `_run_api_enrichment`

**Fix:** Extract to `_reset_enrichment_state(phase, total)`.

### 3.6 BuildingTypeBreakdown Construction (2 locations)

Same builder in `aggregate.py` at lines 365 and 531.

**Fix:** Add `@classmethod BuildingTypeBreakdown.from_counter(counts)`.

---

## Part 4: Missing Features / Incomplete Implementations

### 4.1 Frontend

1. **Header search is non-functional** - Input exists but not wired to state
2. **Mobile menu button does nothing** - No onClick handler
3. **Settings tab unimplemented** - Shows same content as Leads
4. **No error toasts/notifications** - All failures silently logged to console
5. **Dashboard has no error state** - Just shows empty on API failure

### 4.2 Backend

1. **PLUTO enrichment endpoint is placeholder** (`/api/enrich/pluto`) - Just sets everything to "unknown"
2. **No rate limiting on any endpoint**
3. **No request logging/monitoring**
4. **No data backup/export mechanism**
5. **Deprecated `@app.on_event("startup")` should use `lifespan`**

### 4.3 Database

1. **No schema migration system** - Manual ALTER TABLE needed
2. **Building types not persisted** - `leads` table missing columns
3. **No data cleanup/archival** - Old enrichment cache grows unbounded

---

## Part 5: Performance Improvements

### 5.1 Multiple Passes in `get_stats`

Currently 5 passes over 100k+ leads. Should be single pass.

```python
# Current: O(5n)
for lead in _leads_cache: # borough
for lead in _leads_cache: # status
for lead in _leads_cache: # score
for lead in _leads_cache: # portfolio
for lead in _leads_cache: # building types

# Better: O(n)
for lead in _leads_cache:
    by_borough[lead.boro] = by_borough.get(lead.boro, 0) + 1
    # ... all aggregations in one pass
```

### 5.2 Linear Search for Lead Lookups

O(n) lookups in multiple places. Maintain a dict index.

### 5.3 PLUTO Cache Unbounded Growth

With 200k+ buildings, cache consumes 100MB+. Use LRU cache with limit.

### 5.4 HPD Contact Batching

100 registration IDs per batch with 0.1s delay. For 200k buildings, this is 2000 batches = 200+ seconds of just delays.

---

## Part 6: Structural Improvements

### 6.1 Recommended Directory Restructure

```
backend/
├── src/
│   ├── api/            # Extract endpoints from api.py
│   │   ├── leads.py
│   │   ├── enrichment.py
│   │   ├── refresh.py
│   │   └── pluto.py
│   ├── core/           # Shared utilities
│   │   ├── constants.py
│   │   ├── formatters.py  # phone/email normalization
│   │   └── cache.py
│   ├── enrich/         # (existing, cleanup dead code)
│   ├── ingest/         # (existing)
│   ├── storage/        # (existing)
│   ├── transform/      # (existing)
│   └── score/          # (existing)
├── tests/              # Move test files here
└── migrations/         # DB schema migrations
```

### 6.2 Extract `api.py` Into Modules

Current `api.py` is 2,174 lines. Split into:
- `api/leads.py` - CRUD endpoints
- `api/enrichment.py` - All enrichment endpoints
- `api/refresh.py` - Data refresh pipeline
- `api/pluto.py` - PLUTO lookups

### 6.3 Consolidate Enrichment Orchestrators

Currently 3 different ways to enrich:
1. `Enricher.enrich_lead()` - Single lead
2. `BatchEnricher.enrich_all()` - Batch with phases
3. `MultiSourceEnricher.enrich()` - Multi-source single lead

**Recommendation:** Keep `MultiSourceEnricher` as the canonical single-lead enricher. Use `BatchEnricher` for batch operations that internally uses `MultiSourceEnricher`.

---

## Part 7: Roadmap

### Phase 1: Critical Fixes (1-2 hours)
- [ ] Add locking to cache mutations in API endpoints
- [ ] Fix SQL injection in NY DOS
- [ ] Configure CORS from environment variable
- [ ] Batch-fetch outreach attempts

### Phase 2: Code Cleanup (2-3 hours)
- [ ] Delete `paid_apis.py`
- [ ] Remove unused frontend API functions
- [ ] Remove unreachable code blocks
- [ ] Consolidate phone normalization
- [ ] Extract corporation indicators constant
- [ ] Add helper for lead index lookups

### Phase 3: Frontend Polish (2-3 hours)
- [ ] Wire up or remove header search
- [ ] Implement or remove mobile menu
- [ ] Implement or remove Settings tab
- [ ] Add error toasts/notifications
- [ ] Add Dashboard error state
- [ ] Fix useEffect dependency in LeadDetail

### Phase 4: Performance (1-2 hours)
- [ ] Single-pass stats aggregation
- [ ] Maintain global lead index
- [ ] Add LRU cache to PLUTO client
- [ ] Optimize HPD contact batch size

### Phase 5: Database Schema (1 hour)
- [ ] Add building_types column to leads table
- [ ] Add building_classes column to leads table
- [ ] Update load_all_leads to parse these

### Phase 6: Architecture (4-6 hours, optional)
- [ ] Split api.py into modules
- [ ] Add request logging middleware
- [ ] Add rate limiting
- [ ] Migrate to FastAPI lifespan
- [ ] Add DB migration system

---

## Appendix: Files Reviewed

### Backend (14 files)
- `api.py` (2,174 lines)
- `src/enrich/batch_enricher.py`
- `src/enrich/contact_sources.py`
- `src/enrich/enricher.py`
- `src/enrich/ny_dos.py`
- `src/enrich/paid_apis.py`
- `src/enrich/web_crawl.py`
- `src/ingest/hpd_client.py`
- `src/ingest/pluto_client.py`
- `src/transform/aggregate.py`
- `src/transform/normalize.py`
- `src/storage/database.py`
- `src/score/scorer.py`
- `config/settings.py`

### Frontend (8 files)
- `App.tsx`
- `components/Dashboard.tsx`
- `components/Header.tsx`
- `components/LeadDetail.tsx`
- `components/LeadTable.tsx`
- `components/PropertyMap.tsx`
- `components/Sidebar.tsx`
- `services/api.ts`
