# Double Edge - Product Plan

## Status: Production Live End-to-End (Feb 2026)

Double Edge (formerly HPD Leads) is a dual-purpose NYC housing intelligence platform:
- **PE/Acquirer view (Leads tab):** Source and evaluate PM businesses for acquisition
- **PM Operator view (Buildings tab):** Find buildings ripe for high-value outreach

## Completed Phases

### Phase 0: Entity Classification
- Leads classified as Company, Individual Agent, or Owner-Operator
- Company names resolved for person-named agents
- Primary contact and title populated from HPD data

### Phase 1: Performance
- PostgreSQL with async SQLAlchemy (migrated from SQLite)
- Server-side filtering and pagination with parameterized queries
- SQL-indexed queries with sub-second response times

### Phase 2: Enrichment
- 4-tier cascade: Google Places -> NY DOS -> Web Crawl -> Hunter.io
- Preview-first "Enrich Lead" action (contacts + research + AI summary in one approval-gated workflow)
- Retry logic with background processing

### Phase 3: UX Features
- Multi-borough filtering, units/building computed filters
- Contact columns with click-to-call/email
- CSV export (server-side and client-side fallback)
- Address search alongside PM company search

### Phase 4: Reliability
- JWT authentication
- Rate limiting (slowapi)
- fetchWithRetry with toast-based error classification (401/403/404/422/500/timeout)
- Cold-start detection with health polling

### Phase 5: PE-Grade Sourcing
- **5.1 Revenue Estimation**: Per-lead and bulk, borough/type-adjusted
- **5.2 HPD Violations**: Class A/B/C, per-unit normalized, distress signals
- **5.3 Outreach Pipeline**: 8 stages, follow-up dates, priority ranking, event logging, email templates
- **5.4 Auto-Refresh**: Change detection alerts, stale data warnings
- **5.5 Due Diligence**: Quick Risk Snapshot (full DD reports coming soon)
- **5.6 Scoring V2**: 8-dimension scoring

### Phase 6: Hardening & Smart Lists
- **Rebrand**: HPD Leads -> Double Edge across all surfaces
- **HPD branding removal**: "Connected" -> "API connected"; "HPD Violations/Litigation/coverage" -> "Housing Violations/Litigation/data coverage"; metadata, package name updated
- **SQL hardening**: Whitelist-validated sort columns, parameterized WHERE clauses
- **useLeadFilters hook**: Consolidated 20+ filter useState calls into single useReducer
- **Global error layer**: Toast-based error classification in fetchWithRetry
- **Accessibility**: ESC-to-close, focus trapping, ARIA labels, focus-visible indicators
- **URL filter persistence**: useFilterUrl hook syncs filters to/from URL search params
- **Smart Lists**: Saved filter segments with CRUD, change detection (evaluate), and pin-to-dashboard
- **Building Lists**: Saved building collections with CRUD and member management
- **404 route**: Catch-all with styled page
- **Bug fixes**: Multi-borough filter, CSV export path, per-lead revenue endpoint, building_type_has and units_per_bldg filters, leads query-time dedupe collision
- **Leads completion tranche**: Production-safe lead browsing, canonical read APIs, route/query workflow closure, and task-first dashboard/list UX

### Phase 7: Data Truth & Confidence Foundation
- **Canonical truth model**: Additive schema for truth claims, evidence, confidence snapshots, review items, validation runs, and golden cases
- **Evidence-backed truth summaries**: Lead and generic subject summaries expose beliefs, sources, contradictions, freshness, confidence, canonical memberships, and actionability across leads, entities, buildings, contacts, and people
- **Building truth fallback**: Building summaries infer read-only current beliefs from `buildings`, current management links, and HPD contacts before persisted ledger claims are materialized
- **Contact truth fallback**: Public `contact/{id}` and internal `hpd_contact/{id}` summaries expose HPD role, mailing-address, and building-link evidence instead of losing contact truth behind subject-type naming
- **Canonical/entity truth fallback**: Canonical entity summaries expose identity, aliases, lead memberships, building memberships, and open match proposals with contradiction flags for weak candidate memberships and unsafe proposals
- **Person truth fallback**: Person summaries resolve HPD officer/contact names and enrichment owner-principal observations, while flagging same-name and multi-address ambiguity instead of implying a clean individual identity
- **Outreach feedback truth loop**: Existing lead, building, canonical-entity, and target-list outreach outcomes feed subject summaries as read-only support or contradiction evidence before migration, and truth materialization can backfill the same feedback into stable claim/evidence ledger rows after approval
- **Building-level truth UI**: Building Detail surfaces claim-ledger confidence, contradictions, source names, freshness, and safe actions directly on the building record
- **Contact safe-action labels**: Building and lead contact views classify HPD/DOS records as research paths, verification-only legal/mailing evidence, stale evidence, or possible manager paths instead of implying every contact is outreach-ready
- **Review workflow**: Truth review queues show proposed changes, support, contradiction, rationale, and dry-run decisions before any status change
- **Source audit dashboard**: Settings surfaces source freshness, schema readiness, stale/missing public evidence, refreshable jobs, blocked jobs, and truth-health posture
- **Activation approval packet**: Settings and `/api/v1/truth/activation-packet` expose the current schema verdict, blocked business-use state, next safe commands, approval-required steps, first approval-gated source refresh jobs with source names/statuses, and rollback preview command before any migration/materialization/source refresh
- **Rollback-manifested materialization**: Revision `010_truth_manifest` adds a per-run rollback manifest for claim/evidence/snapshot upserts; pilot run `truth-materialization-manual-20260514142022` wrote 2,063 claims and can be preview-rolled back by run ID
- **Validation-to-review safety**: Adversarial validation preview is now no-mutation, confirmed execution can seed run-scoped review items, and validation rollback preserves reviewed decisions by default
- **External verification source audit**: Source readiness now includes NY DOS cache, Google Places, Hunter, company website/web crawl enrichment, and outreach feedback in addition to table-backed public datasets
- **Schema-gated actionability rules**: The truth dashboard still returns the actionability threshold ladder before migration, keeping discovery/outreach/diligence safety criteria visible while the ledger is not yet active
- **Golden benchmark**: Seed cases cover co-op boards, shell LLCs, stale agents, legal suffix variants, shared addresses, false splits, and outreach contradictions with subject IDs plus required/forbidden claim expectations so precision, recall, false-merge, false-split, building-link, contact, and freshness metrics are actually exercised
- **Approval-first mutations**: Truth jobs, public-source refreshes, enrichment, scoring, quality checks, lead generation, reconciliation, selected-lead enrichment, and single-lead enrichment default to previews and require `dry_run=false&confirm_execute=true` to mutate data
- **Current live-data caveat**: The code foundation is implemented, local schema `010_truth_manifest` has been applied, and a pilot ledger materialization exists, but live trust posture remains `not_ready` until verified claims, fuller materialization/review, and stale/missing public-source refresh or manual evidence review clear the gates

## Remaining Backlog

1. **Full Due Diligence reports** — AI-generated with comparables (deferred)
2. **Kanban view** for pipeline stages
3. **Apollo.io** integration for deeper contact discovery
4. **Email digest** for weekly change alerts
5. **Historical violation trending**
6. **Component extraction** — split LeadTable.tsx into smaller components

## Outbound Delivery Infrastructure (Levi Munneke pattern, Apr 2026)

Source: Levi Munneke post on X, March 2026, "Google Maps + Claude Code = 7-figure outbound team." Captured from Michael's AI-tabs dump, April 23, 2026. The idea: Double Edge generates qualified leads, but without cold-email delivery infrastructure the conversion from lead to reply caps at manual sending rates. Levi's pattern scales that.

**Target state:** Double Edge lead enrichment + an always-on outbound layer that delivers 500-2,000 touches per day across email, LinkedIn, and (optionally) Twitter, with deliverability protected and replies auto-routed back into the 8-stage outreach pipeline.

### Outbound-1: VPS + agent infrastructure
- Provision a Hetzner or Hostinger VPS ($7-12 / month), Ubuntu.
- Install Claude Code, tmux (persistent sessions survive connection drops), Tailscale VPN so you can access it from laptop or phone.
- Cron the outbound scripts to run at 6am ET daily.
- Telegram bot alerts: "1,500 sent, 14 replies, 4 booked today."

### Outbound-2: Email delivery infrastructure
- 35 sending domains (mix of .com / .co variants of the main brand, lookalikes, neutral names), each with proper SPF / DKIM / DMARC.
- 3 inboxes per domain = 105 inboxes total. Warmup each inbox for 2-3 weeks before heavy use.
- 2,000 sends / day cap across the fleet.
- Delivery platform: **Instantly** or **SmartLead** (Instantly preferred for integration breadth).
- Warmup networks: Instantly / SmartLead built-in warmup. Keep every domain in warmup perpetually.
- Bounce handling + soft-bounce recovery auto-routed via delivery platform.

### Outbound-3: Sequence content (tied to HPD data)
Templates triggered by specific lead characteristics in Double Edge:
- **No website detected** → "couldn't find a website attached to your property's public listing, happy to show you a free draft of what a modern one could look like for you"
- **Class A+B violations above threshold** → compliance-framed outreach referencing the specific address
- **Low revenue estimate relative to unit count** → operational-improvement framing
- **Outdated contact info** → "just confirming you're still the owner / manager of [address]" (lower-stakes first-touch)

Follow-ups at Day 3, 5, 8, 14 if lead shows engagement signal (open, click) but no reply. Auto-qualify replies. Auto-book via Cal.com integration or equivalent.

### Outbound-4: LinkedIn warm layer
- Claude Code cross-references Double Edge leads against LinkedIn via a lookup API (Apify or similar).
- Send 30-40 soft connect requests per day at human volume (throttle to avoid LinkedIn account risk).
- Not pitching in the connect request. Building familiarity before email lands.

### Outbound-5: Twitter / X monitoring (bonus)
- Claude Code keyword monitor for local NYC PM operators venting about slow business, bad leads, Google visibility, HPD violations, inspection headaches.
- Queue DMs for Michael to review. Low volume, high intent, zero competition.

### Math (Levi's pattern, adjusted for NYC PM market size)
- ~1,600 daily touches across email + LinkedIn + Twitter
- ~50,000 targeted touches / month
- Reply rate on hyper-local + HPD-data-personalized cold email: 2-6% (higher than generic because the HPD signal is specific)
- Expected: 20-40 qualified discovery calls / month
- At 25% close rate and $5-10k retainers: 5-10 new clients / month
- Infrastructure cost: $200-400 / month (VPS + domains + Instantly / SmartLead + LinkedIn tooling)

### Build sequence (2 afternoons, not 2 weeks)
1. Monday: VPS + Claude Code + tmux + Tailscale live.
2. Tuesday: Double Edge → enrichment pipeline → CSV export for outbound (build bridge to delivery platform).
3. Wednesday: Instantly / SmartLead integration + 200 test sends, watch deliverability.
4. Thursday: LinkedIn cross-reference + Twitter monitor active.
5. Friday: Telegram bot alerts + Cal.com booking integration.

## Programmatic SEO (Atheyst pattern, Apr 2026)

Source: Atheyst post on X, April 12, 2026, claiming $20M in bid requests over 60 days via SEO-stuffed city x service pages following @irentdumpsters playbook. Captured from Michael's AI-tabs dump, April 23, 2026. Note: $20M is for a "large scale site development" company; NYC PM will have smaller per-lead values but the mechanic is the same.

**Target state:** organic Google ranking for "property management [NYC neighborhood]", "HOA management [city]", "[borough] rental management" across all five boroughs + LI + Westchester.

### SEO-1: Page matrix
- Boroughs: Manhattan, Brooklyn, Queens, Bronx, Staten Island, Westchester, Long Island.
- Sub-neighborhoods within each borough (20-40 per borough; use HPD data to prioritize the ones with highest building counts).
- Services: property management, HOA management, rental management, commercial PM, tenant management, compliance services (HPD-violation-focused).
- Tertiary services: lease-up, rent collection, maintenance coordination, violation cure.
- Total page matrix: ~1,500-2,500 pages (boroughs × neighborhoods × services × tertiary).

### SEO-2: Content generation
- Claude Code pipeline generates unique content per page using Double Edge data as inputs (real building counts, typical violations, local context).
- Each page must be substantively different (not just keyword swap). Pull real HPD data for local signals.
- Content structure: local market overview, common compliance challenges, service details, CTA to contact form.

### SEO-3: Technical SEO
- Next.js SSR or static generation for all pages.
- Proper schema markup (LocalBusiness, Service).
- Clean URL structure: `/property-management/[borough]/[neighborhood]`.
- Sitemap.xml auto-generated.
- Internal linking between related pages (neighborhood clusters, service clusters).

### SEO-4: Patience and measurement
- Leads start appearing after ~30 days per Atheyst.
- Track: organic impressions, clicks, form submissions, discovery calls attributed to organic.
- Don't add paid SEO tooling beyond Google Search Console until organic has traction.

### Cost
- Hosting: existing Double Edge infrastructure.
- Content generation: Claude API tokens, ~$50-100 for initial 1,500 pages, then negligible.
- Tooling: Google Search Console (free), Ahrefs or similar optional ($100-200 / month if scaling).

### Open question
This SEO play is for the **Operator view** of Double Edge (PM operators looking to acquire more buildings to manage), not directly for the **PE / Acquirer view** (Michael sourcing PM companies to buy). Both are part of the dual-purpose product. The SEO volume is on the Operator side, the revenue is Michael's target companies wanting to buy the leads tool. Wire accordingly.

## Architecture Convergence Program (Pre-Backlog Gate)

Execution is temporarily sequenced through three foundation tracks before deeper backlog expansion:

1. **Runtime Convergence** — canonical PostgreSQL runtime path, no split semantics
2. **Durable Async Platform** — queue + worker for long-running ingestion/enrichment/scoring
3. **Delivery Confidence** — migration safety, critical-path tests, and CI gates

Backlog items above continue after these gates are complete to avoid piecemeal execution and regressions.

### Convergence Status (Completed)

All three convergence tracks are now implemented in the active codebase:

- Runtime is PostgreSQL-first by default; legacy SQLite routers are opt-in only.
- Jobs execute through a canonical queue lifecycle with worker-first dispatch and safe in-process fallback.
- Delivery confidence includes migration safety guards, backend + frontend automated tests, and CI enforcement.
- Production now runs with Redis configured and a dedicated worker service for Celery execution/health.
- Live production data has been reconciled via admin recompute endpoints to restore PM surfacing filter accuracy.
