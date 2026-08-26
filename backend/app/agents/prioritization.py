"""Prioritization/Ranking Agent.

Unlike the other five agents, this one reasons across the whole pipeline at
once rather than a single lead: given every lead's current (non-superseded)
buying-stage classification, it produces an explicit contact-priority order
so a rep knows who to call first, with a reason per lead — not just a raw
score. Real LLM call, same as every other agent; only the scope (many leads
per call instead of one) differs.

Two-phase ranking via genuine TrueForge subagent delegation
-------------------------------------------------------------
The root agent's system instruction requires it to run two phases within one
TrueForge turn:

1. **Per-lead priority assessment (parallel).** For every lead, call
   TrueForge's built-in `create_sub_agent` tool — genuine subagent
   delegation, not a hand-rolled Python `asyncio.gather` over multiple
   `run_turn` calls — passing that lead's stage/confidence/justification and
   asking for a short, isolated `{lead_id, priority_score, reasoning}`
   assessment. TrueForge runs these subagent threads concurrently, each with
   its own fresh context (no shared message history, no visibility into any
   other lead), and returns only each subagent's final result to the root.
2. **Consolidation (root context).** Once every subagent has returned, the
   root agent — which alone has seen every subagent's output — produces the
   final cross-lead ordering, still applying the existing comparative
   judgment (e.g. a high-confidence mid-stage lead can outrank a low-
   confidence late-stage one), because that comparison inherently needs a
   step that sees every lead's signal together, not just one lead in
   isolation the way each subagent does.

This is implemented as instructions to the model (TrueForge subagent
delegation is a runtime feature the model itself decides to invoke via a
built-in tool — see docs/DECISIONS.md for how this was verified against the
live API), not a Python-side fan-out; `run_agent_reasoning_with_delegations`
additionally returns TrueForge's own session-event record of which
subagents actually ran, so a ranking run's `AgentRun.output` carries real
evidence of delegation rather than trusting the model's self-report.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.agents.common import finish_run, run_agent_reasoning_with_delegations, start_run
from app.core.config import get_settings
from app.core.llm import LLMError
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

# With per-lead assessment now delegated to parallel TrueForge subagents,
# each of which gets its own fresh, isolated context, the old ceiling (tuned
# for a single sequential prompt embedding every lead's data at once) no
# longer reflects the real constraint. The remaining bottleneck is the root
# agent's own turn: it must issue one create_sub_agent tool call per lead and
# then read every subagent's short result back into its own context for
# consolidation — bounded by the root's iteration_limit (100, see
# app.core.trueforge.ensure_agent's default RuntimeConfig) and by keeping the
# consolidation-phase context (N short {lead_id, priority_score, reasoning}
# results) comfortably inside one turn. 150 was chosen as 2.5x the old limit:
# generous enough that this build's realistic pipeline sizes (dozens of
# leads) are nowhere close to it, conservative enough to stay well under the
# iteration/context ceilings above without live-testing pipeline sizes this
# build has no real data for. Still a documented, enforced limit with a clear
# error rather than an unbounded assumption.
MAX_LEADS_PER_RANKING_CALL = 150


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
        result, delegations = run_agent_reasoning_with_delegations(
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

    # Real evidence of subagent delegation for the trace, not just the
    # model's self-report: how many per-lead subagents TrueForge's own
    # session events recorded actually ran, and how long each took. Empty
    # when TrueForge was unreachable and the call fell back to a direct LLM
    # call (no subagent capability on that path), or if the model chose not
    # to delegate for a very small lead count.
    result["subagent_delegation"] = {
        "used": len(delegations) > 0,
        "subagent_count": len(delegations),
        "subagents": delegations,
    }

    finish_run(db, run, output=result, reasoning=result.get("summary", ""))
    return result
