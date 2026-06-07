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

## Canonical Identity Layer (Additive)

The production workflow still operates on `lead_id` rows, but the codebase now has an additive canonical identity layer for duplicate review and future rollups.

Primary tables:

| Table | Purpose |
|-------|---------|
| `canonical_entities` | Stable canonical entity identity keyed by `canonical_entity_id` and `normalized_name` |
| `canonical_entity_aliases` | Alias names and normalized variants with source + confidence |
| `canonical_entity_leads` | Maps workflow `lead_id` rows into canonical entities without deleting the original lead rows |
| `canonical_entity_buildings` | Canonical entity to building membership/evidence |
| `canonical_entity_match_proposals` | Persisted duplicate/canonical proposals with bucket, reasons, evidence, and safety flags |

Key rule:

- Canonicalization is additive-first. `lead_id` remains the workflow owner for pipeline stage, follow-up, notes, and outreach history until an explicitly safe migration path is chosen.

## Data Truth & Confidence Layer (Additive)

The canonical identity layer now feeds a claim/evidence/confidence program. The goal is to make every important relationship explainable before it becomes action-driving.

Primary tables:

| Table | Purpose |
|-------|---------|
| `truth_claims` | Beliefs such as "entity manages building", "person is a contact", or "entity owns building" with normalized value, belief status, confidence, freshness, and actionability |
| `truth_evidence` | Source observations attached to claims, marked as supporting or contradicting, with source quality and observed date |
| `confidence_snapshots` | Computed trust posture by lead/entity/building/contact scope |
| `truth_review_items` | Human-review queue with proposed change, supporting evidence, contradicting evidence, and run ID |
| `golden_verification_cases` | Benchmark cases for false merges, false splits, stale agents, co-op boards, shell LLCs, and suffix variants |
| `truth_validation_runs` | Auditable dry-run/execute envelopes for validation and reconciliation jobs |

Actionability rule:

- `broad_discovery`: exploratory only; requires at least one supporting source and evidence item
- `ranked_sourcing`: usable for ranking, still check before outreach; requires at least one supporting source and evidence item
- `automated_enrichment`: safe for non-destructive enrichment/source refresh, not outreach; requires at least one supporting source and evidence item
- `recommended_outreach`: safe for human-reviewed outreach; requires at least one supporting source and evidence item
- `acquisition_quality_diligence`: strong enough for diligence judgment; requires at least two supporting sources and evidence items
- `do_not_act`: weak, stale, or contradictory

Detailed design: `backend/docs/10-data-truth-confidence-program.md`.

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
