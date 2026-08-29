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
- **MCP tools**: four real remote MCP servers, all genuine tool calls
  discovered and invoked through TrueForge's MCP layer, not function calls
  embedded in an agent's own Python code, each served over HTTP.
  `app/mcp_tools/enrichment_server.py` exposes firmographic enrichment tools
  — `classify_company_industry` and `estimate_company_size_band`.
  `app/mcp_tools/research_server.py` exposes `search_company_news` via the
  live Tavily search API (https://tavily.com). `app/mcp_tools/exa_server.py`
  exposes `search_company_semantic` via Exa (https://exa.ai) — a
  differently-sourced, semantic/company-focused search complementing
  Tavily's broad web search. All three are attached to the Persona Fit
  agent, which decides when a second or third read on external context
  would sharpen its fit assessment. `app/mcp_tools/hunter_server.py`
  exposes `find_email`/`verify_email` via Hunter.io (https://hunter.io),
  attached to the Outreach Planner so a generated plan records whether the
  lead's email is actually deliverable before a rep sends anything.
- **Sandboxed execution**: the buying-stage signal-strength score is computed
  by running generated Python inside a Daytona sandbox rather than as
  in-process business logic, with a local fallback computation if the
  sandbox is briefly unreachable. Which path actually ran is recorded on
  every buying-stage agent run.
- **LLM providers**: Hugging Face Inference Providers (`Qwen/Qwen3-4B-Instruct-2507`
  by default, configurable via `HF_MODEL`) is the primary model, registered
  with TrueForge as a `custom` OpenAI-compatible provider. Up to three HF API
  keys can be configured (`HF_TOKEN`, `HF_TOKEN_1`, `HF_TOKEN_2`), each
  registered with TrueForge as its own named provider; a quota/auth failure
  (402/429/401/403) on one key rotates to the next before falling back
  further. Google Gemini (`gemini-3.6-flash` by default, configurable via
  `GEMINI_MODEL`) is registered as the final fallback provider (also
  supporting up to two keys via `GEMINI_API_KEY`/`GEMINI_API_KEY_1`, with the
  same rotation). Three tiers of fallback exist end to end: `run_agent_reasoning`
  (`app/agents/common.py`) first tries every TrueForge-registered model in
  order (`Settings.trueforge_models`: `TRUEFORGE_MODEL`, `TRUEFORGE_MODEL_1`,
  `TRUEFORGE_MODEL_2`, then `TRUEFORGE_MODEL_FALLBACK`) — skipping the
  rotation only if a turn already executed real side effects (an MCP tool
  call, a subagent fan-out) before failing, so a retry never repeats them
  (`TrueForgeTurnExecutedError`). If every TrueForge-registered model fails,
  or TrueForge itself is not running, the same call falls back to a direct
  HF call (with the same key rotation) and then a direct Gemini call — so the
  pipeline still produces real model reasoning even if the TrueForge sidecar
  or every registered provider is briefly unavailable. No agent's reasoning
  is hardcoded or templated at any layer of this fallback chain — every
  classification, fit assessment, plan, and narrative is a real model call
  over real data.
- **Human approval**: every generated outreach plan, and any stage
  classification the model itself was unconfident about, is persisted as
  `pending_approval` and requires an explicit approve/reject action through
  the API/UI before being treated as final. TrueForge's native per-tool
  approval checkpoint (`require_approval_for_tools`) is also wired in: the
  Persona Fit agent's enrichment tool calls pause the turn until a marketer
  approves or rejects them via `POST /api/tool-approvals/{id}/approve|reject`,
  independent of the plan/classification checkpoint above.
- **Subagents**: the Prioritization/Ranking agent delegates per-lead priority
  assessment to TrueForge's built-in `create_sub_agent` tool, run in
  parallel, then consolidates every subagent's result in the root agent's
  own context to produce the final cross-lead ranking. Delegation evidence
  (from TrueForge's session event stream) is persisted and surfaced on the
  ranking API response.
- **Skills**: the Outreach Planner's copywriting guidance lives in a
  TrueForge skill (`app/agents/skills/outreach_copywriting_style_guide/`)
  loaded on demand rather than re-sent in every prompt.
- **Persistent sessions**: a marketer can ask a natural-language follow-up
  question against any trace entry that ran through TrueForge
  (`POST /api/leads/{lead_id}/agent-runs/{agent_run_id}/ask`), answered as a
  genuine continuation turn on that run's own TrueForge session rather than
  a fresh one-shot call.
- **Frontend**: React 18 + Vite + TypeScript, Tailwind CSS, a component
  library built on Radix primitives in the shadcn/ui pattern (owned in this
  codebase, not an installed black box), React Router, TanStack Query,
  Recharts, and Motion for a handful of purposeful transitions.

See `docs/ARCHITECTURE.md` for a system-level overview and request-flow
walkthrough, `docs/AGENT_GRAPH.md` for the full agent graph diagram and
handoff description, `docs/DATA_SCHEMA.md` for the database schema,
`docs/API.md` for the endpoint reference, `docs/DECISIONS.md` for design
trade-offs and how ambiguity in the brief was resolved, and
`docs/TIME_SAVINGS.md` for the manual-vs-agent time comparison.

## Repository layout

```
backend/
  app/
    agents/        six agent modules (five-agent LangGraph pipeline plus the
                   standalone Prioritization/Ranking agent), plus skills/
                   (TrueForge skill content, e.g. outreach copywriting)
    api/routes/    FastAPI routers, one module per resource (including
                   tool_approvals.py and agent_followups.py)
    api/router.py  aggregates every router; app.main only mounts this one
    core/          config, the TrueForge HTTP client, the HF/Gemini LLM
                   fallback (with multi-key rotation), the Daytona sandbox
                   wrapper, and the one-time TrueForge provider bootstrap
                   script (trueforge_bootstrap.py)
    mcp_tools/     the four remote MCP servers: firmographic enrichment,
                   Tavily and Exa web research, and Hunter.io email tools
    db/            SQLAlchemy session/base, demo data seeding, and
                   migrations.py (additive-only, for the one column added to
                   an already-existing table)
    models/        SQLAlchemy ORM models, one module per domain entity
    schemas/       Pydantic request/response schemas, one module per domain
    services/      ingestion + pipeline orchestration services
  data/            bundled sample CRM CSV and website events JSON
  tests/           pytest suite (unit, API, one real-LLM integration test)
frontend/
  src/
    api/           typed API client
    components/    ui/ (shadcn-pattern primitives — badge, button, card,
                   dialog, select, tabs, toast, tooltip, accordion,
                   hover-card, key-value-builder, ...), layout/, leads/
                   (per-lead views: AgentTraceTab, SignalHistoryTab,
                   ConfidenceTrendChart, ApprovePlanButton, LeadAvatar,
                   PipelineProgressPanel, AgentRunFollowupPanel,
                   StageBadge, ConfidenceMeter), dashboard/, setup/
    lib/           cn() className helper, agents.ts (shared agent display
                   metadata), useCountUp.ts (stat-tile animation)
    pages/         the five application screens
    types/         shared TypeScript types matching the backend schemas
docs/              architecture, agent graph, data schema, API reference,
                   time-savings writeup, design decisions, diagrams/
                   (Graphviz source + rendered SVGs)
.github/           CI workflow, PR template, issue templates
CONTRIBUTING.md    development workflow and code review process
CODE_OF_CONDUCT.md community standards
SECURITY.md        vulnerability disclosure process
CHANGELOG.md       notable changes, in Keep a Changelog format
```

## Setup from a clean checkout

### Prerequisites
- Python 3.11+
- Node.js 22+ and npm (required by the TrueForge agent harness)
- A Hugging Face access token (https://huggingface.co/settings/tokens) — the
  primary LLM provider
- Optional: a Gemini API key (https://ai.google.dev) as a fallback provider,
  and a Daytona API key + sandbox (https://app.daytona.io) for sandboxed
  signal scoring. Both are genuinely optional — everything still runs
  correctly without them, just with less redundancy.

### 1. Configure environment variables

Create a `.env` file at the repository root (not inside `backend/`):

```
HF_TOKEN=your-hugging-face-token
HF_MODEL=Qwen/Qwen3-4B-Instruct-2507:nscale
# Optional: up to two more HF keys — a 402/429/401/403 on one rotates to
# the next before falling back to Gemini
HF_TOKEN_1=your-second-hugging-face-token
HF_TOKEN_2=your-third-hugging-face-token
DATABASE_URL=sqlite:///signalis.db

# Optional: used only if every HF key is unavailable or a request fails
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-3.6-flash
# Optional: a second Gemini key, tried if the first one's quota is exhausted
GEMINI_API_KEY_1=your-second-key-here

# Optional: used only for sandboxed signal-scoring execution; falls back to
# an equivalent local computation if unset or unreachable
DAYTONA_API_KEY=your-daytona-api-key
DAYTONA_API_URL=https://app.daytona.io/api
DAYTONA_SANDBOX_ID=your-sandbox-id

# Optional: used only by the Persona Fit agent's search_company_news MCP
# tool (live company news/funding/hiring lookups via https://tavily.com);
# falls back to a "not queried" result if unset or the call fails
TAVILY_API_KEY=your-tavily-api-key

# Optional: used only by the Persona Fit agent's search_company_semantic MCP
# tool (semantic/company-focused web search via https://exa.ai); falls back
# to a "not queried" result if unset or the call fails
EXA_API_KEY=your-exa-api-key

# Optional: used only by the Outreach Planner agent's find_email/verify_email
# MCP tools (email finding + verification via https://hunter.io); falls back
# to a "not queried"/"unverified" result if unset or the call fails
HUNTER_API_KEY=your-hunter-api-key

# Optional: point at a different local TrueForge instance, or set
# TRUEFORGE_ENABLED=false to skip the harness and call HF/Gemini directly
TRUEFORGE_URL=http://localhost:8790
TRUEFORGE_ENABLED=true
TRUEFORGE_MODEL=huggingface/qwen3-4b
# Optional: additional TrueForge-registered models to try, in order, before
# falling back to the direct HF/Gemini path — each normally backed by a
# distinct HF key/provider so one key's quota running out rotates to the
# next registered model rather than failing the turn
TRUEFORGE_MODEL_1=huggingface-2/qwen3-4b
TRUEFORGE_MODEL_2=huggingface-3/qwen3-4b
TRUEFORGE_MODEL_FALLBACK=google-gemini/gemini-3-6-flash
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

### 3b. Start the research MCP server

```bash
cd backend
source venv/bin/activate
python -m app.mcp_tools.research_server
```

This serves the `search_company_news` live web-research tool at
`http://127.0.0.1:8792/mcp`. Leave it running in its own terminal. It works
without `TAVILY_API_KEY` set (the tool returns a graceful "not queried"
result instead of failing the agent turn), but genuinely calls the Tavily
API only once that key is configured.

### 3c. Start the Exa MCP server

```bash
cd backend
source venv/bin/activate
python -m app.mcp_tools.exa_server
```

This serves the `search_company_semantic` live web-research tool at
`http://127.0.0.1:8793/mcp`. Leave it running in its own terminal. Same
graceful fallback as the research server: works without `EXA_API_KEY` set,
genuinely calls Exa only once that key is configured.

### 3d. Start the Hunter.io MCP server

```bash
cd backend
source venv/bin/activate
python -m app.mcp_tools.hunter_server
```

This serves the `find_email`/`verify_email` tools at
`http://127.0.0.1:8794/mcp`. Leave it running in its own terminal. Same
graceful fallback: works without `HUNTER_API_KEY` set, genuinely calls
Hunter.io only once that key is configured.

### 4. Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"   # installs the app plus pytest + ruff, from pyproject.toml
python -m app.core.trueforge_bootstrap   # registers model/sandbox/MCP providers with TrueForge
uvicorn app.main:app --reload
```

The API is now served at `http://localhost:8000`, with interactive docs at
`http://localhost:8000/docs`. Tables are created automatically on startup.
Individual TrueForge agents (one per Signalis agent) register themselves
automatically the first time each one runs.

If TrueForge or any of the four MCP servers is not running, every agent
call still works — `app/agents/common.run_agent_reasoning` catches the
transport failure and falls back to a direct Gemini/Hugging-Face call —
but the MCP tool calls and the TrueForge-native agent loop will not be
exercised in that case.

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
- **Real MCP tool use**: With TrueForge and all four MCP servers running
  (setup steps 2, 3, 3b, 3c, 3d), any pipeline run's Persona Fit step
  genuinely discovers and calls the `classify_company_industry` /
  `estimate_company_size_band` tools, and — when recent external context
  would sharpen the fit assessment — `search_company_news` (Tavily) and/or
  `search_company_semantic` (Exa), over MCP. Separately, the Outreach
  Planner genuinely calls `verify_email`/`find_email` (Hunter.io) to check
  the lead's email is deliverable before finalizing copy. This is visible
  directly in TrueForge's own session events (`GET
  /api/v1/sessions/{id}/events` on the TrueForge harness, port 8790) as real
  `mcp.initialize` and tool-call events, not just in the agent's final
  answer.
- **Sandboxed code execution**: Every Buying Stage Orchestrator run computes
  its recency/strength-weighted signal score by executing generated Python
  inside the configured Daytona sandbox; `agent_runs.output.signal_score_computed_via`
  records `"daytona"` or `"local"` depending on which path actually ran for
  that request.
- **Human approval as a harness primitive**: the Persona Fit agent's
  enrichment tools are gated by TrueForge's `require_approval_for_tools`.
  When the model calls one, the turn pauses and a pending-approval card
  appears inline on the Lead Detail page; approving resumes the same
  TrueForge turn and lets Persona Fit's real result complete, rejecting
  resumes with a denial and marks that run failed.
- **Subagent delegation**: click "Rank Pipeline" on the Dashboard with
  several classified leads present, then check the ranking response's
  `subagent_delegation` field (or TrueForge's own session events at
  `GET /api/v1/sessions/{id}/events`) to see genuine parallel
  `create_sub_agent` delegations, one per lead, feeding into the root
  agent's final consolidated order.
- **Persistent-session follow-up**: on a lead's Agent Trace tab, ask a
  follow-up question against any trace entry that ran through TrueForge —
  the answer is a real continuation turn on that run's own session, not a
  fresh call re-fed a summary of it.
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
pytest -m "not integration" -q      # fast unit + API tests, LLM calls mocked
pytest -m integration -q            # real end-to-end LLM call (needs HF_TOKEN or GEMINI_API_KEY)
```

## Linting

```bash
cd backend && ruff check app tests
cd frontend && npx eslint .
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, test
and lint expectations, and how code review works in this repository. This
project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Security
issues should be reported per [SECURITY.md](SECURITY.md) rather than as a
public issue.

## License

MIT — see [LICENSE](LICENSE).
