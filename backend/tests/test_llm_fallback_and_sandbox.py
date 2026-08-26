"""Tests for the Gemini-to-Hugging-Face fallback and Daytona sandbox scoring,
with each external transport mocked at its own boundary."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.agents.common import run_agent_reasoning, run_agent_reasoning_with_delegations
from app.core.llm import LLMError, generate_json
from app.core.sandbox import run_signal_scoring
from app.core.trueforge import TrueForgeError, _extract_subagent_delegations, ensure_agent

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
    fake_response = MagicMock()
    fake_response.status_code = 409
    fake_response.text = '{"error":{"message":"Agent name already exists: signalis-persona-fit"}}'
    with patch("httpx.post", return_value=fake_response):
        ensure_agent("signalis-persona-fit", model="google-gemini/gemini-2-5-flash", instructions="x")


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
         patch("app.agents.common.run_turn", return_value={"stage": "late", "confidence": 0.9}) as mock_turn:
        mock_settings.return_value.trueforge_enabled = True
        result = run_agent_reasoning(
            trueforge_agent_name="signalis-buying-stage-orchestrator",
            model="google-gemini/gemini-2-5-flash",
            system_instruction="reason about stage",
            prompt="lead data",
            response_schema=SCHEMA,
        )
    assert result == {"stage": "late", "confidence": 0.9}
    mock_ensure.assert_called_once()
    mock_turn.assert_called_once()


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
        result = run_agent_reasoning(
            trueforge_agent_name="signalis-persona-fit",
            model="google-gemini/gemini-2-5-flash",
            system_instruction="Use the classify_company_industry tool to enrich the lead.",
            prompt="lead data",
            response_schema=SCHEMA,
            mcp_servers=[{"name": "signalis-enrichment"}],
        )
    assert result == {"fit": "full_fit", "reasoning": "matches", "missing_data": []}
    assert "No external tools are available" in captured["system_instruction"]


def test_extract_subagent_delegations_pairs_created_and_done_events():
    """Regression coverage for the pairing logic that turns TrueForge's raw
    thread.created/thread.done session events into the per-subagent evidence
    persisted on a Prioritization run's output. Verified against the exact
    event shape observed live against the running TrueForge harness (a
    3-subagent smoke test: session events included thread.created events with
    agent_info.name/input and later thread.done events with the same
    thread_id and a state.status)."""
    events = [
        {
            "event": {
                "type": "thread.created",
                "thread_id": "t1",
                "agent_info": {"type": "dynamic", "name": "subagent_lead_a", "input": "assess lead a"},
                "created_at": "2026-08-26T08:03:45.501Z",
            }
        },
        {
            "event": {
                "type": "thread.done",
                "thread_id": "t1",
                "created_at": "2026-08-26T08:03:47.449Z",
                "state": {"status": "done", "output": {"content": "..."}},
            }
        },
        # An unrelated turn.done event (no thread_id) must be ignored, not
        # mistaken for a subagent event.
        {"event": {"type": "turn.done", "thread_id": None}},
    ]
    delegations = _extract_subagent_delegations(events)
    assert len(delegations) == 1
    assert delegations[0]["thread_id"] == "t1"
    assert delegations[0]["name"] == "subagent_lead_a"
    assert delegations[0]["status"] == "done"
    assert delegations[0]["latency_seconds"] == pytest.approx(1.948, abs=0.01)


def test_extract_subagent_delegations_handles_incomplete_thread():
    """A thread.created with no matching thread.done yet (e.g. events fetched
    mid-flight) must not raise — it should be reported as incomplete rather
    than silently dropped, since that's real information about the run."""
    events = [
        {
            "event": {
                "type": "thread.created",
                "thread_id": "t2",
                "agent_info": {"type": "dynamic", "name": "subagent_lead_b", "input": "assess lead b"},
                "created_at": "2026-08-26T08:03:45.501Z",
            }
        }
    ]
    delegations = _extract_subagent_delegations(events)
    assert len(delegations) == 1
    assert delegations[0]["status"] == "incomplete"
    assert "latency_seconds" not in delegations[0]


def test_run_agent_reasoning_with_delegations_returns_real_subagent_evidence():
    """When TrueForge genuinely delegated (create_sub_agent calls recorded on
    the session), run_agent_reasoning_with_delegations must surface that
    evidence alongside the parsed output, not just the final ranking."""
    fake_delegations = [{"thread_id": "t1", "name": "subagent_lead_a", "status": "done", "latency_seconds": 1.2}]
    with patch("app.agents.common.get_settings") as mock_settings, \
         patch("app.agents.common.ensure_agent") as mock_ensure, \
         patch(
             "app.agents.common.run_turn",
             return_value=({"ranking": [], "summary": "ok"}, fake_delegations),
         ) as mock_turn:
        mock_settings.return_value.trueforge_enabled = True
        output, delegations = run_agent_reasoning_with_delegations(
            trueforge_agent_name="signalis-prioritization",
            model="google-gemini/gemini-2-5-flash",
            system_instruction="rank leads, delegating per-lead assessment to subagents",
            prompt="lead data",
            response_schema=SCHEMA,
        )
    assert output == {"ranking": [], "summary": "ok"}
    assert delegations == fake_delegations
    mock_ensure.assert_called_once()
    mock_turn.assert_called_once_with(
        "signalis-prioritization", "lead data", with_delegations=True
    )


def test_run_agent_reasoning_with_delegations_falls_back_with_no_subagents():
    """The direct Gemini/HF fallback path has no subagent capability, so
    falling back must report an empty, honest delegation list rather than
    fabricating evidence of delegation that never happened."""
    with patch("app.agents.common.get_settings") as mock_settings, \
         patch("app.agents.common.ensure_agent", side_effect=TrueForgeError("unreachable")), \
         patch("app.agents.common.generate_json", return_value={"ranking": [], "summary": "fallback"}):
        mock_settings.return_value.trueforge_enabled = True
        output, delegations = run_agent_reasoning_with_delegations(
            trueforge_agent_name="signalis-prioritization",
            model="google-gemini/gemini-2-5-flash",
            system_instruction="rank leads",
            prompt="lead data",
            response_schema=SCHEMA,
        )
    assert output == {"ranking": [], "summary": "fallback"}
    assert delegations == []
