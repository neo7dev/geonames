import { afterEach, describe, expect, it, vi } from 'vitest'

import { prefersColorScheme } from '@/test/media'
import { applyTheme, currentTheme } from '@/theme'

import script from '../public/theme-init.js?raw'

const root = document.documentElement

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('currentTheme', () => {
  it('follows the system until the user picks one', () => {
    prefersColorScheme('dark')
    expect(currentTheme()).toBe('dark')

    prefersColorScheme('light')
    expect(currentTheme()).toBe('light')
  })

  it('prefers the stored choice over the system', () => {
    prefersColorScheme('dark')
    localStorage.setItem('theme', 'light')

    expect(currentTheme()).toBe('light')
  })

  it('ignores a stored value it does not know', () => {
    prefersColorScheme('dark')
    localStorage.setItem('theme', 'purple')

    expect(currentTheme()).toBe('dark')
  })
})

describe('applyTheme', () => {
  it('flips the class and stores the choice', () => {
    applyTheme('dark')
    expect(root).toHaveClass('dark')
    expect(localStorage.getItem('theme')).toBe('dark')

    applyTheme('light')
    expect(root).not.toHaveClass('dark')
    expect(localStorage.getItem('theme')).toBe('light')
  })

  it('turns transitions off for the frame the theme flips in', () => {
    let nextFrame: FrameRequestCallback = () => {}
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      nextFrame = cb
      return 1
    })

    applyTheme('dark')
    expect(root).toHaveClass('no-transitions')

    nextFrame(0)
    expect(root).not.toHaveClass('no-transitions')
    expect(root).toHaveClass('dark')
  })
})

describe('theme-init.js', () => {
  // The pre-paint script must apply the same rules as currentTheme().
  const run = () => new Function(script)()

  it.each([
    ['dark', null, true],
    ['light', null, false],
    ['light', 'dark', true],
    ['dark', 'light', false],
  ] as const)('system %s, stored %s: dark is %s', (system, stored, dark) => {
    prefersColorScheme(system)
    if (stored) localStorage.setItem('theme', stored)

    run()

    expect(root.classList.contains('dark')).toBe(dark)
  })
})
