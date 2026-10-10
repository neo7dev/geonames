import { TriangleAlert } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import type { CreatedApiKey } from '@/api/client'
import { keyNameError, MAX_KEY_NAME, useCreateKey } from '@/api/keys'
import { CopyButton } from '@/components/copy-button'
import { ErrorAlert, Field } from '@/components/form'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { stayOpen } from '@/pages/keys/stay-open'

/**
 * Name a key, then show it once. The key lives only in this component's state,
 * so closing the dialog drops it for good.
 */
export function CreateKeyDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Remount on every open: nothing from the last key survives. */}
      {open && <CreateKeyContent onDone={() => onOpenChange(false)} />}
    </Dialog>
  )
}

function CreateKeyContent({ onDone }: { onDone: () => void }) {
  const create = useCreateKey()
  const [name, setName] = useState('')
  const [nameError, setNameError] = useState<string | null>(null)
  const [created, setCreated] = useState<CreatedApiKey | null>(null)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const problem = keyNameError(name)
    setNameError(problem)
    if (problem) return
    create.mutate(name, { onSuccess: setCreated })
  }

  if (created) return <NewKeyContent created={created} onDone={onDone} />

  // ApiError carries the API's detail, e.g. the tier's key limit on a 409.
  const apiError = create.error?.message ?? null

  // The key arrives only through this component: closing it mid-request would lose it.
  return (
    <DialogContent {...(create.isPending ? stayOpen : {})}>
      <form onSubmit={onSubmit} className="grid gap-4" noValidate>
        <DialogHeader>
          <DialogTitle>Create an API key</DialogTitle>
          <DialogDescription>Name it after where you will use it, e.g. "ci" or "laptop".</DialogDescription>
        </DialogHeader>
        {apiError && <ErrorAlert>{apiError}</ErrorAlert>}
        <Field id="key-name" label="Name" hint={nameError ?? `Up to ${MAX_KEY_NAME} characters.`}>
          <Input
            id="key-name"
            autoFocus
            autoComplete="off"
            value={name}
            aria-invalid={nameError !== null}
            aria-describedby="key-name-hint"
            onChange={(e) => {
              setName(e.target.value)
              setNameError(null)
            }}
          />
        </Field>
        <DialogFooter>
          <Button type="button" variant="outline" disabled={create.isPending} onClick={onDone}>
            Cancel
          </Button>
          <Button type="submit" disabled={create.isPending}>
            Create key
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  )
}

/** The new key, shown once. Only Done closes it: a stray click or Escape must not lose the key. */
export function NewKeyContent({ created, onDone }: { created: CreatedApiKey; onDone: () => void }) {
  return (
    <DialogContent {...stayOpen} className="sm:max-w-lg">
      <DialogHeader>
        <DialogTitle>Copy your API key</DialogTitle>
        <DialogDescription>
          Key <span className="font-medium text-foreground">{created.name}</span> is ready. Send it in the{' '}
          <code className="font-mono text-xs">X-API-Key</code> header.
        </DialogDescription>
      </DialogHeader>
      <Alert variant="warning">
        <TriangleAlert aria-hidden="true" />
        <AlertTitle>You will not see this key again</AlertTitle>
        <AlertDescription>Only a hash of it is stored. If you lose it, roll it to get a new one.</AlertDescription>
      </Alert>
      <div className="flex gap-2">
        <Input
          readOnly
          aria-label="Your new API key"
          value={created.key}
          className="font-mono text-xs"
          onFocus={(e) => e.currentTarget.select()}
        />
        <CopyButton value={created.key} />
      </div>
      <DialogFooter>
        <Button onClick={onDone}>Done</Button>
      </DialogFooter>
    </DialogContent>
  )
}
