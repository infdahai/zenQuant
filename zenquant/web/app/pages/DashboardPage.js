import { computed, defineComponent, inject, reactive } from "/static/vendor/vue.esm-browser.prod.js";
import { normalizeIndicatorSeries, formatNumber } from "../utils.js";
import { CandleCanvas } from "../components/CandleCanvas.js";
import { SeriesCanvas } from "../components/SeriesCanvas.js";

export const DashboardPage = defineComponent({
  name: "DashboardPage",
  components: { CandleCanvas, SeriesCanvas },
  setup() {
    const store = inject("marketStore");
    const expanded = reactive({});
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

    const indicator = computed(() => store.state.latestIndicator || (store.state.snapshot || {}).latest_indicator || null);
    const directionalBias = computed(() => (store.state.snapshot || {}).directional_bias || null);
    const directionalBiasMeta = computed(() => (store.state.snapshot || {}).directional_bias_meta || null);
    const positionState = computed(() => (store.state.snapshot || {}).position_state || null);
    const latestTarget = computed(() => (store.state.snapshot || {}).latest_target || null);
    const latestReason = computed(() => {
      const reasons = (store.state.snapshot || {}).reasons || [];
      return reasons[0] || null;
    });
    const latestReport = computed(() => {
      const reports = (store.state.snapshot || {}).reports || [];
      return reports[0] || null;
    });
    const latestRiskDecision = computed(() => (store.state.snapshot || {}).risk_decision || null);
    const candles = computed(() => ((store.state.snapshot || {}).candles || []).slice(-180));
    const priceSeries = computed(() => ((store.state.snapshot || {}).price_series || []).slice(-260));
    const proxyHistory = computed(() => (store.state.snapshot || {}).proxy_indicator_history || {});
    const proxyLatest = computed(() => (store.state.snapshot || {}).proxy_indicator_latest || {});
    const indicatorKeys = computed(() => {
      const values = indicator.value && indicator.value.values ? indicator.value.values : {};
      const merged = new Set(defaultIndicatorKeys);
      for (const key of Object.keys(values)) {
        merged.add(key);
      }
      for (const key of Object.keys(proxyLatest.value)) {
        merged.add(key);
      }
      return [...merged].sort();
    });

    const indicatorCards = computed(() => {
      const history = store.state.indicatorHistory || [];
      return indicatorKeys.value.map((key) => ({
        key,
        value:
          indicator.value && indicator.value.values && indicator.value.values[key] !== undefined
            ? indicator.value.values[key]
            : proxyLatest.value[key],
        series: selectSeries(
          key,
          history,
          proxyHistory.value,
          priceSeries.value,
          store.state.categoryMaps,
        ),
      }));
    });

    function toggleExpand(key) {
      expanded[key] = !expanded[key];
    }

    function isExpanded(key) {
      return expanded[key] === true;
    }

    function seriesStats(values) {
      const numeric = values.filter((item) => Number.isFinite(item));
      if (!numeric.length) {
        return { min: "-", max: "-", latest: "-" };
      }
      return {
        min: formatNumber(Math.min(...numeric), 4),
        max: formatNumber(Math.max(...numeric), 4),
        latest: formatNumber(numeric[numeric.length - 1], 4),
      };
    }

    function indicatorRoute(key) {
      return `/indicator/${encodeURIComponent(String(key))}`;
    }

    function displayValue(value) {
      if (typeof value === "number" && Number.isFinite(value)) {
        return formatNumber(value, 4);
      }
      if (value === null || value === undefined) {
        return "-";
      }
      return `${value}`;
    }

    function selectSeries(key, indicatorHistory, proxySeriesMap, pricePoints, categoryMaps) {
      if (indicatorHistory && indicatorHistory.length > 0) {
        const series = normalizeIndicatorSeries(key, indicatorHistory, categoryMaps);
        if (series.some((item) => Number.isFinite(item) && item !== 0)) {
          return series;
        }
      }
      if (key === "last_price" && pricePoints && pricePoints.length > 0) {
        return pricePoints.map((item) => Number(item.price));
      }
      const proxySeries = proxySeriesMap[key];
      if (Array.isArray(proxySeries) && proxySeries.length > 0) {
        return proxySeries.map((item) => Number(item.value));
      }
      return [];
    }

    return {
      store,
      candles,
      directionalBias,
      directionalBiasMeta,
      priceSeries,
      indicator,
      indicatorCards,
      latestReason,
      latestReport,
      latestRiskDecision,
      latestTarget,
      positionState,
      displayValue,
      indicatorRoute,
      isExpanded,
      seriesStats,
      toggleExpand,
      formatNumber,
    };
  },
  template: `
    <section class="indicator-stack">
      <article class="panel">
        <div class="panel-title">Decision Path</div>
        <div class="decision-grid">
          <div class="decision-item">
            <span>1D bias</span>
            <strong>
              {{ directionalBias ? displayValue(directionalBias.direction) : "-" }}
            </strong>
            <small>
              {{
                directionalBiasMeta
                  ? (directionalBiasMeta.is_stale ? "stale" : "fresh")
                  : "unavailable"
              }}
            </small>
          </div>
          <div class="decision-item">
            <span>Bias regime</span>
            <strong>{{ directionalBias ? directionalBias.regime : "-" }}</strong>
            <small>
              {{
                directionalBiasMeta && directionalBiasMeta.quality_flags
                  ? directionalBiasMeta.quality_flags.join(", ") || "OK"
                  : "OK"
              }}
            </small>
          </div>
          <div class="decision-item">
            <span>Position qty</span>
            <strong>{{ positionState ? formatNumber(positionState.current_qty, 4) : "-" }}</strong>
            <small>
              {{
                positionState
                  ? "trail " + formatNumber(positionState.trailing_stop, 2)
                  : "flat"
              }}
            </small>
          </div>
          <div class="decision-item">
            <span>Latest target</span>
            <strong>{{ latestTarget ? formatNumber(latestTarget.target_qty, 4) : "-" }}</strong>
            <small>
              {{
                latestTarget && latestTarget.reason_codes
                  ? latestTarget.reason_codes.join(", ")
                  : "-"
              }}
            </small>
          </div>
          <div class="decision-item">
            <span>Risk flags</span>
            <strong>
              {{
                latestRiskDecision && latestRiskDecision.flags
                  ? latestRiskDecision.flags.join(", ")
                  : "OK"
              }}
            </strong>
            <small>
              {{
                latestRiskDecision
                  ? "lev cap " + formatNumber(latestRiskDecision.lev_cap, 2)
                  : "-"
              }}
            </small>
          </div>
          <div class="decision-item">
            <span>Last execution</span>
            <strong>{{ latestReport ? latestReport.status : "-" }}</strong>
            <small>
              {{
                latestReport
                  ? ("qty " + formatNumber(latestReport.filled_qty, 4) + " @ " + formatNumber(latestReport.avg_price, 2))
                  : "-"
              }}
            </small>
          </div>
          <div class="decision-item decision-item-wide">
            <span>Latest reason</span>
            <strong>{{ latestReason ? latestReason.summary : "-" }}</strong>
            <small>
              {{
                latestReason && latestReason.reason_codes
                  ? latestReason.reason_codes.join(", ")
                  : "-"
              }}
            </small>
          </div>
        </div>
      </article>

      <article class="panel">
        <div class="panel-title">Price Curve</div>
        <CandleCanvas :candles="candles" :prices="priceSeries" :height="290" />
      </article>

      <article class="panel indicator-pane" v-for="item in indicatorCards" :key="item.key">
        <div class="pane-head">
          <div class="pane-title">
            <button class="toggle-btn" @click="toggleExpand(item.key)">
              {{ isExpanded(item.key) ? "▾" : "▸" }}
            </button>
            <router-link class="pane-name" :to="indicatorRoute(item.key)">
              {{ item.key }}
            </router-link>
          </div>
          <div class="pane-stats">
            <span>L {{ seriesStats(item.series).min }}</span>
            <span>H {{ seriesStats(item.series).max }}</span>
            <strong>C {{ displayValue(item.value) }}</strong>
          </div>
        </div>
        <SeriesCanvas
          :values="item.series"
          :height="isExpanded(item.key) ? 190 : 82"
          :show-zero-line="true"
          :fill="isExpanded(item.key)"
        />
      </article>
    </section>
  `,
});
