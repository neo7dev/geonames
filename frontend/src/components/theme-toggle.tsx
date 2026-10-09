import { Moon, Sun } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { applyTheme, currentTheme, type Theme } from '@/theme'

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(currentTheme)
  const next: Theme = theme === 'dark' ? 'light' : 'dark'

  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`Switch to ${next} mode`}
      title={`Switch to ${next} mode`}
      onClick={() => {
        applyTheme(next)
        setTheme(next)
      }}
    >
      {theme === 'dark' ? <Sun /> : <Moon />}
    </Button>
  )
}
