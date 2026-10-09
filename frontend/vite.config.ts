/// <reference types="vitest/config" />
import path from 'node:path'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API (uvicorn on 9500) serves the built app at /ui. In dev, Vite serves
// the pages with hot reload and passes everything else through to the API.
const api = process.env.API_URL ?? 'http://localhost:9500'
const proxied = ['/ui/config.json', '/portal', '/docs', '/openapi.json', '/health']

export default defineConfig({
  base: '/ui/',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, 'src') },
  },
  server: {
    // Inside the devcontainer: listen on all interfaces so the host reaches it.
    host: true,
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(proxied.map((p) => [p, api])),
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    restoreMocks: true,
  },
})
