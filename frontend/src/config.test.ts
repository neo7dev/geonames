import { afterEach, expect, it, vi } from 'vitest'

import { loadConfig } from '@/config'

afterEach(() => {
  vi.unstubAllGlobals()
})

it('loads the config from the API', async () => {
  const config = { supabase_url: 'http://localhost:8000', anon_key: 'anon', password_min_length: 8 }
  const fetch = vi.fn(async () => new Response(JSON.stringify(config)))
  vi.stubGlobal('fetch', fetch)

  expect(await loadConfig()).toEqual(config)
  expect(fetch).toHaveBeenCalledWith('/ui/config.json', expect.anything())
})

it("fails with the API's detail when the UI is not configured", async () => {
  const detail = 'UI is not configured: set SUPABASE_PUBLIC_URL and ANON_KEY'
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail }), { status: 503 })))

  await expect(loadConfig()).rejects.toThrow(detail)
})
