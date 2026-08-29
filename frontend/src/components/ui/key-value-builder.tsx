import { useEffect, useState } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Plus, X } from "lucide-react"

interface Row {
  id: number
  key: string
  value: string
}

let rowIdCounter = 0

function objectToRows(obj: Record<string, unknown>): Row[] {
  return Object.entries(obj).map(([key, value]) => ({
    id: ++rowIdCounter,
    key,
    value: typeof value === "string" ? value : JSON.stringify(value),
  }))
}

function rowsToObject(rows: Row[]): Record<string, string> {
  const obj: Record<string, string> = {}
  for (const row of rows) {
    if (row.key.trim()) obj[row.key.trim()] = row.value
  }
  return obj
}

// A row-based key/value editor that serializes to a plain JSON object
// behind the scenes — so a non-technical marketer never has to type or
// see a brace, quote, or comma to add a custom persona trait.
export function KeyValueBuilder({
  value,
  onChange,
}: {
  value: Record<string, unknown>
  onChange: (next: Record<string, unknown>) => void
}) {
  const [rows, setRows] = useState<Row[]>(() => {
    const initial = objectToRows(value)
    return initial.length > 0 ? initial : [{ id: ++rowIdCounter, key: "", value: "" }]
  })

  // Keep local rows in sync if the parent's saved value changes out from
  // under us (e.g. after a successful save re-fetches the persona).
  useEffect(() => {
    const incoming = objectToRows(value)
    setRows(incoming.length > 0 ? incoming : [{ id: ++rowIdCounter, key: "", value: "" }])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(value)])

  const updateRow = (id: number, patch: Partial<Row>) => {
    const next = rows.map((r) => (r.id === id ? { ...r, ...patch } : r))
    setRows(next)
    onChange(rowsToObject(next))
  }

  const addRow = () => {
    setRows([...rows, { id: ++rowIdCounter, key: "", value: "" }])
  }

  const removeRow = (id: number) => {
    const next = rows.filter((r) => r.id !== id)
    setRows(next)
    onChange(rowsToObject(next))
  }

  return (
    <div className="flex flex-col gap-2">
      {rows.map((row) => (
        <div key={row.id} className="flex items-center gap-2">
          <Input
            placeholder="Trait name, e.g. preferred_tone"
            value={row.key}
            onChange={(e) => updateRow(row.id, { key: e.target.value })}
            className="flex-1"
          />
          <Input
            placeholder="Value"
            value={row.value}
            onChange={(e) => updateRow(row.id, { value: e.target.value })}
            className="flex-1"
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => removeRow(row.id)}
            aria-label="Remove trait"
            className="shrink-0 text-muted-foreground hover:text-destructive"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      ))}
      <Button type="button" variant="outline" size="sm" onClick={addRow} className="self-start">
        <Plus className="h-3.5 w-3.5" /> Add trait
      </Button>
    </div>
  )
}
