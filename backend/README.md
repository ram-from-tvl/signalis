# Signalis backend

FastAPI service orchestrating the five-agent LangGraph pipeline over
TrueForge, plus the standalone Prioritization/Ranking agent. See
[../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) for the system-level
picture and [../README.md](../README.md) for full first-time setup
(environment variables, TrueForge harness, MCP server).

## Directory layout

```
app/
  agents/        The five pipeline agents (signal_extraction, persona_fit,
                 buying_stage, outreach_planner, explainability) plus the
                 standalone prioritization agent, wired together in
                 graph.py. common.py holds shared agent-run tracking.
  api/routes/    One FastAPI router module per resource, registered
                 directly in app/main.py.
  core/
    config.py    Settings, read from environment/.env.
    llm.py       Direct Gemini/Hugging Face call path (the fallback used
                 when TrueForge itself is unavailable).
    trueforge.py HTTP client for the TrueForge harness — starts turns,
                 polls for completion, reads back structured output.
    trueforge_setup.py
                 One-time script registering model/sandbox/MCP providers
                 with a running TrueForge instance. Run once after
                 TrueForge starts: `python -m app.core.trueforge_setup`.
    sandbox.py   Daytona sandbox wrapper for signal-strength scoring, with
                 a local fallback if the sandbox is unreachable.
  db/
    base.py      SQLAlchemy declarative Base.
    session.py   Engine and sessionmaker.
    seed_demo.py Loads the bundled sample dataset.
  mcp_tools/
    enrichment_server.py
                 Remote MCP server exposing firmographic enrichment tools,
                 called by the Persona Fit agent through TrueForge.
  models/entities.py
                 All SQLAlchemy ORM models.
  schemas/schemas.py
                 All Pydantic request/response schemas.
  services/      ingestion.py (CSV/JSON parsing), pipeline.py (graph
                 orchestration), ranking.py (prioritization orchestration).
  main.py        FastAPI app instantiation, CORS, startup hook, and
                 route registration.
data/            Bundled sample CRM CSV and website events JSON.
tests/           pytest suite — unit, API, and one real-Gemini integration
                 test (marked `integration`, skipped by default).
```

> A structural refactor splitting `models/entities.py` and
> `schemas/schemas.py` into per-domain modules, and adding an
> `app/api/router.py` aggregator, is tracked separately. This document
> reflects the current layout on `main`; it will be updated in the same
> pull request that lands that refactor.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install fastapi "uvicorn[standard]" sqlalchemy pydantic pydantic-settings \
  python-dotenv python-multipart langgraph google-genai daytona mcp pytest httpx ruff
```

See [../README.md](../README.md) for the required `.env` file at the
repository root.

## Running

```bash
uvicorn app.main:app --reload
```

The API is served at `http://localhost:8000`, with interactive docs at
`/docs` (Swagger UI) and the raw schema at `/openapi.json`.

## Testing

```bash
pytest -m "not integration" -q      # fast, LLM calls mocked
pytest -m integration -q            # real end-to-end Gemini call, needs GEMINI_API_KEY
```

## Linting

```bash
ruff check app tests
```

Configuration lives in `pyproject.toml` under `[tool.ruff]`.

## Adding a new route

1. Add a new module under `app/api/routes/`, following the pattern of an
   existing one (an `APIRouter` with a `prefix`, request/response models
   from `app/schemas/schemas.py`, DB access via `app.api.deps.get_db`).
2. Import it and call `app.include_router(...)` in `app/main.py`.
3. Add a corresponding section to [../docs/API.md](../docs/API.md).

## Adding a new model or schema

Add the model to `app/models/entities.py` and the schema to
`app/schemas/schemas.py`, following the existing classes as a pattern, then
update [../docs/DATA_SCHEMA.md](../docs/DATA_SCHEMA.md) for any new table.
