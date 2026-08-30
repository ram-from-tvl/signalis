import { useState } from "react"
import { Copy, Check, AlertTriangle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useToast } from "@/components/ui/toast-context"
import { cn } from "@/lib/utils"

// A copy-to-clipboard action that confirms success inline (icon + label
// swap to a checkmark for 1.5s) rather than relying solely on a toast —
// the button itself is the thing the user is looking at when they click
// it, so the confirmation belongs there first. A failure (permission
// denied, unsupported browser) gets the same inline treatment plus a
// toast, so a rep trying to send outreach copy isn't left thinking the
// click did nothing.
export function CopyButton({
  value,
  label = "Copy",
  copiedLabel = "Copied",
  className,
}: {
  value: string
  label?: string
  copiedLabel?: string
  className?: string
}) {
  const { push } = useToast()
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle")

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setState("copied")
    } catch {
      setState("failed")
      push({
        title: "Couldn't copy to clipboard",
        description: "Your browser blocked clipboard access — select and copy the text manually instead.",
        variant: "error",
      })
    } finally {
      setTimeout(() => setState("idle"), 1500)
    }
  }

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={handleCopy}
      className={cn("gap-1.5", className)}
    >
      {state === "copied" && <Check className="h-3.5 w-3.5 text-success" />}
      {state === "failed" && <AlertTriangle className="h-3.5 w-3.5 text-destructive" />}
      {state === "idle" && <Copy className="h-3.5 w-3.5" />}
      {state === "copied" ? copiedLabel : state === "failed" ? "Couldn't copy" : label}
    </Button>
  )
}
