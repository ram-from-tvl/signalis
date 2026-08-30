import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { AgentRunFollowupPanel } from "@/components/leads/AgentRunFollowupPanel"
import { AGENT_ORDER, AGENT_LABELS, AGENT_ACCENT_VARS, AGENT_ICONS, toolActivityLabel } from "@/lib/agents"
import { Clock, Search } from "lucide-react"
import { cn, parseUtcTimestamp } from "@/lib/utils"
import type { AgentRun } from "@/types/api"

function formatDate(iso: string) {
  return parseUtcTimestamp(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

function formatDuration(startedAt: string, completedAt: string | null) {
  if (!completedAt) return null
  const ms = parseUtcTimestamp(completedAt).getTime() - parseUtcTimestamp(startedAt).getTime()
  if (ms < 0) return null
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

// A short, real one-line outcome per agent, read from its already-persisted
// output — the same idea as the live progress panel's completedDetailFor,
// so a collapsed trace step still says something concrete before expanding.
function oneLineOutcome(run: AgentRun): string | null {
  const output = run.output
  if (run.agent_name === "buying_stage_orchestrator" && typeof output.stage === "string") {
    const confidence = typeof output.confidence === "number" ? Math.round(output.confidence * 100) : null
    return confidence !== null ? `${output.stage} stage, ${confidence}% confidence` : `${output.stage} stage`
  }
  if (run.agent_name === "persona_fit" && typeof output.fit === "string") {
    return output.fit.replace(/_/g, " ")
  }
  if (run.agent_name === "outreach_planner" && Array.isArray(output.touchpoints)) {
    return `${output.touchpoints.length} touchpoint(s) drafted`
  }
  if (run.agent_name === "signal_extraction" && Array.isArray(output.classifications)) {
    return output.classifications.length > 0
      ? `${output.classifications.length} signal(s) classified`
      : "No new signals to classify"
  }
  if (run.agent_name === "explainability") return "Narrative complete"
  return null
}

// "unverified"/"verification_failed"/"evidence_unavailable" are all
// non-genuine email_verification_status outcomes (never ran, ran but the
// tool itself failed, or we couldn't prove it ran) — only an actual Hunter
// status means verify_email genuinely completed. See
// _extract_email_verification's docstring in
// backend/app/agents/outreach_planner.py for the full status contract.
const NON_GENUINE_EMAIL_STATUSES = new Set(["unverified", "verification_failed", "evidence_unavailable"])

// Plain-language "what external checks actually ran" for a single run —
// persona_fit persists a structured {items, evidence_unavailable} shape
// (see backend/app/agents/tool_activity.py), outreach_planner instead
// persists a single verified_email/email_verification_status pair; both
// are read here rather than showing raw MCP/tool_call detail.
// evidence_unavailable distinguishes "we asked and nothing ran" (empty
// chips, evidence_unavailable false) from "we couldn't ask" (empty chips,
// evidence_unavailable true, e.g. a TrueForge outage) — both must render
// differently from "genuinely nothing to show."
function toolActivity(run: AgentRun): { chips: string[]; evidenceUnavailable: boolean } {
  const output = run.output
  if (run.agent_name === "persona_fit" && output.tools_used && typeof output.tools_used === "object") {
    const toolsUsed = output.tools_used as { items: { tool: string; label: string }[]; evidence_unavailable: boolean }
    return {
      chips: (toolsUsed.items ?? []).map((t) => t.label),
      evidenceUnavailable: !!toolsUsed.evidence_unavailable,
    }
  }
  const verificationStatus = output.email_verification_status
  if (run.agent_name === "outreach_planner" && typeof verificationStatus === "string") {
    if (!NON_GENUINE_EMAIL_STATUSES.has(verificationStatus)) {
      return { chips: [toolActivityLabel("verify_email")], evidenceUnavailable: false }
    }
    return { chips: [], evidenceUnavailable: verificationStatus === "evidence_unavailable" }
  }
  return { chips: [], evidenceUnavailable: false }
}

// Five dots/segments, one per agent in the pipeline's fixed order, colored
// by whether that agent actually ran (and how) in the current trace — so
// the whole run's shape is visible before scrolling into any step's detail.
function MiniTimeline({ trace }: { trace: AgentRun[] }) {
  const latestByAgent = new Map<string, AgentRun>()
  for (const run of trace) {
    latestByAgent.set(run.agent_name, run)
  }

  return (
    <div className="flex items-center gap-1.5 rounded-lg border border-border bg-secondary/30 px-4 py-3">
      {AGENT_ORDER.map((agentName, i) => {
        const run = latestByAgent.get(agentName)
        const Icon = AGENT_ICONS[agentName]
        const accentVar = AGENT_ACCENT_VARS[agentName]
        const status = run?.status ?? "pending"
        return (
          <div key={agentName} className="flex flex-1 items-center gap-1.5">
            <div
              className={cn(
                "flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2",
                !run && "border-dashed border-border text-muted-foreground/50",
                status === "failed" && "border-destructive bg-destructive/10 text-destructive"
              )}
              style={
                run && status !== "failed"
                  ? {
                      borderColor: `hsl(var(${accentVar}))`,
                      backgroundColor: `hsl(var(${accentVar}) / 0.12)`,
                      color: `hsl(var(${accentVar}))`,
                    }
                  : undefined
              }
              title={AGENT_LABELS[agentName]}
            >
              <Icon className="h-4 w-4" />
            </div>
            {i < AGENT_ORDER.length - 1 && (
              <div className={cn("h-px flex-1", run ? "bg-border" : "bg-border/40")} />
            )}
          </div>
        )
      })}
    </div>
  )
}

export function AgentTraceTab({
  trace,
  traceLoading,
  traceError,
  onRetry,
  leadId,
}: {
  trace: AgentRun[] | undefined
  traceLoading: boolean
  traceError: unknown
  onRetry: () => void
  leadId: string
}) {
  if (traceLoading) {
    return (
      <div className="flex flex-col gap-3" aria-busy="true" aria-live="polite">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
      </div>
    )
  }

  if (traceError) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-muted-foreground">
          Couldn't load the agent trace.{" "}
          {traceError instanceof Error ? traceError.message : "Something went wrong contacting the server."}
          <Button variant="outline" size="sm" className="mt-3 block mx-auto" onClick={onRetry}>
            Try again
          </Button>
        </CardContent>
      </Card>
    )
  }

  if (!trace || trace.length === 0) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-muted-foreground">
          No agent runs yet for this lead.
        </CardContent>
      </Card>
    )
  }

  // Most recent run per step first, matching how a marketer reads a trace:
  // "what just happened," not chronological from the very first ever run.
  const ordered = trace.slice().reverse()

  return (
    <div className="flex flex-col gap-4">
      <MiniTimeline trace={trace} />
      <Card>
        <CardContent className="py-2">
          <Accordion type="multiple" defaultValue={[ordered[0]?.id]}>
            {ordered.map((run) => {
              const Icon = AGENT_ICONS[run.agent_name]
              const accentVar = AGENT_ACCENT_VARS[run.agent_name] ?? "--border"
              const outcome = oneLineOutcome(run)
              const duration = formatDuration(run.started_at, run.completed_at)
              const { chips: toolChips, evidenceUnavailable } = toolActivity(run)
              return (
                <AccordionItem key={run.id} value={run.id}>
                  <AccordionTrigger className="px-2">
                    <div className="flex flex-1 items-center gap-3 min-w-0">
                      <span
                        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full"
                        style={{
                          backgroundColor: `hsl(var(${accentVar}) / 0.12)`,
                          color: `hsl(var(${accentVar}))`,
                        }}
                      >
                        {Icon && <Icon className="h-4 w-4" />}
                      </span>
                      <div className="min-w-0 text-left">
                        <p className="text-sm font-semibold truncate">
                          {AGENT_LABELS[run.agent_name] ?? run.agent_name}
                        </p>
                        {outcome && (
                          <p className="text-xs text-muted-foreground truncate capitalize">{outcome}</p>
                        )}
                      </div>
                      <div className="ml-auto flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                        {duration && (
                          <span className="hidden sm:flex items-center gap-1 tabular-nums">
                            <Clock className="h-3 w-3" /> {duration}
                          </span>
                        )}
                        <Badge variant={run.status === "completed" ? "secondary" : "destructive"}>
                          {run.status}
                        </Badge>
                      </div>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent className="px-2 pl-[3.25rem]">
                    <p className="text-xs text-muted-foreground mb-2">{run.input_summary}</p>
                    <p className="text-sm leading-relaxed">{run.reasoning}</p>
                    <p className="mt-2 text-[11px] text-muted-foreground/70">{formatDate(run.started_at)}</p>
                    {toolChips.length > 0 && (
                      <div className="flex items-center gap-1.5 flex-wrap mt-2">
                        <Search className="h-3 w-3 text-muted-foreground" />
                        <span className="text-xs text-muted-foreground">External checks used:</span>
                        {toolChips.map((label) => (
                          <Badge key={label} variant="outline" className="text-[10px]">
                            {label}
                          </Badge>
                        ))}
                      </div>
                    )}
                    {evidenceUnavailable && (
                      <p className="mt-2 text-xs text-muted-foreground">
                        Couldn't confirm which external checks ran for this step.
                      </p>
                    )}
                    {run.can_ask_followup ? (
                      <AgentRunFollowupPanel leadId={leadId} agentRunId={run.id} />
                    ) : (
                      <p className="mt-3 border-t border-border pt-3 text-xs text-muted-foreground">
                        Follow-up questions aren't available for this run — it took the direct fallback
                        path with no continuable session.
                      </p>
                    )}
                  </AccordionContent>
                </AccordionItem>
              )
            })}
          </Accordion>
        </CardContent>
      </Card>
    </div>
  )
}
