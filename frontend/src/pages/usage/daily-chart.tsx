import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts'

import type { Usage } from '@/api/client'
import { ChartContainer, ChartTooltip, type ChartConfig } from '@/components/ui/chart'
import { formatDay, formatLongDay, formatNumber } from '@/lib/format'

const config = { requests: { label: 'Requests', color: 'var(--chart-1)' } } satisfies ChartConfig

type Payload = { payload: Usage['daily'][number] }

function DayTooltip({ active, payload }: { active?: boolean; payload?: Payload[] }) {
  if (!active || !payload?.length) return null
  const day = payload[0].payload
  return (
    <div className="rounded-md border bg-popover px-2.5 py-1.5 text-xs shadow-sm">
      <p className="font-semibold tabular-nums">{formatNumber(day.requests)} requests</p>
      <p className="text-muted-foreground">{formatLongDay(day.date)} (UTC)</p>
    </div>
  )
}

/** Requests per UTC day: one series, so the page title names it and there is no legend. */
export function DailyChart({ daily }: { daily: Usage['daily'] }) {
  return (
    <ChartContainer config={config} className="aspect-auto h-64 w-full" aria-label="Requests per day" role="img">
      <BarChart data={daily} barCategoryGap={2} margin={{ top: 8, right: 0, left: 0, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis
          dataKey="date"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          minTickGap={24}
          tickFormatter={formatDay}
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          width={48}
          allowDecimals={false}
          tickFormatter={(n: number) => formatNumber(n)}
        />
        <ChartTooltip cursor={{ fill: 'var(--muted)' }} content={<DayTooltip />} isAnimationActive={false} />
        <Bar
          dataKey="requests"
          fill="var(--color-requests)"
          maxBarSize={24}
          radius={[4, 4, 0, 0]}
          isAnimationActive={false}
        />
      </BarChart>
    </ChartContainer>
  )
}
