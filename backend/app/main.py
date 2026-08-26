from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect

from app.api.router import api_router
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import engine

settings = get_settings()
logger = logging.getLogger("signalis.startup")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # This project has no Alembic/versioned migration setup (see
    # docs/DECISIONS.md): every model's table is created opportunistically
    # here via create_all(), which only ever adds tables/columns that don't
    # exist yet and never alters or drops existing ones — safe to run on
    # every boot, including against an already-initialized production
    # database. That additive-only property is also its limit: it has no way
    # to express a genuine schema change (renaming/dropping a column,
    # altering a type, backfilling data), so a future change that needs one
    # of those will require introducing a real migration tool at that point.
    #
    # Logging which tables create_all() is about to add gives that startup
    # step a reviewable trail in ops logs, which is the main thing a fully
    # unversioned schema story is missing today.
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    new_tables = [name for name in Base.metadata.tables if name not in existing_tables]
    if new_tables:
        logger.info("Creating new database table(s) on startup: %s", ", ".join(sorted(new_tables)))
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Signalis",
    description="Agentic buying signal copilot API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api_router)
