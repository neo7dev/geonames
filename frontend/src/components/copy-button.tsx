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

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setFailed(false)
      setCopied(true)
    } catch {
      // No clipboard access (plain http on another host): the value stays selectable.
      setFailed(true)
    }
  }

  return (
    <Button variant="outline" onClick={copy} {...props}>
      {copied ? <Check className="text-primary" aria-hidden="true" /> : <Copy aria-hidden="true" />}
      {copied ? 'Copied' : failed ? 'Copy failed: select and copy' : label}
    </Button>
  )
}
