/** Served by the API at /ui/config.json, so one build fits every deployment. */
export type UiConfig = {
  supabase_url: string
  anon_key: string
  password_min_length: number
}

export async function loadConfig(): Promise<UiConfig> {
  // The API's route, not a file in the build; Vite's dev server proxies it.
  const response = await fetch('/ui/config.json', {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail ?? `Could not load the UI config (${response.status})`)
  }
  return response.json()
}
