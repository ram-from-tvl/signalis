"""Tests for the follow-up Q&A endpoint that continues an existing
TrueForge session as a real conversation turn, plus the trueforge.py
functions and run_agent_reasoning return-shape it depends on."""
from __future__ import annotations

from unittest.mock import patch

from app.core.trueforge import TrueForgeError, run_followup_turn
from app.models import AgentRun


def _make_agent_run(db_session, lead, *, trueforge_session_id: str | None) -> AgentRun:
    run = AgentRun(
        lead_id=lead.id,
        agent_name="buying_stage_orchestrator",
        input_summary="test run",
        output={"stage": "mid", "confidence": 0.6, "justification": "some signals"},
        reasoning="Classified as mid stage because of moderate signal strength.",
        status="completed",
        trueforge_session_id=trueforge_session_id,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def test_ask_followup_returns_answer_and_persists_it(client, db_session, sample_lead):
    run = _make_agent_run(db_session, sample_lead, trueforge_session_id="session-123")

    with patch(
        "app.api.routes.agent_followups.run_followup_turn",
        return_value={"answer": "The lead lacked late-stage signals like a demo request."},
    ) as mock_followup:
        response = client.post(
            f"/api/leads/{sample_lead.id}/agent-runs/{run.id}/ask",
            json={"question": "Why wasn't this lead classified as late-stage?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "The lead lacked late-stage signals like a demo request."
    assert body["question"] == "Why wasn't this lead classified as late-stage?"
    assert body["agent_run_id"] == run.id
    mock_followup.assert_called_once()
    called_session_id = mock_followup.call_args[0][0]
    assert called_session_id == "session-123"

    # Persisted and retrievable via the list endpoint.
    list_response = client.get(f"/api/leads/{sample_lead.id}/agent-runs/{run.id}/followups")
    assert list_response.status_code == 200
    followups = list_response.json()
    assert len(followups) == 1
    assert followups[0]["question"] == "Why wasn't this lead classified as late-stage?"


def test_ask_followup_returns_409_when_no_trueforge_session(client, db_session, sample_lead):
    run = _make_agent_run(db_session, sample_lead, trueforge_session_id=None)

    response = client.post(
        f"/api/leads/{sample_lead.id}/agent-runs/{run.id}/ask",
        json={"question": "Why this stage?"},
    )
    assert response.status_code == 409


def test_ask_followup_404_for_run_not_belonging_to_lead(client, db_session, sample_lead):
    other_run = AgentRun(
        lead_id=None,
        agent_name="prioritization",
        input_summary="pipeline-wide run",
        status="completed",
        trueforge_session_id="session-999",
    )
    db_session.add(other_run)
    db_session.commit()
    db_session.refresh(other_run)

    response = client.post(
        f"/api/leads/{sample_lead.id}/agent-runs/{other_run.id}/ask",
        json={"question": "irrelevant"},
    )
    assert response.status_code == 404


def test_ask_followup_rejects_blank_question(client, db_session, sample_lead):
    run = _make_agent_run(db_session, sample_lead, trueforge_session_id="session-abc")
    response = client.post(
        f"/api/leads/{sample_lead.id}/agent-runs/{run.id}/ask",
        json={"question": "   "},
    )
    assert response.status_code == 422


def test_ask_followup_surfaces_trueforge_error_as_502(client, db_session, sample_lead):
    run = _make_agent_run(db_session, sample_lead, trueforge_session_id="session-abc")
    with patch(
        "app.api.routes.agent_followups.run_followup_turn",
        side_effect=TrueForgeError("session no longer exists"),
    ):
        response = client.post(
            f"/api/leads/{sample_lead.id}/agent-runs/{run.id}/ask",
            json={"question": "Why?"},
        )
    assert response.status_code == 502


def test_agent_run_out_reports_can_ask_followup(client, db_session, sample_lead):
    _make_agent_run(db_session, sample_lead, trueforge_session_id="session-123")
    _make_agent_run(db_session, sample_lead, trueforge_session_id=None)

    response = client.get(f"/api/leads/{sample_lead.id}/trace")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    can_ask_flags = {run["can_ask_followup"] for run in body}
    assert can_ask_flags == {True, False}


def test_run_followup_turn_uses_existing_session_without_creating_a_new_one():
    """This is the core behavior this feature depends on: run_followup_turn
    must post directly to the existing session's turns endpoint and never
    call POST /api/v1/sessions (which would create a fresh, memory-less
    session instead of continuing the original one)."""
    with patch("app.core.trueforge.httpx.post") as mock_post, patch(
        "app.core.trueforge.httpx.get"
    ) as mock_get:
        turn_response = mock_post.return_value
        turn_response.raise_for_status = lambda: None
        turn_response.json.return_value = {"data": {"id": "turn-1"}}

        poll_response = mock_get.return_value
        poll_response.raise_for_status = lambda: None
        poll_response.json.return_value = {
            "data": {"state": {"status": "done", "output": {"content": '{"answer": "because X"}'}}}
        }

        result = run_followup_turn("existing-session-id", "follow-up question")

    assert result == {"answer": "because X"}
    # Exactly one POST: starting the turn. No session-creation POST.
    assert mock_post.call_count == 1
    called_url = mock_post.call_args[0][0]
    assert called_url.endswith("/api/v1/sessions/existing-session-id/turns")
