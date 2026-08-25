from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import approvals, dashboard, leads, personas, pipeline, ranking, solutions, uploads
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import engine

settings = get_settings()

app = FastAPI(
    title="Signalis",
    description="Agentic buying signal copilot API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/api/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(personas.router)
app.include_router(solutions.router)
app.include_router(uploads.router)
app.include_router(leads.router)
app.include_router(pipeline.router)
app.include_router(approvals.router)
app.include_router(dashboard.router)
app.include_router(ranking.router)
