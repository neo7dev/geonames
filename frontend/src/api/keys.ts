import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, type ApiKey, type CreatedApiKey } from '@/api/client'

export const MAX_KEY_NAME = 64

/** The API's rule for key names: 1-64 characters after trimming, no control characters. */
export function keyNameError(name: string): string | null {
  const trimmed = name.trim()
  if (!trimmed) return 'Enter a name.'
  if (trimmed.length > MAX_KEY_NAME) return `Use at most ${MAX_KEY_NAME} characters.`
  // eslint-disable-next-line no-control-regex
  if (/[\x00-\x1f\x7f]/.test(trimmed)) return 'Remove the control characters.'
  return null
}

export function useKeys() {
  return useQuery({ queryKey: ['keys'], queryFn: () => api<ApiKey[]>('/portal/keys') })
}

function useRefreshKeys() {
  const queryClient = useQueryClient()
  // The profile carries the active key count, so it changes too.
  return () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['keys'] }),
    queryClient.invalidateQueries({ queryKey: ['me'] }),
  ])
}

export function useCreateKey() {
  const refresh = useRefreshKeys()
  return useMutation({
    mutationFn: (name: string) =>
      api<CreatedApiKey>('/portal/keys', { method: 'POST', body: JSON.stringify({ name: name.trim() }) }),
    onSuccess: refresh,
  })
}

export function useRevokeKey() {
  const refresh = useRefreshKeys()
  return useMutation({
    mutationFn: (id: string) => api<void>(`/portal/keys/${id}`, { method: 'DELETE' }),
    onSettled: refresh,
  })
}
