import { defineConfig, devices } from '@playwright/test';

// End-to-end tests run against the production build served by `vite preview`.
// The backend is not required: tests stub `/api/query` with scripted SSE.
const PORT = 4273;

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: true,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
  },
  webServer: {
    command: `npm run preview -- --port ${PORT} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
