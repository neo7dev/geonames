import { createClient, type SupabaseClient } from '@supabase/supabase-js'

import type { UiConfig } from '@/config'

let client: SupabaseClient | null = null

/** Created once, after /ui/config.json has loaded. */
export function initSupabase(config: UiConfig): SupabaseClient {
  client = createClient(config.supabase_url, config.anon_key, {
    auth: {
      // Implicit flow: email links log in even when opened in another browser.
      flowType: 'implicit',
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  })
  return client
}

export function supabase(): SupabaseClient {
  if (client === null) throw new Error('Supabase is not initialised')
  return client
}
