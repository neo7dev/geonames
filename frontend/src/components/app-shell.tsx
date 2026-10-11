import { BookOpen, ChartColumn, KeyRound, LayoutDashboard, LogOut, Menu, ShieldUser, X } from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet } from 'react-router'

import { useMe } from '@/api/queries'
import { useAuth } from '@/auth/use-auth'
import { AttributionFooter } from '@/components/attribution-footer'
import { Logo } from '@/components/logo'
import { ThemeToggle } from '@/components/theme-toggle'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const NAV = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/keys', label: 'API keys', icon: KeyRound, end: false },
  { to: '/usage', label: 'Usage', icon: ChartColumn, end: false },
]

// Shown to admins only
const ADMIN_NAV = { to: '/admin', label: 'Administration', icon: ShieldUser, end: false }

function Sidebar({ onNavigate, logo = true }: { onNavigate?: () => void; logo?: boolean }) {
  const me = useMe()
  const nav = me.data?.is_admin ? [...NAV, ADMIN_NAV] : NAV

  return (
    <div className="flex h-full min-w-0 flex-col gap-4 p-3">
      {logo && <Logo className="px-2 pt-1 pb-2" />}
      <nav aria-label="Main" className="flex flex-col gap-0.5">
        {nav.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-sidebar-accent hover:text-foreground',
                isActive && 'bg-sidebar-accent font-medium text-foreground [&>svg]:text-primary',
              )
            }
          >
            <Icon className="size-4" aria-hidden="true" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto flex min-w-0 flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <a
            href="/docs"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-sidebar-accent hover:text-foreground"
          >
            <BookOpen className="size-4" aria-hidden="true" />
            API docs
          </a>
          <ThemeToggle />
        </div>
        <Account onNavigate={onNavigate} />
      </div>
    </div>
  )
}

function Account({ onNavigate }: { onNavigate?: () => void }) {
  const { session, signOut } = useAuth()
  const me = useMe()
  const email = session?.user.email
  const name = me.data?.display_name

  return (
    <div className="flex min-w-0 items-center justify-between gap-1 border-t pt-3">
      {/* The name opens the profile page. */}
      <NavLink
        to="/profile"
        onClick={onNavigate}
        className={({ isActive }) =>
          cn(
            'min-w-0 flex-1 rounded-md px-2 py-1 text-sm hover:bg-sidebar-accent',
            isActive && 'bg-sidebar-accent',
          )
        }
      >
        <p className="truncate font-medium" title={name ? `${name} (${email})` : email}>
          <span className="sr-only">Profile: </span>
          {name ?? email}
        </p>
        <p className="truncate text-xs text-muted-foreground">
          {me.data ? (name ? email : `${me.data.tier} tier`) : '\u00a0'}
        </p>
      </NavLink>
      <Button
        variant="ghost"
        size="icon"
        aria-label="Sign out"
        title="Sign out"
        onClick={signOut}
      >
        <LogOut />
      </Button>
    </div>
  )
}

export function AppShell() {
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <div className="min-h-svh md:grid md:grid-cols-[15rem_1fr]">
      <aside className="hidden border-r bg-sidebar md:sticky md:top-0 md:block md:h-svh">
        <Sidebar />
      </aside>

      {/* Below md the sidebar opens over the page from a menu button. */}
      <header className="flex items-center justify-between border-b px-4 py-2 md:hidden">
        <Logo />
        <Button
          variant="ghost"
          size="icon"
          aria-label={menuOpen ? 'Close menu' : 'Open menu'}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        >
          {menuOpen ? <X /> : <Menu />}
        </Button>
      </header>
      {menuOpen && (
        <div className="fixed inset-x-0 top-[49px] bottom-0 z-40 border-t bg-sidebar md:hidden">
          {/* The header above already shows the logo. */}
          <Sidebar logo={false} onNavigate={() => setMenuOpen(false)} />
        </div>
      )}

      {/* The footer sits at the bottom of the window on short pages, below the content on long ones. */}
      <div className="flex min-h-[calc(100svh-49px)] flex-col md:min-h-svh">
        <main className="mx-auto w-full max-w-[1100px] flex-1 px-4 py-8 md:px-8">
          <Outlet />
        </main>
        <AttributionFooter />
      </div>
    </div>
  )
}
