// Single source of truth for the 5-agent pipeline's display metadata —
// shared between the Agent Trace tab and the live PipelineProgressPanel so
// labels/colors/order never drift between the two.
import { Radar, UserCheck, Gauge, Send, MessageSquareText, type LucideIcon } from "lucide-react"

export const AGENT_ORDER = [
  "signal_extraction",
  "persona_fit",
  "buying_stage_orchestrator",
  "outreach_planner",
  "explainability",
] as const

export type AgentName = (typeof AGENT_ORDER)[number]

export const AGENT_LABELS: Record<string, string> = {
  signal_extraction: "Signal Extraction",
  persona_fit: "Persona Fit",
  buying_stage_orchestrator: "Buying Stage Orchestrator",
  outreach_planner: "Outreach Planner",
  explainability: "Explainability",
}

export const AGENT_ACCENT_VARS: Record<string, string> = {
  signal_extraction: "--agent-signal-extraction",
  persona_fit: "--agent-persona-fit",
  buying_stage_orchestrator: "--agent-buying-stage",
  outreach_planner: "--agent-outreach-planner",
  explainability: "--agent-explainability",
}

// One distinct icon per agent role, learned once and then recognizable
// without reading text — used in the Agent Trace mini-timeline and
// per-step headers.
export const AGENT_ICONS: Record<string, LucideIcon> = {
  signal_extraction: Radar,
  persona_fit: UserCheck,
  buying_stage_orchestrator: Gauge,
  outreach_planner: Send,
  explainability: MessageSquareText,
}

// What each agent is actually doing while its status is "running" — shown
// live in the progress panel, not generic "Loading..." filler.
export const AGENT_RUNNING_INSIGHT: Record<string, string> = {
  signal_extraction:
    "Classifying raw CRM and website events into buying-stage signals",
  persona_fit:
    "Comparing this lead's role, industry, and company size against the target persona",
  buying_stage_orchestrator:
    "Weighing signal recency and strength to decide early, mid, or late stage",
  outreach_planner:
    "Drafting touchpoint copy and checking whether the lead's email is deliverable",
  explainability:
    "Writing a plain-language narrative tying the whole decision together",
}
