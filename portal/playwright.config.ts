import { defineConfig, devices } from '@playwright/test'

// Locally: uses the installed Chrome. In CI: `npx playwright install chromium` and PW_CHANNEL unset.
const channel = process.env.CI ? undefined : 'chrome'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  use: { baseURL: 'http://localhost:4173', channel },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], channel } },
    { name: 'phone', use: { ...devices['Pixel 7'], channel } },
  ],
  webServer: { command: 'npm run build && npx vite preview --port 4173 --strictPort', url: 'http://localhost:4173', reuseExistingServer: true },
})
