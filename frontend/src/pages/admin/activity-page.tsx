import { History, Search, X } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router'

import { PAGE_SIZE, useAccountEvents } from '@/api/admin'
import type { AccountEvent, AccountEventKind } from '@/api/client'
import { useMe } from '@/api/queries'
import { ErrorAlert } from '@/components/form'
import { NativeSelect } from '@/components/native-select'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatDateTime, formatNumber, formatRelative } from '@/lib/format'
import { useDebounced } from '@/lib/use-debounced'
import { cn } from '@/lib/utils'
import { AdminTabs } from '@/pages/admin/admin-tabs'
import { NotFound } from '@/pages/not-found'

const SEARCH_DELAY_MS = 300

/** Filter choices: groups of event kinds. */
const GROUPS: { value: string; label: string; events: AccountEventKind[] }[] = [
  { value: '', label: 'All events', events: [] },
  { value: 'new', label: 'Signups and new accounts', events: ['signed_up', 'created'] },
  { value: 'plan', label: 'Plan changes', events: ['upgraded', 'downgraded', 'role_changed'] },
  { value: 'profile', label: 'Name and email changes', events: ['name_changed', 'email_changed'] },
  {
    value: 'status',
    label: 'Deactivated, deleted, restored',
    events: ['deactivated', 'reactivated', 'deleted', 'restored'],
  },
  { value: 'suspension', label: 'Suspensions', events: ['suspended', 'unsuspended'] },
  { value: 'removed', label: 'Removed for good', events: ['removed'] },
]

/** Admins only, like the rest of the Administration page. */
export function ActivityPage() {
  const me = useMe()
  if (!me.data) return me.error ? <ErrorAlert>{me.error.message}</ErrorAlert> : null
  if (!me.data.is_admin) return <NotFound />
  return <Activity myId={me.data.id} />
}

function Activity({ myId }: { myId: string }) {
  // ?user=<id>&email=<label> shows one account's history (from the Users tab).
  const [params, setParams] = useSearchParams()
  const userId = params.get('user')
  const userLabel = params.get('email') ?? userId
  const [search, setSearch] = useState('')
  const [group, setGroup] = useState('')
  const q = useDebounced(search, SEARCH_DELAY_MS)
  const events = GROUPS.find((g) => g.value === group)?.events ?? []

  const searchKey = `${q.trim()}|${group}|${userId}`
  const [page, setPage] = useState({ searchKey, offset: 0 })
  const offset = page.searchKey === searchKey ? page.offset : 0
  const setOffset = (next: number) => setPage({ searchKey, offset: next })
  const log = useAccountEvents({ q, userId, events, offset })

  const total = log.data?.total ?? 0
  const shown = log.data?.events ?? []

  return (
    <>
      <PageHeader
        title="Administration"
        description="Who signed up, was created, changed plan or was removed, and by whom."
      />
      <AdminTabs />

      <div className="mb-4 flex flex-wrap gap-2">
        {userId ? (
          <div className="flex min-w-60 flex-1 items-center gap-2 rounded-lg border px-3 text-sm">
            <span className="truncate">
              History of <span className="font-medium">{userLabel}</span>
            </span>
            <Button
              variant="ghost"
              size="icon-sm"
              className="ml-auto text-muted-foreground"
              aria-label="Show every account"
              title="Show every account"
              onClick={() => setParams({})}
            >
              <X />
            </Button>
          </div>
        ) : (
          <div className="relative min-w-60 flex-1">
            <Search
              className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <Input
              type="search"
              aria-label="Search by email"
              placeholder="Search by account or admin email"
              className="pl-8"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        )}
        <NativeSelect aria-label="Events" className="w-64" value={group} onChange={(e) => setGroup(e.target.value)}>
          {GROUPS.map((g) => (
            <option key={g.value} value={g.value}>
              {g.label}
            </option>
          ))}
        </NativeSelect>
      </div>

      {log.error && <ErrorAlert>{log.error.message}</ErrorAlert>}

      {log.data && shown.length === 0 && (
        <div className="flex flex-col items-center rounded-lg border px-6 py-12 text-center">
          <History className="size-6 text-muted-foreground" aria-hidden="true" />
          <p className="mt-3 font-medium">No events found</p>
          <p className="mt-1 text-sm text-muted-foreground">Try another search or filter.</p>
        </div>
      )}

      {shown.length > 0 && (
        <>
          <div className={cn('rounded-lg border', log.isPlaceholderData && 'opacity-60')}>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">When</TableHead>
                  <TableHead>Account</TableHead>
                  <TableHead>What happened</TableHead>
                  <TableHead>By</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {shown.map((event) => (
                  <TableRow key={event.id}>
                    <TableCell className="pl-4 whitespace-nowrap" title={formatDateTime(event.created_at)}>
                      {formatRelative(event.created_at)}
                    </TableCell>
                    <TableCell className="max-w-64">
                      <button
                        type="button"
                        className="max-w-full truncate text-left hover:underline"
                        title="Show this account's history"
                        onClick={() => setParams({ user: event.user_id, email: event.user_email ?? event.user_id })}
                      >
                        {event.user_email ?? event.user_id}
                      </button>
                    </TableCell>
                    <TableCell>
                      <EventBadge event={event} /> <span className="ml-1 text-muted-foreground">{describe(event)}</span>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{actor(event, myId)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <div className="mt-3 flex items-center justify-between gap-2 text-sm text-muted-foreground">
            <p>
              {formatNumber(offset + 1)}–{formatNumber(offset + shown.length)} of {formatNumber(total)}
            </p>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </>
  )
}

const LABELS: Record<AccountEventKind, string> = {
  signed_up: 'Signed up',
  created: 'Created',
  upgraded: 'Upgraded',
  downgraded: 'Downgraded',
  role_changed: 'Role changed',
  name_changed: 'Name changed',
  email_changed: 'Email changed',
  deactivated: 'Deactivated',
  reactivated: 'Reactivated',
  deleted: 'Deleted',
  restored: 'Restored',
  suspended: 'Suspended',
  unsuspended: 'Unsuspended',
  removed: 'Removed',
}

const GOOD = 'border-primary/30 bg-primary/10 text-primary'
const BAD = 'border-destructive/30 bg-destructive/10 text-destructive'
const NEUTRAL = 'bg-muted text-muted-foreground'
const TONES: Partial<Record<AccountEventKind, string>> = {
  signed_up: GOOD,
  created: GOOD,
  upgraded: GOOD,
  reactivated: GOOD,
  restored: GOOD,
  unsuspended: GOOD,
  downgraded: BAD,
  deactivated: BAD,
  deleted: BAD,
  suspended: BAD,
  removed: BAD,
}

function EventBadge({ event }: { event: AccountEvent }) {
  return (
    <span
      className={cn('inline-flex rounded-full border px-2 py-0.5 text-xs font-medium', TONES[event.event] ?? NEUTRAL)}
    >
      {LABELS[event.event]}
    </span>
  )
}

const text = (value: unknown) => (typeof value === 'string' && value ? value : 'none')

/** The before and after of an event, in a few words. */
function describe(event: AccountEvent): string {
  const d = event.details
  switch (event.event) {
    case 'signed_up':
    case 'created':
      return d.backfilled ? `as ${text(d.role)} (before the log started)` : `as ${text(d.role)}`
    case 'upgraded':
    case 'downgraded':
    case 'role_changed':
      return d.from_tier === d.to_tier
        ? `${text(d.from_role)} → ${text(d.to_role)} (${text(d.to_tier)} tier)`
        : `${text(d.from_role)} → ${text(d.to_role)} (${text(d.from_tier)} → ${text(d.to_tier)})`
    case 'name_changed':
    case 'email_changed':
      return `${text(d.from)} → ${text(d.to)}`
    case 'suspended':
      return d.banned_until ? 'banned or deleted in Supabase Auth' : 'deleted in Supabase Auth'
    default:
      return ''
  }
}

function actor(event: AccountEvent, myId: string): string {
  if (event.actor_id === myId) return 'You'
  switch (event.source) {
    case 'user':
      return 'Themselves'
    case 'admin':
      return event.actor_email ?? 'An admin'
    case 'system':
      return 'The server (first admin)'
    default:
      return 'Supabase Auth or Studio'
  }
}
