import { useState } from 'react'

import type { ApiKey, CreatedApiKey } from '@/api/client'
import { useRollKey } from '@/api/keys'
import { ErrorAlert } from '@/components/form'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { NewKeyContent } from '@/pages/keys/create-key-dialog'
import { stayOpen } from '@/pages/keys/stay-open'

/** Confirm, then replace the key with a new one of the same name and show it once. */
export function RollKeyDialog({ apiKey, onClose }: { apiKey: ApiKey | null; onClose: () => void }) {
  return (
    <Dialog open={apiKey !== null} onOpenChange={(open) => !open && onClose()}>
      {/* Remount per key: the new key lives only in this component's state. */}
      {apiKey && <RollKeyContent key={apiKey.id} apiKey={apiKey} onDone={onClose} />}
    </Dialog>
  )
}

function RollKeyContent({ apiKey, onDone }: { apiKey: ApiKey; onDone: () => void }) {
  const roll = useRollKey()
  const [created, setCreated] = useState<CreatedApiKey | null>(null)

  if (created) return <NewKeyContent created={created} onDone={onDone} />

  // The new key arrives only through this component: closing it mid-request would lose it.
  return (
    <DialogContent {...(roll.isPending ? stayOpen : {})}>
      <DialogHeader>
        <DialogTitle>Roll "{apiKey.name}"?</DialogTitle>
        <DialogDescription>
          Requests with <code className="font-mono text-xs">{apiKey.prefix}…</code> stop working at once. You get a
          new key with the same name.
        </DialogDescription>
      </DialogHeader>
      {roll.error && <ErrorAlert>{roll.error.message}</ErrorAlert>}
      <DialogFooter>
        <Button variant="outline" disabled={roll.isPending} onClick={onDone}>
          Cancel
        </Button>
        <Button disabled={roll.isPending} onClick={() => roll.mutate(apiKey.id, { onSuccess: setCreated })}>
          Roll key
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}
