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
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agents.common import AgentPausedForToolApproval
from app.agents.persona_fit import resume_persona_fit
from app.api.deps import get_db
from app.core.llm import LLMError
from app.core.trueforge import TrueForgeError
from app.models import AgentRun, ToolApprovalRequest
from app.schemas import ToolApprovalActionRequest, ToolApprovalRequestOut, ToolApprovalResolutionOut

logger = logging.getLogger("signalis.tool_approvals")

router = APIRouter(prefix="/api/tool-approvals", tags=["tool-approvals"])


@router.get("", response_model=list[ToolApprovalRequestOut])
def list_pending_tool_approvals(lead_id: str | None = None, db: Session = Depends(get_db)):
    """Lists pending tool-approval requests, optionally filtered to one
    lead. Excludes "claimed" rows too — a row mid-resolution by a
    concurrent request shouldn't be offered as a decision to a second
    viewer (see _claim_pending_request)."""
    query = select(ToolApprovalRequest).where(ToolApprovalRequest.status == "pending")
    if lead_id:
        query = query.where(ToolApprovalRequest.lead_id == lead_id)
    query = query.order_by(ToolApprovalRequest.created_at.desc())
    return db.execute(query).scalars().all()


def _get_request_or_404(db: Session, request_id: str) -> ToolApprovalRequest:
    request = db.get(ToolApprovalRequest, request_id)
    if not request:
        raise HTTPException(404, "Tool approval request not found")
    return request


def _claim_pending_request(db: Session, request_id: str) -> ToolApprovalRequest:
    """Atomically transitions a request from "pending" to "claimed" and
    returns the now-claimed row, or raises 404/409.

    Compare-and-swap via the UPDATE's WHERE clause: two concurrent approve/
    reject calls on the same request can't both see "pending" and race to
    resolve it independently. Claiming before any TrueForge I/O also means
    a crashed/slow request leaves the row "claimed" (409 on retry), not
    "pending" (which would allow a second racing attempt).
    """
    result = db.execute(
        update(ToolApprovalRequest)
        .where(ToolApprovalRequest.id == request_id, ToolApprovalRequest.status == "pending")
        .values(status="claimed")
    )
    db.commit()
    if result.rowcount == 0:
        request = db.get(ToolApprovalRequest, request_id)
        if not request:
            raise HTTPException(404, "Tool approval request not found")
        raise HTTPException(409, f"Tool approval request already {request.status}")

    request = db.get(ToolApprovalRequest, request_id)
    assert request is not None  # the UPDATE above just affected this row
    return request


def _release_claim(db: Session, request: ToolApprovalRequest) -> None:
    """Restores a claimed request to "pending" so it can be retried, used
    when the TrueForge call fails and the decision was never delivered."""
    request.status = "pending"
    db.add(request)
    db.commit()


@router.post("/{request_id}/approve", response_model=ToolApprovalResolutionOut)
def approve_tool_call(request_id: str, db: Session = Depends(get_db)):
    """Approves the pending tool call: resumes the TrueForge turn with
    approval: allow and lets Persona Fit's result complete. If the resumed
    turn hits another gated tool call, a new ToolApprovalRequest is
    persisted and surfaced as `followup` in the response body — always
    HTTP 200, so a client can't mistake an ambiguous 2xx for "done"."""
    request = _claim_pending_request(db, request_id)
    agent_run = db.get(AgentRun, request.agent_run_id) if request.agent_run_id else None
    if agent_run is None:
        _release_claim(db, request)
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
        followup = _persist_followup_requests(db, request, exc)
        db.commit()
        db.refresh(request)
        return ToolApprovalResolutionOut(resolved=request, followup=followup)
    except (TrueForgeError, LLMError) as exc:
        logger.warning("Failed to resume tool approval %s: %s", request_id, exc)
        _release_claim(db, request)
        raise HTTPException(502, f"Failed to resume the paused agent turn: {exc}") from exc

    _mark_resolved(db, request, "approved")
    db.commit()
    db.refresh(request)
    return ToolApprovalResolutionOut(resolved=request, followup=None)


@router.post("/{request_id}/reject", response_model=ToolApprovalResolutionOut)
def reject_tool_call(request_id: str, payload: ToolApprovalActionRequest, db: Session = Depends(get_db)):
    """Rejects the pending tool call: resumes the turn with approval: deny
    and marks the backing AgentRun failed.

    A transport failure here is NOT treated as an equivalent to a
    successful denial — whether TrueForge actually applied the deny is
    unknown, so the request is released back to "pending" for retry rather
    than falsely reported resolved."""
    request = _claim_pending_request(db, request_id)
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
        _mark_resolved(db, request, "rejected")
        followup = _persist_followup_requests(db, request, exc)
        db.commit()
        db.refresh(request)
        return ToolApprovalResolutionOut(resolved=request, followup=followup)
    except (TrueForgeError, LLMError) as exc:
        logger.warning("Resume-with-deny failed for %s; leaving pending for retry: %s", request_id, exc)
        _release_claim(db, request)
        raise HTTPException(502, f"Failed to deliver the rejection to the paused agent turn: {exc}") from exc

    _mark_resolved(db, request, "rejected")
    if agent_run is not None and agent_run.status == "running":
        # Guard in case agent_run was never actually resumed.
        agent_run.status = "failed"
        agent_run.reasoning = agent_run.reasoning or "Tool call rejected by marketer."
        agent_run.completed_at = datetime.datetime.utcnow()
        db.add(agent_run)
    db.commit()
    db.refresh(request)
    return ToolApprovalResolutionOut(resolved=request, followup=None)


def _mark_resolved(db: Session, request: ToolApprovalRequest, status: str) -> None:
    request.status = status
    request.resolved_at = datetime.datetime.utcnow()
    db.add(request)


def _persist_followup_requests(
    db: Session, original: ToolApprovalRequest, exc: AgentPausedForToolApproval
) -> ToolApprovalRequest | None:
    """Persists one ToolApprovalRequest per tool call TrueForge is now
    pausing on, reconciling against any existing row for the same
    (session_id, tool_call_id) instead of inserting a duplicate — a
    multi-call pause already has one pending row per call, and resolving
    one shouldn't leave the others stranded alongside new duplicates.

    Returns the first followup request (approve/reject only ever expect
    one in practice)."""
    existing_by_call_id = {
        row.tool_call_id: row
        for row in db.execute(
            select(ToolApprovalRequest).where(
                ToolApprovalRequest.session_id == original.session_id,
                ToolApprovalRequest.status.in_(("pending", "claimed")),
            )
        ).scalars()
    }

    followups: list[ToolApprovalRequest] = []
    for pending in exc.pending:
        reused = existing_by_call_id.get(pending.tool_call_id)
        if reused is not None:
            reused.turn_id = pending.turn_id
            reused.thread_id = pending.thread_id
            reused.tool_name = pending.tool_name
            reused.tool_input = pending.tool_input
            db.add(reused)
            followups.append(reused)
            continue

        request = ToolApprovalRequest(
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
        db.add(request)
        followups.append(request)

    return followups[0] if followups else None
