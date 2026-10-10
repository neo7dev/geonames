import { expect, it } from 'vitest'

import { safeNext } from '@/lib/next'

it.each([
  ['/keys', '/keys'],
  ['/usage?days=7', '/usage?days=7'],
  ['/keys#new', '/keys#new'],
])('keeps the app path %s', (value, expected) => {
  expect(safeNext(value)).toBe(expected)
})

it.each([
  [null],
  [''],
  ['keys'],
  ['//evil.example/keys'],
  ['/\\evil.example'],
  ['https://evil.example/'],
  ['javascript:alert(1)'],
])('falls back for %s, which could leave the app', (value) => {
  expect(safeNext(value)).toBe('/')
})
