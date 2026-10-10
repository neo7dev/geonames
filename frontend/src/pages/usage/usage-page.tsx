import { useState } from 'react'
import { useSearchParams } from 'react-router'

import { useUsage } from '@/api/usage'
import { ErrorAlert } from '@/components/form'
import { LiveNote } from '@/components/live-note'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { UsageMeter } from '@/components/usage-meter'
import { formatLongDay, formatNumber } from '@/lib/format'
import { cn } from '@/lib/utils'
import { DailyChart } from '@/pages/usage/daily-chart'

const RANGES = [7, 30, 90] as const
type Range = (typeof RANGES)[number]

function useRange(): [Range, (days: Range) => void] {
  const [params, setParams] = useSearchParams()
  const value = Number(params.get('days'))
  const days = (RANGES as readonly number[]).includes(value) ? (value as Range) : 30
  return [days, (next) => setParams(next === 30 ? {} : { days: String(next) }, { replace: true })]
}

export function UsagePage() {
  const [days, setDays] = useRange()
  const usage = useUsage(days)
  const data = usage.data
  const total = data?.daily.reduce((sum, d) => sum + d.requests, 0) ?? 0
  // The range `data` covers: while a new range loads, still the old one.
  const [dataDays, setDataDays] = useState(days)
  if (data && !usage.isPlaceholderData && dataDays !== days) setDataDays(days)

  return (
    <>
      <PageHeader title="Usage" description="Requests made with your API keys. Days and months are UTC." />

      {/* Filters: one row, above everything they scope. */}
      <div role="group" aria-label="Time range" className="mb-6 inline-flex rounded-lg border p-0.5">
        {RANGES.map((range) => (
          <Button
            key={range}
            variant="ghost"
            size="sm"
            aria-pressed={days === range}
            className={cn('px-3', days === range && 'bg-muted font-semibold text-foreground')}
            onClick={() => setDays(range)}
          >
            {range} days
          </Button>
        ))}
      </div>

      {usage.error && <ErrorAlert>{usage.error.message}</ErrorAlert>}
      {data && !data.live && <LiveNote />}

      {data && (
        // A new range keeps the old figures, dimmed, until the new ones arrive.
        <div className={cn('grid gap-6', usage.isPlaceholderData && 'opacity-60')}>
          <div className="grid gap-4 sm:grid-cols-3">
            <UsageMeter label="This minute" window={data.current.minute} />
            <UsageMeter label="This hour" window={data.current.hour} />
            <UsageMeter label="This month (UTC)" window={data.current.month} />
          </div>

          <section className="rounded-lg border p-4">
            <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="font-medium">Requests per day</h2>
              <p className="text-sm text-muted-foreground">
                {formatNumber(total)} in the last {dataDays} days
              </p>
            </div>
            <DailyChart daily={data.daily} />
            <details className="mt-3 text-sm">
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Show as a table</summary>
              <Table className="mt-2">
                <TableHeader>
                  <TableRow>
                    <TableHead>Day (UTC)</TableHead>
                    <TableHead className="text-right">Requests</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {[...data.daily].reverse().map((d) => (
                    <TableRow key={d.date}>
                      <TableCell>{formatLongDay(d.date)}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatNumber(d.requests)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </details>
            <p className="mt-3 text-xs text-muted-foreground">
              Daily counts are saved every few minutes, so today's bar can lag the meters above.
            </p>
          </section>

          <section>
            <h2 className="mb-3 font-medium">This month by key</h2>
            {data.keys.length === 0 ? (
              <p className="rounded-lg border px-4 py-6 text-center text-sm text-muted-foreground">
                No keys yet, and no requests this month.
              </p>
            ) : (
              <div className="rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="pl-4">Name</TableHead>
                      <TableHead>Key</TableHead>
                      <TableHead className="pr-4 text-right">Requests</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.keys.map((key) => (
                      <TableRow key={key.id}>
                        <TableCell className="pl-4 font-medium">
                          {key.name}
                          {key.revoked && (
                            <span className="ml-2 rounded border px-1.5 py-0.5 text-xs font-normal text-muted-foreground">
                              Revoked
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">{key.prefix}…</TableCell>
                        <TableCell className="pr-4 text-right tabular-nums">
                          {formatNumber(key.requests_this_month)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </section>
        </div>
      )}
    </>
  )
}
