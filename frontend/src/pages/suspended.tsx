import { ShieldAlert } from 'lucide-react'

import { useAuth } from '@/auth/use-auth'
import { AttributionFooter } from '@/components/attribution-footer'
import { Logo } from '@/components/logo'
import { Button } from '@/components/ui/button'

/** Shown when the API refuses the account: banned, deleted, or without a profile. */
export function SuspendedPage() {
  const { session, signOut } = useAuth()

  return (
    <div className="flex min-h-svh flex-col items-center px-4 pt-[18vh]">
      <Logo className="mb-8" />
      <div className="w-full max-w-sm rounded-lg border bg-card p-6">
        <ShieldAlert className="size-6 text-destructive" aria-hidden="true" />
        <h1 className="mt-3 text-lg font-semibold tracking-tight">Account suspended</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {session?.user.email ?? 'This account'} cannot use the API right now. Ask an admin to restore it.
        </p>
        <Button variant="outline" className="mt-6 w-full" onClick={signOut}>
          Sign out
        </Button>
      </div>
      <AttributionFooter className="mt-auto" />
    </div>
  )
}
