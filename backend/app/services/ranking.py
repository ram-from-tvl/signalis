"""Runs the Prioritization/Ranking Agent over every currently-classified lead
and persists the result as a new PipelineRanking snapshot.

Boundary between the stateless agent (app.agents.prioritization) and the
database, same pattern as app.services.pipeline for the per-lead agents.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.prioritization import run_prioritization
from app.models import Lead, PipelineRanking, StageClassification


def _current_classifications(db: Session) -> list[tuple[Lead, StageClassification]]:
    rows = db.execute(
        select(Lead, StageClassification)
        .join(StageClassification, StageClassification.lead_id == Lead.id)
        .where(StageClassification.superseded_by_id.is_(None))
    ).all()
    return [(lead, classification) for lead, classification in rows]


def run_pipeline_ranking(db: Session) -> PipelineRanking:
    pairs = _current_classifications(db)
    result: dict[str, Any] = run_prioritization(db, pairs)

    ranking_row = PipelineRanking(
        agent_run_id=result.get("agent_run_id"),
        ranked_leads=result.get("ranking", []),
        summary=result.get("summary", ""),
        subagent_delegation=result.get("subagent_delegation"),
    )
    db.add(ranking_row)
    db.commit()
    db.refresh(ranking_row)
    return ranking_row


def latest_pipeline_ranking(db: Session) -> PipelineRanking | None:
    return db.execute(
        select(PipelineRanking).order_by(PipelineRanking.created_at.desc())
    ).scalars().first()
