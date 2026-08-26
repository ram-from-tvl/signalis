import { apiClient } from "./client"
import type {
  AgentRun,
  AgentRunFollowup,
  DashboardStats,
  IngestionReport,
  LeadDetail,
  LeadListItem,
  Persona,
  PersonaInput,
  PipelineRanking,
  PipelineRunResponse,
  Solution,
  SolutionInput,
  ToolApprovalRequest,
  ToolApprovalResolution,
} from "@/types/api"

export const personasApi = {
  list: () => apiClient.get<Persona[]>("/api/personas").then((r) => r.data),
  create: (payload: PersonaInput) =>
    apiClient.post<Persona>("/api/personas", payload).then((r) => r.data),
  update: (id: string, payload: PersonaInput) =>
    apiClient.put<Persona>(`/api/personas/${id}`, payload).then((r) => r.data),
  remove: (id: string) => apiClient.delete(`/api/personas/${id}`),
}

export const solutionsApi = {
  list: () => apiClient.get<Solution[]>("/api/solutions").then((r) => r.data),
  create: (payload: SolutionInput) =>
    apiClient.post<Solution>("/api/solutions", payload).then((r) => r.data),
  update: (id: string, payload: SolutionInput) =>
    apiClient.put<Solution>(`/api/solutions/${id}`, payload).then((r) => r.data),
  remove: (id: string) => apiClient.delete(`/api/solutions/${id}`),
}

export const uploadsApi = {
  crmCsv: (file: File) => {
    const formData = new FormData()
    formData.append("file", file)
    return apiClient
      .post<IngestionReport>("/api/uploads/crm-csv", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data)
  },
  websiteEvents: (file: File) => {
    const formData = new FormData()
    formData.append("file", file)
    return apiClient
      .post<IngestionReport>("/api/uploads/website-events", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data)
  },
}

export const leadsApi = {
  list: () => apiClient.get<LeadListItem[]>("/api/leads").then((r) => r.data),
  detail: (id: string) => apiClient.get<LeadDetail>(`/api/leads/${id}`).then((r) => r.data),
  trace: (id: string) => apiClient.get<AgentRun[]>(`/api/leads/${id}/trace`).then((r) => r.data),
  appendSignals: (id: string, signals: Record<string, unknown>[]) =>
    apiClient
      .post<IngestionReport>(`/api/leads/${id}/signals`, { signals })
      .then((r) => r.data),
}

export const agentFollowupsApi = {
  list: (leadId: string, agentRunId: string) =>
    apiClient
      .get<AgentRunFollowup[]>(`/api/leads/${leadId}/agent-runs/${agentRunId}/followups`)
      .then((r) => r.data),
  ask: (leadId: string, agentRunId: string, question: string) =>
    apiClient
      .post<AgentRunFollowup>(`/api/leads/${leadId}/agent-runs/${agentRunId}/ask`, { question })
      .then((r) => r.data),
}

export const pipelineApi = {
  run: (leadIds?: string[]) =>
    apiClient
      .post<PipelineRunResponse>("/api/pipeline/run", { lead_ids: leadIds ?? null })
      .then((r) => r.data),
}

export const approvalsApi = {
  actOnPlan: (
    planId: string,
    payload: { action: "approve" | "reject" | "edit"; notes?: string; approved_by?: string; edited_touchpoints?: unknown }
  ) => apiClient.post(`/api/approvals/plans/${planId}`, payload).then((r) => r.data),
  actOnClassification: (
    classificationId: string,
    payload: { action: "approve" | "reject"; notes?: string }
  ) => apiClient.post(`/api/approvals/classifications/${classificationId}`, payload).then((r) => r.data),
}

export const dashboardApi = {
  stats: () => apiClient.get<DashboardStats>("/api/dashboard/stats").then((r) => r.data),
}

export const rankingApi = {
  run: () => apiClient.post<PipelineRanking>("/api/ranking/run").then((r) => r.data),
  latest: () => apiClient.get<PipelineRanking | null>("/api/ranking/latest").then((r) => r.data),
}

export const toolApprovalsApi = {
  listForLead: (leadId: string) =>
    apiClient
      .get<ToolApprovalRequest[]>("/api/tool-approvals", { params: { lead_id: leadId } })
      .then((r) => r.data),
  approve: (requestId: string) =>
    apiClient
      .post<ToolApprovalResolution>(`/api/tool-approvals/${requestId}/approve`)
      .then((r) => r.data),
  reject: (requestId: string, reason?: string) =>
    apiClient
      .post<ToolApprovalResolution>(`/api/tool-approvals/${requestId}/reject`, { reason: reason ?? "" })
      .then((r) => r.data),
}
