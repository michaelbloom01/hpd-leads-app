"""Seed a default scoring configuration."""
import sys, os, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from src.db.session import get_sync_url

engine = create_engine(get_sync_url())
session = Session(engine)

weights = json.dumps({
    "violation_severity": 10,
    "complaint_frequency": 10,
    "management_changes": 10,
    "ownership_transfers": 10,
    "permit_activity": 10,
    "litigation_history": 10,
    "emergency_repairs": 10,
    "energy_grade": 10,
    "building_age": 10,
    "rent_stabilization": 10,
})

existing = session.execute(text("SELECT count(*) FROM scoring_configs")).scalar()
if existing == 0:
    session.execute(
        text("""INSERT INTO scoring_configs
            (name, description, weights, is_active, version, created_at, updated_at)
            VALUES ('Default', 'Default equal-weight scoring', :w::jsonb, true, 1, now(), now())"""),
        {"w": weights},
    )
    session.commit()
    print("Default scoring config created.")
else:
    print(f"Scoring configs already exist ({existing}). Skipping.")

session.close()
