# Scheduling and Operations

## Daily Run Schedule

### Windows Task Scheduler Setup

1. Open Task Scheduler
2. Create Basic Task
3. Name: "HPD Leads Daily Update"
4. Trigger: Daily at 6:00 AM
5. Action: Start a program
6. Program: `C:\Users\micha\AppData\Local\Programs\Python\Python312-arm64\python.exe`
7. Arguments: `C:\Users\micha\Projects\hpd-leads\scripts\daily_run.py`
8. Start in: `C:\Users\micha\Projects\hpd-leads`

### Alternative: PowerShell Script

Create `run_daily.ps1`:
```powershell
cd C:\Users\micha\Projects\hpd-leads
.\venv\Scripts\activate
python scripts\daily_run.py
```

Schedule the .ps1 file instead.

---

## Logging

### Log Locations
- Console output: Real-time during run
- File logs: `data/logs/pipeline_{YYYYMMDD}.log`

### Log Format
```
2026-02-03 06:00:01 INFO  [ingest] Starting HPD fetch...
2026-02-03 06:00:15 INFO  [ingest] Fetched 52,431 buildings
2026-02-03 06:00:16 INFO  [transform] Normalizing...
2026-02-03 06:00:20 INFO  [aggregate] Created 8,234 leads
2026-02-03 06:00:21 INFO  [enrich] Enriching top 50 leads...
2026-02-03 06:02:45 INFO  [enrich] Enriched 47/50 (3 failed)
2026-02-03 06:02:46 INFO  [score] Scoring leads...
2026-02-03 06:02:47 INFO  [publish] Writing to sheet...
2026-02-03 06:02:52 INFO  [publish] Updated 45, added 12
2026-02-03 06:02:52 INFO  [main] Run complete in 171s
```

### Log Rotation
Keep last 30 days of logs. Delete older files in daily_run.py:
```python
import os
from datetime import datetime, timedelta

def cleanup_old_logs(days=30):
    log_dir = Path("data/logs")
    cutoff = datetime.now() - timedelta(days=days)
    for f in log_dir.glob("pipeline_*.log"):
        file_date = datetime.strptime(f.stem.split("_")[1], "%Y%m%d")
        if file_date < cutoff:
            f.unlink()
```

---

## Monitoring

### Success Indicators
- Log file created with "Run complete" message
- Google Sheet "Last Updated" timestamp updated
- No ERROR level log entries

### Failure Alerts (Future)

Option 1: Email on failure
```python
import smtplib
from email.mime.text import MIMEText

def send_alert(subject, body):
    msg = MIMEText(body)
    msg['Subject'] = f"[HPD Leads] {subject}"
    msg['From'] = "alerts@example.com"
    msg['To'] = "michaelbloom01@gmail.com"
    
    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(EMAIL, APP_PASSWORD)
        server.send_message(msg)
```

Option 2: Slack webhook (simpler)
```python
import requests

def send_slack_alert(message):
    requests.post(SLACK_WEBHOOK_URL, json={"text": message})
```

---

## Manual Operations

### Force Full Refresh
```bash
cd C:\Users\micha\Projects\hpd-leads
.\venv\Scripts\activate
python scripts\backfill.py --force
```

### Enrich Specific Lead
```bash
python scripts\enrich_one.py "ABC Management LLC"
```

### Export to CSV
```bash
python scripts\export_csv.py --output leads_export.csv
```

### Check Run Status
```bash
python scripts\status.py
# Output:
# Last run: 2026-02-03 06:02:52
# Total leads: 1,234
# Enriched: 890 (72%)
# Top score: 94 (XYZ Property Mgmt)
```

---

## Troubleshooting

### HPD API Issues
**Symptom:** Fetch returns 0 records or times out
**Check:** 
1. NYC Open Data status page
2. Try endpoint in browser
3. Check if app token expired

**Fix:** Wait and retry, or use cached data

### Google Sheets Auth Issues
**Symptom:** 403 Forbidden on sheet write
**Check:**
1. Service account email has edit access to sheet
2. Credentials JSON is valid
3. Sheet ID is correct

**Fix:** Re-share sheet with service account email

### Enrichment Failures
**Symptom:** Many leads stuck at `enrichment_failed`
**Check:**
1. API keys still valid
2. Rate limits not exceeded
3. Network connectivity

**Fix:** 
1. Check `data/logs/` for specific errors
2. Reset failed leads: `python scripts/reset_enrichment.py`
3. Re-run enrichment

---

## Performance Benchmarks

Expected run times (rough):
| Stage | Time |
|-------|------|
| HPD fetch (full) | 30-60s |
| HPD fetch (incremental) | 5-10s |
| Normalize + Aggregate | 5-10s |
| Enrich (50 leads) | 2-5 min |
| Score | <1s |
| Sheet write | 5-10s |
| **Total (daily)** | **3-7 min** |
| **Total (backfill)** | **5-10 min** |

---

## Backup

### Automated Backups
Export sheet to CSV weekly:
```python
# In weekly_backup.py
from datetime import datetime
writer.export_to_csv(f"data/backups/leads_{datetime.now():%Y%m%d}.csv")
```

### Manual Backup
Google Sheets has version history. Can restore from any point in last 30 days.
