"""Unit tests for agent logic with the TrueForge/LLM call boundary mocked.

These tests mock app.agents.common.run_agent_reasoning directly (the shared
call boundary every agent goes through), never the agent functions
themselves, so the real branching logic inside each agent (confidence
clamping, approval routing, prompt assembly) is exercised for real.
"""
from __future__ import annotations

from unittest.mock import patch

from app.agents.buying_stage import run_buying_stage
from app.agents.outreach_planner import run_outreach_planner
from app.agents.persona_fit import run_persona_fit
from app.models import Persona, Solution


def test_buying_stage_confidence_is_clamped_to_valid_range(db_session, sample_lead):
    with patch(
        "app.agents.buying_stage.run_agent_reasoning",
        return_value=({"stage": "late", "confidence": 1.7, "justification": "test"}, "session-abc"),
    ), patch(
        "app.agents.buying_stage.run_signal_scoring",
        return_value=({"weighted_score": 2.1, "signal_count": 2}, "local"),
    ):
        result = run_buying_stage(db_session, sample_lead, sample_lead.signals, {"fit": "full_fit"})
    assert result["confidence"] == 1.0
    assert result["signal_score_computed_via"] == "local"


def test_buying_stage_confidence_clamped_when_negative(db_session, sample_lead):
    with patch(
        "app.agents.buying_stage.run_agent_reasoning",
        return_value=({"stage": "early", "confidence": -0.3, "justification": "test"}, "session-def"),
    ), patch(
        "app.agents.buying_stage.run_signal_scoring",
        return_value=({"weighted_score": 1.0, "signal_count": 2}, "local"),
    ):
        result = run_buying_stage(db_session, sample_lead, sample_lead.signals, {"fit": "mismatch"})
    assert result["confidence"] == 0.0


def test_buying_stage_with_no_signals_returns_low_confidence_early(db_session, sample_lead):
    sample_lead.signals.clear()
    result = run_buying_stage(db_session, sample_lead, [], {"fit": "unknown"})
    assert result["stage"] == "early"
    assert result["confidence"] < 0.5


def test_outreach_plan_has_expected_shape(db_session, sample_lead):
    persona = Persona(
        role="VP Sales",
        seniority="VP",
        industry="SaaS",
        company_size_band="51-200",
        geography="US",
    )
    solution = Solution(
        name="Test Solution",
        problem_solved="Testing things",
        channels=["email", "linkedin"],
    )
    mocked_response = {
        "touchpoints": [
            {
                "day_offset": 0,
                "channel": "email",
                "content_theme": "intro",
                "message_copy": "Hello there",
            },
            {
                "day_offset": 3,
                "channel": "linkedin",
                "content_theme": "follow-up",
                "message_copy": "Following up",
            },
        ],
        "channels": ["email", "linkedin"],
        "summary": "A short plan.",
    }
    with patch("app.agents.outreach_planner.run_agent_reasoning", return_value=(mocked_response, "session-ghi")):
        result = run_outreach_planner(
            db_session, sample_lead, "mid", 0.7, {"fit": "full_fit"}, persona, solution
        )

    assert "touchpoints" in result
    assert len(result["touchpoints"]) == 2
    for touchpoint in result["touchpoints"]:
        assert "day_offset" in touchpoint
        assert "channel" in touchpoint
        assert "content_theme" in touchpoint
        assert "message_copy" in touchpoint
    assert result["channels"] == ["email", "linkedin"]


def test_outreach_planner_attaches_hunter_mcp_server(db_session, sample_lead):
    """The Outreach Planner must attach signalis-hunter so it can genuinely
    call find_email/verify_email, not just claim to check deliverability."""
    persona = Persona(role="VP Sales", seniority="VP", industry="SaaS", company_size_band="51-200", geography="US")
    solution = Solution(name="Test Solution", problem_solved="Testing things", channels=["email"])
    mocked_response = {
        "touchpoints": [{"day_offset": 0, "channel": "email", "content_theme": "intro", "message_copy": "Hi"}],
        "channels": ["email"],
        "summary": "A short plan.",
    }
    with patch("app.agents.outreach_planner.run_agent_reasoning", return_value=(mocked_response, "session-abc")) as mock_reasoning:
        run_outreach_planner(db_session, sample_lead, "mid", 0.7, {"fit": "full_fit"}, persona, solution)

    _, reasoning_kwargs = mock_reasoning.call_args
    mcp_names = {server["name"] for server in reasoning_kwargs["mcp_servers"]}
    assert "signalis-hunter" in mcp_names


def test_outreach_planner_includes_lead_email_in_prompt(db_session, sample_lead):
    """The model has nothing to verify without the lead's email in context."""
    sample_lead.email = "jane@acme.com"
    persona = Persona(role="VP Sales", seniority="VP", industry="SaaS", company_size_band="51-200", geography="US")
    solution = Solution(name="Test Solution", problem_solved="Testing things", channels=["email"])
    mocked_response = {
        "touchpoints": [{"day_offset": 0, "channel": "email", "content_theme": "intro", "message_copy": "Hi"}],
        "channels": ["email"],
        "summary": "A short plan.",
    }
    with patch("app.agents.outreach_planner.run_agent_reasoning", return_value=(mocked_response, "session-abc")) as mock_reasoning:
        run_outreach_planner(db_session, sample_lead, "mid", 0.7, {"fit": "full_fit"}, persona, solution)

    _, reasoning_kwargs = mock_reasoning.call_args
    assert "jane@acme.com" in reasoning_kwargs["prompt"]


def test_outreach_planner_propagates_verified_email_when_queried(db_session, sample_lead):
    persona = Persona(role="VP Sales", seniority="VP", industry="SaaS", company_size_band="51-200", geography="US")
    solution = Solution(name="Test Solution", problem_solved="Testing things", channels=["email"])
    mocked_response = {
        "touchpoints": [{"day_offset": 0, "channel": "email", "content_theme": "intro", "message_copy": "Hi"}],
        "channels": ["email"],
        "summary": "A short plan.",
        "email_verification": {"queried": True, "email": "jane@acme.com", "status": "valid"},
    }
    with patch("app.agents.outreach_planner.run_agent_reasoning", return_value=(mocked_response, "session-abc")):
        result = run_outreach_planner(db_session, sample_lead, "mid", 0.7, {"fit": "full_fit"}, persona, solution)

    assert result["verified_email"] == "jane@acme.com"
    assert result["email_verification_status"] == "valid"


def test_outreach_planner_reports_unverified_when_email_not_queried(db_session, sample_lead):
    """If the model never called verify_email (e.g. no HUNTER_API_KEY
    configured, or no email to check), the status must be the honest
    'unverified' default, not a fabricated status."""
    persona = Persona(role="VP Sales", seniority="VP", industry="SaaS", company_size_band="51-200", geography="US")
    solution = Solution(name="Test Solution", problem_solved="Testing things", channels=["email"])
    mocked_response = {
        "touchpoints": [{"day_offset": 0, "channel": "email", "content_theme": "intro", "message_copy": "Hi"}],
        "channels": ["email"],
        "summary": "A short plan.",
        # No email_verification key at all -- the model didn't call the tool.
    }
    with patch("app.agents.outreach_planner.run_agent_reasoning", return_value=(mocked_response, "session-abc")):
        result = run_outreach_planner(db_session, sample_lead, "mid", 0.7, {"fit": "full_fit"}, persona, solution)

    assert result["verified_email"] is None
    assert result["email_verification_status"] == "unverified"


def test_outreach_planner_registers_and_attaches_copywriting_skill(db_session, sample_lead):
    """The Outreach Planner must register the outreach-copywriting-style-guide
    TrueForge skill and attach it (name-only reference) to the agent call, so
    the manifest genuinely includes the skill rather than the skill sitting
    unused next to an unchanged prompt."""
    persona = Persona(role="VP Sales", seniority="VP", industry="SaaS", company_size_band="51-200", geography="US")
    solution = Solution(name="Test Solution", problem_solved="Testing things", channels=["email"])
    mocked_response = {
        "touchpoints": [
            {"day_offset": 0, "channel": "email", "content_theme": "intro", "message_copy": "Hi"}
        ],
        "channels": ["email"],
        "summary": "A short plan.",
    }

    with patch("app.agents.outreach_planner.get_settings") as mock_settings, \
         patch("app.agents.outreach_planner.ensure_skill") as mock_ensure_skill, \
         patch("app.agents.outreach_planner.run_agent_reasoning", return_value=(mocked_response, "session-abc")) as mock_reasoning:
        mock_settings.return_value.trueforge_enabled = True
        mock_settings.return_value.trueforge_model = "google-gemini/gemini-2-5-flash"
        run_outreach_planner(db_session, sample_lead, "mid", 0.7, {"fit": "full_fit"}, persona, solution)

    mock_ensure_skill.assert_called_once()
    _, ensure_skill_kwargs = mock_ensure_skill.call_args
    assert ensure_skill_kwargs["repo_url"].startswith("https://github.com/")
    assert ensure_skill_kwargs["path"].endswith("outreach_copywriting_style_guide")

    _, reasoning_kwargs = mock_reasoning.call_args
    assert reasoning_kwargs["skills"] == [{"name": "outreach-copywriting-style-guide"}]
    # The task-mechanical instruction should still point at the skill even
    # though the copywriting craft guidance itself has been extracted out of it.
    assert "outreach-copywriting-style-guide" in reasoning_kwargs["system_instruction"]
    assert reasoning_kwargs["fallback_style_guidance"]


def test_outreach_planner_skips_skill_registration_when_trueforge_disabled(db_session, sample_lead):
    """When TrueForge is disabled, registering a TrueForge-only skill would
    always fail; the planner should skip registration and still produce a
    plan via the direct fallback path, relying on `fallback_style_guidance`
    to carry the craft guidance instead."""
    mocked_response = {"touchpoints": [], "channels": ["email"], "summary": "ok"}

    with patch("app.agents.outreach_planner.get_settings") as mock_settings, \
         patch("app.agents.outreach_planner.ensure_skill") as mock_ensure_skill, \
         patch("app.agents.outreach_planner.run_agent_reasoning", return_value=(mocked_response, "session-abc")) as mock_reasoning:
        mock_settings.return_value.trueforge_enabled = False
        mock_settings.return_value.trueforge_model = "google-gemini/gemini-2-5-flash"
        run_outreach_planner(db_session, sample_lead, "early", 0.3, {"fit": "partial_fit"}, None, None)

    mock_ensure_skill.assert_not_called()
    _, reasoning_kwargs = mock_reasoning.call_args
    assert reasoning_kwargs["skills"] is None
    assert reasoning_kwargs["fallback_style_guidance"]


def test_outreach_planner_injects_condensed_guidance_when_skill_registration_fails(db_session, sample_lead):
    """Regression test for Finding 2 (Qodo, PR #11): if TrueForge is enabled
    but the skill fails to register this call (e.g. a transient TrueForge
    error), the TrueForge-path call must NOT silently proceed with neither
    the full skill nor the condensed fallback guidance — that would produce
    an outreach plan with no copywriting craft guidance at all, persisted
    as an indistinguishable "completed" run. The condensed guidance (the
    same string already used on the direct-LLM fallback path) must be
    folded into the TrueForge system_instruction for that call instead."""
    persona = Persona(role="VP Sales", seniority="VP", industry="SaaS", company_size_band="51-200", geography="US")
    solution = Solution(name="Test Solution", problem_solved="Testing things", channels=["email"])
    mocked_response = {
        "touchpoints": [
            {"day_offset": 0, "channel": "email", "content_theme": "intro", "message_copy": "Hi"}
        ],
        "channels": ["email"],
        "summary": "A short plan.",
    }

    with patch("app.agents.outreach_planner.get_settings") as mock_settings, \
         patch("app.agents.outreach_planner.ensure_skill", side_effect=RuntimeError("TrueForge 500")), \
         patch("app.agents.outreach_planner.run_agent_reasoning", return_value=(mocked_response, "session-abc")) as mock_reasoning:
        mock_settings.return_value.trueforge_enabled = True
        mock_settings.return_value.trueforge_model = "google-gemini/gemini-2-5-flash"
        run_outreach_planner(db_session, sample_lead, "mid", 0.7, {"fit": "full_fit"}, persona, solution)

    _, reasoning_kwargs = mock_reasoning.call_args
    # skills must be None (caller has no opinion this call), not [] (which
    # would mean "explicitly no skills" and could clobber a previously
    # attached skill on ensure_agent's update path).
    assert reasoning_kwargs["skills"] is None
    # But the instruction actually sent to run_agent_reasoning — which is
    # what ensure_agent wraps into the TrueForge agent's manifest
    # instructions — must still carry real copywriting guidance.
    from app.agents.skills.outreach_copywriting_style_guide import CONDENSED_STYLE_GUIDANCE

    assert CONDENSED_STYLE_GUIDANCE in reasoning_kwargs["system_instruction"]


def test_outreach_planner_end_to_end_fallback_does_not_duplicate_guidance_after_skill_failure(
    db_session, sample_lead
):
    """End-to-end regression test for Finding 2 (Qodo, 2nd pass, PR #11),
    exercising the real `run_agent_reasoning`/`common.py` logic (not mocked
    away like the other outreach-planner tests) so the interaction between
    `outreach_planner.py`'s condensed-guidance injection and `common.py`'s
    own fallback-guidance injection is actually tested end to end.

    Scenario: TrueForge is enabled, but skill registration fails this call
    (skills=None) *and* the TrueForge call itself fails, so execution
    reaches the direct-LLM fallback. Before the fix, the model would have
    received CONDENSED_STYLE_GUIDANCE twice in its system instruction:
    once because `trueforge_instruction` already had it folded in, and
    again because `common.py` unconditionally appended
    `fallback_style_guidance`."""
    from app.agents.skills.outreach_copywriting_style_guide import CONDENSED_STYLE_GUIDANCE
    from app.core.trueforge import TrueForgeError

    captured = {}

    def fake_generate_json(*, system_instruction, prompt, response_schema, temperature):
        captured["system_instruction"] = system_instruction
        return {"touchpoints": [], "channels": ["email"], "summary": "ok"}

    with patch("app.agents.outreach_planner.get_settings") as mock_outreach_settings, \
         patch("app.agents.outreach_planner.ensure_skill", side_effect=RuntimeError("TrueForge 500")), \
         patch("app.agents.common.get_settings") as mock_common_settings, \
         patch("app.agents.common.ensure_agent", side_effect=TrueForgeError("unreachable")), \
         patch("app.agents.common.generate_json", side_effect=fake_generate_json):
        mock_outreach_settings.return_value.trueforge_enabled = True
        mock_outreach_settings.return_value.trueforge_model = "google-gemini/gemini-2-5-flash"
        mock_common_settings.return_value.trueforge_enabled = True
        run_outreach_planner(db_session, sample_lead, "mid", 0.7, {"fit": "full_fit"}, None, None)

    assert captured["system_instruction"].count(CONDENSED_STYLE_GUIDANCE) == 1


def test_condensed_style_guidance_covers_core_craft_points():
    """Sanity check that the fallback path's condensed guidance is a real
    summary of the skill's content, not an empty placeholder — covers the
    same core points called out in SKILL.md so the no-TrueForge path doesn't
    silently regress in output quality."""
    from app.agents.skills.outreach_copywriting_style_guide import CONDENSED_STYLE_GUIDANCE

    assert len(CONDENSED_STYLE_GUIDANCE) > 200
    for keyword in ["channel", "signal", "cadence", "AI-sounding"]:
        assert keyword.lower() in CONDENSED_STYLE_GUIDANCE.lower()


def test_persona_fit_handles_missing_persona_and_solution(db_session, sample_lead):
    with patch(
        "app.agents.persona_fit.run_agent_reasoning",
        return_value=(
            {"fit": "partial_fit", "reasoning": "no persona defined", "missing_data": ["persona"]},
            "session-jkl",
        ),
    ):
        result, agent_run_id = run_persona_fit(db_session, sample_lead, None, None)
    assert result["fit"] == "partial_fit"
    assert "missing_data" in result
    assert agent_run_id is not None


def test_persona_fit_attaches_exa_mcp_server(db_session, sample_lead):
    """Persona Fit must attach signalis-exa alongside the existing
    enrichment/research servers so it can genuinely call
    search_company_semantic, not just claim a second, differently-sourced
    read is possible."""
    with patch(
        "app.agents.persona_fit.run_agent_reasoning",
        return_value=({"fit": "full_fit", "reasoning": "test", "missing_data": []}, "session-mno"),
    ) as mock_reasoning:
        run_persona_fit(db_session, sample_lead, None, None)

    _, reasoning_kwargs = mock_reasoning.call_args
    mcp_names = {server["name"] for server in reasoning_kwargs["mcp_servers"]}
    assert "signalis-exa" in mcp_names
    assert "signalis-research" in mcp_names
    assert "signalis-enrichment" in mcp_names


def test_run_persona_fit_reuses_completed_run_instead_of_hitting_gate_again(db_session, sample_lead):
    """Regression test for "Regeneration repeats approval gate": once a
    Persona Fit AgentRun has completed (e.g. via resume_persona_fit after a
    marketer approved a paused tool call) and its result hasn't been
    consumed by any StageClassification yet, a subsequent run_persona_fit
    call (as "Regenerate Plan" triggers via the graph) must reuse that
    result instead of starting a brand new TrueForge turn and re-hitting the
    same require_approval_for_tools gate."""
    from app.agents.common import finish_run, start_run

    completed_run = start_run(
        db_session, lead_id=sample_lead.id, agent_name="persona_fit", input_summary="prior run"
    )
    finish_run(
        db_session,
        completed_run,
        output={"fit": "full_fit", "reasoning": "already approved", "missing_data": []},
        reasoning="already approved",
    )

    with patch("app.agents.persona_fit.run_agent_reasoning") as mock_reasoning:
        result, agent_run_id = run_persona_fit(db_session, sample_lead, None, None)

    mock_reasoning.assert_not_called()  # no new TrueForge turn started
    assert agent_run_id == completed_run.id
    assert result["fit"] == "full_fit"
    assert result["reasoning"] == "already approved"


def test_run_persona_fit_starts_fresh_once_prior_result_is_consumed(db_session, sample_lead):
    """The reuse in the previous test must not be permanent: once a
    StageClassification records based_on_agent_run_id for the completed run,
    the *next* run_persona_fit call (a later, unrelated Regenerate Plan) must
    start a fresh TrueForge turn rather than reusing stale output forever."""
    from app.agents.common import finish_run, start_run
    from app.models import StageClassification

    completed_run = start_run(
        db_session, lead_id=sample_lead.id, agent_name="persona_fit", input_summary="prior run"
    )
    finish_run(
        db_session,
        completed_run,
        output={"fit": "full_fit", "reasoning": "already approved", "missing_data": []},
        reasoning="already approved",
    )
    classification = StageClassification(
        lead_id=sample_lead.id,
        stage="mid",
        confidence=0.9,
        justification="test",
        persona_fit_result={"fit": "full_fit"},
        based_on_agent_run_id=completed_run.id,
    )
    db_session.add(classification)
    db_session.commit()

    with patch(
        "app.agents.persona_fit.run_agent_reasoning",
        return_value=({"fit": "mismatch", "reasoning": "fresh run", "missing_data": []}, "sess-fresh"),
    ) as mock_reasoning:
        result, agent_run_id = run_persona_fit(db_session, sample_lead, None, None)

    mock_reasoning.assert_called_once()
    assert agent_run_id != completed_run.id
    assert result["fit"] == "mismatch"
