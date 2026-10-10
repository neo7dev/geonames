import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'

import { setUiConfig } from '@/config'
import { resetFakeSupabase } from '@/test/fake-supabase'

// Every test talks to a fake Supabase (src/test/fake-supabase.ts), never the network.
vi.mock('@/lib/supabase', async () => (await import('@/test/fake-supabase')).supabaseModule)

beforeEach(() => {
  resetFakeSupabase()
  setUiConfig({ supabase_url: 'http://supabase.test', anon_key: 'anon-key', password_min_length: 8 })
})

afterEach(() => {
  cleanup()
  // Also after a test that failed before switching back
  vi.useRealTimers()
  vi.unstubAllGlobals()
  localStorage.clear()
  document.documentElement.className = ''
})
