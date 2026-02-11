"""
Structured logging configuration for HPD Leads backend.
Provides consistent log formatting across all modules.
"""
import logging
import os
import sys
from datetime import datetime


class StructuredFormatter(logging.Formatter):
    """JSON-like structured log formatter for production."""
    
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).isoformat()
        level = record.levelname
        logger_name = record.name
        message = record.getMessage()
        
        # Base log entry
        parts = [
            f"ts={timestamp}",
            f"level={level}",
            f"logger={logger_name}",
            f"msg=\"{message}\"",
        ]
        
        # Add exception info if present
        if record.exc_info and record.exc_info[0]:
            parts.append(f"error={record.exc_info[0].__name__}: {record.exc_info[1]}")
        
        # Add extra fields if present
        for key in ('lead_id', 'user_id', 'endpoint', 'duration_ms', 'status_code'):
            val = getattr(record, key, None)
            if val is not None:
                parts.append(f"{key}={val}")
        
        return " ".join(parts)


class ReadableFormatter(logging.Formatter):
    """Human-readable formatter for development."""
    
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        level_colors = {
            'DEBUG': '\033[36m',     # Cyan
            'INFO': '\033[32m',      # Green
            'WARNING': '\033[33m',   # Yellow
            'ERROR': '\033[31m',     # Red
            'CRITICAL': '\033[35m',  # Magenta
        }
        reset = '\033[0m'
        color = level_colors.get(record.levelname, '')
        
        msg = f"{timestamp} {color}{record.levelname:8s}{reset} [{record.name}] {record.getMessage()}"
        
        if record.exc_info and record.exc_info[0]:
            msg += f"\n  {record.exc_info[0].__name__}: {record.exc_info[1]}"
        
        return msg


def setup_logging(level: str = "INFO"):
    """
    Configure logging for the application.
    Uses structured format in production, readable format in development.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    # Determine environment
    is_production = os.environ.get("ENVIRONMENT", "development") == "production"
    
    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    
    if is_production:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(ReadableFormatter())
    
    # Configure root logger
    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers = [handler]
    
    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    
    logging.getLogger(__name__).info(
        f"Logging configured: level={level}, format={'structured' if is_production else 'readable'}"
    )
