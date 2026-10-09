import './index.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from '@/App'
import { loadConfig } from '@/config'
import { initSupabase } from '@/lib/supabase'

const root = createRoot(document.getElementById('root')!)

loadConfig()
  .then((config) => {
    initSupabase(config)
    root.render(
      <StrictMode>
        <App />
      </StrictMode>,
    )
  })
  .catch((error: Error) => {
    root.render(
      <div className="mx-auto max-w-md px-4 py-24 text-sm">
        <h1 className="mb-2 text-base font-semibold">The app could not start</h1>
        <p className="text-muted-foreground">{error.message}</p>
      </div>,
    )
  })
