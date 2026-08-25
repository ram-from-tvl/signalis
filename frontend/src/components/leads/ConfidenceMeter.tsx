import { motion } from "motion/react"
import { cn } from "@/lib/utils"

export function ConfidenceMeter({
  confidence,
  className,
  showLabel = true,
}: {
  confidence: number
  className?: string
  showLabel?: boolean
}) {
  const pct = Math.round(confidence * 100)
  const color =
    confidence >= 0.7 ? "bg-success" : confidence >= 0.5 ? "bg-warning" : "bg-destructive"

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="h-1.5 w-20 rounded-full bg-muted overflow-hidden">
        <motion.div
          className={cn("h-full rounded-full", color)}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.5, ease: "easeOut" }}
        />
      </div>
      {showLabel && <span className="text-xs font-semibold tabular-nums text-muted-foreground">{pct}%</span>}
    </div>
  )
}
