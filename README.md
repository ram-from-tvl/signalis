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
- Ranks the whole pipeline by contact priority on demand, so a rep knows who
  to call first — not a raw score, but an explainable order with a reason
  per lead.

## Architecture

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy 2.x, SQLite, Pydantic v2.
- **Agent runtime**: every agent's reasoning step runs as a session/turn on a
  local TrueForge agent harness process rather than a bare model API call —
  TrueForge owns the actual agent loop (model calls, MCP tool discovery and
  execution, context management), and the backend only starts turns and
  reads back their structured output over TrueForge's HTTP API
  (`app/core/trueforge.py`). A LangGraph `StateGraph` still sits above this
  as the Python-side coordinator (`app/agents/graph.py`), wiring the five
  agent steps together with a real conditional edge on the buying-stage
  confidence score; what changed is that each *node* in that graph now
  delegates its reasoning to TrueForge instead of calling an LLM SDK
  directly.
- **MCP tools**: the Persona Fit agent calls a real remote MCP server
  (`app/mcp_tools/enrichment_server.py`, served over HTTP) exposing firmographic
  enrichment tools — `classify_company_industry` and
  `estimate_company_size_band` — so a lead's company data is enriched via a
  genuine tool call discovered and invoked through TrueForge's MCP layer,
  not a function call embedded in the agent's own Python code.
- **Sandboxed execution**: the buying-stage signal-strength score is computed
  by running generated Python inside a Daytona sandbox rather than as
  in-process business logic, with a local fallback computation if the
  sandbox is briefly unreachable. Which path actually ran is recorded on
  every buying-stage agent run.
- **LLM providers**: Google Gemini (`gemini-2.5-flash` by default,
  configurable via `GEMINI_MODEL`) is the primary model, registered with
  TrueForge as a native `google-gemini` provider. If TrueForge itself is not
  running, or a turn fails after retries, the same call falls back to a
  direct Gemini call and then to a Hugging Face Inference Providers model
  (`Qwen/Qwen3-4B-Instruct-2507` by default, configurable via `HF_MODEL`)
  using OpenAI-compatible tool calling — so the pipeline still produces real
  model reasoning even if the TrueForge sidecar or the primary provider is
  briefly unavailable. No agent's reasoning is hardcoded or templated at any
  layer of this fallback chain — every classification, fit assessment, plan,
  and narrative is a real model call over real data.
- **Human approval**: every generated outreach plan, and any stage
  classification the model itself was unconfident about, is persisted as
  `pending_approval` and requires an explicit approve/reject action through
  the API/UI before being treated as final. TrueForge separately supports a
  native per-tool approval checkpoint (`require_approval_for_tools`, which
  pauses a turn until a `user.tool_approval` is submitted) — this build does
  not gate the enrichment tool behind it, since the application-level
  plan/classification approval is the actual user-facing checkpoint that
  matters for this product, but the primitive is real and demonstrated in
  `docs/DECISIONS.md`.
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
    agents/        six agent modules (five-agent LangGraph pipeline plus the
                   standalone Prioritization/Ranking agent)
    api/routes/    FastAPI routers, one module per resource
    api/router.py  aggregates every router; app.main only mounts this one
    core/          config, the TrueForge HTTP client, the Gemini/HF LLM
                   fallback, the Daytona sandbox wrapper, and the one-time
                   TrueForge provider bootstrap script (trueforge_bootstrap.py)
    mcp_tools/     the remote MCP server exposing enrichment tools
    db/            SQLAlchemy session/base + demo data seeding
    models/        SQLAlchemy ORM models, one module per domain entity
    schemas/       Pydantic request/response schemas, one module per domain
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
- Node.js 22+ and npm (required by the TrueForge agent harness)
- A Gemini API key (https://ai.google.dev)
- Optional: a Hugging Face access token (https://huggingface.co/settings/tokens)
  for the LLM fallback, and a Daytona API key + sandbox (https://app.daytona.io)
  for sandboxed signal scoring. Both are genuinely optional — everything
  still runs correctly without them, just with less redundancy.

### 1. Configure environment variables

Create a `.env` file at the repository root (not inside `backend/`):

```
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-2.5-flash
DATABASE_URL=sqlite:///signalis.db

# Optional: used only if Gemini is unavailable or a request fails
HF_TOKEN=your-hugging-face-token
HF_MODEL=Qwen/Qwen3-4B-Instruct-2507:nscale

# Optional: used only for sandboxed signal-scoring execution; falls back to
# an equivalent local computation if unset or unreachable
DAYTONA_API_KEY=your-daytona-api-key
DAYTONA_API_URL=https://app.daytona.io/api
DAYTONA_SANDBOX_ID=your-sandbox-id

# Optional: point at a different local TrueForge instance, or set
# TRUEFORGE_ENABLED=false to skip the harness and call Gemini/HF directly
TRUEFORGE_URL=http://localhost:8790
TRUEFORGE_ENABLED=true
TRUEFORGE_MODEL=google-gemini/gemini-2-5-flash
```

### 2. Start the TrueForge agent harness

```bash
npx @truefoundry/trueforge@latest --port 8790
```

This starts a local, SQLite-backed TrueForge instance on `http://localhost:8790`
(first run downloads the package). Leave it running in its own terminal.

### 3. Start the enrichment MCP server

```bash
cd backend
source venv/bin/activate   # after step 4 below has created the venv
python -m app.mcp_tools.enrichment_server
```

This serves the firmographic enrichment tools at `http://127.0.0.1:8791/mcp`.
Leave it running in its own terminal.

### 4. Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install fastapi "uvicorn[standard]" sqlalchemy pydantic pydantic-settings \
  python-dotenv python-multipart langgraph google-genai daytona mcp pytest httpx ruff
python -m app.core.trueforge_bootstrap   # registers model/sandbox/MCP providers with TrueForge
uvicorn app.main:app --reload
```

The API is now served at `http://localhost:8000`, with interactive docs at
`http://localhost:8000/docs`. Tables are created automatically on startup.
Individual TrueForge agents (one per Signalis agent) register themselves
automatically the first time each one runs.

If TrueForge or the MCP server is not running, every agent call still works —
`app/agents/common.run_agent_reasoning` catches the transport failure and
falls back to a direct Gemini/Hugging-Face call — but the MCP tool call and
the TrueForge-native agent loop will not be exercised in that case.

### 5. Load sample data (optional but recommended for a first run)

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

### 6. Frontend

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
- **Real MCP tool use**: With TrueForge and the enrichment MCP server running
  (setup steps 2-3), any pipeline run's Persona Fit step genuinely discovers
  and calls the `classify_company_industry` / `estimate_company_size_band`
  tools over MCP. This is visible directly in TrueForge's own session events
  (`GET /api/v1/sessions/{id}/events` on the TrueForge harness, port 8790)
  as real `mcp.initialize` and tool-call events, not just in the agent's
  final answer.
- **Sandboxed code execution**: Every Buying Stage Orchestrator run computes
  its recency/strength-weighted signal score by executing generated Python
  inside the configured Daytona sandbox; `agent_runs.output.signal_score_computed_via`
  records `"daytona"` or `"local"` depending on which path actually ran for
  that request.
- **Human approval as a harness primitive**: separately from the
  application-level approval workflow above, TrueForge's own
  `require_approval_for_tools` mechanism (a real per-tool approval gate that
  pauses a turn with `required_actions: [{"type": "tool.approval_required"}]`
  until a `user.tool_approval` turn input is submitted) is demonstrated in
  `docs/DECISIONS.md` as a capability of the runtime, distinct from the
  product-level checkpoint the UI exposes.
- **Pipeline-wide prioritization**: On the Dashboard, click "Rank Pipeline"
  to run the Prioritization/Ranking Agent over every currently-classified
  lead in one call. It returns a single contact-priority order with a
  specific reason per lead — e.g. a high-confidence, recently-active mid-stage
  lead can legitimately outrank a low-confidence late-stage one — not a
  naive sort by stage label.

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
