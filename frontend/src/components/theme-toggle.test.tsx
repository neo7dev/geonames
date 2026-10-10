import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'

import { ThemeToggle } from '@/components/theme-toggle'
import { prefersColorScheme } from '@/test/media'

afterEach(() => {
  vi.unstubAllGlobals()
})

it('switches between light and dark and says what it will do', async () => {
  prefersColorScheme('light')
  render(<ThemeToggle />)

  await userEvent.click(screen.getByRole('button', { name: 'Switch to dark mode' }))
  expect(document.documentElement).toHaveClass('dark')

  await userEvent.click(screen.getByRole('button', { name: 'Switch to light mode' }))
  expect(document.documentElement).not.toHaveClass('dark')
  expect(localStorage.getItem('theme')).toBe('light')
})

it('starts from the system theme', () => {
  prefersColorScheme('dark')
  render(<ThemeToggle />)

  expect(screen.getByRole('button', { name: 'Switch to light mode' })).toBeInTheDocument()
})

it('keeps every toggle in step, e.g. the menu one and the sidebar one', async () => {
  prefersColorScheme('light')
  render(
    <>
      <ThemeToggle />
      <ThemeToggle />
    </>,
  )

  await userEvent.click(screen.getAllByRole('button', { name: 'Switch to dark mode' })[0])

  expect(screen.getAllByRole('button', { name: 'Switch to light mode' })).toHaveLength(2)
})
