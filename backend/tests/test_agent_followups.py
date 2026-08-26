"""Tests for the follow-up Q&A endpoint that continues an existing
TrueForge session as a real conversation turn, plus the trueforge.py
functions and run_agent_reasoning return-shape it depends on."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.core.trueforge import TrueForgeError, run_followup_turn, run_turn
from app.models import AgentRun, AgentRunFollowup


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


def _count_followups(db_session, agent_run_id: str) -> int:
    return len(
        db_session.execute(
            select(AgentRunFollowup).where(AgentRunFollowup.agent_run_id == agent_run_id)
        ).scalars().all()
    )


def test_ask_followup_missing_answer_key_returns_502_and_no_db_row(client, db_session, sample_lead):
    run = _make_agent_run(db_session, sample_lead, trueforge_session_id="session-abc")
    with patch(
        "app.api.routes.agent_followups.run_followup_turn",
        return_value={"not_answer": "oops"},
    ):
        response = client.post(
            f"/api/leads/{sample_lead.id}/agent-runs/{run.id}/ask",
            json={"question": "Why?"},
        )
    assert response.status_code == 502
    assert _count_followups(db_session, run.id) == 0


def test_ask_followup_blank_answer_returns_502_and_no_db_row(client, db_session, sample_lead):
    run = _make_agent_run(db_session, sample_lead, trueforge_session_id="session-abc")
    with patch(
        "app.api.routes.agent_followups.run_followup_turn",
        return_value={"answer": "   "},
    ):
        response = client.post(
            f"/api/leads/{sample_lead.id}/agent-runs/{run.id}/ask",
            json={"question": "Why?"},
        )
    assert response.status_code == 502
    assert _count_followups(db_session, run.id) == 0


def test_ask_followup_non_string_answer_returns_502_and_no_db_row(client, db_session, sample_lead):
    run = _make_agent_run(db_session, sample_lead, trueforge_session_id="session-abc")
    with patch(
        "app.api.routes.agent_followups.run_followup_turn",
        return_value={"answer": ["not", "a", "string"]},
    ):
        response = client.post(
            f"/api/leads/{sample_lead.id}/agent-runs/{run.id}/ask",
            json={"question": "Why?"},
        )
    assert response.status_code == 502
    assert _count_followups(db_session, run.id) == 0


def test_ask_followup_non_dict_result_returns_502_and_no_db_row(client, db_session, sample_lead):
    run = _make_agent_run(db_session, sample_lead, trueforge_session_id="session-abc")
    with patch(
        "app.api.routes.agent_followups.run_followup_turn",
        return_value=["totally", "wrong", "shape"],
    ):
        response = client.post(
            f"/api/leads/{sample_lead.id}/agent-runs/{run.id}/ask",
            json={"question": "Why?"},
        )
    assert response.status_code == 502
    assert _count_followups(db_session, run.id) == 0


def test_ask_followup_rejects_question_over_max_length(client, db_session, sample_lead):
    run = _make_agent_run(db_session, sample_lead, trueforge_session_id="session-abc")
    response = client.post(
        f"/api/leads/{sample_lead.id}/agent-runs/{run.id}/ask",
        json={"question": "x" * 2001},
    )
    assert response.status_code == 422
    assert _count_followups(db_session, run.id) == 0


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


def test_run_followup_turn_wraps_malformed_json_from_start_turn_as_trueforge_error():
    """A 200-status response whose body isn't valid JSON must surface as
    TrueForgeError (-> the route's 502), not an uncaught JSONDecodeError
    (-> a raw 500)."""
    with patch("app.core.trueforge.httpx.post") as mock_post:
        turn_response = mock_post.return_value
        turn_response.raise_for_status = lambda: None
        turn_response.json.side_effect = json.JSONDecodeError("bad json", "doc", 0)

        with pytest.raises(TrueForgeError):
            run_followup_turn("existing-session-id", "question")


def test_run_followup_turn_wraps_malformed_json_from_poll_as_trueforge_error():
    with patch("app.core.trueforge.httpx.post") as mock_post, patch(
        "app.core.trueforge.httpx.get"
    ) as mock_get:
        turn_response = mock_post.return_value
        turn_response.raise_for_status = lambda: None
        turn_response.json.return_value = {"data": {"id": "turn-1"}}

        poll_response = mock_get.return_value
        poll_response.raise_for_status = lambda: None
        poll_response.json.side_effect = json.JSONDecodeError("bad json", "doc", 0)

        with pytest.raises(TrueForgeError):
            run_followup_turn("existing-session-id", "question")


def test_run_followup_turn_wraps_non_dict_turn_state_as_trueforge_error():
    """If the poll response's `state` isn't an object (e.g. TrueForge
    returned a malformed-but-200 body), accessing .get on it must not raise
    a raw AttributeError."""
    with patch("app.core.trueforge.httpx.post") as mock_post, patch(
        "app.core.trueforge.httpx.get"
    ) as mock_get:
        turn_response = mock_post.return_value
        turn_response.raise_for_status = lambda: None
        turn_response.json.return_value = {"data": {"id": "turn-1"}}

        poll_response = mock_get.return_value
        poll_response.raise_for_status = lambda: None
        poll_response.json.return_value = {"data": {"state": "not-an-object"}}

        with pytest.raises(TrueForgeError):
            run_followup_turn("existing-session-id", "question")


def test_run_followup_turn_wraps_non_dict_output_as_trueforge_error():
    with patch("app.core.trueforge.httpx.post") as mock_post, patch(
        "app.core.trueforge.httpx.get"
    ) as mock_get:
        turn_response = mock_post.return_value
        turn_response.raise_for_status = lambda: None
        turn_response.json.return_value = {"data": {"id": "turn-1"}}

        poll_response = mock_get.return_value
        poll_response.raise_for_status = lambda: None
        poll_response.json.return_value = {
            "data": {"state": {"status": "done", "output": "not-an-object"}}
        }

        with pytest.raises(TrueForgeError):
            run_followup_turn("existing-session-id", "question")


def test_run_turn_wraps_malformed_json_from_session_creation_as_trueforge_error():
    """Same class of bug, pre-existing code path (run_turn's session
    creation, not just the newer run_followup_turn)."""
    with patch("app.core.trueforge.httpx.post") as mock_post:
        session_response = mock_post.return_value
        session_response.raise_for_status = lambda: None
        session_response.json.side_effect = json.JSONDecodeError("bad json", "doc", 0)

        with pytest.raises(TrueForgeError):
            run_turn("some-agent", "prompt")
