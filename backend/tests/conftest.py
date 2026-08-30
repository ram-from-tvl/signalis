from __future__ import annotations

import datetime
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.base import Base
from app.main import app
from app.models import Campaign, Lead, Persona, Signal, Solution

TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture()
def db_session() -> Generator:
    # StaticPool keeps a single shared connection alive for the whole test
    # so the in-memory database survives across the multiple connections
    # FastAPI's dependency injection and the app's startup event each open.
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session) -> Generator[TestClient, None, None]:
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def default_campaign(db_session) -> Campaign:
    """Every lead needs a resolvable campaign for the pipeline to run
    (see app.services.pipeline._resolve_campaign) — this mirrors the real
    app's default-campaign fallback rather than every test needing to wire
    up its own persona/solution/campaign trio by hand."""
    persona = Persona(
        role="VP of Sales",
        seniority="VP",
        industry="SaaS",
        company_size_band="51-200",
        geography="US",
    )
    solution = Solution(
        name="Test Solution",
        problem_solved="Test problem",
        value_props=["Test value prop"],
        differentiators=["Test differentiator"],
        channels=["email"],
    )
    db_session.add_all([persona, solution])
    db_session.commit()
    db_session.refresh(persona)
    db_session.refresh(solution)

    campaign = Campaign(
        name="Default", persona_id=persona.id, solution_id=solution.id, is_default=True
    )
    db_session.add(campaign)
    db_session.commit()
    db_session.refresh(campaign)
    return campaign


@pytest.fixture()
def sample_lead(db_session, default_campaign) -> Lead:
    lead = Lead(
        name="Test Lead",
        company="Test Co",
        title="VP Sales",
        company_size="51-200",
        industry="SaaS",
        geography="US",
        email="test.lead@testco.com",
        campaign_id=default_campaign.id,
    )
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)

    signal = Signal(
        lead_id=lead.id,
        raw_source="website",
        raw_payload={"page": "/pricing", "event_type": "pricing_page_visit"},
        event_type="unknown",
        intent_stage_hint="early",
        occurred_at=datetime.datetime.utcnow(),
    )
    db_session.add(signal)
    db_session.commit()
    db_session.refresh(lead)
    return lead
