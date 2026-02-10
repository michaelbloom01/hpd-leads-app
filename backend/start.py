#!/usr/bin/env python3
"""Startup script that reads PORT from environment and starts gunicorn."""
import logging
import os
import subprocess
import sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

port = os.environ.get("PORT", "8000")
logger.info(f"Starting server on port {port}")

cmd = [
    "gunicorn",
    "api:app",
    "--workers", "1",
    "--worker-class", "uvicorn.workers.UvicornWorker",
    "--bind", f"0.0.0.0:{port}",
    "--timeout", "120",
]

logger.info(f"Running: {' '.join(cmd)}")
sys.exit(subprocess.call(cmd))
