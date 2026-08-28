import { useRef } from "react"
import { useQuery } from "@tanstack/react-query"
import { AnimatePresence, motion } from "motion/react"
import { leadsApi } from "@/api/endpoints"
import { Card, CardContent } from "@/components/ui/card"
import { AGENT_ORDER, AGENT_LABELS, AGENT_ACCENT_VARS, AGENT_RUNNING_INSIGHT } from "@/lib/agents"
import { Check } from "lucide-react"
import { cn, parseUtcTimestamp } from "@/lib/utils"

type StepStatus = "pending" | "running" | "completed" | "failed"
type TraceRow = { agent_name: string; status: string; started_at: string; output: Record<string, unknown> }

// A lead can have prior pipeline runs in its history (a previous
// classification, a paused-then-abandoned run from an earlier session).
// Without this cutoff, a stale "completed" row from an old run would
// outrank the current run's real (pending/running) state and the panel
// would show misleading leftover progress instead of this run's own.
function currentRunRows(trace: TraceRow[] | undefined, runStartedAt: number): TraceRow[] {
  if (!trace) return []
  return trace.filter((r) => parseUtcTimestamp(r.started_at).getTime() >= runStartedAt)
}

function stepStatusFor(agentName: string, rows: TraceRow[]): StepStatus {
  const agentRows = rows.filter((r) => r.agent_name === agentName)
  if (agentRows.length === 0) return "pending"
  const latest = agentRows[agentRows.length - 1]
  if (latest.status === "completed") return "completed"
  if (latest.status === "failed") return "failed"
  return "running"
}

// A short, real detail surfaced the instant a step completes — not a
// generic checkmark. Falls back to nothing (just the checkmark) for agents
// whose output doesn't have an obvious one-line summary.
function completedDetailFor(agentName: string, rows: TraceRow[]): string | null {
  const agentRows = rows.filter((r) => r.agent_name === agentName)
  if (agentRows.length === 0) return null
  const output = agentRows[agentRows.length - 1].output
  if (agentName === "buying_stage_orchestrator" && typeof output.stage === "string") {
    const confidence = typeof output.confidence === "number" ? Math.round(output.confidence * 100) : null
    return confidence !== null ? `${output.stage} stage, ${confidence}% confidence` : `${output.stage} stage`
  }
  if (agentName === "persona_fit" && typeof output.fit === "string") {
    return output.fit.replace(/_/g, " ")
  }
  if (agentName === "outreach_planner" && Array.isArray(output.touchpoints)) {
    return `${output.touchpoints.length} touchpoint(s) drafted`
  }
  if (agentName === "signal_extraction" && Array.isArray(output.classifications)) {
    return output.classifications.length > 0
      ? `${output.classifications.length} signal(s) classified`
      : "No new signals to classify"
  }
  return null
}

export function PipelineProgressPanel({ leadId }: { leadId: string }) {
  // Captured once, on mount — this panel only mounts when a fresh pipeline
  // run starts, so this is a reliable "this run began around here" marker.
  // A 5s safety margin absorbs client/server clock skew and the gap
  // between the mutation firing and the first agent's row actually landing
  // in the database, without being wide enough to pull in a genuinely
  // separate prior run.
  const runStartedAtRef = useRef(Date.now() - 5000)

  const { data: trace } = useQuery({
    queryKey: ["lead-trace", leadId],
    queryFn: () => leadsApi.trace(leadId),
    refetchInterval: 1200,
  })

  const rows = currentRunRows(trace, runStartedAtRef.current)

  return (
    <Card aria-busy="true" aria-live="polite">
      <CardContent className="py-5">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-4">
          Running the agent pipeline
        </p>
        <ol className="flex flex-col gap-1">
          <AnimatePresence initial={false}>
            {AGENT_ORDER.map((agentName, i) => {
              const status = stepStatusFor(agentName, rows)
              const accentVar = AGENT_ACCENT_VARS[agentName]
              const detail = status === "completed" ? completedDetailFor(agentName, rows) : null

              return (
                <motion.li
                  key={agentName}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.25, delay: i * 0.03 }}
                  className="flex items-start gap-3 py-2"
                >
                  <span
                    className={cn(
                      "relative flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 mt-0.5",
                      status === "pending" && "border-border",
                      status === "running" && "border-transparent",
                      status === "completed" && "border-transparent",
                      status === "failed" && "border-destructive bg-destructive/10"
                    )}
                    style={
                      status === "completed" || status === "running"
                        ? { backgroundColor: `hsl(var(${accentVar}) / ${status === "completed" ? 1 : 0.15})` }
                        : undefined
                    }
                  >
                    {status === "completed" && <Check className="h-3.5 w-3.5 text-white" strokeWidth={3} />}
                    {status === "running" && (
                      <span
                        className="absolute inset-0 rounded-full border-2 animate-spin motion-reduce:animate-none"
                        style={{
                          borderColor: `hsl(var(${accentVar}) / 0.25)`,
                          borderTopColor: `hsl(var(${accentVar}))`,
                        }}
                      />
                    )}
                    {status === "failed" && <span className="h-1.5 w-1.5 rounded-full bg-destructive" />}
                  </span>
                  <div className="min-w-0">
                    <p
                      className={cn(
                        "text-sm font-semibold transition-colors",
                        status === "pending" ? "text-muted-foreground" : "text-foreground"
                      )}
                    >
                      {AGENT_LABELS[agentName]}
                    </p>
                    {status === "running" && (
                      <p className="text-xs text-muted-foreground mt-0.5">{AGENT_RUNNING_INSIGHT[agentName]}</p>
                    )}
                    {status === "completed" && detail && (
                      <p className="text-xs text-muted-foreground mt-0.5 capitalize">{detail}</p>
                    )}
                    {status === "failed" && (
                      <p className="text-xs text-destructive mt-0.5">This step failed — see the error once the run finishes.</p>
                    )}
                  </div>
                </motion.li>
              )
            })}
          </AnimatePresence>
        </ol>
      </CardContent>
    </Card>
  )
}
