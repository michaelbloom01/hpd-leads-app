# Lessons Learned

## Data & Architecture

1. **HPD contacts have no phone/email** - The HPD Contacts dataset (feu5-w2e2) has 15 columns but phone/email are redacted from the bulk API. Business addresses (79% coverage) and person names (84% coverage) ARE available and are the best enrichment signals.

2. **Address-based Google Places search > name-based** - Searching by the HPD business address returns far better results than searching by legal entity name. Most NYC property management companies don't have Google Business listings under their legal LLC name.

3. **Entity classification matters** - ~14% of high-value leads are person-named agents who represent real companies. The `_classify_entity` function resolves company names from CorporateOwner contacts and HPD data, which dramatically improves enrichment targeting.

4. **In-memory processing doesn't scale** - With 102k leads, Python-side filtering caused API timeouts. SQL-indexed queries with `LIMIT`/`OFFSET` reduced response times from 60s+ to <1s.

5. **SQLite is sufficient for this scale** - 102k leads with full enrichment data fits comfortably in SQLite. The key was proper indexing (9+ indexes) and moving aggregation to SQL.

## Enrichment

6. **Persist all API caches to SQLite** - The NY DOS in-memory cache was lost on every Railway restart. Persisting to SQLite (`dos_cache`, `places_cache` tables) saves both money and API quota.

7. **Retry logic with a cap** - Failed enrichments should be retried up to 3 times. Beyond that, the lead likely has genuinely unfindable contact info.

8. **Rate limiting is essential** - Google Places, Hunter.io, and the free web search APIs all have rate limits. The enrichment cascade needs deliberate pacing.

## Frontend & UX

9. **Server-side pagination is mandatory** - Never fetch 100k+ leads to the client. The frontend uses `limit`/`offset` and the backend handles filtering in SQL.

10. **Default to high-value view** - Setting `min_portfolio=10` by default shows ~1,300 leads instead of 102k, making the tool immediately useful.

## DevOps

11. **PowerShell != Bash** - `&&` doesn't work in PowerShell (use `;` or separate commands). `curl` in PowerShell is an alias for `Invoke-WebRequest`, use `curl.exe` for actual curl. Heredocs don't work for git commit messages in PowerShell.

12. **CORS must be locked down** - Default `*` CORS origins are a security risk in production. Lock to the actual frontend URL + localhost.

13. **Thread safety matters even with GIL** - FastAPI with background threads for enrichment creates race conditions on shared `_leads_cache`. All reads need locks or should go through DB queries.

14. **Railway worker deploys can use the wrong root silently** - The dedicated worker service can drift away from the backend service's root/build settings. For `hpd-leads-worker`, `railway up . --path-as-root --service hpd-leads-worker` was required to force the backend Dockerfile instead of a failing repo-root Railpack build.

15. **Production-only table drift shows up in background jobs first** - The fixed lead-generation job surfaced a `data_quality_log` primary-key sequence mismatch only after the worker actually reached the quality-log write. Background job recovery code should self-heal common sequence drift instead of assuming perfect DB metadata.

16. **Legacy task paths often hide multiple runtime mismatches** - Fixing the `scripts...` import path was necessary but not sufficient. Once the worker could execute `lead_generation`, `quality_checks` then exposed timezone-awareness bugs and `change_alerts.dismissed` insert assumptions. End-to-end live verification matters more than a single green local test.

17. **Persisted map coordinates are safer than browser geocoding for normal UX** - Portfolio and building maps should default to stored coordinates with provenance. If coordinates are missing, operators should run a coordinate sync job rather than silently relying on approximate client-side geocoding.

18. **Background job hardening needs both task tests and router-dispatch tests** - It is not enough to test the low-level helper. For `building_coordinates`, regressions were only meaningfully covered after testing both the task lifecycle and the `/api/v1/jobs/{job_type}/start` fallback path.

## Patterns to Follow

- Read docs before coding
- Test with small data first
- Cache API responses (both in-memory and persistent)
- Respect rate limits
- Preserve user-entered data (notes, outreach status) across refreshes
- Use SQL for filtering/aggregation, not Python
- Always provide DB-backed fallback for cache reads
- Verify API and worker services separately on Railway when jobs fail in production
