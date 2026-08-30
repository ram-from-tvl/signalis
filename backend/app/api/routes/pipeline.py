from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.llm import LLMError
from app.models import Lead
from app.schemas import (
    OutreachPlanOut,
    PipelineRunRequest,
    PipelineRunResponse,
    PipelineRunResult,
    StageClassificationOut,
)
from app.services.pipeline import NoCampaignConfigured, PipelinePausedForApproval, run_pipeline_for_lead

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.post("/run", response_model=PipelineRunResponse)
def run_pipeline(payload: PipelineRunRequest, db: Session = Depends(get_db)):
    """Triggers the full agent graph for one or more leads. Also the
    endpoint used to re-classify a lead after new signals have been
    appended, and to regenerate a stage-specific outreach plan on demand."""
    if payload.lead_ids:
        leads = [db.get(Lead, lead_id) for lead_id in payload.lead_ids]
        leads = [lead for lead in leads if lead is not None]
    else:
        leads = db.execute(select(Lead)).scalars().all()

    results: list[PipelineRunResult] = []
    errors: list[dict] = []

    for lead in leads:
        started = time.perf_counter()
        try:
            outcome = run_pipeline_for_lead(db, lead)
        except NoCampaignConfigured as exc:
            errors.append({"lead_id": lead.id, "error": str(exc)})
            continue
        except PipelinePausedForApproval as exc:
            tool_names = ", ".join(sorted({r.tool_name for r in exc.requests}))
            errors.append(
                {
                    "lead_id": lead.id,
                    "error": (
                        f"Paused awaiting tool approval ({tool_names}). Review and approve/reject "
                        "it under Pending Tool Approvals, then regenerate the plan."
                    ),
                }
            )
            continue
        except LLMError as exc:
            errors.append({"lead_id": lead.id, "error": str(exc)})
            continue
        elapsed = time.perf_counter() - started
        results.append(
            PipelineRunResult(
                lead_id=lead.id,
                classification=StageClassificationOut.model_validate(outcome["classification"]),
                plan=OutreachPlanOut.model_validate(outcome["plan"]),
                explainability_narrative=outcome["explainability"].get("narrative", ""),
                latency_seconds=elapsed,
            )
        )

    return PipelineRunResponse(results=results, errors=errors)
