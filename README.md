# HPD Leads App

NYC property management lead generation pipeline with web interface.

## Architecture

```
hpd-leads-app/
├── backend/          # Python FastAPI server (Railway)
│   ├── api.py        # REST API endpoints
│   ├── src/          # Pipeline code (ingest, transform, score, enrich)
│   └── requirements.txt
├── frontend/         # Web UI (Vercel) - from Google AI Studio
└── README.md
```

## Backend API

The backend exposes these endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check |
| `/api/leads` | GET | Get leads with filtering |
| `/api/leads/{id}` | GET | Get single lead |
| `/api/status` | GET | Pipeline status |
| `/api/refresh` | POST | Refresh data from HPD |
| `/api/enrich` | POST | Enrich specific leads |
| `/api/enrich/batch` | POST | Auto-enrich top leads |

### Query Parameters for `/api/leads`

- `min_score` - Filter by minimum score
- `min_portfolio` - Filter by minimum portfolio size
- `boro` - Filter by borough (Manhattan, Brooklyn, etc.)
- `has_website` - Filter by website availability
- `has_email` - Filter by email availability
- `limit` - Max results (default 100, max 500)
- `offset` - Pagination offset

## Local Development

### Backend

```bash
cd backend
pip install -r requirements.txt
python api.py
# API runs at http://localhost:8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# UI runs at http://localhost:5173 (or similar)
```

## Deployment

### Backend → Railway

1. Connect Railway to this repo
2. Set root directory to `backend`
3. Railway auto-detects Python and runs `uvicorn api:app --host 0.0.0.0 --port $PORT`

### Frontend → Vercel

1. Connect Vercel to this repo
2. Set root directory to `frontend`
3. Set environment variable `VITE_API_URL` to your Railway backend URL

## Environment Variables

### Backend (.env)

```
NYC_OPEN_DATA_APP_TOKEN=optional_for_higher_rate_limits
```

### Frontend

```
VITE_API_URL=https://your-backend.railway.app
```
