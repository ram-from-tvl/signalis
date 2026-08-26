"""Aggregates every route module under a single router.

Adding a new route module only requires registering it here — app.main no
longer needs to import each router individually.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    approvals,
    dashboard,
    leads,
    personas,
    pipeline,
    ranking,
    solutions,
    tool_approvals,
    uploads,
)

api_router = APIRouter()

for module in (personas, solutions, uploads, leads, pipeline, approvals, tool_approvals, dashboard, ranking):
    api_router.include_router(module.router)
