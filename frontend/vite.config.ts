import path from 'node:path'
import { fileURLToPath } from 'node:url'

import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vitest/config'

const dirname = path.dirname(fileURLToPath(import.meta.url))

// https://vitejs.dev/config/
export default defineConfig({
  base: '/app/',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(dirname, './src'),
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
})
