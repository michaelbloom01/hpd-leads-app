import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from sqlalchemy import create_engine, text
from src.db.session import get_sync_url

engine = create_engine(get_sync_url())
with engine.connect() as conn:
    tables = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).fetchall()
    for t in tables:
        try:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {t[0]}")).scalar()
            print(f"{t[0]}: {count}")
        except Exception as e:
            print(f"{t[0]}: ERROR - {e}")
