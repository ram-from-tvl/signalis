import { Badge } from "@/components/ui/badge"
import type { Stage } from "@/types/api"
import { cn } from "@/lib/utils"

const stageConfig: Record<Stage, { label: string; variant: "stageEarly" | "stageMid" | "stageLate" }> = {
  early: { label: "Early", variant: "stageEarly" },
  mid: { label: "Mid", variant: "stageMid" },
  late: { label: "Late", variant: "stageLate" },
}

export function StageBadge({ stage, className }: { stage: Stage; className?: string }) {
  const config = stageConfig[stage]
  return (
    <Badge variant={config.variant} className={cn(className)}>
      {config.label}
    </Badge>
  )
}
