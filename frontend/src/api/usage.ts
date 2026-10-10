import { keepPreviousData, useQuery } from '@tanstack/react-query'

import { api, type Usage } from '@/api/client'

export type UsageWindow = Usage['current']['minute']

/**
 * The current minute, hour and month, plus `days` of daily history. The live
 * counts change on every API call, so it refetches every 10 s while the tab is
 * visible; a new `days` keeps showing the old data until the new arrives.
 */
export function useUsage(days = 30) {
  return useQuery({
    queryKey: ['usage', days],
    queryFn: () => api<Usage>(`/portal/usage?days=${days}`),
    refetchInterval: 10_000,
    placeholderData: keepPreviousData,
  })
}
