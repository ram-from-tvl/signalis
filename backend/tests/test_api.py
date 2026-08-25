from __future__ import annotations

import io
from unittest.mock import patch


def test_health_check(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_crm_csv_creates_leads_and_reports_skips(client):
    csv_content = (
        "name,company,title,company_size,industry,geography,email,last_activity_date\n"
        "Jane Doe,Acme Corp,VP Sales,51-200,SaaS,US,jane@acme.com,2026-08-01\n"
        ",Missing Name Co,Director,11-50,Retail,US,nobody@missing.com,2026-08-01\n"
    )
    response = client.post(
        "/api/uploads/crm-csv",
        files={"file": ("leads.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["leads_created"] == 1
    assert body["rows_skipped"] == 1
    assert "missing required field" in body["skipped_reasons"][0]


def test_upload_crm_csv_rejects_non_csv_file(client):
    response = client.post(
        "/api/uploads/crm-csv",
        files={"file": ("leads.txt", io.BytesIO(b"not a csv"), "text/plain")},
    )
    assert response.status_code == 400


def test_upload_website_events_handles_malformed_json(client):
    response = client.post(
        "/api/uploads/website-events",
        files={"file": ("events.json", io.BytesIO(b"{not valid json"), "application/json")},
    )
    assert response.status_code == 400


def test_upload_website_events_skips_events_without_lead_identifier(client):
    events = b'[{"page": "/pricing", "event_type": "pricing_page_visit"}]'
    response = client.post(
        "/api/uploads/website-events",
        files={"file": ("events.json", io.BytesIO(events), "application/json")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rows_skipped"] == 1


def test_get_lead_detail_returns_signal_history(client, sample_lead):
    response = client.get(f"/api/leads/{sample_lead.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["lead"]["id"] == sample_lead.id
    assert len(body["signals"]) == 1
    assert body["classification_history"] == []


def test_get_lead_detail_404_for_unknown_lead(client):
    response = client.get("/api/leads/does-not-exist")
    assert response.status_code == 404


def test_trigger_pipeline_for_lead_persists_classification_and_plan(client, sample_lead):
    mocked_state = {
        "signal_extraction_result": {"classifications": [], "summary": "ok"},
        "persona_fit_result": {"fit": "full_fit", "reasoning": "matches", "missing_data": []},
        "stage_result": {"stage": "mid", "confidence": 0.75, "justification": "solid mid-stage signals"},
        "requires_approval": False,
        "plan_result": {
            "touchpoints": [
                {"day_offset": 0, "channel": "email", "content_theme": "intro", "message_copy": "Hi there"}
            ],
            "channels": ["email"],
            "summary": "plan summary",
        },
        "explainability_result": {"narrative": "narrative text", "agent_order": []},
    }
    with patch("app.agents.graph.build_graph") as mock_build:
        mock_build.return_value.invoke.return_value = mocked_state
        import app.agents.graph as graph_module

        graph_module._compiled_graph = None

        response = client.post("/api/pipeline/run", json={"lead_ids": [sample_lead.id]})

    assert response.status_code == 200
    body = response.json()
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert result["classification"]["stage"] == "mid"
    assert result["plan"]["status"] == "pending_approval"
    assert result["latency_seconds"] >= 0

    detail = client.get(f"/api/leads/{sample_lead.id}").json()
    assert len(detail["classification_history"]) == 1
    assert detail["latest_plan"]["status"] == "pending_approval"


def test_trigger_pipeline_for_unknown_lead_returns_empty_results(client):
    response = client.post("/api/pipeline/run", json={"lead_ids": ["nonexistent-id"]})
    assert response.status_code == 200
    body = response.json()
    assert body["results"] == []
    assert body["errors"] == []


def test_approve_plan_updates_status(client, sample_lead, db_session):
    from app.models import OutreachPlan, StageClassification

    classification = StageClassification(
        lead_id=sample_lead.id, stage="mid", confidence=0.7, justification="test"
    )
    db_session.add(classification)
    db_session.flush()
    plan = OutreachPlan(
        lead_id=sample_lead.id,
        stage_classification_id=classification.id,
        touchpoints=[],
        channels=["email"],
        messaging_examples=[],
        status="pending_approval",
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)

    response = client.post(
        f"/api/approvals/plans/{plan.id}", json={"action": "approve", "approved_by": "test_user"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["approved_by"] == "test_user"


def test_dashboard_stats_with_no_data(client):
    response = client.get("/api/dashboard/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["total_leads"] == 0
    assert body["average_confidence"] == 0.0


def test_dashboard_latency_excludes_pipeline_wide_agent_runs(client, db_session):
    """Regression test for the Qodo finding: the Prioritization/Ranking
    agent's AgentRun (lead_id=None, reasoning over the whole pipeline) must
    not be averaged into the per-lead agent latency the dashboard reports,
    since it isn't a per-lead call and would skew the number."""
    import datetime

    from app.models import AgentRun

    fast_start = datetime.datetime(2026, 1, 1, 0, 0, 0)
    fast_end = fast_start + datetime.timedelta(seconds=2)
    slow_start = datetime.datetime(2026, 1, 1, 0, 0, 0)
    slow_end = slow_start + datetime.timedelta(seconds=200)

    db_session.add(
        AgentRun(
            lead_id=None,
            agent_name="prioritization",
            status="completed",
            started_at=slow_start,
            completed_at=slow_end,
        )
    )
    db_session.add(
        AgentRun(
            lead_id="some-lead-id",
            agent_name="signal_extraction",
            status="completed",
            started_at=fast_start,
            completed_at=fast_end,
        )
    )
    db_session.commit()

    response = client.get("/api/dashboard/stats")
    body = response.json()
    assert body["average_agent_latency_seconds"] == 2.0
