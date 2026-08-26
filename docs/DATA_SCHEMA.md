# Data Schema

Signalis persists all state in a single SQLite database (`backend/signalis.db`),
created automatically from the SQLAlchemy models on application startup. There
is still no migration framework in this build: new tables are created via
`Base.metadata.create_all`, sufficient for every schema change so far except
one — a nullable column added to an already-existing table
(`agent_runs.trueforge_session_id`), which `create_all` does not retrofit
onto existing rows. That one case is handled by a small, purpose-built
additive-only patcher, `app/db/migrations.py::run_startup_migrations`, run
right after `create_all` on every startup: it inspects each table's current
columns and issues `ALTER TABLE ... ADD COLUMN` only for ones genuinely
missing, so it is a no-op on a fresh database (where `create_all` already
created the column) and idempotent on repeated runs. This was judged the
right scope for a single-column addition; see DECISIONS.md for why Alembic
was not pulled in for it. Tables that represent a decision the system
makes (stage classifications, outreach plans) are append-only history tables
rather than rows that get overwritten in place, so the full reasoning history
behind any current state is always inspectable.

## Tables

### personas

Defines the marketer's target buyer profile.

| Column | Type | Notes |
|---|---|---|
| id | string (uuid hex) | primary key |
| role | string | e.g. "VP of Sales" |
| seniority | string | e.g. "VP / Director" |
| industry | string | free text, may list several |
| company_size_band | string | e.g. "51-1000" |
| geography | string | free text |
| custom_traits | JSON | open-ended extra criteria (buying committee size, priorities, etc.) |
| created_at | datetime | |

Only the most recently created persona is treated as "active" by the pipeline
(see DECISIONS.md for why multi-persona support was scoped out).

### solutions

Defines what is being sold and how it should be positioned in outreach.

| Column | Type | Notes |
|---|---|---|
| id | string (uuid hex) | primary key |
| name | string | |
| problem_solved | text | |
| value_props | JSON (list[str]) | |
| icp_filters | JSON | ideal customer profile filter criteria |
| differentiators | JSON (list[str]) | |
| channels | JSON (list[str]) | selected outreach channels: email, linkedin, phone, events |
| created_at | datetime | |

Channel selection is stored directly as a JSON column on `solutions` rather
than as a separate preferences table, since channels are solution-scoped, not
user- or workspace-scoped, in this build's data model (see DECISIONS.md).

### leads

One row per person/account being tracked.

| Column | Type | Notes |
|---|---|---|
| id | string (uuid hex) | primary key |
| name | string | |
| company | string | |
| title | string | |
| company_size | string | |
| industry | string | |
| geography | string | |
| email | string | used as the primary dedupe key across CRM and website uploads |
| created_at | datetime | |

### signals

Raw and classified interaction events for a lead, from any source.

| Column | Type | Notes |
|---|---|---|
| id | string (uuid hex) | primary key |
| lead_id | FK -> leads.id | |
| raw_source | string | one of `crm`, `website`, `email`, `linkedin` |
| raw_payload | JSON | the original row/event fields, kept verbatim |
| event_type | string | normalized label assigned by the Signal Extraction Agent (defaults to `unknown` until processed) |
| intent_stage_hint | string | `early` / `mid` / `late`, assigned by the Signal Extraction Agent |
| occurred_at | datetime | when the interaction actually happened |
| extracted_at | datetime | when the row was ingested |
| extracted_by_agent_run_id | FK -> agent_runs.id, nullable | which Signal Extraction run classified this signal |

### agent_runs

The audit trail backing the agent trace view. One row per agent invocation.

| Column | Type | Notes |
|---|---|---|
| id | string (uuid hex) | primary key |
| lead_id | FK -> leads.id, nullable | |
| agent_name | string | `signal_extraction`, `persona_fit`, `buying_stage_orchestrator`, `outreach_planner`, `explainability` |
| input_summary | text | short human-readable description of what was fed in |
| output | JSON | the full structured output returned by the agent |
| reasoning | text | the plain-language reasoning/narrative for this run |
| status | string | `running` / `completed` / `failed` |
| started_at | datetime | |
| completed_at | datetime, nullable | used to compute real agent latency for the dashboard |
| trueforge_session_id | string, nullable | the TrueForge session that produced this run's reasoning; null when the direct-Gemini/Hugging-Face fallback path was used instead (that path never creates a TrueForge session). When present, a marketer's follow-up question can be answered as a real continuation turn on this exact session — see `agent_run_followups` below and DECISIONS.md. Added via an additive `ALTER TABLE` in `app/db/migrations.py` since this project has no migration framework and the column was added to an already-existing table. |

### agent_run_followups

A marketer's follow-up Q&A exchanges against one `agent_runs` row's
reasoning, answered as a genuine continuation turn on that run's
`trueforge_session_id`. Append-only, same philosophy as
`stage_classifications`/`outreach_plans`: every question/answer pair is its
own row, so a marketer's full follow-up history for a trace entry survives
a page reload.

| Column | Type | Notes |
|---|---|---|
| id | string (uuid hex) | primary key |
| agent_run_id | FK -> agent_runs.id | the run whose TrueForge session this question was asked against |
| question | text | the marketer's free-text question |
| answer | text | the model's free-text answer, from a turn run on the same TrueForge session as the original run |
| created_at | datetime | |

### tool_approval_requests

TrueForge's native per-tool approval gate (`require_approval_for_tools`),
distinct from `approval_events` below. Persists a paused TrueForge turn so a
marketer can review and resolve it through the UI instead of the turn
hanging until an out-of-band API call resumes it.

| Column | Type | Notes |
|---|---|---|
| id | string (uuid hex) | primary key |
| lead_id | FK -> leads.id, nullable | |
| agent_run_id | FK -> agent_runs.id, nullable | the paused run this request belongs to |
| trueforge_agent_name | string | |
| session_id | string | |
| turn_id | string | |
| thread_id | string | |
| tool_call_id | string | |
| tool_name | string | e.g. `classify_company_industry` |
| tool_input | JSON | the tool call's arguments, shown to the marketer verbatim |
| status | string | `pending` / `claimed` / `approved` / `rejected` |
| created_at | datetime | |
| resolved_at | datetime, nullable | |

`claimed` is a short-lived transitional state: the approve/reject endpoint
atomically claims a `pending` row (a conditional `UPDATE ... WHERE status =
'pending'`) before making any TrueForge call, so two concurrent decisions on
the same request can't both proceed.

### stage_classifications

Full history of buying-stage decisions for a lead. Never mutated after
creation; a new classification supersedes the previous one via
`superseded_by_id`.

| Column | Type | Notes |
|---|---|---|
| id | string (uuid hex) | primary key |
| lead_id | FK -> leads.id | |
| stage | string | `early` / `mid` / `late` |
| confidence | float | 0.0-1.0 |
| justification | text | plain-language explanation from the Buying Stage Orchestrator Agent |
| persona_fit_result | JSON | snapshot of the Persona Fit Agent's output at classification time |
| based_on_agent_run_id | FK -> agent_runs.id, nullable | |
| requires_approval | boolean | true when confidence fell below the approval threshold |
| approval_status | string | `auto_approved` / `pending_approval` / `approved` / `rejected` |
| created_at | datetime | |
| superseded_by_id | FK -> stage_classifications.id, nullable | set once a newer classification exists for the same lead |

### outreach_plans

Full history of generated outreach micro-plans. Also never mutated in place
except by an explicit approve/reject/edit action.

| Column | Type | Notes |
|---|---|---|
| id | string (uuid hex) | primary key |
| lead_id | FK -> leads.id | |
| stage_classification_id | FK -> stage_classifications.id | the classification this plan was generated for |
| touchpoints | JSON | list of `{day_offset, channel, content_theme, message_copy}` |
| channels | JSON (list[str]) | channels used in this plan |
| messaging_examples | JSON (list[str]) | flattened message copy, convenient for list views |
| status | string | `pending_approval` / `approved` / `rejected` / `superseded` |
| created_at | datetime | |
| approved_at | datetime, nullable | |
| approved_by | string, nullable | |

Every newly generated plan starts as `pending_approval`, regardless of the
confidence of the classification it is based on — this is the mandatory
human-approval checkpoint described in the product brief.

### approval_events

An immutable log of every approve/reject/edit action taken by a marketer.

| Column | Type | Notes |
|---|---|---|
| id | string (uuid hex) | primary key |
| outreach_plan_id | FK -> outreach_plans.id, nullable | |
| stage_classification_id | FK -> stage_classifications.id, nullable | |
| action | string | `approve` / `reject` / `edit` |
| notes | text | optional marketer commentary |
| created_at | datetime | |

### pipeline_rankings

An append-only snapshot produced by the Prioritization/Ranking Agent each
time it runs. Unlike every other table above, this one is not scoped to a
single lead — one row represents one ranking pass over the whole pipeline.

| Column | Type | Notes |
|---|---|---|
| id | string (uuid hex) | primary key |
| agent_run_id | FK -> agent_runs.id, nullable | the ranking agent's own audit-trail row (`agent_runs.lead_id` is NULL for this run) |
| ranked_leads | JSON | list of `{lead_id, rank, reasoning, name, company, title, stage, confidence}`, one entry per currently-classified lead |
| summary | text | one-paragraph narrative explaining the overall priority order |
| created_at | datetime | |

Each `ranked_leads` entry is a self-contained snapshot, not a pointer to
live `leads`/`stage_classifications` rows: the lead's name, company, title,
stage, and confidence are baked in at write time. This is deliberate — if
the lead is reclassified after this ranking ran, `GET /api/ranking/latest`
still shows the stage/confidence that were true when the ranking was
generated, so the priority order and its stated reasoning never drift out
of sync with what is displayed alongside them. It also means reading a
ranking back needs zero additional database queries regardless of how many
leads were ranked.

## Relationships at a glance

```
personas            solutions
   (latest active)      (latest active)
        \                /
         \              /
              leads
                |
          ------+------
          |            |
       signals    agent_runs
          |            |----> agent_run_followups
          |            |----> tool_approval_requests
          |            |
          +--> stage_classifications --> outreach_plans
                       |                       |
                       +----> approval_events <+
                       |
                       +----> pipeline_rankings (reads across all leads at once)
```
