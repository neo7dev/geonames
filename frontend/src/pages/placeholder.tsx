import { PageHeader } from '@/components/page-header'

/** Stands in for pages built in later increments. */
export function Placeholder({ title }: { title: string }) {
  return (
    <>
      <PageHeader title={title} description="Coming soon." />
      <div className="rounded-lg border p-8 text-sm text-muted-foreground">Nothing here yet.</div>
    </>
  )
}
