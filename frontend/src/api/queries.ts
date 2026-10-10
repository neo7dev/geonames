import { useQuery } from '@tanstack/react-query'

import { api, type Me } from '@/api/client'

export function useMe() {
  return useQuery({ queryKey: ['me'], queryFn: () => api<Me>('/portal/me') })
}
