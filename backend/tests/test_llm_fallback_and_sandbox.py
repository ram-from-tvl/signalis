"""Tests for the Gemini-to-Hugging-Face fallback and Daytona sandbox scoring,
with each external transport mocked at its own boundary."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.agents.common import run_agent_reasoning
from app.core.llm import LLMError, generate_json
from app.core.sandbox import run_signal_scoring
from app.core.trueforge import TrueForgeError, ensure_agent, ensure_skill

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
    # no-op), since config like this PR's skills attachment has to take
    # effect on an agent that was already registered by an earlier run.
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
