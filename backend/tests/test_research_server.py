"""Tests for the research MCP server's search_company_news tool.

Mirrors the mocking style in test_llm_fallback_and_sandbox.py: the Tavily
HTTP call is mocked at the httpx boundary, and get_settings is patched to
control whether TAVILY_API_KEY is considered configured. The tool must never
raise — every branch here (missing key, transport failure, malformed
response) is expected to return the graceful `queried: False` shape instead.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from app.mcp_tools.research_server import search_company_news


def test_search_company_news_returns_normalized_results_on_success():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "results": [
            {"title": "Acme raises Series B", "url": "https://news.example.com/1", "content": "Acme Corp raised $30M."},
            {"title": "Acme hires new VP Eng", "url": "https://news.example.com/2", "content": "Acme is expanding engineering."},
        ]
    }
    with patch("app.mcp_tools.research_server.get_settings") as mock_settings, \
         patch("httpx.post", return_value=fake_response) as mock_post:
        mock_settings.return_value.tavily_api_key = "fake-tavily-key"
        result = search_company_news("Acme Corp")

    assert result["company_name"] == "Acme Corp"
    assert result["queried"] is True
    assert len(result["results"]) == 2
    assert result["results"][0] == {
        "title": "Acme raises Series B",
        "url": "https://news.example.com/1",
        "snippet": "Acme Corp raised $30M.",
    }
    # Confirm the call actually went to Tavily's search endpoint with the key.
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["json"]["api_key"] == "fake-tavily-key"
    assert "Acme Corp" in call_kwargs["json"]["query"]


def test_search_company_news_caps_results_at_five():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "results": [{"title": f"Story {i}", "url": f"https://x.com/{i}", "content": "..."} for i in range(10)]
    }
    with patch("app.mcp_tools.research_server.get_settings") as mock_settings, \
         patch("httpx.post", return_value=fake_response):
        mock_settings.return_value.tavily_api_key = "fake-tavily-key"
        result = search_company_news("Acme Corp")

    assert len(result["results"]) == 5


def test_search_company_news_includes_focus_in_query_when_given():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"results": []}
    with patch("app.mcp_tools.research_server.get_settings") as mock_settings, \
         patch("httpx.post", return_value=fake_response) as mock_post:
        mock_settings.return_value.tavily_api_key = "fake-tavily-key"
        search_company_news("Acme Corp", focus="funding")

    call_kwargs = mock_post.call_args.kwargs
    assert "funding" in call_kwargs["json"]["query"]


def test_search_company_news_returns_graceful_shape_when_key_unset():
    """Real, unmocked execution of the actual 'no API key configured' path —
    exercises the real get_settings() default (tavily_api_key='') rather
    than mocking it, so this genuinely proves the fallback behaves without
    a configured key, not just that the logic branch exists."""
    with patch("app.mcp_tools.research_server.get_settings") as mock_settings:
        mock_settings.return_value.tavily_api_key = ""
        result = search_company_news("Acme Corp")

    assert result == {
        "company_name": "Acme Corp",
        "results": [],
        "queried": False,
        "reason": "TAVILY_API_KEY is not configured",
    }


def test_search_company_news_returns_graceful_shape_on_transport_failure():
    with patch("app.mcp_tools.research_server.get_settings") as mock_settings, \
         patch("httpx.post", side_effect=httpx.ConnectError("connection refused")):
        mock_settings.return_value.tavily_api_key = "fake-tavily-key"
        result = search_company_news("Acme Corp")

    assert result["queried"] is False
    assert result["results"] == []
    assert result["company_name"] == "Acme Corp"
    assert "reason" in result


def test_search_company_news_returns_graceful_shape_on_timeout():
    with patch("app.mcp_tools.research_server.get_settings") as mock_settings, \
         patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
        mock_settings.return_value.tavily_api_key = "fake-tavily-key"
        result = search_company_news("Acme Corp")

    assert result["queried"] is False
    assert result["results"] == []


def test_search_company_news_returns_graceful_shape_on_http_error_status():
    fake_response = MagicMock()
    fake_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error", request=MagicMock(), response=fake_response
    )
    with patch("app.mcp_tools.research_server.get_settings") as mock_settings, \
         patch("httpx.post", return_value=fake_response):
        mock_settings.return_value.tavily_api_key = "fake-tavily-key"
        result = search_company_news("Acme Corp")

    assert result["queried"] is False
    assert result["results"] == []


def test_search_company_news_returns_graceful_shape_on_malformed_response_shape():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    # "results" is a string, not a list of dicts -- exercises the malformed-shape branch.
    fake_response.json.return_value = {"results": "not-a-list"}
    with patch("app.mcp_tools.research_server.get_settings") as mock_settings, \
         patch("httpx.post", return_value=fake_response):
        mock_settings.return_value.tavily_api_key = "fake-tavily-key"
        result = search_company_news("Acme Corp")

    assert result["queried"] is False
    assert result["results"] == []
