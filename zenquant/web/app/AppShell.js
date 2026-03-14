import { computed, defineComponent, inject } from "/static/vendor/vue.esm-browser.prod.js";

export const AppShell = defineComponent({
  name: "AppShell",
  setup() {
    const store = inject("marketStore");
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

    function onSymbolChange(event) {
      store.setSelection(event.target.value, store.state.tf);
    }

    function onTfChange(event) {
      store.setSelection(store.state.symbol, event.target.value);
    }

    function indicatorRoute(key) {
      return `/indicator/${encodeURIComponent(String(key))}`;
    }

    return {
      store,
      indicatorKeys,
      indicatorRoute,
      onSymbolChange,
      onTfChange,
    };
  },
  template: `
    <div class="app-shell">
      <header class="topbar">
        <div class="brand-block">
          <h1>ZenQuant Indicators</h1>
          <p>Vue architecture · indicator-per-page routing</p>
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
          <div class="status-pill" :class="store.state.lastError ? 'danger' : 'ok'">
            {{ store.state.lastError ? 'DEGRADED' : 'LIVE' }}
          </div>
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
