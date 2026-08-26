"""Tests for the TrueForge per-tool approval endpoints (approve/reject a
pending ToolApprovalRequest) and the pipeline-level pause/persist path that
creates them, with the TrueForge HTTP boundary mocked exactly like the other
trueforge tests in test_llm_fallback_and_sandbox.py."""
from __future__ import annotations

from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.agents.common import AgentPausedForToolApproval
from app.core.llm import LLMError
from app.core.trueforge import PendingToolApproval, TrueForgeError
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
    assert body["followup"] is None
    assert body["resolved"]["status"] == "approved"
    assert body["resolved"]["resolved_at"] is not None
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
    assert body["followup"] is None
    assert body["resolved"]["status"] == "rejected"
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

    assert response.status_code == 200
    body = response.json()
    assert body["resolved"]["status"] == "approved"
    assert body["followup"] is not None
    assert body["followup"]["tool_name"] == "estimate_company_size_band"
    assert body["followup"]["status"] == "pending"

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
            return {"extracted_signals": []}, "sess-signal-extraction"
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


def test_reject_surfaces_resume_transport_failure_instead_of_hiding_it(client, sample_lead, db_session):
    """Regression test for "Hide deny-resume failures": if the deny resume
    call itself fails transport-wise, the request must NOT be silently
    marked rejected/resolved — the caller needs a 502 so it knows the
    rejection was not actually delivered to TrueForge, and the request must
    stay retryable ("pending"), not stuck in "claimed" or falsely
    "rejected"."""
    run = _make_pending_run(db_session, sample_lead)
    request = _make_pending_request(db_session, sample_lead, run)

    with patch(
        "app.api.routes.tool_approvals.resume_persona_fit",
        side_effect=TrueForgeError("connection reset"),
    ):
        response = client.post(f"/api/tool-approvals/{request.id}/reject", json={"reason": "not needed"})

    assert response.status_code == 502

    db_session.refresh(request)
    assert request.status == "pending"
    assert request.resolved_at is None

    db_session.refresh(run)
    assert run.status == "running"  # untouched: the deny was never confirmed delivered


def test_approve_surfaces_resume_transport_failure_and_releases_claim(client, sample_lead, db_session):
    """The approve side already returned 502 on transport failure, but must
    also release its atomic claim back to "pending" rather than leaving the
    row stuck in "claimed" forever."""
    run = _make_pending_run(db_session, sample_lead)
    request = _make_pending_request(db_session, sample_lead, run)

    with patch(
        "app.api.routes.tool_approvals.resume_persona_fit",
        side_effect=LLMError("model unreachable"),
    ):
        response = client.post(f"/api/tool-approvals/{request.id}/approve")

    assert response.status_code == 502

    db_session.refresh(request)
    assert request.status == "pending"

    # Retryable: a second approve call should be allowed to proceed again.
    with patch(
        "app.api.routes.tool_approvals.resume_persona_fit",
        return_value={"fit": "full_fit", "reasoning": "matches", "missing_data": []},
    ):
        retry_response = client.post(f"/api/tool-approvals/{request.id}/approve")
    assert retry_response.status_code == 200
    assert retry_response.json()["resolved"]["status"] == "approved"


def test_reject_always_fails_run_even_if_denial_yields_a_normal_result(client, sample_lead, db_session):
    """Regression test for "Reject leaves run completed": some models answer
    normally even after a tool-call denial instead of erroring out. A
    rejected tool call must still leave the backing AgentRun "failed" —
    resume_persona_fit itself must not finish it as "completed" just because
    resume_turn returned a well-formed result dict."""
    from app.agents.persona_fit import resume_persona_fit

    run = _make_pending_run(db_session, sample_lead)
    request = _make_pending_request(db_session, sample_lead, run)

    with patch(
        "app.agents.persona_fit.resume_agent_reasoning",
        return_value={"fit": "mismatch", "reasoning": "answered anyway", "missing_data": []},
    ):
        response = client.post(f"/api/tool-approvals/{request.id}/reject", json={"reason": "denied"})

    assert response.status_code == 200
    db_session.refresh(run)
    assert run.status == "failed"

    # Also verify directly at the agents layer (resume_persona_fit is the
    # actual fix location, not just the route's defensive guard).
    run2 = _make_pending_run(db_session, sample_lead)
    with patch(
        "app.agents.persona_fit.resume_agent_reasoning",
        return_value={"fit": "mismatch", "reasoning": "answered anyway", "missing_data": []},
    ):
        resume_persona_fit(
            db_session, run2, session_id="s", thread_id="main", tool_call_id="c", approve=False
        )
    db_session.refresh(run2)
    assert run2.status == "failed"


def test_followup_reuses_existing_pending_row_for_same_tool_call(client, sample_lead, db_session):
    """Regression test for "Follow-ups duplicate pending calls": a follow-up
    pause that reports a tool_call_id already tracked by an existing
    pending/claimed row for the same session must reuse that row instead of
    inserting a duplicate."""
    run = _make_pending_run(db_session, sample_lead)
    request = _make_pending_request(db_session, sample_lead, run)

    # A sibling pending request from the *original* multi-call pause, same
    # session, different tool_call_id than `request`.
    sibling = ToolApprovalRequest(
        lead_id=sample_lead.id,
        agent_run_id=run.id,
        trueforge_agent_name="signalis-persona-fit",
        session_id="sess-1",
        turn_id="turn-1",
        thread_id="main",
        tool_call_id="call-2",
        tool_name="estimate_company_size_band",
        tool_input={"company_name": "Acme Corp"},
        status="pending",
    )
    db_session.add(sibling)
    db_session.commit()
    db_session.refresh(sibling)

    # TrueForge reports the same call-2 (still pending) again on resume,
    # rather than a genuinely new tool call.
    followup = [
        PendingToolApproval(
            session_id="sess-1",
            turn_id="turn-2",
            thread_id="main",
            tool_call_id="call-2",
            tool_name="estimate_company_size_band",
            tool_input={"company_name": "Acme Corp", "refined": True},
        )
    ]

    with patch(
        "app.api.routes.tool_approvals.resume_persona_fit",
        side_effect=AgentPausedForToolApproval(followup),
    ):
        response = client.post(f"/api/tool-approvals/{request.id}/approve")

    assert response.status_code == 200
    body = response.json()
    assert body["followup"]["id"] == sibling.id

    rows = (
        db_session.execute(select(ToolApprovalRequest).where(ToolApprovalRequest.tool_call_id == "call-2"))
        .scalars()
        .all()
    )
    assert len(rows) == 1  # no duplicate inserted
    assert rows[0].id == sibling.id
    assert rows[0].turn_id == "turn-2"  # refreshed from the followup
    assert rows[0].tool_input == {"company_name": "Acme Corp", "refined": True}


def test_concurrent_claims_on_same_request_only_one_wins(db_session, sample_lead):
    """Regression test for "Approval resolution races".

    Exercises the atomic claim (_claim_pending_request's UPDATE ... WHERE
    status = 'pending' compare-and-swap) that both approve/reject now
    perform before any TrueForge I/O. This deterministically simulates the
    interleaving that matters — a second decision arriving after the first
    has committed its claim but before it has finished resuming the
    TrueForge turn — via two independent Sessions used in a fixed order,
    rather than real OS threads: SQLite's single-writer locking under
    StaticPool makes genuinely concurrent writer threads flaky to assert on
    in a unit test (lock contention surfaces as OperationalError depending
    on timing, which is a SQLite/StaticPool test-harness artifact, not a
    property of the claim logic itself — Postgres row locking under a real
    per-request connection pool doesn't have this issue). The invariant
    under test — the WHERE clause only ever lets one UPDATE affect a row —
    is exactly what a real concurrent-thread race would also exercise.

    Without this claim, both concurrent requests could read status ==
    "pending" via a plain db.get, both proceed to call TrueForge, and
    whichever commits last would silently overwrite the other's decision."""
    from app.api.routes.tool_approvals import _claim_pending_request

    run = _make_pending_run(db_session, sample_lead)
    request = _make_pending_request(db_session, sample_lead, run)
    request_id = request.id

    engine = db_session.get_bind()
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Request A's session claims the row first (as if its UPDATE won the
    # race), exactly as approve_tool_call/reject_tool_call do before
    # starting any TrueForge I/O.
    session_a = SessionLocal()
    claimed = _claim_pending_request(session_a, request_id)
    assert claimed.status == "claimed"

    # Request B's session — a second concurrent decision on the very same
    # request_id — must now see it as no longer pending and be rejected,
    # never allowed to also claim it and independently call TrueForge.
    session_b = SessionLocal()
    try:
        _claim_pending_request(session_b, request_id)
        raised = False
    except HTTPException as exc:
        raised = True
        assert exc.status_code == 409
        assert "claimed" in exc.detail
    finally:
        session_b.close()
    assert raised

    session_a.close()
    db_session.expire_all()
    db_session.refresh(request)
    assert request.status == "claimed"
