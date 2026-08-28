import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// Backend timestamps (Python's naive datetime.utcnow(), serialized without
// a timezone suffix, e.g. "2026-08-28T11:57:30.142470") are genuinely UTC
// but carry no marker saying so. `new Date(...)` on a marker-less ISO
// string parses it as *local* time instead, silently shifting every parsed
// instant by the browser's UTC offset (a multi-hour error, not a rounding
// quirk). Appending "Z" (only when no offset is already present) makes the
// parse honest. Use this instead of a bare `new Date(iso)` for any
// backend-sourced timestamp string.
export function parseUtcTimestamp(iso: string): Date {
  const hasOffset = /Z$|[+-]\d{2}:?\d{2}$/.test(iso)
  return new Date(hasOffset ? iso : `${iso}Z`)
}
