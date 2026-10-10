import { Info } from 'lucide-react'

/** Shown while the API reads the counts from memory or Postgres instead of Redis. */
export function LiveNote() {
  return (
    <p className="mb-4 flex items-center gap-2 rounded-lg border px-3 py-2 text-sm text-muted-foreground">
      <Info className="size-4 shrink-0" aria-hidden="true" />
      Live counts are unavailable right now, so the counts may lag. Limits still apply.
    </p>
  )
}
