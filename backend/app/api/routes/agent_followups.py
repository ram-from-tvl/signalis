"""Marketer follow-up Q&A against a single AgentRun's TrueForge session.

TrueForge's session store persists a turn's conversation independent of
Signalis's own DB (survives reconnects/restarts on TrueForge's side). This
route is what actually exploits that: instead of re-running the agent's
prompt from scratch or answering statelessly, a follow-up question is
posted as a new turn on the SAME session_id that produced the original
run's reasoning, so the model genuinely remembers what it said and why.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.llm import LLMError
from app.core.trueforge import TrueForgeError, run_followup_turn
from app.models import AgentRun, AgentRunFollowup
from app.schemas import AgentRunFollowupCreate, AgentRunFollowupOut

router = APIRouter(prefix="/api/leads/{lead_id}/agent-runs/{agent_run_id}", tags=["agent-followups"])

_FOLLOWUP_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def _get_agent_run(db: Session, lead_id: str, agent_run_id: str) -> AgentRun:
    # Lead-scoped by design: a pipeline-wide run (lead_id=None, currently
    # only prioritization) can never match here, even with a session
    # persisted on it — an intentional scope limitation, not an oversight.
    run = db.get(AgentRun, agent_run_id)
    if not run or run.lead_id != lead_id:
        raise HTTPException(404, "Agent run not found for this lead")
    return run


@router.post("/ask", response_model=AgentRunFollowupOut)
def ask_followup(
    lead_id: str, agent_run_id: str, payload: AgentRunFollowupCreate, db: Session = Depends(get_db)
):
    run = _get_agent_run(db, lead_id, agent_run_id)

    if not run.trueforge_session_id:
        raise HTTPException(
            409,
            "Follow-up questions aren't available for this agent run: it has no TrueForge session "
            "(either TrueForge was disabled/unreachable when it ran, so the direct-Gemini fallback "
            "path was used instead, which never creates a session).",
        )

    question = payload.question.strip()
    if not question:
        raise HTTPException(422, "Question must not be empty")

    message = (
        "You are continuing your own prior reasoning about this lead in this same session — you "
        "already produced the structured analysis earlier in this conversation. A marketer now has "
        "a follow-up question about that analysis. Answer it directly and specifically, referencing "
        "your prior analysis by name where relevant (e.g. the stage, confidence, or fit you assigned "
        "and why). Do not restate your entire prior output; just answer the question.\n\n"
        f"Marketer's follow-up question: {question}\n\n"
        f"Respond with a single JSON object matching this JSON schema exactly, and nothing else: "
        f"{_FOLLOWUP_RESPONSE_SCHEMA}"
    )

    try:
        result = run_followup_turn(run.trueforge_session_id, message)
    except TrueForgeError as exc:
        raise HTTPException(502, f"Follow-up turn failed: {exc}") from exc
    except LLMError as exc:  # pragma: no cover - run_followup_turn never falls back, defensive only
        raise HTTPException(502, f"Follow-up turn failed: {exc}") from exc

    # _extract_json_object is only a best-effort parse, not a validated
    # shape — a missing/blank/wrong-typed answer must never be silently
    # turned into a canned placeholder and persisted as a real exchange.
    # Treat it as the same class of dependency failure as a transport
    # error: 502, no DB row.
    if not isinstance(result, dict):
        raise HTTPException(
            502, f"Follow-up turn failed: TrueForge returned an unexpected response shape: {result!r}"
        )
    raw_answer = result.get("answer")
    if not isinstance(raw_answer, str):
        raise HTTPException(
            502,
            "Follow-up turn failed: TrueForge did not return a string 'answer' "
            f"(got {type(raw_answer).__name__ if raw_answer is not None else 'missing'}).",
        )
    answer = raw_answer.strip()
    if not answer:
        raise HTTPException(502, "Follow-up turn failed: TrueForge returned a blank answer.")

    followup = AgentRunFollowup(agent_run_id=run.id, question=question, answer=answer)
    db.add(followup)
    db.commit()
    db.refresh(followup)
    return followup


@router.get("/followups", response_model=list[AgentRunFollowupOut])
def list_followups(lead_id: str, agent_run_id: str, db: Session = Depends(get_db)):
    _get_agent_run(db, lead_id, agent_run_id)
    return db.execute(
        select(AgentRunFollowup)
        .where(AgentRunFollowup.agent_run_id == agent_run_id)
        .order_by(AgentRunFollowup.created_at)
    ).scalars().all()
