#!/usr/bin/env python3
"""Startup script that reads PORT from environment and starts gunicorn."""
import os
import subprocess
import sys

port = os.environ.get("PORT", "8000")
print(f"Starting server on port {port}")

cmd = [
    "gunicorn",
    "api:app",
    "--workers", "1",
    "--worker-class", "uvicorn.workers.UvicornWorker",
    "--bind", f"0.0.0.0:{port}",
    "--timeout", "120",
]

print(f"Running: {' '.join(cmd)}")
sys.exit(subprocess.call(cmd))
