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
