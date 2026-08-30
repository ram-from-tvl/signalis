import { useEffect, useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { campaignsApi, leadsApi, pipelineApi } from "@/api/endpoints"
import type { Stage } from "@/types/api"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { StageBadge } from "@/components/leads/StageBadge"
import { ConfidenceMeter } from "@/components/leads/ConfidenceMeter"
import { LeadAvatar } from "@/components/leads/LeadAvatar"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Input } from "@/components/ui/input"
import { useToast } from "@/components/ui/toast-context"
import { AGENT_ORDER, AGENT_LABELS } from "@/lib/agents"
import { parseUtcTimestamp } from "@/lib/utils"
import { Sparkles, ArrowUpDown, Users, Loader2, Search } from "lucide-react"
import { motion } from "motion/react"

type SortKey = "name" | "confidence" | "created_at"

// Bulk pipeline runs span N leads at once, so there's no single lead to poll
// a real per-agent trace for — this rotates through the pipeline's real 5
// agent names as an honest "here's the kind of work happening" cue rather
// than a generic spinner, without inventing per-lead progress tracking.
function useRotatingAgentLabel(active: boolean, intervalMs = 1800) {
  const [index, setIndex] = useState(0)
  useEffect(() => {
    if (!active) {
      setIndex(0)
      return
    }
    const id = setInterval(() => setIndex((i) => (i + 1) % AGENT_ORDER.length), intervalMs)
    return () => clearInterval(id)
  }, [active, intervalMs])
  return AGENT_LABELS[AGENT_ORDER[index]]
}

export function LeadPipelinePage() {
  const queryClient = useQueryClient()
  const { push } = useToast()
  const { data: leads, isLoading } = useQuery({ queryKey: ["leads"], queryFn: leadsApi.list })
  const { data: campaigns, isError: campaignsErrored } = useQuery({
    queryKey: ["campaigns"],
    queryFn: campaignsApi.list,
  })

  const [stageFilter, setStageFilter] = useState<Stage | "all">("all")
  const [campaignFilter, setCampaignFilter] = useState<string>("all")
  const [sortKey, setSortKey] = useState<SortKey>("created_at")
  const [search, setSearch] = useState("")

  const runPipeline = useMutation({
    mutationFn: (leadIds?: string[]) => pipelineApi.run(leadIds),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ["leads"] })
      queryClient.invalidateQueries({ queryKey: ["dashboard-stats"] })
      if (response.errors.length > 0) {
        push({
          title: `${response.results.length} lead(s) classified, ${response.errors.length} failed`,
          description: response.errors[0]?.error,
          variant: response.results.length > 0 ? "info" : "error",
        })
      } else {
        push({ title: `${response.results.length} lead(s) classified`, variant: "success" })
      }
    },
    onError: (err: Error) => push({ title: "Pipeline run failed", description: err.message, variant: "error" }),
  })

  const rotatingAgentLabel = useRotatingAgentLabel(runPipeline.isPending)

  const filteredSorted = useMemo(() => {
    if (!leads) return []
    let items = [...leads]
    if (stageFilter !== "all") {
      items = items.filter((item) => item.latest_classification?.stage === stageFilter)
    }
    if (campaignFilter !== "all") {
      items = items.filter((item) => item.lead.campaign_id === campaignFilter)
    }
    const query = search.trim().toLowerCase()
    if (query) {
      items = items.filter(
        (item) =>
          item.lead.name.toLowerCase().includes(query) ||
          item.lead.company.toLowerCase().includes(query) ||
          (item.lead.title ?? "").toLowerCase().includes(query)
      )
    }
    items.sort((a, b) => {
      if (sortKey === "name") return a.lead.name.localeCompare(b.lead.name)
      if (sortKey === "confidence")
        return (b.latest_classification?.confidence ?? -1) - (a.latest_classification?.confidence ?? -1)
      return parseUtcTimestamp(b.lead.created_at).getTime() - parseUtcTimestamp(a.lead.created_at).getTime()
    })
    return items
  }, [leads, stageFilter, campaignFilter, sortKey, search])

  return (
    <div className="flex flex-col gap-6 pb-12">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <h1 className="font-heading text-2xl font-bold">Lead Pipeline</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {leads?.length ?? 0} lead(s) loaded. Trigger the agent pipeline to classify buying stage and generate outreach plans.
          </p>
        </div>
        <Button
          variant="accent"
          onClick={() => runPipeline.mutate(undefined)}
          disabled={runPipeline.isPending || !leads?.length}
        >
          {runPipeline.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
          ) : (
            <Sparkles className="h-4 w-4" />
          )}
          {runPipeline.isPending ? "Running agents..." : "Run Pipeline for All Leads"}
        </Button>
      </div>

      {runPipeline.isPending && (
        <div
          className="flex items-center gap-3 rounded-lg border border-border bg-secondary/40 px-4 py-3"
          aria-busy="true"
          aria-live="polite"
        >
          <div className="h-1.5 flex-1 rounded-full bg-muted overflow-hidden">
            <div className="h-full w-1/3 rounded-full bg-accent animate-[indeterminate_1.4s_ease-in-out_infinite] motion-reduce:animate-none motion-reduce:w-full" />
          </div>
          <p className="text-xs text-muted-foreground shrink-0 tabular-nums min-w-[11rem] text-right">
            Running {rotatingAgentLabel}&hellip;
          </p>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search by name, company, title..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8"
            aria-label="Search leads"
          />
        </div>
        <Select value={stageFilter} onValueChange={(v) => setStageFilter(v as Stage | "all")}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="Filter by stage" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All stages</SelectItem>
            <SelectItem value="early">Early</SelectItem>
            <SelectItem value="mid">Mid</SelectItem>
            <SelectItem value="late">Late</SelectItem>
          </SelectContent>
        </Select>
        {campaignsErrored && (
          <span className="text-xs text-destructive" role="status">
            Campaign filter unavailable — couldn't load campaigns
          </span>
        )}
        {(campaigns?.length ?? 0) > 1 && (
          <Select value={campaignFilter} onValueChange={setCampaignFilter}>
            <SelectTrigger className="w-48">
              <SelectValue placeholder="Filter by campaign" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All campaigns</SelectItem>
              {campaigns?.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Select value={sortKey} onValueChange={(v) => setSortKey(v as SortKey)}>
          <SelectTrigger className="w-48">
            <ArrowUpDown className="h-3.5 w-3.5 mr-1 opacity-60" />
            <SelectValue placeholder="Sort by" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="created_at">Newest first</SelectItem>
            <SelectItem value="confidence">Confidence (high to low)</SelectItem>
            <SelectItem value="name">Name (A-Z)</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {isLoading && (
        <div className="grid gap-3" aria-busy="true" aria-live="polite">
          {Array.from({ length: 5 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="py-4 flex items-center gap-6">
                <div className="flex-1 min-w-0">
                  <Skeleton className="h-4 w-40 mb-2" />
                  <Skeleton className="h-3 w-56" />
                </div>
                <Skeleton className="h-6 w-16" />
                <Skeleton className="h-1.5 w-20" />
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {!isLoading && filteredSorted.length === 0 && (leads?.length ?? 0) > 0 && (
        <Card>
          <CardContent className="py-12 flex flex-col items-center text-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent/10 text-accent">
              <Search className="h-6 w-6" />
            </div>
            <p className="text-sm text-muted-foreground max-w-sm">
              No leads match "{search}"{stageFilter !== "all" ? ` in the ${stageFilter} stage` : ""}.
            </p>
            <Button
              variant="outline"
              className="mt-1"
              onClick={() => {
                setSearch("")
                setStageFilter("all")
              }}
            >
              Clear filters
            </Button>
          </CardContent>
        </Card>
      )}

      {!isLoading && (leads?.length ?? 0) === 0 && (
        <Card>
          <CardContent className="py-12 flex flex-col items-center text-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent/10 text-accent">
              <Users className="h-6 w-6" />
            </div>
            <p className="text-sm text-muted-foreground max-w-sm">
              No leads loaded yet. Head to Data Sources to upload or load the sample dataset.
            </p>
            <Button asChild variant="outline" className="mt-1">
              <Link to="/data">Go to Data Sources</Link>
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-3">
        {filteredSorted.map((item, i) => (
          <motion.div
            key={item.lead.id}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, delay: Math.min(i * 0.02, 0.3) }}
          >
            <Link to={`/leads/${item.lead.id}`}>
              <Card className="hover:border-accent/50 hover:shadow-raised transition-[border-color,box-shadow] duration-200">
                <CardContent className="py-4 flex flex-col md:flex-row md:items-center gap-3 md:gap-4">
                  <LeadAvatar name={item.lead.name} className="hidden sm:flex" />
                  <div className="flex-1 min-w-0">
                    <p className="font-heading font-semibold truncate">{item.lead.name}</p>
                    <p className="text-sm text-muted-foreground truncate">
                      {item.lead.title || "Title unknown"} at {item.lead.company}
                    </p>
                    {item.campaign_name && (
                      <Badge variant="outline" className="mt-1 text-[10px]">
                        {item.campaign_name}
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-4 shrink-0">
                    {item.latest_classification ? (
                      <>
                        <StageBadge stage={item.latest_classification.stage} />
                        <ConfidenceMeter confidence={item.latest_classification.confidence} />
                        {item.latest_classification.approval_status === "pending_approval" && (
                          <Badge variant="warning">Needs Approval</Badge>
                        )}
                      </>
                    ) : (
                      <Badge variant="outline">Not classified yet</Badge>
                    )}
                    {item.latest_plan_status === "pending_approval" && (
                      <Badge variant="secondary">Plan pending</Badge>
                    )}
                    {item.latest_plan_status === "approved" && (
                      <Badge variant="success">Plan approved</Badge>
                    )}
                  </div>
                </CardContent>
              </Card>
            </Link>
          </motion.div>
        ))}
      </div>
    </div>
  )
}
