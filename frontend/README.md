# HPD Leads Frontend

This folder will contain the frontend code exported from Google AI Studio.

## Setup Instructions

1. Export/download your code from Google AI Studio
2. Extract the files into this `frontend/` folder
3. Update the API URL to point to your backend:
   - Look for API calls in the code (fetch, axios, etc.)
   - Replace the URL with your Railway backend URL or use environment variable

## Expected Structure

After you add your Google AI Studio export, this folder should look something like:

```
frontend/
├── index.html
├── package.json
├── src/
│   ├── App.jsx (or .tsx)
│   ├── main.jsx
│   └── components/
├── public/
└── vite.config.js (or similar)
```

## Connecting to Backend

The backend API is at:
- Local: `http://localhost:8000`
- Production: `https://your-app.railway.app` (after deployment)

Example API calls:

```javascript
// Get leads
const response = await fetch(`${API_URL}/api/leads?min_score=50&limit=50`);
const leads = await response.json();

// Refresh pipeline
await fetch(`${API_URL}/api/refresh`, { method: 'POST' });

// Enrich specific leads
await fetch(`${API_URL}/api/enrich`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ lead_ids: ['abc123', 'def456'] })
});
```
