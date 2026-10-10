import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, type AdminUser, type AdminUserList, type RoleOption, type UserStatus } from '@/api/client'

export const PAGE_SIZE = 50

export type UserSearch = { q: string; status: UserStatus | null; offset: number }

export type UserChanges = { role?: string; is_active?: boolean; is_deleted?: boolean }

/** Admins only: the API answers 403 for everyone else, which reads as a suspended account. */
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

export function useRoles() {
  return useQuery({ queryKey: ['admin', 'roles'], queryFn: () => api<RoleOption[]>('/portal/admin/roles') })
}

function useRefreshUsers() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ['admin', 'users'] })
}

export function useCreateUser() {
  const refresh = useRefreshUsers()
  return useMutation({
    mutationFn: (user: { email: string; password: string; role: string }) =>
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
