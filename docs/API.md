# API Reference

The full interactive OpenAPI specification is served by the running backend
at `/docs` (Swagger UI) and `/openapi.json`. This document is a shorter,
human-oriented description of each endpoint and how a marketer or another
system would use it. All endpoints are prefixed with `/api`.

## Health

### `GET /api/health`
Liveness check. Returns `{"status": "ok"}`. Used by deployment tooling and
by the frontend to confirm the backend is reachable.

## Personas

### `GET /api/personas`
List all saved personas, most recent first.

### `POST /api/personas`
Create a persona: role, seniority, industry, company_size_band, geography,
and an open-ended `custom_traits` JSON object. Used by the Setup screen.

### `GET /api/personas/{id}`, `PUT /api/personas/{id}`, `DELETE /api/personas/{id}`
Standard CRUD for a single persona.

## Solutions

### `GET /api/solutions`
List all saved solutions.

### `POST /api/solutions`
Create a solution: name, problem_solved, value_props, icp_filters,
differentiators, and the selected outreach `channels` (email, linkedin,
phone, events). This is also where channel selection lives.

### `GET /api/solutions/{id}`, `PUT /api/solutions/{id}`, `DELETE /api/solutions/{id}`
Standard CRUD for a single solution.

Note: the pipeline always uses the most recently created persona and solution
as the "active" configuration. Multi-persona support is a scoped-out stretch
goal documented in DECISIONS.md.

## Data ingestion

### `POST /api/uploads/crm-csv`
Multipart file upload. Expects a CSV with at minimum `name` and `company`
columns, plus optional `title`, `company_size`, `industry`, `geography`,
`email`, and CRM activity columns (`deal_stage`, `last_activity`,
`last_activity_date`, `notes`, `deal_value`). Rows missing `name` or
`company` are skipped and reported, not fatal. Leads are deduplicated by
email first, then by (name, company). Returns an ingestion report: rows
parsed/skipped, reasons for any skips, and counts of leads/signals created.

### `POST /api/uploads/website-events`
Multipart file upload. Expects a JSON array of event objects, each with a
lead identifier (`lead_email`, `email`, or `lead_id`, or a `name` +
`company` pair), a `page`, an `event_type` hint, and a `timestamp`. Events
that cannot be matched to an existing lead are skipped and reported.

## Leads

### `GET /api/leads`
List every lead with its latest (non-superseded) stage classification and
latest outreach plan status, for the pipeline table view.

### `GET /api/leads/{id}`
Full detail for one lead: profile, complete signal history, full
classification history (including superseded entries), and the latest
outreach plan.

### `GET /api/leads/{id}/trace`
The ordered list of `agent_runs` for this lead — this is the multi-agent
trace view: which agent ran, when, what it concluded, and why.

### `POST /api/leads/{id}/signals`
Append one or more new raw signals to an existing lead, simulating a live
event arriving (e.g. a fresh pricing page visit). Does not itself
re-classify the lead — follow with a call to `/api/pipeline/run` to see the
stage and plan update in response to the new signal.

## Pipeline

### `POST /api/pipeline/run`
Body: `{"lead_ids": ["..."] }` or `{"lead_ids": null}` to run every lead.
Runs the full LangGraph agent graph (Signal Extraction -> Persona Fit ->
Buying Stage Orchestrator -> conditional approval routing -> Outreach
Planner -> Explainability) for each lead, persists the resulting
classification, plan, and agent_runs rows, and returns per-lead results
including measured latency. This is the endpoint used both for the initial
classification and for every re-classification after new signals arrive or
after a marketer asks to regenerate a plan.

## Approvals

### `POST /api/approvals/plans/{plan_id}`
Body: `{"action": "approve"|"reject"|"edit", "notes": "...", "approved_by": "...", "edited_touchpoints": [...]}`.
Approves, rejects, or edits-and-approves an outreach plan. Every action is
also recorded in `approval_events`.

### `POST /api/approvals/classifications/{classification_id}`
Body: `{"action": "approve"|"reject", "notes": "..."}`. Used for
low-confidence stage classifications that were routed to
`pending_approval` by the graph's conditional edge.

## Dashboard

### `GET /api/dashboard/stats`
Aggregate pipeline statistics: total leads, stage distribution, plans
generated, plans pending approval, average classification confidence,
measured average agent latency, and the manual-vs-agent time comparison
numbers used on the dashboard's time-saved chart.
