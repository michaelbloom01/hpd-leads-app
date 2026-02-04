# HPD Leads API - Railway Deployment
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Railway injects PORT env var - default to 8000 for local testing
ENV PORT=8000

# Start the server - use shell form with /bin/sh -c to expand $PORT
CMD ["/bin/sh", "-c", "gunicorn api:app --workers 1 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT --timeout 120"]
