# Agent Graph

Signalis runs five agents as nodes in a LangGraph `StateGraph`, plus a sixth,
standalone Prioritization/Ranking Agent that is not part of that per-lead
graph (see "The sixth agent" below). Each node's actual reasoning executes
as a session/turn on a local TrueForge agent harness process rather than a
bare model API call. TrueForge owns the real agent loop for that step —
model calls, MCP tool discovery/execution, context management — and the
Python graph node only starts the turn and reads back its structured output
(`app/core/trueforge.py`). LangGraph remains the Python-side coordinator: it
is what decides node order and evaluates the one real conditional edge (the
confidence-threshold branch), while TrueForge is what each individual node
actually runs on.

```mermaid
flowchart TD
    Start([Pipeline triggered for a lead]) --> SE["Signal Extraction Agent\n(TrueForge session)"]
    SE --> PF["Persona Fit Agent\n(TrueForge session + MCP tools)"]
    PF -->|"MCP tool calls"| MCP[["signalis-enrichment MCP server\nclassify_company_industry\nestimate_company_size_band"]]
    MCP --> PF
    PF --> BS["Buying Stage Orchestrator Agent\n(TrueForge session)"]
    BS -->|"scoring computation"| SB[["Daytona sandbox\nrecency/strength-weighted score"]]
    SB --> BS
    BS --> Decision{confidence below threshold?}
    Decision -->|yes, low confidence| OP["Outreach Planner Agent\n(TrueForge session)"]
    Decision -->|no, high confidence| OP
    OP --> EX["Explainability Agent\n(TrueForge session)"]
    EX --> Persist[(Persist stage_classification + outreach_plan\nboth start pending_approval)]
    Persist --> Human{Marketer reviews in UI}
    Human -->|approve| Approved[Classification / Plan marked approved]
    Human -->|reject| Rejected[Classification / Plan marked rejected]
    Human -->|edit touchpoints, then approve| Approved

    RankStart([Marketer clicks Rank Pipeline]) --> Rank["Prioritization/Ranking Agent\n(TrueForge session)"]
    ClassStore[(stage_classifications\nnon-superseded rows)] --> Rank
    Rank --> RankPersist[(Persist PipelineRanking snapshot)]
```

Every "TrueForge session" box falls back to a direct Gemini call, and then
to a Hugging Face model, if TrueForge is not running or a turn fails — see
`docs/DECISIONS.md` for the fallback chain and why it exists. The
Prioritization/Ranking Agent (bottom of the diagram) runs independently of
the per-lead pipeline above it — it is triggered separately and reads across
all leads at once rather than being a node in the per-lead graph.

## Node responsibilities and handoffs

1. **Signal Extraction Agent** receives every raw, not-yet-classified
   `signals` row for the lead (CRM fields and website events, exactly as
   ingested, including malformed or sparse ones). It returns a normalized
   `event_type` and an `intent_stage_hint` per record. Its output updates
   the `signals` rows in place and is handed forward as a summary.

2. **Persona Fit Agent** receives the lead's firmographic profile plus the
   currently active persona and solution ICP, and has the
   `signalis-enrichment` MCP server attached. When the lead's industry or
   company size is missing or worth verifying, it genuinely calls the
   `classify_company_industry` and/or `estimate_company_size_band` MCP tools
   before answering — this is TrueForge discovering and invoking a real
   remote tool over MCP, not a Python function call embedded in the agent's
   own code. It returns a fit classification (`full_fit` / `partial_fit` /
   `mismatch`), reasoning, and any missing data it had to work around. This
   result is handed to both the Buying Stage Orchestrator (fit context
   informs how much weight to give ambiguous signals) and later to the
   Outreach Planner (fit context shapes message tone).

3. **Buying Stage Orchestrator Agent** receives the lead's full signal
   history (all `signals`, not just the newly extracted ones, within the
   configured rolling window) plus the persona fit result. Before calling
   the model, the graph node computes a recency/strength-weighted signal
   score by executing generated Python inside a Daytona sandbox
   (`app/core/sandbox.py`) — falling back to an equivalent local computation
   if the sandbox is briefly unreachable — and passes that score to the
   agent as supporting evidence. The agent then returns the overall `stage`,
   a `confidence` score, and a `justification`. This is the node whose
   confidence score drives the graph's one real conditional branch; its
   persisted output also records `signal_score_computed_via` (`"daytona"` or
   `"local"`) so which execution path actually ran is independently
   verifiable after the fact.

4. **Conditional edge**: if confidence is below
   `confidence_approval_threshold` (default 0.5, configurable), the
   classification is flagged `requires_approval=True` and its
   `approval_status` is set to `pending_approval` instead of
   `auto_approved`. Both branches still proceed to plan generation — the
   difference is purely in how the resulting classification is flagged, not
   whether a plan gets produced. See DECISIONS.md for why every plan,
   regardless of branch, still requires a separate approval step.

5. **Outreach Planner Agent** receives the stage, confidence, persona fit
   result, active persona, and active solution, and returns a 3-5 touchpoint
   micro-plan with day offsets, channels, content themes, and example
   message copy. The resulting `outreach_plans` row always starts as
   `pending_approval`.

6. **Explainability Agent** receives the structured outputs of all four
   prior agents plus the `requires_approval` flag, and synthesizes a
   coherent narrative explaining the whole reasoning chain in plain
   language. This narrative is what the UI's agent trace view foregrounds
   for a fast, non-technical read of "why did the system land here."

## Runtime architecture: LangGraph coordinates, TrueForge executes

Each node function in `app/agents/graph.py` calls its corresponding agent
module (`app/agents/signal_extraction.py`, etc.), which in turn calls
`app.agents.common.run_agent_reasoning`. That function is the actual
TrueForge integration point: it registers a named TrueForge agent (once,
idempotently) with the node's system instruction and model, starts a
session, runs one turn with the node's prompt, and polls until the turn
completes or errors. The registered instruction always embeds the node's
JSON response schema directly, so TrueForge's model call — whichever
provider it is actually routed to — returns exactly the structured shape the
rest of the pipeline expects.

If TrueForge is disabled (`TRUEFORGE_ENABLED=false`) or unreachable, the same
call falls back to a direct Gemini call and then a Hugging Face call
(`app/core/llm.py`), using the identical prompt and schema — this is a
transport fallback, not a second reasoning path. Because the direct fallback
has no MCP tool access, any tool-referencing instruction (Persona Fit's) is
rewritten for that path specifically to tell the model no tools are
available, rather than leaving it to try invoking tools that do not exist
in that call.

## Human-approval checkpoint

Every outreach plan produced by the graph starts in `pending_approval`
status and never becomes usable output until a marketer explicitly approves
it via `POST /api/approvals/plans/{id}`. Separately, a low-confidence stage
classification (confidence below the threshold) is flagged
`pending_approval` at the classification level too, so a marketer can reject
the classification itself before deciding whether the plan built on top of
it makes sense. Both approval paths write an immutable `approval_events`
row.

Separately from this application-level checkpoint, TrueForge itself provides
a native per-tool human-approval primitive: an MCP server attached to an
agent can set `require_approval_for_tools`, and any matching tool call then
pauses the turn with `state.status == "done"` and
`required_actions: [{"type": "tool.approval_required", ...}]` until a
`user.tool_approval` turn input resumes it. This build does not gate the
enrichment tool behind it — see `docs/DECISIONS.md` for why — but the
primitive was exercised directly against the running harness to confirm it
genuinely pauses and resumes a turn, not just that the field exists in the
API schema.

## Re-running the graph

The same graph is invoked again, unmodified, whenever new signals arrive for
a lead or a marketer asks to regenerate a plan. Each run supersedes the
lead's previous `stage_classification` and `outreach_plan` (via
`superseded_by_id` / `status="superseded"`) rather than overwriting them, so
the full decision history remains inspectable.

## The sixth agent: Prioritization/Ranking

Unlike the five agents above, the Prioritization/Ranking Agent
(`app/agents/prioritization.py`) does not run per-lead as part of the graph.
It runs once across the whole pipeline, on demand (`POST /api/ranking/run`),
over every lead's current (non-superseded) `stage_classification`. Given
every lead's stage, confidence, and justification in one prompt, it returns
a single priority order — rank 1 is who to contact first — with a specific,
non-generic reason per lead, using the same TrueForge session/turn mechanism
and Gemini→Hugging Face fallback chain as the other five agents. It
deliberately does not just sort by stage label: a high-confidence, recently
active mid-stage lead can legitimately outrank a low-confidence, stale
late-stage one, and the agent's own reasoning is expected to say so.

Results are persisted as an append-only `PipelineRanking` snapshot (see
`docs/DATA_SCHEMA.md`), the same pattern as `stage_classifications` and
`outreach_plans` — re-running the ranking creates a new snapshot rather than
mutating the previous one, so a manager can see how the priority order
shifted over time. The audit trail for this agent's own run is a regular
`agent_runs` row with `lead_id = NULL`, since it reasons about the pipeline
as a whole rather than any single lead.
