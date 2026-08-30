"""Tests the campaign resolution the pipeline uses to decide which
persona/solution to score a lead against — the fix for the "singleton
persona" problem: every lead used to be scored against whichever
persona/solution happened to be most-recently-saved, system-wide,
regardless of which lead it was for."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.models import Campaign, Lead, Persona, Solution
from app.services.pipeline import NoCampaignConfigured, run_pipeline_for_lead


def _mock_graph_invoke_capturing_persona(captured: dict):
    def _invoke(self, state):
        captured["persona"] = state["persona"]
        captured["solution"] = state["solution"]
        return {
            **state,
            "signal_extraction_result": {"classifications": [], "summary": "ok"},
            "persona_fit_result": {"fit": "full_fit", "reasoning": "matches", "missing_data": []},
            "stage_result": {"stage": "mid", "confidence": 0.8, "justification": "j"},
            "requires_approval": False,
            "plan_result": {"touchpoints": [], "channels": [], "summary": ""},
            "explainability_result": {"narrative": "n", "agent_order": []},
        }

    return _invoke


def _make_campaign(db_session, name: str, role: str) -> Campaign:
    persona = Persona(
        role=role, seniority="VP", industry="SaaS", company_size_band="1-50", geography="US"
    )
    solution = Solution(
        name=f"Solution for {role}",
        problem_solved="p",
        value_props=["v"],
        differentiators=["d"],
        channels=["email"],
    )
    db_session.add_all([persona, solution])
    db_session.commit()
    db_session.refresh(persona)
    db_session.refresh(solution)
    campaign = Campaign(name=name, persona_id=persona.id, solution_id=solution.id)
    db_session.add(campaign)
    db_session.commit()
    db_session.refresh(campaign)
    return campaign


def test_lead_is_scored_against_its_own_campaigns_persona(db_session, default_campaign):
    """The core regression test for the singleton-persona bug: two leads on
    two different campaigns must be scored against their OWN campaign's
    persona, not whichever persona was saved most recently system-wide."""
    cto_campaign = _make_campaign(db_session, "CTO campaign", role="CTO")

    lead = Lead(
        name="Someone",
        company="Acme",
        title="CTO",
        company_size="10-50",
        industry="SaaS",
        geography="US",
        email="someone@acme.com",
        campaign_id=cto_campaign.id,
    )
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)

    captured: dict = {}
    with patch("app.agents.graph.build_graph") as mock_build:
        mock_build.return_value.invoke = _mock_graph_invoke_capturing_persona(captured).__get__(
            mock_build.return_value
        )
        import app.agents.graph as graph_module

        graph_module._compiled_graph = None
        run_pipeline_for_lead(db_session, lead)

    assert captured["persona"].role == "CTO"
    assert captured["persona"].id == cto_campaign.persona_id
    # And explicitly NOT the default campaign's persona, proving this
    # isn't accidentally still picking "whatever was created last".
    assert captured["persona"].id != default_campaign.persona_id


def test_lead_with_no_campaign_falls_back_to_default(db_session, default_campaign, sample_lead):
    # sample_lead already carries default_campaign.id via the fixture;
    # simulate a pre-campaign-model lead by clearing it.
    sample_lead.campaign_id = None
    db_session.add(sample_lead)
    db_session.commit()

    captured: dict = {}
    with patch("app.agents.graph.build_graph") as mock_build:
        mock_build.return_value.invoke = _mock_graph_invoke_capturing_persona(captured).__get__(
            mock_build.return_value
        )
        import app.agents.graph as graph_module

        graph_module._compiled_graph = None
        run_pipeline_for_lead(db_session, sample_lead)

    assert captured["persona"].id == default_campaign.persona_id


def test_lead_with_no_campaign_and_no_default_raises_clear_error(db_session):
    lead = Lead(
        name="Orphan",
        company="Nowhere Inc",
        title="Someone",
        company_size="1-10",
        industry="Unknown",
        geography="Unknown",
        email="orphan@nowhere.com",
    )
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)

    with pytest.raises(NoCampaignConfigured):
        run_pipeline_for_lead(db_session, lead)
