# Data Sources

## Primary: HPD Multiple Dwelling Registrations

### What it is
NYC Department of Housing Preservation and Development requires owners of residential rental buildings to register annually. This dataset contains all registrations.

### Coverage
- Buildings with 3+ residential units
- 1-2 family homes where owner doesn't reside
- All NYC boroughs

### Access
- **Platform:** NYC Open Data (Socrata)
- **Endpoint:** `https://data.cityofnewyork.us/resource/vx8i-nprf.json`
- **Format:** JSON, CSV, XML
- **Rate limits:** 1000 rows/request without token, higher with app token
- **Refresh:** Updated periodically by HPD

### Key Fields (expected)
```
RegistrationID
BuildingID
BoroID / Borough
HouseNumber, StreetName, Apartment, Zip
Block, Lot
BIN (Building Identification Number)
RegistrationContactID
Type (Owner, Agent, etc.)
ContactDescription
CorporationName
Title
FirstName, MiddleInitial, LastName
BusinessHouseNumber, BusinessStreetName, BusinessApartment
BusinessCity, BusinessState, BusinessZip
```

### App Token
Optional but recommended. Get one at: https://data.cityofnewyork.us/profile/edit/developer_settings

### API Documentation
- Socrata docs: https://dev.socrata.com/
- Dataset page: https://data.cityofnewyork.us/Housing-Development/Multiple-Dwelling-Registrations/vx8i-nprf

---

## Secondary: NY DOS Corporation Database

### What it is
New York Department of State maintains records of all corporations, LLCs, and other business entities registered in NY.

### Use case
- Look up LLC/Corp details for management companies
- Find registered agent (often the owner)
- Verify entity status (active/inactive)

### Access
- **Dataset:** "Corporations and Other Entities: All Filings"
- **URL:** https://data.ny.gov/Economic-Development/Corporations-and-Other-Entities-All-Filings
- **Address dataset:** Separate dataset with entity addresses
- **Format:** CSV, JSON, XML

### Key Fields
```
DOS ID
Entity Name
Entity Type (LLC, CORP, etc.)
Current Entity Status
County
Jurisdiction
Date of Formation
DOS Process Name / Address (registered agent)
```

### Search Tool
Manual lookup: https://apps.dos.ny.gov/publicInquiry/

---

## Enrichment: Web Crawling

### Strategy
1. Search Google for `"{company name}" property management NYC`
2. Extract company website from results
3. Crawl website for:
   - About page → business summary
   - Contact page → phone, email
   - Team page → owner/principal names
   - Portfolio page → buildings managed

### Rate Limiting
- Google search: Use SerpAPI or similar (has free tier)
- Website crawl: 1 request/second, respect robots.txt

### Tools
- `requests` + `BeautifulSoup` for simple pages
- `playwright` or `selenium` for JS-heavy sites
- `trafilatura` for clean text extraction

---

## Enrichment: Paid APIs (Optional)

### Apollo.io
- **Free tier:** 50 credits/month
- **Use case:** Email/phone lookup by company domain
- **Docs:** https://docs.apollo.io/docs/enrich-people-data

### Clearbit
- **Pricing:** Contact sales (expensive)
- **Use case:** Company enrichment (size, industry, funding)

### People Data Labs
- **Free tier:** 100 API calls/month
- **Use case:** Person lookup by name + company

### Recommendation
Start with Apollo free tier. Only add others if hit rate is low.

---

## Data Quality Notes

### HPD Data
- Phone/email fields may be redacted or outdated
- Corporate names may be holding companies, not management firms
- Agent vs Owner distinction is important — agents are often the managers

### NY DOS Data
- Entity may be dissolved but still in database
- Registered agent address may be lawyer, not actual office
- Name variations (LLC vs L.L.C. vs Limited Liability Company)

### Web Data
- Some companies have no web presence
- Contact info may be behind forms, not scrapeable
- Business descriptions vary wildly in quality
