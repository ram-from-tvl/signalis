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

// Plain-language labels for external checks an agent genuinely ran — must
// stay in sync with backend/app/agents/tool_activity.py's TOOL_LABELS.
// Same present-participle, outcome-focused voice as AGENT_RUNNING_INSIGHT:
// no "MCP", "tool call", "session", or server names anywhere here.
export const TOOL_ACTIVITY_LABELS: Record<string, string> = {
  classify_company_industry: "Looked up the company's industry",
  estimate_company_size_band: "Estimated the company's size",
  search_company_news: "Checked recent news about the company",
  search_company_semantic: "Searched for more context about the company",
  verify_email: "Verified the lead's email address",
}

export function toolActivityLabel(toolName: string): string {
  return TOOL_ACTIVITY_LABELS[toolName] ?? toolName.replace(/_/g, " ")
}

// The pending tool-approval card must read like a plain question a
// non-technical marketer would understand, never expose "MCP"/tool
// names/raw JSON. Keyed by tool_name since that's all the request payload
// reliably carries a stable identity for.
const APPROVAL_QUESTIONS: Record<string, string> = {
  classify_company_industry:
    "We'd like to look up this company's industry using an outside data source before deciding how well it fits your target persona.",
  estimate_company_size_band:
    "We'd like to estimate this company's size using an outside data source before deciding how well it fits your target persona.",
}

export function toolApprovalQuestion(toolName: string): string {
  return (
    APPROVAL_QUESTIONS[toolName] ??
    "We'd like to run one more check using an outside data source before continuing."
  )
}
