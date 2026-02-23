"""One-time script to create the admin user in SQLite.
Run from backend/: python scripts/create_admin_user.py
"""
import os
import sys
import uuid
from pathlib import Path

# Load .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.storage.database import get_database
from src.auth import hash_password

def main():
    email = os.environ.get("ADMIN_EMAIL", "admin@example.com")
    password = os.environ.get("ADMIN_PASSWORD", "change-me")
    db = get_database()
    existing = db.get_user_by_email(email)
    if existing:
        print(f"User {email} already exists.")
        return
    uid = str(uuid.uuid4())
    db.create_user(uid, email, hash_password(password), role="admin")
    print(f"Created admin user: {email}")
    print("You can now log in with these credentials.")

if __name__ == "__main__":
    main()
