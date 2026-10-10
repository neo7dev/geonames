import { KeyRound, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { useState } from 'react'

import type { ApiKey } from '@/api/client'
import { useKeys } from '@/api/keys'
import { useMe } from '@/api/queries'
import { ErrorAlert } from '@/components/form'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatDate, formatDateTime, formatRelative } from '@/lib/format'
import { CreateKeyDialog } from '@/pages/keys/create-key-dialog'
import { RevokeKeyDialog } from '@/pages/keys/revoke-key-dialog'
import { RollKeyDialog } from '@/pages/keys/roll-key-dialog'

export function KeysPage() {
  const keys = useKeys()
  const me = useMe()
  const [creating, setCreating] = useState(false)
  const [revoking, setRevoking] = useState<ApiKey | null>(null)
  const [rolling, setRolling] = useState<ApiKey | null>(null)

  const maxKeys = me.data?.limits.max_keys ?? null
  const atLimit = me.data !== undefined && maxKeys !== null && me.data.active_keys >= maxKeys

  return (
    <>
      <PageHeader
        title="API keys"
        description={
          <>
            Send a key in the <code className="font-mono text-xs">X-API-Key</code> header. Your limits are shared
            across all your keys.
          </>
        }
        action={
          <Button onClick={() => setCreating(true)} disabled={!me.data || atLimit}>
            <Plus aria-hidden="true" />
            Create API key
          </Button>
        }
      />

      {atLimit && (
        <p className="mb-4 rounded-lg border px-3 py-2 text-sm text-muted-foreground">
          The {me.data!.tier} tier allows {maxKeys} active API {maxKeys === 1 ? 'key' : 'keys'}. Revoke one to create
          another.
        </p>
      )}

      {/* Without the profile the key limit is unknown, so Create stays disabled: say why. */}
      {me.error && <ErrorAlert>{me.error.message}</ErrorAlert>}
      {keys.error && <ErrorAlert>{keys.error.message}</ErrorAlert>}

      {keys.data && keys.data.length === 0 && (
        <div className="flex flex-col items-center rounded-lg border px-6 py-12 text-center">
          <KeyRound className="size-6 text-muted-foreground" aria-hidden="true" />
          <p className="mt-3 font-medium">No API keys yet</p>
          <p className="mt-1 text-sm text-muted-foreground">Create one to start calling the API.</p>
        </div>
      )}

      {keys.data && keys.data.length > 0 && (
        <>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">Name</TableHead>
                  <TableHead>Key</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Last used</TableHead>
                  <TableHead className="w-20">
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {keys.data.map((key) => (
                  <TableRow key={key.id}>
                    <TableCell className="pl-4 font-medium">{key.name}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{key.prefix}…</TableCell>
                    <TableCell title={formatDateTime(key.created_at)}>{formatDate(key.created_at)}</TableCell>
                    <TableCell
                      className="text-muted-foreground"
                      title={key.last_used_at ? formatDateTime(key.last_used_at) : undefined}
                    >
                      {key.last_used_at ? formatRelative(key.last_used_at) : 'Never'}
                    </TableCell>
                    <TableCell className="pr-2 text-right whitespace-nowrap">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="text-muted-foreground"
                        aria-label={`Roll ${key.name}`}
                        title="Roll: replace with a new key"
                        onClick={() => setRolling(key)}
                      >
                        <RefreshCw />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="text-muted-foreground hover:text-destructive"
                        aria-label={`Revoke ${key.name}`}
                        title="Revoke"
                        onClick={() => setRevoking(key)}
                      >
                        <Trash2 />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">"Last used" is updated every few minutes.</p>
        </>
      )}

      <CreateKeyDialog open={creating} onOpenChange={setCreating} />
      <RevokeKeyDialog apiKey={revoking} onClose={() => setRevoking(null)} />
      <RollKeyDialog apiKey={rolling} onClose={() => setRolling(null)} />
    </>
  )
}
