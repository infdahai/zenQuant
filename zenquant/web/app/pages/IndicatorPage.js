// Focused page for one indicator key across the current selection.

import { computed, defineComponent, inject } from "/static/vendor/vue.esm-browser.prod.js";
import { useRoute } from "/static/vendor/vue-router.esm-browser.prod.js";
import { CandleCanvas } from "../components/CandleCanvas.js";
import { SeriesCanvas } from "../components/SeriesCanvas.js";
import {
  collectIndicatorKeys,
  formatDisplayValue,
  formatNumber,
  normalizeIndicatorSeries,
} from "../utils.js";

export const IndicatorPage = defineComponent({
  name: "IndicatorPage",
  components: { CandleCanvas, SeriesCanvas },
  setup() {
    // This page reuses the shared store rather than fetching its own data so
    // indicator detail stays in lockstep with the dashboard selection.
    const store = inject("marketStore");
    const route = useRoute();
    const indicator = computed(() => store.state.latestIndicator || (store.state.snapshot || {}).latest_indicator || null);
    const keys = computed(() => {
      const proxy = (store.state.snapshot || {}).proxy_indicator_latest || {};
      return collectIndicatorKeys(indicator.value, proxy);
    });

    const selectedKey = computed(() => {
      const requested = decodeURIComponent(String(route.params.key || "")).trim();
      if (!requested) {
        return keys.value[0] || "";
      }
      if (keys.value.includes(requested)) {
        return requested;
      }
      return keys.value[0] || requested;
    });

    const selectedValue = computed(() => {
      const proxy = (store.state.snapshot || {}).proxy_indicator_latest || {};
      if (indicator.value && indicator.value.values && indicator.value.values[selectedKey.value] !== undefined) {
        return indicator.value.values[selectedKey.value];
      }
      return proxy[selectedKey.value];
    });

    const series = computed(() => {
      const indicatorSeries = normalizeIndicatorSeries(
        selectedKey.value,
        store.state.indicatorHistory,
        store.state.categoryMaps,
      );
      if (indicatorSeries.some((item) => Number.isFinite(item) && item !== 0)) {
        return indicatorSeries;
      }
      if (selectedKey.value === "last_price") {
        return ((store.state.snapshot || {}).price_series || []).map((item) => Number(item.price));
      }
      const proxyHistory = (store.state.snapshot || {}).proxy_indicator_history || {};
      const proxySeries = proxyHistory[selectedKey.value];
      if (Array.isArray(proxySeries)) {
        return proxySeries.map((item) => Number(item.value));
      }
      return [];
    });
    const candles = computed(() => ((store.state.snapshot || {}).candles || []).slice(-160));
    const priceSeries = computed(() => ((store.state.snapshot || {}).price_series || []).slice(-260));

    const qualityFlags = computed(() => {
      if (!indicator.value) {
        return "-";
      }
      return (indicator.value.quality_flags || []).join(", ") || "OK";
    });

    function indicatorRoute(key) {
      return `/indicator/${encodeURIComponent(String(key))}`;
    }

    return {
      store,
      keys,
      candles,
      priceSeries,
      indicatorRoute,
      selectedKey,
      selectedValue,
      series,
      qualityFlags,
      formatDisplayValue,
      formatNumber,
    };
  },
  template: `
    <section class="indicator-page">
      <article class="panel">
        <div class="panel-title">Indicator Header</div>
        <div class="indicator-header">
          <div>
            <h2>{{ selectedKey }}</h2>
            <p>Symbol {{ store.state.symbol }} · Timeframe {{ store.state.tf }}</p>
          </div>
          <div class="header-value">
            <span>Latest</span>
            <strong>{{ formatDisplayValue(selectedValue, 6) }}</strong>
          </div>
        </div>
        <div class="indicator-tabs">
          <router-link
            v-for="key in keys"
            :key="key"
            :to="indicatorRoute(key)"
            class="indicator-tab"
            :class="{ active: key === selectedKey }"
          >
            {{ key }}
          </router-link>
        </div>
      </article>

      <article class="panel">
        <div class="panel-title">Price Panel</div>
        <CandleCanvas :candles="candles" :prices="priceSeries" :height="250" />
      </article>

      <article class="panel">
        <div class="panel-title">{{ selectedKey }} Curve</div>
        <SeriesCanvas :values="series" :height="250" :color="'#f6a400'" :show-zero-line="true" />
      </article>

      <article class="panel">
        <div class="panel-title">Data Quality</div>
        <div class="quality-grid">
          <div class="quality-item">
            <span>Flags</span>
            <strong>{{ qualityFlags }}</strong>
          </div>
          <div class="quality-item">
            <span>Venue Count</span>
            <strong>{{ indicator && indicator.coverage ? indicator.coverage.venue_count : '-' }}</strong>
          </div>
          <div class="quality-item">
            <span>OI Coverage</span>
            <strong>{{ indicator && indicator.coverage ? formatNumber(indicator.coverage.oi_coverage, 4) : '-' }}</strong>
          </div>
          <div class="quality-item">
            <span>Staleness ms</span>
            <strong>{{ indicator && indicator.coverage ? indicator.coverage.staleness_ms : '-' }}</strong>
          </div>
        </div>
      </article>
    </section>
  `,
});
