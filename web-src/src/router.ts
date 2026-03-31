import { createRouter, createWebHashHistory } from "vue-router";
import DashboardPage from "./pages/DashboardPage.vue";

export const router = createRouter({
  history: createWebHashHistory("/static/"),
  routes: [
    { path: "/", component: DashboardPage },
    {
      path: "/indicator/:key",
      component: () => import("./pages/IndicatorPage.vue"),
    },
  ],
});
