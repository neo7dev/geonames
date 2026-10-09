import { Link } from 'react-router'

import { PageHeader } from '@/components/page-header'

export function NotFound() {
  return (
    <>
      <PageHeader title="Page not found" description="This page does not exist." />
      <Link to="/" className="text-sm text-primary underline-offset-4 hover:underline">
        Back to the overview
      </Link>
    </>
  )
}
