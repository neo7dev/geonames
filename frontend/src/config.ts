/** Served by the API at /ui/config.json, so one build fits every deployment. */
export type UiConfig = {
  supabase_url: string
  anon_key: string
  password_min_length: number
}

let current: UiConfig | null = null

export async function loadConfig(): Promise<UiConfig> {
  // The API's route, not a file in the build; Vite's dev server proxies it.
  const response = await fetch('/ui/config.json', {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail ?? `Could not load the UI config (${response.status})`)
  }
  current = await response.json()
  return current!
}

/** The config loaded at startup (main.tsx renders nothing before it). */
export function uiConfig(): UiConfig {
  if (current === null) throw new Error('The UI config is not loaded')
  return current
}

/** For tests. */
export function setUiConfig(config: UiConfig) {
  current = config
}
