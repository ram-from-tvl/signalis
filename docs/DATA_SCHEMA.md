# Data Schema

Signalis persists all state in a single SQLite database (`backend/signalis.db`),
created automatically from the SQLAlchemy models on application startup. There
are no migrations in this build: the schema is created via
`Base.metadata.create_all`, which is sufficient for a system with no
production upgrade history yet. Tables that represent a decision the system
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
          |            |
          +--> stage_classifications --> outreach_plans
                       |                       |
                       +----> approval_events <+
```
