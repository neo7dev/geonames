import type { UsageWindow } from '@/api/usage'
import { formatDateTime, formatNumber, formatRelative } from '@/lib/format'
import { cn } from '@/lib/utils'

/** Fraction of the limit at which the meter turns amber. */
const WARN_AT = 0.8

export function UsageMeter({ label, window }: { label: string; window: UsageWindow }) {
  const { used, limit, resets_at } = window
  const share = used !== null && limit !== null && limit > 0 ? Math.min(used / limit, 1) : null
  const fill = share === null ? '' : share >= 1 ? 'bg-destructive' : share >= WARN_AT ? 'bg-meter-warn' : 'bg-chart-1'

  return (
    <div className="rounded-lg border p-4">
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight">
        {used === null ? '—' : formatNumber(used)}
        <span className="ml-1 text-sm font-normal text-muted-foreground">
          {limit === null ? 'requests, unlimited' : `of ${formatNumber(limit)}`}
        </span>
      </p>
      {limit !== null && (
        <div
          role="meter"
          aria-label={`${label}: requests used`}
          aria-valuemin={0}
          aria-valuemax={limit}
          aria-valuenow={used ?? undefined}
          aria-valuetext={used === null ? 'Unknown' : `${formatNumber(used)} of ${formatNumber(limit)}`}
          className="mt-3 h-1.5 overflow-hidden rounded-full bg-meter-track"
        >
          {share !== null && <div className={cn('h-full rounded-full', fill)} style={{ width: `${share * 100}%` }} />}
        </div>
      )}
      <p className="mt-2 text-xs text-muted-foreground" title={`${formatDateTime(resets_at)} (your time)`}>
        Resets {formatRelative(resets_at)}
      </p>
    </div>
  )
}
