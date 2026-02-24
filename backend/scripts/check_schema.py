import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["DATABASE_URL"])
with engine.connect() as conn:
    cols = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='buildings' ORDER BY ordinal_position")).fetchall()
    print("buildings columns:", [c[0] for c in cols])
