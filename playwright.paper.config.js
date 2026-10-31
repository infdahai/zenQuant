import { defineConfig } from '@playwright/test';
const port = process.env.ZQ_PAPER_PORT || '8093';
export default defineConfig({
  testDir:'./options-ui/e2e',testMatch:'paper.spec.js',timeout:45000,workers:1,
  use:{baseURL:`http://127.0.0.1:${port}`,browserName:'chromium',launchOptions:process.env.ZQ_BROWSER?{executablePath:process.env.ZQ_BROWSER}:{},viewport:{width:1440,height:1100}},
  webServer:{command:process.env.ZQ_PAPER_SERVER || `./bin/zenquant serve --engineering --port ${port}`,url:`http://127.0.0.1:${port}/paper`,reuseExistingServer:false,timeout:15000},
});
