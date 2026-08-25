"""Unit tests for agent logic with the Gemini SDK boundary mocked.

These tests mock app.core.llm.generate_json directly (the actual network
boundary), never the agent functions themselves, so the real branching logic
inside each agent (confidence clamping, approval routing, prompt assembly)
is exercised for real.
"""
from __future__ import annotations

from unittest.mock import patch

from app.agents.buying_stage import run_buying_stage
from app.agents.outreach_planner import run_outreach_planner
from app.agents.persona_fit import run_persona_fit
from app.models.entities import Persona, Solution


def test_buying_stage_confidence_is_clamped_to_valid_range(db_session, sample_lead):
    with patch(
        "app.agents.buying_stage.generate_json",
        return_value={"stage": "late", "confidence": 1.7, "justification": "test"},
    ):
        result = run_buying_stage(db_session, sample_lead, sample_lead.signals, {"fit": "full_fit"})
    assert result["confidence"] == 1.0


def test_buying_stage_confidence_clamped_when_negative(db_session, sample_lead):
    with patch(
        "app.agents.buying_stage.generate_json",
        return_value={"stage": "early", "confidence": -0.3, "justification": "test"},
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
    with patch("app.agents.outreach_planner.generate_json", return_value=mocked_response):
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


def test_persona_fit_handles_missing_persona_and_solution(db_session, sample_lead):
    with patch(
        "app.agents.persona_fit.generate_json",
        return_value={"fit": "partial_fit", "reasoning": "no persona defined", "missing_data": ["persona"]},
    ):
        result = run_persona_fit(db_session, sample_lead, None, None)
    assert result["fit"] == "partial_fit"
    assert "missing_data" in result
