# Enrichment Strategy

## Overview

Enrichment happens in tiers, from free/fast to paid/slow. Stop when we have enough data.

```
Tier 1: Google Search → Website → Scrape
Tier 2: NY DOS Registry Lookup
Tier 3: Paid APIs (Apollo, Clearbit, PDL)
```

## Tier 1: Web Enrichment

### Step 1: Find Website

**Input:** Company/agent name from HPD
**Method:** 
1. Search Google for `"{name}" property management NYC site`
2. Filter results for likely company sites (exclude Yelp, LinkedIn, directories)
3. Take first result that looks like company domain

**Tools:**
- SerpAPI (100 free searches/month) or
- `googlesearch-python` (rate limited, may get blocked)

**Fallback:**
- Try variations: `{name} NYC`, `{name} real estate`
- Check if HPD business address has associated domain

### Step 2: Scrape Website

**Target pages (in order):**
1. `/contact` or `/contact-us`
2. `/about` or `/about-us`
3. `/team` or `/our-team`
4. Homepage

**Extract:**
| Field | Selectors to try |
|-------|-----------------|
| Phone | `tel:` links, regex `\d{3}[-.\s]?\d{3}[-.\s]?\d{4}` |
| Email | `mailto:` links, regex `[\w.-]+@[\w.-]+\.\w+` |
| Address | Schema.org markup, `<address>` tags |
| Summary | Meta description, first paragraph of about page |
| Owner | "Owner", "Principal", "Founder" near name patterns |

**Rate limiting:**
- 1 request per second
- Respect robots.txt
- Random delay 1-3 seconds between sites

### Step 3: Text Extraction

Use `trafilatura` for clean text from HTML:
```python
from trafilatura import extract
text = extract(html_content)
```

Then use regex or simple NLP to extract structured fields.

---

## Tier 2: NY DOS Lookup

### When to use
- After Tier 1 if owner/principal not found
- For LLC/Corp entities to find registered agent

### Method
1. Query NY DOS dataset by entity name (fuzzy match)
2. Extract DOS ID, status, registered agent
3. If registered agent is a person (not law firm), likely the owner

### API Access
```python
# Socrata endpoint for NY DOS
endpoint = "https://data.ny.gov/resource/7tqb-y2d4.json"
params = {
    "$where": f"entity_name like '%{name}%'",
    "$limit": 10
}
```

### Matching Logic
1. Normalize both names (remove LLC, punctuation)
2. Check for exact match first
3. Fall back to fuzzy match (Levenshtein distance < 3)
4. If multiple matches, prefer active entities

---

## Tier 3: Paid APIs

### Apollo.io (Recommended first)

**Free tier:** 50 credits/month
**Best for:** Email lookup by domain

```python
import requests

response = requests.post(
    "https://api.apollo.io/v1/organizations/enrich",
    headers={"Content-Type": "application/json"},
    json={
        "api_key": APOLLO_API_KEY,
        "domain": "example.com"
    }
)
```

**Returns:** Company info, employee emails, phone numbers

### People Data Labs

**Free tier:** 100 calls/month
**Best for:** Person lookup by name + company

### Clearbit

**No free tier** — skip unless others fail

---

## Enrichment Queue

### Priority Order
1. Leads with score > 60 (top prospects)
2. Leads with portfolio > 20 buildings
3. Remaining leads by score descending

### Batch Processing
- Process 50 leads per run
- Track enrichment status per lead
- Retry failed enrichments after 7 days

### Caching
- Cache all API responses in `data/cache/`
- Cache key: normalized company name + source
- Cache TTL: 30 days

---

## Error Handling

| Error | Action |
|-------|--------|
| Rate limited | Back off exponentially, retry after 1 hour |
| 404 / No results | Mark as `enrichment_failed`, move on |
| Timeout | Retry once, then mark failed |
| Invalid data | Log warning, skip field |

---

## Enrichment Status Flow

```
none → pending → (processing) → partial/complete/failed
```

- `none`: Never attempted
- `pending`: In queue for next run
- `partial`: Some fields populated
- `complete`: All tiers attempted
- `failed`: All tiers failed

---

## Cost Tracking

Log API usage to monitor costs:

```python
# data/enrichment_log.jsonl
{"timestamp": "...", "source": "apollo", "lead_id": "...", "credits_used": 1, "success": true}
```

Monthly budget alerts:
- Apollo: 40/50 credits → warning
- SerpAPI: 80/100 searches → warning
