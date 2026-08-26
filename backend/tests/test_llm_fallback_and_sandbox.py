"""Tests for the Gemini-to-Hugging-Face fallback and Daytona sandbox scoring,
with each external transport mocked at its own boundary."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.agents.common import AgentPausedForToolApproval, run_agent_reasoning
from app.core.llm import LLMError, generate_json
from app.core.sandbox import run_signal_scoring
from app.core.trueforge import PendingToolApproval, TrueForgeError, ensure_agent, resume_turn, run_turn

SCHEMA = {
    "type": "object",
    "properties": {"stage": {"type": "string"}, "confidence": {"type": "number"}},
    "required": ["stage", "confidence"],
}


def test_generate_json_uses_gemini_when_it_succeeds():
    with patch("app.core.llm._call_gemini_json", return_value={"stage": "mid", "confidence": 0.8}) as gemini, \
         patch("app.core.llm._call_hf_fallback_json") as hf:
        result = generate_json(system_instruction="x", prompt="y", response_schema=SCHEMA)
    assert result == {"stage": "mid", "confidence": 0.8}
    gemini.assert_called_once()
    hf.assert_not_called()


def test_generate_json_falls_back_to_hf_when_gemini_fails():
    with patch("app.core.llm._call_gemini_json", side_effect=LLMError("quota exceeded")), \
         patch("app.core.llm._call_hf_fallback_json", return_value={"stage": "late", "confidence": 0.6}) as hf:
        result = generate_json(system_instruction="x", prompt="y", response_schema=SCHEMA)
    assert result == {"stage": "late", "confidence": 0.6}
    hf.assert_called_once()


def test_generate_json_raises_when_both_providers_fail():
    with patch("app.core.llm._call_gemini_json", side_effect=LLMError("gemini down")), \
         patch("app.core.llm._call_hf_fallback_json", side_effect=LLMError("hf down")):
        with pytest.raises(LLMError, match="All LLM providers failed"):
            generate_json(system_instruction="x", prompt="y", response_schema=SCHEMA)


def test_hf_fallback_parses_tool_call_arguments():
    from app.core.llm import _call_hf_fallback_json

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"function": {"arguments": '{"stage": "early", "confidence": 0.3}'}}
                    ]
                }
            }
        ]
    }
    with patch("app.core.llm.get_settings") as mock_settings, patch("httpx.post", return_value=fake_response):
        mock_settings.return_value.hf_token = "fake-token"
        mock_settings.return_value.hf_model = "fake-model"
        result = _call_hf_fallback_json("x", "y", SCHEMA, 0.3)
    assert result == {"stage": "early", "confidence": 0.3}


def test_hf_fallback_raises_llm_error_on_transport_failure():
    from app.core.llm import _call_hf_fallback_json

    with patch("app.core.llm.get_settings") as mock_settings, \
         patch("httpx.post", side_effect=httpx.ConnectError("connection refused")):
        mock_settings.return_value.hf_token = "fake-token"
        mock_settings.return_value.hf_model = "fake-model"
        with pytest.raises(LLMError, match="Hugging Face fallback call failed"):
            _call_hf_fallback_json("x", "y", SCHEMA, 0.3)


def test_hf_fallback_wraps_json_decode_error_as_llm_error():
    """Regression test: a malformed response body (e.g. an HTML error page
    served with a 200 status) must surface as LLMError, not an unhandled
    JSONDecodeError, so generate_json's fallback handling is never bypassed."""
    from app.core.llm import _call_hf_fallback_json

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.side_effect = json.JSONDecodeError("bad json", "doc", 0)
    with patch("app.core.llm.get_settings") as mock_settings, patch("httpx.post", return_value=fake_response):
        mock_settings.return_value.hf_token = "fake-token"
        mock_settings.return_value.hf_model = "fake-model"
        with pytest.raises(LLMError, match="non-JSON response body"):
            _call_hf_fallback_json("x", "y", SCHEMA, 0.3)


def test_sandbox_script_safely_escapes_malicious_signal_content():
    """Regression test: signal field content containing a triple-quote
    sequence must not be able to break out of the generated script's string
    literal and alter what code runs in the sandbox."""
    from app.core.sandbox import _build_scoring_script

    malicious_rows = [
        {"intent_stage_hint": "late''' ; import os; os.system('echo pwned') ; x = '''", "days_ago": 1}
    ]
    script = _build_scoring_script(malicious_rows)
    compile(script, "<test>", "exec")  # raises SyntaxError if the literal was broken out of
    # The malicious payload must remain a plain string literal, not become
    # executable Python source.
    assert "rows = json.loads('" in script or 'rows = json.loads("' in script


def test_signal_scoring_uses_daytona_when_available():
    fake_execution = MagicMock()
    fake_execution.stdout = '{"weighted_score": 2.5, "signal_count": 1}\n'
    fake_execution.exit_code = 0
    fake_sandbox = MagicMock()
    fake_sandbox.code_interpreter.run_code.return_value = fake_execution

    with patch("app.core.sandbox._get_sandbox", return_value=fake_sandbox):
        result, path = run_signal_scoring([{"intent_stage_hint": "late", "days_ago": 1}])
    assert path == "daytona"
    assert result["weighted_score"] == 2.5


def test_signal_scoring_falls_back_to_local_when_sandbox_unavailable():
    with patch("app.core.sandbox._get_sandbox", side_effect=RuntimeError("sandbox unreachable")):
        result, path = run_signal_scoring([{"intent_stage_hint": "early", "days_ago": 10}])
    assert path == "local"
    assert result["signal_count"] == 1
    assert result["weighted_score"] > 0


def test_ensure_agent_treats_already_exists_conflict_as_success():
    fake_post_response = MagicMock()
    fake_post_response.status_code = 409
    fake_post_response.text = '{"error":{"message":"Agent name already exists: signalis-persona-fit"}}'

    fake_get_response = MagicMock()
    fake_get_response.json.return_value = {
        "data": [{"id": "agent-123", "name": "signalis-persona-fit"}]
    }

    fake_put_response = MagicMock()

    with patch("httpx.post", return_value=fake_post_response), \
        patch("httpx.get", return_value=fake_get_response), \
        patch("httpx.put", return_value=fake_put_response) as mock_put:
        ensure_agent("signalis-persona-fit", model="google-gemini/gemini-2-5-flash", instructions="x")

    # The already-exists path must PUT-update the manifest (not silently
    # no-op), since config like require_approval_for_tools or a newly
    # attached MCP server has to take effect on an agent that was already
    # registered by an earlier run.
    mock_put.assert_called_once()
    assert mock_put.call_args.args[0] == "http://localhost:8790/api/v1/agents/agent-123"

def test_ensure_agent_raises_on_genuine_error():
    fake_response = MagicMock()
    fake_response.status_code = 500
    fake_response.text = '{"error":{"message":"internal error"}}'
    fake_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error", request=MagicMock(), response=fake_response
    )
    with patch("httpx.post", return_value=fake_response):
        with pytest.raises(TrueForgeError):
            ensure_agent("some-agent", model="google-gemini/gemini-2-5-flash", instructions="x")


def test_run_agent_reasoning_uses_trueforge_when_available():
    with patch("app.agents.common.get_settings") as mock_settings, \
         patch("app.agents.common.ensure_agent") as mock_ensure, \
         patch(
             "app.agents.common.run_turn",
             return_value=({"stage": "late", "confidence": 0.9}, "session-abc123"),
         ) as mock_turn:
        mock_settings.return_value.trueforge_enabled = True
        result, session_id = run_agent_reasoning(
            trueforge_agent_name="signalis-buying-stage-orchestrator",
            model="google-gemini/gemini-2-5-flash",
            system_instruction="reason about stage",
            prompt="lead data",
            response_schema=SCHEMA,
        )
    assert result == {"stage": "late", "confidence": 0.9}
    assert session_id == "session-abc123"
    mock_ensure.assert_called_once()
    mock_turn.assert_called_once()


def test_ensure_agent_updates_manifest_when_agent_already_exists():
    """Regression test for the PUT-update fix: previously ensure_agent
    treated 409-already-exists as a no-op, so config changes (like
    require_approval_for_tools) never took effect on an already-registered
    agent. It must now look up the agent id and PUT the new manifest."""
    create_conflict = MagicMock()
    create_conflict.status_code = 409
    create_conflict.text = '{"error":{"message":"Agent name already exists: signalis-persona-fit"}}'

    list_response = MagicMock()
    list_response.raise_for_status = MagicMock()
    list_response.json.return_value = {"data": [{"id": "agent-123", "name": "signalis-persona-fit"}]}

    put_response = MagicMock()
    put_response.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=create_conflict), \
         patch("httpx.get", return_value=list_response) as mock_get, \
         patch("httpx.put", return_value=put_response) as mock_put:
        ensure_agent(
            "signalis-persona-fit",
            model="google-gemini/gemini-2-5-flash",
            instructions="x",
            mcp_servers=[{"name": "signalis-enrichment", "require_approval_for_tools": ["classify_company_industry"]}],
        )

    mock_get.assert_called_once()
    mock_put.assert_called_once()
    put_url, put_kwargs = mock_put.call_args
    assert put_url[0] == "http://localhost:8790/api/v1/agents/agent-123"
    assert put_kwargs["json"]["manifest"]["mcp_servers"][0]["require_approval_for_tools"] == [
        "classify_company_industry"
    ]


def test_update_agent_wraps_malformed_json_as_trueforge_error():
    """Regression test for "Malformed responses bypass fallback": a
    non-list `data` value (or invalid JSON) from GET /api/v1/agents used to
    escape _update_agent as a raw TypeError/JSONDecodeError, which
    run_agent_reasoning's `except TrueForgeError` fallback boundary doesn't
    catch — turning a recoverable dependency response failure into an
    unhandled 500 instead of falling back to the direct LLM call."""
    create_conflict = MagicMock()
    create_conflict.status_code = 409
    create_conflict.text = '{"error":{"message":"Agent name already exists: some-agent"}}'

    malformed_list_response = MagicMock()
    malformed_list_response.raise_for_status = MagicMock()
    malformed_list_response.json.return_value = {"data": "not-a-list"}

    with patch("httpx.post", return_value=create_conflict), \
         patch("httpx.get", return_value=malformed_list_response):
        with pytest.raises(TrueForgeError):
            ensure_agent("some-agent", model="google-gemini/gemini-2-5-flash", instructions="x")


def test_update_agent_wraps_invalid_json_body_as_trueforge_error():
    create_conflict = MagicMock()
    create_conflict.status_code = 409
    create_conflict.text = '{"error":{"message":"Agent name already exists: some-agent"}}'

    invalid_json_response = MagicMock()
    invalid_json_response.raise_for_status = MagicMock()
    invalid_json_response.json.side_effect = json.JSONDecodeError("bad json", "doc", 0)

    with patch("httpx.post", return_value=create_conflict), \
         patch("httpx.get", return_value=invalid_json_response):
        with pytest.raises(TrueForgeError):
            ensure_agent("some-agent", model="google-gemini/gemini-2-5-flash", instructions="x")


def test_run_agent_reasoning_falls_back_when_update_agent_returns_malformed_json():
    """The end-to-end version of the two tests above: run_agent_reasoning's
    fallback boundary (except TrueForgeError) must actually trigger for a
    malformed dependency response, not just the low-level helper raising the
    right exception type in isolation."""
    create_conflict = MagicMock()
    create_conflict.status_code = 409
    create_conflict.text = '{"error":{"message":"Agent name already exists: some-agent"}}'

    malformed_list_response = MagicMock()
    malformed_list_response.raise_for_status = MagicMock()
    malformed_list_response.json.return_value = {"data": None}

    with patch("app.agents.common.get_settings") as mock_settings, \
         patch("httpx.post", return_value=create_conflict), \
         patch("httpx.get", return_value=malformed_list_response), \
         patch("app.core.llm._call_gemini_json", return_value={"stage": "mid", "confidence": 0.5}) as gemini:
        mock_settings.return_value.trueforge_enabled = True
        result, session_id = run_agent_reasoning(
            trueforge_agent_name="some-agent",
            model="google-gemini/gemini-2-5-flash",
            system_instruction="reason",
            prompt="data",
            response_schema=SCHEMA,
        )
    assert result == {"stage": "mid", "confidence": 0.5}
    assert session_id is None
    gemini.assert_called_once()


def test_poll_turn_wraps_malformed_state_as_trueforge_error():
    from app.core.trueforge import _poll_turn_to_done

    malformed_poll = MagicMock()
    malformed_poll.raise_for_status = MagicMock()
    malformed_poll.json.return_value = {"data": {"state": "not-a-dict"}}

    with patch("httpx.get", return_value=malformed_poll):
        with pytest.raises(TrueForgeError):
            _poll_turn_to_done("some-agent", "sess-1", "turn-1")


def test_resolve_turn_result_wraps_malformed_required_actions_as_trueforge_error():
    from app.core.trueforge import _resolve_turn_result

    # `tool_calls` inside a required_actions entry missing the expected
    # "id" key used to raise a raw KeyError out of _extract_pending_approvals.
    state = {
        "status": "done",
        "required_actions": [{"type": "tool.approval_required", "tool_calls": [{"not_id": "x"}]}],
        "output": {"tool_calls": []},
    }
    with pytest.raises(TrueForgeError):
        _resolve_turn_result("some-agent", "sess-1", "turn-1", state)


def test_run_turn_returns_pending_approvals_when_turn_pauses():
    """Regression test for the dead "requires_action" check: TrueForge
    actually reports status == "done" with a populated required_actions
    list when a turn pauses on a tool-approval gate, not a separate
    "requires_action" status. run_turn must detect that shape and return
    PendingToolApproval objects instead of raising or mis-parsing."""
    session_resp = MagicMock()
    session_resp.raise_for_status = MagicMock()
    session_resp.json.return_value = {"data": {"id": "sess-1"}}

    turn_create_resp = MagicMock()
    turn_create_resp.raise_for_status = MagicMock()
    turn_create_resp.json.return_value = {"data": {"id": "turn-1"}}

    poll_resp = MagicMock()
    poll_resp.raise_for_status = MagicMock()
    poll_resp.json.return_value = {
        "data": {
            "state": {
                "status": "done",
                "output": {
                    "type": "model.message",
                    "thread_id": "main",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "classify_company_industry",
                                "arguments": '{"company_name": "Acme Corp"}',
                            },
                        }
                    ],
                },
                "required_actions": [
                    {
                        "type": "tool.approval_required",
                        "thread_id": "main",
                        "tool_calls": [{"id": "call-1", "source_event_id": "evt-1"}],
                    }
                ],
                "completed_at": "2026-01-01T00:00:00Z",
            }
        }
    }

    with patch("httpx.post", side_effect=[session_resp, turn_create_resp]), \
         patch("httpx.get", return_value=poll_resp):
        result, session_id = run_turn("signalis-persona-fit", "assess fit")

    assert session_id == "sess-1"
    assert isinstance(result, list)
    assert len(result) == 1
    pending = result[0]
    assert isinstance(pending, PendingToolApproval)
    assert pending.session_id == "sess-1"
    assert pending.turn_id == "turn-1"
    assert pending.thread_id == "main"
    assert pending.tool_call_id == "call-1"
    assert pending.tool_name == "classify_company_industry"
    assert pending.tool_input == {"company_name": "Acme Corp"}


def test_run_turn_returns_final_answer_when_no_pause():
    session_resp = MagicMock()
    session_resp.raise_for_status = MagicMock()
    session_resp.json.return_value = {"data": {"id": "sess-2"}}

    turn_create_resp = MagicMock()
    turn_create_resp.raise_for_status = MagicMock()
    turn_create_resp.json.return_value = {"data": {"id": "turn-2"}}

    poll_resp = MagicMock()
    poll_resp.raise_for_status = MagicMock()
    poll_resp.json.return_value = {
        "data": {
            "state": {
                "status": "done",
                "output": {"content": '{"fit": "full_fit", "reasoning": "ok", "missing_data": []}'},
                "required_actions": [],
                "completed_at": "2026-01-01T00:00:00Z",
            }
        }
    }

    with patch("httpx.post", side_effect=[session_resp, turn_create_resp]), \
         patch("httpx.get", return_value=poll_resp):
        result, session_id = run_turn("signalis-persona-fit", "assess fit")

    assert result == {"fit": "full_fit", "reasoning": "ok", "missing_data": []}
    assert session_id == "sess-2"


def test_resume_turn_posts_tool_approval_and_returns_final_answer():
    """Regression test for the resume payload shape: POST a new turn on the
    same session with previous_turn_id: "auto" and a user.tool_approval
    input item carrying thread_id/tool_call_id/approval — verified against
    TrueForge's live openapi.json schema (UserToolApprovalEvent)."""
    turn_resp = MagicMock()
    turn_resp.raise_for_status = MagicMock()
    turn_resp.json.return_value = {"data": {"id": "turn-3"}}

    poll_resp = MagicMock()
    poll_resp.raise_for_status = MagicMock()
    poll_resp.json.return_value = {
        "data": {
            "state": {
                "status": "done",
                "output": {"content": '{"fit": "partial_fit", "reasoning": "resumed", "missing_data": []}'},
                "required_actions": [],
                "completed_at": "2026-01-01T00:00:00Z",
            }
        }
    }

    with patch("httpx.post", return_value=turn_resp) as mock_post, patch("httpx.get", return_value=poll_resp):
        result = resume_turn(
            "signalis-persona-fit",
            session_id="sess-1",
            thread_id="main",
            tool_call_id="call-1",
            approve=True,
        )

    assert result == {"fit": "partial_fit", "reasoning": "resumed", "missing_data": []}
    _, kwargs = mock_post.call_args
    payload = kwargs["json"]
    assert payload["previous_turn_id"] == "auto"
    assert payload["input"] == [
        {
            "type": "user.tool_approval",
            "thread_id": "main",
            "tool_call_id": "call-1",
            "approval": {"status": "allow"},
        }
    ]


def test_resume_turn_denies_with_reason():
    turn_resp = MagicMock()
    turn_resp.raise_for_status = MagicMock()
    turn_resp.json.return_value = {"data": {"id": "turn-4"}}

    poll_resp = MagicMock()
    poll_resp.raise_for_status = MagicMock()
    poll_resp.json.return_value = {
        "data": {
            "state": {
                "status": "done",
                "output": {"content": '{"fit": "mismatch", "reasoning": "denied", "missing_data": []}'},
                "required_actions": [],
                "completed_at": "2026-01-01T00:00:00Z",
            }
        }
    }

    with patch("httpx.post", return_value=turn_resp) as mock_post, patch("httpx.get", return_value=poll_resp):
        resume_turn(
            "signalis-persona-fit",
            session_id="sess-1",
            thread_id="main",
            tool_call_id="call-1",
            approve=False,
            deny_reason="not needed",
        )

    _, kwargs = mock_post.call_args
    assert kwargs["json"]["input"][0]["approval"] == {"status": "deny", "reason": "not needed"}


def test_run_agent_reasoning_raises_agent_paused_when_turn_pauses():
    pending = [
        PendingToolApproval(
            session_id="sess-1",
            turn_id="turn-1",
            thread_id="main",
            tool_call_id="call-1",
            tool_name="classify_company_industry",
            tool_input={"company_name": "Acme"},
        )
    ]
    with patch("app.agents.common.get_settings") as mock_settings, \
         patch("app.agents.common.ensure_agent"), \
         patch("app.agents.common.run_turn", return_value=(pending, "sess-1")):
        mock_settings.return_value.trueforge_enabled = True
        with pytest.raises(AgentPausedForToolApproval) as exc_info:
            run_agent_reasoning(
                trueforge_agent_name="signalis-persona-fit",
                model="google-gemini/gemini-2-5-flash",
                system_instruction="assess fit",
                prompt="lead data",
                response_schema=SCHEMA,
                mcp_servers=[{"name": "signalis-enrichment"}],
            )
    assert exc_info.value.pending == pending


def test_run_agent_reasoning_falls_back_and_strips_tool_references_when_mcp_configured():
    """Regression test: when TrueForge is unreachable, the direct fallback call
    has no tool access, so any tool-referencing system instruction must be
    neutralized — otherwise the model tries to invoke tools that don't exist
    in that call and answers incoherently instead of following the schema."""
    captured = {}

    def fake_generate_json(*, system_instruction, prompt, response_schema, temperature):
        captured["system_instruction"] = system_instruction
        return {"fit": "full_fit", "reasoning": "matches", "missing_data": []}

    with patch("app.agents.common.get_settings") as mock_settings, \
         patch("app.agents.common.ensure_agent", side_effect=TrueForgeError("unreachable")), \
         patch("app.agents.common.generate_json", side_effect=fake_generate_json):
        mock_settings.return_value.trueforge_enabled = True
        result, session_id = run_agent_reasoning(
            trueforge_agent_name="signalis-persona-fit",
            model="google-gemini/gemini-2-5-flash",
            system_instruction="Use the classify_company_industry tool to enrich the lead.",
            prompt="lead data",
            response_schema=SCHEMA,
            mcp_servers=[{"name": "signalis-enrichment"}],
        )
    assert result == {"fit": "full_fit", "reasoning": "matches", "missing_data": []}
    assert session_id is None
    assert "No external tools are available" in captured["system_instruction"]
