# Compliance and Legal Notes

## Data Source Licensing

### NYC Open Data (HPD Registrations)
- **License:** Public Domain (CC0)
- **Terms:** https://opendata.cityofnewyork.us/overview/#termsofuse
- **Attribution:** Not required, but nice to include
- **Commercial use:** Allowed
- **Restrictions:** None

### NY DOS Corporation Data
- **License:** Public Record
- **Terms:** Standard NY FOIL (Freedom of Information Law)
- **Commercial use:** Allowed
- **Restrictions:** None for public data

### Web Scraping
- **General rule:** Public information on public websites is generally legal to scrape
- **Respect robots.txt:** Always check and follow
- **Rate limits:** Don't overload servers (1 req/sec is conservative)
- **Terms of service:** Some sites prohibit scraping; check TOS if unsure

---

## Rate Limits and Fair Use

### NYC Open Data (Socrata)
- **Without app token:** 1,000 requests/hour
- **With app token:** Much higher (not published, but generous)
- **Best practice:** Use app token, paginate with $offset and $limit

### SerpAPI
- **Free tier:** 100 searches/month
- **Paid:** $50/month for 5,000 searches
- **Best practice:** Cache results, don't search same company twice

### Apollo.io
- **Free tier:** 50 credits/month
- **Paid:** Starts at $49/month
- **Best practice:** Only use for high-priority leads

---

## Privacy Considerations

### What We're Collecting
- Business entity names (not individuals acting privately)
- Business contact information (public records)
- Business addresses (public records)
- Professional information about property managers

### What We're NOT Collecting
- Personal home addresses
- Personal phone numbers
- Social security numbers
- Financial information

### GDPR / CCPA
- This data is about businesses, not individuals in a personal capacity
- Property management is a professional/commercial activity
- Public record exemptions typically apply
- If someone requests removal, honor it

---

## Ethical Guidelines

### Do
- Use data only for legitimate business outreach
- Respect "do not contact" requests
- Keep data secure
- Delete data you no longer need

### Don't
- Sell or share the lead list
- Use for spam or harassment
- Misrepresent yourself in outreach
- Scrape sites that explicitly prohibit it

---

## Data Retention

### Active Data
- Google Sheet: Retained indefinitely (useful for tracking)
- Raw API responses: 7 days (development/debugging)
- Enrichment cache: 30 days (avoid re-fetching)

### Deletion Policy
- Remove leads older than 2 years with no activity
- Delete raw data after successful processing
- Clear cache on request

---

## Documentation for Transparency

In the Google Sheet, include a "Data Sources" note:

```
This lead list is compiled from:
1. NYC HPD Multiple Dwelling Registrations (public data)
2. NY DOS Corporation Records (public records)
3. Public company websites
4. Apollo.io enrichment API

Data is refreshed daily. For questions or removal requests, 
contact: michaelbloom01@gmail.com

Last updated: {date}
```

---

## Incident Response

### If Someone Complains
1. Acknowledge receipt promptly
2. Remove their data if requested
3. Add to "do not contact" list
4. Document the interaction

### If API Access Revoked
1. Check if we violated terms
2. Apply for reinstatement if appropriate
3. Use alternative data source
4. Document incident

---

## Disclaimer

This document provides general guidance, not legal advice. For specific legal questions about data use, web scraping, or commercial solicitation, consult an attorney.

Michael's context: As someone reaching out to potential acquisition targets, outreach is legitimate business development, not spam. Be professional, identify yourself clearly, and respect "no" as an answer.
