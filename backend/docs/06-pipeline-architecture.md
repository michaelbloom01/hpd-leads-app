# Pipeline Architecture

> Note (Feb 24, 2026): This document includes legacy "Publish to Sheets" architecture. Treat it as historical reference. Current execution baseline is API-first with PostgreSQL canonical storage and queued background jobs.

## Current End-to-End Flow (Canonical)

1. Ingest public + enrichment data sources
2. Normalize and aggregate into lead/building models
3. Score/classify and persist workflow state in PostgreSQL
4. Materialize additive canonical identity proposals for duplicate review
5. Run long tasks via queue workers (not request lifecycle)
6. Serve frontend and agent via stable API contracts

## Canonical Identity Read Model

Current production behavior separates:

- `lead_id` workflow rows used by the CRM-style UI for pipeline stages, follow-ups, notes, and outreach history
- canonical entity tables used for duplicate observability, audit-safe grouping, and future rollups

Read surfaces now include:

- `GET /api/leads/{lead_id}/lineage` for source lineage plus canonical memberships/proposals
- `GET /api/v1/quality/data-health` for materialized canonical counts and review buckets
- `GET /api/v1/canonical/entities`
- `GET /api/v1/canonical/entities/{canonical_entity_id}`
- `GET /api/v1/canonical/proposals`

This keeps canonicalization additive and reviewable before any destructive merge behavior is introduced.

## High-Level Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Ingest    │────▶│  Transform  │────▶│  Aggregate  │
│ (HPD API)   │     │ (Normalize) │     │ (Group)     │
└─────────────┘     └─────────────┘     └─────────────┘
                                              │
                                              ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Publish   │◀────│    Score    │◀────│   Enrich    │
│ (Sheets)    │     │ (Rank)      │     │ (Web/API)   │
└─────────────┘     └─────────────┘     └─────────────┘
```

## Component Responsibilities

### 1. Ingest (`src/ingest/hpd_client.py`)

**Input:** None (pulls from API)
**Output:** List of raw building records

Responsibilities:
- Connect to Socrata API
- Handle pagination (1000 rows at a time)
- Retry on failure with exponential backoff
- Cache raw responses to `data/raw/`
- Log row counts and timing

```python
class HPDClient:
    def fetch_all_registrations(self) -> List[dict]:
        """Fetch all registrations, handling pagination."""
        
    def fetch_since(self, last_date: date) -> List[dict]:
        """Fetch registrations updated since date (incremental)."""
```

### 2. Transform (`src/transform/normalize.py`)

**Input:** List of raw building records
**Output:** List of normalized building records

Responsibilities:
- Parse and validate fields
- Normalize names, phones, emails
- Clean addresses
- Parse dates
- Handle missing/null values

```python
def normalize_building(raw: dict) -> Building:
    """Normalize a single building record."""

def normalize_name(name: str) -> str:
    """Normalize company/person name."""

def normalize_phone(phone: str) -> Optional[str]:
    """Normalize phone to (XXX) XXX-XXXX or None."""
```

### 3. Aggregate (`src/transform/aggregate.py`)

**Input:** List of normalized buildings
**Output:** List of leads (grouped by agent/owner)

Responsibilities:
- Group buildings by normalized agent name
- Compute portfolio metrics (building count, unit sum)
- Determine primary borough
- Collect all contact info variants
- Pick best contact info (most recent registration)

```python
def aggregate_to_leads(buildings: List[Building]) -> List[Lead]:
    """Group buildings into leads by agent/owner."""
```

### 4. Enrich (`src/enrich/`)

**Input:** List of leads
**Output:** List of enriched leads

Submodules:
- `web_crawl.py` — Google search + website scraping
- `ny_dos.py` — NY DOS registry lookup
- `paid_apis.py` — Apollo, Clearbit, etc.

```python
class Enricher:
    def enrich_lead(self, lead: Lead) -> Lead:
        """Run all enrichment tiers on a lead."""
        
    def enrich_batch(self, leads: List[Lead], limit: int = 50) -> List[Lead]:
        """Enrich a batch of leads (respects rate limits)."""
```

### 5. Score (`src/score/scorer.py`)

**Input:** List of enriched leads
**Output:** List of scored leads

Responsibilities:
- Calculate component scores
- Apply weights from config
- Assign tier labels
- Generate tags

```python
class Scorer:
    def __init__(self, config_path: str):
        self.weights = load_weights(config_path)
        
    def score_lead(self, lead: Lead) -> Lead:
        """Calculate score and assign tier."""
```

### 6. Publish (`src/publish/sheets_writer.py`)

**Input:** List of scored leads
**Output:** None (writes to Google Sheet)

Responsibilities:
- Connect to Google Sheets API
- Format data for sheet columns
- Handle full refresh vs incremental update
- Preserve manual columns
- Update metadata (last refresh time)

```python
class SheetsWriter:
    def full_refresh(self, leads: List[Lead]) -> int:
        """Clear and rewrite all data. Returns row count."""
        
    def incremental_update(self, leads: List[Lead]) -> Tuple[int, int]:
        """Update existing + append new. Returns (updated, added)."""
```

---

## Data Flow Details

### Backfill (scripts/backfill.py)

```python
# 1. Ingest all
client = HPDClient()
raw_buildings = client.fetch_all_registrations()

# 2. Transform
buildings = [normalize_building(b) for b in raw_buildings]

# 3. Aggregate
leads = aggregate_to_leads(buildings)

# 4. Filter (optional: only 10+ buildings)
leads = [l for l in leads if l.portfolio_size >= 10]

# 5. Enrich (top 100 by portfolio size first)
enricher = Enricher()
leads = enricher.enrich_batch(leads, limit=100)

# 6. Score
scorer = Scorer('config/scoring_weights.yaml')
leads = [scorer.score_lead(l) for l in leads]

# 7. Publish
writer = SheetsWriter(SHEET_ID)
writer.full_refresh(leads)
```

### Daily Run (scripts/daily_run.py)

```python
# 1. Load last run date
last_run = load_last_run_date()

# 2. Ingest incremental
raw_buildings = client.fetch_since(last_run)

# 3-6. Same as backfill...

# 7. Incremental update
updated, added = writer.incremental_update(leads)
logger.info(f"Updated {updated}, added {added}")

# 8. Save run date
save_last_run_date(datetime.now())
```

---

## Error Handling

### Retry Strategy
- API calls: 3 retries with exponential backoff (1s, 2s, 4s)
- Web scraping: 1 retry, then skip
- Sheet writes: 2 retries

### Logging
All components log to:
- Console (INFO level)
- `data/logs/pipeline_{date}.log` (DEBUG level)

### Failure Modes
| Failure | Handling |
|---------|----------|
| HPD API down | Retry 3x, then abort run |
| Single building parse error | Log warning, skip building |
| Enrichment API error | Mark lead as `enrichment_failed` |
| Sheets API error | Retry 2x, then abort |

---

## Caching

### Raw Data Cache
- Location: `data/raw/hpd_{date}.json`
- TTL: 7 days
- Purpose: Avoid re-fetching during development

### Enrichment Cache
- Location: `data/cache/enrichment/`
- Key: `{normalized_name}_{source}.json`
- TTL: 30 days
- Purpose: Avoid re-enriching same company

---

## Configuration

### Environment Variables (.env)
```
NYC_OPEN_DATA_APP_TOKEN=
GOOGLE_SHEETS_CREDENTIALS=path/to/credentials.json
GOOGLE_SHEET_ID=
APOLLO_API_KEY=
SERPAPI_KEY=
```

### Runtime Config (config/settings.py)
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    nyc_app_token: str = ""
    google_creds_path: str = "credentials.json"
    google_sheet_id: str
    apollo_api_key: str = ""
    
    min_portfolio_size: int = 10
    enrichment_batch_size: int = 50
    
    class Config:
        env_file = ".env"
```
