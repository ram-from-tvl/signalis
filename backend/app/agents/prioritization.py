"""Prioritization/Ranking Agent.

Reasons across the whole pipeline at once rather than a single lead: given
every lead's current (non-superseded) buying-stage classification, produces
a contact-priority order with a reason per lead.

Two-phase ranking via TrueForge subagent delegation: the model calls
`create_sub_agent` once per lead (parallel, isolated context per subagent),
then consolidates every subagent's result into the final cross-lead order
in its own context. This is model-decided delegation via a built-in
TrueForge tool, not a Python-side fan-out.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.agents.common import finish_run, run_agent_reasoning_with_delegations, start_run
from app.core.config import get_settings
from app.core.llm import LLMError
from app.core.trueforge import TrueForgeError
from app.models import Lead, StageClassification

logger = logging.getLogger("signalis.agents")

SYSTEM_INSTRUCTION = """You are the Prioritization/Ranking Agent inside a B2B sales intelligence
system. You are given every lead currently in the pipeline with its buying stage, confidence
score, and stage justification. Produce a single priority order across ALL of them — rank 1 is
who a rep should contact first today.

You MUST work in two phases:

PHASE 1 — Per-lead priority assessment, delegated in parallel. For EVERY lead given to you, call
your subagent delegation tool (create_sub_agent) once, in parallel, one subagent per lead. Give
each subagent ONLY that one lead's data (lead_id, stage, confidence, justification) and instruct
it to return strictly this JSON object and nothing else: {"lead_id": "<the lead_id>",
"priority_score": <number 0-100, higher = contact sooner>, "reasoning": "<one sentence>"}. Do not
assess any lead yourself in this phase — every lead's initial assessment must come from its own
subagent. Wait for every subagent to return before moving to phase 2.

PHASE 2 — Consolidation, done by you (the root agent) once every subagent has returned. You are
the only one who sees every lead's assessment together, so this is where the real cross-lead
comparison happens: weigh buying stage most heavily (late beats mid beats early), but let
confidence and justification break ties or override a naive stage-only ordering when the evidence
clearly calls for it (e.g. a high-confidence mid-stage lead with an urgent, time-sensitive signal
can outrank a low-confidence late-stage lead) — use the subagents' priority_score and reasoning as
input to this judgment, not as the final answer by itself. For every lead, give a short, specific
final reason for its rank a rep could read in five seconds — not a repeat of the stage label and
not a verbatim copy of the subagent's reasoning. Include every lead you are given, exactly once,
with no gaps or duplicate ranks."""

# create_sub_agent doesn't exist on the direct Gemini/HF fallback path, so
# this replaces SYSTEM_INSTRUCTION rather than reusing it verbatim.
FALLBACK_SYSTEM_INSTRUCTION = """You are the Prioritization/Ranking Agent inside a B2B sales intelligence
system. You are given every lead currently in the pipeline with its buying stage, confidence
score, and stage justification. Produce a single priority order across ALL of them — rank 1 is
who a rep should contact first today.

No subagent delegation is available for this request. Reason about every lead yourself, directly,
in this one call — do not reference, attempt to call, or claim to have used any subagent or tool.

Weigh buying stage most heavily (late beats mid beats early), but let confidence and justification
break ties or override a naive stage-only ordering when the evidence clearly calls for it (e.g. a
high-confidence mid-stage lead with an urgent, time-sensitive signal can outrank a low-confidence
late-stage lead). For every lead, give a short, specific final reason for its rank a rep could read
in five seconds — not a repeat of the stage label. Include every lead you are given, exactly once,
with no gaps or duplicate ranks."""

# Bounded by the root agent's iteration_limit (100, one create_sub_agent
# call per lead) plus keeping consolidation-phase context in one turn.
MAX_LEADS_PER_RANKING_CALL = 150


class PrioritizationError(RuntimeError):
    """Raised when the pipeline is too large to rank in a single call."""


def _normalize_ranking(
    raw_ranking: list[dict], leads_with_classifications: list[tuple[Lead, StageClassification]]
) -> list[dict]:
    """Normalizes the model's ranking to exactly one entry per lead, ranks
    1..N, no gaps or duplicates — the JSON schema alone can't enforce this."""
    by_id = {lead.id: (lead, classification) for lead, classification in leads_with_classifications}

    # Sort by declared rank first so a correctly-ordered response with
    # duplicate/gapped rank numbers still comes out in the model's order.
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
        f"(rank 1 = contact first). Remember: phase 1 is one create_sub_agent call per lead, in "
        f"parallel, before you do any ranking yourself; phase 2 (the final ranking) only happens "
        f"after every subagent has returned.\n\n{rows}\n\n"
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
        result, delegations, is_delegated = run_agent_reasoning_with_delegations(
            trueforge_agent_name="signalis-prioritization",
            model=get_settings().trueforge_model,
            system_instruction=SYSTEM_INSTRUCTION,
            fallback_instruction=FALLBACK_SYSTEM_INSTRUCTION,
            prompt=prompt,
            response_schema=schema,
            temperature=0.2,
        )
    except (LLMError, TrueForgeError) as exc:
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

    # trueforge_session_id is intentionally not persisted here: this run's
    # lead_id is always None (pipeline-wide, not lead-scoped), so the
    # lead-scoped follow-up endpoint could never reach it anyway.
    #
    # subagent_delegation status is real evidence from TrueForge's session
    # events, not the model's self-report: not_delegated (fell back to
    # direct LLM, no subagent capability), evidence_unavailable (turn
    # succeeded but the events fetch failed), delegated (count matches lead
    # count), partial (fewer subagents ran than leads).
    if not is_delegated:
        status = "not_delegated"
    elif delegations is None:
        status = "evidence_unavailable"
    elif len(delegations) >= len(leads_with_classifications) and len(leads_with_classifications) > 0:
        status = "delegated"
    else:
        status = "partial"

    subagent_count = len(delegations) if delegations is not None else None
    result["subagent_delegation"] = {
        "status": status,
        # Kept for backward compatibility with any existing reader of this
        # field: True only for a genuinely, fully delegated run.
        "used": status == "delegated",
        "subagent_count": subagent_count,
        "expected_count": len(leads_with_classifications),
        "subagents": delegations if delegations is not None else [],
    }
    if status == "partial":
        logger.warning(
            "Prioritization agent delegated %s/%d leads to subagents; expected exactly one "
            "subagent per lead. Marking this run's delegation as partial.",
            subagent_count,
            len(leads_with_classifications),
        )

    finish_run(db, run, output=result, reasoning=result.get("summary", ""))
    return result
