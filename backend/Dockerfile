# HPD Leads API - Railway Deployment v2 - Cache bust
FROM python:3.12-slim

# Force cache invalidation
ARG CACHEBUST=1

WORKDIR /app

# Install dependencies first
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Start using Python script that reads PORT env var
ENTRYPOINT ["python", "start.py"]
