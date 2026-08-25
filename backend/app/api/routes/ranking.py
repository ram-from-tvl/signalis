from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.entities import PipelineRanking
from app.schemas.schemas import PipelineRankingOut, RankedLeadEntry
from app.services.ranking import latest_pipeline_ranking, run_pipeline_ranking

logger = logging.getLogger("signalis.api")

router = APIRouter(prefix="/api/ranking", tags=["ranking"])


def _to_response(ranking: PipelineRanking) -> PipelineRankingOut:
    # Every field needed is already baked into ranked_leads at write time
    # (see PipelineRanking in app.models.entities), so building this
    # response needs zero additional database queries regardless of how
    # many leads were ranked. ranked_leads is stored as unvalidated JSON, so
    # a malformed row (e.g. from a manually edited DB) is skipped rather
    # than raising a 500 from Pydantic validation.
    entries = []
    for entry in ranking.ranked_leads:
        try:
            entries.append(RankedLeadEntry(**entry))
        except (TypeError, ValueError) as exc:
            logger.warning("Skipping malformed ranked_leads entry in ranking %s: %s", ranking.id, exc)
    return PipelineRankingOut(
        id=ranking.id,
        summary=ranking.summary,
        ranked_leads=entries,
        created_at=ranking.created_at,
    )


@router.post("/run", response_model=PipelineRankingOut)
def trigger_ranking(db: Session = Depends(get_db)):
    ranking = run_pipeline_ranking(db)
    return _to_response(ranking)


@router.get("/latest", response_model=PipelineRankingOut | None)
def get_latest_ranking(db: Session = Depends(get_db)):
    ranking = latest_pipeline_ranking(db)
    if ranking is None:
        return None
    return _to_response(ranking)
