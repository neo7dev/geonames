import type { CSSProperties } from 'react'
import { Toaster as Sonner, type ToasterProps } from 'sonner'

import { useTheme } from '@/theme'

/** How long a toast stays up, unless the pointer is on it. */
export const TOAST_DURATION_MS = 2000

/** Sonner's toasts, in the app's theme and colors. */
export function Toaster(props: ToasterProps) {
  const theme = useTheme()
  return (
    <Sonner
      theme={theme}
      position="bottom-right"
      duration={TOAST_DURATION_MS}
      className="toaster group"
      style={
        {
          '--normal-bg': 'var(--popover)',
          '--normal-text': 'var(--popover-foreground)',
          '--normal-border': 'var(--border)',
          '--border-radius': 'var(--radius)',
        } as CSSProperties
      }
      {...props}
    />
  )
}
