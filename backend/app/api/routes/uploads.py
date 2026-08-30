from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Campaign
from app.schemas import IngestionReportOut
from app.services.ingestion import ingest_crm_csv, ingest_website_events_json

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post("/crm-csv", response_model=IngestionReportOut)
async def upload_crm_csv(
    file: UploadFile = File(...),
    campaign_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Expected a .csv file")

    if campaign_id:
        if not db.get(Campaign, campaign_id):
            raise HTTPException(400, "campaign_id does not refer to an existing campaign")
        resolved_campaign_id = campaign_id
    else:
        # No campaign specified — resolve to the default now, rather than
        # passing None through and leaving newly-created leads genuinely
        # campaign-less (they'd only get the default transiently at
        # pipeline-run time, so campaign badges/filtering would treat them
        # as unassigned until the next startup backfill).
        default_campaign = db.execute(
            select(Campaign).where(Campaign.is_default.is_(True))
        ).scalars().first()
        resolved_campaign_id = default_campaign.id if default_campaign else None

    content = await file.read()
    if not content:
        raise HTTPException(400, "Uploaded file is empty")
    report = ingest_crm_csv(db, content, campaign_id=resolved_campaign_id)
    return report


@router.post("/website-events", response_model=IngestionReportOut)
async def upload_website_events(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename or not file.filename.lower().endswith(".json"):
        raise HTTPException(400, "Expected a .json file")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Uploaded file is empty")
    try:
        events = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"Invalid JSON: {exc}") from exc
    report = ingest_website_events_json(db, events)
    return report
