# HPD Leads API - Railway Deployment
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Use Python script to handle PORT - avoids shell expansion issues
CMD ["python", "start.py"]
