# Scoring Rules

## Philosophy

Score leads by **acquisition attractiveness**, not just size. A larger portfolio indicates a more established business, but other factors matter too.

## Scoring Formula

```
score = (portfolio_weight * portfolio_score) 
      + (units_weight * units_score)
      + (professional_weight * professional_score)
      + (contact_weight * contact_score)
```

Default weights (configurable in `config/scoring_weights.yaml`):
- `portfolio_weight`: 0.50
- `units_weight`: 0.20
- `professional_weight`: 0.15
- `contact_weight`: 0.15

## Component Scores

### Portfolio Score (0-100)

Based on number of buildings managed.

| Buildings | Score |
|-----------|-------|
| 100+ | 100 |
| 50-99 | 80 |
| 25-49 | 60 |
| 10-24 | 40 |
| 5-9 | 20 |
| 1-4 | 10 |

### Units Score (0-100)

Based on total units across portfolio.

| Units | Score |
|-------|-------|
| 1000+ | 100 |
| 500-999 | 80 |
| 250-499 | 60 |
| 100-249 | 40 |
| 50-99 | 20 |
| <50 | 10 |

### Professional Score (0-100)

Based on entity type and indicators.

| Indicator | Points |
|-----------|--------|
| Agent type = "AGENT" or "MANAGEMENT" | +40 |
| Entity is LLC/Corp (not individual) | +30 |
| Has dedicated management company name | +20 |
| Multiple owner types in portfolio | +10 |

### Contact Score (0-100)

Based on enrichment completeness.

| Field | Points |
|-------|--------|
| Has phone | +30 |
| Has email | +30 |
| Has website | +20 |
| Has business summary | +10 |
| Has owner principal name | +10 |

## Score Tiers

For filtering and display:

| Tier | Score Range | Label |
|------|-------------|-------|
| A | 80-100 | Top Tier |
| B | 60-79 | Strong |
| C | 40-59 | Moderate |
| D | 20-39 | Low Priority |
| F | 0-19 | Minimal |

## Configuration File

`config/scoring_weights.yaml`:

```yaml
weights:
  portfolio: 0.50
  units: 0.20
  professional: 0.15
  contact: 0.15

thresholds:
  portfolio:
    tier_100: 100
    tier_80: 50
    tier_60: 25
    tier_40: 10
    tier_20: 5
  units:
    tier_100: 1000
    tier_80: 500
    tier_60: 250
    tier_40: 100
    tier_20: 50

professional_keywords:
  - management
  - mgmt
  - property
  - realty
  - holdings
  - associates

tier_labels:
  A: "Top Tier"
  B: "Strong"
  C: "Moderate"
  D: "Low Priority"
  F: "Minimal"
```

## Future Enhancements

- Add violation score (distress signal) from HPD violations dataset
- Add growth score (recent registrations vs historical)
- Add geographic concentration score
- Add AI-based opportunity scoring
