"""Tests for the TrueForge per-tool approval endpoints (approve/reject a
pending ToolApprovalRequest) and the pipeline-level pause/persist path that
creates them, with the TrueForge HTTP boundary mocked exactly like the other
trueforge tests in test_llm_fallback_and_sandbox.py."""
from __future__ import annotations

from unittest.mock import patch

from app.agents.common import AgentPausedForToolApproval
from app.core.trueforge import PendingToolApproval
from app.models import AgentRun, ToolApprovalRequest
from app.services.pipeline import PipelinePausedForApproval, run_pipeline_for_lead


def _make_pending_run(db_session, lead) -> AgentRun:
    run = AgentRun(
        lead_id=lead.id,
        agent_name="persona_fit",
        input_summary="test",
        status="running",
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def _make_pending_request(db_session, lead, agent_run) -> ToolApprovalRequest:
    request = ToolApprovalRequest(
        lead_id=lead.id,
        agent_run_id=agent_run.id,
        trueforge_agent_name="signalis-persona-fit",
        session_id="sess-1",
        turn_id="turn-1",
        thread_id="main",
        tool_call_id="call-1",
        tool_name="classify_company_industry",
        tool_input={"company_name": "Acme Corp"},
        status="pending",
    )
    db_session.add(request)
    db_session.commit()
    db_session.refresh(request)
    return request


def test_list_pending_tool_approvals_filters_by_lead(client, sample_lead, db_session):
    run = _make_pending_run(db_session, sample_lead)
    request = _make_pending_request(db_session, sample_lead, run)

    response = client.get("/api/tool-approvals", params={"lead_id": sample_lead.id})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == request.id
    assert body[0]["tool_name"] == "classify_company_industry"
    assert body[0]["status"] == "pending"


def test_list_pending_tool_approvals_excludes_resolved(client, sample_lead, db_session):
    run = _make_pending_run(db_session, sample_lead)
    request = _make_pending_request(db_session, sample_lead, run)
    request.status = "approved"
    db_session.add(request)
    db_session.commit()

    response = client.get("/api/tool-approvals", params={"lead_id": sample_lead.id})
    assert response.status_code == 200
    assert response.json() == []


def test_approve_tool_call_resumes_turn_and_completes_run(client, sample_lead, db_session):
    run = _make_pending_run(db_session, sample_lead)
    request = _make_pending_request(db_session, sample_lead, run)

    with patch(
        "app.api.routes.tool_approvals.resume_persona_fit",
        return_value={"fit": "full_fit", "reasoning": "matches", "missing_data": []},
    ) as mock_resume:
        response = client.post(f"/api/tool-approvals/{request.id}/approve")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["resolved_at"] is not None
    mock_resume.assert_called_once()
    _, kwargs = mock_resume.call_args
    assert kwargs["session_id"] == "sess-1"
    assert kwargs["thread_id"] == "main"
    assert kwargs["tool_call_id"] == "call-1"
    assert kwargs["approve"] is True

    db_session.refresh(request)
    assert request.status == "approved"


def test_reject_tool_call_resumes_with_deny_and_fails_run(client, sample_lead, db_session):
    run = _make_pending_run(db_session, sample_lead)
    request = _make_pending_request(db_session, sample_lead, run)

    with patch("app.api.routes.tool_approvals.resume_persona_fit") as mock_resume:
        def fake_resume(db, agent_run, **kwargs):
            agent_run.status = "failed"
            agent_run.reasoning = "Tool call denied"
            db.add(agent_run)
            return {"error": "denied"}

        mock_resume.side_effect = fake_resume
        response = client.post(f"/api/tool-approvals/{request.id}/reject", json={"reason": "not needed"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    mock_resume.assert_called_once()
    _, kwargs = mock_resume.call_args
    assert kwargs["approve"] is False
    assert kwargs["deny_reason"] == "not needed"

    db_session.refresh(run)
    assert run.status == "failed"


def test_approve_already_resolved_request_returns_409(client, sample_lead, db_session):
    run = _make_pending_run(db_session, sample_lead)
    request = _make_pending_request(db_session, sample_lead, run)
    request.status = "approved"
    db_session.add(request)
    db_session.commit()

    response = client.post(f"/api/tool-approvals/{request.id}/approve")
    assert response.status_code == 409


def test_approve_unknown_request_returns_404(client):
    response = client.post("/api/tool-approvals/does-not-exist/approve")
    assert response.status_code == 404


def test_approve_followup_pause_creates_new_pending_request(client, sample_lead, db_session):
    """Regression test: if the resumed turn immediately hits a second
    approval gate, the endpoint must persist a new ToolApprovalRequest
    rather than silently losing the pause."""
    run = _make_pending_run(db_session, sample_lead)
    request = _make_pending_request(db_session, sample_lead, run)

    followup = [
        PendingToolApproval(
            session_id="sess-1",
            turn_id="turn-2",
            thread_id="main",
            tool_call_id="call-2",
            tool_name="estimate_company_size_band",
            tool_input={"company_name": "Acme Corp"},
        )
    ]

    with patch(
        "app.api.routes.tool_approvals.resume_persona_fit",
        side_effect=AgentPausedForToolApproval(followup),
    ):
        response = client.post(f"/api/tool-approvals/{request.id}/approve")

    assert response.status_code == 202

    db_session.refresh(request)
    assert request.status == "approved"

    from sqlalchemy import select

    new_requests = (
        db_session.execute(
            select(ToolApprovalRequest).where(ToolApprovalRequest.tool_call_id == "call-2")
        )
        .scalars()
        .all()
    )
    assert len(new_requests) == 1
    assert new_requests[0].status == "pending"
    assert new_requests[0].tool_name == "estimate_company_size_band"


def test_run_pipeline_for_lead_persists_pending_approval_and_raises(db_session, sample_lead):
    """The pipeline-level integration: a paused Persona Fit node must
    surface as PipelinePausedForApproval with a persisted ToolApprovalRequest,
    not an unhandled crash or a silently-swallowed pause."""
    # app.agents.graph caches a compiled graph in a module global; other test
    # modules (test_pipeline.py) may have left a mocked graph cached there,
    # so force a real rebuild for this test regardless of run order.
    import app.agents.graph as graph_module

    graph_module._compiled_graph = None

    pending = [
        PendingToolApproval(
            session_id="sess-9",
            turn_id="turn-9",
            thread_id="main",
            tool_call_id="call-9",
            tool_name="classify_company_industry",
            tool_input={"company_name": sample_lead.company},
        )
    ]

    def fake_run_agent_reasoning(**kwargs):
        if kwargs["trueforge_agent_name"] == "signalis-signal-extraction":
            return {"extracted_signals": []}
        if kwargs["trueforge_agent_name"] == "signalis-persona-fit":
            raise AgentPausedForToolApproval(pending)
        raise AssertionError(f"unexpected agent call: {kwargs['trueforge_agent_name']}")

    try:
        with patch("app.agents.signal_extraction.run_agent_reasoning", side_effect=fake_run_agent_reasoning), \
             patch("app.agents.persona_fit.run_agent_reasoning", side_effect=fake_run_agent_reasoning):
            try:
                run_pipeline_for_lead(db_session, sample_lead)
                raised = False
            except PipelinePausedForApproval as exc:
                raised = True
                assert len(exc.requests) == 1
                assert exc.requests[0].tool_name == "classify_company_industry"
                assert exc.requests[0].status == "pending"
    finally:
        graph_module._compiled_graph = None

    assert raised

    from sqlalchemy import select

    persisted = (
        db_session.execute(
            select(ToolApprovalRequest).where(ToolApprovalRequest.lead_id == sample_lead.id)
        )
        .scalars()
        .all()
    )
    assert len(persisted) == 1
    assert persisted[0].tool_call_id == "call-9"
