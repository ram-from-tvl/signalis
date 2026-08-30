"""CRM CSV and website-event JSON ingestion.

Both parsers are deliberately defensive: a malformed row is skipped and
reported rather than aborting the whole upload, per the "handle messy data
gracefully" requirement.
"""
from __future__ import annotations

import csv
import datetime
import io
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Lead, Signal

REQUIRED_CRM_FIELDS = ("name", "company")


@dataclass
class IngestionReport:
    rows_parsed: int = 0
    rows_skipped: int = 0
    skipped_reasons: list[str] = field(default_factory=list)
    leads_created: int = 0
    leads_updated: int = 0
    signals_created: int = 0


def parse_date(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    value = value.strip()
    formats = (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y",
    )
    for fmt in formats:
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _find_lead_by_identifier(db: Session, *, email: str | None, name: str | None, company: str | None) -> Lead | None:
    if email:
        found = db.execute(select(Lead).where(Lead.email == email)).scalars().first()
        if found:
            return found
    if name and company:
        return db.execute(
            select(Lead).where(Lead.name == name, Lead.company == company)
        ).scalars().first()
    return None


def ingest_crm_csv(db: Session, file_content: bytes, campaign_id: str | None = None) -> IngestionReport:
    """`campaign_id` assigns every newly-created lead from this upload to a
    specific campaign — the point where "who is this batch of leads for"
    gets decided, rather than every lead implicitly sharing one global
    persona/solution. A lead that already existed keeps its current
    campaign_id unchanged (re-uploading the same CRM export shouldn't
    silently move a lead to a different campaign)."""
    report = IngestionReport()
    text = file_content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        report.skipped_reasons.append("File has no header row; nothing to parse.")
        return report

    known_activity_cols = {
        "deal_stage",
        "last_activity",
        "last_activity_date",
        "notes",
        "deal_value",
    }

    for row_num, row in enumerate(reader, start=2):
        report.rows_parsed += 1
        name = (row.get("name") or "").strip()
        company = (row.get("company") or "").strip()

        if not name or not company:
            report.rows_skipped += 1
            report.skipped_reasons.append(
                f"Row {row_num}: missing required field(s) name/company; skipped."
            )
            continue

        email = (row.get("email") or "").strip()
        lead = _find_lead_by_identifier(db, email=email or None, name=name, company=company)
        if lead is None:
            lead = Lead(
                name=name,
                company=company,
                title=(row.get("title") or "").strip(),
                company_size=(row.get("company_size") or "").strip(),
                industry=(row.get("industry") or "").strip(),
                geography=(row.get("geography") or "").strip(),
                email=email,
                campaign_id=campaign_id,
            )
            db.add(lead)
            db.flush()
            report.leads_created += 1
        else:
            report.leads_updated += 1

        activity_payload: dict[str, Any] = {
            k: v for k, v in row.items() if k in known_activity_cols and v
        }
        occurred_at = parse_date(row.get("last_activity_date")) or datetime.datetime.utcnow()

        if activity_payload:
            signal = Signal(
                lead_id=lead.id,
                raw_source="crm",
                raw_payload=activity_payload,
                event_type="unknown",
                intent_stage_hint="early",
                occurred_at=occurred_at,
            )
            db.add(signal)
            report.signals_created += 1

    db.commit()
    return report


def ingest_website_events_json(db: Session, events: list[dict[str, Any]]) -> IngestionReport:
    report = IngestionReport()

    if not isinstance(events, list):
        report.skipped_reasons.append("Expected a JSON array of event objects.")
        return report

    for idx, event in enumerate(events):
        report.rows_parsed += 1
        if not isinstance(event, dict):
            report.rows_skipped += 1
            report.skipped_reasons.append(f"Event {idx}: not an object; skipped.")
            continue

        lead_identifier = event.get("lead_email") or event.get("email") or event.get("lead_id")
        name = event.get("name") or event.get("lead_name")
        company = event.get("company")

        if not lead_identifier and not (name and company):
            report.rows_skipped += 1
            report.skipped_reasons.append(
                f"Event {idx}: no lead identifier (lead_email/lead_id or name+company); skipped."
            )
            continue

        lead = _find_lead_by_identifier(
            db,
            email=lead_identifier if lead_identifier and "@" in str(lead_identifier) else None,
            name=name,
            company=company,
        )
        if lead is None:
            # Fall back to matching purely on email-like identifier already stored.
            lead = db.execute(select(Lead).where(Lead.email == lead_identifier)).scalars().first()

        if lead is None:
            report.rows_skipped += 1
            report.skipped_reasons.append(
                f"Event {idx}: no matching lead found for identifier '{lead_identifier}'; skipped."
            )
            continue

        page = event.get("page", "")
        event_type_hint = event.get("event_type", "page_view")
        occurred_at = parse_date(event.get("timestamp")) or datetime.datetime.utcnow()

        signal = Signal(
            lead_id=lead.id,
            raw_source="website",
            raw_payload={"page": page, "event_type": event_type_hint, **{
                k: v for k, v in event.items() if k not in ("page", "event_type")
            }},
            event_type="unknown",
            intent_stage_hint="early",
            occurred_at=occurred_at,
        )
        db.add(signal)
        report.signals_created += 1

    db.commit()
    return report
