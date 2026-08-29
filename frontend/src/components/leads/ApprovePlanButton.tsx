import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { ClickSpark } from "@/components/ui/click-spark"
import { CheckCircle2 } from "lucide-react"

// A single unconfirmed click undersells a product whose entire pitch is
// "nothing ships without explicit human approval" — this gives the
// approve action a real confirm-on-click beat instead of firing instantly.
// Auto-resets after a few seconds so an accidental first click doesn't
// leave the button silently armed indefinitely.
export function ApprovePlanButton({
  onConfirm,
  disabled,
  confirmLabel = "Confirm send?",
}: {
  onConfirm: () => void
  disabled?: boolean
  confirmLabel?: string
}) {
  const [confirming, setConfirming] = useState(false)

  useEffect(() => {
    if (!confirming) return
    const timeout = setTimeout(() => setConfirming(false), 4000)
    return () => clearTimeout(timeout)
  }, [confirming])

  return (
    <ClickSpark>
      <Button
        size="sm"
        variant={confirming ? "accent" : "default"}
        disabled={disabled}
        onClick={() => {
          if (confirming) {
            setConfirming(false)
            onConfirm()
          } else {
            setConfirming(true)
          }
        }}
        onBlur={() => setConfirming(false)}
        className="transition-transform active:scale-95"
      >
        <CheckCircle2 className="h-4 w-4" />
        {confirming ? confirmLabel : "Approve Plan"}
      </Button>
    </ClickSpark>
  )
}
