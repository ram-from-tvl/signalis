import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { campaignsApi, uploadsApi } from "@/api/endpoints"
import { API_BASE_URL } from "@/api/client"
import type { IngestionReport } from "@/types/api"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useToast } from "@/components/ui/toast-context"
import { cn } from "@/lib/utils"
import { useRotatingLabel } from "@/lib/useRotatingLabel"
import {
  FileSpreadsheet,
  FileJson,
  CheckCircle2,
  AlertTriangle,
  UploadCloud,
  Sparkles,
  ThumbsUp,
  Loader2,
} from "lucide-react"

const CSV_UPLOAD_STEPS = ["Reading rows...", "Matching existing leads...", "Creating signals..."]
const JSON_UPLOAD_STEPS = ["Reading events...", "Matching leads by email...", "Creating signals..."]

function UploadProgress({ active, steps }: { active: boolean; steps: string[] }) {
  const label = useRotatingLabel(steps, active)
  if (!active) return null
  return (
    <div
      className="flex items-center gap-2 rounded-lg border border-border bg-secondary/40 px-3 py-2"
      aria-busy="true"
      aria-live="polite"
    >
      <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin motion-reduce:animate-none text-accent" />
      <span className="text-xs text-muted-foreground tabular-nums">{label}</span>
    </div>
  )
}

function ReportSummary({ report }: { report: IngestionReport }) {
  return (
    <div className="rounded-lg border border-border bg-secondary/40 p-4 mt-3 flex flex-col gap-2 animate-fade-in">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <CheckCircle2 className="h-4 w-4 text-success" />
        Ingestion complete
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm text-muted-foreground">
        <span>Rows parsed: <strong className="text-foreground">{report.rows_parsed}</strong></span>
        <span>Rows skipped: <strong className="text-foreground">{report.rows_skipped}</strong></span>
        <span>Leads created: <strong className="text-foreground">{report.leads_created}</strong></span>
        <span>Leads updated: <strong className="text-foreground">{report.leads_updated}</strong></span>
        <span className="col-span-2">Signals created: <strong className="text-foreground">{report.signals_created}</strong></span>
      </div>
      {report.skipped_reasons.length > 0 && (
        <div className="mt-1 flex flex-col gap-1">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-warning">
            <AlertTriangle className="h-3.5 w-3.5" />
            Skipped row details
          </div>
          <ul className="text-xs text-muted-foreground list-disc pl-4 max-h-32 overflow-y-auto scrollbar-thin">
            {report.skipped_reasons.map((reason, i) => (
              <li key={i}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function DropZone({
  icon: Icon,
  accept,
  label,
  onFile,
  disabled,
  inputRef,
}: {
  icon: React.ElementType
  accept: string
  label: string
  onFile: (file: File) => void
  disabled: boolean
  inputRef: React.RefObject<HTMLInputElement>
}) {
  const [isDragOver, setIsDragOver] = useState(false)
  const [dropError, setDropError] = useState<string | null>(null)

  // A new upload starting (browse, sample-data, or a subsequent valid drop)
  // makes any earlier rejected-drop message stale — clear it so the zone
  // never keeps claiming a previous file was invalid once a different
  // upload is actually in flight.
  useEffect(() => {
    if (disabled) setDropError(null)
  }, [disabled])

  // Accept is a single extension like ".csv" today; matching by suffix
  // keeps this correct if it's ever widened to a comma-separated list.
  const acceptedExtensions = accept.split(",").map((ext) => ext.trim().toLowerCase())
  const isAcceptedFile = (file: File) => {
    const name = file.name.toLowerCase()
    return acceptedExtensions.some((ext) => name.endsWith(ext))
  }

  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-label={label}
      aria-disabled={disabled}
      onClick={() => {
        if (disabled) return
        inputRef.current?.click()
      }}
      onKeyDown={(e) => {
        if (disabled) return
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          inputRef.current?.click()
        }
      }}
      onDragOver={(e) => {
        e.preventDefault()
        if (!disabled) setIsDragOver(true)
      }}
      onDragLeave={() => setIsDragOver(false)}
      onDrop={(e) => {
        // Always preventDefault, even while disabled — otherwise a file
        // dropped during a pending upload falls through to the browser's
        // default file-open/navigation behavior instead of being ignored.
        e.preventDefault()
        setIsDragOver(false)
        if (disabled) return
        const file = e.dataTransfer.files?.[0]
        if (!file) return
        if (!isAcceptedFile(file)) {
          setDropError(`"${file.name}" isn't a ${accept} file.`)
          return
        }
        setDropError(null)
        onFile(file)
      }}
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border-dashed p-6 text-center transition-[border-color,border-width,background-color] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
        isDragOver
          ? "border-[3px] border-accent bg-accent/10"
          : "border-2 border-border hover:border-accent/50 hover:bg-secondary/40"
      )}
    >
      <Icon className={cn("h-6 w-6", isDragOver ? "text-accent" : "text-muted-foreground")} />
      <p className="text-sm font-medium">
        Drag and drop, or <span className="text-accent">browse</span>
      </p>
      <p className="text-xs text-muted-foreground">Accepts {accept}</p>
      {dropError && <p className="text-xs text-destructive">{dropError}</p>}
    </div>
  )
}

export function DataSourcesPage() {
  const { push } = useToast()
  const csvInputRef = useRef<HTMLInputElement>(null)
  const jsonInputRef = useRef<HTMLInputElement>(null)
  const [csvReport, setCsvReport] = useState<IngestionReport | null>(null)
  const [jsonReport, setJsonReport] = useState<IngestionReport | null>(null)
  const [csvIsSample, setCsvIsSample] = useState(false)
  const [jsonIsSample, setJsonIsSample] = useState(false)
  const [targetCampaignId, setTargetCampaignId] = useState<string | undefined>(undefined)

  const { data: campaigns, isError: campaignsErrored } = useQuery({
    queryKey: ["campaigns"],
    queryFn: campaignsApi.list,
  })

  // Default to whichever campaign is marked default once campaigns load,
  // without overriding a choice the marketer already made.
  useEffect(() => {
    if (targetCampaignId || !campaigns?.length) return
    const defaultCampaign = campaigns.find((c) => c.is_default) ?? campaigns[0]
    setTargetCampaignId(defaultCampaign.id)
  }, [campaigns, targetCampaignId])

  const csvMutation = useMutation({
    mutationFn: (file: File) => uploadsApi.crmCsv(file, targetCampaignId),
    onSuccess: (report) => {
      setCsvReport(report)
      push({ title: "CRM CSV ingested", description: `${report.leads_created} lead(s) created`, variant: "success" })
    },
    onError: (err: Error) => push({ title: "CRM upload failed", description: err.message, variant: "error" }),
  })

  const jsonMutation = useMutation({
    mutationFn: uploadsApi.websiteEvents,
    onSuccess: (report) => {
      setJsonReport(report)
      push({ title: "Website events ingested", description: `${report.signals_created} signal(s) created`, variant: "success" })
    },
    onError: (err: Error) => push({ title: "Website event upload failed", description: err.message, variant: "error" }),
  })

  const loadSample = async (kind: "crm" | "events") => {
    const path = kind === "crm" ? "/sample-data/sample_crm_leads.csv" : "/sample-data/sample_website_events.json"
    const response = await fetch(path)
    const blob = await response.blob()
    const file = new File([blob], kind === "crm" ? "sample_crm_leads.csv" : "sample_website_events.json", {
      type: kind === "crm" ? "text/csv" : "application/json",
    })
    if (kind === "crm") {
      setCsvIsSample(true)
      csvMutation.mutate(file)
    } else {
      setJsonIsSample(true)
      jsonMutation.mutate(file)
    }
  }

  return (
    <div className="flex flex-col gap-6 pb-12">
      <div>
        <h1 className="font-heading text-2xl font-bold">Data Sources</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Connect a CRM-style export and website event log. Use the bundled
          sample dataset to see the full system work end to end, or upload
          your own files in the same shape.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <FileSpreadsheet className="h-5 w-5 text-accent" />
              <CardTitle>CRM Export (CSV)</CardTitle>
            </div>
            <CardDescription>
              name, company, title, company_size, industry, geography, email, plus deal/activity columns.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {campaignsErrored && (
              <p className="text-xs text-destructive">
                Couldn't load campaigns — new leads will go to whichever campaign is marked
                default on the server, since targeting can't be chosen right now. Reload the
                page to try again.
              </p>
            )}
            {(campaigns?.length ?? 0) > 0 && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="upload-campaign">Assign new leads to</Label>
                <Select value={targetCampaignId} onValueChange={setTargetCampaignId}>
                  <SelectTrigger id="upload-campaign">
                    <SelectValue placeholder="Choose a campaign" />
                  </SelectTrigger>
                  <SelectContent>
                    {campaigns?.map((c) => (
                      <SelectItem key={c.id} value={c.id}>
                        {c.name}
                        {c.is_default ? " (default)" : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            <DropZone
              icon={UploadCloud}
              accept=".csv"
              label="Upload CRM CSV"
              disabled={csvMutation.isPending}
              inputRef={csvInputRef}
              onFile={(file) => {
                setCsvIsSample(false)
                csvMutation.mutate(file)
              }}
            />
            <div className="flex gap-2">
              <input
                ref={csvInputRef}
                type="file"
                accept=".csv"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) {
                    setCsvIsSample(false)
                    csvMutation.mutate(file)
                  }
                  e.target.value = ""
                }}
              />
              <Button variant="accent" size="sm" onClick={() => loadSample("crm")} disabled={csvMutation.isPending}>
                Use Sample Data
              </Button>
            </div>
            {csvIsSample && <Badge variant="outline">Loaded from bundled sample dataset</Badge>}
            <UploadProgress active={csvMutation.isPending} steps={CSV_UPLOAD_STEPS} />
            {csvReport && <ReportSummary report={csvReport} />}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <FileJson className="h-5 w-5 text-accent" />
              <CardTitle>Website Events (JSON)</CardTitle>
            </div>
            <CardDescription>
              Array of {"{"}lead_email, name, company, page, event_type, timestamp{"}"} objects.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <DropZone
              icon={UploadCloud}
              accept=".json"
              label="Upload website events JSON"
              disabled={jsonMutation.isPending}
              inputRef={jsonInputRef}
              onFile={(file) => {
                setJsonIsSample(false)
                jsonMutation.mutate(file)
              }}
            />
            <div className="flex gap-2">
              <input
                ref={jsonInputRef}
                type="file"
                accept=".json"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) {
                    setJsonIsSample(false)
                    jsonMutation.mutate(file)
                  }
                  e.target.value = ""
                }}
              />
              <Button variant="accent" size="sm" onClick={() => loadSample("events")} disabled={jsonMutation.isPending}>
                Use Sample Data
              </Button>
            </div>
            {jsonIsSample && <Badge variant="outline">Loaded from bundled sample dataset</Badge>}
            <UploadProgress active={jsonMutation.isPending} steps={JSON_UPLOAD_STEPS} />
            {jsonReport && <ReportSummary report={jsonReport} />}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Pipeline at a glance</CardTitle>
          <CardDescription>What happens to your data once it's loaded.</CardDescription>
        </CardHeader>
        <CardContent>
          <ol className="relative flex flex-col gap-6 before:absolute before:left-[15px] before:top-4 before:h-[calc(100%-2rem)] before:w-px before:bg-border">
            {[
              {
                icon: UploadCloud,
                title: "Upload",
                body: "CRM and website data land as leads and signals, deduped by email.",
              },
              {
                icon: Sparkles,
                title: "Classify",
                body: "The agent pipeline scores buying stage, persona fit, and drafts an outreach plan.",
              },
              {
                icon: ThumbsUp,
                title: "Approve",
                body: "A marketer reviews and approves every plan before anything is considered ready to send.",
              },
            ].map((step) => (
              <li key={step.title} className="relative pl-11">
                <span className="absolute left-0 top-0 flex h-8 w-8 items-center justify-center rounded-full bg-accent/10 text-accent">
                  <step.icon className="h-4 w-4" />
                </span>
                <p className="text-sm font-semibold">{step.title}</p>
                <p className="text-sm text-muted-foreground mt-0.5">{step.body}</p>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>What happens next</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 text-sm text-muted-foreground">
          <p>
            Once leads and signals are loaded, head to the Pipeline view to trigger the agent graph
            for one or all leads.
          </p>
          <details className="group text-xs">
            <summary className="cursor-pointer select-none font-medium text-foreground/70 hover:text-foreground">
              Developer info
            </summary>
            <div className="mt-2 flex flex-col gap-1">
              <span>
                API: <code className="bg-secondary px-1 py-0.5 rounded">{API_BASE_URL}</code>
              </span>
              <span>
                Interactive docs: <code className="bg-secondary px-1 py-0.5 rounded">{API_BASE_URL}/docs</code>
              </span>
            </div>
          </details>
        </CardContent>
      </Card>
    </div>
  )
}
