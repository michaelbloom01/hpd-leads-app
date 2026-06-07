# Double Edge Agent Guide

This file is model-neutral. Use it before `CLAUDE_CONTEXT.md`, `CODEX_REVIEW_HANDOFF.md`, or broad repo scans.

## Purpose

`C:\Users\micha\Projects\hpd-leads-app` is Double Edge, a NYC property management intelligence platform for:

- PE acquisition sourcing, finding PM companies and portfolios worth diligencing.
- PM operator lead generation, finding buildings and entities ripe for outreach.

Live frontend: `https://frontend-nine-psi-58.vercel.app`
Live backend: `https://hpd-leads-app-production.up.railway.app`
GitHub: `https://github.com/michaelbloom01/hpd-leads-app`

## Current caution

This repo often has active in-progress work. Always run git status before editing and do not revert changes you did not make.

```powershell
cd C:\Users\micha\Projects\hpd-leads-app
git status --short
```

## Architecture

- `backend/`: Python FastAPI on Railway, PostgreSQL, async SQLAlchemy, ingestion/scoring/enrichment/workers.
- `frontend/`: React, TypeScript, Vite, Tailwind, Vercel.
- `docs/`: archived reviews and project notes.

Backend service: Railway app API plus worker.
Frontend service: Vercel static app.

## Common commands

Backend:

```powershell
cd C:\Users\micha\Projects\hpd-leads-app\backend
python -m pytest
python -m uvicorn api:app --reload --port 8000
```

Frontend:

```powershell
cd C:\Users\micha\Projects\hpd-leads-app\frontend
npm run test:run
npm run build
npm run dev
```

## Product surfaces to protect

- Leads tab: filter, sort, pagination, URL-persisted filters, lead modal, enrichment actions.
- Dashboard: metrics, follow-ups, ready-to-contact panels.
- Lead detail modal: contacts, pipeline, buildings, due diligence.
- Smart Lists and Building Lists.
- Backend lead listing stability and computed columns.

If asked for UX QA, start with Leads tab workflows and confirm the frontend talks to the expected backend.

## Deploy

Backend deploys through Railway from the repository's production branch and worker setup. Confirm current branch and deployment expectations before pushing.

Frontend production deploy:

```powershell
cd C:\Users\micha\Projects\hpd-leads-app\frontend
npx vercel --prod --yes
```

Pushing code may redeploy backend services and interrupt background enrichment. Push code before starting enrichment jobs.

## Gotchas

- Legacy SQLite paths should not reappear. PostgreSQL is the canonical runtime data path.
- Frontend uses React 18 and `react-leaflet` 4.2.1. Avoid upgrading to a version known to crash without explicit testing.
- Broad manual click-throughs are expensive. Prefer repeatable Vitest or Playwright smoke tests for recurring checks.
- Do not bulk-delete orphan or blank leads without an explicit reconciliation plan and approval.
- Treat production data mutations and enrichment jobs as high-impact operations.
