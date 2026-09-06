/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dev server proxies API calls to a locally running FastAPI instance so
// the browser always talks to the same origin (mirrors the Nginx deployment).
const backend = 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': backend,
      '/health': backend,
      '/ready': backend,
      '/debug': backend,
    },
  },
  build: {
    // Carbon + d3 produce a large bundle; this is an internal workbench, so a
    // single large chunk is acceptable and keeps the build simple.
    chunkSizeWarningLimit: 1600,
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: false,
    restoreMocks: true,
    exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
  },
});
