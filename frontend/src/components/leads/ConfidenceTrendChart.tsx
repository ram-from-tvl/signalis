import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceDot } from "recharts"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { parseUtcTimestamp } from "@/lib/utils"
import type { StageClassification } from "@/types/api"

// The confidence-over-time story a flat list of rows hides: is the agent
// growing more or less sure about this lead as new evidence arrives.
export function ConfidenceTrendChart({ history }: { history: StageClassification[] }) {
  if (history.length < 2) return null

  // history arrives newest-first from the API; chart reads left-to-right
  // chronologically, the way a trend line is naturally read.
  const chronological = history.slice().reverse()
  const data = chronological.map((c) => ({
    id: c.id,
    time: parseUtcTimestamp(c.created_at).getTime(),
    confidence: Math.round(c.confidence * 100),
    stage: c.stage,
    label: parseUtcTimestamp(c.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" }),
  }))

  // Annotate the single biggest jump (up or down) with a marker, since
  // that's the moment a marketer actually cares about — new evidence that
  // changed the agent's mind, not every intermediate wobble.
  let biggestJumpIndex = -1
  let biggestJump = 0
  for (let i = 1; i < data.length; i++) {
    const delta = data[i].confidence - data[i - 1].confidence
    if (Math.abs(delta) > Math.abs(biggestJump)) {
      biggestJump = delta
      biggestJumpIndex = i
    }
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Confidence over time</CardTitle>
        <CardDescription>
          {biggestJumpIndex >= 0 && Math.abs(biggestJump) >= 10
            ? `${biggestJump > 0 ? "+" : ""}${biggestJump}% after the ${data[biggestJumpIndex].label} run`
            : `${data.length} classification runs for this lead`}
        </CardDescription>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="h-28 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="confidenceFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="hsl(var(--accent))" stopOpacity={0.25} />
                  <stop offset="100%" stopColor="hsl(var(--accent))" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="label" tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
              <YAxis domain={[0, 100]} hide />
              <Tooltip
                contentStyle={{
                  background: "hsl(var(--popover))",
                  border: "1px solid hsl(var(--border))",
                  borderRadius: "8px",
                  fontSize: "12px",
                }}
                labelStyle={{ color: "hsl(var(--foreground))", fontWeight: 600 }}
                formatter={(value) => [`${value}%`, "Confidence"]}
              />
              <Area
                type="monotone"
                dataKey="confidence"
                stroke="hsl(var(--accent))"
                strokeWidth={2}
                fill="url(#confidenceFill)"
              />
              {biggestJumpIndex >= 0 && Math.abs(biggestJump) >= 10 && (
                <ReferenceDot
                  x={data[biggestJumpIndex].label}
                  y={data[biggestJumpIndex].confidence}
                  r={4}
                  fill="hsl(var(--warning))"
                  stroke="hsl(var(--surface))"
                  strokeWidth={2}
                />
              )}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  )
}
