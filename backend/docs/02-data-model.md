# Data Model

## Lead Schema

A "lead" is a unique agent or owner entity that manages/owns multiple buildings.

| Field | Type | Source | Required |
|-------|------|--------|----------|
| `lead_id` | str | generated (hash of normalized name) | Yes |
| `agent_name` | str | HPD | Yes |
| `owner_name` | str | HPD | No |
| `owner_type` | str | HPD (Owner, Agent, Corp, etc.) | Yes |
| `portfolio_size` | int | computed (count of buildings) | Yes |
| `total_units` | int | computed (sum of units) | Yes |
| `buildings` | list[str] | HPD (list of addresses) | Yes |
| `phone` | str | HPD + enrichment | No |
| `email` | str | enrichment | No |
| `website` | str | enrichment | No |
| `business_summary` | str | web crawl | No |
| `owner_principal` | str | NY DOS + web | No |
| `contacts` | list[dict] | enrichment | No |
| `address` | str | HPD (business address) | No |
| `boro` | str | HPD (primary borough) | Yes |
| `reg_status` | str | HPD | No |
| `last_registration` | date | HPD | No |
| `dos_id` | str | NY DOS | No |
| `dos_status` | str | NY DOS | No |
| `enrichment_status` | str | pipeline (none/partial/complete) | Yes |
| `enrichment_sources` | list[str] | pipeline | No |
| `last_enriched` | datetime | pipeline | No |
| `score` | float | computed | Yes |
| `score_breakdown` | dict | computed | No |
| `tags` | list[str] | computed | No |
| `opportunity_note` | str | manual/AI | No |
| `created_at` | datetime | pipeline | Yes |
| `updated_at` | datetime | pipeline | Yes |

## Building Schema (intermediate)

| Field | Type | Source |
|-------|------|--------|
| `building_id` | str | HPD |
| `registration_id` | str | HPD |
| `address` | str | HPD |
| `boro` | str | HPD |
| `zip` | str | HPD |
| `block` | str | HPD |
| `lot` | str | HPD |
| `bin` | str | HPD |
| `unit_count` | int | HPD (if available) |
| `owner_name` | str | HPD |
| `owner_type` | str | HPD |
| `agent_name` | str | HPD |
| `agent_phone` | str | HPD |
| `agent_address` | str | HPD |
| `reg_status` | str | HPD |
| `last_registration` | date | HPD |

## Deduplication Rules

### Name Normalization
1. Uppercase
2. Remove punctuation (LLC → LLC, L.L.C. → LLC)
3. Remove common suffixes for comparison: LLC, INC, CORP, CO, COMPANY
4. Trim whitespace
5. Replace multiple spaces with single space

### Phone Normalization
1. Remove all non-digits
2. Remove leading 1 (country code)
3. Must be 10 digits to be valid
4. Format as (XXX) XXX-XXXX for display

### Email Normalization
1. Lowercase
2. Trim whitespace
3. Basic validation (contains @ and .)

### Grouping Logic
1. Primary key: normalized agent_name
2. Secondary grouping: normalized owner_name (if agent_name is empty)
3. Merge buildings with same agent/owner into one lead
4. Use most recent registration data for contact info

## Enrichment Status

| Status | Meaning |
|--------|---------|
| `none` | No enrichment attempted |
| `pending` | Queued for enrichment |
| `partial` | Some fields enriched, others failed |
| `complete` | All enrichment sources tried |
| `failed` | All enrichment attempts failed |

## Tags

Auto-generated tags based on data:

| Tag | Condition |
|-----|-----------|
| `large_portfolio` | 50+ buildings |
| `medium_portfolio` | 10-49 buildings |
| `small_portfolio` | <10 buildings |
| `professional_mgmt` | Agent type = "AGENT" or "MANAGEMENT" |
| `owner_operator` | Agent type = "OWNER" |
| `has_website` | Website field populated |
| `has_email` | Email field populated |
| `has_phone` | Phone field populated |
| `manhattan` | Primary boro = Manhattan |
| `brooklyn` | Primary boro = Brooklyn |
| `queens` | Primary boro = Queens |
| `bronx` | Primary boro = Bronx |
| `staten_island` | Primary boro = Staten Island |
