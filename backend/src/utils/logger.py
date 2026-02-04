"""
Logging configuration.

See docs/07-scheduling-ops.md for log format.
"""
import logging
import sys
from datetime import datetime
from pathlib import Path

from config.settings import settings


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """
    Configure logging for the pipeline.
    
    Args:
        level: Logging level (default INFO)
        
    Returns:
        Root logger
    """
    # Create log directory
    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Log file path
    log_file = log_dir / f"pipeline_{datetime.now():%Y%m%d}.log"
    
    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    # Console handler (INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(console)
    
    # File handler (DEBUG)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(file_handler)
    
    return logger
