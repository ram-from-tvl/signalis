"""TrueForge's native per-tool approval, surfaced as real API endpoints.

Follows the same style as app.api.routes.approvals: look up the row, act on
it, persist the outcome, return the updated resource. The difference here is
that "acting on it" means resuming a paused TrueForge turn over HTTP rather
than only flipping a status column — see app.agents.common.resume_agent_reasoning
and app.core.trueforge.resume_turn.
"""
from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.common import AgentPausedForToolApproval
from app.agents.persona_fit import resume_persona_fit
from app.api.deps import get_db
from app.core.llm import LLMError
from app.core.trueforge import TrueForgeError
from app.models import AgentRun, ToolApprovalRequest
from app.schemas import ToolApprovalActionRequest, ToolApprovalRequestOut

logger = logging.getLogger("signalis.tool_approvals")

router = APIRouter(prefix="/api/tool-approvals", tags=["tool-approvals"])


@router.get("", response_model=list[ToolApprovalRequestOut])
def list_pending_tool_approvals(lead_id: str | None = None, db: Session = Depends(get_db)):
    """Lists pending tool-approval requests, optionally filtered to one lead
    (LeadDetailPage uses the filtered form to show only what's relevant to
    the lead being viewed)."""
    query = select(ToolApprovalRequest).where(ToolApprovalRequest.status == "pending")
    if lead_id:
        query = query.where(ToolApprovalRequest.lead_id == lead_id)
    query = query.order_by(ToolApprovalRequest.created_at.desc())
    return db.execute(query).scalars().all()


def _get_pending_request(db: Session, request_id: str) -> ToolApprovalRequest:
    request = db.get(ToolApprovalRequest, request_id)
    if not request:
        raise HTTPException(404, "Tool approval request not found")
    if request.status != "pending":
        raise HTTPException(409, f"Tool approval request already {request.status}")
    return request


@router.post("/{request_id}/approve", response_model=ToolApprovalRequestOut)
def approve_tool_call(request_id: str, db: Session = Depends(get_db)):
    """Approves the pending tool call: resumes the TrueForge turn with
    approval: allow, lets Persona Fit's result complete, and persists it via
    finish_run exactly as a non-paused run would. If the resumed turn hits
    another approval gate (a second gated tool call in the same turn), a new
    ToolApprovalRequest is persisted instead of silently dropping it."""
    request = _get_pending_request(db, request_id)
    agent_run = db.get(AgentRun, request.agent_run_id) if request.agent_run_id else None
    if agent_run is None:
        raise HTTPException(
            409, "The agent run backing this approval request is missing; cannot resume it."
        )

    try:
        resume_persona_fit(
            db,
            agent_run,
            session_id=request.session_id,
            thread_id=request.thread_id,
            tool_call_id=request.tool_call_id,
            approve=True,
        )
    except AgentPausedForToolApproval as exc:
        _mark_resolved(db, request, "approved")
        _persist_followup_requests(db, request, exc)
        db.commit()
        raise HTTPException(
            202,
            "Approved, but the agent immediately hit another tool-approval gate. "
            "A new pending request has been created.",
        ) from exc
    except (TrueForgeError, LLMError) as exc:
        logger.warning("Failed to resume tool approval %s: %s", request_id, exc)
        raise HTTPException(502, f"Failed to resume the paused agent turn: {exc}") from exc

    _mark_resolved(db, request, "approved")
    db.commit()
    db.refresh(request)
    return request


@router.post("/{request_id}/reject", response_model=ToolApprovalRequestOut)
def reject_tool_call(request_id: str, payload: ToolApprovalActionRequest, db: Session = Depends(get_db)):
    """Rejects the pending tool call: resumes the TrueForge turn with
    approval: deny (optionally with a reason shown to the agent), and marks
    the backing AgentRun as failed/rejected rather than leaving it stuck in
    "running" forever."""
    request = _get_pending_request(db, request_id)
    agent_run = db.get(AgentRun, request.agent_run_id) if request.agent_run_id else None

    try:
        if agent_run is not None:
            resume_persona_fit(
                db,
                agent_run,
                session_id=request.session_id,
                thread_id=request.thread_id,
                tool_call_id=request.tool_call_id,
                approve=False,
                deny_reason=payload.reason or "Denied by marketer via tool-approval review",
            )
    except AgentPausedForToolApproval as exc:
        # Even on denial, the agent may still be mid-turn on another gated
        # tool call it invoked before this one resolved.
        _mark_resolved(db, request, "rejected")
        _persist_followup_requests(db, request, exc)
        db.commit()
        raise HTTPException(
            202,
            "Rejected, but the agent immediately hit another tool-approval gate. "
            "A new pending request has been created.",
        ) from exc
    except (TrueForgeError, LLMError):
        # A denial is still a denial even if the resume call itself failed
        # transport-wise (the turn will simply time out on TrueForge's side);
        # don't block marking this rejected on that.
        logger.warning("Resume-with-deny transport failed for %s; marking rejected anyway", request_id)
        if agent_run is not None:
            agent_run.status = "failed"
            agent_run.reasoning = "Tool call rejected by marketer; TrueForge resume call also failed."
            agent_run.completed_at = datetime.datetime.utcnow()
            db.add(agent_run)

    _mark_resolved(db, request, "rejected")
    if agent_run is not None and agent_run.status == "running":
        # resume_persona_fit's own finish_run already marks it failed when
        # the denial completes normally (LLMError path inside the agent), but
        # guard here too in case the resumed turn produced a result instead
        # of erroring out on denial (model-dependent behavior).
        agent_run.status = "failed"
        agent_run.reasoning = agent_run.reasoning or "Tool call rejected by marketer."
        agent_run.completed_at = datetime.datetime.utcnow()
        db.add(agent_run)
    db.commit()
    db.refresh(request)
    return request


def _mark_resolved(db: Session, request: ToolApprovalRequest, status: str) -> None:
    request.status = status
    request.resolved_at = datetime.datetime.utcnow()
    db.add(request)


def _persist_followup_requests(
    db: Session, original: ToolApprovalRequest, exc: AgentPausedForToolApproval
) -> None:
    for pending in exc.pending:
        db.add(
            ToolApprovalRequest(
                lead_id=original.lead_id,
                agent_run_id=original.agent_run_id,
                trueforge_agent_name=original.trueforge_agent_name,
                session_id=pending.session_id,
                turn_id=pending.turn_id,
                thread_id=pending.thread_id,
                tool_call_id=pending.tool_call_id,
                tool_name=pending.tool_name,
                tool_input=pending.tool_input,
                status="pending",
            )
        )
