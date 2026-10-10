import { useState, type FormEvent } from 'react'

import { roleLabel, useCreateUser, useRoles } from '@/api/admin'
import { ErrorAlert, Field } from '@/components/form'
import { NativeSelect } from '@/components/native-select'
import { PasswordInput } from '@/components/password-input'
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
import { uiConfig } from '@/config'

export function CreateUserDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Remount on every open: the form starts empty. */}
      {open && <CreateUserContent onDone={() => onOpenChange(false)} />}
    </Dialog>
  )
}

function CreateUserContent({ onDone }: { onDone: () => void }) {
  const create = useCreateUser()
  const roles = useRoles()
  const minLength = uiConfig().password_min_length
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  // New users get the default role unless the admin picks another.
  const chosenRole = role ?? roles.data?.find((r) => r.is_default)?.name ?? ''

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const trimmed = email.trim()
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(trimmed)) return setProblem('Enter an email address.')
    if (password.length < minLength) return setProblem(`Use a password of at least ${minLength} characters.`)
    setProblem(null)
    create.mutate({ email: trimmed, password, role: chosenRole }, { onSuccess: onDone })
  }

  const error = problem ?? create.error?.message ?? roles.error?.message ?? null

  return (
    <DialogContent className="sm:max-w-md">
      <form onSubmit={onSubmit} className="grid gap-4" noValidate>
        <DialogHeader>
          <DialogTitle>Create a user</DialogTitle>
          <DialogDescription>
            The account is confirmed at once: they can sign in with this password. Share it with them safely.
          </DialogDescription>
        </DialogHeader>
        {error && <ErrorAlert>{error}</ErrorAlert>}
        <Field id="new-user-email" label="Email">
          <Input
            id="new-user-email"
            type="email"
            autoFocus
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Field id="new-user-password" label="Password" hint={`At least ${minLength} characters.`}>
          <PasswordInput
            id="new-user-password"
            autoComplete="new-password"
            aria-describedby="new-user-password-hint"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <Field id="new-user-role" label="Plan">
          <NativeSelect
            id="new-user-role"
            value={chosenRole}
            disabled={!roles.data}
            onChange={(e) => setRole(e.target.value)}
          >
            {roles.data?.map((r) => (
              <option key={r.name} value={r.name}>
                {roleLabel(r)}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <DialogFooter>
          <Button type="button" variant="outline" disabled={create.isPending} onClick={onDone}>
            Cancel
          </Button>
          <Button type="submit" disabled={create.isPending || !roles.data}>
            Create user
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  )
}
