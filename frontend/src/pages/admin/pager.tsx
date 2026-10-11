import { PAGE_SIZE } from '@/api/admin'
import { Button } from '@/components/ui/button'
import { formatNumber } from '@/lib/format'

/** Which rows of how many are shown, with Previous and Next. */
export function Pager({
  offset,
  shown,
  total,
  onOffset,
}: {
  offset: number
  shown: number
  total: number
  onOffset: (offset: number) => void
}) {
  return (
    <div className="mt-3 flex items-center justify-between gap-2 text-sm text-muted-foreground">
      <p>
        {formatNumber(offset + 1)}–{formatNumber(offset + shown)} of {formatNumber(total)}
      </p>
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={offset === 0}
          onClick={() => onOffset(Math.max(0, offset - PAGE_SIZE))}
        >
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={offset + PAGE_SIZE >= total}
          onClick={() => onOffset(offset + PAGE_SIZE)}
        >
          Next
        </Button>
      </div>
    </div>
  )
}
