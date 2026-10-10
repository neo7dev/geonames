import { Link } from 'react-router'

import type { Me } from '@/api/client'
import { useMe } from '@/api/queries'
import { useUsage } from '@/api/usage'
import { CopyButton } from '@/components/copy-button'
import { ErrorAlert } from '@/components/form'
import { LiveNote } from '@/components/live-note'
import { PageHeader } from '@/components/page-header'
import { UsageMeter } from '@/components/usage-meter'
import { formatNumber } from '@/lib/format'

const limit = (n: number | null, unit: string) => (n === null ? 'Unlimited' : `${formatNumber(n)} ${unit}`)

function Plan({ me }: { me: Me }) {
  const rows = [
    ['Role', me.role],
    ['Per minute', limit(me.limits.per_minute, 'requests')],
    ['Per hour', limit(me.limits.per_hour, 'requests')],
    ['Per month', limit(me.limits.per_month, 'requests')],
    ['API keys', me.limits.max_keys === null ? `${me.active_keys} of unlimited` : `${me.active_keys} of ${me.limits.max_keys}`],
  ]
  return (
    <section className="rounded-lg border p-4">
      <h2 className="font-medium">
        <span className="capitalize">{me.tier}</span> tier
      </h2>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
        {rows.map(([term, value]) => (
          <div key={term} className="contents">
            <dt className="text-muted-foreground">{term}</dt>
            <dd className="tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-xs text-muted-foreground">Limits count every request across all your keys.</p>
    </section>
  )
}

function QuickStart() {
  const command = `curl -H "X-API-Key: YOUR_API_KEY" \\\n  "${window.location.origin}/cities15000/?limit=5"`
  return (
    <section className="rounded-lg border p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-medium">Make a request</h2>
        <CopyButton value={command} size="sm" />
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        Replace <code className="font-mono text-xs">YOUR_API_KEY</code> with a key from{' '}
        <Link to="/keys" className="text-primary underline-offset-4 hover:underline">
          API keys
        </Link>
        . Every endpoint is in the{' '}
        <a href="/docs" target="_blank" rel="noreferrer" className="text-primary underline-offset-4 hover:underline">
          API docs
        </a>
        .
      </p>
      <pre className="mt-3 overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs">{command}</pre>
    </section>
  )
}

export function OverviewPage() {
  const me = useMe()
  const usage = useUsage()
  const current = usage.data?.current

  return (
    <>
      <PageHeader title="Overview" description="Your limits and how much of them you have used." />
      {(me.error || usage.error) && <ErrorAlert>{(me.error ?? usage.error)!.message}</ErrorAlert>}
      {usage.data && !usage.data.live && <LiveNote />}
      {current && (
        <div className="grid gap-4 sm:grid-cols-3">
          <UsageMeter label="This minute" window={current.minute} />
          <UsageMeter label="This hour" window={current.hour} />
          <UsageMeter label="This month (UTC)" window={current.month} />
        </div>
      )}
      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
        {me.data && <Plan me={me.data} />}
        <QuickStart />
      </div>
    </>
  )
}
