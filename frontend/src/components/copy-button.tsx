import { Check, Copy } from 'lucide-react'
import { useEffect, useState, type ComponentProps } from 'react'

import { Button } from '@/components/ui/button'

/** Copies `value`; says "Copied" for two seconds after. */
export function CopyButton({
  value,
  label = 'Copy',
  ...props
}: { value: string; label?: string } & Omit<ComponentProps<typeof Button>, 'onClick' | 'value'>) {
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(false), 2000)
    return () => clearTimeout(timer)
  }, [copied])

  const copy = async (button: HTMLButtonElement) => {
    let ok: boolean
    try {
      // Missing on plain http other than localhost; the browser may also refuse it.
      await navigator.clipboard.writeText(value)
      ok = true
    } catch {
      ok = copyWithSelection(value, button)
    }
    setFailed(!ok)
    setCopied(ok)
  }

  return (
    <Button variant="outline" onClick={(e) => void copy(e.currentTarget)} {...props}>
      {copied ? <Check className="text-primary" aria-hidden="true" /> : <Copy aria-hidden="true" />}
      {copied ? 'Copied' : failed ? 'Copy failed: select and copy' : label}
    </Button>
  )
}

/**
 * The older way: select `value` in a hidden textarea and copy the selection.
 * The textarea goes next to the button, so a dialog's focus trap lets it be selected.
 */
function copyWithSelection(value: string, button: HTMLButtonElement): boolean {
  const area = document.createElement('textarea')
  area.value = value
  area.readOnly = true
  area.setAttribute('aria-hidden', 'true')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  area.style.pointerEvents = 'none'
  ;(button.parentElement ?? document.body).appendChild(area)
  area.focus()
  area.select()
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    area.remove()
    button.focus()
  }
}
