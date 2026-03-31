<script setup lang="ts">
import { darkTheme } from "naive-ui";
import { NConfigProvider, NMessageProvider } from "naive-ui";
import { onBeforeUnmount, onMounted } from "vue";
import { themeOverrides } from "./theme/dark";
import { useMarketStore } from "./stores/market";
import StatusBar from "./components/layout/StatusBar.vue";

const store = useMarketStore();

onMounted(() => {
  store.reload();
  const timer = setInterval(() => store.refreshState(), 2500);
  onBeforeUnmount(() => clearInterval(timer));
});

onBeforeUnmount(() => {
  store.cleanupSocket();
});
</script>

<template>
  <NConfigProvider :theme="darkTheme" :theme-overrides="themeOverrides">
    <NMessageProvider>
      <div class="app-root">
        <StatusBar />
        <main class="main-content">
          <router-view />
        </main>
      </div>
    </NMessageProvider>
  </NConfigProvider>
</template>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  background: #0a0e14;
  color: #e0e6ed;
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI",
    sans-serif;
  font-size: 13px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

.app-root {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.main-content {
  flex: 1;
  max-width: 1600px;
  width: 100%;
  margin: 0 auto;
  padding: 12px 16px 24px;
}

::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

::-webkit-scrollbar-track {
  background: #0a0e14;
}

::-webkit-scrollbar-thumb {
  background: rgba(55, 90, 120, 0.4);
  border-radius: 3px;
}

::-webkit-scrollbar-thumb:hover {
  background: rgba(55, 90, 120, 0.6);
}
</style>
