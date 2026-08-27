"""Tests for the Hunter.io MCP server's find_email and verify_email tools.

Mirrors test_research_server.py's mocking style: the Hunter HTTP call is
mocked at the httpx boundary, and get_settings is patched to control whether
HUNTER_API_KEY is considered configured. Both tools must never raise —
every branch here (missing key, transport failure, malformed response) is
expected to return the graceful `queried: False` shape instead.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from app.mcp_tools.hunter_server import find_email, verify_email

# ---------------------------------------------------------------------------
# find_email
# ---------------------------------------------------------------------------


def test_find_email_returns_normalized_result_on_success():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"data": {"email": "jane@acme.com", "score": 92}}
    with patch("app.mcp_tools.hunter_server.get_settings") as mock_settings, \
         patch("httpx.get", return_value=fake_response) as mock_get:
        mock_settings.return_value.hunter_api_key = "fake-hunter-key"
        result = find_email("Jane", "Doe", "acme.com")

    assert result == {"email": "jane@acme.com", "score": 92, "queried": True, "reason": None}
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["headers"]["X-API-KEY"] == "fake-hunter-key"
    assert call_kwargs["params"] == {"domain": "acme.com", "first_name": "Jane", "last_name": "Doe"}


def test_find_email_returns_graceful_shape_when_key_unset():
    with patch("app.mcp_tools.hunter_server.get_settings") as mock_settings:
        mock_settings.return_value.hunter_api_key = ""
        result = find_email("Jane", "Doe", "acme.com")

    assert result == {"email": None, "score": None, "queried": False, "reason": "HUNTER_API_KEY is not configured"}


def test_find_email_returns_graceful_shape_on_transport_failure():
    with patch("app.mcp_tools.hunter_server.get_settings") as mock_settings, \
         patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
        mock_settings.return_value.hunter_api_key = "fake-hunter-key"
        result = find_email("Jane", "Doe", "acme.com")

    assert result["queried"] is False
    assert result["email"] is None
    assert "reason" in result


def test_find_email_returns_graceful_shape_on_timeout():
    with patch("app.mcp_tools.hunter_server.get_settings") as mock_settings, \
         patch("httpx.get", side_effect=httpx.TimeoutException("timed out")):
        mock_settings.return_value.hunter_api_key = "fake-hunter-key"
        result = find_email("Jane", "Doe", "acme.com")

    assert result["queried"] is False
    assert result["email"] is None


def test_find_email_returns_graceful_shape_on_http_error_status():
    fake_response = MagicMock()
    fake_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error", request=MagicMock(), response=fake_response
    )
    with patch("app.mcp_tools.hunter_server.get_settings") as mock_settings, \
         patch("httpx.get", return_value=fake_response):
        mock_settings.return_value.hunter_api_key = "fake-hunter-key"
        result = find_email("Jane", "Doe", "acme.com")

    assert result["queried"] is False
    assert result["email"] is None


def test_find_email_returns_graceful_shape_on_malformed_response_shape():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"data": "not-an-object"}
    with patch("app.mcp_tools.hunter_server.get_settings") as mock_settings, \
         patch("httpx.get", return_value=fake_response):
        mock_settings.return_value.hunter_api_key = "fake-hunter-key"
        result = find_email("Jane", "Doe", "acme.com")

    assert result["queried"] is False
    assert result["email"] is None


def test_find_email_returns_graceful_shape_when_data_field_missing():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"errors": [{"details": "invalid domain"}]}
    with patch("app.mcp_tools.hunter_server.get_settings") as mock_settings, \
         patch("httpx.get", return_value=fake_response):
        mock_settings.return_value.hunter_api_key = "fake-hunter-key"
        result = find_email("Jane", "Doe", "acme.com")

    assert result["queried"] is False
    assert result["email"] is None
    assert "reason" in result


def test_find_email_rejects_blank_first_name_without_calling_hunter():
    with patch("httpx.get") as mock_get:
        result = find_email("   ", "Doe", "acme.com")

    mock_get.assert_not_called()
    assert result["queried"] is False


def test_find_email_rejects_oversized_domain_without_calling_hunter():
    with patch("httpx.get") as mock_get:
        result = find_email("Jane", "Doe", "a" * 201)

    mock_get.assert_not_called()
    assert result["queried"] is False


# ---------------------------------------------------------------------------
# verify_email
# ---------------------------------------------------------------------------


def test_verify_email_returns_normalized_result_on_success():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"data": {"status": "valid", "score": 97}}
    with patch("app.mcp_tools.hunter_server.get_settings") as mock_settings, \
         patch("httpx.get", return_value=fake_response) as mock_get:
        mock_settings.return_value.hunter_api_key = "fake-hunter-key"
        result = verify_email("jane@acme.com")

    assert result == {"email": "jane@acme.com", "status": "valid", "score": 97, "queried": True, "reason": None}
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["headers"]["X-API-KEY"] == "fake-hunter-key"
    assert call_kwargs["params"] == {"email": "jane@acme.com"}


def test_verify_email_returns_graceful_shape_when_key_unset():
    with patch("app.mcp_tools.hunter_server.get_settings") as mock_settings:
        mock_settings.return_value.hunter_api_key = ""
        result = verify_email("jane@acme.com")

    assert result == {
        "email": "jane@acme.com",
        "status": None,
        "score": None,
        "queried": False,
        "reason": "HUNTER_API_KEY is not configured",
    }


def test_verify_email_rejects_blank_email_without_calling_hunter():
    with patch("httpx.get") as mock_get:
        result = verify_email("   ")

    mock_get.assert_not_called()
    assert result["queried"] is False
    assert "reason" in result


def test_verify_email_rejects_malformed_email_without_calling_hunter():
    with patch("httpx.get") as mock_get:
        result = verify_email("not-an-email")

    mock_get.assert_not_called()
    assert result["queried"] is False
    assert "missing" in result["reason"].lower()


def test_verify_email_rejects_oversized_email_without_calling_hunter():
    with patch("httpx.get") as mock_get:
        result = verify_email(("a" * 195) + "@x.com")

    mock_get.assert_not_called()
    assert result["queried"] is False


def test_verify_email_returns_graceful_shape_on_transport_failure():
    with patch("app.mcp_tools.hunter_server.get_settings") as mock_settings, \
         patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
        mock_settings.return_value.hunter_api_key = "fake-hunter-key"
        result = verify_email("jane@acme.com")

    assert result["queried"] is False
    assert result["status"] is None


def test_verify_email_returns_graceful_shape_on_timeout():
    with patch("app.mcp_tools.hunter_server.get_settings") as mock_settings, \
         patch("httpx.get", side_effect=httpx.TimeoutException("timed out")):
        mock_settings.return_value.hunter_api_key = "fake-hunter-key"
        result = verify_email("jane@acme.com")

    assert result["queried"] is False


def test_verify_email_returns_graceful_shape_on_http_error_status():
    fake_response = MagicMock()
    fake_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error", request=MagicMock(), response=fake_response
    )
    with patch("app.mcp_tools.hunter_server.get_settings") as mock_settings, \
         patch("httpx.get", return_value=fake_response):
        mock_settings.return_value.hunter_api_key = "fake-hunter-key"
        result = verify_email("jane@acme.com")

    assert result["queried"] is False


def test_verify_email_returns_graceful_shape_on_malformed_response_shape():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"data": "not-an-object"}
    with patch("app.mcp_tools.hunter_server.get_settings") as mock_settings, \
         patch("httpx.get", return_value=fake_response):
        mock_settings.return_value.hunter_api_key = "fake-hunter-key"
        result = verify_email("jane@acme.com")

    assert result["queried"] is False
