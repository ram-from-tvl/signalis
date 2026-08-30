import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { dashboardApi, rankingApi } from "@/api/endpoints"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { StageBadge } from "@/components/leads/StageBadge"
import { ConfidenceMeter } from "@/components/leads/ConfidenceMeter"
import { useToast } from "@/components/ui/toast-context"
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card"
import { useCountUp } from "@/lib/useCountUp"
import type { SubagentDelegation } from "@/types/api"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts"
import { Users, FileCheck, Gauge, Timer, ListOrdered, Loader2 } from "lucide-react"

const STAGE_COLORS: Record<string, string> = {
  early: "hsl(var(--stage-early))",
  mid: "hsl(var(--stage-mid))",
  late: "hsl(var(--stage-late))",
}

function StatTile({
  icon: Icon,
  label,
  value,
  format,
  hint,
}: {
  icon: React.ElementType
  label: string
  value: number
  format?: (n: number) => string
  hint?: string
}) {
  const animated = useCountUp(value)
  const displayValue = format ? format(animated) : String(Math.round(animated))

  return (
    <Card className="transition-[transform,box-shadow] duration-200 hover:-translate-y-0.5 hover:shadow-raised">
      <CardContent className="py-5 flex items-start gap-4">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br from-accent/15 to-accent/5 text-accent shrink-0">
          <Icon className="h-5 w-5" />
        </div>
        <div>
          <p className="text-xs text-muted-foreground font-medium">{label}</p>
          <p className="font-heading text-2xl font-bold mt-0.5 tabular-nums">{displayValue}</p>
          {hint && <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>}
        </div>
      </CardContent>
    </Card>
  )
}

// Plain-language status a marketer can actually parse, with the internal
// mechanism (subagent delegation, TrueForge) moved to a hover tooltip
// rather than sitting in the primary copy.
function DelegationBadge({ delegation }: { delegation: SubagentDelegation | null | undefined }) {
  if (!delegation) return null

  const config: Record<
    SubagentDelegation["status"],
    { label: string; detail: string; variant: "success" | "warning" | "secondary" | "outline" }
  > = {
    delegated: {
      label: "Ranked lead-by-lead in parallel",
      detail: `Each of the ${delegation.expected_count} leads was independently assessed by its own sub-agent (${delegation.subagent_count}/${delegation.expected_count} completed), then combined into one priority order.`,
      variant: "success",
    },
    partial: {
      label: "Partially ranked in parallel",
      detail: `${delegation.subagent_count ?? 0} of ${delegation.expected_count} leads were independently assessed by their own sub-agent; the rest were folded in directly.`,
      variant: "warning",
    },
    evidence_unavailable: {
      label: "Ranking computed",
      detail: "This ranking completed, but we couldn't confirm afterward how the work was split up.",
      variant: "secondary",
    },
    not_delegated: {
      label: "Ranked in one pass",
      detail: "This ranking was computed directly in a single pass rather than split across parallel sub-agents this run.",
      variant: "outline",
    },
  }
  const { label, detail, variant } = config[delegation.status]
  return (
    <HoverCard openDelay={150}>
      <HoverCardTrigger asChild>
        <Badge variant={variant} className="cursor-default">
          {label}
        </Badge>
      </HoverCardTrigger>
      <HoverCardContent>
        <p className="text-xs text-muted-foreground leading-relaxed">{detail}</p>
      </HoverCardContent>
    </HoverCard>
  )
}

export function DashboardPage() {
  const queryClient = useQueryClient()
  const { push } = useToast()

  const {
    data: stats,
    isLoading,
    error: statsError,
  } = useQuery({
    queryKey: ["dashboard-stats"],
    queryFn: dashboardApi.stats,
  })

  const { data: ranking, isLoading: rankingLoading } = useQuery({
    queryKey: ["pipeline-ranking"],
    queryFn: rankingApi.latest,
  })

  const runRanking = useMutation({
    mutationFn: rankingApi.run,
    onSuccess: (data) => {
      queryClient.setQueryData(["pipeline-ranking"], data)
      push({ title: `Ranked ${data.ranked_leads.length} lead(s)`, variant: "success" })
    },
    onError: (err: Error) => push({ title: "Ranking failed", description: err.message, variant: "error" }),
  })

  if (isLoading) {
    return (
      <div className="flex flex-col gap-6 pb-12" aria-busy="true" aria-live="polite">
        <div>
          <Skeleton className="h-7 w-40" />
          <Skeleton className="h-4 w-72 mt-2" />
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="py-5">
                <Skeleton className="h-10 w-10 rounded-lg mb-3" />
                <Skeleton className="h-3 w-20 mb-2" />
                <Skeleton className="h-6 w-14" />
              </CardContent>
            </Card>
          ))}
        </div>
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardContent className="pt-6">
              <Skeleton className="h-72 w-full" />
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <Skeleton className="h-72 w-full" />
            </CardContent>
          </Card>
        </div>
      </div>
    )
  }

  if (!stats) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
        <p className="font-heading text-xl">Couldn't load the dashboard</p>
        <p className="text-sm text-muted-foreground max-w-sm">
          {statsError instanceof Error ? statsError.message : "Something went wrong contacting the server."}
        </p>
        <Button
          variant="outline"
          className="mt-2"
          onClick={() => queryClient.invalidateQueries({ queryKey: ["dashboard-stats"] })}
        >
          Try again
        </Button>
      </div>
    )
  }

  const stageData = Object.entries(stats.stage_distribution).map(([stage, count]) => ({
    stage: stage.charAt(0).toUpperCase() + stage.slice(1),
    key: stage,
    count,
  }))

  const manualSeconds = stats.manual_minutes_per_lead_estimate * 60
  const agentSeconds = stats.agent_seconds_per_lead_actual || 1
  const speedupMultiple = manualSeconds / agentSeconds

  return (
    <div className="flex flex-col gap-6 pb-12">
      <div>
        <h1 className="font-heading text-2xl font-bold">Dashboard</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Aggregate view across the whole lead pipeline.
        </p>
      </div>

      {/* Bento grid: the hero cell (best number, largest) leads, stat tiles
          are medium cells, stage distribution is a wide supporting cell —
          size signals importance instead of every card competing equally. */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-1 lg:row-span-2 bg-gradient-to-br from-accent/10 via-accent/5 to-transparent border-accent/20">
          <CardContent className="py-6 flex h-full flex-col justify-center gap-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-accent">Speed, measured</p>
            <p className="font-heading text-6xl font-bold tabular-nums leading-none mt-2">
              {stats.agent_seconds_per_lead_actual > 0 ? `${speedupMultiple.toFixed(0)}x` : "—"}
            </p>
            <p className="text-sm font-semibold mt-2">faster than manual research</p>
            <p className="text-xs text-muted-foreground mt-3 leading-relaxed">
              Manual: ~{stats.manual_minutes_per_lead_estimate.toFixed(0)} min/lead (illustrative baseline)
              <br />
              Agent: ~{(agentSeconds / 60).toFixed(1)} min/lead — measured from real run timestamps, not
              estimated
            </p>
          </CardContent>
        </Card>

        <div className="grid gap-4 sm:grid-cols-2 lg:col-span-2">
          <StatTile icon={Users} label="Total Leads" value={stats.total_leads} />
          <StatTile
            icon={FileCheck}
            label="Plans Generated"
            value={stats.plans_generated}
            hint={`${stats.plans_pending_approval} pending approval`}
          />
          <StatTile
            icon={Gauge}
            label="Avg. Confidence"
            value={stats.average_confidence * 100}
            format={(n) => `${Math.round(n)}%`}
          />
          <StatTile
            icon={Timer}
            label="Avg. Agent Latency"
            value={stats.average_agent_latency_seconds}
            format={(n) => `${n.toFixed(1)}s`}
            hint="per agent call, measured"
          />
        </div>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Pipeline Stage Distribution</CardTitle>
            <CardDescription>Current (non-superseded) classification per lead.</CardDescription>
          </CardHeader>
          <CardContent className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={stageData} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                <XAxis dataKey="stage" tick={{ fontSize: 12, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 12, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} allowDecimals={false} />
                <RechartsTooltip
                  contentStyle={{
                    background: "hsl(var(--popover))",
                    border: "1px solid hsl(var(--border))",
                    borderRadius: 8,
                    fontSize: 13,
                  }}
                />
                <Bar dataKey="count" radius={[6, 6, 0, 0]} maxBarSize={64}>
                  {stageData.map((entry) => (
                    <Cell key={entry.key} fill={STAGE_COLORS[entry.key]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex-row items-center justify-between gap-4 flex-wrap">
          <div>
            <CardTitle>Priority Queue</CardTitle>
            <CardDescription>
              Who to contact first across the whole pipeline, ranked by the Prioritization Agent.
            </CardDescription>
          </div>
          <Button
            variant="accent"
            size="sm"
            onClick={() => runRanking.mutate()}
            disabled={runRanking.isPending || !stats.total_leads}
          >
            {runRanking.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
            ) : (
              <ListOrdered className="h-4 w-4" />
            )}
            {runRanking.isPending ? "Ranking..." : "Rank Pipeline"}
          </Button>
        </CardHeader>
        <CardContent>
          {rankingLoading && (
            <div className="flex flex-col gap-2" aria-busy="true" aria-live="polite">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          )}
          {!rankingLoading && !ranking && (
            <p className="text-sm text-muted-foreground">
              No ranking yet. Classify some leads, then click "Rank Pipeline" to see who to contact first.
            </p>
          )}
          {ranking && ranking.ranked_leads.length === 0 && (
            <p className="text-sm text-muted-foreground">{ranking.summary}</p>
          )}
          {ranking && ranking.ranked_leads.length > 0 && (
            <div className="flex flex-col gap-4">
              <div className="flex items-center justify-between gap-4 flex-wrap">
                <p className="text-sm text-muted-foreground leading-relaxed">{ranking.summary}</p>
                <DelegationBadge delegation={ranking.subagent_delegation} />
              </div>
              <ol className="flex flex-col gap-2">
                {ranking.ranked_leads.map((entry) => (
                  <li key={entry.lead_id}>
                    <Link
                      to={`/leads/${entry.lead_id}`}
                      className="flex items-center gap-4 rounded-lg border border-border p-3 hover:border-accent/50 hover:shadow-raised transition-[border-color,box-shadow] duration-200"
                    >
                      <span className="font-heading text-lg font-bold text-accent w-8 text-center shrink-0">
                        {entry.rank}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="font-semibold truncate">{entry.name}</p>
                        <p className="text-xs text-muted-foreground truncate">
                          {entry.title || "Title unknown"} at {entry.company}
                        </p>
                        <p className="text-xs text-muted-foreground mt-1">{entry.reasoning}</p>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        <StageBadge stage={entry.stage} />
                        <ConfidenceMeter confidence={entry.confidence} />
                      </div>
                    </Link>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>How this is measured</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground leading-relaxed">
          The manual baseline (~{stats.manual_minutes_per_lead_estimate.toFixed(0)} minutes per lead) is an
          illustrative estimate of the time an SDR spends pulling CRM and website activity, judging buying
          stage, and drafting an outreach sequence by hand. The agent-side number is not estimated: it is
          the average wall-clock duration of completed agent_runs rows recorded in the database, multiplied
          by the five sequential agents in the pipeline. See docs/TIME_SAVINGS.md for the full breakdown.
        </CardContent>
      </Card>
    </div>
  )
}
