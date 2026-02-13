"""User repository mixin — auth/user management."""
import sqlite3
import logging
from datetime import datetime
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class UserRepo:
    """Mixin: user CRUD for JWT auth."""

    def create_user(self, user_id: str, email: str, password_hash: str, role: str = "user") -> bool:
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO users (user_id, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    (user_id, email, password_hash, role),
                )
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            return False

    def get_user_by_email(self, email: str) -> Optional[Dict]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            return dict(row) if row else None

    def get_user_by_id(self, user_id: str) -> Optional[Dict]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            return dict(row) if row else None

    def update_user_password(self, user_id: str, new_password_hash: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("UPDATE users SET password_hash = ? WHERE user_id = ?", (new_password_hash, user_id))
            conn.commit()
            return cursor.rowcount > 0

    def update_user_last_login(self, user_id: str):
        with self._get_connection() as conn:
            conn.execute("UPDATE users SET last_login = ? WHERE user_id = ?", (datetime.now().isoformat(), user_id))
            conn.commit()

    def list_users(self) -> list:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT user_id, email, role, created_at, last_login FROM users ORDER BY created_at").fetchall()
            return [dict(r) for r in rows]
