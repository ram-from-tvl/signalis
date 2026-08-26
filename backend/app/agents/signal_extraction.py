"""Signal Extraction Agent.

Takes a lead's raw interaction rows (CRM fields + website events, already
stored as Signal rows with raw_payload) and asks Gemini to classify each one
into a normalized event_type and a rough intent stage hint. This agent is
what makes messy/incomplete source data usable by the rest of the pipeline:
it is explicitly prompted to cope with missing fields and to never fail the
whole batch over one bad row.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.common import finish_run, run_agent_reasoning, start_run
from app.core.config import get_settings
from app.core.llm import LLMError
from app.models import Lead, Signal

SYSTEM_INSTRUCTION = """You are the Signal Extraction Agent inside a B2B sales intelligence system.
You receive raw, sometimes messy interaction records for one lead (CRM fields and/or website
events). For each record, classify it into a normalized event_type (short snake_case label such
as pricing_page_visit, demo_request, demo_attended, content_download, email_reply, email_open,
case_study_view, feature_page_visit, homepage_visit, blog_visit, contact_form_submit,
free_trial_signup, deal_stage_change, unknown) and an intent_stage_hint of exactly one of:
early, mid, late.

Guidance: pricing page visits, demo requests/attendance, free trial signups, and contact form
submissions are late-stage signals. Case studies, feature pages, repeated content downloads,
and email replies are mid-stage. Generic page views, single blog visits, and first-touch
homepage visits are early-stage. If a record is missing fields or malformed, make the best
reasonable inference from whatever is present rather than discarding it, and never invent data
that is not implied by the record. Always return exactly one classification per input record,
in the same order given."""


def _signal_to_prompt_row(signal: Signal, index: int) -> str:
    return (
        f"Record {index}: source={signal.raw_source}, "
        f"occurred_at={signal.occurred_at.isoformat() if signal.occurred_at else 'unknown'}, "
        f"payload={signal.raw_payload}"
    )


def run_signal_extraction(db: Session, lead: Lead, signals: list[Signal]) -> dict[str, Any]:
    """Classify a batch of a lead's raw signals. Persists an AgentRun and
    updates each Signal's event_type/intent_stage_hint in place."""
    input_summary = f"{len(signals)} raw signal(s) for lead {lead.name} ({lead.company})"
    run = start_run(db, lead_id=lead.id, agent_name="signal_extraction", input_summary=input_summary)

    if not signals:
        result = {"classifications": []}
        finish_run(db, run, output=result, reasoning="No raw signals to classify.")
        return result

    rows = "\n".join(_signal_to_prompt_row(s, i) for i, s in enumerate(signals))
    prompt = (
        f"Lead: {lead.name}, title={lead.title}, company={lead.company}, "
        f"industry={lead.industry}, company_size={lead.company_size}.\n\n"
        f"Classify the following {len(signals)} interaction record(s):\n{rows}"
    )

    schema = {
        "type": "object",
        "properties": {
            "classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "event_type": {"type": "string"},
                        "intent_stage_hint": {
                            "type": "string",
                            "enum": ["early", "mid", "late"],
                        },
                        "note": {"type": "string"},
                    },
                    "required": ["index", "event_type", "intent_stage_hint"],
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["classifications", "summary"],
    }

    try:
        result, session_id = run_agent_reasoning(
            trueforge_agent_name="signalis-signal-extraction",
            model=get_settings().trueforge_model,
            system_instruction=SYSTEM_INSTRUCTION,
            prompt=prompt,
            response_schema=schema,
            temperature=0.1,
        )
    except LLMError as exc:
        finish_run(db, run, output={"error": str(exc)}, reasoning=str(exc), status="failed")
        raise

    classifications = {c["index"]: c for c in result.get("classifications", [])}
    for i, signal in enumerate(signals):
        classification = classifications.get(i)
        if classification is None:
            continue
        signal.event_type = classification.get("event_type", signal.event_type or "unknown")
        signal.intent_stage_hint = classification.get("intent_stage_hint", signal.intent_stage_hint or "early")
        signal.extracted_by_agent_run_id = run.id
        db.add(signal)
    db.commit()

    finish_run(
        db,
        run,
        output=result,
        reasoning=result.get("summary", "Signals classified."),
        trueforge_session_id=session_id,
    )
    return result
