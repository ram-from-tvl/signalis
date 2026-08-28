import { useCallback, useRef } from "react"
import { cn } from "@/lib/utils"

interface Spark {
  x: number
  y: number
  angle: number
  startTime: number
}

const SPARK_COUNT = 6
const SPARK_DURATION_MS = 300
const SPARK_LENGTH = 8
const SPARK_COLOR = "hsl(var(--accent))"

/**
 * A brief burst of short lines radiating from the click point, adapted
 * from ReactBits' "Click Spark" pattern into a small dependency-free
 * canvas overlay. Used only on approval actions as a positive-affirmation
 * micro-moment — not a general-purpose effect.
 */
export function ClickSpark({
  children,
  className,
  disabled,
}: {
  children: React.ReactNode
  className?: string
  disabled?: boolean
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const sparksRef = useRef<Spark[]>([])
  const rafRef = useRef<number | null>(null)

  const draw = useCallback((canvas: HTMLCanvasElement) => {
    const ctx = canvas.getContext("2d")
    if (!ctx) return

    const now = performance.now()
    ctx.clearRect(0, 0, canvas.width, canvas.height)

    sparksRef.current = sparksRef.current.filter((spark) => {
      const elapsed = now - spark.startTime
      if (elapsed >= SPARK_DURATION_MS) return false

      const progress = elapsed / SPARK_DURATION_MS
      const eased = 1 - Math.pow(1 - progress, 2)
      const length = SPARK_LENGTH * (1 - eased)
      const distance = eased * 12

      const x1 = spark.x + distance * Math.cos(spark.angle)
      const y1 = spark.y + distance * Math.sin(spark.angle)
      const x2 = x1 + length * Math.cos(spark.angle)
      const y2 = y1 + length * Math.sin(spark.angle)

      ctx.strokeStyle = SPARK_COLOR
      ctx.globalAlpha = 1 - progress
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.moveTo(x1, y1)
      ctx.lineTo(x2, y2)
      ctx.stroke()

      return true
    })

    if (sparksRef.current.length > 0) {
      rafRef.current = requestAnimationFrame(() => draw(canvas))
    } else {
      rafRef.current = null
    }
  }, [])

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (disabled || typeof window === "undefined") return
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return

      const canvas = canvasRef.current
      if (!canvas) return
      const rect = canvas.getBoundingClientRect()
      if (canvas.width !== rect.width || canvas.height !== rect.height) {
        canvas.width = rect.width
        canvas.height = rect.height
      }

      const x = e.clientX - rect.left
      const y = e.clientY - rect.top
      const now = performance.now()

      for (let i = 0; i < SPARK_COUNT; i++) {
        sparksRef.current.push({
          x,
          y,
          angle: (i / SPARK_COUNT) * Math.PI * 2,
          startTime: now,
        })
      }

      if (rafRef.current === null) {
        rafRef.current = requestAnimationFrame(() => draw(canvas))
      }
    },
    [disabled, draw]
  )

  return (
    <div className={cn("relative inline-block", className)} onClickCapture={handleClick}>
      {children}
      <canvas
        ref={canvasRef}
        className="pointer-events-none absolute inset-0 h-full w-full"
        aria-hidden="true"
      />
    </div>
  )
}
