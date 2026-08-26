"""Aggregates every route module under a single router.

Adding a new route module only requires registering it here — app.main no
longer needs to import each router individually.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    agent_followups,
    approvals,
    dashboard,
    leads,
    personas,
    pipeline,
    ranking,
    solutions,
    uploads,
)

api_router = APIRouter()

for module in (personas, solutions, uploads, leads, pipeline, approvals, dashboard, ranking, agent_followups):
    api_router.include_router(module.router)
