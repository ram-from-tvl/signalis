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
        return_value={"stage": "late", "confidence": 1.7, "justification": "test"},
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
        return_value={"stage": "early", "confidence": -0.3, "justification": "test"},
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
    with patch("app.agents.outreach_planner.run_agent_reasoning", return_value=mocked_response):
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
         patch("app.agents.outreach_planner.run_agent_reasoning", return_value=mocked_response) as mock_reasoning:
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
         patch("app.agents.outreach_planner.run_agent_reasoning", return_value=mocked_response) as mock_reasoning:
        mock_settings.return_value.trueforge_enabled = False
        mock_settings.return_value.trueforge_model = "google-gemini/gemini-2-5-flash"
        run_outreach_planner(db_session, sample_lead, "early", 0.3, {"fit": "partial_fit"}, None, None)

    mock_ensure_skill.assert_not_called()
    _, reasoning_kwargs = mock_reasoning.call_args
    assert reasoning_kwargs["skills"] is None
    assert reasoning_kwargs["fallback_style_guidance"]


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
        return_value={"fit": "partial_fit", "reasoning": "no persona defined", "missing_data": ["persona"]},
    ):
        result = run_persona_fit(db_session, sample_lead, None, None)
    assert result["fit"] == "partial_fit"
    assert "missing_data" in result
