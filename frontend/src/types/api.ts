export interface Persona {
  id: string
  role: string
  seniority: string
  industry: string
  company_size_band: string
  geography: string
  custom_traits: Record<string, unknown>
  created_at: string
}

export interface PersonaInput {
  role: string
  seniority: string
  industry: string
  company_size_band: string
  geography: string
  custom_traits?: Record<string, unknown>
}

export interface Solution {
  id: string
  name: string
  problem_solved: string
  value_props: string[]
  icp_filters: Record<string, unknown>
  differentiators: string[]
  channels: string[]
  created_at: string
}

export interface SolutionInput {
  name: string
  problem_solved: string
  value_props: string[]
  icp_filters?: Record<string, unknown>
  differentiators: string[]
  channels: string[]
}

export interface Lead {
  id: string
  name: string
  company: string
  title: string
  company_size: string
  industry: string
  geography: string
  email: string
  created_at: string
}

export interface Signal {
  id: string
  raw_source: string
  raw_payload: Record<string, unknown>
  event_type: string
  intent_stage_hint: string
  occurred_at: string
  extracted_at: string
}

export type Stage = "early" | "mid" | "late"
export type ApprovalStatus = "auto_approved" | "pending_approval" | "approved" | "rejected"
export type PlanStatus = "pending_approval" | "approved" | "rejected" | "superseded"

export interface StageClassification {
  id: string
  lead_id: string
  stage: Stage
  confidence: number
  justification: string
  persona_fit_result: {
    fit?: string
    reasoning?: string
    missing_data?: string[]
  }
  requires_approval: boolean
  approval_status: ApprovalStatus
  created_at: string
  superseded_by_id: string | null
}

export interface Touchpoint {
  day_offset: number
  channel: string
  content_theme: string
  message_copy: string
}

export interface OutreachPlan {
  id: string
  lead_id: string
  stage_classification_id: string
  touchpoints: Touchpoint[]
  channels: string[]
  messaging_examples: string[]
  status: PlanStatus
  created_at: string
  approved_at: string | null
  approved_by: string | null
}

export interface AgentRun {
  id: string
  lead_id: string | null
  agent_name: string
  input_summary: string
  output: Record<string, unknown>
  reasoning: string
  status: string
  started_at: string
  completed_at: string | null
}

export interface LeadListItem {
  lead: Lead
  latest_classification: StageClassification | null
  latest_plan_status: PlanStatus | null
}

export interface LeadDetail {
  lead: Lead
  signals: Signal[]
  classification_history: StageClassification[]
  latest_plan: OutreachPlan | null
}

export interface PipelineRunResult {
  lead_id: string
  classification: StageClassification
  plan: OutreachPlan
  explainability_narrative: string
  latency_seconds: number
}

export interface PipelineRunResponse {
  results: PipelineRunResult[]
  errors: { lead_id: string; error: string }[]
}

export interface IngestionReport {
  rows_parsed: number
  rows_skipped: number
  skipped_reasons: string[]
  leads_created: number
  leads_updated: number
  signals_created: number
}

export interface DashboardStats {
  total_leads: number
  stage_distribution: Record<string, number>
  plans_generated: number
  plans_pending_approval: number
  average_confidence: number
  average_agent_latency_seconds: number
  manual_minutes_per_lead_estimate: number
  agent_seconds_per_lead_actual: number
}

export interface RankedLeadEntry {
  lead_id: string
  rank: number
  reasoning: string
  name: string
  company: string
  title: string
  stage: Stage
  confidence: number
}

export interface SubagentDelegation {
  status: "delegated" | "partial" | "evidence_unavailable" | "not_delegated"
  used: boolean
  subagent_count: number | null
  expected_count: number
  subagents: Record<string, unknown>[]
}

export interface PipelineRanking {
  id: string
  summary: string
  ranked_leads: RankedLeadEntry[]
  subagent_delegation: SubagentDelegation | null
  created_at: string
}

// Mirrors backend/app/models/tool_approval.py's ToolApprovalStatus, the
// single source of truth for this contract (there is no shared codegen in
// this repo — see docs/DECISIONS.md). Keep these three files in sync:
// backend/app/models/tool_approval.py, backend/app/schemas/tool_approval.py,
// and this one.
export type ToolApprovalStatus = "pending" | "claimed" | "approved" | "rejected"

export interface ToolApprovalRequest {
  id: string
  lead_id: string | null
  agent_run_id: string | null
  trueforge_agent_name: string
  tool_name: string
  tool_input: Record<string, unknown>
  status: ToolApprovalStatus
  created_at: string
  resolved_at: string | null
}

// Response body for POST /api/tool-approvals/{id}/approve|reject. `resolved`
// is always the approval request that was just acted on; `followup` is set
// only when the resumed TrueForge turn immediately hit another approval
// gate, in which case the pipeline is still paused and not "done".
export interface ToolApprovalResolution {
  resolved: ToolApprovalRequest
  followup: ToolApprovalRequest | null
}
