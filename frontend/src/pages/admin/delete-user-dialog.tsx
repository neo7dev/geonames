import { useDeleteUser } from '@/api/admin'
import type { AdminUser } from '@/api/client'
import { ErrorAlert } from '@/components/form'
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'

/** Soft-delete a user; a user already deleted is removed for good instead. */
export function DeleteUserDialog({ user, onClose }: { user: AdminUser | null; onClose: () => void }) {
  const remove = useDeleteUser()
  const permanent = user?.is_deleted ?? false
  const name = user?.email ?? user?.id

  return (
    <AlertDialog
      open={user !== null}
      onOpenChange={(open) => {
        if (!open) {
          remove.reset()
          onClose()
        }
      }}
    >
      {user && (
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{permanent ? `Permanently delete ${name}?` : `Delete ${name}?`}</AlertDialogTitle>
            <AlertDialogDescription>
              {permanent
                ? 'The account, its API keys and its usage history are removed from Supabase Auth and the database. This cannot be undone.'
                : 'They can no longer sign in, and their API keys stop working at once. You can restore the account later.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {remove.error && <ErrorAlert>{remove.error.message}</ErrorAlert>}
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            {/* Not AlertDialogAction: that closes before the request is done. */}
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => remove.mutate({ id: user.id, permanent }, { onSuccess: onClose })}
            >
              {permanent ? 'Delete permanently' : 'Delete user'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      )}
    </AlertDialog>
  )
}
