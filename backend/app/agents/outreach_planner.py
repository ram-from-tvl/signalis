"""Outreach Planner Agent.

Generates a 1-2 week outreach micro-plan tailored to a lead's buying stage,
persona fit, and the solution's positioning. Callable repeatedly for the
same lead so that a new signal (e.g. a fresh pricing page visit) produces
an updated plan reflecting current state, not a stale one.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.agents.common import finish_run, run_agent_reasoning, start_run
from app.agents.skills.outreach_copywriting_style_guide import CONDENSED_STYLE_GUIDANCE
from app.core.config import get_settings
from app.core.llm import LLMError
from app.core.trueforge import ensure_skill
from app.models import Lead, Persona, Solution

logger = logging.getLogger("signalis.agents.outreach_planner")

# Name-only reference to the skill registered by `_ensure_style_guide_skill`
# below. TrueForge loads only this name + the skill's description into the
# agent's base context; the full craft guidance in SKILL.md is fetched into
# context only when the model decides a given touchpoint actually needs it —
# see docs/DECISIONS.md for why this was extracted out of SYSTEM_INSTRUCTION.
_STYLE_GUIDE_SKILL_NAME = "outreach-copywriting-style-guide"
_STYLE_GUIDE_SKILLS = [{"name": _STYLE_GUIDE_SKILL_NAME}]

# Repo/path/ref the skill's content is fetched from. TrueForge skills are
# git-backed (see app.core.trueforge.ensure_skill's docstring) — there is no
# "post raw content" registration shape in the real API — so this must point
# at a real, reachable ref containing backend/app/agents/skills/
# outreach_copywriting_style_guide/SKILL.md.
_STYLE_GUIDE_REPO_URL = "https://github.com/ram-from-tvl/signalis"
_STYLE_GUIDE_REPO_PATH = "backend/app/agents/skills/outreach_copywriting_style_guide"
_STYLE_GUIDE_REPO_REF = "main"

# Task-mechanical instructions only: what fields to output, what lead/persona
# context to weigh, and touchpoint count/day-offset requirements. The actual
# copywriting craft (tone by channel, referencing signals tactfully, cadence
# structure, avoiding generic AI-sounding copy, good/bad examples) used to be
# baked directly into this string and re-sent on every single call; it now
# lives in the `outreach-copywriting-style-guide` TrueForge skill instead, and
# this instruction just tells the model the skill exists and when to use it.
SYSTEM_INSTRUCTION = """You are the Outreach Planner Agent inside a B2B sales intelligence
system. Given a lead's buying stage, confidence, persona fit, and the solution's value
proposition and differentiators, produce a concrete 1-2 week outreach micro-plan.

Output requirements:
- Sequence 3-5 touchpoints, each with a day_offset (0 = today), a channel drawn from the
  solution's available channels, a content_theme, and message_copy specific to this lead's
  company, title, and stage — never generic boilerplate.
- Weigh the lead's buying stage, confidence, persona fit, and target persona's seniority/role
  when choosing tone and directness: early-stage leads get educational, low-pressure touches;
  late-stage leads get direct, action-oriented touches (e.g. proposing a specific next call).

You have access to the "outreach-copywriting-style-guide" skill, which covers channel-appropriate
tone, how to reference a buying signal without sounding surveillance-creepy, how to structure the
touchpoint cadence, how to avoid generic AI-sounding copy, and examples of strong vs. weak opening
lines. Consult it before writing message_copy for any touchpoint — it carries the actual
copywriting craft guidance this instruction does not repeat."""


def _ensure_style_guide_skill() -> list[dict] | None:
    """Register the copywriting-craft skill with TrueForge (idempotent, same
    "already exists is success" contract as `ensure_agent`), returning the
    name-only skill reference to attach to the agent's manifest.

    Registration is best-effort: if TrueForge is disabled or unreachable this
    silently returns None rather than raising, so a skill-registration hiccup
    degrades to "no skill attached this call" instead of failing the whole
    outreach-planning step — `run_agent_reasoning` already has its own
    TrueForge-unreachable fallback for the reasoning call itself.

    IMPORTANT: `None` here must be read by callers as "skill unavailable
    *this call*", never as "no skill wanted, ever". `run_outreach_planner`
    below reacts to a `None` return by injecting `CONDENSED_STYLE_GUIDANCE`
    directly into the TrueForge instruction for that call (so the run still
    gets real copywriting guidance instead of running fully unguided), and
    `ensure_agent` treats a `skills=None` argument as "caller has no opinion
    this call" — it preserves whatever skills a previously-successful call
    already attached to the agent's manifest rather than overwriting them
    with a skill-less one. See `app.core.trueforge.ensure_agent`/
    `_update_agent` docstrings for the update-path mechanics.
    """
    if not get_settings().trueforge_enabled:
        return None
    try:
        ensure_skill(
            _STYLE_GUIDE_SKILL_NAME,
            repo_url=_STYLE_GUIDE_REPO_URL,
            path=_STYLE_GUIDE_REPO_PATH,
            ref=_STYLE_GUIDE_REPO_REF,
            description=(
                "B2B outreach copywriting craft guidance: channel-appropriate tone, "
                "referencing a buying signal without sounding surveillance-creepy, "
                "structuring a 3-5 touchpoint cadence, avoiding generic AI-sounding copy, "
                "and strong vs. weak opening line examples. Consult when writing or "
                "revising a touchpoint's message_copy."
            ),
        )
        return _STYLE_GUIDE_SKILLS
    except Exception as exc:  # noqa: BLE001 - registration is best-effort, see docstring
        logger.warning("Failed to register outreach copywriting skill, continuing without it: %s", exc)
        return None


def run_outreach_planner(
    db: Session,
    lead: Lead,
    stage: str,
    confidence: float,
    persona_fit: dict[str, Any],
    persona: Persona | None,
    solution: Solution | None,
) -> dict[str, Any]:
    input_summary = f"Outreach plan for {lead.name} ({lead.company}) at stage={stage}"
    run = start_run(db, lead_id=lead.id, agent_name="outreach_planner", input_summary=input_summary)

    channels = solution.channels if solution and solution.channels else ["email"]
    solution_desc = (
        f"name={solution.name}, problem_solved={solution.problem_solved}, "
        f"value_props={solution.value_props}, differentiators={solution.differentiators}"
        if solution
        else "No solution has been defined; use generic B2B SaaS value language."
    )
    persona_desc = (
        f"role={persona.role}, seniority={persona.seniority}" if persona else "No persona defined."
    )

    prompt = (
        f"Lead: {lead.name}, {lead.title or 'unknown title'} at {lead.company} "
        f"({lead.industry or 'unknown industry'}, {lead.company_size or 'unknown size'}).\n"
        f"Buying stage: {stage} (confidence {confidence:.2f}).\n"
        f"Persona fit: {persona_fit.get('fit', 'unknown')} — {persona_fit.get('reasoning', '')}\n"
        f"Target persona: {persona_desc}\n"
        f"Solution: {solution_desc}\n"
        f"Available channels: {channels}\n\n"
        "Generate the outreach micro-plan."
    )

    schema = {
        "type": "object",
        "properties": {
            "touchpoints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "day_offset": {"type": "integer"},
                        "channel": {"type": "string"},
                        "content_theme": {"type": "string"},
                        "message_copy": {"type": "string"},
                    },
                    "required": ["day_offset", "channel", "content_theme", "message_copy"],
                },
            },
            "channels": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
        "required": ["touchpoints", "channels", "summary"],
    }

    skills = _ensure_style_guide_skill()

    # If the skill failed to register this call, the TrueForge path would
    # otherwise run with neither the full skill (not attached) nor the
    # condensed guidance (that string is only injected on the *direct LLM*
    # fallback path below) — i.e. no copywriting craft guidance at all,
    # while still being persisted as an indistinguishable "completed" run.
    # Fold the same condensed guidance already used on the direct-fallback
    # path into the TrueForge instruction too, so a skill-registration
    # hiccup degrades to "condensed guidance instead of the full skill"
    # rather than "no guidance at all". This mirrors the app's existing
    # policy of never silently degrading a run without at least recording
    # real, guided reasoning (see the identical precedent for
    # `fallback_style_guidance` on the direct-LLM path just below).
    trueforge_instruction = SYSTEM_INSTRUCTION
    if skills is None:
        logger.warning(
            "Outreach copywriting skill unavailable this call; injecting condensed "
            "style guidance into the TrueForge instruction instead of running unguided."
        )
        trueforge_instruction = f"{SYSTEM_INSTRUCTION}\n\n{CONDENSED_STYLE_GUIDANCE}"

    try:
        result, session_id = run_agent_reasoning(
            trueforge_agent_name="signalis-outreach-planner",
            model=get_settings().trueforge_model,
            system_instruction=trueforge_instruction,
            prompt=prompt,
            response_schema=schema,
            temperature=0.4,
            skills=skills,
            fallback_style_guidance=CONDENSED_STYLE_GUIDANCE,
        )
    except LLMError as exc:
        finish_run(db, run, output={"error": str(exc)}, reasoning=str(exc), status="failed")
        raise

    finish_run(db, run, output=result, reasoning=result.get("summary", ""), trueforge_session_id=session_id)
    return result
