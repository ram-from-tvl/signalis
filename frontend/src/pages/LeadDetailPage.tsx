import { useState } from "react"
import { useParams, Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { leadsApi, pipelineApi, approvalsApi, toolApprovalsApi, campaignsApi } from "@/api/endpoints"
import { ApiError } from "@/api/client"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { ClickSpark } from "@/components/ui/click-spark"
import { CopyButton } from "@/components/ui/copy-button"
import { MarkdownLite } from "@/components/ui/markdown-lite"
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { StageBadge } from "@/components/leads/StageBadge"
import { ConfidenceMeter } from "@/components/leads/ConfidenceMeter"
import { PipelineProgressPanel } from "@/components/leads/PipelineProgressPanel"
import { AgentTraceTab } from "@/components/leads/AgentTraceTab"
import { ConfidenceTrendChart } from "@/components/leads/ConfidenceTrendChart"
import { SignalHistoryTab } from "@/components/leads/SignalHistoryTab"
import { ApprovePlanButton } from "@/components/leads/ApprovePlanButton"
import { useToast } from "@/components/ui/toast-context"
import { ArrowLeft, Sparkles, TrendingUp, CheckCircle2, XCircle, ShieldAlert, Search, Loader2, Play, Send, History } from "lucide-react"
import { parseUtcTimestamp } from "@/lib/utils"
import { toolApprovalQuestion } from "@/lib/agents"

function formatDate(iso: string) {
  return parseUtcTimestamp(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

type BadgeVariant = "success" | "destructive" | "warning" | "outline"

// Every value email_verification_status can actually hold (Hunter.io's own
// statuses, plus this app's own not-queried/failure states) gets an
// explicit, distinct label here — an unhandled status must never silently
// collapse into "will bounce", since accept_all/unknown are genuinely
// uncertain, not failures.
function emailVerificationBadge(status: string): { variant: BadgeVariant; label: string } {
  switch (status) {
    case "valid":
      return { variant: "success", label: "Email verified" }
    case "invalid":
      return { variant: "destructive", label: "Email invalid — will bounce" }
    case "accept_all":
      return { variant: "warning", label: "Email uncertain (accept-all domain)" }
    case "webmail":
      return { variant: "warning", label: "Email uncertain (webmail address)" }
    case "disposable":
      return { variant: "destructive", label: "Email invalid — disposable address" }
    case "unknown":
      return { variant: "warning", label: "Email deliverability unknown" }
    case "verification_failed":
      return { variant: "warning", label: "Verification failed — unconfirmed" }
    case "evidence_unavailable":
      return { variant: "outline", label: "Verification evidence unavailable" }
    case "unverified":
      return { variant: "outline", label: "Email unverified" }
    default:
      return { variant: "outline", label: "Email unverified" }
  }
}

export function LeadDetailPage() {
  const { leadId } = useParams<{ leadId: string }>()
  const queryClient = useQueryClient()
  const { push } = useToast()
  const [simulatingSignal, setSimulatingSignal] = useState(false)
  const [activeTab, setActiveTab] = useState("plan")

  const {
    data: detail,
    isLoading,
    error: detailError,
  } = useQuery({
    queryKey: ["lead-detail", leadId],
    queryFn: () => leadsApi.detail(leadId!),
    enabled: !!leadId,
    // a confirmed 404 means "no such lead" and retrying won't help; anything
    // else (network error, 5xx) is transient and should keep the default retries
    retry: (failureCount, err) => (err instanceof ApiError && err.status === 404 ? false : failureCount < 3),
  })
  const isNotFound = detailError instanceof ApiError && detailError.status === 404

  const { data: campaigns, isError: campaignsErrored } = useQuery({
    queryKey: ["campaigns"],
    queryFn: campaignsApi.list,
  })

  const {
    data: trace,
    error: traceError,
    isLoading: traceLoading,
  } = useQuery({
    queryKey: ["lead-trace", leadId],
    queryFn: () => leadsApi.trace(leadId!),
    enabled: !!leadId,
    retry: (failureCount, err) => (err instanceof ApiError && err.status === 404 ? false : failureCount < 3),
  })

  const {
    data: toolApprovals,
    error: toolApprovalsError,
    isLoading: toolApprovalsLoading,
  } = useQuery({
    queryKey: ["tool-approvals", leadId],
    queryFn: () => toolApprovalsApi.listForLead(leadId!),
    enabled: !!leadId,
    retry: (failureCount, err) => (err instanceof ApiError && err.status === 404 ? false : failureCount < 3),
  })

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["lead-detail", leadId] })
    queryClient.invalidateQueries({ queryKey: ["lead-trace", leadId] })
    queryClient.invalidateQueries({ queryKey: ["tool-approvals", leadId] })
    queryClient.invalidateQueries({ queryKey: ["leads"] })
    queryClient.invalidateQueries({ queryKey: ["dashboard-stats"] })
  }

  const runPipeline = useMutation({
    mutationFn: () => pipelineApi.run([leadId!]),
    onSuccess: (response) => {
      invalidateAll()
      if (response.errors.length > 0) {
        push({ title: "Pipeline failed", description: response.errors[0].error, variant: "error" })
      } else {
        push({ title: "Classification and plan regenerated", variant: "success" })
      }
    },
    onError: (err: Error) => push({ title: "Pipeline run failed", description: err.message, variant: "error" }),
  })

  const simulateSignal = useMutation({
    mutationFn: () =>
      leadsApi.appendSignals(leadId!, [
        {
          raw_source: "website",
          payload: { page: "/pricing", event_type: "pricing_page_visit", simulated: true },
          occurred_at: new Date().toISOString(),
        },
      ]),
    onSuccess: async () => {
      setSimulatingSignal(false)
      push({ title: "New signal appended", description: "Pricing page visit recorded just now", variant: "info" })
      invalidateAll()
      await runPipeline.mutateAsync()
    },
    onError: (err: Error) => push({ title: "Could not append signal", description: err.message, variant: "error" }),
  })

  const approvePlan = useMutation({
    mutationFn: (planId: string) => approvalsApi.actOnPlan(planId, { action: "approve", approved_by: "marketer" }),
    onSuccess: () => {
      invalidateAll()
      push({ title: "Outreach plan approved", variant: "success" })
    },
  })

  const rejectPlan = useMutation({
    mutationFn: (planId: string) => approvalsApi.actOnPlan(planId, { action: "reject", approved_by: "marketer" }),
    onSuccess: () => {
      invalidateAll()
      push({ title: "Outreach plan rejected", variant: "info" })
    },
  })

  const approveClassification = useMutation({
    mutationFn: (classificationId: string) =>
      approvalsApi.actOnClassification(classificationId, { action: "approve" }),
    onSuccess: () => {
      invalidateAll()
      push({ title: "Classification approved", variant: "success" })
    },
  })

  const rejectClassification = useMutation({
    mutationFn: (classificationId: string) =>
      approvalsApi.actOnClassification(classificationId, { action: "reject" }),
    onSuccess: () => {
      invalidateAll()
      push({ title: "Classification rejected", variant: "info" })
    },
  })

  const approveToolCall = useMutation({
    mutationFn: (requestId: string) => toolApprovalsApi.approve(requestId),
    onSuccess: (resolution) => {
      invalidateAll()
      if (resolution.followup) {
        push({
          title: "Check confirmed",
          description: "One more check needs your OK before we can continue — review it below.",
          variant: "info",
        })
      } else {
        push({
          title: "Check confirmed",
          description: "Click \"Regenerate Plan\" to continue.",
          variant: "success",
        })
      }
    },
    onError: (err: Error) => push({ title: "Couldn't confirm that check", description: err.message, variant: "error" }),
  })

  const rejectToolCall = useMutation({
    mutationFn: (requestId: string) => toolApprovalsApi.reject(requestId),
    onSuccess: (resolution) => {
      invalidateAll()
      if (resolution.followup) {
        push({
          title: "Check skipped",
          description: "One more check needs your OK before we can continue — review it below.",
          variant: "info",
        })
      } else {
        push({ title: "Check skipped", variant: "info" })
      }
    },
    onError: (err: Error) => push({ title: "Couldn't skip that check", description: err.message, variant: "error" }),
  })

  if (isLoading) {
    return (
      <div className="flex flex-col gap-6 pb-16" aria-busy="true" aria-live="polite">
        <Skeleton className="h-4 w-32" />
        <div>
          <Skeleton className="h-7 w-56 mb-2" />
          <Skeleton className="h-4 w-72" />
        </div>
        <Card>
          <CardContent className="py-6">
            <Skeleton className="h-4 w-full mb-2" />
            <Skeleton className="h-4 w-5/6 mb-2" />
            <Skeleton className="h-4 w-2/3" />
          </CardContent>
        </Card>
      </div>
    )
  }
  if (isNotFound) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
        <p className="font-heading text-xl">Lead not found</p>
        <p className="text-sm text-muted-foreground max-w-sm">
          This lead may have been removed, or the link is incorrect.
        </p>
        <Button asChild variant="outline" className="mt-2">
          <Link to="/leads">Back to pipeline</Link>
        </Button>
      </div>
    )
  }
  if (!detail) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
        <p className="font-heading text-xl">Couldn't load this lead</p>
        <p className="text-sm text-muted-foreground max-w-sm">
          {detailError instanceof Error ? detailError.message : "Something went wrong contacting the server."}
        </p>
        <Button variant="outline" className="mt-2" onClick={() => queryClient.invalidateQueries({ queryKey: ["lead-detail", leadId] })}>
          Try again
        </Button>
      </div>
    )
  }

  const { lead, signals, classification_history, latest_plan } = detail
  const latestClassification = classification_history[0]
  const hasPriorRuns = classification_history.length > 0 || (trace?.length ?? 0) > 0

  return (
    <div className="flex flex-col gap-6 pb-16">
      <Link to="/leads" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground w-fit">
        <ArrowLeft className="h-3.5 w-3.5" /> Back to pipeline
      </Link>

      <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
        <div>
          <h1 className="font-heading text-2xl font-bold">{lead.name}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {lead.title || "Title unknown"} at {lead.company} &middot; {lead.industry || "industry unknown"} &middot; {lead.company_size || "size unknown"}
          </p>
          {campaignsErrored ? (
            <Badge variant="outline" className="mt-2 text-destructive">
              Campaign unknown — couldn't load campaigns
            </Badge>
          ) : (
            (() => {
              const campaign = campaigns?.find((c) => c.id === lead.campaign_id)
              return campaign ? (
                <Badge variant="secondary" className="mt-2">
                  Campaign: {campaign.name}
                </Badge>
              ) : null
            })()
          )}
        </div>
        <div className="flex gap-2 shrink-0">
          <Button
            variant="outline"
            onClick={() => {
              setSimulatingSignal(true)
              simulateSignal.mutate()
            }}
            disabled={simulateSignal.isPending || runPipeline.isPending}
          >
            {simulatingSignal && simulateSignal.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
            ) : (
              <TrendingUp className="h-4 w-4" />
            )}
            {simulatingSignal && simulateSignal.isPending ? "Simulating..." : "Simulate New Signal"}
          </Button>
          <Button variant="accent" onClick={() => runPipeline.mutate()} disabled={runPipeline.isPending}>
            {runPipeline.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
            ) : hasPriorRuns ? (
              <Sparkles className="h-4 w-4" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            {runPipeline.isPending ? "Running agents..." : hasPriorRuns ? "Regenerate Plan" : "Generate Plan"}
          </Button>
        </div>
      </div>

      {runPipeline.isPending ? (
        <PipelineProgressPanel leadId={leadId!} />
      ) : latestClassification ? (
        <Card>
          <CardHeader className="flex-row items-center justify-between gap-4 flex-wrap">
            <div>
              <CardTitle>Current Buying Stage</CardTitle>
              <CardDescription>From the Buying Stage Orchestrator Agent</CardDescription>
            </div>
            <div className="flex items-center gap-3">
              <StageBadge stage={latestClassification.stage} />
              <ConfidenceMeter confidence={latestClassification.confidence} />
              {latestClassification.approval_status === "pending_approval" && (
                <Badge variant="warning">Awaiting Approval</Badge>
              )}
              {latestClassification.approval_status === "approved" && <Badge variant="success">Approved</Badge>}
              {latestClassification.approval_status === "auto_approved" && (
                <Badge variant="secondary">Auto-approved (high confidence)</Badge>
              )}
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <MarkdownLite text={latestClassification.justification} className="text-sm leading-relaxed" />
            {latestClassification.persona_fit_result?.reasoning && (
              <div className="rounded-lg bg-secondary/50 p-3 text-sm">
                <p className="font-semibold mb-1">
                  Persona fit: <span className="capitalize">{latestClassification.persona_fit_result.fit}</span>
                </p>
                <MarkdownLite text={latestClassification.persona_fit_result.reasoning} className="text-muted-foreground" />
                {!!latestClassification.persona_fit_result.missing_data?.length && (
                  <p className="text-xs text-warning mt-1">
                    Missing data: {latestClassification.persona_fit_result.missing_data.join(", ")}
                  </p>
                )}
                {!!latestClassification.persona_fit_result.tools_used?.items.length && (
                  <div className="flex items-center gap-1.5 flex-wrap mt-2">
                    <span className="text-xs text-muted-foreground">External checks used:</span>
                    {latestClassification.persona_fit_result.tools_used.items.map((t) => (
                      <Badge key={t.tool} variant="outline" className="text-[10px]">
                        {t.label}
                      </Badge>
                    ))}
                  </div>
                )}
                {latestClassification.persona_fit_result.tools_used?.evidence_unavailable && (
                  <p className="text-xs text-muted-foreground mt-2">
                    Couldn't confirm which external checks ran for this assessment.
                  </p>
                )}
              </div>
            )}
            {latestClassification.approval_status === "pending_approval" && (
              <div className="flex gap-2">
                <ClickSpark>
                  <Button
                    size="sm"
                    variant="default"
                    onClick={() => approveClassification.mutate(latestClassification.id)}
                  >
                    <CheckCircle2 className="h-4 w-4" /> Approve Classification
                  </Button>
                </ClickSpark>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => rejectClassification.mutate(latestClassification.id)}
                >
                  <XCircle className="h-4 w-4" /> Reject
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="py-8 flex flex-col items-center gap-2 text-center text-sm text-muted-foreground">
            <Sparkles className="h-5 w-5 text-muted-foreground/60" />
            No classification yet. Click "Generate Plan" to run the agent pipeline for this lead.
          </CardContent>
        </Card>
      )}

      {toolApprovalsLoading && (
        <Card aria-busy="true" aria-live="polite">
          <CardContent className="py-4">
            <Skeleton className="h-4 w-48 mb-2" />
            <Skeleton className="h-4 w-full" />
          </CardContent>
        </Card>
      )}
      {!toolApprovalsLoading && toolApprovalsError && (
        <Card>
          <CardContent className="py-4 text-sm text-muted-foreground">
            Couldn't load pending tool approvals.{" "}
            {toolApprovalsError instanceof Error ? toolApprovalsError.message : "Something went wrong contacting the server."}
            <Button
              variant="outline"
              size="sm"
              className="mt-3 block"
              onClick={() => queryClient.invalidateQueries({ queryKey: ["tool-approvals", leadId] })}
            >
              Try again
            </Button>
          </CardContent>
        </Card>
      )}
      {!toolApprovalsLoading &&
        !toolApprovalsError &&
        (toolApprovals ?? []).map((request) => (
          <Card key={request.id} className="border-warning/50">
            <CardHeader className="flex-row items-center justify-between gap-4 flex-wrap">
              <div className="flex items-center gap-2">
                <Search className="h-4 w-4 text-warning shrink-0" />
                <div>
                  <CardTitle>One More Check Before Continuing</CardTitle>
                  <CardDescription>{toolApprovalQuestion(request.tool_name)}</CardDescription>
                </div>
              </div>
              <Badge variant="warning">Awaiting Your OK</Badge>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <div className="flex gap-2">
                <ClickSpark>
                  <Button
                    size="sm"
                    variant="default"
                    onClick={() => approveToolCall.mutate(request.id)}
                    disabled={approveToolCall.isPending || rejectToolCall.isPending}
                  >
                    <CheckCircle2 className="h-4 w-4" /> Go Ahead
                  </Button>
                </ClickSpark>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => rejectToolCall.mutate(request.id)}
                  disabled={approveToolCall.isPending || rejectToolCall.isPending}
                >
                  <XCircle className="h-4 w-4" /> Skip This Check
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <div className="sticky top-0 z-20 -mx-1 px-1 py-2 bg-background/80 backdrop-blur-sm">
          <TabsList>
            <TabsTrigger value="plan" active={activeTab === "plan"}>Outreach Plan</TabsTrigger>
            <TabsTrigger value="signals" active={activeTab === "signals"}>Signal History</TabsTrigger>
            <TabsTrigger value="trace" active={activeTab === "trace"}>Agent Trace</TabsTrigger>
            <TabsTrigger value="history" active={activeTab === "history"}>Classification History</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="plan">
          {latest_plan ? (
            <Card>
              <CardHeader className="flex-row items-center justify-between flex-wrap gap-3">
                <div>
                  <CardTitle>Outreach Micro-Plan</CardTitle>
                  <CardDescription>
                    {latest_plan.channels.join(", ") || "No channels"} &middot; {latest_plan.touchpoints.length} touchpoint(s)
                  </CardDescription>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  {latest_plan.email_verification_status && (
                    <Badge
                      variant={emailVerificationBadge(latest_plan.email_verification_status).variant}
                      title={latest_plan.email_verification_reason ?? undefined}
                    >
                      {emailVerificationBadge(latest_plan.email_verification_status).label}
                    </Badge>
                  )}
                  <Badge
                    variant={
                      latest_plan.status === "approved"
                        ? "success"
                        : latest_plan.status === "rejected"
                          ? "destructive"
                          : latest_plan.status === "superseded"
                            ? "outline"
                            : "warning"
                    }
                  >
                    {latest_plan.status.replace("_", " ")}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                {latest_plan.email_verification_status &&
                  ["invalid", "disposable"].includes(latest_plan.email_verification_status) && (
                    <div className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/10 p-3">
                      <ShieldAlert className="h-4 w-4 shrink-0 text-destructive mt-0.5" />
                      <p className="text-xs text-destructive">
                        <span className="font-semibold">
                          {emailVerificationBadge(latest_plan.email_verification_status).label}.
                        </span>{" "}
                        Sending to this address risks a bounce and damages sender reputation — verify or
                        replace the lead's email before approving.
                      </p>
                    </div>
                  )}
                <ol className="flex flex-col gap-3">
                  {latest_plan.touchpoints.map((tp, i) => (
                    <li key={i} className="relative rounded-lg border border-border p-4 pl-12">
                      <span className="absolute left-4 top-4 flex h-6 w-6 items-center justify-center rounded-full bg-secondary text-xs font-semibold text-secondary-foreground">
                        {i + 1}
                      </span>
                      <div className="flex items-center gap-2 mb-2 flex-wrap justify-between">
                        <div className="flex items-center gap-2 flex-wrap">
                          <Badge variant="secondary">Day {tp.day_offset}</Badge>
                          <Badge variant="outline" className="capitalize">{tp.channel}</Badge>
                          <span className="text-sm font-semibold">{tp.content_theme}</span>
                        </div>
                        {tp.channel.toLowerCase() === "email" &&
                        latest_plan.email_verification_status === "unverified" ? (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span>
                                <CopyButton value={tp.message_copy} label="Copy email" />
                              </span>
                            </TooltipTrigger>
                            <TooltipContent>This email hasn't been verified — it may not be deliverable.</TooltipContent>
                          </Tooltip>
                        ) : (
                          <CopyButton
                            value={tp.message_copy}
                            label={tp.channel.toLowerCase() === "linkedin" ? "Copy LinkedIn message" : `Copy ${tp.channel}`}
                          />
                        )}
                      </div>
                      <p className="text-sm leading-relaxed text-foreground/90 whitespace-pre-wrap">{tp.message_copy}</p>
                    </li>
                  ))}
                </ol>
                {latest_plan.status === "pending_approval" && (
                  <div className="flex gap-2">
                    <ApprovePlanButton onConfirm={() => approvePlan.mutate(latest_plan.id)} />
                    <Button size="sm" variant="outline" onClick={() => rejectPlan.mutate(latest_plan.id)}>
                      <XCircle className="h-4 w-4" /> Reject Plan
                    </Button>
                  </div>
                )}
                {latest_plan.status === "approved" && (
                  <p className="text-xs text-muted-foreground">
                    Approved {latest_plan.approved_at ? formatDate(latest_plan.approved_at) : ""} by {latest_plan.approved_by}
                  </p>
                )}
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardContent className="py-8 flex flex-col items-center gap-2 text-center text-sm text-muted-foreground">
                <Send className="h-5 w-5 text-muted-foreground/60" />
                No outreach plan generated yet.
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="signals">
          <SignalHistoryTab signals={signals} />
        </TabsContent>

        <TabsContent value="trace">
          <AgentTraceTab
            trace={trace}
            traceLoading={traceLoading}
            traceError={traceError}
            onRetry={() => queryClient.invalidateQueries({ queryKey: ["lead-trace", leadId] })}
            leadId={leadId!}
          />
        </TabsContent>

        <TabsContent value="history">
          <div className="flex flex-col gap-3">
            {classification_history.length === 0 && (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <History className="h-4 w-4 text-muted-foreground/60" />
                No classification history yet.
              </p>
            )}
            <ConfidenceTrendChart history={classification_history} />
            {classification_history.map((c) => (
              <Card key={c.id} className={c.superseded_by_id ? "opacity-60" : ""}>
                <CardContent className="py-4 flex items-center justify-between gap-4 flex-wrap">
                  <div className="flex items-center gap-3">
                    <StageBadge stage={c.stage} />
                    <ConfidenceMeter confidence={c.confidence} />
                    {c.superseded_by_id && <Badge variant="outline">Superseded</Badge>}
                  </div>
                  <span className="text-xs text-muted-foreground">{formatDate(c.created_at)}</span>
                </CardContent>
              </Card>
            ))}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}
