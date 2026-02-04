# HPD Leads Project — Agent Context

## What This Project Does

Generates acquisition leads for Property Management and HOA/COA management firms in NYC by:
1. Pulling HPD Multiple Dwelling Registration data (owners/agents of rental buildings)
2. Enriching with contact info from web crawling, NY DOS registry, and paid APIs
3. Scoring leads by portfolio size (largest first)
4. Publishing to Google Sheet for manual outreach

## Quick Start

```bash
cd C:\Users\micha\Projects\hpd-leads
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
python scripts/backfill.py
```

## Key Files

| File | Purpose |
|------|---------|
| `docs/` | Knowledge base — read before coding any module |
| `src/ingest/hpd_client.py` | Socrata API client for HPD data |
| `src/transform/normalize.py` | Name/phone/email normalization |
| `src/enrich/` | Web crawl, NY DOS, paid API enrichment |
| `src/score/scorer.py` | Portfolio-size scoring |
| `src/publish/sheets_writer.py` | Google Sheets upsert |
| `config/settings.py` | Load env vars |
| `config/scoring_weights.yaml` | Tunable scoring params |
| `scripts/backfill.py` | One-shot full load |
| `scripts/daily_run.py` | Incremental update |
| `tasks/todo.md` | Current task list |
| `tasks/lessons.md` | Learnings from corrections |

## Data Sources

1. **HPD Multiple Dwelling Registrations** — Socrata endpoint `vx8i-nprf`
2. **NY DOS Corporation Database** — data.ny.gov
3. **Web crawl** — Company websites for contact/summary
4. **Paid APIs** — Apollo (free tier), Clearbit, People Data Labs

## Lead Schema

See `docs/02-data-model.md` for full schema. Key fields:
- `lead_id`, `agent_name`, `owner_name`, `portfolio_size`, `total_units`
- `phone`, `email`, `website`, `business_summary`, `owner_principal`
- `score`, `tags`, `enrichment_status`

## Scoring

Primary: Portfolio size (count of buildings per agent/owner)
Secondary: Professional indicators (LLC/Inc, multiple properties)
Config: `config/scoring_weights.yaml`

## Output

Google Sheet with tabs:
- `Leads` — Primary list, sorted by score
- `Agents` — Aggregated by agent
- `Owners` — Aggregated by owner
- `Config` — Scoring weights and filters

## API Keys Needed

```
# .env
NYC_OPEN_DATA_APP_TOKEN=...      # Optional but recommended
GOOGLE_SHEETS_CREDENTIALS=...    # Service account JSON path
GOOGLE_SHEET_ID=...              # Output sheet ID
APOLLO_API_KEY=...               # Optional: paid enrichment
ANTHROPIC_API_KEY=...            # Optional: AI summaries
```

## Compliance Notes

- HPD data is public domain (NYC Open Data)
- NY DOS is public record
- Web crawl: respect robots.txt, 1 req/sec default
- Paid APIs: follow terms of service

## Task Tracking

Before starting work, check:
1. `tasks/todo.md` — Current tasks
2. `tasks/lessons.md` — Mistakes to avoid

After completing work:
1. Update `tasks/todo.md` with progress
2. If corrected, add to `tasks/lessons.md`

## Related Projects

- Deal Flow v2: `C:\Users\micha\Projects\smb-deal-flow\`
- Personal CRM v2: `C:\Users\micha\Projects\personal-crm-v2\`
