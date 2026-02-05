# HPD Leads App - Product Plan

**Date:** February 5, 2026  
**Goal:** Make this tool "just work" for finding high-quality property management company acquisition targets in NYC.

---

## Current State Assessment

### What's Working Well
- **102,505 leads loaded** from 189,737 buildings (complete HPD dataset)
- **PLUTO integration complete** - all leads have building type classification (condo/coop/rental/etc.)
- **Multi-borough support** - leads show all boroughs they operate in
- **Scoring system** - leads ranked 0-100 based on portfolio size and concentration
- **SQLite persistence** - data survives server restarts
- **Basic enrichment** - web crawl, NY DOS lookup working (3 leads enriched as test)

### What's Broken or Incomplete
1. **Only 3 of 102,505 leads are enriched** - need batch enrichment to run
2. **Google Maps API key exposed** - needs rotation and env var configuration
3. **No error feedback to user** - failures silent in console
4. **Dashboard shows stale "last refresh" time** - confusing
5. **CSV export may not include all fields** - needs verification
6. **AI Summary feature requires API key** - not configured in Railway

### User Journey Gaps
1. User loads app → sees 102k leads but no contact info → frustrating
2. User clicks "Deep Research" → works but slow, one at a time
3. User wants to export leads → CSV missing enriched data
4. User can't tell which leads are worth pursuing → no visual priority

---

## Product Vision

**One sentence:** Open the app, see the top 500 property management companies in NYC with their contact info, sorted by acquisition potential.

**Success criteria:**
- [ ] Top 500 leads by portfolio size all have phone/email/website
- [ ] AI summary generated for top 100 leads
- [ ] User can filter by building type mix (e.g., "mostly condos")
- [ ] User can export a call list with one click
- [ ] Dashboard shows clear "what to do next" guidance

---

## Sprint 1: Make Enrichment "Just Work" (Priority: Critical)

### 1.1 Auto-Enrich Top Leads on First Load
**Problem:** User sees 102k leads with no contact info.  
**Solution:** When leads have <100 enriched, automatically start background enrichment of top 500.

```
Backend Change:
- On startup, check enriched count
- If < 100 enriched, trigger batch enrichment of top 500 by portfolio
- Show enrichment progress on Dashboard
```

### 1.2 Enrichment Progress Indicator
**Problem:** User doesn't know enrichment is happening.  
**Solution:** Dashboard shows real-time enrichment status.

```
Frontend Change:
- Add "Enrichment Status" card to Dashboard
- Shows: "Enriching 127/500 leads... (25%)"
- Shows: "✓ 500 leads enriched" when done
```

### 1.3 Fix Batch Enrichment Reliability
**Problem:** Enrichment may fail silently or timeout.  
**Solution:** 
- Add retry logic (3 attempts per lead)
- Skip leads that consistently fail
- Log failures to database for later review

### 1.4 Configure AI Summary (Anthropic API)
**Problem:** AI Summary button shows "not configured".  
**Solution:** Add ANTHROPIC_API_KEY to Railway, auto-generate summaries for top 100.

---

## Sprint 2: Dashboard That Guides Action (Priority: High)

### 2.1 "Ready to Contact" Section
Show leads that have:
- Portfolio size > 10
- Phone OR email available
- Not yet contacted (outreach_status = 'new')

```
Frontend Change:
- New Dashboard card: "Ready to Contact (47)"
- Shows top 10 with quick-action buttons
- Click to expand full list
```

### 2.2 "Needs Research" Section
Show leads that have:
- Portfolio size > 25
- No contact info yet
- Not in current enrichment queue

```
Frontend Change:
- New Dashboard card: "Needs Research (23)"
- One-click "Enrich All" button
```

### 2.3 Key Metrics at a Glance
Replace current stats with actionable numbers:
- **Total Leads:** 102,505
- **With Contact Info:** 487 (0.5%)
- **Ready to Contact:** 234
- **Contacted:** 12
- **In Pipeline:** 5

### 2.4 Remove Confusing Elements
- Remove "Refresh Data" button from header (rarely needed)
- Move to Settings or hidden menu
- Remove the percentage bars that show nothing useful

---

## Sprint 3: Lead Table Improvements (Priority: High)

### 3.1 Default to "High Value" View
**Problem:** Table shows all 102k leads, most are tiny.  
**Solution:** Default filter to portfolio >= 10 buildings.

```
Frontend Change:
- Pre-set filter on load: min_portfolio=10
- Shows ~2,700 leads instead of 102k
- Add "Show All" button to remove filter
```

### 3.2 Contact Info Columns
**Problem:** Can't see contact info without opening detail.  
**Solution:** Add columns for phone/email with icons.

```
| Company | Boro | Buildings | Type Mix | Phone | Email | Score |
|---------|------|-----------|----------|-------|-------|-------|
| AKAM    | MAN  | 255       | 10C 5Co  | ✓     | ✓     | 87.5  |
| Front Door | BRX | 158    | 8R       | -     | ✓     | 69.5  |
```

### 3.3 Quick Actions in Table
- Click phone icon → copies to clipboard
- Click email icon → opens mailto:
- Click website icon → opens in new tab

### 3.4 "Export Call List" Button
One-click export of filtered leads with:
- Company name
- Phone (primary)
- Email (primary)
- Portfolio size
- Borough focus
- AI Summary (first 100 chars)

---

## Sprint 4: Lead Detail Polish (Priority: Medium)

### 4.1 Contact Info Front and Center
Move phone/email/website to top of detail modal, not buried.

```
+------------------------------------------+
| AKAM Associates                    [X]   |
| 255 buildings • Manhattan, Brooklyn      |
|                                          |
| 📞 (212) 555-1234  [Copy] [Call]        |
| ✉️  info@akam.com  [Copy] [Email]        |
| 🌐 www.akam.com    [Open]                |
|                                          |
| AI Summary:                              |
| "Full-service property management..."    |
+------------------------------------------+
```

### 4.2 Outreach History
Show when user last contacted and outcome:
```
Outreach History:
- Feb 3: Called, left voicemail
- Feb 5: Email sent, no response
- Feb 7: Call scheduled for 2pm
```

### 4.3 Similar Leads Section
"Other companies in Manhattan with 50+ buildings"
- Helps with market research
- Suggests next leads to contact

---

## Sprint 5: Reliability & Operations (Priority: Medium)

### 5.1 Health Dashboard (Admin Only)
Show:
- Last successful refresh
- Enrichment queue status
- Error counts last 24h
- Database size

### 5.2 Automated Daily Tasks
Set up Railway cron:
- 6am: Check for new HPD data (monthly)
- 6am: Resume any failed enrichments
- 6am: Generate AI summaries for new high-value leads

### 5.3 Error Notifications
Email Michael when:
- Enrichment fails 10+ times in a row
- Database corruption detected
- API errors spike

### 5.4 Data Backup
Weekly SQLite backup to Google Drive.

---

## Technical Debt to Address

### Code Cleanup (from REVIEW.md)
- [ ] Remove dead code: `paid_apis.py`, unused frontend functions
- [ ] Fix race conditions in cache updates (add locking)
- [ ] Single-pass stats aggregation (performance)
- [ ] Remove Settings tab (not implemented)

### Security
- [x] Google Maps API key moved to env var
- [ ] Rotate the exposed key
- [ ] Add API key restrictions
- [ ] Configure CORS properly (not allow all origins)

### Testing
- [ ] Add integration tests for enrichment pipeline
- [ ] Add frontend tests for critical flows
- [ ] Test CSV export with all fields

---

## Implementation Order

### Week 1: Core Functionality
1. Configure ANTHROPIC_API_KEY in Railway
2. Trigger batch enrichment of top 500 leads
3. Add enrichment progress to Dashboard
4. Default table to portfolio >= 10

### Week 2: UX Polish
1. Add contact columns to table
2. Move contact info to top of detail modal
3. Add "Export Call List" button
4. Add "Ready to Contact" Dashboard section

### Week 3: Operations
1. Set up daily cron for enrichment
2. Add error notifications
3. Clean up dead code
4. Add data backup

---

## Metrics to Track

| Metric | Current | Target |
|--------|---------|--------|
| Leads with contact info | 3 | 500+ |
| Leads with AI summary | 0 | 100+ |
| Time to find a lead to call | ~5 min | <30 sec |
| Export a call list | ~2 min | 1 click |

---

## Questions to Answer

1. **What's the ideal portfolio size for acquisition targets?**
   - Current filter: 10+ buildings
   - Should it be higher (25+)?

2. **Which building types are most valuable?**
   - Condos? Coops? Rentals?
   - Should we weight the score by type?

3. **How many leads can you realistically contact per week?**
   - 10? 50? 100?
   - This determines how many we need enriched

4. **What info do you need before making a call?**
   - Just phone/name? Or full AI summary?
   - This affects the "call list" export format

---

## Next Steps

1. **Immediate:** I'll start Sprint 1 now - configure API keys and trigger enrichment
2. **Today:** Get top 500 leads enriched with contact info
3. **Tomorrow:** Add Dashboard enrichment status and default table filter
4. **This Week:** Complete Sprint 2 (Dashboard guidance)

Ready to start?
