"""Tests the campaign resolution the pipeline uses to decide which
persona/solution to score a lead against — the fix for the "singleton
persona" problem: every lead used to be scored against whichever
persona/solution happened to be most-recently-saved, system-wide,
regardless of which lead it was for."""
from __future__ import annotations

import io
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


def _create_persona_and_solution(client, suffix: str = "") -> tuple[str, str]:
    persona = client.post(
        "/api/personas",
        json={
            "role": f"Role{suffix}",
            "seniority": "VP",
            "industry": "SaaS",
            "company_size_band": "1-50",
            "geography": "US",
        },
    ).json()
    solution = client.post(
        "/api/solutions",
        json={
            "name": f"Solution{suffix}",
            "problem_solved": "p",
            "value_props": ["v"],
            "differentiators": ["d"],
            "channels": ["email"],
        },
    ).json()
    return persona["id"], solution["id"]


class TestDefaultCampaignInvariant:
    """The API layer, not just the pipeline resolver, must keep "exactly
    one default campaign, or explicitly none if truly none exist yet"
    true — otherwise a campaign-less lead can end up with nothing to fall
    back to even though campaigns genuinely exist."""

    def test_first_campaign_ever_created_becomes_default_even_if_not_requested(self, client):
        persona_id, solution_id = _create_persona_and_solution(client)
        response = client.post(
            "/api/campaigns",
            json={"name": "First Campaign", "persona_id": persona_id, "solution_id": solution_id},
        )
        assert response.status_code == 201
        assert response.json()["is_default"] is True

    def test_second_campaign_is_not_default_unless_requested(self, client):
        p1, s1 = _create_persona_and_solution(client, "1")
        client.post("/api/campaigns", json={"name": "First", "persona_id": p1, "solution_id": s1})

        p2, s2 = _create_persona_and_solution(client, "2")
        response = client.post(
            "/api/campaigns", json={"name": "Second", "persona_id": p2, "solution_id": s2}
        )
        assert response.json()["is_default"] is False

    def test_cannot_clear_default_via_update_without_promoting_another(self, client):
        persona_id, solution_id = _create_persona_and_solution(client)
        campaign = client.post(
            "/api/campaigns",
            json={"name": "Only Campaign", "persona_id": persona_id, "solution_id": solution_id},
        ).json()
        assert campaign["is_default"] is True

        response = client.put(
            f"/api/campaigns/{campaign['id']}",
            json={
                "name": campaign["name"],
                "persona_id": persona_id,
                "solution_id": solution_id,
                "is_default": False,
            },
        )
        assert response.status_code == 409

    def test_cannot_delete_the_default_campaign(self, client):
        persona_id, solution_id = _create_persona_and_solution(client)
        campaign = client.post(
            "/api/campaigns",
            json={"name": "Only Campaign", "persona_id": persona_id, "solution_id": solution_id},
        ).json()
        assert campaign["is_default"] is True

        response = client.delete(f"/api/campaigns/{campaign['id']}")
        assert response.status_code == 409

    def test_promoting_a_different_campaign_moves_the_default(self, client):
        p1, s1 = _create_persona_and_solution(client, "1")
        first = client.post(
            "/api/campaigns", json={"name": "First", "persona_id": p1, "solution_id": s1}
        ).json()
        assert first["is_default"] is True

        p2, s2 = _create_persona_and_solution(client, "2")
        second = client.post(
            "/api/campaigns", json={"name": "Second", "persona_id": p2, "solution_id": s2}
        ).json()

        response = client.put(
            f"/api/campaigns/{second['id']}",
            json={"name": second["name"], "persona_id": p2, "solution_id": s2, "is_default": True},
        )
        assert response.json()["is_default"] is True

        first_now = client.get(f"/api/campaigns/{first['id']}").json()
        assert first_now["is_default"] is False


def test_crm_upload_without_explicit_campaign_uses_the_default_campaign(client):
    """A lead created by an upload that doesn't specify campaign_id must
    still land on the default campaign — not stay genuinely campaign-less
    until some later process backfills it."""
    persona_id, solution_id = _create_persona_and_solution(client)
    campaign = client.post(
        "/api/campaigns",
        json={"name": "Default Campaign", "persona_id": persona_id, "solution_id": solution_id},
    ).json()
    assert campaign["is_default"] is True

    csv_content = "name,company,email\nJane Doe,Acme Corp,jane@acme.com\n"
    response = client.post(
        "/api/uploads/crm-csv",
        files={"file": ("leads.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert response.status_code == 200

    leads = client.get("/api/leads").json()
    jane = next(row for row in leads if row["lead"]["email"] == "jane@acme.com")
    assert jane["lead"]["campaign_id"] == campaign["id"]


class TestSharedPersonaSolutionGuard:
    """Two campaigns must never end up silently sharing a persona/solution
    row — the UI's whole premise is "each campaign has its own persona and
    solution," and editing one would otherwise mutate the other."""

    def test_cannot_repoint_a_campaign_at_another_campaigns_persona(self, client):
        p1, s1 = _create_persona_and_solution(client, "1")
        campaign_a = client.post(
            "/api/campaigns", json={"name": "A", "persona_id": p1, "solution_id": s1}
        ).json()

        p2, s2 = _create_persona_and_solution(client, "2")
        campaign_b = client.post(
            "/api/campaigns", json={"name": "B", "persona_id": p2, "solution_id": s2}
        ).json()

        response = client.put(
            f"/api/campaigns/{campaign_b['id']}",
            json={"name": "B", "persona_id": p1, "solution_id": s2, "is_default": False},
        )
        assert response.status_code == 409
        assert campaign_a["persona_id"] == p1  # unaffected
