"""Unit and API tests for the Prioritization/Ranking Agent, with the shared
run_agent_reasoning boundary mocked (same convention as the other agents)."""
from __future__ import annotations

from unittest.mock import patch

from app.agents.prioritization import run_prioritization
from app.models.entities import Lead, StageClassification


def _lead_and_classification(db_session, *, name: str, stage: str, confidence: float) -> tuple[Lead, StageClassification]:
    lead = Lead(name=name, company=f"{name} Co", title="VP", company_size="51-200", industry="SaaS", geography="US")
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)

    classification = StageClassification(
        lead_id=lead.id,
        stage=stage,
        confidence=confidence,
        justification=f"{stage} stage evidence",
    )
    db_session.add(classification)
    db_session.commit()
    db_session.refresh(classification)
    return lead, classification


def test_prioritization_returns_empty_ranking_with_no_leads(db_session):
    result = run_prioritization(db_session, [])
    assert result["ranking"] == []
    assert "No classified leads" in result["summary"]


def test_prioritization_orders_and_filters_llm_output(db_session):
    lead_a, class_a = _lead_and_classification(db_session, name="Alice", stage="late", confidence=0.9)
    lead_b, class_b = _lead_and_classification(db_session, name="Bob", stage="early", confidence=0.3)

    mocked_response = {
        "ranking": [
            {"lead_id": lead_b.id, "rank": 2, "reasoning": "early stage, low urgency"},
            {"lead_id": lead_a.id, "rank": 1, "reasoning": "late stage, strong signals"},
            {"lead_id": "nonexistent-lead-id", "rank": 3, "reasoning": "should be filtered out"},
        ],
        "summary": "Alice first due to late-stage signals.",
    }
    with patch("app.agents.prioritization.run_agent_reasoning", return_value=mocked_response):
        result = run_prioritization(db_session, [(lead_a, class_a), (lead_b, class_b)])

    assert len(result["ranking"]) == 2
    assert result["ranking"][0]["lead_id"] == lead_a.id
    assert result["ranking"][0]["rank"] == 1
    assert result["ranking"][1]["lead_id"] == lead_b.id
    assert "agent_run_id" in result


def test_ranking_api_run_and_latest_endpoints(client, db_session):
    lead_a, class_a = _lead_and_classification(db_session, name="Carol", stage="late", confidence=0.85)

    mocked_response = {
        "ranking": [{"lead_id": lead_a.id, "rank": 1, "reasoning": "only lead, late stage"}],
        "summary": "Only one classified lead; ranked first by default.",
    }
    with patch("app.agents.prioritization.run_agent_reasoning", return_value=mocked_response):
        run_resp = client.post("/api/ranking/run")
    assert run_resp.status_code == 200
    body = run_resp.json()
    assert body["ranked_leads"][0]["lead_id"] == lead_a.id
    assert body["ranked_leads"][0]["lead"]["name"] == "Carol"
    assert body["ranked_leads"][0]["stage"] == "late"

    latest_resp = client.get("/api/ranking/latest")
    assert latest_resp.status_code == 200
    assert latest_resp.json()["id"] == body["id"]


def test_ranking_latest_returns_null_when_never_run(client):
    resp = client.get("/api/ranking/latest")
    assert resp.status_code == 200
    assert resp.json() is None
