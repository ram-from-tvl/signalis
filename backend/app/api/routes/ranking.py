from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.entities import Lead, PipelineRanking, StageClassification
from app.schemas.schemas import LeadOut, PipelineRankingOut, RankedLeadEntry
from app.services.ranking import latest_pipeline_ranking, run_pipeline_ranking

router = APIRouter(prefix="/api/ranking", tags=["ranking"])


def _to_response(db: Session, ranking: PipelineRanking) -> PipelineRankingOut:
    entries = []
    for entry in ranking.ranked_leads:
        lead = db.get(Lead, entry.get("lead_id"))
        classification = db.execute(
            select(StageClassification)
            .where(
                StageClassification.lead_id == entry.get("lead_id"),
                StageClassification.superseded_by_id.is_(None),
            )
            .order_by(StageClassification.created_at.desc())
        ).scalars().first()
        entries.append(
            RankedLeadEntry(
                lead_id=entry.get("lead_id"),
                rank=entry.get("rank"),
                reasoning=entry.get("reasoning", ""),
                lead=LeadOut.model_validate(lead) if lead else None,
                stage=classification.stage if classification else None,
                confidence=classification.confidence if classification else None,
            )
        )
    return PipelineRankingOut(
        id=ranking.id,
        summary=ranking.summary,
        ranked_leads=entries,
        created_at=ranking.created_at,
    )


@router.post("/run", response_model=PipelineRankingOut)
def trigger_ranking(db: Session = Depends(get_db)):
    ranking = run_pipeline_ranking(db)
    return _to_response(db, ranking)


@router.get("/latest", response_model=PipelineRankingOut | None)
def get_latest_ranking(db: Session = Depends(get_db)):
    ranking = latest_pipeline_ranking(db)
    if ranking is None:
        return None
    return _to_response(db, ranking)
