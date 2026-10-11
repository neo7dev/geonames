import { cn } from '@/lib/utils'

const link = 'underline underline-offset-4 hover:text-foreground'

/** GeoNames' CC BY 4.0 licence asks that every use of the data credits it: on every page. */
export function AttributionFooter({ className }: { className?: string }) {
  return (
    <footer className={cn('px-4 py-4 text-center text-xs text-muted-foreground', className)}>
      Place data from{' '}
      <a href="https://www.geonames.org" target="_blank" rel="noreferrer" className={link}>
        GeoNames
      </a>
      , licensed under{' '}
      <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noreferrer" className={link}>
        CC BY 4.0
      </a>
      .
    </footer>
  )
}
