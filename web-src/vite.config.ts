import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  base: "/static/",
  build: {
    outDir: "../zenquant/web",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    open: false,
    proxy: {
      "/api": "http://localhost:8090",
      "/ws": { target: "ws://localhost:8090", ws: true },
      "/healthz": "http://localhost:8090",
    },
  },
});
