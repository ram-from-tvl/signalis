# Verifying each agentic behavior

A checklist mapping each required agentic behavior to the concrete UI
action or API call that exercises it — useful for reviewing the app or
confirming a change hasn't quietly broken one of them.

| Behavior | How to see it |
|---|---|
| **Autonomous pull + summarize** | Pipeline screen → **Run Pipeline for All Leads** runs the full 5-agent graph for every lead, no per-row manual classification. |
| **Live re-classification on new signals** | Lead detail → **Simulate New Signal** appends a high-intent event and re-runs the pipeline; stage/confidence/justification update immediately and the prior classification is marked `superseded`. |
| **Plan regeneration** | Lead detail → **Regenerate Plan** produces a new `pending_approval` plan reflecting current state; the previous plan is marked `superseded`, not deleted. |
| **Human-approval checkpoint** | Every new outreach plan carries an Approve/Reject action. A classification the model itself was unconfident about gets its own separate Approve/Reject action above the plan. |
| **Visible multi-agent trace** | Lead detail → **Agent Trace** tab lists every persisted `agent_runs` row for that lead, in order, with input summary and full reasoning — inspectable after the fact, not a live-only view. |
| **Real MCP tool use** | With TrueForge + all 4 MCP servers running, Persona Fit genuinely calls `classify_company_industry` / `estimate_company_size_band`, and — when useful — `search_company_news` (Tavily) / `search_company_semantic` (Exa). Outreach Planner calls `verify_email`/`find_email` (Hunter.io). Visible on TrueForge's own session events (`GET /api/v1/sessions/{id}/events`, port 8790), not just the agent's final answer. The lead detail page's classification card and Agent Trace tab also surface which checks genuinely ran, in plain language. |
| **Sandboxed code execution** | Every Buying Stage run computes its signal score by executing generated Python in the configured Daytona sandbox; `agent_runs.output.signal_score_computed_via` records `"daytona"` or `"local"` depending on which path actually ran. |
| **Human approval as a harness primitive** | Persona Fit's enrichment tools are gated by TrueForge's `require_approval_for_tools`. A call pauses the turn and shows an inline approval card on the lead detail page; approving resumes the same TrueForge turn, rejecting resumes with a denial and marks the run failed. |
| **Subagent delegation** | Dashboard → **Rank Pipeline** with several classified leads present. The ranking response's `subagent_delegation` field (or TrueForge's session events) shows genuine parallel `create_sub_agent` calls, one per lead, feeding into the root agent's final order. |
| **Persistent-session follow-up** | Agent Trace tab → ask a follow-up question against any entry that ran through TrueForge. The answer is a real continuation turn on that run's own session, not a fresh call re-fed a summary. |
| **Pipeline-wide prioritization** | Dashboard → **Rank Pipeline** runs the Prioritization Agent over every classified lead, returning one contact-priority order with a reason per lead — not a naive sort by stage label. |
| **Multiple concurrent campaigns** | Setup screen → each campaign has its own persona + solution; leads are scored against their assigned campaign, falling back to whichever campaign is marked default. See [DATA_SCHEMA.md](DATA_SCHEMA.md) and [API.md](API.md) for the `campaigns` model. |
