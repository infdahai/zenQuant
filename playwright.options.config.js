import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './options-ui/e2e', timeout: 30000, workers: 1,
  use: { baseURL: 'http://127.0.0.1:8092', browserName: 'chromium',
    launchOptions: process.env.ZQ_BROWSER ? { executablePath: process.env.ZQ_BROWSER } : {},
    viewport: { width: 1440, height: 1100 },
  },
  webServer: { command: './rust/target/release/options-ui --port 8092', url: 'http://127.0.0.1:8092', reuseExistingServer: !process.env.CI, timeout: 15000 },
});
