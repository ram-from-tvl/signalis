import { useRef, useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { uploadsApi } from "@/api/endpoints"
import { API_BASE_URL } from "@/api/client"
import type { IngestionReport } from "@/types/api"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { useToast } from "@/components/ui/toast-context"
import { FileSpreadsheet, FileJson, CheckCircle2, AlertTriangle } from "lucide-react"

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

export function DataSourcesPage() {
  const { push } = useToast()
  const csvInputRef = useRef<HTMLInputElement>(null)
  const jsonInputRef = useRef<HTMLInputElement>(null)
  const [csvReport, setCsvReport] = useState<IngestionReport | null>(null)
  const [jsonReport, setJsonReport] = useState<IngestionReport | null>(null)
  const [csvIsSample, setCsvIsSample] = useState(false)
  const [jsonIsSample, setJsonIsSample] = useState(false)

  const csvMutation = useMutation({
    mutationFn: uploadsApi.crmCsv,
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
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  setCsvIsSample(false)
                  csvInputRef.current?.click()
                }}
              >
                Upload CSV
              </Button>
              <Button variant="accent" onClick={() => loadSample("crm")} disabled={csvMutation.isPending}>
                Use Sample Data
              </Button>
              <input
                ref={csvInputRef}
                type="file"
                accept=".csv"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) csvMutation.mutate(file)
                  e.target.value = ""
                }}
              />
            </div>
            {csvIsSample && <Badge variant="outline">Loaded from bundled sample dataset</Badge>}
            {csvMutation.isPending && <p className="text-sm text-muted-foreground">Uploading...</p>}
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
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  setJsonIsSample(false)
                  jsonInputRef.current?.click()
                }}
              >
                Upload JSON
              </Button>
              <Button variant="accent" onClick={() => loadSample("events")} disabled={jsonMutation.isPending}>
                Use Sample Data
              </Button>
              <input
                ref={jsonInputRef}
                type="file"
                accept=".json"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) jsonMutation.mutate(file)
                  e.target.value = ""
                }}
              />
            </div>
            {jsonIsSample && <Badge variant="outline">Loaded from bundled sample dataset</Badge>}
            {jsonMutation.isPending && <p className="text-sm text-muted-foreground">Uploading...</p>}
            {jsonReport && <ReportSummary report={jsonReport} />}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>What happens next</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Once leads and signals are loaded, head to the Pipeline view to
          trigger the agent graph for one or all leads. The API is reachable
          directly at <code className="text-xs bg-secondary px-1 py-0.5 rounded">{API_BASE_URL}</code> if
          you want to inspect requests, and the interactive API reference is
          served at <code className="text-xs bg-secondary px-1 py-0.5 rounded">{API_BASE_URL}/docs</code>.
        </CardContent>
      </Card>
    </div>
  )
}
