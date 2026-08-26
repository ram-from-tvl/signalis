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
    """Lists pending tool-approval requests, optionally filtered to one lead
    (LeadDetailPage uses the filtered form to show only what's relevant to
    the lead being viewed). Deliberately excludes "claimed" rows too — a row
    mid-resolution by a concurrent request is not something a second viewer
    should be offered a decision on (see _claim_pending_request)."""
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

    This is a compare-and-swap: the UPDATE's WHERE clause only matches rows
    still "pending", so if two concurrent approve/reject calls race on the
    same request_id, exactly one UPDATE affects a row (SQLite and Postgres
    both serialize concurrent UPDATEs to the same row) and the other sees
    rowcount == 0. Without this, both requests could read status == "pending"
    via a plain db.get, both proceed to call TrueForge, and whichever commits
    last would silently overwrite the other's decision — see the "Approval
    resolution races" finding this fixes.

    Doing the claim before any TrueForge I/O also means a crashed or slow
    request leaves the row in "claimed", not "pending" — a future decision
    endpoint call on it correctly 409s instead of allowing a second, racing
    attempt to resolve the same tool call.
    """
    result = db.execute(
        update(ToolApprovalRequest)
        .where(ToolApprovalRequest.id == request_id, ToolApprovalRequest.status == "pending")
        .values(status="claimed")
    )
    db.commit()
    if result.rowcount == 0:
        # Either the row doesn't exist, or it's not pending anymore (already
        # claimed/resolved by a concurrent request, or by an earlier call).
        request = db.get(ToolApprovalRequest, request_id)
        if not request:
            raise HTTPException(404, "Tool approval request not found")
        raise HTTPException(409, f"Tool approval request already {request.status}")

    request = db.get(ToolApprovalRequest, request_id)
    assert request is not None  # the UPDATE above just affected this row
    return request


def _release_claim(db: Session, request: ToolApprovalRequest) -> None:
    """Restores a claimed request to "pending" so it can be retried, used
    when the external TrueForge call fails in a way that leaves the local
    decision undelivered (see the "Hide deny-resume failures" finding)."""
    request.status = "pending"
    db.add(request)
    db.commit()


@router.post("/{request_id}/approve", response_model=ToolApprovalResolutionOut)
def approve_tool_call(request_id: str, db: Session = Depends(get_db)):
    """Approves the pending tool call: resumes the TrueForge turn with
    approval: allow, lets Persona Fit's result complete, and persists it via
    finish_run exactly as a non-paused run would. If the resumed turn hits
    another approval gate (a second gated tool call in the same turn), a new
    ToolApprovalRequest is persisted instead of silently dropping it, and
    surfaced to the caller as `followup` in the response body (always HTTP
    200 — see ToolApprovalResolutionOut) rather than as an ambiguous 202
    that a plain 2xx-is-success HTTP client can't distinguish from "done"."""
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
    """Rejects the pending tool call: resumes the TrueForge turn with
    approval: deny (optionally with a reason shown to the agent), and marks
    the backing AgentRun as failed rather than leaving it stuck in "running"
    forever.

    A resume call that fails transport-wise (TrueForgeError/LLMError) is NOT
    treated as an equivalent to a successful denial: whether TrueForge ever
    received and applied the deny is genuinely unknown in that case (the
    turn may still be paused, or may have already timed out on TrueForge's
    side), so the request is released back to "pending" (not resolved) and
    the caller gets the same 502 the approve endpoint returns for the same
    failure mode — the marketer can retry the rejection, and the UI keeps
    showing this as awaiting a decision instead of falsely reporting it
    resolved."""
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
        # Even on denial, the agent may still be mid-turn on another gated
        # tool call it invoked before this one resolved.
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
        # resume_persona_fit finishes a denied run as "failed" itself now,
        # but guard here too in case agent_run was never actually resumed
        # (e.g. its backing AgentRun row was missing entirely).
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
    pausing on, reconciling against any row that already exists for the same
    (session_id, tool_call_id) instead of blindly inserting a duplicate.

    A multi-call pause creates one pending row per call up front (see
    app.services.pipeline.run_pipeline_for_lead). Resolving one of those rows
    resumes the turn with only that call's decision; if TrueForge reports the
    *same remaining* calls as still pending (rather than a genuinely new
    call), inserting fresh rows for them would leave the original rows for
    those calls stranded in "pending" forever alongside new duplicates for
    the same tool_call_id — letting the UI offer two independent decisions
    for what TrueForge treats as one outstanding approval.

    Returns the first followup request (approve/reject only ever expect one
    in practice, since Persona Fit's enrichment gate covers two tools calls
    at most, but this handles more without losing any)."""
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
            # Already tracked (e.g. a sibling call from the original pause
            # that's still awaiting its own decision) — refresh the mutable
            # fields TrueForge may have updated and reuse the row rather than
            # creating a duplicate.
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
