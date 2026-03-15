// Shell layout for the Phase 3 state console.

import { computed, defineComponent, inject, onBeforeUnmount, onMounted, ref } from "/static/vendor/vue.esm-browser.prod.js";
import {
  collectIndicatorKeys,
  formatNumber,
  formatPercent,
  formatRegimeLabel,
  formatRelativeTimeFromNs,
  getMarketHealth,
  humanizeCode,
} from "./utils.js";

export const AppShell = defineComponent({
  name: "AppShell",
  setup() {
    // The shell owns global selection and health-state presentation. Page-level
    // components focus on the current snapshot rather than managing sockets.
    const store = inject("marketStore");
    const nowMs = ref(Date.now());
    const indicatorKeys = computed(() => {
      const indicator = store.state.latestIndicator || (store.state.snapshot || {}).latest_indicator;
      const proxy = (store.state.snapshot || {}).proxy_indicator_latest || {};
      return collectIndicatorKeys(indicator, proxy);
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

    const snapshot = computed(() => store.state.snapshot || {});
    const runtimeStatus = computed(() => snapshot.value.runtime_status || {});
    const marketHealth = computed(() => getMarketHealth(runtimeStatus.value, nowMs.value));
    const marketUpdateCopy = computed(() => {
      const lastNs = Number(runtimeStatus.value.last_market_event_ns || 0);
      return formatRelativeTimeFromNs(lastNs, nowMs.value);
    });
    const shellSummary = computed(() => {
      const state = snapshot.value.market_state || null;
      const latestTarget = snapshot.value.latest_target || null;
      const latestRiskDecision = snapshot.value.risk_decision || null;
      const riskFlags =
        latestRiskDecision && Array.isArray(latestRiskDecision.flags)
          ? latestRiskDecision.flags.filter((flag) => flag !== "OK")
          : [];

      if (!state) {
        return {
          tone: "warning",
          regime: "Warming",
          regimeDetail: "State publishes on confirmed 4H and 1D bar close",
          state: marketHealth.value.label,
          stateDetail: marketHealth.value.detail,
          posture: "Awaiting first classification",
          target: "No state-driven target yet",
          statusLine: "Live feed is up, but the state engine has not emitted a confirmed bar-close state yet.",
        };
      }

      const transitionRisk = Number(state.transition_risk || 0);
      let tone = "ok";
      if (marketHealth.value.tone === "danger" || String(state.quality_state) === "UNSAFE") {
        tone = "danger";
      } else if (
        marketHealth.value.tone === "warning"
        || transitionRisk >= 0.45
        || String(state.quality_state) !== "TRADABLE"
      ) {
        tone = "warning";
      }

      return {
        tone,
        regime: formatRegimeLabel(state.regime),
        regimeDetail: `${humanizeCode(state.volatility_state)} volatility`,
        state: humanizeCode(state.directional_state),
        stateDetail: `${humanizeCode(state.positioning_state)} positioning`,
        posture: humanizeCode(state.quality_state),
        target: latestTarget
          ? `${formatNumber(latestTarget.target_qty, 4)} BTC target`
          : "No target published yet",
        statusLine: `${formatPercent(transitionRisk, 2)} transition risk · ${
          riskFlags.length ? riskFlags.slice(0, 2).map(humanizeCode).join(" | ") : "Risk overlay open"
        }`,
      };
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
      shellSummary,
    };
  },
  template: `
    <div class="app-shell">
      <header class="topbar">
        <div class="brand-block">
          <div class="brand-kicker">ZenQuant Phase 3</div>
          <h1>ZenQuant State Console</h1>
          <p>Operator view for live state, bias, risk, and execution on the flagship BTC strategy.</p>
          <div class="brand-summary-grid">
            <div class="brand-summary-card">
              <span>Regime</span>
              <strong>{{ shellSummary.regime }}</strong>
              <small>{{ shellSummary.regimeDetail }}</small>
            </div>
            <div class="brand-summary-card">
              <span>State</span>
              <strong>{{ shellSummary.state }}</strong>
              <small>{{ shellSummary.stateDetail }}</small>
            </div>
            <div class="brand-summary-card" :class="'brand-summary-card-' + shellSummary.tone">
              <span>Operator Read</span>
              <strong>{{ shellSummary.posture }}</strong>
              <small>{{ shellSummary.target }}</small>
            </div>
          </div>
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
            <span>{{ store.state.tf.toUpperCase() }} state console</span>
            <span>
              {{ store.state.lastError || shellSummary.statusLine }}
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
