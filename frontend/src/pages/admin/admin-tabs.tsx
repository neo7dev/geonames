import { NavLink } from 'react-router'

import { cn } from '@/lib/utils'

const TABS = [
  { to: '/admin', label: 'Users', end: true },
  { to: '/admin/activity', label: 'Activity', end: false },
]

/** Switches between the Administration page's users and its account log. */
export function AdminTabs() {
  return (
    <nav aria-label="Administration" className="mb-4 flex gap-1 border-b">
      {TABS.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end={tab.end}
          className={({ isActive }) =>
            cn(
              '-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors',
              isActive
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )
          }
        >
          {tab.label}
        </NavLink>
      ))}
    </nav>
  )
}
