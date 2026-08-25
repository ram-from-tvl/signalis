# Signalis

Signalis is an agentic buying signal copilot for marketing and SDR teams. It
ingests lead activity from a CRM-style export and a website event log,
figures out where each lead sits in the buying journey with explainable,
LLM-generated reasoning, and produces a tailored outreach micro-plan for each
lead — updating its understanding automatically whenever new signals arrive.

## What it does

- Parses raw CRM rows and website events into normalized, stage-tagged
  signals, tolerating missing or malformed data instead of failing.
- Scores each lead's fit against a marketer-defined persona and solution
  ICP, flagging mismatches and missing data with reasoning attached.
- Aggregates a lead's signal history, weighing recency and signal strength,
  into an overall buying stage (early / mid / late) with a confidence score
  and a plain-language justification.
- Generates a 1-2 week outreach micro-plan (touchpoints, channels, content
  themes, example message copy) tailored to that stage, persona fit, and
  solution positioning.
- Synthesizes a narrative explanation of the full reasoning chain for every
  pipeline run, forming a visible, ordered multi-agent trace.
- Enforces a human-approval checkpoint: every generated outreach plan starts
  as `pending_approval`, and any classification the model itself is
  unconfident about is additionally routed for explicit approval before it
  is treated as final.
- Re-runs its full reasoning chain on demand when new signals arrive for a
  lead, producing an updated classification and a regenerated plan without
  a human re-deriving anything from scratch.

## Architecture

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy 2.x, SQLite, Pydantic v2.
- **Agent orchestration**: LangGraph `StateGraph` wiring five agent nodes
  with a real conditional edge on the buying-stage confidence score.
- **LLM**: Google Gemini (`gemini-2.5-flash` by default, configurable via
  `GEMINI_MODEL`) through the `google-genai` SDK, using structured JSON
  response schemas for every agent call. No agent's reasoning is hardcoded
  or templated — every classification, fit assessment, plan, and narrative
  is a real model call over real data.
- **Frontend**: React 18 + Vite + TypeScript, Tailwind CSS, a component
  library built on Radix primitives in the shadcn/ui pattern (owned in this
  codebase, not an installed black box), React Router, TanStack Query,
  Recharts, and Motion for a handful of purposeful transitions.

See `docs/AGENT_GRAPH.md` for the full agent graph diagram and handoff
description, `docs/DATA_SCHEMA.md` for the database schema, `docs/API.md`
for the endpoint reference, `docs/DECISIONS.md` for design trade-offs and how
ambiguity in the brief was resolved, and `docs/TIME_SAVINGS.md` for the
manual-vs-agent time comparison.

## Repository layout

```
backend/
  app/
    agents/        five agent modules + the LangGraph graph definition
    api/routes/    FastAPI routers
    core/          config and the single Gemini client wrapper
    db/            SQLAlchemy session/base + demo data seeding
    models/        SQLAlchemy ORM models
    schemas/       Pydantic request/response schemas
    services/      ingestion + pipeline orchestration services
  data/            bundled sample CRM CSV and website events JSON
  tests/           pytest suite (unit, API, one real-Gemini integration test)
frontend/
  src/
    api/           typed API client
    components/    ui/ (shadcn-pattern primitives), layout/, leads/
    pages/         the six application screens
    types/         shared TypeScript types matching the backend schemas
docs/              all required documentation deliverables
.github/workflows/ CI: lint + test + build gate on every pull request
```

## Setup from a clean checkout

### Prerequisites
- Python 3.11+
- Node.js 18+ and npm
- A Gemini API key (https://ai.google.dev)

### 1. Configure environment variables

Create a `.env` file at the repository root (not inside `backend/`):

```
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-2.5-flash
DATABASE_URL=sqlite:///signalis.db
```

### 2. Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install fastapi "uvicorn[standard]" sqlalchemy pydantic pydantic-settings \
  python-dotenv python-multipart langgraph google-genai pytest httpx ruff
uvicorn app.main:app --reload
```

The API is now served at `http://localhost:8000`, with interactive docs at
`http://localhost:8000/docs`. Tables are created automatically on startup.

### 3. Load sample data (optional but recommended for a first run)

```bash
cd backend
source venv/bin/activate
python -m app.db.seed_demo
```

This creates a default persona and solution, then ingests
`backend/data/sample_crm_leads.csv` (20 synthetic leads spanning early, mid,
and late-stage signals, including intentionally messy rows) and
`backend/data/sample_website_events.json` (44 synthetic website events over
the last month). The same files can also be loaded from the frontend's Data
Sources screen via the "Use Sample Data" button, which uploads them through
the same API endpoints a real CSV/JSON upload would use.

### 4. Frontend

```bash
cd frontend
npm install
cp .env.example .env   # defaults to http://localhost:8000, adjust if needed
npm run dev
```

The app is served at `http://localhost:5173`.

## Exercising each agentic behavior requirement

- **Autonomous pull + summarize**: On the Pipeline screen, click "Run
  Pipeline for All Leads" to trigger the full five-agent graph for every
  lead in one action, with no per-row manual classification.
- **Live re-classification on new signals**: On a lead's detail page, click
  "Simulate New Signal" to append a fresh high-intent website event (a
  pricing page visit) and automatically re-run the pipeline; the stage,
  confidence, and justification update immediately and the classification
  history tab shows the prior classification marked superseded.
- **Plan regeneration**: The "Regenerate Plan" button on the lead detail
  page re-runs the graph for that lead and produces a new
  `pending_approval` outreach plan reflecting current state; the previous
  plan is marked `superseded`, not deleted.
- **Human-approval checkpoint**: Every newly generated outreach plan appears
  with an Approve/Reject action in the Outreach Plan tab before it is
  considered final. A classification the model itself was unconfident about
  additionally shows its own Approve/Reject action above the plan.
- **Visible multi-agent trace**: The Agent Trace tab on the lead detail page
  lists every `agent_runs` row for that lead in order, each visually
  distinguished by agent, with its input summary and full reasoning text —
  this is real persisted data, inspectable after the fact, not a live-only
  view.

## Running tests

```bash
cd backend
source venv/bin/activate
pytest -m "not integration" -q      # fast unit + API tests, Gemini calls mocked
pytest -m integration -q            # real end-to-end Gemini call (needs GEMINI_API_KEY)
```

## Linting

```bash
cd backend && ruff check app tests
cd frontend && npx eslint .
```
