import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { dashboardApi, rankingApi } from "@/api/endpoints"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { StageBadge } from "@/components/leads/StageBadge"
import { ConfidenceMeter } from "@/components/leads/ConfidenceMeter"
import { useToast } from "@/components/ui/toast-context"
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
import { Users, FileCheck, Gauge, Timer, ListOrdered } from "lucide-react"

const STAGE_COLORS: Record<string, string> = {
  early: "hsl(220 14% 55%)",
  mid: "hsl(38 92% 50%)",
  late: "hsl(152 55% 34%)",
}

function StatTile({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: React.ElementType
  label: string
  value: string
  hint?: string
}) {
  return (
    <Card>
      <CardContent className="py-5 flex items-start gap-4">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent/10 text-accent shrink-0">
          <Icon className="h-5 w-5" />
        </div>
        <div>
          <p className="text-xs text-muted-foreground font-medium">{label}</p>
          <p className="font-heading text-2xl font-bold mt-0.5">{value}</p>
          {hint && <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>}
        </div>
      </CardContent>
    </Card>
  )
}

export function DashboardPage() {
  const queryClient = useQueryClient()
  const { push } = useToast()

  const { data: stats, isLoading } = useQuery({
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

  if (isLoading || !stats) {
    return <p className="text-sm text-muted-foreground">Loading dashboard...</p>
  }

  const stageData = Object.entries(stats.stage_distribution).map(([stage, count]) => ({
    stage: stage.charAt(0).toUpperCase() + stage.slice(1),
    key: stage,
    count,
  }))

  const manualSeconds = stats.manual_minutes_per_lead_estimate * 60
  const agentSeconds = stats.agent_seconds_per_lead_actual || 1
  const speedupMultiple = manualSeconds / agentSeconds

  const timeSavedData = [
    { label: "Manual research + drafting", seconds: manualSeconds, key: "manual" },
    { label: "Agent-generated (measured)", seconds: agentSeconds, key: "agent" },
  ]

  return (
    <div className="flex flex-col gap-6 pb-12">
      <div>
        <h1 className="font-heading text-2xl font-bold">Dashboard</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Aggregate view across the whole lead pipeline.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile icon={Users} label="Total Leads" value={String(stats.total_leads)} />
        <StatTile
          icon={FileCheck}
          label="Plans Generated"
          value={String(stats.plans_generated)}
          hint={`${stats.plans_pending_approval} pending approval`}
        />
        <StatTile
          icon={Gauge}
          label="Avg. Confidence"
          value={`${Math.round(stats.average_confidence * 100)}%`}
        />
        <StatTile
          icon={Timer}
          label="Avg. Agent Latency"
          value={`${stats.average_agent_latency_seconds.toFixed(1)}s`}
          hint="per agent call, measured"
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Pipeline Stage Distribution</CardTitle>
            <CardDescription>Current (non-superseded) classification per lead.</CardDescription>
          </CardHeader>
          <CardContent className="h-72">
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

        <Card>
          <CardHeader>
            <CardTitle>Time Saved: Manual vs. Agent-Generated</CardTitle>
            <CardDescription>
              Manual estimate is an illustrative baseline (see docs); the agent bar is measured from real
              agent_runs timestamps.
            </CardDescription>
          </CardHeader>
          <CardContent className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={timeSavedData}
                layout="vertical"
                margin={{ top: 8, right: 24, left: 8, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 12, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                <YAxis
                  type="category"
                  dataKey="label"
                  width={170}
                  tick={{ fontSize: 12, fill: "hsl(var(--muted-foreground))" }}
                  axisLine={false}
                  tickLine={false}
                />
                <RechartsTooltip
                  formatter={(value) => `${Number(value).toFixed(1)}s`}
                  contentStyle={{
                    background: "hsl(var(--popover))",
                    border: "1px solid hsl(var(--border))",
                    borderRadius: 8,
                    fontSize: 13,
                  }}
                />
                <Bar dataKey="seconds" radius={[0, 6, 6, 0]} maxBarSize={40}>
                  <Cell fill="hsl(var(--muted-foreground))" />
                  <Cell fill="hsl(var(--accent))" />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
            {stats.agent_seconds_per_lead_actual > 0 && (
              <p className="text-xs text-muted-foreground text-center -mt-2">
                Roughly <strong className="text-foreground">{speedupMultiple.toFixed(0)}x</strong> faster than the manual baseline.
              </p>
            )}
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
            <ListOrdered className="h-4 w-4" />
            {runRanking.isPending ? "Ranking..." : "Rank Pipeline"}
          </Button>
        </CardHeader>
        <CardContent>
          {rankingLoading && <p className="text-sm text-muted-foreground">Loading ranking...</p>}
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
              <p className="text-sm text-muted-foreground leading-relaxed">{ranking.summary}</p>
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
                        <ConfidenceMeter confidence={entry.confidence} showLabel={false} />
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
