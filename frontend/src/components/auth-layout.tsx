import type { ReactNode } from 'react'
import { Outlet } from 'react-router'

import { AttributionFooter } from '@/components/attribution-footer'
import { Logo } from '@/components/logo'
import { ThemeToggle } from '@/components/theme-toggle'

/** Centered card for the login, signup and password pages. */
export function AuthLayout() {
  return (
    <div className="flex min-h-svh flex-col">
      <header className="flex items-center justify-between px-4 py-3">
        <Logo />
        <ThemeToggle />
      </header>
      <main className="flex flex-1 items-start justify-center px-4 pt-[12vh] pb-12">
        <div className="w-full max-w-sm">
          <Outlet />
        </div>
      </main>
      <AttributionFooter />
    </div>
  )
}

export function AuthCard({
  title,
  description,
  children,
  footer,
}: {
  title: string
  description?: ReactNode
  children: ReactNode
  footer?: ReactNode
}) {
  return (
    <>
      <div className="rounded-lg border bg-card p-6">
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
        <div className="mt-6">{children}</div>
      </div>
      {footer && <p className="mt-4 text-center text-sm text-muted-foreground">{footer}</p>}
    </>
  )
}
