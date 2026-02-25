# Double Edge API - Railway Deployment v2
FROM python:3.12-slim

# Force cache invalidation
ARG CACHEBUST=1

WORKDIR /app

# Install curl for HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install dependencies first
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Start using Python script that reads PORT env var
ENTRYPOINT ["python", "start.py"]

HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD sh -c 'if [ "${WORKER_MODE}" = "1" ] || [ "${WORKER_MODE}" = "true" ]; then exit 0; else curl -f http://localhost:${PORT:-8000}/api/health || exit 1; fi'
