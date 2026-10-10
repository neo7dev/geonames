import { useState, type FormEvent } from 'react'
import { toast } from 'sonner'

import { roleLabel, useRoles, useUpdateUser, type UserChanges } from '@/api/admin'
import type { AdminUser } from '@/api/client'
import { displayNameError } from '@/api/queries'
import { ErrorAlert, Field } from '@/components/form'
import { NativeSelect } from '@/components/native-select'
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
import { Label } from '@/components/ui/label'

/** Change a user's plan (their role) and whether their account is active. */
export function EditUserDialog({
  user,
  isSelf,
  onClose,
}: {
  user: AdminUser | null
  isSelf: boolean
  onClose: () => void
}) {
  return (
    <Dialog open={user !== null} onOpenChange={(open) => !open && onClose()}>
      {user && <EditUserContent key={user.id} user={user} isSelf={isSelf} onDone={onClose} />}
    </Dialog>
  )
}

function EditUserContent({ user, isSelf, onDone }: { user: AdminUser; isSelf: boolean; onDone: () => void }) {
  const update = useUpdateUser()
  const roles = useRoles()
  const [name, setName] = useState(user.display_name ?? '')
  const [nameProblem, setNameProblem] = useState<string | null>(null)
  const [role, setRole] = useState(user.role)
  const [active, setActive] = useState(user.is_active)

  const changes: UserChanges = {}
  if (name.trim() !== (user.display_name ?? '')) changes.display_name = name.trim() || null
  if (role !== user.role) changes.role = role
  if (active !== user.is_active) changes.is_active = active
  const changed = Object.keys(changes).length > 0

  // Admins cannot lock themselves out; the API refuses it too.
  const choices = roles.data?.filter((r) => !isSelf || r.is_admin)

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!changed) return onDone()
    const problem = displayNameError(name)
    setNameProblem(problem)
    if (problem) return
    update.mutate(
      { id: user.id, changes },
      {
        onSuccess: (saved) => {
          toast.success(`Saved ${saved.display_name ?? saved.email}`)
          onDone()
        },
      },
    )
  }

  const error = update.error?.message ?? roles.error?.message ?? null

  return (
    <DialogContent className="sm:max-w-md">
      <form onSubmit={onSubmit} className="grid gap-4" noValidate>
        <DialogHeader>
          <DialogTitle>Edit user</DialogTitle>
          <DialogDescription className="truncate" title={user.email ?? undefined}>
            {user.email ?? user.id}
          </DialogDescription>
        </DialogHeader>
        {error && <ErrorAlert>{error}</ErrorAlert>}
        {user.is_deleted && (
          <p className="rounded-lg border px-3 py-2 text-muted-foreground">
            This account is deleted. Restore it from the list to let them back in.
          </p>
        )}
        <Field id="edit-user-name" label="Name" hint={nameProblem ?? 'Empty shows their email instead.'}>
          <Input
            id="edit-user-name"
            autoComplete="off"
            value={name}
            aria-invalid={nameProblem !== null}
            aria-describedby="edit-user-name-hint"
            onChange={(e) => {
              setName(e.target.value)
              setNameProblem(null)
            }}
          />
        </Field>
        <Field
          id="edit-user-role"
          label="Plan"
          hint={isSelf ? 'You can only switch yourself between admin roles.' : 'Limits change at once.'}
        >
          <NativeSelect
            id="edit-user-role"
            aria-describedby="edit-user-role-hint"
            value={role}
            disabled={!choices}
            onChange={(e) => setRole(e.target.value)}
          >
            {/* The current role, even before the list arrives */}
            {!choices && <option value={user.role}>{roleLabel({ name: user.role, tier: user.tier })}</option>}
            {choices?.map((r) => (
              <option key={r.name} value={r.name}>
                {roleLabel(r)}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <div className="flex items-start gap-3 rounded-lg border p-3">
          <input
            id="edit-user-active"
            type="checkbox"
            className="mt-0.5 size-4 accent-primary"
            checked={active}
            disabled={isSelf}
            aria-describedby="edit-user-active-hint"
            onChange={(e) => setActive(e.target.checked)}
          />
          <div className="grid gap-1">
            <Label htmlFor="edit-user-active">Active</Label>
            <p id="edit-user-active-hint" className="text-xs text-muted-foreground">
              {isSelf
                ? 'You cannot deactivate your own account.'
                : 'Inactive users cannot sign in to the portal, and their API keys stop working.'}
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" disabled={update.isPending} onClick={onDone}>
            Cancel
          </Button>
          <Button type="submit" disabled={update.isPending || !changed}>
            Save changes
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  )
}
