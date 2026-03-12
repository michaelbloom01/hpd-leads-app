# Double Edge — Overview Memo

**Author:** Michael Bloom
**Date:** February 2026

> Context refresh (Mar 6, 2026): The product scope and JTBD remain the same. Current initiative is production runtime hardening plus conservative data-integrity cleanup, not feature reduction.

---

## What Is This Project?

Double Edge (formerly HPD Leads) is a dual-purpose NYC housing intelligence platform. It serves as both a **PE acquisition sourcing tool** (finding PM companies to acquire) and a **lead generation tool for operators** (finding buildings ripe for outreach). It scans public city records, identifies who manages large numbers of apartment buildings, figures out their contact information, and ranks them so the most promising targets float to the top.

The end result is a searchable live surface of roughly **315,000 lead rows** and **180,000 buildings**, scored and sorted so you can focus outreach on the ones that matter most. That surface is still being cleaned conservatively so ambiguous rows are not deleted without evidence.

---

## Why Does It Exist?

When looking to buy a property management firm, the first challenge is simply: *who is out there?* NYC requires every residential building with 3+ units to register with the city's Department of Housing Preservation and Development (HPD). That registry is public, free, and contains the name and address of every building's managing agent.

This project takes that raw government data — hundreds of thousands of building records — and turns it into a usable prospecting list with contact details, revenue estimates, and lead scores.

---

## How Does It Work?

The system follows a five-step process, all running automatically:

### Step 1: Gather Building Records
The system pulls the full list of registered buildings from HPD — over **200,000 buildings** — along with who manages each one. It also pulls building classification data (is it a condo, co-op, rental walk-up, etc.) from a separate city dataset called PLUTO.

### Step 2: Group by Management Company
Many buildings share the same manager. The system groups and materializes building-contact entities into lead rows. The current live production surface is larger than the earlier ~102k-lead era because the modern runtime preserves more contact/entity variants while integrity cleanup remains conservative.

### Step 3: Score and Rank
Each lead gets a score from 0-100 based on eight factors:

| Factor | What It Measures |
|--------|-----------------|
| Portfolio Size | How many buildings they manage |
| Total Units | Total apartments across all buildings |
| Professional Signals | Is this a formal company (LLC, Inc.) vs. an individual? |
| Contact Availability | Do we have a phone, email, or website? |
| Geographic Concentration | Are buildings clustered in one area or spread across boroughs? |
| Estimated Revenue | How much the company likely earns from management fees |
| Distress Signals | Do their buildings have lots of HPD violations? |
| Deal Fit | Overall match to the target acquisition profile |

### Step 4: Enrich with Contact Info
For the highest-scoring leads, the system looks up contact information using multiple sources in sequence:

1. **Google Places** — Search for the company by name and address to find phone numbers and websites
2. **NY Department of State** — Look up the company's corporate registration for officer names and addresses
3. **Web Crawl** — Visit the company's website to pull emails, phone numbers, and a description of what they do
4. **Hunter.io** — A third-party service that finds email addresses associated with a company's website

An AI then writes a short summary of each company based on what was found.

### Step 5: Present in a Web App
Everything is displayed in a web-based dashboard where you can:

- **Filter and search** leads by borough, building count, score, entity type, and more
- **View detailed profiles** for any lead — contact info, building portfolio, violations, revenue estimate
- **Track outreach** through a pipeline (Research → First Contact → Follow-Up → Meeting → LOI → Due Diligence → Closed)
- **Set follow-up reminders** and log outreach attempts
- **Generate due diligence reports** with a single click
- **Export to CSV** for offline work

---

## Full Data Flow (Current Production)

This is the end-to-end runtime/data flow now running in production:

1. **Ingestion trigger**
   - A user action or scheduled job hits a jobs endpoint (for buildings, violations, scoring, enrichment, etc.).
2. **Job creation**
   - Backend writes a canonical `ingestion_jobs` row in PostgreSQL with status `queued`.
3. **Dispatch path**
   - Primary: Celery task dispatch through Redis broker.
   - Fallback: in-process execution if worker dispatch fails.
4. **Worker execution**
   - Dedicated Railway worker service consumes tasks from Redis and runs task modules under `src/tasks/*`.
5. **Raw/source updates**
   - Source data lands in normalized DB tables (buildings, violations, complaints, contacts, etc.).
6. **Transform and aggregate**
   - Building-linked records aggregate into lead-level snapshots (`portfolio_size`, `total_units`, scoring inputs).
7. **Scoring/enrichment**
   - Scoring and contact/research enrichment tasks update lead records and derived metrics.
8. **API read path**
   - Frontend calls FastAPI endpoints; server-side SQL filtering/sorting returns paginated results.
9. **Operational visibility**
   - Queue health and worker health endpoints expose queue depth, failures, stale-running jobs, and worker/broker liveness.
10. **Recovery controls**
   - Admin recompute endpoints restore lead snapshots if drift occurs.
   - Stale-running reconciliation endpoint marks zombie jobs failed to keep queue state trustworthy.

### Key Data Integrity Guardrails
- Lead unit/building filters are protected by snapshot sync to reduce stale-value false negatives.
- Canonical status vocabulary (`queued/running/succeeded/failed`) is normalized in jobs APIs.
- Migration safety and API contract checks are enforced in CI.

---

## What Data Sources Does It Use?

All primary data comes from **free, public government databases**:

| Source | What It Provides | Where It Comes From |
|--------|-----------------|-------------------|
| HPD Building Registrations | Every registered residential building in NYC — address, managing agent, owner | NYC Open Data (city government) |
| HPD Contacts | Registered contact people for each building (names, titles, phone numbers) | NYC Open Data |
| HPD Violations | Building code violations — useful as a signal of distress or opportunity | NYC Open Data |
| PLUTO | Building classification — whether a building is a condo, co-op, rental, etc. | NYC Open Data |
| NY Dept. of State (DOS) | Corporate registration records — officer names, filing dates, entity type | NY State Open Data |

For enrichment (finding contact info), it also uses:

| Source | What It Provides | Cost |
|--------|-----------------|------|
| Google Places API | Phone numbers, websites, and addresses from Google's business listings | Pay-per-use (small cost) |
| Hunter.io | Email addresses associated with a company's web domain | Free tier available |
| Company Websites | Emails, phone numbers, company descriptions (found by visiting the site) | Free |
| Claude AI | Auto-generated company summaries based on all collected information | Pay-per-use (small cost) |

---

## Current Numbers

| Metric | Value |
|--------|-------|
| Total leads in the system | 314,723 |
| Total buildings tracked | 179,985 |
| Zero-link leads | 55,804 |
| Blank display-name leads | 54,507 |
| Same-entity duplicate current links | 0 |
| Latest lead_generation job | Succeeded through normal worker path |

---

## Where Does It Live?

- **The website** (Vercel): `https://frontend-nine-psi-58.vercel.app`
- **The backend API** (Railway): `https://hpd-leads-app-production.up.railway.app`
- **The code repository** (GitHub): `https://github.com/michaelbloom01/hpd-leads-app`
- **Background worker runtime**: dedicated Railway worker service + Redis broker (`hpd-leads-worker` + managed Redis)

No special software is needed to use it — just open the website in a browser.
