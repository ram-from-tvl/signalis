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
from app.db.migrations import backfill_default_campaign, run_startup_migrations
from app.db.session import SessionLocal, engine

settings = get_settings()
logger = logging.getLogger("signalis.startup")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # No Alembic/versioned migrations: create_all() only ever adds
    # tables/columns that don't exist, safe on every boot. Logging which
    # tables it's about to add gives this unversioned setup a reviewable
    # startup trail.
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    new_tables = [name for name in Base.metadata.tables if name not in existing_tables]
    if new_tables:
        logger.info("Creating new database table(s) on startup: %s", ", ".join(sorted(new_tables)))
    Base.metadata.create_all(bind=engine)
    run_startup_migrations(engine)
    with SessionLocal() as db:
        backfill_default_campaign(db)
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
