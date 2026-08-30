import { useEffect, useState } from "react"

// Rotates through a fixed list of short status strings while `active` is
// true — used anywhere a real multi-step/multi-row process is in flight
// but there's no single item to poll live progress for (bulk pipeline
// runs, CSV ingestion), so the loading state still reads as "here's the
// kind of work happening" rather than a generic spinner.
export function useRotatingLabel(labels: string[], active: boolean, intervalMs = 1800) {
  const [index, setIndex] = useState(0)
  useEffect(() => {
    if (!active) {
      setIndex(0)
      return
    }
    const id = setInterval(() => setIndex((i) => (i + 1) % labels.length), intervalMs)
    return () => clearInterval(id)
  }, [active, intervalMs, labels.length])
  return labels[index]
}
