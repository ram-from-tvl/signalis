"""Plain-language labels for external tool calls an agent turn genuinely
made, for surfacing to non-technical users (marketers/SDRs) instead of raw
MCP/tool jargon.

Uses the same "prove it happened via TrueForge's own session data" method as
app.agents.outreach_planner._extract_email_verification: a tool is only
reported as used if it shows up as a real tool_call/tool.response pair in
the session's own event stream, not because the model's final answer claims
it called something.
"""
from __future__ import annotations

import logging

from app.core.trueforge import TrueForgeError, get_tool_call_results

logger = logging.getLogger("signalis.agents.tool_activity")

# Present-participle, outcome-focused phrasing — matches the voice already
# used in frontend/src/lib/agents.ts's AGENT_RUNNING_INSIGHT ("Classifying
# raw CRM and website events...", "Comparing this lead's role..."). No
# mention of "MCP", "tool call", "session", or server names.
TOOL_LABELS: dict[str, str] = {
    "classify_company_industry": "Looked up the company's industry",
    "estimate_company_size_band": "Estimated the company's size",
    "search_company_news": "Checked recent news about the company",
    "search_company_semantic": "Searched for more context about the company",
    "verify_email": "Verified the lead's email address",
}


def extract_tools_used(session_id: str | None, tool_names: set[str]) -> dict:
    """Returns {"items": [{"tool": <raw name>, "label": <plain-language
    label>}, ...], "evidence_unavailable": bool} for every tool in
    tool_names that genuinely ran on this session, in the order TrueForge
    recorded them.

    evidence_unavailable distinguishes "we asked and genuinely nothing ran"
    (items=[], evidence_unavailable=False — a normal, honest outcome) from
    "we couldn't ask" (items=[], evidence_unavailable=True — the
    session-events fetch itself failed, e.g. a TrueForge outage), the same
    distinction _extract_email_verification's "evidence_unavailable" status
    makes for verify_email. Never raises — this is display-only detail, not
    worth failing a pipeline run over."""
    if not session_id:
        return {"items": [], "evidence_unavailable": False}
    try:
        tool_results = get_tool_call_results(session_id, tool_names)
    except TrueForgeError as exc:
        logger.warning(
            "Could not fetch session events to report which tools were used; "
            "marking tool-activity evidence unavailable for this run: %s",
            exc,
        )
        return {"items": [], "evidence_unavailable": True}

    seen: set[str] = set()
    used: list[dict[str, str]] = []
    for entry in tool_results:
        name = entry["tool_name"]
        if name in seen:
            continue
        result = entry.get("result")
        # Tools with a real external dependency (search_company_news via
        # Tavily, search_company_semantic via Exa — same convention as
        # verify_email) report queried=False on transport/config/malformed-
        # response failure rather than raising; a call is only genuine
        # activity if it actually reached the external service. Tools with
        # no "queried" key (classify_company_industry,
        # estimate_company_size_band) are always-local/deterministic and
        # have no such failure mode, so their mere presence is enough.
        if isinstance(result, dict) and result.get("queried") is False:
            continue
        seen.add(name)
        used.append({"tool": name, "label": TOOL_LABELS.get(name, name)})
    return {"items": used, "evidence_unavailable": False}


def tool_label(tool_name: str) -> str:
    """Plain-language label for a single tool name, for contexts (like the
    pending tool-approval card) that only have the raw name, not a full
    session to query. Falls back to the raw name if unrecognized so the UI
    never renders empty."""
    return TOOL_LABELS.get(tool_name, tool_name)
