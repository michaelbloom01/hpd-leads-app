# HPD Leads Pipeline

Generate acquisition leads for Property Management and HOA/COA firms in NYC by aggregating HPD registration data and enriching with contact information.

## Quick Start

```bash
# Clone and setup
cd C:\Users\micha\Projects\hpd-leads
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your API keys

# Run backfill (first time)
python scripts/backfill.py

# Run daily update
python scripts/daily_run.py
```

## Output

Google Sheet with:
- Lead name, contact info, website
- Portfolio size (buildings managed)
- Score and tier (A/B/C/D/F)
- Business summary
- Tags for filtering

## Data Sources

1. **HPD Multiple Dwelling Registrations** — NYC Open Data
2. **NY DOS Corporation Database** — NY State Open Data
3. **Web crawl** — Company websites
4. **Paid APIs** — Apollo.io (optional)

## Project Structure

```
hpd-leads/
├── docs/           # Knowledge base (read before coding)
├── src/            # Source code
│   ├── ingest/     # HPD API client
│   ├── transform/  # Normalization
│   ├── enrich/     # Web/API enrichment
│   ├── score/      # Lead scoring
│   └── publish/    # Google Sheets writer
├── scripts/        # Runnable scripts
├── config/         # Settings and weights
├── data/           # Cached data (gitignored)
├── tasks/          # Task tracking
└── skills/         # Extracted learnings
```

## Documentation

See `docs/` folder for detailed documentation:
- `00-project-overview.md` — Goals and scope
- `01-data-sources.md` — API details
- `02-data-model.md` — Lead schema
- `03-scoring-rules.md` — How scoring works
- `04-enrichment-strategy.md` — Web/API enrichment
- `05-google-sheet-design.md` — Output format
- `06-pipeline-architecture.md` — Technical flow
- `07-scheduling-ops.md` — Daily runs
- `08-compliance-notes.md` — Legal/ethical notes

## Requirements

- Python 3.10+
- Google Cloud service account with Sheets API
- NYC Open Data app token (optional but recommended)
- Apollo.io API key (optional, for enrichment)

## License

Private project for Michael Bloom's acquisition search.
