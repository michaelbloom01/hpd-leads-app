# Double Edge Frontend

React + TypeScript + Vite frontend for Double Edge (formerly HPD Leads).

## Current Scope

- Leads workflow (filtering, enrichment actions, pipeline management)
- Buildings workflow (building-level targeting and outreach context)
- Smart Lists workflow (saved segments + change evaluation)
- Dashboard metrics and operational visibility
- AI Agent interaction panel

## Run Locally

```bash
npm install
npm run dev
```

Default dev URL: `http://localhost:5173`

## Environment

Create `.env.development`:

```bash
VITE_API_URL=http://localhost:8000
```

For production, set `VITE_API_URL` to deployed backend.

## Key Entry Points

- `App.tsx` - app shell, routing, auth guard, route boundaries
- `components/LeadTable.tsx` - primary PE sourcing workflow
- `components/LeadDetail.tsx` - lead operations modal
- `components/BuildingsPage.tsx` - PM operator workflow
- `components/SmartListsPage.tsx` - saved segment workflow
- `components/Dashboard.tsx` - KPI and action hub
- `services/api.ts` - typed API contract layer

## Architecture Constraints (Execution Baseline)

- UI reads through backend API contracts only.
- No frontend-side truth store for business state.
- Smart Lists, Leads, and Pipeline workflows must remain contract-aligned.
- Critical workflow regressions are blocked by test and phase gates.
