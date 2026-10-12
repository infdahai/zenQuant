import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir:'./options-ui/e2e',testMatch:'paper.spec.js',timeout:45000,workers:1,
  use:{baseURL:'http://127.0.0.1:8093',browserName:'chromium',launchOptions:process.env.ZQ_BROWSER?{executablePath:process.env.ZQ_BROWSER}:{},viewport:{width:1440,height:1100}},
  webServer:{command:'./rust/target/release/examples/paper_browser_fixture',url:'http://127.0.0.1:8093/paper',reuseExistingServer:false,timeout:15000},
});
