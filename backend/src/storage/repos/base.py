"""Base repository providing database connection access."""
import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


class BaseRepository:
    """Provides shared database connection access for all repos."""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    @contextmanager
    def _get_connection(self):
        """Get a connection with WAL mode and row_factory set."""
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()
