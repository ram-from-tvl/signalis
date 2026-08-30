from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Campaign, Lead, Persona, Solution
from app.schemas import CampaignCreate, CampaignDetail, CampaignOut

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


@router.get("", response_model=list[CampaignDetail])
def list_campaigns(db: Session = Depends(get_db)):
    campaigns = db.execute(select(Campaign).order_by(Campaign.created_at.desc())).scalars().all()
    lead_counts = dict(
        db.execute(
            select(Lead.campaign_id, func.count(Lead.id)).group_by(Lead.campaign_id)
        ).all()
    )
    return [
        CampaignDetail.model_validate(
            {
                **CampaignOut.model_validate(c).model_dump(),
                "persona": c.persona,
                "solution": c.solution,
                "lead_count": lead_counts.get(c.id, 0),
            }
        )
        for c in campaigns
    ]


@router.post("", response_model=CampaignOut, status_code=201)
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db)):
    if not db.get(Persona, payload.persona_id):
        raise HTTPException(400, "persona_id does not refer to an existing persona")
    if not db.get(Solution, payload.solution_id):
        raise HTTPException(400, "solution_id does not refer to an existing solution")

    campaign = Campaign(**payload.model_dump())
    if campaign.is_default:
        _clear_existing_default(db)
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


@router.get("/{campaign_id}", response_model=CampaignDetail)
def get_campaign(campaign_id: str, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    lead_count = db.execute(
        select(func.count(Lead.id)).where(Lead.campaign_id == campaign_id)
    ).scalar_one()
    return CampaignDetail.model_validate(
        {
            **CampaignOut.model_validate(campaign).model_dump(),
            "persona": campaign.persona,
            "solution": campaign.solution,
            "lead_count": lead_count,
        }
    )


@router.put("/{campaign_id}", response_model=CampaignOut)
def update_campaign(campaign_id: str, payload: CampaignCreate, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    if not db.get(Persona, payload.persona_id):
        raise HTTPException(400, "persona_id does not refer to an existing persona")
    if not db.get(Solution, payload.solution_id):
        raise HTTPException(400, "solution_id does not refer to an existing solution")

    if payload.is_default and not campaign.is_default:
        _clear_existing_default(db)
    for key, value in payload.model_dump().items():
        setattr(campaign, key, value)
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


@router.delete("/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: str, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    assigned_leads = db.execute(
        select(func.count(Lead.id)).where(Lead.campaign_id == campaign_id)
    ).scalar_one()
    if assigned_leads:
        raise HTTPException(
            409,
            f"{assigned_leads} lead(s) are still assigned to this campaign — "
            "reassign them to a different campaign before deleting it.",
        )
    db.delete(campaign)
    db.commit()


class AssignLeadsRequest(BaseModel):
    lead_ids: list[str]


@router.post("/{campaign_id}/assign-leads", response_model=CampaignDetail)
def assign_leads(campaign_id: str, payload: AssignLeadsRequest, db: Session = Depends(get_db)):
    """Moves the given leads onto this campaign — the point where a
    marketer decides "these leads are being worked as part of this
    targeting config" rather than the whole pipeline sharing one config."""
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")

    leads = db.execute(select(Lead).where(Lead.id.in_(payload.lead_ids))).scalars().all()
    found_ids = {lead.id for lead in leads}
    missing = set(payload.lead_ids) - found_ids
    if missing:
        raise HTTPException(400, f"Lead id(s) not found: {', '.join(sorted(missing))}")

    for lead in leads:
        lead.campaign_id = campaign.id
        db.add(lead)
    db.commit()

    lead_count = db.execute(
        select(func.count(Lead.id)).where(Lead.campaign_id == campaign_id)
    ).scalar_one()
    db.refresh(campaign)
    return CampaignDetail.model_validate(
        {
            **CampaignOut.model_validate(campaign).model_dump(),
            "persona": campaign.persona,
            "solution": campaign.solution,
            "lead_count": lead_count,
        }
    )


def _clear_existing_default(db: Session) -> None:
    """Only one campaign should be is_default at a time (see the note on
    Campaign.is_default) — application-enforced since SQLite has no
    partial-unique-index support for this."""
    for existing_default in db.execute(
        select(Campaign).where(Campaign.is_default.is_(True))
    ).scalars().all():
        existing_default.is_default = False
        db.add(existing_default)
