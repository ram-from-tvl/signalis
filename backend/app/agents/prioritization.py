"""Prioritization/Ranking Agent.

Unlike the other five agents, this one reasons across the whole pipeline at
once rather than a single lead: given every lead's current (non-superseded)
buying-stage classification, it produces an explicit contact-priority order
so a rep knows who to call first, with a reason per lead — not just a raw
score. Real LLM call, same as every other agent; only the scope (many leads
per call instead of one) differs.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.common import finish_run, run_agent_reasoning, start_run
from app.core.config import get_settings
from app.core.llm import LLMError
from app.models.entities import Lead, StageClassification

SYSTEM_INSTRUCTION = """You are the Prioritization/Ranking Agent inside a B2B sales intelligence
system. You are given every lead currently in the pipeline with its buying stage, confidence
score, and stage justification. Produce a single priority order across ALL of them — rank 1 is
who a rep should contact first today. Weigh buying stage most heavily (late beats mid beats
early), but let confidence and justification break ties or override a naive stage-only ordering
when the evidence clearly calls for it (e.g. a high-confidence mid-stage lead with an urgent,
time-sensitive signal can outrank a low-confidence late-stage lead). For every lead, give a short,
specific reason for its rank a rep could read in five seconds — not a repeat of the stage label.
Include every lead you are given, exactly once, with no gaps or duplicate ranks."""


def run_prioritization(db: Session, leads_with_classifications: list[tuple[Lead, StageClassification]]) -> dict[str, Any]:
    input_summary = f"Ranking {len(leads_with_classifications)} classified lead(s) across the pipeline"
    run = start_run(db, lead_id=None, agent_name="prioritization", input_summary=input_summary)

    if not leads_with_classifications:
        result = {"ranking": [], "summary": "No classified leads to rank yet.", "agent_run_id": run.id}
        finish_run(db, run, output=result, reasoning=result["summary"])
        return result

    rows = "\n".join(
        f"- lead_id={lead.id}, name={lead.name}, company={lead.company}, "
        f"stage={classification.stage}, confidence={classification.confidence:.2f}, "
        f"justification={classification.justification}"
        for lead, classification in leads_with_classifications
    )

    prompt = (
        f"Rank the following {len(leads_with_classifications)} lead(s) by contact priority "
        f"(rank 1 = contact first):\n{rows}\n\n"
        "Return every lead exactly once, ranked 1..N, with a short reason per lead."
    )

    schema = {
        "type": "object",
        "properties": {
            "ranking": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "lead_id": {"type": "string"},
                        "rank": {"type": "integer"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["lead_id", "rank", "reasoning"],
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["ranking", "summary"],
    }

    try:
        result = run_agent_reasoning(
            trueforge_agent_name="signalis-prioritization",
            model=get_settings().trueforge_model,
            system_instruction=SYSTEM_INSTRUCTION,
            prompt=prompt,
            response_schema=schema,
            temperature=0.2,
        )
    except LLMError as exc:
        finish_run(db, run, output={"error": str(exc)}, reasoning=str(exc), status="failed")
        raise

    valid_lead_ids = {lead.id for lead, _ in leads_with_classifications}
    ranking = [entry for entry in result.get("ranking", []) if entry.get("lead_id") in valid_lead_ids]
    ranking.sort(key=lambda e: e.get("rank", len(ranking) + 1))
    result["ranking"] = ranking
    result["agent_run_id"] = run.id

    finish_run(db, run, output=result, reasoning=result.get("summary", ""))
    return result
