import { defineConfig } from '@playwright/test';
const port = process.env.ZQ_OPTIONS_PORT || '8092';
export default defineConfig({
  testDir: './options-ui/e2e', testMatch: ['payoff.spec.js', 'risk.spec.js'], timeout: 30000, workers: 1,
  use: { baseURL: `http://127.0.0.1:${port}`, browserName: 'chromium',
    launchOptions: process.env.ZQ_BROWSER ? { executablePath: process.env.ZQ_BROWSER } : {},
    viewport: { width: 1440, height: 1100 },
  },
  webServer: { command: process.env.ZQ_OPTIONS_SERVER || `./bin/zenquant serve --engineering --port ${port}`, url: `http://127.0.0.1:${port}`, reuseExistingServer: false, timeout: 15000 },
});
