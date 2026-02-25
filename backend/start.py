#!/usr/bin/env python3
"""Startup script that reads mode from env and starts API or worker."""
import logging
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

worker_mode = os.environ.get("WORKER_MODE", "").strip() in {"1", "true", "TRUE", "yes", "YES"}

if worker_mode:
    worker_cmd = [
        "celery",
        "-A",
        "src.worker.app",
        "worker",
        "--loglevel=info",
        "--concurrency=2",
    ]
    logger.info("Starting Celery worker mode")
    logger.info("Running: %s", " ".join(worker_cmd))
    worker_proc = subprocess.Popen(worker_cmd)

    class _HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler naming
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"worker-ok")

        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            return

    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    server.timeout = 1.0
    logger.info("Worker health server listening on port %s", port)

    try:
        while True:
            rc = worker_proc.poll()
            if rc is not None:
                logger.error("Celery worker exited with code %s", rc)
                sys.exit(rc)
            server.handle_request()
    finally:
        try:
            worker_proc.terminate()
        except Exception:
            pass
else:
    port = os.environ.get("PORT", "8000")
    logger.info(f"Starting API server on port {port}")
    cmd = [
        "gunicorn",
        "api:app",
        "--workers", "2",
        "--worker-class", "uvicorn.workers.UvicornWorker",
        "--bind", f"0.0.0.0:{port}",
        "--timeout", "300",
    ]
    logger.info("Running: %s", " ".join(cmd))
    sys.exit(subprocess.call(cmd))
