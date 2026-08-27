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

# Name-only reference to the skill registered by _ensure_style_guide_skill;
# TrueForge loads only name+description into base context, fetching the full
# SKILL.md content on demand.
_STYLE_GUIDE_SKILL_NAME = "outreach-copywriting-style-guide"
_STYLE_GUIDE_SKILLS = [{"name": _STYLE_GUIDE_SKILL_NAME}]

# TrueForge skills are git-backed only; must point at a reachable ref
# containing backend/app/agents/skills/outreach_copywriting_style_guide/SKILL.md.
_STYLE_GUIDE_REPO_URL = "https://github.com/ram-from-tvl/signalis"
_STYLE_GUIDE_REPO_PATH = "backend/app/agents/skills/outreach_copywriting_style_guide"
_STYLE_GUIDE_REPO_REF = "main"

# Attaches Hunter.io so the agent can check whether the lead's email is
# actually deliverable before generating copy for it.
_MCP_SERVERS = [
    {
        "name": "signalis-hunter",
        "enable_tools": ["@all"],
        "require_approval_for_tools": [],
    },
]

# Task-mechanical only; copywriting craft guidance lives in the
# outreach-copywriting-style-guide TrueForge skill instead.
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

Before finalizing message_copy, use the verify_email tool (and find_email if the lead's email is
missing but you have a company domain to search) to check whether this lead's email is actually
deliverable. If verify_email reports the address is invalid or undeliverable, say so plainly in
the plan's summary field as a blocker a rep should resolve before sending outreach, rather than
silently writing copy for an address that will bounce. Do not fabricate a verification result if
the tool was not queried (e.g. no API key configured) — report the email's deliverability as
unverified instead. Include your verification outcome in the email_verification field of your
response: {"queried": true/false, "email": the address checked, "status": Hunter's status string
if queried}.

You have access to the "outreach-copywriting-style-guide" skill, which covers channel-appropriate
tone, how to reference a buying signal without sounding surveillance-creepy, how to structure the
touchpoint cadence, how to avoid generic AI-sounding copy, and examples of strong vs. weak opening
lines. Consult it before writing message_copy for any touchpoint — it carries the actual
copywriting craft guidance this instruction does not repeat."""


def _ensure_style_guide_skill() -> list[dict] | None:
    """Register the copywriting-craft skill with TrueForge (idempotent),
    returning the name-only skill reference to attach to the agent manifest.

    Registration is best-effort: returns None on failure rather than
    raising, so a hiccup degrades to "no skill this call" rather than
    failing the whole step. Callers must treat None as "unavailable this
    call", not "no skill ever" — see `run_outreach_planner`'s condensed-
    guidance fallback and `ensure_agent`'s skills=None preserve semantics.
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
        f"Lead email: {lead.email or '(missing)'}\n"
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
            "email_verification": {
                "type": "object",
                "properties": {
                    "queried": {"type": "boolean"},
                    "email": {"type": "string"},
                    "status": {"type": "string"},
                },
            },
        },
        "required": ["touchpoints", "channels", "summary"],
    }

    skills = _ensure_style_guide_skill()

    # If skill registration failed, fold the same condensed guidance used on
    # the direct-LLM fallback into the TrueForge instruction too, so this
    # degrades to "condensed guidance" rather than "no guidance at all".
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
            mcp_servers=_MCP_SERVERS,
            skills=skills,
            fallback_style_guidance=CONDENSED_STYLE_GUIDANCE,
        )
    except LLMError as exc:
        finish_run(db, run, output={"error": str(exc)}, reasoning=str(exc), status="failed")
        raise

    # Model-authored, like every other agent's structured output (not
    # independently re-verified against TrueForge session events the way
    # Prioritization's delegation evidence is — the worst case here is a
    # conservative "unverified" report, not a false capability claim).
    ev = result.get("email_verification") or {}
    result["verified_email"] = ev.get("email")
    result["email_verification_status"] = ev.get("status") if ev.get("queried") else "unverified"

    finish_run(db, run, output=result, reasoning=result.get("summary", ""), trueforge_session_id=session_id)
    return result
