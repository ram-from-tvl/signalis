import { cn } from "@/lib/utils"

// A deterministic color from a small warm-neutral-adjacent palette, hashed
// from the lead's name — gives an otherwise text-only list a real visual
// anchor per row without pulling in real avatar images that don't exist.
const PALETTE = [
  "bg-stage-late/20 text-stage-late",
  "bg-stage-mid/20 text-stage-mid",
  "bg-accent/20 text-accent",
  "bg-info/20 text-info",
  "bg-stage-early/20 text-stage-early",
]

function hashName(name: string) {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash << 5) - hash + name.charCodeAt(i)
    hash |= 0
  }
  return Math.abs(hash)
}

function initials(name: string) {
  const parts = name.trim().split(/\s+/)
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

export function LeadAvatar({ name, className }: { name: string; className?: string }) {
  const colorClass = PALETTE[hashName(name) % PALETTE.length]
  return (
    <div
      className={cn(
        "flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-sm font-semibold",
        colorClass,
        className
      )}
      aria-hidden="true"
    >
      {initials(name)}
    </div>
  )
}
