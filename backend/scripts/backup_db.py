#!/usr/bin/env python3
"""
R3: SQLite database backup script.

Creates a timestamped copy of the SQLite database on the same volume.
Designed to be run periodically (e.g., cron or Railway scheduled task).

Usage:
    python scripts/backup_db.py               # Backs up to data/backups/
    python scripts/backup_db.py --max-backups 5  # Keep only 5 most recent
"""
import argparse
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Determine DB path
DB_PATH_ENV = os.environ.get("DATABASE_PATH")
if DB_PATH_ENV:
    DB_PATH = Path(DB_PATH_ENV)
else:
    DB_PATH = Path(__file__).parent.parent / "data" / "leads.db"

BACKUP_DIR = DB_PATH.parent / "backups"


def backup_database(max_backups: int = 7) -> Path:
    """Create a timestamped backup of the SQLite database.
    
    Args:
        max_backups: Maximum number of backup files to keep (oldest are pruned).
        
    Returns:
        Path to the new backup file.
    """
    if not DB_PATH.exists():
        logger.error(f"Database not found at {DB_PATH}")
        sys.exit(1)
    
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"leads_backup_{timestamp}.db"
    backup_path = BACKUP_DIR / backup_name
    
    db_size_mb = DB_PATH.stat().st_size / (1024 * 1024)
    logger.info(f"Backing up {DB_PATH} ({db_size_mb:.1f} MB) -> {backup_path}")
    
    # Use shutil.copy2 to preserve metadata
    shutil.copy2(DB_PATH, backup_path)
    
    # Also copy WAL and SHM files if they exist (for consistency)
    for suffix in ["-wal", "-shm"]:
        wal_path = DB_PATH.parent / (DB_PATH.name + suffix)
        if wal_path.exists():
            shutil.copy2(wal_path, BACKUP_DIR / (backup_name + suffix))
            logger.info(f"  Also backed up {wal_path.name}")
    
    logger.info(f"Backup complete: {backup_path}")
    
    # Prune old backups
    if max_backups > 0:
        existing = sorted(BACKUP_DIR.glob("leads_backup_*.db"), key=lambda p: p.name)
        while len(existing) > max_backups:
            old = existing.pop(0)
            old.unlink()
            # Remove associated WAL/SHM files
            for suffix in ["-wal", "-shm"]:
                sidecar = BACKUP_DIR / (old.name + suffix)
                if sidecar.exists():
                    sidecar.unlink()
            logger.info(f"  Pruned old backup: {old.name}")
    
    return backup_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backup the Double Edge SQLite database")
    parser.add_argument("--max-backups", type=int, default=7, help="Max backup files to keep (default: 7)")
    args = parser.parse_args()
    
    backup_database(max_backups=args.max_backups)
