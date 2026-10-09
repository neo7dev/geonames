import { BookOpen, ChartColumn, KeyRound, LayoutDashboard, Menu, X } from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet } from 'react-router'

import { Logo } from '@/components/logo'
import { ThemeToggle } from '@/components/theme-toggle'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const NAV = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/keys', label: 'API keys', icon: KeyRound, end: false },
  { to: '/usage', label: 'Usage', icon: ChartColumn, end: false },
]

function Sidebar({ onNavigate, logo = true }: { onNavigate?: () => void; logo?: boolean }) {
  return (
    <div className="flex h-full flex-col gap-4 p-3">
      {logo && <Logo className="px-2 pt-1 pb-2" />}
      <nav aria-label="Main" className="flex flex-col gap-0.5">
        {NAV.map(({ to, label, icon: Icon, end }) => (
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
      <div className="mt-auto flex items-center justify-between gap-2">
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

      <main className="mx-auto w-full max-w-[1100px] px-4 py-8 md:px-8">
        <Outlet />
      </main>
    </div>
  )
}
