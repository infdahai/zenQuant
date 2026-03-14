import { createApp } from "/static/vendor/vue.esm-browser.prod.js";
import { createRouter, createWebHashHistory } from "/static/vendor/vue-router.esm-browser.prod.js";

import { AppShell } from "./AppShell.js";
import { DashboardPage } from "./pages/DashboardPage.js";
import { IndicatorPage } from "./pages/IndicatorPage.js";
import { createMarketStore } from "./store.js";

const routes = [
  { path: "/", component: DashboardPage },
  { path: "/indicator/:key", component: IndicatorPage },
];

const router = createRouter({
  history: createWebHashHistory(),
  routes,
});

const store = createMarketStore();

const app = createApp(AppShell);
app.provide("marketStore", store);
app.use(router);
app.mount("#app");

store.reload();
setInterval(() => {
  store.refreshState();
}, 2500);

window.addEventListener("beforeunload", () => {
  store.cleanupSocket();
});
