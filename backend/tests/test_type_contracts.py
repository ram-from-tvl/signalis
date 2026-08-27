"""Guards against the OutreachPlanOut/OutreachPlan (frontend) API shape
silently drifting apart. There is no schema-generation pipeline in this
project (see docs/DECISIONS.md for why one wasn't pulled in for a handful
of fields); this is the proportionate alternative — a CI-enforced diff of
both field names and (normalized) types between the two hand-maintained
declarations, so a field added, removed, or retyped in one and forgotten in
the other fails a test instead of shipping unnoticed."""
from __future__ import annotations

import datetime
import re
import types
from pathlib import Path

from app.schemas.classification import OutreachPlanOut

_FRONTEND_TYPES_PATH = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "types" / "api.ts"
)

# Maps a normalized Python type annotation to the TS type it must appear as.
# Only covers the annotations OutreachPlanOut actually uses; extend if a new
# field introduces a type not listed here.
_PY_TO_TS: dict[str, str] = {
    "str": "string",
    "str | None": "string | null",
    "datetime.datetime": "string",
}

# Fields where the frontend intentionally narrows the backend's plain `str`
# to a string-literal union (e.g. PlanStatus) — not drift, since every value
# the backend can send is still a valid TS string.
_NARROWED_STRING_FIELDS = {"status"}


def _normalize_py_annotation(annotation: object) -> str:
    if annotation is str:
        return "str"
    if annotation is datetime.datetime:
        return "datetime.datetime"
    if isinstance(annotation, types.UnionType):
        args = annotation.__args__
        if len(args) == 2 and type(None) in args:
            other = args[0] if args[1] is type(None) else args[1]
            return f"{_normalize_py_annotation(other)} | None"
    return str(annotation)


def _backend_outreach_plan_field_types() -> dict[str, str]:
    return {
        name: _PY_TO_TS[_normalize_py_annotation(field.annotation)]
        for name, field in OutreachPlanOut.model_fields.items()
        # list/dict fields (touchpoints, channels, messaging_examples) aren't
        # in _PY_TO_TS; skip type comparison for them, name-presence is still
        # checked via the set-equality assertion below.
        if _normalize_py_annotation(field.annotation) in _PY_TO_TS
    }


def _frontend_outreach_plan_fields() -> dict[str, str]:
    source = _FRONTEND_TYPES_PATH.read_text()
    match = re.search(r"export interface OutreachPlan \{(.*?)\}", source, re.DOTALL)
    assert match, "OutreachPlan interface not found in frontend/src/types/api.ts"
    body = match.group(1)
    fields = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("//") or ":" not in line:
            continue
        name, ts_type = line.split(":", 1)
        fields[name.strip()] = ts_type.strip()
    return fields


def test_outreach_plan_frontend_type_matches_backend_schema_field_names():
    backend_fields = set(OutreachPlanOut.model_fields.keys())
    frontend_fields = set(_frontend_outreach_plan_fields().keys())
    assert backend_fields == frontend_fields, (
        f"OutreachPlanOut and frontend OutreachPlan have drifted: "
        f"backend-only={backend_fields - frontend_fields}, "
        f"frontend-only={frontend_fields - backend_fields}"
    )


def test_outreach_plan_frontend_type_matches_backend_schema_field_types():
    backend_types = _backend_outreach_plan_field_types()
    frontend_fields = _frontend_outreach_plan_fields()
    mismatched = {
        name: (expected_ts, frontend_fields.get(name))
        for name, expected_ts in backend_types.items()
        if name not in _NARROWED_STRING_FIELDS and frontend_fields.get(name) != expected_ts
    }
    assert not mismatched, (
        f"OutreachPlanOut and frontend OutreachPlan field types have drifted "
        f"(field: (expected_ts, actual_ts)): {mismatched}"
    )
