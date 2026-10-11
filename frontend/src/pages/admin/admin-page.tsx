import { History, Pencil, Plus, RotateCcw, Search, Trash2, Users } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'

import { PAGE_SIZE, useAdminUsers, useUpdateUser } from '@/api/admin'
import type { AdminUser, UserStatus } from '@/api/client'
import { useMe } from '@/api/queries'
import { ErrorAlert } from '@/components/form'
import { NativeSelect } from '@/components/native-select'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatDate, formatDateTime, formatNumber } from '@/lib/format'
import { useDebounced } from '@/lib/use-debounced'
import { cn } from '@/lib/utils'
import { AdminTabs } from '@/pages/admin/admin-tabs'
import { CreateUserDialog } from '@/pages/admin/create-user-dialog'
import { DeleteUserDialog } from '@/pages/admin/delete-user-dialog'
import { EditUserDialog } from '@/pages/admin/edit-user-dialog'
import { NotFound } from '@/pages/not-found'

const SEARCH_DELAY_MS = 300

/** For admins only; everyone else sees the not-found page, as if it did not exist. */
export function AdminPage() {
  const me = useMe()
  if (!me.data) return me.error ? <ErrorAlert>{me.error.message}</ErrorAlert> : null
  if (!me.data.is_admin) return <NotFound />
  return <Administration myId={me.data.id} />
}

function Administration({ myId }: { myId: string }) {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<UserStatus | null>(null)
  const q = useDebounced(search, SEARCH_DELAY_MS)
  // A new search starts from the first page.
  const searchKey = `${q.trim()}|${status}`
  const [page, setPage] = useState({ searchKey, offset: 0 })
  const offset = page.searchKey === searchKey ? page.offset : 0
  const setOffset = (next: number) => setPage({ searchKey, offset: next })
  const users = useAdminUsers({ q, status, offset })
  const restore = useUpdateUser()
  const navigate = useNavigate()

  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<AdminUser | null>(null)
  const [deleting, setDeleting] = useState<AdminUser | null>(null)

  const total = users.data?.total ?? 0
  const shown = users.data?.users ?? []

  // Deleting or restoring the last users on the last page empties it: go back
  // to the page that is now the last.
  if (users.data && !users.isPlaceholderData && offset > 0 && offset >= total) {
    setOffset(Math.max(0, Math.ceil(total / PAGE_SIZE) - 1) * PAGE_SIZE)
  }

  return (
    <>
      <PageHeader
        title="Administration"
        description="Find user accounts, change their name or plan, deactivate, delete or restore them."
        action={
          <Button onClick={() => setCreating(true)}>
            <Plus aria-hidden="true" />
            Create user
          </Button>
        }
      />
      <AdminTabs />

      <div className="mb-4 flex flex-wrap gap-2">
        <div className="relative min-w-60 flex-1">
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <Input
            type="search"
            aria-label="Search by name or email"
            placeholder="Search by name or email"
            className="pl-8"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <NativeSelect
          aria-label="Status"
          className="w-40"
          value={status ?? ''}
          onChange={(e) => setStatus((e.target.value || null) as UserStatus | null)}
        >
          <option value="">All users</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
          <option value="deleted">Deleted</option>
        </NativeSelect>
      </div>

      {users.error && <ErrorAlert>{users.error.message}</ErrorAlert>}
      {restore.error && <ErrorAlert>{restore.error.message}</ErrorAlert>}

      {users.data && shown.length === 0 && (
        <div className="flex flex-col items-center rounded-lg border px-6 py-12 text-center">
          <Users className="size-6 text-muted-foreground" aria-hidden="true" />
          <p className="mt-3 font-medium">No users found</p>
          <p className="mt-1 text-sm text-muted-foreground">Try another search or status.</p>
        </div>
      )}

      {shown.length > 0 && (
        <>
          <div className={cn('rounded-lg border', users.isPlaceholderData && 'opacity-60')}>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">User</TableHead>
                  <TableHead>Plan</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Keys</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="w-32">
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {shown.map((user) => {
                  const label = user.email ?? user.id
                  const isSelf = user.id === myId
                  return (
                    <TableRow key={user.id}>
                      <TableCell className="max-w-72 pl-4">
                        <p className="truncate font-medium" title={user.display_name ?? label}>
                          {user.display_name ?? label}
                          {isSelf && <span className="ml-2 text-xs font-normal text-muted-foreground">(you)</span>}
                        </p>
                        {user.display_name && (
                          <p className="truncate text-xs text-muted-foreground" title={label}>
                            {label}
                          </p>
                        )}
                      </TableCell>
                      <TableCell>
                        {user.role}
                        <span className="ml-1 text-muted-foreground">· {user.tier}</span>
                      </TableCell>
                      <TableCell>
                        <StatusBadge user={user} />
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{formatNumber(user.active_keys)}</TableCell>
                      <TableCell title={formatDateTime(user.created_at)}>{formatDate(user.created_at)}</TableCell>
                      <TableCell className="pr-2 text-right whitespace-nowrap">
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          className="text-muted-foreground"
                          aria-label={`History of ${label}`}
                          title="History"
                          onClick={() =>
                            navigate(`/admin/activity?${new URLSearchParams({ user: user.id, email: label })}`)
                          }
                        >
                          <History />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          className="text-muted-foreground"
                          aria-label={`Edit ${label}`}
                          title="Edit"
                          onClick={() => setEditing(user)}
                        >
                          <Pencil />
                        </Button>
                        {user.is_deleted && (
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            className="text-muted-foreground"
                            aria-label={`Restore ${label}`}
                            title="Restore"
                            disabled={restore.isPending}
                            onClick={() =>
                              restore.mutate(
                                { id: user.id, changes: { is_deleted: false } },
                                { onSuccess: () => toast.success(`Restored ${user.display_name ?? label}`) },
                              )
                            }
                          >
                            <RotateCcw />
                          </Button>
                        )}
                        {!isSelf && (
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            className="text-muted-foreground hover:text-destructive"
                            aria-label={user.is_deleted ? `Permanently delete ${label}` : `Delete ${label}`}
                            title={user.is_deleted ? 'Delete permanently' : 'Delete'}
                            onClick={() => setDeleting(user)}
                          >
                            <Trash2 />
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  )
                })}
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

      <CreateUserDialog open={creating} onOpenChange={setCreating} />
      <EditUserDialog user={editing} isSelf={editing?.id === myId} onClose={() => setEditing(null)} />
      <DeleteUserDialog user={deleting} onClose={() => setDeleting(null)} />
    </>
  )
}

function StatusBadge({ user }: { user: AdminUser }) {
  const [label, tone, title] = user.is_deleted
    ? ['Deleted', 'border-destructive/30 bg-destructive/10 text-destructive', undefined]
    : !user.is_active
      ? ['Inactive', 'bg-muted text-muted-foreground', undefined]
      : user.auth_suspended
        ? ['Suspended', 'bg-muted text-muted-foreground', 'Banned or deleted in Supabase Auth (Studio)']
        : ['Active', 'border-primary/30 bg-primary/10 text-primary', undefined]
  return (
    <span className={cn('inline-flex rounded-full border px-2 py-0.5 text-xs font-medium', tone)} title={title}>
      {label}
    </span>
  )
}
