# Google Sheet Design

## Sheet Structure

**Sheet name:** `HPD PM Leads`
**Location:** Michael's Google Drive (link in .env)

### Tab: Leads (Primary)

Main lead list, sorted by score descending.

| Column | Width | Format | Notes |
|--------|-------|--------|-------|
| A: Score | 60 | Number (0-100) | Conditional formatting by tier |
| B: Tier | 50 | Text | A/B/C/D/F |
| C: Agent Name | 200 | Text | Primary identifier |
| D: Owner Name | 200 | Text | If different from agent |
| E: Portfolio Size | 80 | Number | Count of buildings |
| F: Total Units | 80 | Number | Sum of units |
| G: Phone | 120 | Text | Formatted (XXX) XXX-XXXX |
| H: Email | 200 | Text | Clickable mailto link |
| I: Website | 200 | Text | Clickable link |
| J: Business Summary | 300 | Text (wrap) | From website |
| K: Owner/Principal | 150 | Text | From DOS or website |
| L: Address | 250 | Text | Business address |
| M: Primary Boro | 100 | Text | Most common borough |
| N: Tags | 200 | Text | Comma-separated |
| O: Enrichment Status | 100 | Text | none/partial/complete |
| P: Last Updated | 100 | Date | Auto-updated |
| Q: Opportunity Notes | 300 | Text (wrap) | Manual notes |
| R: Status | 100 | Dropdown | New/Contacted/Meeting/Pass |
| S: Lead ID | 100 | Text | Hidden, for updates |

### Tab: Agents (Aggregated)

Grouped view by agent name.

| Column | Notes |
|--------|-------|
| Agent Name | Unique agent |
| Building Count | Total buildings |
| Unit Count | Total units |
| Boroughs | Comma-separated list |
| Latest Registration | Most recent date |
| Contact Info | Phone / Email if available |

### Tab: Owners (Aggregated)

Grouped view by owner name.

| Column | Notes |
|--------|-------|
| Owner Name | Unique owner |
| Building Count | Total buildings |
| Unit Count | Total units |
| Agent Names | Comma-separated agents |
| Boroughs | Comma-separated list |

### Tab: Buildings (Reference)

Raw building data for reference.

| Column | Notes |
|--------|-------|
| Building ID | HPD ID |
| Address | Full address |
| Boro | Borough |
| Units | Unit count |
| Owner | Owner name |
| Agent | Agent name |
| Registration Date | Last registration |

### Tab: Config

Settings and metadata.

| Row | Content |
|-----|---------|
| 1 | Last refresh timestamp |
| 2 | Total leads |
| 3 | Enriched leads |
| 4 | Scoring weights |
| 5 | Filter settings |

---

## Conditional Formatting

### Score Column (A)
- 80-100: Green background
- 60-79: Light green
- 40-59: Yellow
- 20-39: Orange
- 0-19: Red

### Enrichment Status (O)
- complete: Green text
- partial: Orange text
- none: Gray text
- failed: Red text

### Status Column (R)
- New: No color
- Contacted: Blue
- Meeting: Green
- Pass: Gray strikethrough

---

## Filter Views

Create saved filter views:

1. **Top Tier** — Score >= 80
2. **Manhattan** — Primary Boro = Manhattan
3. **Brooklyn** — Primary Boro = Brooklyn
4. **Large Portfolio** — Portfolio Size >= 50
5. **Has Contact** — Phone OR Email not empty
6. **Needs Enrichment** — Enrichment Status = none

---

## Data Validation

### Status Column
Dropdown list: New, Contacted, Meeting, Pass

### Tier Column
Dropdown list: A, B, C, D, F

---

## Update Strategy

### Full Refresh (backfill.py)
1. Clear all data in Leads tab
2. Write new data
3. Preserve manual columns (Opportunity Notes, Status)

### Incremental Update (daily_run.py)
1. Read existing Lead IDs
2. Update existing rows by Lead ID
3. Append new leads
4. Preserve manual columns

### Preserving Manual Data
Columns Q (Opportunity Notes) and R (Status) are user-edited.
On update:
1. Read existing values by Lead ID
2. Write new data
3. Restore manual column values

---

## Google Sheets API

### Authentication
Use service account with Sheets API enabled.
Share sheet with service account email.

### Libraries
```python
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

creds = Credentials.from_service_account_file(
    'credentials.json',
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
service = build('sheets', 'v4', credentials=creds)
```

### Batch Updates
Use `batchUpdate` for efficiency:
```python
service.spreadsheets().values().batchUpdate(
    spreadsheetId=SHEET_ID,
    body={'valueInputOption': 'USER_ENTERED', 'data': [...]}
)
```

---

## Estimated Size

- HPD has ~50,000 registered buildings
- Estimated unique agents: 5,000-10,000
- Estimated unique owners: 20,000-30,000
- After filtering (10+ buildings): ~500-2,000 leads

Google Sheets limit: 10 million cells. We're fine.
