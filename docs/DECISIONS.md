# Design Decisions and Trade-offs

This document records the choices made while building Signalis, why they
were made, and what was deliberately simplified for scope reasons.

## Stack

- **SQLite over a client/server database.** The brief calls for real
  persistence, not in-memory-only state; it does not require multi-user
  concurrent write throughput. SQLite gives real durability and a real
  relational schema with zero operational overhead for a local/demo
  deployment, and SQLAlchemy's ORM layer means moving to Postgres later is a
  connection-string change, not a rewrite.
- **LangGraph over a hand-rolled if/else pipeline.** The brief explicitly
  asks for genuine multi-agent coordination, not one large prompt. LangGraph
  gives a real `StateGraph` with typed shared state and an actual
  conditional edge (the confidence-threshold branch), so the orchestration
  logic is declarative and inspectable rather than embedded procedurally in
  a service function.
- **Gemini 2.5 Flash via `google-genai`, with structured JSON schemas.**
  Every agent call uses `response_schema` / `response_mime_type:
  application/json` rather than parsing free text, so agent output is
  reliably structured without needing a second "fix the JSON" pass. The
  model id is read once from `GEMINI_MODEL` in `app/core/config.py` and
  never hardcoded elsewhere.
- **No vector store.** Nothing in this build needs semantic retrieval over
  an unbounded corpus — every agent's context is a small, fully enumerable
  set of rows scoped to one lead (its signals, its persona, its solution).
  Adding a vector store would have been complexity with no corresponding
  requirement.

## Scope reductions from the original implementation plan

The original planning document called for a custom agent-harness runtime
with MCP tool connectivity, sandboxed code execution as a first-class tool,
and session persistence across reconnects. That scope was deliberately cut
in favor of the stack above. What was *not* cut: every agent still makes a
real, separately-invokable LLM call with genuine reasoning; the graph still
has a genuine conditional branch and real handoffs between nodes; every
agent's structured output and reasoning text is persisted and inspectable
after the fact via the agent trace; and there is a real, enforced
human-approval checkpoint before any plan is treated as final. Those are the
properties the brief actually asks for, and none of them depend on the
heavier runtime architecture that was scoped out.

## Ambiguities in the brief and how they were resolved

- **Stage granularity.** The brief allows "early/mid/late or a finer-grained
  scale." Three stages were kept, on the grounds that a finer scale would
  need meaningfully more training/prompting care to stay well-calibrated
  than this build's time budget allowed, and three stages are already
  enough to drive materially different outreach plans.
- **Confidence approval threshold.** Set to **0.5** in
  `app/core/config.py` (`confidence_approval_threshold`), configurable via
  environment variable. Chosen as a natural midpoint: below 0.5 means the
  model itself is expressing more uncertainty than confidence about the
  assigned stage, which is exactly the case where a human sanity check adds
  the most value.
- **Every generated plan requires approval, not just low-confidence ones.**
  The brief's approval-checkpoint language is phrased around "before an
  outreach plan is finalized/marked ready-to-send" as one example trigger,
  and separately around low-confidence classifications as another. This
  build implements both: every newly generated `outreach_plan` starts as
  `pending_approval` regardless of the confidence of the classification
  behind it, and additionally, low-confidence classifications themselves
  require a separate approval step before being treated as final. This is
  the safer reading of "stop and ask a human before an important action" —
  sending outreach copy to a real prospect is the important action, not
  merely computing a confidence score.
- **Channel selection storage.** Channels are stored as a JSON list on
  `solutions.channels` rather than as a separate `preferences` table,
  because in this data model channels are a property of what is being sold
  and how it should be pitched, not a property of a user account (there is
  no multi-user auth in this build) or of an individual lead.
- **Multi-persona / multi-solution support.** The schema fully supports
  multiple personas and solutions (they are ordinary tables, not
  singletons), but the pipeline resolves "the active persona" and "the
  active solution" as simply the most recently created row of each. Full
  per-lead or per-segment persona assignment was treated as the stretch
  goal the brief explicitly calls optional, not core scope.
- **"Autonomous pull and summarize."** Interpreted as: the Signal Extraction
  Agent runs over whatever raw signals exist for a lead without a human
  hand-classifying each row first, and the pipeline is triggerable for the
  entire lead list in one action rather than requiring a human to walk
  through leads one at a time. A background scheduler was not built, since
  the brief explicitly allows simulating "new events arriving" via a
  user-triggered mechanism rather than a live webhook integration, and a
  scheduler adds operational complexity (a running process, drift between
  demo and production behavior) without changing what agentic behavior is
  actually being demonstrated.
- **The fifth agent.** Explainability was chosen over
  Prioritization/Ranking or Feedback/Learning because it directly serves
  the brief's "usable by a non-technical marketer" and "visible multi-agent
  trace" success criteria: it turns four separate structured outputs into
  one narrative a marketer can read in ten seconds, which is exactly the
  translation layer a non-technical user needs to trust the system's
  output.

## Known limitations

- The "active persona/solution" model means this build does not support
  running two different go-to-market motions concurrently against the same
  lead list. This was an explicit, documented trade-off above rather than an
  oversight.
- Signal Extraction re-classifies only signals it has not yet processed
  (tracked via `extracted_by_agent_run_id`); it does not re-classify a
  previously extracted signal if the extraction prompt or model changes.
  This keeps re-runs fast and idempotent, at the cost of not automatically
  backfilling old signals if extraction logic improves later.
- There is no authentication/authorization layer. This is a single-tenant
  demo system; adding real auth was out of scope for the time budget and
  does not change any of the agentic behavior being demonstrated.
