import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, type Me } from '@/api/client'

export const MAX_DISPLAY_NAME = 100

export function useMe() {
  return useQuery({ queryKey: ['me'], queryFn: () => api<Me>('/portal/me') })
}

/** Change your display name; an empty one clears it. */
export function useUpdateMe() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (displayName: string) =>
      api<Me>('/portal/me', { method: 'PATCH', body: JSON.stringify({ display_name: displayName.trim() || null }) }),
    onSuccess: (me) => queryClient.setQueryData(['me'], me),
  })
}

/** The API's rule for names: up to 100 characters after trimming, no control characters. */
export function displayNameError(name: string): string | null {
  const trimmed = name.trim()
  if (trimmed.length > MAX_DISPLAY_NAME) return `Use at most ${MAX_DISPLAY_NAME} characters.`
  // eslint-disable-next-line no-control-regex
  if (/[\x00-\x1f\x7f]/.test(trimmed)) return 'Remove the control characters.'
  return null
}
