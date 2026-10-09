import { cn } from '@/lib/utils'

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn('flex items-center gap-2 font-semibold', className)}>
      <svg
        viewBox="0 0 32 32"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        className="size-5 text-primary"
        aria-hidden="true"
      >
        <circle cx="16" cy="16" r="13" />
        <ellipse cx="16" cy="16" rx="5.5" ry="13" />
        <path d="M3 16h26" />
      </svg>
      GeoNames
    </span>
  )
}
