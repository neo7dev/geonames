import { vi } from 'vitest'

/** jsdom has no matchMedia; pretend the system prefers `scheme`. */
export function prefersColorScheme(scheme: 'light' | 'dark') {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: query === `(prefers-color-scheme: ${scheme})`,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  )
}
