import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  api,
  type AccountEventKind,
  type AccountEventList,
  type AdminUser,
  type AdminUserList,
  type RoleOption,
  type UserStatus,
} from '@/api/client'

export const PAGE_SIZE = 50

export type UserSearch = { q: string; status: UserStatus | null; offset: number }

export type UserChanges = { display_name?: string | null; role?: string; is_active?: boolean; is_deleted?: boolean }

/** Admins only: the API answers 403 for everyone else. */
export function useAdminUsers({ q, status, offset }: UserSearch) {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) })
  if (q.trim()) params.set('q', q.trim())
  if (status) params.set('status', status)
  return useQuery({
    queryKey: ['admin', 'users', q.trim(), status, offset],
    queryFn: () => api<AdminUserList>(`/portal/admin/users?${params}`),
    // Keep the current page on screen while the next one loads.
    placeholderData: keepPreviousData,
  })
}

export type EventSearch = { q: string; userId: string | null; events: AccountEventKind[]; offset: number }

/** The account log, newest first. Admins only. */
export function useAccountEvents({ q, userId, events, offset }: EventSearch) {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) })
  if (q.trim()) params.set('q', q.trim())
  if (userId) params.set('user_id', userId)
  for (const event of events) params.append('event', event)
  return useQuery({
    queryKey: ['admin', 'events', q.trim(), userId, events.join(','), offset],
    queryFn: () => api<AccountEventList>(`/portal/admin/events?${params}`),
    placeholderData: keepPreviousData,
  })
}

export function useRoles() {
  return useQuery({ queryKey: ['admin', 'roles'], queryFn: () => api<RoleOption[]>('/portal/admin/roles') })
}

function useRefreshUsers() {
  const queryClient = useQueryClient()
  // Every change to a user also adds to the account log.
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] }),
      queryClient.invalidateQueries({ queryKey: ['admin', 'events'] }),
    ])
}

export function useCreateUser() {
  const refresh = useRefreshUsers()
  return useMutation({
    mutationFn: (user: { email: string; password: string; role: string; display_name: string | null }) =>
      api<AdminUser>('/portal/admin/users', { method: 'POST', body: JSON.stringify(user) }),
    onSuccess: refresh,
  })
}

export function useUpdateUser() {
  const refresh = useRefreshUsers()
  return useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: UserChanges }) =>
      api<AdminUser>(`/portal/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(changes) }),
    onSettled: refresh,
  })
}

/** Soft-delete a user, or remove an already deleted one for good. */
export function useDeleteUser() {
  const refresh = useRefreshUsers()
  return useMutation({
    mutationFn: ({ id, permanent }: { id: string; permanent: boolean }) =>
      api<void>(`/portal/admin/users/${id}${permanent ? '?permanent=true' : ''}`, { method: 'DELETE' }),
    onSettled: refresh,
  })
}

/** "dev (pro tier)": the role, and the plan it puts the user on. */
export const roleLabel = (role: { name: string; tier: string }) => `${role.name} (${role.tier} tier)`
