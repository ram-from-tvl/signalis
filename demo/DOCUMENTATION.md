# Signalis — Full Documentation

This is the single-file, detailed walkthrough of the entire codebase: what
the product does, how every piece fits together, every API endpoint, every
database table, every agent's real behavior, and the full frontend
component inventory. For narrower, more focused references, see the
`docs/` directory (linked throughout below); this file exists to give a
reader the complete picture in one place, current as of the state of the
repository after PR #19.

---

## Table of contents

1. [What Signalis is](#1-what-signalis-is)
2. [System architecture, end to end](#2-system-architecture-end-to-end)
3. [The agent pipeline, node by node](#3-the-agent-pipeline-node-by-node)
4. [The LLM provider fallback chain](#4-the-llm-provider-fallback-chain)
5. [Real tool use: MCP servers](#5-real-tool-use-mcp-servers)
6. [Sandboxed execution: Daytona](#6-sandboxed-execution-daytona)
7. [Human approval, twice over](#7-human-approval-twice-over)
8. [Database schema](#8-database-schema)
9. [REST API reference](#9-rest-api-reference)
10. [Frontend architecture](#10-frontend-architecture)
11. [Frontend directory-by-directory inventory](#11-frontend-directory-by-directory-inventory)
12. [Design system](#12-design-system)
13. [Setup from a clean checkout](#13-setup-from-a-clean-checkout)
14. [Testing and linting](#14-testing-and-linting)
15. [Known limitations and honest caveats](#15-known-limitations-and-honest-caveats)
16. [Where everything else lives](#16-where-everything-else-lives)

---

## 1. What Signalis is

Signalis is an agentic buying-signal copilot for marketing and SDR teams.
It ingests lead activity from a CRM-style export and a website event log,
figures out where each lead sits in the buying journey with explainable,
LLM-generated reasoning, and produces a tailored outreach micro-plan for
each lead — updating its understanding automatically whenever new signals
arrive.

Concretely, it:

- Parses raw CRM rows and website events into normalized, stage-tagged
  signals, tolerating missing or malformed data instead of failing the
  whole batch.
- Scores each lead's fit against a marketer-defined persona and solution
  ICP (ideal customer profile), flagging mismatches and missing data with
  reasoning attached.
- Aggregates a lead's signal history — weighing recency and signal
  strength — into an overall buying stage (`early` / `mid` / `late`) with
  a confidence score and a plain-language justification.
- Generates a 1–2 week outreach micro-plan (touchpoints, channels,
  content themes, example message copy) tailored to that stage, persona
  fit, and solution positioning.
- Synthesizes a narrative explanation of the full reasoning chain for
  every pipeline run, forming a visible, ordered multi-agent trace.
- Enforces a human-approval checkpoint: every generated outreach plan
  starts as `pending_approval`, and any classification the model itself
  is unconfident about is additionally routed for explicit approval.
- Re-runs its full reasoning chain on demand when new signals arrive for
  a lead, producing an updated classification and a regenerated plan.
- Ranks the whole pipeline by contact priority on demand — an explainable
  order with a specific reason per lead, not a raw sort by stage label.

---

## 2. System architecture, end to end

```
┌──────────────┐      HTTP/JSON      ┌────────────────────┐
│  React        │ ──────────────────▶ │  FastAPI backend    │
│  frontend     │ ◀────────────────── │  (app/api/routes/)  │
│  (Vite,       │                     │                      │
│   port 5173)  │                     │  Services layer      │
└──────────────┘                     │  (app/services/)     │
                                       │        │             │
                                       │        ▼             │
                                       │  LangGraph            │
                                       │  StateGraph            │
                                       │  (app/agents/graph.py) │
                                       └───────┬───────────────┘
                                               │ session/turn per node
                                               ▼
                                   ┌─────────────────────────┐
                                   │  TrueForge agent harness  │
                                   │  (local process, port 8790)│
                                   │  — owns model calls, MCP    │
                                   │    tool discovery/execution,│
                                   │    context management       │
                                   └──────┬───────┬──────────────┘
                                          │       │
                       ┌──────────────────┘       └───────────────────┐
                       ▼                                              ▼
          ┌─────────────────────────┐                  ┌───────────────────────┐
          │  LLM providers            │                  │  4 remote MCP servers   │
          │  Hugging Face (primary,   │                  │  enrichment, research    │
          │  multi-key rotation)      │                  │  (Tavily), exa, hunter   │
          │  Gemini (fallback)        │                  │  (each its own process,  │
          └───────────────────────────┘                  │  ports 8791–8794)        │
                                                           └───────────────────────┘
                                       │
                                       ▼
                          ┌─────────────────────────┐
                          │  Daytona sandbox           │
                          │  (buying-stage signal       │
                          │  scoring; local fallback     │
                          │  if unreachable)              │
                          └─────────────────────────┘

                                       │
                                       ▼
                              ┌─────────────────┐
                              │  SQLite (via       │
                              │  SQLAlchemy)         │
                              └─────────────────┘
```

**Request flow, end to end:**

1. A marketer loads sample data or uploads a CRM export + website event
   log through the frontend's Data Sources screen. The backend's
   ingestion service (`app/services/ingestion.py`) parses both into
   normalized `Lead` and `Signal` rows, tolerating missing or malformed
   fields rather than failing the whole batch.
2. Triggering the pipeline (per-lead or for the whole list) runs
   `app/services/pipeline.py`, which invokes the LangGraph `StateGraph`
   (`app/agents/graph.py`) for each lead.
3. Each of the five graph nodes delegates its actual reasoning to a
   session/turn on the local TrueForge harness rather than calling an LLM
   SDK directly (see [§3](#3-the-agent-pipeline-node-by-node)).
4. The Persona Fit node's reasoning is grounded by genuine tool calls
   through TrueForge's MCP layer to real remote servers (see [§5](#5-real-tool-use-mcp-servers)).
5. The Buying Stage node's signal-strength score is computed by running
   generated Python inside a Daytona sandbox, with an equivalent local
   fallback if the sandbox is unreachable (see [§6](#6-sandboxed-execution-daytona)).
6. Every graph run persists a `StageClassification` and `OutreachPlan` as
   new, append-only rows (never mutated in place), plus an `AgentRun` row
   per node capturing its reasoning, so the full decision trail is
   inspectable from the Agent Trace tab.
7. Any classification the model itself was unconfident about, and every
   generated outreach plan, is persisted as `pending_approval` and
   requires an explicit human approve/reject action before being treated
   as final.
8. The standalone Prioritization/Ranking Agent runs independently of the
   per-lead graph, over the whole pipeline at once, producing an ordered,
   explainable "who to contact first" list.

**Why classifications and plans are append-only:** the *content* of a
stage classification or outreach plan is never updated in place — a new
pipeline run always inserts a new row rather than rewriting an existing
one's stage, confidence, justification, or touchpoints. The only mutation
applied to a prior row is a supersession marker (`superseded_by_id` /
`status="superseded"`). This means the full reasoning history behind a
lead's current state is always reconstructable, and the Classification
History and Agent Trace tabs read real history, not a bolted-on log.

Full detail: [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

---

## 3. The agent pipeline, node by node

Five agents run as nodes in a LangGraph `StateGraph`
(`app/agents/graph.py`), plus a sixth, standalone Prioritization/Ranking
agent that is not part of the per-lead graph. Each node's actual reasoning
executes as a session/turn on the local TrueForge harness — TrueForge owns
the real agent loop (model calls, MCP tool discovery/execution, context
management), and the Python graph node only starts the turn and reads back
its structured output (`app/core/trueforge.py`).

| # | Agent | Module | What it does |
|---|---|---|---|
| 1 | **Signal Extraction** | `app/agents/signal_extraction.py` | Receives every raw, not-yet-classified `signals` row for the lead. Returns a normalized `event_type` and `intent_stage_hint` per record, updating the `signals` rows in place. |
| 2 | **Persona Fit** | `app/agents/persona_fit.py` | Receives the lead's firmographic profile plus the persona/solution ICP from the lead's assigned campaign (or the default campaign). Has three MCP servers attached (`signalis-enrichment`, `signalis-research`, `signalis-exa`) and genuinely calls them when useful. Returns a fit classification (`full_fit` / `partial_fit` / `mismatch`), reasoning, and missing-data notes. |
| 3 | **Buying Stage Orchestrator** | `app/agents/buying_stage.py` | Receives the lead's full signal history plus the persona fit result. Computes a recency/strength-weighted signal score by executing generated Python in a Daytona sandbox (or a local fallback) before calling the model. Returns `stage`, `confidence`, and a `justification`. This node's confidence score drives the pipeline's one real conditional branch. |
| — | **Conditional edge** | (in `graph.py`) | If confidence is below `confidence_approval_threshold` (default `0.5`), the classification is flagged `requires_approval=True` / `pending_approval`. Both branches still proceed to plan generation. |
| 4 | **Outreach Planner** | `app/agents/outreach_planner.py` | Receives stage, confidence, persona fit, the campaign's persona/solution, and the lead's email. Returns a 3–5 touchpoint micro-plan (day offsets, channels, content themes, message copy). Has the `signalis-hunter` MCP server attached and genuinely verifies the lead's email before finalizing copy. Also consults a TrueForge **skill** for copywriting craft guidance. |
| 5 | **Explainability** | `app/agents/explainability.py` | Receives the structured outputs of all four prior agents plus the `requires_approval` flag, and synthesizes a coherent plain-language narrative explaining the whole reasoning chain — this is what the Agent Trace tab foregrounds. |
| 6 | **Prioritization / Ranking** | `app/agents/prioritization.py` | Runs independently, on demand, across every currently-classified lead. Uses genuine TrueForge subagent delegation (`create_sub_agent`, run in parallel, one per lead) for per-lead priority assessment, then consolidates in the root agent's own context for the final cross-lead order. |

**Tool-approval gate:** Persona Fit's two enrichment tools
(`classify_company_industry`, `estimate_company_size_band`) are gated by
TrueForge's native `require_approval_for_tools` — when the model decides
to call either, the turn pauses and a `ToolApprovalRequest` row is
persisted; a marketer approves or rejects it inline on the Lead Detail
page (`POST /api/tool-approvals/{id}/approve|reject`). This gate does not
fire on every run — only when the model actually decides a lead's
industry or company size is missing or worth verifying. Research/Exa
calls are *not* gated (`require_approval_for_tools: []`), since they're
read-only searches, not enrichment writes.

**Follow-up questions on an agent's own reasoning:** a marketer can ask a
natural-language follow-up against any trace entry that ran through
TrueForge (`POST /api/leads/{lead_id}/agent-runs/{agent_run_id}/ask`),
answered as a genuine continuation turn on that run's own TrueForge
session (`app.core.trueforge.run_followup_turn`) — the model answers with
its own prior reasoning still in context, not a fresh one-shot call
re-fed a summary. If the run used the direct-LLM fallback path (no
TrueForge session exists), the endpoint returns `409 Conflict` rather than
faking statelessness.

Full detail, including the exact per-node system-instruction behavior and
the full mermaid-to-Graphviz diagram: [`docs/AGENT_GRAPH.md`](../docs/AGENT_GRAPH.md)
([rendered diagram](../docs/diagrams/agent_graph.svg)).

---

## 4. The LLM provider fallback chain

Every agent call goes through `app.agents.common.run_agent_reasoning`,
which tries, in order:

1. **Every TrueForge-registered model**, in the order given by
   `Settings.trueforge_models` (`app/core/config.py`): the primary
   Hugging Face model (`TRUEFORGE_MODEL`, default
   `huggingface/qwen3-4b`), then any additional configured HF-key models
   (`TRUEFORGE_MODEL_1`, `TRUEFORGE_MODEL_2` — each backed by a distinct
   `HF_TOKEN_1`/`HF_TOKEN_2`), then the Gemini fallback model
   (`TRUEFORGE_MODEL_FALLBACK`, default `google-gemini/gemini-3-6-flash`).
   A pre-execution failure (registration, session/turn creation,
   transport) rotates to the next model in the list. A failure *after*
   the turn genuinely executed real side effects (an MCP tool call, a
   subagent fan-out) does **not** rotate — retrying would repeat those
   side effects — and falls straight to step 2 instead
   (`TrueForgeTurnExecutedError`).
2. **A direct Hugging Face call** (`app/core/llm.py::generate_json`),
   trying every configured `HF_TOKEN`/`HF_TOKEN_1`/`HF_TOKEN_2` in order —
   only reached if every TrueForge-registered model failed, or if
   TrueForge itself is disabled (`TRUEFORGE_ENABLED=false`) or
   unreachable.
3. **A direct Gemini call**, trying every configured
   `GEMINI_API_KEY`/`GEMINI_API_KEY_1` in order — the true last resort.

No layer of this chain returns templated or hardcoded output — every
classification, fit assessment, plan, and narrative is a real model call
over real data, even in the fully-degraded direct-fallback path. The
direct fallback has no MCP tool access (tool-referencing instructions are
rewritten to say so explicitly, rather than letting the model try to
invoke tools that don't exist there) and no access to TrueForge skills
(a `fallback_style_guidance` string is appended instead, so craft
guidance that lives in a skill isn't silently lost).

Hugging Face is the primary provider specifically because Gemini's free
tier is capped at 20 requests/day and was repeatedly exhausted during
ordinary development/demo use — Gemini was, in practice, the *less*
reliable provider despite historically being listed first. See
[`docs/DECISIONS.md`](../docs/DECISIONS.md) for the full narrative (local-only
file — see [§16](#16-where-everything-else-lives)).

---

## 5. Real tool use: MCP servers

Four real remote MCP servers, each its own local HTTP process, each
genuinely discovered and invoked through TrueForge's MCP layer — not
function calls embedded in an agent's own Python code:

| Server | File | Port | Tools | Attached to |
|---|---|---|---|---|
| Enrichment | `app/mcp_tools/enrichment_server.py` | 8791 | `classify_company_industry`, `estimate_company_size_band` | Persona Fit (gated by tool-approval) |
| Research | `app/mcp_tools/research_server.py` | 8792 | `search_company_news` (live Tavily search) | Persona Fit |
| Exa | `app/mcp_tools/exa_server.py` | 8793 | `search_company_semantic` (live Exa search — a differently-sourced, semantic complement to Tavily) | Persona Fit |
| Hunter | `app/mcp_tools/hunter_server.py` | 8794 | `find_email`, `verify_email` (live Hunter.io API) | Outreach Planner |

Every server degrades gracefully if its corresponding API key
(`TAVILY_API_KEY`, `EXA_API_KEY`, `HUNTER_API_KEY`) is unset — it returns
a "not queried" result rather than failing the agent turn. With a key
configured, the calls are genuinely live: this has been directly verified
during development by tailing each server's log and observing real
outbound HTTP requests (e.g. `GET https://api.hunter.io/v2/email-finder`,
`200 OK`) during a real pipeline run.

Starting all four is required for the tool-approval gate and MCP tool use
to be exercised at all — see [§13](#13-setup-from-a-clean-checkout).

---

## 6. Sandboxed execution: Daytona

The Buying Stage Orchestrator's recency/strength-weighted signal score is
computed by running genuinely generated Python inside a Daytona sandbox
(`app/core/sandbox.py::run_signal_scoring`), rather than as in-process
business logic. The generated script is built with `repr()`-escaped
literals so untrusted signal data can never break out of the script and
alter what runs. Which execution path actually ran is recorded on the
persisted output as `signal_score_computed_via`: `"daytona"` or `"local"`.

**Important operational note:** Daytona sandboxes auto-stop after a
configured idle interval (15 minutes by default on the sandbox used in
this project) — this is Daytona's own cost-saving behavior. The app does
not currently call Daytona's `start()` API anywhere; it only tries to run
code and, if the sandbox happens to be stopped, catches the resulting
error and falls back to an equivalent local computation
(`_score_locally`) rather than failing the pipeline run. This means a
pipeline run after a period of inactivity will silently use the local
fallback rather than the sandbox, until the sandbox is manually started
again (`daytona.Daytona(...).get(sandbox_id).start()`). This has been
directly observed and verified: after calling `start()` explicitly, the
next pipeline run produced a genuine `signal_score_computed_via:
"daytona"` result.

---

## 7. Human approval, twice over

Two independent approval mechanisms exist, deliberately layered:

1. **Application-level checkpoint.** Every generated outreach plan starts
   `pending_approval` and never becomes usable output until a marketer
   explicitly approves it (`POST /api/approvals/plans/{id}`). A
   low-confidence stage classification is separately flagged
   `pending_approval` at the classification level, so a marketer can
   reject the classification itself before deciding whether the plan
   built on top of it makes sense. Both paths write an immutable
   `approval_events` row.
2. **TrueForge-native tool-approval gate.** Independent of the above,
   Persona Fit's two enrichment tool calls pause the actual TrueForge
   turn (`require_approval_for_tools`) until a marketer approves or
   rejects the specific tool call via `POST /api/tool-approvals/{id}/approve|reject`.
   Approving resumes the exact same TrueForge turn with
   `previous_turn_id: "auto"`; rejecting resumes with a denial and marks
   the backing `AgentRun` as failed. **Scope note:** approving a pending
   tool call completes only the Persona Fit step — it does not
   automatically resume the rest of the pipeline. The marketer re-clicks
   "Regenerate Plan" to run the remaining steps with the now-unblocked
   Persona Fit result already on record.

---

## 8. Database schema

SQLite via SQLAlchemy 2.x, `backend/app/models/`. Every table below is
current as of the state of the repo after PR #19 (verified directly
against the model source files, not from memory).

| Table | Purpose |
|---|---|
| `personas` | Target-persona definitions (role, seniority, industry, company size band, geography, custom traits as JSON) the Persona Fit agent scores leads against. |
| `solutions` | What you sell — problem solved, value props, differentiators, target channels — the Outreach Planner positions every message around this. |
| `campaigns` | Pairs one persona with one solution — the unit a lead is actually scored against, so multiple GTM motions run concurrently instead of sharing one global config. `is_default` marks the fallback campaign for leads with none assigned. |
| `leads` | One row per unique lead, deduped by email. Carries a nullable `campaign_id`. |
| `signals` | Raw and classified interaction events for a lead, from any source (`crm`/`website`/`email`/`linkedin`), keeping the original payload verbatim (`raw_payload` JSON) alongside a normalized `event_type`/`intent_stage_hint`. |
| `agent_runs` | One row per agent invocation — `agent_name`, `input_summary`, `output` (JSON), `reasoning`, `status`, timestamps, and `trueforge_session_id` (nullable — null on the direct-fallback path). This is the append-only audit trail every "Agent Trace" view reads. |
| `agent_run_followups` | Append-only Q&A pairs from the persistent-session follow-up feature. |
| `tool_approval_requests` | One row per paused tool call awaiting a marketer's approve/reject decision. |
| `stage_classifications` | Append-only — a new row per pipeline run, never mutated. `superseded_by_id` marks the previous row when a new one supersedes it. |
| `outreach_plans` | Append-only, same supersession pattern. Includes `verified_email`/`email_verification_status`/`email_verification_reason` from the Outreach Planner's real Hunter.io check. |
| `approval_events` | Immutable audit log of every approve/reject action, at both the plan and classification level. |
| `pipeline_rankings` | Append-only snapshot from the Prioritization/Ranking agent — one row per ranking pass over the whole pipeline (not scoped to a single lead). Includes `subagent_delegation` (JSON, nullable): real evidence of whether the run genuinely delegated per-lead assessment to parallel TrueForge subagents, read back from TrueForge's own session events rather than the model's self-report. |

Full column-by-column detail: [`docs/DATA_SCHEMA.md`](../docs/DATA_SCHEMA.md).

---

## 9. REST API reference

All routes are aggregated under `app/api/router.py` and mounted once by
`app/main.py`. Every route below was verified directly against
`backend/app/api/routes/*.py` — this list is exhaustive, not a summary.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/personas` | List personas |
| `POST` | `/api/personas` | Create a persona |
| `GET` | `/api/personas/{id}` | Get one persona |
| `PUT` | `/api/personas/{id}` | Update a persona |
| `DELETE` | `/api/personas/{id}` | Delete a persona |
| `GET` | `/api/solutions` | List solutions |
| `POST` | `/api/solutions` | Create a solution |
| `GET` | `/api/solutions/{id}` | Get one solution |
| `PUT` | `/api/solutions/{id}` | Update a solution |
| `DELETE` | `/api/solutions/{id}` | Delete a solution |
| `GET` | `/api/campaigns` | List campaigns, with persona/solution/lead count joined in |
| `POST` | `/api/campaigns` | Create a campaign |
| `GET` | `/api/campaigns/{id}` | Get one campaign |
| `PUT` | `/api/campaigns/{id}` | Update a campaign |
| `DELETE` | `/api/campaigns/{id}` | Delete a campaign (rejected if it's the default, or has assigned leads) |
| `POST` | `/api/campaigns/{id}/assign-leads` | Move a set of leads onto this campaign |
| `POST` | `/api/uploads/crm-csv` | Ingest a CRM CSV export |
| `POST` | `/api/uploads/website-events` | Ingest a website event log JSON |
| `GET` | `/api/leads` | List all leads with their latest classification/plan status |
| `GET` | `/api/leads/{id}` | Full detail for one lead — profile, signals, classification history, plan |
| `GET` | `/api/leads/{id}/trace` | This lead's full `agent_runs` trace, in order |
| `POST` | `/api/leads/{id}/signals` | Append a new signal to a lead (used by "Simulate New Signal") |
| `POST` | `/api/pipeline/run` | Run the 5-agent pipeline for one lead or all leads |
| `POST` | `/api/approvals/plans/{id}` | Approve or reject an outreach plan |
| `POST` | `/api/approvals/classifications/{id}` | Approve or reject a stage classification |
| `GET` | `/api/tool-approvals` | List pending tool-approval requests |
| `POST` | `/api/tool-approvals/{id}/approve` | Approve a paused MCP tool call, resuming the TrueForge turn |
| `POST` | `/api/tool-approvals/{id}/reject` | Reject a paused MCP tool call |
| `GET` | `/api/dashboard/stats` | Aggregate pipeline stats (total leads, plans generated, avg confidence, avg agent latency, stage distribution, time-savings numbers) |
| `POST` | `/api/ranking/run` | Run the Prioritization/Ranking agent over every classified lead |
| `GET` | `/api/ranking/latest` | Get the most recent ranking snapshot |
| `POST` | `/api/leads/{lead_id}/agent-runs/{agent_run_id}/ask` | Ask a follow-up question against one trace entry's TrueForge session |
| `GET` | `/api/leads/{lead_id}/agent-runs/{agent_run_id}/followups` | List follow-up Q&A history for one trace entry |

Interactive Swagger docs are served live at `http://localhost:8000/docs`
whenever the backend is running. Full narrative reference with request/
response shapes and error semantics: [`docs/API.md`](../docs/API.md).

---

## 10. Frontend architecture

React 18 + Vite + TypeScript, Tailwind CSS, a component library built on
Radix primitives in the shadcn/ui pattern — owned in this codebase under
`frontend/src/components/ui/`, not an installed black box, so every
primitive can be adjusted to the app's own design tokens
(`frontend/src/index.css`). Data fetching goes through TanStack Query
(`frontend/src/api/`), with every query in the app expected to handle
loading, error, and empty states as three distinct branches, not just
loading-vs-data.

**Routing** (`frontend/src/App.tsx`), five screens total:

| Path | Page |
|---|---|
| `/` | Dashboard |
| `/leads` | Lead Pipeline (list) |
| `/leads/:leadId` | Lead Detail |
| `/data` | Data Sources |
| `/setup` | Persona & Solution |

**Backend timestamp parsing:** backend timestamps are serialized via
Python's `datetime.utcnow()`, which carries no timezone marker. A bare
`new Date(iso)` in the browser parses such a string as *local* time, not
UTC, silently shifting every displayed timestamp by the browser's UTC
offset. `frontend/src/lib/utils.ts::parseUtcTimestamp` appends `Z` only
when no offset is already present, and is used everywhere a backend
timestamp is formatted across the app.

---

## 11. Frontend directory-by-directory inventory

```
frontend/src/
  api/
    client.ts        Axios instance + response interceptor; ApiError
                      class carries the HTTP status so callers can
                      distinguish a confirmed 404 from a transient failure.
    endpoints.ts      Typed endpoint functions grouped by resource.
  components/
    ui/               shadcn-pattern primitives, owned in this codebase:
                       accordion.tsx      Radix accordion — powers the
                                          Agent Trace chain-of-thought view.
                       badge.tsx          Semantic-color status pills
                                          (success/warning/destructive/
                                          info/stage-early/mid/late/...).
                       button.tsx
                       card.tsx
                       checkbox.tsx
                       click-spark.tsx    Canvas-based micro-interaction on
                                          the human-approval actions.
                       dialog.tsx
                       hover-card.tsx     Powers the Dashboard's plain-
                                          language delegation-status tooltip.
                       input.tsx
                       key-value-builder.tsx
                                          Row-based key/value editor
                                          serializing to a plain JSON
                                          object — used for Persona's
                                          Custom Traits so a marketer
                                          never has to type or see a brace.
                       label.tsx
                       select.tsx
                       skeleton.tsx
                       tabs.tsx
                       textarea.tsx
                       toast-context.tsx  Hand-built toast system (not a
                                          third-party library) wired to
                                          every mutating action in the app.
                       tooltip.tsx
    layout/
      AppShell.tsx    Sidebar navigation + page frame, wraps every route.
    leads/            Domain-specific, per-lead components:
      StageBadge.tsx           Early/Mid/Late pill.
      ConfidenceMeter.tsx      Animated confidence bar + percentage,
                                color-coded by confidence level.
      PipelineProgressPanel.tsx
                                Live per-agent progress panel shown while
                                a pipeline run is in flight — polls the
                                trace endpoint, shows real running/
                                completed status and per-agent insight
                                text per step, not a generic spinner.
      AgentTraceTab.tsx         Collapsible chain-of-thought accordion for
                                the Agent Trace tab: a 5-icon mini-timeline
                                at the top, each step collapsed by default
                                (most recent expanded) showing a real
                                one-line outcome and real per-step
                                duration, with the follow-up-question
                                panel nested inside each expanded step.
      SignalHistoryTab.tsx      Signal History timeline: dedupes exact-
                                duplicate signals into a single row with
                                a ×N count, gives each event type its own
                                icon, and formats raw_payload as labeled
                                key/value pairs rather than JSON.
      ConfidenceTrendChart.tsx  Sparkline (Recharts AreaChart) showing a
                                lead's confidence trend across
                                classification runs, with the single
                                biggest jump annotated.
      ApprovePlanButton.tsx     Confirm-on-click approval: arms on first
                                click ("Confirm send?"), fires on second,
                                auto-resets after 4s if abandoned.
      LeadAvatar.tsx            Deterministic per-lead avatar initials,
                                colored from a hash of the lead's name.
      AgentRunFollowupPanel.tsx Ask-a-follow-up input + history, nested
                                inside each Agent Trace step.
  pages/
    DashboardPage.tsx     Bento-grid layout: a large hero cell for the
                          measured speedup number, 4 stat tiles, the
                          stage-distribution chart, and the Priority
                          Queue (ranking) panel.
    LeadPipelinePage.tsx  Searchable, filterable, sortable lead list with
                          per-lead avatars and an indeterminate bulk-run
                          progress indicator with rotating agent labels.
    LeadDetailPage.tsx    Per-lead view: current classification, persona
                          fit, and the four tabs (Outreach Plan, Signal
                          History, Agent Trace, Classification History).
    DataSourcesPage.tsx   Drag-and-drop CRM/website upload zones, a
                          3-step "Pipeline at a glance" explainer, and a
                          collapsed "Developer info" disclosure for the
                          raw API/docs URLs.
    SetupPage.tsx         Persona and Solution forms, including the
                          key/value Custom Traits builder.
  lib/
    utils.ts          cn() class-merge helper; parseUtcTimestamp().
    agents.ts         Single source of truth for the 5-agent pipeline's
                      display metadata: fixed order, labels, per-agent
                      accent CSS variable, per-agent Lucide icon, and
                      real running-state insight text — shared between
                      AgentTraceTab and PipelineProgressPanel so they can
                      never drift out of sync with each other.
    useCountUp.ts     Hook animating a dashboard stat tile's number up
                      from 0 on load.
  types/api.ts        TypeScript interfaces mirroring the backend's
                      Pydantic schemas (hand-mirrored, no shared codegen).
  index.css           Design tokens (CSS custom properties, both light
                      and dark) and global styles.
  App.tsx             Route definitions.
  main.tsx            Entry point.
```

---

## 12. Design system

- **Typography:** DM Serif Display (headings) + Plus Jakarta Sans (body),
  loaded via Google Fonts in `index.css`. Chosen specifically to avoid
  both the generic Inter/Space-Grotesk "AI-built" look and the more
  recent "Poppins fatigue" — a warm, characterful serif paired with a
  friendly, less-generic sans, consistent with the app's warm palette.
- **Color:** a warm off-white / sage-slate / terracotta palette applied
  per the 60/30/10 rule (60% warm off-white background, 30% sage-slate
  secondary surfaces, 10% terracotta accent reserved for primary actions
  and active state). Full semantic token set — `success`, `warning`,
  `destructive`, `info`, and a distinct `stage-early`/`stage-mid`/
  `stage-late` scale — all defined for both light and dark themes in
  `frontend/src/index.css`, so no component ever has to choose between
  brand color and status color for the same visual role.
- **Agent identity colors:** five muted, evenly-spaced hues (one per
  pipeline agent) used consistently across the live progress panel and
  the Agent Trace mini-timeline, so a user learns the color-to-agent
  mapping once.
- **Motion:** Motion (the renamed `framer-motion`) for staggered list
  entrance, the live progress panel's spinning-ring and checkmark
  transitions, and the click-spark micro-interaction on approval actions.
  All motion respects `prefers-reduced-motion`.
- **Dark mode:** every token above has a defined dark-theme value; there
  is currently no in-app toggle UI to switch to it (verified working via
  direct `document.documentElement.classList.add("dark")` testing, not
  yet wired to a user-facing control).

---

## 13. Setup from a clean checkout

Full step-by-step instructions with copy-pasteable commands live in the
root [`README.md`](../README.md#setup-from-a-clean-checkout) — summarized
here:

1. **Configure `.env`** at the repo root with at minimum `HF_TOKEN`
   (the primary LLM provider). Everything else — Gemini, Daytona, Tavily,
   Exa, Hunter.io, extra TrueForge model slots — is optional; the app
   runs correctly without them, just with less redundancy and without
   genuine MCP tool use.
2. **Start the TrueForge harness**: `npx @truefoundry/trueforge@latest --port 8790`
   (requires Node.js 22+; leave running in its own terminal).
3. **Start all four MCP servers** (`python -m app.mcp_tools.enrichment_server`,
   `.research_server`, `.exa_server`, `.hunter_server` — each its own
   terminal, ports 8791–8794). Skipping any of these means that server's
   tools are simply never available to call — the pipeline still runs,
   just without that tool's grounding.
4. **Backend**: create a venv, `pip install -e ".[dev]"`, run
   `python -m app.core.trueforge_bootstrap` once (registers every
   model/sandbox/MCP provider with the running TrueForge instance), then
   `uvicorn app.main:app --reload`. Served at `http://localhost:8000`,
   interactive docs at `/docs`.
5. **Load sample data** (optional but recommended):
   `python -m app.db.seed_demo` — creates a default persona/solution and
   ingests the bundled `backend/data/sample_crm_leads.csv` (20 synthetic
   leads) and `sample_website_events.json` (45 synthetic events).
6. **Frontend**: `npm install`, `cp .env.example .env`, `npm run dev`.
   Served at `http://localhost:5173`.

If TrueForge or any MCP server isn't running, every agent call still
works — the fallback chain in [§4](#4-the-llm-provider-fallback-chain)
handles it — but MCP tool calls and the TrueForge-native agent loop won't
be exercised in that case.

---

## 14. Testing and linting

```bash
# Backend
cd backend && source venv/bin/activate
pytest -m "not integration" -q      # fast unit + API tests, LLM calls mocked
pytest -m integration -q            # real end-to-end LLM call, needs HF_TOKEN or GEMINI_API_KEY
ruff check app tests

# Frontend
cd frontend
npx tsc --noEmit
npx eslint .
npm run build
```

Both suites are gated in CI on every pull request (`.github/workflows/`).

---

## 15. Known limitations and honest caveats

These are real, currently-true gaps — not aspirational TODOs dressed up
as documentation:

- **Daytona sandbox goes idle.** See [§6](#6-sandboxed-execution-daytona) — the
  app never calls `start()` on a stopped sandbox, so after ~15 minutes of
  inactivity, signal scoring silently falls back to the local
  computation until someone manually restarts the sandbox.
- **Dark mode has no UI toggle.** Tokens are fully defined and verified
  working for both themes, but there's no switch a user can click.
- **No account/profile menu** exists anywhere in the app, even as a stub.
- **Mid-pipeline resumption after a tool-approval gate is partial.**
  Approving a paused Persona Fit tool call only completes that one step —
  the marketer has to manually click "Regenerate Plan" again to run the
  remaining steps. Full automatic resumption would require checkpointing
  and replaying partial LangGraph state across an HTTP round trip, judged
  out of scope for this build's actual needs.
- **`docs/DECISIONS.md`, `IMPLEMENTATION_PLAN.md`, and `challenge.md` are
  intentionally not part of the published repository** (see `.gitignore`) —
  they're local working documents. If you're reading this file from a fresh
  clone, those files won't be present; this `DOCUMENTATION.md` folds in
  everything from them that's relevant to understanding the shipped
  product. `docs/R&D.md` is tracked and does ship with the repo.
- **Lead Pipeline is a card list, not a data table.** At the current
  scale (dozens of leads) this was judged the right tradeoff over a full
  TanStack Table rebuild (sorting/filtering/virtualization), even though
  `@tanstack/react-table` is already a dependency for future use.

---

## 16. Where everything else lives

| Doc | Covers |
|---|---|
| [`README.md`](../README.md) | Top-level overview, full setup instructions, how to exercise every agentic behavior requirement |
| [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) | System-level diagram and request-flow walkthrough |
| [`docs/AGENT_GRAPH.md`](../docs/AGENT_GRAPH.md) | Full agent graph diagram and per-node handoff description |
| [`docs/DATA_SCHEMA.md`](../docs/DATA_SCHEMA.md) | Every table, column, and the append-only rationale in detail |
| [`docs/API.md`](../docs/API.md) | Full endpoint reference with request/response shapes |
| [`docs/TIME_SAVINGS.md`](../docs/TIME_SAVINGS.md) | How the manual-vs-agent time comparison on the dashboard is computed |
| [`docs/diagrams/`](../docs/diagrams/) | Graphviz `.dot` source + rendered `.svg` for the architecture and agent-graph diagrams |
| [`CHANGELOG.md`](../CHANGELOG.md) | Every notable change, in Keep a Changelog format |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md) | Development workflow and code review process |
| [`backend/README.md`](../backend/README.md) | Backend directory-level tour |
| [`frontend/README.md`](../frontend/README.md) | Frontend directory-level tour |
| `docs/DECISIONS.md` *(local-only, not in the published repo)* | Full narrative of every non-obvious design decision and why — the "why" behind almost everything summarized in this file |
| `docs/R&D.md` *(local-only, not in the published repo)* | The research memo that led to the Exa/Hunter.io MCP integrations |

This file, `demo/DOCUMENTATION.md`, is meant to be the one place a new
reader can start without needing to open any of the above — but every
claim in it traces back to one of these documents or directly to the
source code, and none of it should be treated as more authoritative than
the code itself if the two ever disagree.
