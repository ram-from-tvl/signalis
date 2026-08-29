# Time Savings: Manual vs. Agent-Generated

## The manual baseline

For a marketing/SDR team without Signalis, assessing one lead and drafting an
outreach plan typically involves:

| Step | Rough time |
|---|---|
| Pull CRM activity, deal notes, and last-touch history | 5-7 minutes |
| Check website analytics / marketing automation for page visits, downloads, email engagement | 5-7 minutes |
| Judge buying stage and write a one-line justification | 3-5 minutes |
| Draft a 3-5 touch outreach sequence with channel and message copy per touch | 8-12 minutes |
| **Total** | **~21-31 minutes per lead** |

We use **25 minutes per lead** as the illustrative baseline throughout the
dashboard and this document. This is a rough estimate consistent with how
the brief frames this comparison ("a simple before/after narrative...not a
rigorous benchmark study"), not a measured field study.

## The agent-generated side (measured, not estimated)

Unlike the manual baseline, the agent-side number shown on the dashboard is
**not** an illustrative guess. It is computed from real `agent_runs` rows:
`average_agent_latency_seconds` is the mean wall-clock duration
(`completed_at - started_at`) across every completed agent invocation
recorded in the database, and `agent_seconds_per_lead_actual` multiplies that
by 5 (one call per agent in the pipeline: Signal Extraction, Persona Fit,
Buying Stage Orchestrator, Outreach Planner, Explainability).

In practice, a single call in this pipeline — Hugging Face's
`Qwen/Qwen3-4B-Instruct-2507` by default, the primary provider, with
`gemini-3.6-flash` as the configured Gemini fallback — completes in
roughly 5-30 seconds depending on prompt size, tool calls made, and
provider load, putting the full five-agent pipeline for one lead typically
in the 1-4 minute range for a lead with a non-trivial signal history and
genuine MCP tool use. This number will differ run to run since it depends
on live model latency (and, when a provider is briefly rate-limited or out
of quota, on how many models/keys the call had to rotate through before
succeeding — see `docs/DECISIONS.md`), which is exactly why it is measured
from real timestamps rather than hardcoded.

## The comparison

| | Manual | Signalis (agent-generated) |
|---|---|---|
| Time per lead | ~25 minutes (illustrative) | tens of seconds (measured) |
| Consistency across reps | Varies by rep experience and time pressure | Same reasoning process every time |
| Reasoning visible after the fact | Rarely written down | Every agent's output and justification persisted and viewable in the agent trace |
| Updates when new signals arrive | Requires the rep to notice and redo the work | Re-triggerable in one action; produces an updated classification and plan |

Even taking the conservative end of the agent-side range, this represents on
the order of a **20-40x reduction in hands-on time per lead**, freeing the
rep's time for the parts of outreach that genuinely require a human: judgment
calls on messaging tone, relationship context, and the actual conversation
with the buyer. The dashboard's time-saved chart renders both bars to scale
and computes the multiple directly from the live measured average, so the
number shown in the running system will track real latency rather than this
document's illustrative range.
