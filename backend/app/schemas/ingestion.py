from __future__ import annotations

from pydantic import BaseModel


class IngestionReportOut(BaseModel):
    rows_parsed: int
    rows_skipped: int
    skipped_reasons: list[str]
    leads_created: int
    leads_updated: int
    signals_created: int
