"""Tests the confidence-threshold branching in the pipeline service, i.e.
the mandatory human-approval checkpoint, with the LangGraph node functions
mocked at the Gemini call boundary."""
from __future__ import annotations

from unittest.mock import patch

from app.services.pipeline import run_pipeline_for_lead


def _mock_graph_invoke(stage: str, confidence: float):
    def _invoke(self, state):
        state["db"].commit()
        requires_approval = confidence < 0.5
        return {
            **state,
            "signal_extraction_result": {"classifications": [], "summary": "ok"},
            "persona_fit_result": {"fit": "full_fit", "reasoning": "matches", "missing_data": []},
            "stage_result": {"stage": stage, "confidence": confidence, "justification": "test justification"},
            "requires_approval": requires_approval,
            "plan_result": {
                "touchpoints": [
                    {"day_offset": 0, "channel": "email", "content_theme": "intro", "message_copy": "hi"}
                ],
                "channels": ["email"],
                "summary": "plan summary",
            },
            "explainability_result": {"narrative": "narrative text", "agent_order": []},
        }

    return _invoke


def test_low_confidence_requires_approval(db_session, sample_lead):
    with patch(
        "app.agents.graph.build_graph",
    ) as mock_build:
        mock_graph = mock_build.return_value
        mock_graph.invoke.return_value = {
            "signal_extraction_result": {"classifications": [], "summary": "ok"},
            "persona_fit_result": {"fit": "full_fit", "reasoning": "matches", "missing_data": []},
            "stage_result": {"stage": "early", "confidence": 0.2, "justification": "weak signals"},
            "requires_approval": True,
            "plan_result": {"touchpoints": [], "channels": [], "summary": ""},
            "explainability_result": {"narrative": "narrative", "agent_order": []},
        }
        import app.agents.graph as graph_module

        graph_module._compiled_graph = None
        outcome = run_pipeline_for_lead(db_session, sample_lead)

    assert outcome["classification"].approval_status == "pending_approval"
    assert outcome["classification"].requires_approval is True
    assert outcome["plan"].status == "pending_approval"


def test_high_confidence_is_auto_approved_but_plan_still_pending(db_session, sample_lead):
    with patch("app.agents.graph.build_graph") as mock_build:
        mock_graph = mock_build.return_value
        mock_graph.invoke.return_value = {
            "signal_extraction_result": {"classifications": [], "summary": "ok"},
            "persona_fit_result": {"fit": "full_fit", "reasoning": "matches", "missing_data": []},
            "stage_result": {"stage": "late", "confidence": 0.9, "justification": "strong signals"},
            "requires_approval": False,
            "plan_result": {
                "touchpoints": [
                    {"day_offset": 0, "channel": "email", "content_theme": "intro", "message_copy": "hi"}
                ],
                "channels": ["email"],
                "summary": "plan",
            },
            "explainability_result": {"narrative": "narrative", "agent_order": []},
        }
        import app.agents.graph as graph_module

        graph_module._compiled_graph = None
        outcome = run_pipeline_for_lead(db_session, sample_lead)

    assert outcome["classification"].approval_status == "auto_approved"
    assert outcome["classification"].requires_approval is False
    # Every generated plan requires explicit approval regardless of
    # classification confidence.
    assert outcome["plan"].status == "pending_approval"


def test_pipeline_persists_email_verification_onto_outreach_plan(db_session, sample_lead):
    """run_pipeline_for_lead must persist verified_email/email_verification_status
    from the Outreach Planner's plan_result onto the created OutreachPlan row —
    not silently drop the Hunter.io verification outcome."""
    with patch("app.agents.graph.build_graph") as mock_build:
        mock_graph = mock_build.return_value
        mock_graph.invoke.return_value = {
            "signal_extraction_result": {"classifications": [], "summary": "ok"},
            "persona_fit_result": {"fit": "full_fit", "reasoning": "matches", "missing_data": []},
            "stage_result": {"stage": "late", "confidence": 0.9, "justification": "strong signals"},
            "requires_approval": False,
            "plan_result": {
                "touchpoints": [
                    {"day_offset": 0, "channel": "email", "content_theme": "intro", "message_copy": "hi"}
                ],
                "channels": ["email"],
                "summary": "plan",
                "verified_email": "jane@acme.com",
                "email_verification_status": "valid",
                "email_verification_reason": None,
            },
            "explainability_result": {"narrative": "narrative", "agent_order": []},
        }
        import app.agents.graph as graph_module

        graph_module._compiled_graph = None
        outcome = run_pipeline_for_lead(db_session, sample_lead)

    assert outcome["plan"].verified_email == "jane@acme.com"
    assert outcome["plan"].email_verification_status == "valid"
    assert outcome["plan"].email_verification_reason is None


def test_rerunning_pipeline_supersedes_prior_classification(db_session, sample_lead):
    with patch("app.agents.graph.build_graph") as mock_build:
        mock_graph = mock_build.return_value
        mock_graph.invoke.return_value = {
            "signal_extraction_result": {"classifications": [], "summary": "ok"},
            "persona_fit_result": {"fit": "full_fit", "reasoning": "matches", "missing_data": []},
            "stage_result": {"stage": "mid", "confidence": 0.8, "justification": "j1"},
            "requires_approval": False,
            "plan_result": {"touchpoints": [], "channels": [], "summary": ""},
            "explainability_result": {"narrative": "n1", "agent_order": []},
        }
        import app.agents.graph as graph_module

        graph_module._compiled_graph = None
        first = run_pipeline_for_lead(db_session, sample_lead)

        mock_graph.invoke.return_value["stage_result"] = {
            "stage": "late",
            "confidence": 0.85,
            "justification": "j2",
        }
        second = run_pipeline_for_lead(db_session, sample_lead)

    db_session.refresh(first["classification"])
    assert first["classification"].superseded_by_id == second["classification"].id
    assert second["classification"].stage == "late"
