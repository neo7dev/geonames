import { Moon, Sun } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { applyTheme, useTheme, type Theme } from '@/theme'

export function ThemeToggle() {
  const theme = useTheme()
  const next: Theme = theme === 'dark' ? 'light' : 'dark'

  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`Switch to ${next} mode`}
      title={`Switch to ${next} mode`}
      onClick={() => applyTheme(next)}
    >
      {theme === 'dark' ? <Sun /> : <Moon />}
    </Button>
  )
}
