"""Buying Stage Orchestrator Agent.

Aggregates a lead's extracted signals over a rolling window, weighs recency
and signal strength, and assigns an overall buying stage with a confidence
score and a plain-language justification. This is the node whose confidence
score drives the human-approval checkpoint in the LangGraph graph.
"""
from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.agents.common import finish_run, run_agent_reasoning, start_run
from app.core.config import get_settings
from app.core.llm import LLMError
from app.core.sandbox import run_signal_scoring
from app.models import Lead, Signal

SYSTEM_INSTRUCTION = """You are the Buying Stage Orchestrator Agent inside a B2B sales
intelligence system. You receive a lead's classified interaction signals (each with an
event_type, an intent_stage_hint of early/mid/late, and how many days ago it occurred) plus
that lead's persona-fit assessment. Weigh more recent signals more heavily than older ones, and
weigh strong late-stage signals (pricing page visits, demo requests, trial signups) more heavily
than repeated weak early-stage signals (single page views). Assign one overall buying stage of
early, mid, or late, a confidence score between 0 and 1 reflecting how much the evidence actually
supports that stage (low confidence when signals conflict, are old, or are sparse), and a
plain-language justification a sales rep could read in ten seconds and trust. A weak or
contradictory signal history should produce a genuinely low confidence score, not an
artificially inflated one."""


def _days_ago(occurred_at: datetime.datetime | None) -> int:
    if occurred_at is None:
        return 9999
    delta = datetime.datetime.utcnow() - occurred_at
    return max(delta.days, 0)


def run_buying_stage(
    db: Session, lead: Lead, signals: list[Signal], persona_fit: dict[str, Any]
) -> dict[str, Any]:
    settings = get_settings()
    window_days = settings.signal_window_days
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=window_days)
    windowed = [s for s in signals if (s.occurred_at or datetime.datetime.min) >= cutoff]
    considered = windowed or signals

    input_summary = (
        f"{len(considered)} signal(s) in {window_days}-day window for {lead.name} ({lead.company})"
    )
    run = start_run(db, lead_id=lead.id, agent_name="buying_stage_orchestrator", input_summary=input_summary)

    if not considered:
        result = {
            "stage": "early",
            "confidence": 0.2,
            "justification": "No interaction signals are on record for this lead yet, "
            "so an early-stage classification is assumed with low confidence.",
        }
        finish_run(db, run, output=result, reasoning=result["justification"])
        return result

    rows = "\n".join(
        f"- event_type={s.event_type}, intent_stage_hint={s.intent_stage_hint}, "
        f"days_ago={_days_ago(s.occurred_at)}, source={s.raw_source}"
        for s in considered
    )

    signal_rows = [
        {"intent_stage_hint": s.intent_stage_hint, "days_ago": _days_ago(s.occurred_at)}
        for s in considered
    ]
    score, execution_path = run_signal_scoring(signal_rows)

    prompt = (
        f"Lead: {lead.name} at {lead.company} ({lead.title or 'title unknown'}).\n"
        f"Persona fit assessment: {persona_fit.get('fit', 'unknown')} — "
        f"{persona_fit.get('reasoning', 'no reasoning available')}\n\n"
        f"Signals within the last {window_days} days (or all available history if fewer):\n{rows}\n\n"
        f"A recency- and strength-weighted signal score has been computed: "
        f"{score['weighted_score']} (higher means stronger, more recent intent; "
        f"roughly 1.0=early, 1.6=mid, 3.0=late scale). Use it as supporting evidence, "
        f"not as the sole determinant.\n\n"
        "Assign the overall buying stage, confidence, and justification."
    )

    schema = {
        "type": "object",
        "properties": {
            "stage": {"type": "string", "enum": ["early", "mid", "late"]},
            "confidence": {"type": "number"},
            "justification": {"type": "string"},
        },
        "required": ["stage", "confidence", "justification"],
    }

    try:
        result = run_agent_reasoning(
            trueforge_agent_name="signalis-buying-stage-orchestrator",
            model=get_settings().trueforge_model,
            system_instruction=SYSTEM_INSTRUCTION,
            prompt=prompt,
            response_schema=schema,
            temperature=0.2,
        )
    except LLMError as exc:
        finish_run(db, run, output={"error": str(exc)}, reasoning=str(exc), status="failed")
        raise

    result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.0))))
    result["signal_score"] = score["weighted_score"]
    result["signal_score_computed_via"] = execution_path
    finish_run(db, run, output=result, reasoning=result.get("justification", ""))
    return result
