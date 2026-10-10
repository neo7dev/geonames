import type { ApiKey } from '@/api/client'
import { useRevokeKey } from '@/api/keys'
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

export function RevokeKeyDialog({ apiKey, onClose }: { apiKey: ApiKey | null; onClose: () => void }) {
  const revoke = useRevokeKey()

  return (
    <AlertDialog
      open={apiKey !== null}
      onOpenChange={(open) => {
        if (!open) {
          revoke.reset()
          onClose()
        }
      }}
    >
      {apiKey && (
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke "{apiKey.name}"?</AlertDialogTitle>
            <AlertDialogDescription>
              Requests with <code className="font-mono text-xs">{apiKey.prefix}…</code> stop working at once. This
              cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {revoke.error && <ErrorAlert>{revoke.error.message}</ErrorAlert>}
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            {/* Not AlertDialogAction: that closes before the request is done. */}
            <Button
              variant="destructive"
              disabled={revoke.isPending}
              onClick={() => revoke.mutate(apiKey.id, { onSuccess: onClose })}
            >
              Revoke key
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      )}
    </AlertDialog>
  )
}
