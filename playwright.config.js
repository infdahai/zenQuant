import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests_e2e",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:8091",
    trace: "on-first-retry",
  },
  webServer: {
    command: "uv run zenquant-web --host 127.0.0.1 --port 8091 --mode demo",
    url: "http://127.0.0.1:8091/healthz",
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
