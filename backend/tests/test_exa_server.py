"""Tests for the Exa MCP server's search_company_semantic tool.

Mirrors test_research_server.py's mocking style: the Exa HTTP call is mocked
at the httpx boundary, and get_settings is patched to control whether
EXA_API_KEY is considered configured. The tool must never raise — every
branch here (missing key, transport failure, malformed response) is expected
to return the graceful `queried: False` shape instead.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from app.mcp_tools.exa_server import search_company_semantic


def test_search_company_semantic_returns_normalized_results_on_success():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "results": [
            {"title": "Acme raises Series B", "url": "https://news.example.com/1", "text": "Acme Corp raised $30M."},
            {"title": "Acme hires new VP Eng", "url": "https://news.example.com/2", "text": "Acme is expanding engineering."},
        ]
    }
    with patch("app.mcp_tools.exa_server.get_settings") as mock_settings, \
         patch("httpx.post", return_value=fake_response) as mock_post:
        mock_settings.return_value.exa_api_key = "fake-exa-key"
        result = search_company_semantic("Acme Corp")

    assert result["company_name"] == "Acme Corp"
    assert result["queried"] is True
    assert len(result["results"]) == 2
    assert result["results"][0] == {
        "title": "Acme raises Series B",
        "url": "https://news.example.com/1",
        "snippet": "Acme Corp raised $30M.",
    }
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["headers"]["Authorization"] == "Bearer fake-exa-key"
    assert "Acme Corp" in call_kwargs["json"]["query"]


def test_search_company_semantic_uses_highlights_when_text_missing():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "results": [{"title": "Acme", "url": "https://x.com", "highlights": ["a relevant snippet"]}]
    }
    with patch("app.mcp_tools.exa_server.get_settings") as mock_settings, \
         patch("httpx.post", return_value=fake_response):
        mock_settings.return_value.exa_api_key = "fake-exa-key"
        result = search_company_semantic("Acme Corp")

    assert result["results"][0]["snippet"] == "a relevant snippet"


def test_search_company_semantic_caps_results_at_five():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "results": [{"title": f"Story {i}", "url": f"https://x.com/{i}", "text": "..."} for i in range(10)]
    }
    with patch("app.mcp_tools.exa_server.get_settings") as mock_settings, \
         patch("httpx.post", return_value=fake_response):
        mock_settings.return_value.exa_api_key = "fake-exa-key"
        result = search_company_semantic("Acme Corp")

    assert len(result["results"]) == 5


def test_search_company_semantic_includes_focus_in_query_when_given():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"results": []}
    with patch("app.mcp_tools.exa_server.get_settings") as mock_settings, \
         patch("httpx.post", return_value=fake_response) as mock_post:
        mock_settings.return_value.exa_api_key = "fake-exa-key"
        search_company_semantic("Acme Corp", focus="funding")

    call_kwargs = mock_post.call_args.kwargs
    assert "funding" in call_kwargs["json"]["query"]


def test_search_company_semantic_returns_graceful_shape_when_key_unset():
    """Real, unmocked execution of the actual 'no API key configured' path."""
    with patch("app.mcp_tools.exa_server.get_settings") as mock_settings:
        mock_settings.return_value.exa_api_key = ""
        result = search_company_semantic("Acme Corp")

    assert result == {
        "company_name": "Acme Corp",
        "results": [],
        "queried": False,
        "reason": "EXA_API_KEY is not configured",
    }


def test_search_company_semantic_returns_graceful_shape_on_transport_failure():
    with patch("app.mcp_tools.exa_server.get_settings") as mock_settings, \
         patch("httpx.post", side_effect=httpx.ConnectError("connection refused")):
        mock_settings.return_value.exa_api_key = "fake-exa-key"
        result = search_company_semantic("Acme Corp")

    assert result["queried"] is False
    assert result["results"] == []
    assert result["company_name"] == "Acme Corp"
    assert "reason" in result


def test_search_company_semantic_returns_graceful_shape_on_timeout():
    with patch("app.mcp_tools.exa_server.get_settings") as mock_settings, \
         patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
        mock_settings.return_value.exa_api_key = "fake-exa-key"
        result = search_company_semantic("Acme Corp")

    assert result["queried"] is False
    assert result["results"] == []


def test_search_company_semantic_returns_graceful_shape_on_http_error_status():
    fake_response = MagicMock()
    fake_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error", request=MagicMock(), response=fake_response
    )
    with patch("app.mcp_tools.exa_server.get_settings") as mock_settings, \
         patch("httpx.post", return_value=fake_response):
        mock_settings.return_value.exa_api_key = "fake-exa-key"
        result = search_company_semantic("Acme Corp")

    assert result["queried"] is False
    assert result["results"] == []


def test_search_company_semantic_returns_graceful_shape_on_malformed_response_shape():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"results": "not-a-list"}
    with patch("app.mcp_tools.exa_server.get_settings") as mock_settings, \
         patch("httpx.post", return_value=fake_response):
        mock_settings.return_value.exa_api_key = "fake-exa-key"
        result = search_company_semantic("Acme Corp")

    assert result["queried"] is False
    assert result["results"] == []


def test_search_company_semantic_returns_graceful_shape_when_results_field_missing():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"some_other_field": "unexpected"}
    with patch("app.mcp_tools.exa_server.get_settings") as mock_settings, \
         patch("httpx.post", return_value=fake_response):
        mock_settings.return_value.exa_api_key = "fake-exa-key"
        result = search_company_semantic("Acme Corp")

    assert result["queried"] is False
    assert result["results"] == []
    assert "reason" in result


def test_search_company_semantic_rejects_blank_company_name_without_calling_exa():
    with patch("httpx.post") as mock_post:
        result = search_company_semantic("   ")

    mock_post.assert_not_called()
    assert result["queried"] is False
    assert result["results"] == []
    assert "reason" in result


def test_search_company_semantic_rejects_missing_company_name_without_calling_exa():
    with patch("httpx.post") as mock_post:
        result = search_company_semantic("")

    mock_post.assert_not_called()
    assert result["queried"] is False
    assert result["results"] == []


def test_search_company_semantic_rejects_oversized_company_name_without_calling_exa():
    with patch("httpx.post") as mock_post:
        result = search_company_semantic("A" * 201)

    mock_post.assert_not_called()
    assert result["queried"] is False
    assert result["results"] == []
    assert "reason" in result


def test_search_company_semantic_rejects_oversized_focus_without_calling_exa():
    with patch("app.mcp_tools.exa_server.get_settings") as mock_settings, \
         patch("httpx.post") as mock_post:
        mock_settings.return_value.exa_api_key = "fake-exa-key"
        result = search_company_semantic("Acme Corp", focus="x" * 201)

    mock_post.assert_not_called()
    assert result["queried"] is False
    assert result["results"] == []
    assert "reason" in result


def test_search_company_semantic_accepts_company_name_at_max_length():
    """Boundary check: exactly _MAX_INPUT_LENGTH characters must still be
    accepted and reach Exa (only over-the-limit input is rejected)."""
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"results": []}
    with patch("app.mcp_tools.exa_server.get_settings") as mock_settings, \
         patch("httpx.post", return_value=fake_response) as mock_post:
        mock_settings.return_value.exa_api_key = "fake-exa-key"
        result = search_company_semantic("A" * 200)

    mock_post.assert_called_once()
    assert result["queried"] is True
