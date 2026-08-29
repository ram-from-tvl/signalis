# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once a `1.0.0` tag is cut.

## [Unreleased]

### Added
- Repository-wide documentation set: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  `SECURITY.md`, `docs/ARCHITECTURE.md`, `backend/README.md`,
  `frontend/README.md`, `LICENSE` (MIT), and issue/PR templates.
- `.pr_agent.toml` configuring Qodo Merge: automatic review and description
  on every pull request, re-review on push, custom issue and compliance
  guidelines specific to this codebase's known failure patterns.

### Changed
- Split `app/models/entities.py` and `app/schemas/schemas.py` into
  per-domain modules under `app/models/` and `app/schemas/`, each re-exported
  from a clean package `__init__.py`. Fixed a pre-existing gap where
  `PipelineRanking` wasn't exported from `app.models` at all.
- Added `app/api/router.py` to aggregate all route modules under one router.
- Renamed `app/core/trueforge_setup.py` to `trueforge_bootstrap.py`.
- Modernized `app/main.py`'s startup hook to a `lifespan` context manager.
- Filled in `[project]` metadata in `backend/pyproject.toml` and added
  `backend/requirements.txt`; CI now installs from the pinned lockfile
  instead of an unconstrained or hardcoded dependency list.

## [0.6.0] — Live pipeline progress and readable signal detail

### Changed
- Replaced the disabled-button-only "Running agents..." state with a live
  per-agent progress panel (`PipelineProgressPanel.tsx`) that polls the
  existing agent trace endpoint while a pipeline run is in flight: each of
  the five agents shows pending/running/completed/failed status, a real
  one-line description of what it's doing while running, and a genuine
  result summary (classified stage/confidence, persona fit, touchpoint
  count, signal count) the instant it completes (#17).
- Gave the bulk "Run Pipeline for All Leads" action an equivalent
  indeterminate progress bar with rotating agent-name status text, since
  it spans many leads at once rather than one traceable run (#17).
- Replaced the raw `JSON.stringify(signal.raw_payload)` dump in Signal
  History with labeled, readable detail lines, filtering out fields
  already shown elsewhere in the row (#17).
- Swapped the type pairing from Instrument Serif + Instrument Sans to DM
  Serif Display + Plus Jakarta Sans (#17).
- Centralized agent display metadata (`lib/agents.ts`) — order, labels,
  accent colors, running-state insight text — previously duplicated inline
  in `LeadDetailPage.tsx` (#17).

### Fixed
- Backend timestamps serialized without a timezone marker (Python's
  `datetime.utcnow()`) were being parsed by the browser as local time
  instead of UTC, silently shifting every displayed timestamp in the app
  by the browser's UTC offset. Fixed with a shared `parseUtcTimestamp`
  helper applied everywhere a backend timestamp is formatted (#17).

## [0.5.0] — Visual polish pass

### Changed
- Added an ambient, very-low-opacity gradient-mesh background across the
  app shell (respects `prefers-reduced-motion`), animated sliding
  indicators for the sidebar nav and Lead Detail tabs, and richer
  button hover/press feedback using the existing shadow token scale (#16).
- Data Sources: real drag-and-drop upload zones (with click-to-browse
  fallback) and a new "Pipeline at a glance" 3-step explainer, reusing the
  existing timeline pattern from Lead Detail (#16).
- Dashboard stat tiles gained an animated count-up on load and hover lift;
  empty states across pages now use a consistent icon-in-circle treatment
  (#16).
- Setup page's channel selection reworked from plain checkboxes into
  pill-style toggle chips, matching the badge visual language already used
  elsewhere on the page (#16).
- Added a small click-spark micro-interaction on the two human-approval
  actions (Approve Plan, Approve Classification) (#16).

## [0.4.0] — UI redesign

### Changed
- Redesigned the frontend around a light, warm off-white / sage-slate /
  terracotta palette applied per the 60/30/10 rule, with Instrument Serif
  and Instrument Sans typography, verified WCAG contrast, and skeleton
  loading states across dashboard, pipeline, and lead-detail views (#3).
- Fixed the dashboard stage chart and agent-trace border colors, both of
  which had drifted to hardcoded values instead of the shared design tokens.
- Hardened error handling so a transient network/server failure surfaces
  its own error state instead of being misreported as "not found" or
  rendering an infinite loading skeleton.

## [0.3.0] — Prioritization agent

### Added
- A sixth, standalone Prioritization/Ranking Agent: ranks the whole lead
  pipeline by contact priority on demand, with a plain-language reason per
  lead rather than a raw sort by stage or confidence (#2).
- `PipelineRanking` model and `/api/ranking` endpoints (`run`, `latest`).
- A Priority Queue panel on the dashboard surfacing the latest ranking.

## [0.2.0] — TrueForge migration

### Changed
- Every agent's reasoning now runs as a session/turn on a local TrueForge
  agent harness process instead of a direct LLM SDK call. TrueForge owns
  the agent loop (model calls, MCP tool discovery/execution, context
  management); a LangGraph `StateGraph` remains the Python-side coordinator
  of node order and the confidence-threshold branch.
- The Persona Fit agent now calls a real remote MCP server for firmographic
  enrichment (`classify_company_industry`, `estimate_company_size_band`)
  instead of an in-process function call.
- Buying-stage signal scoring runs inside a Daytona sandbox, with a local
  fallback if the sandbox is briefly unreachable.
- Gemini remains the primary model; a Hugging Face model is used as a
  genuine fallback (not a canned response) if TrueForge or Gemini is
  unavailable.

### Fixed
- Prevented code injection in the script string generated for sandboxed
  signal scoring.
- Wrapped Hugging Face fallback JSON decode errors as a typed `LLMError`
  instead of letting a raw parse exception propagate.
- Recovered JSON from free-text Hugging Face fallback responses that didn't
  return a clean JSON body.

## [0.1.0] — Initial release

### Added
- FastAPI backend: five-agent LangGraph pipeline (Signal Extraction,
  Persona Fit, Buying Stage Orchestrator, Outreach Planner, Explainability),
  SQLAlchemy models, Pydantic schemas, and REST endpoints for personas,
  solutions, leads, pipeline runs, approvals, uploads, and dashboard stats.
- Human-approval checkpoint: every generated outreach plan, and any
  low-confidence classification, is persisted as `pending_approval` and
  requires explicit approve/reject before being treated as final.
- React + TypeScript frontend: dashboard, lead pipeline, lead detail with
  agent trace, dataset upload, and persona/solution setup screens.
- Bundled sample CRM export and website event log for a working demo
  without external data.
- Full test suite (unit, API, one real end-to-end integration test) and a
  CI workflow gating lint, tests, and build on every pull request.
- Initial documentation: `README.md`, `docs/API.md`, `docs/DATA_SCHEMA.md`,
  `docs/AGENT_GRAPH.md`, `docs/TIME_SAVINGS.md`.
