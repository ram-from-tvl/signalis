"""Prioritization/Ranking Agent.

Unlike the other five agents, this one reasons across the whole pipeline at
once rather than a single lead: given every lead's current (non-superseded)
buying-stage classification, it produces an explicit contact-priority order
so a rep knows who to call first, with a reason per lead — not just a raw
score. Real LLM call, same as every other agent; only the scope (many leads
per call instead of one) differs.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.agents.common import finish_run, run_agent_reasoning, start_run
from app.core.config import get_settings
from app.core.llm import LLMError
from app.models import Lead, StageClassification

logger = logging.getLogger("signalis.agents")

SYSTEM_INSTRUCTION = """You are the Prioritization/Ranking Agent inside a B2B sales intelligence
system. You are given every lead currently in the pipeline with its buying stage, confidence
score, and stage justification. Produce a single priority order across ALL of them — rank 1 is
who a rep should contact first today. Weigh buying stage most heavily (late beats mid beats
early), but let confidence and justification break ties or override a naive stage-only ordering
when the evidence clearly calls for it (e.g. a high-confidence mid-stage lead with an urgent,
time-sensitive signal can outrank a low-confidence late-stage lead). For every lead, give a short,
specific reason for its rank a rep could read in five seconds — not a repeat of the stage label.
Include every lead you are given, exactly once, with no gaps or duplicate ranks."""

# The prompt embeds one row (and the model must emit one ranking entry) per
# lead, with no batching/summarization — bounded here so a very large
# pipeline fails predictably with a clear error rather than silently
# exceeding a provider's context window or the TrueForge turn timeout.
MAX_LEADS_PER_RANKING_CALL = 60


class PrioritizationError(RuntimeError):
    """Raised when the pipeline is too large to rank in a single call."""


def _normalize_ranking(
    raw_ranking: list[dict], leads_with_classifications: list[tuple[Lead, StageClassification]]
) -> list[dict]:
    """Force the model's output to satisfy the contract stated in the system
    prompt (every lead exactly once, ranks 1..N with no gaps or duplicates),
    since the JSON schema alone cannot enforce uniqueness/completeness and a
    malformed or partial response must never silently produce a broken
    snapshot. Missing leads are appended in their original order; duplicate
    or out-of-range ranks are discarded and reassigned sequentially."""
    by_id = {lead.id: (lead, classification) for lead, classification in leads_with_classifications}

    # Sort by the model's declared rank first (falling back to input order
    # for entries with a missing/non-numeric rank), so a correctly-ordered
    # response with duplicate or gapped rank *numbers* still comes out in
    # the model's intended order rather than raw list order.
    def _declared_rank(entry: dict) -> int:
        rank = entry.get("rank")
        return rank if isinstance(rank, int) else len(raw_ranking) + 1

    seen: set[str] = set()
    ordered_entries: list[dict] = []
    for entry in sorted(raw_ranking, key=_declared_rank):
        lead_id = entry.get("lead_id")
        if lead_id in by_id and lead_id not in seen:
            seen.add(lead_id)
            ordered_entries.append(entry)

    for lead_id, (lead, _classification) in by_id.items():
        if lead_id not in seen:
            ordered_entries.append(
                {"lead_id": lead_id, "reasoning": "Not ranked by the model; appended for completeness."}
            )
            seen.add(lead_id)

    normalized = []
    for rank, entry in enumerate(ordered_entries, start=1):
        lead, classification = by_id[entry["lead_id"]]
        normalized.append(
            {
                "lead_id": lead.id,
                "rank": rank,
                "reasoning": entry.get("reasoning") or "",
                "name": lead.name,
                "company": lead.company,
                "title": lead.title,
                "stage": classification.stage,
                "confidence": classification.confidence,
            }
        )
    return normalized


def run_prioritization(db: Session, leads_with_classifications: list[tuple[Lead, StageClassification]]) -> dict[str, Any]:
    input_summary = f"Ranking {len(leads_with_classifications)} classified lead(s) across the pipeline"
    run = start_run(db, lead_id=None, agent_name="prioritization", input_summary=input_summary)

    if not leads_with_classifications:
        result = {"ranking": [], "summary": "No classified leads to rank yet.", "agent_run_id": run.id}
        finish_run(db, run, output=result, reasoning=result["summary"])
        return result

    if len(leads_with_classifications) > MAX_LEADS_PER_RANKING_CALL:
        error = PrioritizationError(
            f"{len(leads_with_classifications)} classified leads exceeds the "
            f"{MAX_LEADS_PER_RANKING_CALL}-lead limit for a single ranking call"
        )
        finish_run(db, run, output={"error": str(error)}, reasoning=str(error), status="failed")
        raise error

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
        result, session_id = run_agent_reasoning(
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

    raw_ranking = result.get("ranking", [])
    if len(raw_ranking) != len(leads_with_classifications):
        logger.warning(
            "Prioritization agent returned %d ranking entries for %d leads; normalizing.",
            len(raw_ranking),
            len(leads_with_classifications),
        )
    result["ranking"] = _normalize_ranking(raw_ranking, leads_with_classifications)
    result["agent_run_id"] = run.id

    # trueforge_session_id is persisted here like every other agent, but note
    # that the follow-up Q&A endpoint (app/api/routes/agent_followups.py) is
    # scoped under /api/leads/{lead_id}/agent-runs/{agent_run_id} and this
    # run's lead_id is always None (it's a pipeline-wide run, not
    # lead-scoped) — so this session is currently unreachable through any
    # existing endpoint. That's an intentional, documented scope limitation
    # for this PR rather than an oversight; see docs/DECISIONS.md. The
    # session id is still saved so a future prioritization-scoped follow-up
    # path can use it without a backfill.
    finish_run(db, run, output=result, reasoning=result.get("summary", ""), trueforge_session_id=session_id)
    return result
