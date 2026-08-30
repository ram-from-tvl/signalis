"""Loads the bundled sample dataset plus a default persona/solution.

Run with `python -m app.db.seed_demo` from the backend directory after the
database has been created (the API creates tables automatically on startup,
but this script also creates them if run standalone).
"""
from __future__ import annotations

import json
from pathlib import Path

from app.db.base import Base
from app.db.migrations import backfill_default_campaign, run_startup_migrations
from app.db.session import SessionLocal, engine
from app.models import Campaign, Persona, Solution
from app.services.ingestion import ingest_crm_csv, ingest_website_events_json

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    run_startup_migrations(engine)
    db = SessionLocal()
    try:
        seeded_persona = db.query(Persona).count() == 0
        seeded_solution = db.query(Solution).count() == 0
        if seeded_persona:
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
        if seeded_solution:
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

        default_campaign = db.query(Campaign).filter(Campaign.is_default.is_(True)).first()
        if default_campaign is None and (seeded_persona or seeded_solution):
            persona = db.query(Persona).order_by(Persona.created_at.desc()).first()
            solution = db.query(Solution).order_by(Solution.created_at.desc()).first()
            default_campaign = Campaign(
                name="Default", persona_id=persona.id, solution_id=solution.id, is_default=True
            )
            db.add(default_campaign)
            db.commit()
            db.refresh(default_campaign)

        crm_path = DATA_DIR / "sample_crm_leads.csv"
        events_path = DATA_DIR / "sample_website_events.json"

        crm_report = ingest_crm_csv(
            db, crm_path.read_bytes(), campaign_id=default_campaign.id if default_campaign else None
        )
        events = json.loads(events_path.read_text())
        events_report = ingest_website_events_json(db, events)

        # Backfill covers any lead that was already in the DB before this
        # run (e.g. re-running seed_demo against an existing DB, or a lead
        # that existed before campaigns did).
        backfill_default_campaign(db)

        print(f"CRM ingestion: {crm_report}")
        print(f"Website events ingestion: {events_report}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
