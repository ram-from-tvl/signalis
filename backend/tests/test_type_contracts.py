"""Guards against the OutreachPlanOut/OutreachPlan (frontend) API shape
silently drifting apart. There is no schema-generation pipeline in this
project (see docs/DECISIONS.md for why one wasn't pulled in for a handful
of fields); this is the proportionate alternative — a CI-enforced field-name
diff between the two hand-maintained declarations, so a field added to one
and forgotten in the other fails a test instead of shipping unnoticed."""
from __future__ import annotations

import re
from pathlib import Path

from app.schemas.classification import OutreachPlanOut

_FRONTEND_TYPES_PATH = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "types" / "api.ts"
)


def _frontend_outreach_plan_fields() -> set[str]:
    source = _FRONTEND_TYPES_PATH.read_text()
    match = re.search(r"export interface OutreachPlan \{(.*?)\}", source, re.DOTALL)
    assert match, "OutreachPlan interface not found in frontend/src/types/api.ts"
    body = match.group(1)
    # Field lines look like "  verified_email: string | null" — comment-only
    # lines (no colon) are cross-reference notes, not fields.
    return {
        line.split(":", 1)[0].strip()
        for line in body.splitlines()
        if ":" in line and not line.strip().startswith("//")
    }


def test_outreach_plan_frontend_type_matches_backend_schema():
    backend_fields = set(OutreachPlanOut.model_fields.keys())
    frontend_fields = _frontend_outreach_plan_fields()
    assert backend_fields == frontend_fields, (
        f"OutreachPlanOut and frontend OutreachPlan have drifted: "
        f"backend-only={backend_fields - frontend_fields}, "
        f"frontend-only={frontend_fields - backend_fields}"
    )
