"""Unit and API tests for the Prioritization/Ranking Agent, with the shared
run_agent_reasoning boundary mocked (same convention as the other agents)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.agents.prioritization import run_prioritization
from app.models import Lead, StageClassification


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
    with patch("app.agents.prioritization.run_agent_reasoning", return_value=(mocked_response, "session-xyz")):
        result = run_prioritization(db_session, [(lead_a, class_a), (lead_b, class_b)])

    assert len(result["ranking"]) == 2
    assert result["ranking"][0]["lead_id"] == lead_a.id
    assert result["ranking"][0]["rank"] == 1
    assert result["ranking"][0]["name"] == "Alice"
    assert result["ranking"][0]["stage"] == "late"
    assert result["ranking"][0]["confidence"] == 0.9
    assert result["ranking"][1]["lead_id"] == lead_b.id
    assert result["ranking"][1]["rank"] == 2
    assert "agent_run_id" in result


def test_prioritization_normalizes_duplicate_and_out_of_range_ranks(db_session):
    """Regression test for the Qodo/Copilot finding: malformed LLM output
    (duplicate ranks, ranks that don't form a contiguous 1..N sequence) must
    not be persisted as-is — it must be normalized into a clean 1..N order."""
    lead_a, class_a = _lead_and_classification(db_session, name="Alice", stage="late", confidence=0.9)
    lead_b, class_b = _lead_and_classification(db_session, name="Bob", stage="mid", confidence=0.6)

    mocked_response = {
        "ranking": [
            {"lead_id": lead_a.id, "rank": 1, "reasoning": "first"},
            {"lead_id": lead_b.id, "rank": 1, "reasoning": "duplicate rank with Alice"},
        ],
        "summary": "test",
    }
    with patch("app.agents.prioritization.run_agent_reasoning", return_value=(mocked_response, "session-xyz")):
        result = run_prioritization(db_session, [(lead_a, class_a), (lead_b, class_b)])

    ranks = [entry["rank"] for entry in result["ranking"]]
    assert ranks == [1, 2]
    assert {entry["lead_id"] for entry in result["ranking"]} == {lead_a.id, lead_b.id}


def test_prioritization_appends_leads_missing_from_llm_output(db_session):
    """Regression test: if the model omits a lead entirely, every classified
    lead must still appear exactly once in the final, persisted ranking."""
    lead_a, class_a = _lead_and_classification(db_session, name="Alice", stage="late", confidence=0.9)
    lead_b, class_b = _lead_and_classification(db_session, name="Bob", stage="mid", confidence=0.6)

    mocked_response = {
        "ranking": [{"lead_id": lead_a.id, "rank": 1, "reasoning": "only lead the model ranked"}],
        "summary": "test",
    }
    with patch("app.agents.prioritization.run_agent_reasoning", return_value=(mocked_response, "session-xyz")):
        result = run_prioritization(db_session, [(lead_a, class_a), (lead_b, class_b)])

    lead_ids = [entry["lead_id"] for entry in result["ranking"]]
    assert set(lead_ids) == {lead_a.id, lead_b.id}
    ranks = [entry["rank"] for entry in result["ranking"]]
    assert ranks == [1, 2]


def test_ranking_api_run_and_latest_endpoints(client, db_session):
    lead_a, class_a = _lead_and_classification(db_session, name="Carol", stage="late", confidence=0.85)

    mocked_response = {
        "ranking": [{"lead_id": lead_a.id, "rank": 1, "reasoning": "only lead, late stage"}],
        "summary": "Only one classified lead; ranked first by default.",
    }
    with patch("app.agents.prioritization.run_agent_reasoning", return_value=(mocked_response, "session-xyz")):
        run_resp = client.post("/api/ranking/run")
    assert run_resp.status_code == 200
    body = run_resp.json()
    assert body["ranked_leads"][0]["lead_id"] == lead_a.id
    assert body["ranked_leads"][0]["name"] == "Carol"
    assert body["ranked_leads"][0]["stage"] == "late"
    assert body["ranked_leads"][0]["confidence"] == 0.85

    latest_resp = client.get("/api/ranking/latest")
    assert latest_resp.status_code == 200
    assert latest_resp.json()["id"] == body["id"]


def test_ranking_snapshot_is_unaffected_by_later_reclassification(client, db_session):
    """Regression test for the Qodo finding: a ranking snapshot must show the
    stage/confidence that was true when it was generated, not whatever the
    lead's classification has since become."""
    lead_a, class_a = _lead_and_classification(db_session, name="Dana", stage="mid", confidence=0.5)

    mocked_response = {
        "ranking": [{"lead_id": lead_a.id, "rank": 1, "reasoning": "mid stage at ranking time"}],
        "summary": "test",
    }
    with patch("app.agents.prioritization.run_agent_reasoning", return_value=(mocked_response, "session-xyz")):
        run_resp = client.post("/api/ranking/run")
    ranking_id = run_resp.json()["id"]

    # Lead gets reclassified after the ranking snapshot was taken.
    class_a.superseded_by_id = "some-newer-classification"
    new_classification = StageClassification(
        lead_id=lead_a.id, stage="late", confidence=0.95, justification="new evidence"
    )
    db_session.add(new_classification)
    db_session.commit()

    latest_resp = client.get("/api/ranking/latest")
    body = latest_resp.json()
    assert body["id"] == ranking_id
    assert body["ranked_leads"][0]["stage"] == "mid"
    assert body["ranked_leads"][0]["confidence"] == 0.5


def test_prioritization_rejects_pipelines_over_the_size_limit(db_session):
    from app.agents.prioritization import MAX_LEADS_PER_RANKING_CALL, PrioritizationError

    pairs = [
        _lead_and_classification(db_session, name=f"Lead {i}", stage="early", confidence=0.5)
        for i in range(MAX_LEADS_PER_RANKING_CALL + 1)
    ]
    with pytest.raises(PrioritizationError):
        run_prioritization(db_session, pairs)


def test_ranking_latest_returns_null_when_never_run(client):
    resp = client.get("/api/ranking/latest")
    assert resp.status_code == 200
    assert resp.json() is None
