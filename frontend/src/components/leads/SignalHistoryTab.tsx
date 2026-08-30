import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { MousePointerClick, FileText, Mail, PhoneCall, TrendingUp, Radar } from "lucide-react"
import { cn, parseUtcTimestamp } from "@/lib/utils"
import type { Signal } from "@/types/api"

function formatDate(iso: string) {
  return parseUtcTimestamp(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

// `raw_source` values are lowercase machine identifiers; CSS `capitalize`
// alone turns "crm" into "Crm" instead of the real acronym.
const RAW_SOURCE_LABELS: Record<string, string> = {
  crm: "CRM",
  website: "Website",
  email: "Email",
  linkedin: "LinkedIn",
}

const SIGNAL_PAYLOAD_HIDDEN_KEYS = new Set([
  "event_type",
  "lead_email",
  "name",
  "company",
  "timestamp",
  "simulated",
])

function formatSignalPayloadKey(key: string) {
  return key
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ")
}

function formatSignalPayloadValue(value: unknown) {
  if (value === null || value === undefined) return "—"
  if (typeof value === "string") return value.replace(/_/g, " ")
  if (typeof value === "boolean") return value ? "Yes" : "No"
  return String(value)
}

function SignalPayloadDetail({ payload }: { payload: Record<string, unknown> | null | undefined }) {
  if (!payload || typeof payload !== "object") return null
  const entries = Object.entries(payload).filter(([key]) => !SIGNAL_PAYLOAD_HIDDEN_KEYS.has(key))
  if (entries.length === 0) return null

  return (
    <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground mt-1">
      {entries.map(([key, value]) => (
        <span key={key}>
          <span className="font-medium text-foreground/70">{formatSignalPayloadKey(key)}:</span>{" "}
          {formatSignalPayloadValue(value)}
        </span>
      ))}
    </div>
  )
}

function eventIcon(eventType: string) {
  const lower = eventType.toLowerCase()
  if (lower.includes("email") || lower.includes("reply")) return Mail
  if (lower.includes("demo") || lower.includes("call")) return PhoneCall
  if (lower.includes("pricing") || lower.includes("deal") || lower.includes("stage")) return TrendingUp
  if (lower.includes("page") || lower.includes("visit") || lower.includes("download")) return FileText
  return MousePointerClick
}

const STRENGTH_CONFIG: Record<string, { dot: string; ring: string; label: string }> = {
  late: { dot: "bg-stage-late", ring: "ring-stage-late/25", label: "High intent" },
  mid: { dot: "bg-stage-mid", ring: "ring-stage-mid/25", label: "Medium intent" },
  early: { dot: "bg-stage-early", ring: "ring-stage-early/25", label: "Low intent" },
}

interface SignalGroup {
  key: string
  signal: Signal
  count: number
}

// Signals that share lead + event type + source + exact occurred_at
// timestamp are the same underlying event ingested more than once (a
// real, visible data quirk) — group them into one row with a ×N count
// instead of listing exact duplicates as separate entries.
function groupSignals(signals: Signal[]): SignalGroup[] {
  const groups = new Map<string, SignalGroup>()
  for (const signal of signals) {
    const key = `${signal.event_type}|${signal.raw_source}|${signal.occurred_at}`
    const existing = groups.get(key)
    if (existing) {
      existing.count += 1
    } else {
      groups.set(key, { key, signal, count: 1 })
    }
  }
  return Array.from(groups.values())
}

export function SignalHistoryTab({ signals }: { signals: Signal[] }) {
  if (signals.length === 0) {
    return (
      <Card>
        <CardContent className="py-8 flex flex-col items-center gap-2 text-center text-sm text-muted-foreground">
          <Radar className="h-5 w-5 text-muted-foreground/60" />
          No signals recorded for this lead yet.
        </CardContent>
      </Card>
    )
  }

  const ordered = signals.slice().reverse()
  const grouped = groupSignals(ordered)

  return (
    <Card>
      <CardContent className="py-4">
        <ol className="relative flex flex-col gap-5 before:absolute before:left-[13px] before:top-3 before:bottom-3 before:w-px before:bg-border">
          {grouped.map(({ key, signal, count }) => {
            const strength = STRENGTH_CONFIG[signal.intent_stage_hint] ?? STRENGTH_CONFIG.early
            const Icon = eventIcon(signal.event_type)
            return (
              <li key={key} className="relative pl-9">
                <span
                  className={cn(
                    "absolute left-0 top-0 flex h-7 w-7 items-center justify-center rounded-full ring-4",
                    strength.dot,
                    strength.ring
                  )}
                  title={strength.label}
                >
                  <Icon className="h-3.5 w-3.5 text-white" />
                </span>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-semibold capitalize">
                    {signal.event_type.replace(/_/g, " ")}
                  </span>
                  {count > 1 && (
                    <Badge variant="secondary" className="text-[10px]">
                      &times;{count}
                    </Badge>
                  )}
                  <Badge variant="outline" className="text-[10px]">
                    {RAW_SOURCE_LABELS[signal.raw_source] ?? signal.raw_source}
                  </Badge>
                  <span className="text-xs text-muted-foreground">{formatDate(signal.occurred_at)}</span>
                </div>
                <SignalPayloadDetail payload={signal.raw_payload} />
              </li>
            )
          })}
        </ol>
      </CardContent>
    </Card>
  )
}
