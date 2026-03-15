// Shell layout for the explainability dashboard.

import { computed, defineComponent, inject, onBeforeUnmount, onMounted, ref } from "/static/vendor/vue.esm-browser.prod.js";
import { formatRelativeTimeFromNs, getMarketHealth } from "./utils.js";

export const AppShell = defineComponent({
  name: "AppShell",
  setup() {
    // The shell owns global selection and health-state presentation. Page-level
    // components focus on the current snapshot rather than managing sockets.
    const store = inject("marketStore");
    const nowMs = ref(Date.now());
    const defaultIndicatorKeys = [
      "last_price",
      "funding_oi_weighted",
      "funding_oi_z",
      "cvd_slope",
      "ob_liq_z",
      "overall_ob_liq_z",
      "realized_vol",
      "atr",
      "momentum_roc",
      "momentum_roc_z",
      "zen_pulse",
      "structure_state",
    ];

    const indicatorKeys = computed(() => {
      const indicator = store.state.latestIndicator || (store.state.snapshot || {}).latest_indicator;
      const proxy = (store.state.snapshot || {}).proxy_indicator_latest || {};
      const merged = new Set(defaultIndicatorKeys);
      for (const key of Object.keys(proxy)) {
        merged.add(key);
      }
      if (indicator && indicator.values) {
        for (const key of Object.keys(indicator.values)) {
          merged.add(key);
        }
      }
      return [...merged].sort();
    });

    let refreshTimer;
    onMounted(() => {
      refreshTimer = window.setInterval(() => {
        nowMs.value = Date.now();
      }, 1000);
    });
    onBeforeUnmount(() => {
      if (refreshTimer) {
        window.clearInterval(refreshTimer);
      }
    });

    const runtimeStatus = computed(() => (store.state.snapshot || {}).runtime_status || {});
    const marketHealth = computed(() => getMarketHealth(runtimeStatus.value, nowMs.value));
    const marketUpdateCopy = computed(() => {
      const lastNs = Number(runtimeStatus.value.last_market_event_ns || 0);
      return formatRelativeTimeFromNs(lastNs, nowMs.value);
    });

    function onSymbolChange(event) {
      store.setSelection(event.target.value, store.state.tf);
    }

    function onTfChange(event) {
      store.setSelection(store.state.symbol, event.target.value);
    }

    function indicatorRoute(key) {
      return `/indicator/${encodeURIComponent(String(key))}`;
    }

    function retryNow() {
      void store.reload();
    }

    return {
      store,
      indicatorKeys,
      indicatorRoute,
      marketHealth,
      marketUpdateCopy,
      onSymbolChange,
      onTfChange,
      retryNow,
    };
  },
  template: `
    <div class="app-shell">
      <header class="topbar">
        <div class="brand-block">
          <div class="brand-kicker">ZenQuant Phase 2</div>
          <h1>ZenQuant Indicators</h1>
          <p>Explainable regime dashboard for live bias, risk, and execution state.</p>
        </div>
        <div class="top-controls">
          <label>
            Symbol
            <select :value="store.state.symbol" @change="onSymbolChange">
              <option value="BTCUSDT_PERP">BTCUSDT_PERP</option>
              <option value="ETHUSDT_PERP">ETHUSDT_PERP</option>
            </select>
          </label>
          <label>
            Timeframe
            <select :value="store.state.tf" @change="onTfChange">
              <option value="4h">4H</option>
              <option value="1d">1D</option>
            </select>
          </label>
        </div>
        <div class="status-panel">
          <div class="status-pill" :class="marketHealth.tone">
            {{ store.state.lastError ? 'ERROR' : marketHealth.label }}
          </div>
          <div class="status-copy">
            <strong>{{ store.state.symbol }}</strong>
            <span>{{ store.state.tf.toUpperCase() }} decision surface</span>
            <span>
              {{ store.state.lastError || marketHealth.detail }}
            </span>
            <span>
              Last market update {{ marketUpdateCopy }}
            </span>
          </div>
          <button
            v-if="store.state.lastError || marketHealth.tone !== 'ok'"
            class="retry-btn"
            @click="retryNow"
          >
            Retry feed
          </button>
        </div>
      </header>

      <nav class="route-strip">
        <router-link to="/" class="route-chip">Dashboard</router-link>
        <router-link
          v-for="key in indicatorKeys"
          :key="key"
          class="route-chip"
          :to="indicatorRoute(key)"
        >
          {{ key }}
        </router-link>
      </nav>

      <main class="content-view">
        <router-view />
      </main>

      <footer class="footer-note" v-if="store.state.lastError">
        {{ store.state.lastError }}
      </footer>
    </div>
  `,
});
