"""Loads the bundled sample dataset plus a default persona/solution.

Run with `python -m app.db.seed_demo` from the backend directory after the
database has been created (the API creates tables automatically on startup,
but this script also creates them if run standalone).
"""
from __future__ import annotations

import json
from pathlib import Path

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.entities import Persona, Solution
from app.services.ingestion import ingest_crm_csv, ingest_website_events_json

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(Persona).count() == 0:
            db.add(
                Persona(
                    role="VP of Sales / Sales Operations",
                    seniority="VP / Director",
                    industry="B2B Logistics, Manufacturing, Financial Services",
                    company_size_band="51-1000",
                    geography="Global, English-speaking preferred",
                    custom_traits={
                        "buying_committee_size": "3-6 stakeholders",
                        "priorities": ["operational efficiency", "predictable revenue"],
                    },
                )
            )
        if db.query(Solution).count() == 0:
            db.add(
                Solution(
                    name="Signalis Copilot",
                    problem_solved=(
                        "Sales and marketing teams cannot see buying intent scattered across "
                        "CRM, website, and email systems in time to act on it."
                    ),
                    value_props=[
                        "Unifies CRM and website signals into one buying-stage view",
                        "Cuts manual lead research and outreach drafting time by roughly 10x",
                        "Surfaces explainable reasoning, not a black-box score",
                    ],
                    icp_filters={
                        "company_size_band": "51-1000",
                        "industries": ["Logistics", "Manufacturing", "Financial Services", "SaaS"],
                    },
                    differentiators=[
                        "Full agent reasoning trail for every classification",
                        "Human-approval checkpoint before any plan goes out",
                        "Plans regenerate automatically as new signals arrive",
                    ],
                    channels=["email", "linkedin", "phone", "events"],
                )
            )
        db.commit()

        crm_path = DATA_DIR / "sample_crm_leads.csv"
        events_path = DATA_DIR / "sample_website_events.json"

        crm_report = ingest_crm_csv(db, crm_path.read_bytes())
        events = json.loads(events_path.read_text())
        events_report = ingest_website_events_json(db, events)

        print(f"CRM ingestion: {crm_report}")
        print(f"Website events ingestion: {events_report}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
