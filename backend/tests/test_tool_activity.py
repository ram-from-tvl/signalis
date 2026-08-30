"""Tests for app.agents.tool_activity — the plain-language tool-usage
labels surfaced to marketers/SDRs instead of raw MCP/tool jargon."""
from __future__ import annotations

from unittest.mock import patch

from app.agents.tool_activity import TOOL_LABELS, extract_tools_used, tool_label
from app.core.trueforge import TrueForgeError


def test_extract_tools_used_returns_empty_without_a_session():
    assert extract_tools_used(None, {"classify_company_industry"}) == []


def test_extract_tools_used_maps_raw_names_to_plain_labels():
    tool_results = [
        {"tool_name": "classify_company_industry", "result": {"industry": "SaaS"}},
        {"tool_name": "search_company_news", "result": {"articles": []}},
    ]
    with patch("app.agents.tool_activity.get_tool_call_results", return_value=tool_results) as mock_results:
        used = extract_tools_used("session-abc", {"classify_company_industry", "search_company_news"})

    mock_results.assert_called_once_with("session-abc", {"classify_company_industry", "search_company_news"})
    assert used == [
        {"tool": "classify_company_industry", "label": TOOL_LABELS["classify_company_industry"]},
        {"tool": "search_company_news", "label": TOOL_LABELS["search_company_news"]},
    ]


def test_extract_tools_used_dedupes_repeated_calls():
    tool_results = [
        {"tool_name": "classify_company_industry", "result": {}},
        {"tool_name": "classify_company_industry", "result": {}},
    ]
    with patch("app.agents.tool_activity.get_tool_call_results", return_value=tool_results):
        used = extract_tools_used("session-abc", {"classify_company_industry"})

    assert len(used) == 1


def test_extract_tools_used_returns_empty_when_events_fetch_fails():
    """A failed session-events fetch must degrade to an empty list, not
    raise — this is display-only detail, not worth failing a pipeline run
    over (same tradeoff as _extract_email_verification's
    evidence_unavailable path)."""
    with patch("app.agents.tool_activity.get_tool_call_results", side_effect=TrueForgeError("boom")):
        used = extract_tools_used("session-abc", {"classify_company_industry"})

    assert used == []


def test_tool_label_falls_back_to_raw_name_for_unrecognized_tools():
    assert tool_label("some_future_tool") == "some_future_tool"


def test_tool_label_known_tools_are_non_technical():
    for label in TOOL_LABELS.values():
        assert "mcp" not in label.lower()
        assert "tool" not in label.lower()
        assert "session" not in label.lower()
