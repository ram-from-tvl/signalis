"""Unit and API tests for the Prioritization/Ranking Agent, with the shared
run_agent_reasoning_with_delegations boundary mocked (same convention as the
other agents' run_agent_reasoning mocking, extended with the (output,
delegations) tuple this agent's two-phase flow returns)."""
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
    with patch("app.agents.prioritization.run_agent_reasoning_with_delegations", return_value=(mocked_response, [], False)):
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
    with patch("app.agents.prioritization.run_agent_reasoning_with_delegations", return_value=(mocked_response, [], False)):
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
    with patch("app.agents.prioritization.run_agent_reasoning_with_delegations", return_value=(mocked_response, [], False)):
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
    with patch("app.agents.prioritization.run_agent_reasoning_with_delegations", return_value=(mocked_response, [], False)):
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


def test_ranking_api_response_surfaces_subagent_delegation_evidence(client, db_session):
    """Regression test for the Qodo finding: subagent_delegation evidence is
    persisted on a pipeline-wide AgentRun (lead_id=None) that the lead-scoped
    Agent Trace UI/API can never reach. The fix surfaces it directly on the
    ranking API response instead, since that's what the frontend actually
    calls to render a ranking (DashboardPage's Priority Queue card)."""
    lead_a, class_a = _lead_and_classification(db_session, name="Erin", stage="late", confidence=0.9)
    lead_b, class_b = _lead_and_classification(db_session, name="Frank", stage="mid", confidence=0.6)

    mocked_response = {
        "ranking": [
            {"lead_id": lead_a.id, "rank": 1, "reasoning": "late stage"},
            {"lead_id": lead_b.id, "rank": 2, "reasoning": "mid stage"},
        ],
        "summary": "Erin first.",
    }
    mocked_delegations = [
        {"thread_id": "t1", "name": "subagent_erin", "status": "done", "latency_seconds": 1.1},
        {"thread_id": "t2", "name": "subagent_frank", "status": "done", "latency_seconds": 0.9},
    ]
    with patch(
        "app.agents.prioritization.run_agent_reasoning_with_delegations",
        return_value=(mocked_response, mocked_delegations, True),
    ):
        run_resp = client.post("/api/ranking/run")
    assert run_resp.status_code == 200
    body = run_resp.json()

    assert body["subagent_delegation"] is not None
    assert body["subagent_delegation"]["status"] == "delegated"
    assert body["subagent_delegation"]["used"] is True
    assert body["subagent_delegation"]["subagent_count"] == 2
    assert body["subagent_delegation"]["expected_count"] == 2
    assert len(body["subagent_delegation"]["subagents"]) == 2

    latest_resp = client.get("/api/ranking/latest")
    assert latest_resp.json()["subagent_delegation"]["status"] == "delegated"


def test_ranking_snapshot_is_unaffected_by_later_reclassification(client, db_session):
    """Regression test for the Qodo finding: a ranking snapshot must show the
    stage/confidence that was true when it was generated, not whatever the
    lead's classification has since become."""
    lead_a, class_a = _lead_and_classification(db_session, name="Dana", stage="mid", confidence=0.5)

    mocked_response = {
        "ranking": [{"lead_id": lead_a.id, "rank": 1, "reasoning": "mid stage at ranking time"}],
        "summary": "test",
    }
    with patch("app.agents.prioritization.run_agent_reasoning_with_delegations", return_value=(mocked_response, [], False)):
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


def test_prioritization_records_subagent_delegation_evidence(db_session):
    """When TrueForge genuinely delegated per-lead assessment to subagents,
    that evidence (count + per-subagent latency, from TrueForge's own session
    events) must be persisted on the run's output, not just the model's final
    ranking, so the Agent Trace can show real subagent delegation happened."""
    lead_a, class_a = _lead_and_classification(db_session, name="Alice", stage="late", confidence=0.9)
    lead_b, class_b = _lead_and_classification(db_session, name="Bob", stage="mid", confidence=0.6)

    mocked_response = {
        "ranking": [
            {"lead_id": lead_a.id, "rank": 1, "reasoning": "late stage, high confidence"},
            {"lead_id": lead_b.id, "rank": 2, "reasoning": "mid stage"},
        ],
        "summary": "Alice first.",
    }
    mocked_delegations = [
        {"thread_id": "t1", "name": "subagent_alice", "input": "...", "status": "done", "latency_seconds": 1.2},
        {"thread_id": "t2", "name": "subagent_bob", "input": "...", "status": "done", "latency_seconds": 1.4},
    ]
    with patch(
        "app.agents.prioritization.run_agent_reasoning_with_delegations",
        return_value=(mocked_response, mocked_delegations, True),
    ):
        result = run_prioritization(db_session, [(lead_a, class_a), (lead_b, class_b)])

    assert result["subagent_delegation"]["status"] == "delegated"
    assert result["subagent_delegation"]["used"] is True
    assert result["subagent_delegation"]["subagent_count"] == 2
    assert result["subagent_delegation"]["expected_count"] == 2
    assert result["subagent_delegation"]["subagents"] == mocked_delegations


def test_prioritization_marks_delegation_not_delegated_on_fallback(db_session):
    """If the call never reached TrueForge at all (e.g. TrueForge unreachable
    and the call fell back to a direct LLM call with no subagent capability),
    the output must honestly report "not_delegated" rather than implying a
    genuine two-phase delegated run happened."""
    lead_a, class_a = _lead_and_classification(db_session, name="Alice", stage="late", confidence=0.9)

    mocked_response = {
        "ranking": [{"lead_id": lead_a.id, "rank": 1, "reasoning": "only lead"}],
        "summary": "test",
    }
    with patch(
        "app.agents.prioritization.run_agent_reasoning_with_delegations",
        return_value=(mocked_response, [], False),
    ):
        result = run_prioritization(db_session, [(lead_a, class_a)])

    assert result["subagent_delegation"]["status"] == "not_delegated"
    assert result["subagent_delegation"]["used"] is False
    assert result["subagent_delegation"]["subagent_count"] == 0
    assert result["subagent_delegation"]["subagents"] == []


def test_prioritization_marks_delegation_evidence_unavailable_without_rerunning(db_session):
    """Regression test for the Qodo finding: when a genuine TrueForge turn
    succeeds but fetching delegation evidence (session events) fails,
    run_agent_reasoning_with_delegations returns delegations=None as the
    'evidence unavailable' sentinel. run_prioritization must persist the
    successful ranking with delegation explicitly marked unavailable/error —
    not treat None as zero delegations, and not rerun via the fallback."""
    lead_a, class_a = _lead_and_classification(db_session, name="Alice", stage="late", confidence=0.9)

    mocked_response = {
        "ranking": [{"lead_id": lead_a.id, "rank": 1, "reasoning": "only lead"}],
        "summary": "test",
    }
    with patch(
        "app.agents.prioritization.run_agent_reasoning_with_delegations",
        return_value=(mocked_response, None, True),
    ):
        result = run_prioritization(db_session, [(lead_a, class_a)])

    assert result["subagent_delegation"]["status"] == "evidence_unavailable"
    assert result["subagent_delegation"]["used"] is False
    assert result["subagent_delegation"]["subagent_count"] is None
    assert result["subagent_delegation"]["subagents"] == []
    # The valid ranking itself must still be preserved, not discarded.
    assert result["ranking"][0]["lead_id"] == lead_a.id


def test_prioritization_marks_delegation_partial_when_count_below_lead_count(db_session):
    """Regression test for the Qodo finding: the system prompt mandates
    exactly one subagent delegation per lead. A response with fewer
    delegations than leads (e.g. a partial outage or a model that skipped
    some leads) must be visibly marked partial, not silently accepted as
    equivalent to a fully delegated run."""
    lead_a, class_a = _lead_and_classification(db_session, name="Alice", stage="late", confidence=0.9)
    lead_b, class_b = _lead_and_classification(db_session, name="Bob", stage="mid", confidence=0.6)

    mocked_response = {
        "ranking": [
            {"lead_id": lead_a.id, "rank": 1, "reasoning": "late stage"},
            {"lead_id": lead_b.id, "rank": 2, "reasoning": "mid stage"},
        ],
        "summary": "test",
    }
    mocked_delegations = [
        {"thread_id": "t1", "name": "subagent_alice", "status": "done", "latency_seconds": 1.0},
    ]
    with patch(
        "app.agents.prioritization.run_agent_reasoning_with_delegations",
        return_value=(mocked_response, mocked_delegations, True),
    ):
        result = run_prioritization(db_session, [(lead_a, class_a), (lead_b, class_b)])

    assert result["subagent_delegation"]["status"] == "partial"
    assert result["subagent_delegation"]["used"] is False
    assert result["subagent_delegation"]["subagent_count"] == 1
    assert result["subagent_delegation"]["expected_count"] == 2


def test_prioritization_marks_delegation_partial_when_zero_delegations_on_genuine_turn(db_session):
    """A genuine TrueForge turn that completed with zero delegations (model
    ignored the delegation mandate, or an outage during phase 1) must not be
    silently accepted as a normal completed ranking indistinguishable from a
    properly delegated one."""
    lead_a, class_a = _lead_and_classification(db_session, name="Alice", stage="late", confidence=0.9)

    mocked_response = {
        "ranking": [{"lead_id": lead_a.id, "rank": 1, "reasoning": "only lead"}],
        "summary": "test",
    }
    with patch(
        "app.agents.prioritization.run_agent_reasoning_with_delegations",
        return_value=(mocked_response, [], True),
    ):
        result = run_prioritization(db_session, [(lead_a, class_a)])

    assert result["subagent_delegation"]["status"] == "partial"
    assert result["subagent_delegation"]["used"] is False
    assert result["subagent_delegation"]["subagent_count"] == 0
    assert result["subagent_delegation"]["expected_count"] == 1


def test_run_prioritization_uses_fallback_instruction_for_delegation_mandate(db_session):
    """Regression test for the Qodo finding: run_prioritization must pass a
    fallback_instruction (not the tool-mandating SYSTEM_INSTRUCTION) so the
    direct Gemini/HF fallback path — which has no create_sub_agent tool — is
    never told to call a tool that doesn't exist on that path."""
    from app.agents.prioritization import FALLBACK_SYSTEM_INSTRUCTION, SYSTEM_INSTRUCTION

    lead_a, class_a = _lead_and_classification(db_session, name="Alice", stage="late", confidence=0.9)
    mocked_response = {
        "ranking": [{"lead_id": lead_a.id, "rank": 1, "reasoning": "only lead"}],
        "summary": "test",
    }
    with patch(
        "app.agents.prioritization.run_agent_reasoning_with_delegations",
        return_value=(mocked_response, [], False),
    ) as mock_run:
        run_prioritization(db_session, [(lead_a, class_a)])

    assert mock_run.call_args.kwargs["fallback_instruction"] == FALLBACK_SYSTEM_INSTRUCTION
    assert "create_sub_agent" not in FALLBACK_SYSTEM_INSTRUCTION
    assert mock_run.call_args.kwargs["system_instruction"] == SYSTEM_INSTRUCTION


def test_run_prioritization_marks_run_failed_on_truforge_error(db_session):
    """Regression test for the Qodo finding: a TrueForgeError raised from the
    delegation-evidence path (e.g. malformed session events response) must
    mark the AgentRun as failed, not leave it stuck in 'running' forever."""
    from app.core.trueforge import TrueForgeError
    from app.models import AgentRun

    lead_a, class_a = _lead_and_classification(db_session, name="Alice", stage="late", confidence=0.9)

    with patch(
        "app.agents.prioritization.run_agent_reasoning_with_delegations",
        side_effect=TrueForgeError("malformed session events payload"),
    ), pytest.raises(TrueForgeError):
        run_prioritization(db_session, [(lead_a, class_a)])

    run = (
        db_session.query(AgentRun)
        .filter(AgentRun.agent_name == "prioritization")
        .order_by(AgentRun.started_at.desc())
        .first()
    )
    assert run is not None
    assert run.status == "failed"
