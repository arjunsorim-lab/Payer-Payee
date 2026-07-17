import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:4000',
      '/health': 'http://127.0.0.1:4000',
    },
  },
  build: {
    outDir: '../dist',
    emptyOutDir: true,
  },
})
