import { useRouteError } from 'react-router'

import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'

/**
 * Shown instead of a page that failed to load or render. Most often the page's
 * code was replaced by a deploy since the tab opened, and a reload fixes it.
 */
export function RouteError() {
  const error = useRouteError()
  console.error(error)
  return (
    <>
      <PageHeader
        title="This page could not load"
        description="The app may have been updated since you opened it. Reloading usually fixes this."
      />
      <Button onClick={() => window.location.reload()}>Reload</Button>
    </>
  )
}
