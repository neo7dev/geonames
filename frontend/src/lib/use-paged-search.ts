import { useState } from 'react'

/** The offset of a paged list, back to 0 whenever `searchKey` (the search and filters) changes. */
export function usePagedSearch(searchKey: string): [number, (offset: number) => void] {
  const [page, setPage] = useState({ searchKey, offset: 0 })
  const offset = page.searchKey === searchKey ? page.offset : 0
  return [offset, (next: number) => setPage({ searchKey, offset: next })]
}
