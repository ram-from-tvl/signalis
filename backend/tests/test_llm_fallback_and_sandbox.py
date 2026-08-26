"""Tests for the Gemini-to-Hugging-Face fallback and Daytona sandbox scoring,
with each external transport mocked at its own boundary."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.agents.common import (
    AgentPausedForToolApproval,
    run_agent_reasoning,
    run_agent_reasoning_with_delegations,
)
from app.core.llm import LLMError, generate_json
from app.core.sandbox import run_signal_scoring
from app.core.trueforge import (
    PendingToolApproval,
    TrueForgeError,
    _extract_subagent_delegations,
    _get_session_events,
    ensure_agent,
    ensure_skill,
    resume_turn,
    run_turn,
)

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
    fake_get_response.raise_for_status = MagicMock()
    fake_get_response.json.return_value = {
        "data": [{"id": "agent-123", "name": "signalis-persona-fit", "manifest": {}}]
    }

    fake_put_response = MagicMock()
    fake_put_response.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=fake_post_response), \
         patch("httpx.get", return_value=fake_get_response), \
         patch("httpx.put", return_value=fake_put_response) as mock_put:
        ensure_agent("signalis-persona-fit", model="google-gemini/gemini-2-5-flash", instructions="x")

    # The already-exists path must PUT-update the manifest (not silently
    # no-op), since config like require_approval_for_tools, a newly attached
    # MCP server, or this PR's skills attachment has to take effect on an
    # agent that was already registered by an earlier run.
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


def test_ensure_skill_treats_already_exists_conflict_as_success():
    fake_response = MagicMock()
    fake_response.status_code = 409
    fake_response.text = '{"error":{"message":"Skill name already exists: outreach-copywriting-style-guide"}}'
    with patch("httpx.post", return_value=fake_response):
        ensure_skill(
            "outreach-copywriting-style-guide",
            repo_url="https://github.com/ram-from-tvl/signalis",
            path="backend/app/agents/skills/outreach_copywriting_style_guide",
            ref="main",
            description="test",
        )


def test_ensure_skill_raises_on_genuine_error():
    fake_response = MagicMock()
    fake_response.status_code = 500
    fake_response.text = '{"error":{"message":"internal error"}}'
    fake_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error", request=MagicMock(), response=fake_response
    )
    with patch("httpx.post", return_value=fake_response):
        with pytest.raises(TrueForgeError):
            ensure_skill(
                "some-skill",
                repo_url="https://github.com/ram-from-tvl/signalis",
                path="some/path",
                ref="main",
                description="test",
            )


def test_ensure_skill_posts_git_backed_manifest():
    """Regression test locking in the real TrueForge skill contract, verified
    against the live harness's OpenAPI schema: a skill is a `type: git`
    manifest with a GitHub/GitLab URL, path, ref, and description — never
    raw content posted inline."""
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        resp = MagicMock()
        resp.status_code = 201
        return resp

    with patch("httpx.post", side_effect=fake_post):
        ensure_skill(
            "outreach-copywriting-style-guide",
            repo_url="https://github.com/ram-from-tvl/signalis",
            path="backend/app/agents/skills/outreach_copywriting_style_guide",
            ref="main",
            description="craft guidance",
        )

    assert captured["url"].endswith("/api/v1/settings/skills")
    manifest = captured["json"]["manifest"]
    assert manifest["type"] == "git"
    assert manifest["name"] == "outreach-copywriting-style-guide"
    assert manifest["url"] == "https://github.com/ram-from-tvl/signalis"
    assert manifest["path"] == "backend/app/agents/skills/outreach_copywriting_style_guide"
    assert manifest["ref"] == "main"
    assert manifest["description"] == "craft guidance"


def test_ensure_agent_updates_manifest_with_skills_when_agent_already_exists():
    """Regression test for Finding 1 (Qodo, PR #11): an agent that was
    already registered by an earlier run (e.g. this repo's own local
    TrueForge instance, which already has signalis-outreach-planner
    registered *without* skills from prior testing) must actually receive
    this PR's skills attachment and sandbox.enabled=true change on the next
    ensure_agent call — not silently keep running with its old, pre-skill
    manifest because the create-time POST 409'd and was previously treated
    as unconditional success."""
    create_conflict = MagicMock()
    create_conflict.status_code = 409
    create_conflict.text = '{"error":{"message":"Agent name already exists: signalis-outreach-planner"}}'

    # The pre-existing agent, as it would be found on a TrueForge instance
    # that registered it before this PR's skill attachment landed: no
    # `skills` key, sandbox disabled.
    list_response = MagicMock()
    list_response.raise_for_status = MagicMock()
    list_response.json.return_value = {
        "data": [
            {
                "id": "agent-outreach-1",
                "name": "signalis-outreach-planner",
                "manifest": {
                    "model": {"name": "google-gemini/gemini-2-5-flash"},
                    "instructions": "old pre-skill instructions",
                    "config": {"sandbox": {"enabled": False}},
                },
            }
        ]
    }

    put_response = MagicMock()
    put_response.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=create_conflict), \
         patch("httpx.get", return_value=list_response) as mock_get, \
         patch("httpx.put", return_value=put_response) as mock_put:
        ensure_agent(
            "signalis-outreach-planner",
            model="google-gemini/gemini-2-5-flash",
            instructions="new instructions mentioning the skill",
            skills=[{"name": "outreach-copywriting-style-guide"}],
        )

    mock_get.assert_called_once()
    mock_put.assert_called_once()
    put_url, put_kwargs = mock_put.call_args
    assert put_url[0] == "http://localhost:8790/api/v1/agents/agent-outreach-1"
    put_manifest = put_kwargs["json"]["manifest"]
    # The whole point of the fix: the PUT payload must actually carry the
    # skills list and enabled sandbox this time, not the old skill-less
    # manifest shape.
    assert put_manifest["skills"] == [{"name": "outreach-copywriting-style-guide"}]
    assert put_manifest["config"]["sandbox"]["enabled"] is True
    assert put_manifest["instructions"] == "new instructions mentioning the skill"


def test_ensure_agent_preserves_existing_skills_when_caller_has_no_opinion():
    """Regression test for the related risk Qodo flagged: if a later call's
    skill registration fails (skills=None passed to ensure_agent, not an
    explicit skills=[]), updating an already-skill-attached agent must NOT
    clobber its existing skills/sandbox config with a skill-less manifest —
    otherwise a single transient registration hiccup would permanently
    regress a previously-working, skill-attached agent."""
    create_conflict = MagicMock()
    create_conflict.status_code = 409
    create_conflict.text = '{"error":{"message":"Agent name already exists: signalis-outreach-planner"}}'

    # This agent already has the skill attached from a prior, successful call.
    list_response = MagicMock()
    list_response.raise_for_status = MagicMock()
    list_response.json.return_value = {
        "data": [
            {
                "id": "agent-outreach-1",
                "name": "signalis-outreach-planner",
                "manifest": {
                    "model": {"name": "google-gemini/gemini-2-5-flash"},
                    "instructions": "old instructions",
                    "config": {"sandbox": {"enabled": True}},
                    "skills": [{"name": "outreach-copywriting-style-guide"}],
                },
            }
        ]
    }

    put_response = MagicMock()
    put_response.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=create_conflict), \
         patch("httpx.get", return_value=list_response), \
         patch("httpx.put", return_value=put_response) as mock_put:
        # skills=None: this call's skill registration failed upstream, so
        # the caller has no opinion — it must not imply "remove skills".
        ensure_agent(
            "signalis-outreach-planner",
            model="google-gemini/gemini-2-5-flash",
            instructions="new instructions, skill registration failed this time",
            skills=None,
        )

    put_manifest = mock_put.call_args.kwargs["json"]["manifest"]
    assert put_manifest["skills"] == [{"name": "outreach-copywriting-style-guide"}]
    assert put_manifest["config"]["sandbox"]["enabled"] is True


def test_ensure_agent_explicit_empty_skills_clears_existing_skills():
    """Contrast case: an explicit skills=[] (caller genuinely wants no
    skills) must still be honored on update, distinguishing it from the
    skills=None 'no opinion, preserve existing' case above."""
    create_conflict = MagicMock()
    create_conflict.status_code = 409
    create_conflict.text = '{"error":{"message":"Agent name already exists: signalis-outreach-planner"}}'

    list_response = MagicMock()
    list_response.raise_for_status = MagicMock()
    list_response.json.return_value = {
        "data": [
            {
                "id": "agent-outreach-1",
                "name": "signalis-outreach-planner",
                "manifest": {
                    "model": {"name": "google-gemini/gemini-2-5-flash"},
                    "instructions": "old instructions",
                    "config": {"sandbox": {"enabled": True}},
                    "skills": [{"name": "outreach-copywriting-style-guide"}],
                },
            }
        ]
    }

    put_response = MagicMock()
    put_response.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=create_conflict), \
         patch("httpx.get", return_value=list_response), \
         patch("httpx.put", return_value=put_response) as mock_put:
        ensure_agent(
            "signalis-outreach-planner",
            model="google-gemini/gemini-2-5-flash",
            instructions="explicitly no skills",
            skills=[],
        )

    put_manifest = mock_put.call_args.kwargs["json"]["manifest"]
    assert "skills" not in put_manifest
    assert put_manifest["config"]["sandbox"]["enabled"] is False


def test_ensure_agent_wraps_malformed_list_response_as_trueforge_error():
    """Regression test for Finding 3 (Qodo, 2nd pass, PR #11): a malformed
    but 2xx response body from the list-agents endpoint that `_update_agent`
    calls on a 409 (e.g. an HTML error page or truncated body served with a
    200 status by a misbehaving proxy/dependency) must still surface as
    `TrueForgeError`, not a raw `json.JSONDecodeError` — otherwise
    `run_agent_reasoning`'s `except TrueForgeError` never triggers, the
    direct-LLM fallback never runs, and the whole outreach-planning call
    blows up instead of degrading gracefully."""
    create_conflict = MagicMock()
    create_conflict.status_code = 409
    create_conflict.text = '{"error":{"message":"Agent name already exists: signalis-outreach-planner"}}'

    malformed_list_response = MagicMock()
    malformed_list_response.raise_for_status = MagicMock()
    malformed_list_response.json.side_effect = json.JSONDecodeError("bad json", "<html>error</html>", 0)

    with patch("httpx.post", return_value=create_conflict), \
         patch("httpx.get", return_value=malformed_list_response):
        with pytest.raises(TrueForgeError):
            ensure_agent(
                "signalis-outreach-planner",
                model="google-gemini/gemini-2-5-flash",
                instructions="new instructions",
                skills=[{"name": "outreach-copywriting-style-guide"}],
            )


def test_ensure_agent_wraps_unexpected_list_shape_as_trueforge_error():
    """Companion to the malformed-JSON case above: a 2xx response that parses
    as JSON but has an unexpected `data` shape (e.g. a dict instead of a
    list of agent records) must also surface as `TrueForgeError` rather than
    a raw `TypeError` from iterating/indexing it, so it is still caught by
    `run_agent_reasoning`'s fallback handling."""
    create_conflict = MagicMock()
    create_conflict.status_code = 409
    create_conflict.text = '{"error":{"message":"Agent name already exists: signalis-outreach-planner"}}'

    bad_shape_response = MagicMock()
    bad_shape_response.raise_for_status = MagicMock()
    bad_shape_response.json.return_value = {"data": {"unexpected": "shape"}}

    with patch("httpx.post", return_value=create_conflict), \
         patch("httpx.get", return_value=bad_shape_response):
        with pytest.raises(TrueForgeError):
            ensure_agent(
                "signalis-outreach-planner",
                model="google-gemini/gemini-2-5-flash",
                instructions="new instructions",
                skills=[{"name": "outreach-copywriting-style-guide"}],
            )


def test_ensure_agent_attaches_skills_and_enables_sandbox():
    """TrueForge requires config.sandbox.enabled=true to attach skills to an
    agent (verified against the live harness's manifest schema). ensure_agent
    must flip that on whenever skills are passed, not just pass the list
    through and leave sandbox disabled."""
    captured = {}

    def fake_post(url, json, timeout):
        captured["json"] = json
        resp = MagicMock()
        resp.status_code = 201
        return resp

    with patch("httpx.post", side_effect=fake_post):
        ensure_agent(
            "signalis-outreach-planner",
            model="google-gemini/gemini-2-5-flash",
            instructions="x",
            skills=[{"name": "outreach-copywriting-style-guide"}],
        )

    manifest = captured["json"]["manifest"]
    assert manifest["skills"] == [{"name": "outreach-copywriting-style-guide"}]
    assert manifest["config"]["sandbox"]["enabled"] is True


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
        result = run_agent_reasoning(
            trueforge_agent_name="some-agent",
            model="google-gemini/gemini-2-5-flash",
            system_instruction="reason",
            prompt="data",
            response_schema=SCHEMA,
        )
    assert result == {"stage": "mid", "confidence": 0.5}
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
        result = run_turn("signalis-persona-fit", "assess fit")

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
        result = run_turn("signalis-persona-fit", "assess fit")

    assert result == {"fit": "full_fit", "reasoning": "ok", "missing_data": []}


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
         patch("app.agents.common.run_turn", return_value=pending):
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
        output, delegations, is_delegated = run_agent_reasoning_with_delegations(
            trueforge_agent_name="signalis-prioritization",
            model="google-gemini/gemini-2-5-flash",
            system_instruction="rank leads, delegating per-lead assessment to subagents",
            prompt="lead data",
            response_schema=SCHEMA,
        )
    assert output == {"ranking": [], "summary": "ok"}
    assert delegations == fake_delegations
    assert is_delegated is True
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
        output, delegations, is_delegated = run_agent_reasoning_with_delegations(
            trueforge_agent_name="signalis-prioritization",
            model="google-gemini/gemini-2-5-flash",
            system_instruction="rank leads",
            prompt="lead data",
            response_schema=SCHEMA,
        )
    assert output == {"ranking": [], "summary": "fallback"}
    assert delegations == []
    assert is_delegated is False


def test_run_agent_reasoning_with_delegations_falls_back_uses_fallback_instruction():
    """Regression test for the Qodo finding: the fallback path has no
    create_sub_agent tool, so a caller-supplied fallback_instruction (which
    should strip/replace the delegation mandate) must be what actually
    reaches generate_json on the fallback path, not the original
    tool-mandating system_instruction."""
    with patch("app.agents.common.get_settings") as mock_settings, \
         patch("app.agents.common.ensure_agent", side_effect=TrueForgeError("unreachable")), \
         patch("app.agents.common.generate_json", return_value={"ranking": [], "summary": "fallback"}) as mock_generate:
        mock_settings.return_value.trueforge_enabled = True
        run_agent_reasoning_with_delegations(
            trueforge_agent_name="signalis-prioritization",
            model="google-gemini/gemini-2-5-flash",
            system_instruction="rank leads; you MUST call create_sub_agent for every lead",
            fallback_instruction="rank leads; no subagent delegation is available, reason directly",
            prompt="lead data",
            response_schema=SCHEMA,
        )
    mock_generate.assert_called_once()
    assert mock_generate.call_args.kwargs["system_instruction"] == (
        "rank leads; no subagent delegation is available, reason directly"
    )


def _fake_response(*, json_side_effect=None, json_return=None, status_ok=True):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    if not status_ok:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError("bad status", request=MagicMock(), response=MagicMock())
    if json_side_effect is not None:
        resp.json.side_effect = json_side_effect
    else:
        resp.json.return_value = json_return
    return resp


def test_get_session_events_raises_trueforge_error_on_invalid_json():
    """Regression test for the Qodo finding: a response body that isn't
    valid JSON must raise TrueForgeError (json.JSONDecodeError was
    previously uncaught), not escape as a raw exception."""
    bad_response = _fake_response(json_side_effect=json.JSONDecodeError("bad json", "not json", 0))
    with patch("httpx.get", return_value=bad_response):
        with pytest.raises(TrueForgeError):
            _get_session_events("session-1")


def test_get_session_events_raises_trueforge_error_on_missing_data_key():
    """A well-formed JSON body missing the expected 'data' key must also
    raise TrueForgeError rather than a raw KeyError escaping upward."""
    response = _fake_response(json_return={"unexpected": "shape"})
    with patch("httpx.get", return_value=response):
        with pytest.raises(TrueForgeError):
            _get_session_events("session-1")


def test_get_session_events_raises_trueforge_error_on_non_list_data():
    """Regression test for the Qodo finding: _extract_subagent_delegations
    assumes every event is a dict; a 'data' field that isn't a list of dicts
    (e.g. a string, or a list of non-dict items) must be rejected here
    rather than raising deeper in _extract_subagent_delegations."""
    response = _fake_response(json_return={"data": "not-a-list"})
    with patch("httpx.get", return_value=response):
        with pytest.raises(TrueForgeError):
            _get_session_events("session-1")

    response2 = _fake_response(json_return={"data": [1, 2, 3]})
    with patch("httpx.get", return_value=response2):
        with pytest.raises(TrueForgeError):
            _get_session_events("session-1")


def test_get_session_events_returns_valid_list_of_dicts():
    response = _fake_response(json_return={"data": [{"event": {"type": "thread.created", "thread_id": "t1"}}]})
    with patch("httpx.get", return_value=response):
        events = _get_session_events("session-1")
    assert events == [{"event": {"type": "thread.created", "thread_id": "t1"}}]


def _mock_session_and_turn_responses(output_content: str):
    session_resp = MagicMock()
    session_resp.raise_for_status = MagicMock()
    session_resp.json.return_value = {"data": {"id": "session-1"}}

    turn_start_resp = MagicMock()
    turn_start_resp.raise_for_status = MagicMock()
    turn_start_resp.json.return_value = {"data": {"id": "turn-1"}}

    poll_resp = MagicMock()
    poll_resp.raise_for_status = MagicMock()
    poll_resp.json.return_value = {
        "data": {"state": {"status": "done", "output": {"content": output_content}}}
    }
    return session_resp, turn_start_resp, poll_resp


def test_run_turn_returns_successful_ranking_when_events_fetch_fails():
    """Regression test for the Qodo finding: run_turn's own turn success/
    failure must be judged independently of the session-events fetch used
    for delegation evidence. If the turn completes and parses successfully
    but the events fetch fails, run_turn must NOT raise (which would force
    a full, possibly-different rerun via the fallback) — it must return the
    successful output with delegations=None as an explicit 'evidence
    unavailable' sentinel, distinct from an empty list meaning no
    delegation occurred."""
    session_resp, turn_start_resp, poll_resp = _mock_session_and_turn_responses('{"ranking": [], "summary": "ok"}')

    with patch("httpx.post", side_effect=[session_resp, turn_start_resp]), \
         patch("httpx.get", side_effect=[poll_resp, httpx.ConnectError("events endpoint down")]):
        output, delegations = run_turn("signalis-prioritization", "rank these leads", with_delegations=True)

    assert output == {"ranking": [], "summary": "ok"}
    assert delegations is None


def test_run_turn_returns_delegations_when_events_fetch_succeeds():
    session_resp, turn_start_resp, poll_resp = _mock_session_and_turn_responses('{"ranking": [], "summary": "ok"}')
    events_resp = _fake_response(json_return={"data": []})

    with patch("httpx.post", side_effect=[session_resp, turn_start_resp]), \
         patch("httpx.get", side_effect=[poll_resp, events_resp]):
        output, delegations = run_turn("signalis-prioritization", "rank these leads", with_delegations=True)

    assert output == {"ranking": [], "summary": "ok"}
    assert delegations == []
def test_run_agent_reasoning_passes_skills_to_ensure_agent():
    """The TrueForge path must actually forward `skills` through to
    ensure_agent so the manifest genuinely includes the attachment, not just
    accept the parameter and drop it."""
    with patch("app.agents.common.get_settings") as mock_settings, \
         patch("app.agents.common.ensure_agent") as mock_ensure, \
         patch("app.agents.common.run_turn", return_value={"stage": "late", "confidence": 0.9}):
        mock_settings.return_value.trueforge_enabled = True
        run_agent_reasoning(
            trueforge_agent_name="signalis-outreach-planner",
            model="google-gemini/gemini-2-5-flash",
            system_instruction="plan outreach",
            prompt="lead data",
            response_schema=SCHEMA,
            skills=[{"name": "outreach-copywriting-style-guide"}],
        )
    _, kwargs = mock_ensure.call_args
    assert kwargs["skills"] == [{"name": "outreach-copywriting-style-guide"}]


def test_run_agent_reasoning_injects_fallback_style_guidance_when_trueforge_unavailable():
    """Regression test for the fallback-path style-guidance precedent: skills
    only exist inside TrueForge's agent loop, so when the direct Gemini/HF
    fallback path is used instead, any craft guidance that now lives only in
    a skill must be injected into the fallback's system instruction directly
    (mirroring the existing tool-stripping precedent), or the fallback path's
    output quality regresses relative to the TrueForge+skill path."""
    captured = {}

    def fake_generate_json(*, system_instruction, prompt, response_schema, temperature):
        captured["system_instruction"] = system_instruction
        return {"touchpoints": [], "channels": [], "summary": "ok"}

    with patch("app.agents.common.get_settings") as mock_settings, \
         patch("app.agents.common.ensure_agent", side_effect=TrueForgeError("unreachable")), \
         patch("app.agents.common.generate_json", side_effect=fake_generate_json):
        mock_settings.return_value.trueforge_enabled = True
        run_agent_reasoning(
            trueforge_agent_name="signalis-outreach-planner",
            model="google-gemini/gemini-2-5-flash",
            system_instruction="Plan outreach touchpoints for this lead.",
            prompt="lead data",
            response_schema=SCHEMA,
            skills=[{"name": "outreach-copywriting-style-guide"}],
            fallback_style_guidance="CONDENSED GUIDANCE: avoid generic AI-sounding copy.",
        )
    assert "CONDENSED GUIDANCE" in captured["system_instruction"]


def test_run_agent_reasoning_does_not_duplicate_style_guidance_already_in_system_instruction():
    """Regression test for Finding 2 (Qodo, 2nd pass, PR #11): when a caller
    (e.g. `run_outreach_planner`, on a skill-registration failure) has
    already folded the condensed style guidance into `system_instruction`
    itself — so the TrueForge-bound instruction carries it — and also passes
    the same text as `fallback_style_guidance`, the direct-LLM fallback path
    must not append it a second time. Before the fix, `fallback_instruction`
    started as `system_instruction` (already containing the guidance once)
    and then unconditionally appended `fallback_style_guidance` (the same
    text again), so every direct-fallback call in that situation sent the
    model two copies of the same guidance block."""
    captured = {}

    def fake_generate_json(*, system_instruction, prompt, response_schema, temperature):
        captured["system_instruction"] = system_instruction
        return {"touchpoints": [], "channels": [], "summary": "ok"}

    guidance = "CONDENSED GUIDANCE: avoid generic AI-sounding copy."
    system_instruction_with_guidance = f"Plan outreach touchpoints for this lead.\n\n{guidance}"

    with patch("app.agents.common.get_settings") as mock_settings, \
         patch("app.agents.common.ensure_agent", side_effect=TrueForgeError("unreachable")), \
         patch("app.agents.common.generate_json", side_effect=fake_generate_json):
        mock_settings.return_value.trueforge_enabled = True
        run_agent_reasoning(
            trueforge_agent_name="signalis-outreach-planner",
            model="google-gemini/gemini-2-5-flash",
            system_instruction=system_instruction_with_guidance,
            prompt="lead data",
            response_schema=SCHEMA,
            skills=None,
            fallback_style_guidance=guidance,
        )
    assert captured["system_instruction"].count(guidance) == 1
