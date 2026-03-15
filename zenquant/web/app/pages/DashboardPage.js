// Main trader-facing dashboard page.

import {
  computed,
  defineComponent,
  inject,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
} from "/static/vendor/vue.esm-browser.prod.js";
import {
  formatBiasLabel,
  formatCurrency,
  formatNumber,
  formatPercent,
  formatRelativeTimeFromNs,
  formatRegimeLabel,
  formatSignedCurrency,
  getMarketHealth,
  humanizeCode,
  normalizeIndicatorSeries,
} from "../utils.js";
import { CandleCanvas } from "../components/CandleCanvas.js";
import { SeriesCanvas } from "../components/SeriesCanvas.js";

export const DashboardPage = defineComponent({
  name: "DashboardPage",
  components: { CandleCanvas, SeriesCanvas },
  setup() {
    // Keep most values as computed projections of the shared snapshot so the
    // UI can update on every websocket payload without manual state syncing.
    const store = inject("marketStore");
    const expanded = reactive({});
    const nowMs = ref(Date.now());
    let timerId = 0;

    onMounted(() => {
      timerId = window.setInterval(() => {
        nowMs.value = Date.now();
      }, 1000);
    });

    onBeforeUnmount(() => {
      if (timerId) {
        window.clearInterval(timerId);
      }
    });

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

    const snapshot = computed(() => store.state.snapshot || {});
    const indicator = computed(() => store.state.latestIndicator || snapshot.value.latest_indicator || null);
    const directionalBias = computed(() => snapshot.value.directional_bias || null);
    const directionalBiasMeta = computed(() => snapshot.value.directional_bias_meta || null);
    const positionState = computed(() => snapshot.value.position_state || null);
    const latestTarget = computed(() => snapshot.value.latest_target || null);
    const latestReason = computed(() => {
      const reasons = snapshot.value.reasons || [];
      return reasons[0] || null;
    });
    const reports = computed(() => snapshot.value.reports || []);
    const latestReport = computed(() => reports.value[0] || null);
    const riskSnapshot = computed(() => snapshot.value.risk_snapshot || null);
    const latestRiskDecision = computed(() => snapshot.value.risk_decision || null);
    const runtimeStatus = computed(() => snapshot.value.runtime_status || {});
    const marketHealth = computed(() => getMarketHealth(runtimeStatus.value, nowMs.value));
    const candles = computed(() => (snapshot.value.candles || []).slice(-180));
    const priceSeries = computed(() => (snapshot.value.price_series || []).slice(-300));
    const equityCurve = computed(() =>
      (snapshot.value.equity_curve || []).map((item) => Number(item.equity)),
    );
    const pnlSummary = computed(() => snapshot.value.pnl_summary || null);
    const alerts = computed(() => (snapshot.value.alerts || []).slice(0, 6));
    const proxyHistory = computed(() => snapshot.value.proxy_indicator_history || {});
    const proxyLatest = computed(() => snapshot.value.proxy_indicator_latest || {});

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

    const primaryRiskTone = computed(() => {
      const flags = latestRiskDecision.value && latestRiskDecision.value.flags ? latestRiskDecision.value.flags : [];
      if (flags.some((flag) => ["KILL_SWITCH", "DD_STEP_3", "SEVERE_CROWDING", "VOL_SHOCK"].includes(flag))) {
        return "danger";
      }
      if (flags.some((flag) => flag !== "OK")) {
        return "warning";
      }
      return "ok";
    });

    const positionSummary = computed(() => {
      const position = positionState.value;
      const lastPrice = Number(snapshot.value.last_price || 0);
      if (!position) {
        return {
          headline: "Flat",
          detail: "No open position",
        };
      }
      const direction = position.direction > 0 ? "Long" : "Short";
      let stopDetail = "Trailing stop not set";
      if (Number(position.trailing_stop) > 0 && lastPrice > 0) {
        const distance = Math.abs((lastPrice - position.trailing_stop) / lastPrice);
        const relation = position.direction > 0 ? "below last" : "above last";
        stopDetail = `Trailing stop ${formatCurrency(position.trailing_stop)} (${formatPercent(distance, 2)} ${relation})`;
      }
      return {
        headline: `${direction} ${formatNumber(position.current_qty, 4)} BTC`,
        detail: stopDetail,
      };
    });

    const biasSummary = computed(() => {
      const bias = directionalBias.value;
      const meta = directionalBiasMeta.value;
      if (!bias) {
        return {
          headline: "NO SIGNAL",
          detail: marketHealth.value.tone === "ok" ? "Awaiting 1D bias or no directional edge yet" : marketHealth.value.detail,
        };
      }
      const detailParts = [];
      if (meta && meta.is_stale) {
        detailParts.push(`Stale (${formatNumber(meta.age_4h_bars, 1)} 4h bars old)`);
      } else {
        detailParts.push(`Confidence ${formatNumber(bias.confidence, 2)}`);
      }
      if (meta && meta.quality_flags && meta.quality_flags.length) {
        detailParts.push(meta.quality_flags.map(humanizeCode).join(", "));
      }
      return {
        headline: formatBiasLabel(bias.direction, true),
        detail: detailParts.join(" | "),
      };
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

    function formatCodes(values, limit = 3) {
      if (!Array.isArray(values) || !values.length) {
        return "None";
      }
      return values.slice(0, limit).map(humanizeCode).join(" | ");
    }

    function formatAlert(alert) {
      if (!alert) {
        return "-";
      }
      const flags = Array.isArray(alert.flags) ? alert.flags.map(humanizeCode).join(", ") : humanizeCode(alert.type);
      return `${humanizeCode(alert.type)}: ${flags}`;
    }

    function alertTone(alert) {
      if (!alert || !Array.isArray(alert.flags)) {
        return "warning";
      }
      if (alert.flags.some((flag) => ["KILL_SWITCH", "DD_STEP_3", "DATA_FUTURE"].includes(flag))) {
        return "danger";
      }
      return "warning";
    }

    function selectSeries(key, indicatorHistory, proxySeriesMap, pricePoints, categoryMaps) {
      // Prefer true indicator history, then fall back to live tick price series,
      // then finally any proxy metric series attached to the snapshot.
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
      alerts,
      biasSummary,
      candles,
      directionalBias,
      displayValue,
      equityCurve,
      formatAlert,
      formatCodes,
      formatCurrency,
      formatNumber,
      formatPercent,
      formatRegimeLabel,
      formatRelativeTimeFromNs,
      formatSignedCurrency,
      indicator,
      indicatorCards,
      indicatorRoute,
      isExpanded,
      latestReason,
      latestReport,
      latestRiskDecision,
      latestTarget,
      marketHealth,
      pnlSummary,
      positionSummary,
      priceSeries,
      primaryRiskTone,
      reports,
      riskSnapshot,
      runtimeStatus,
      seriesStats,
      snapshot,
      store,
      toggleExpand,
      alertTone,
      humanizeCode,
    };
  },
  template: `
    <section class="indicator-stack">
      <article class="panel">
        <div class="panel-title">Live Overview</div>
        <div class="hero-grid">
          <div class="hero-card">
            <span>Market health</span>
            <strong>{{ marketHealth.label }}</strong>
            <small>{{ marketHealth.detail }}</small>
          </div>
          <div class="hero-card">
            <span>Position</span>
            <strong>{{ positionSummary.headline }}</strong>
            <small>{{ positionSummary.detail }}</small>
          </div>
          <div class="hero-card" :class="'hero-card-' + primaryRiskTone">
            <span>Total PnL</span>
            <strong>{{ pnlSummary ? formatSignedCurrency(pnlSummary.pnl_abs, 2) : "-" }}</strong>
            <small>
              {{
                pnlSummary
                  ? (formatPercent(pnlSummary.pnl_pct, 2) + " from start")
                  : "Waiting for equity baseline"
              }}
            </small>
          </div>
          <div class="hero-card">
            <span>Equity</span>
            <strong>{{ riskSnapshot ? formatCurrency(riskSnapshot.equity, 2) : "-" }}</strong>
            <small>
              {{
                riskSnapshot
                  ? ("Drawdown " + formatPercent(riskSnapshot.drawdown, 2))
                  : "No risk snapshot yet"
              }}
            </small>
          </div>
        </div>
      </article>

      <article class="panel">
        <div class="panel-title">Decision Path</div>
        <div class="decision-grid">
          <div class="decision-item">
            <span>1D bias</span>
            <strong>{{ biasSummary.headline }}</strong>
            <small>{{ biasSummary.detail }}</small>
          </div>
          <div class="decision-item">
            <span>Bias regime</span>
            <strong>{{ directionalBias ? formatRegimeLabel(directionalBias.regime) : "Awaiting regime" }}</strong>
            <small>
              {{
                directionalBias
                  ? ("Updated " + formatRelativeTimeFromNs(directionalBias.cutoff_time_ns))
                  : "No 1D regime published yet"
              }}
            </small>
          </div>
          <div class="decision-item">
            <span>Stream status</span>
            <strong data-testid="stream-status">{{ marketHealth.label }}</strong>
            <small data-testid="stream-update">{{ marketHealth.detail }}</small>
          </div>
          <div class="decision-item">
            <span>Latest target</span>
            <strong>{{ latestTarget ? formatNumber(latestTarget.target_qty, 4) : "-" }}</strong>
            <small>
              {{
                latestTarget
                  ? formatCodes(latestTarget.reason_codes)
                  : "No target yet"
              }}
            </small>
          </div>
          <div class="decision-item" :class="'decision-item-' + primaryRiskTone">
            <span>Risk flags</span>
            <strong>
              {{
                latestRiskDecision && latestRiskDecision.flags
                  ? formatCodes(latestRiskDecision.flags, 2)
                  : "Normal"
              }}
            </strong>
            <small>
              {{
                latestRiskDecision
                  ? ("Leverage cap " + formatNumber(latestRiskDecision.lev_cap, 2))
                  : "No risk decision yet"
              }}
            </small>
          </div>
          <div class="decision-item">
            <span>Last execution</span>
            <strong>{{ latestReport ? latestReport.status : "No fills yet" }}</strong>
            <small>
              {{
                latestReport
                  ? (formatNumber(latestReport.filled_qty, 4) + " @ " + formatCurrency(latestReport.avg_price, 2))
                  : "Execution log is empty"
              }}
            </small>
          </div>
          <div class="decision-item decision-item-wide">
            <span>Latest reason</span>
            <strong>{{ latestReason ? latestReason.summary : "Waiting for strategy reason" }}</strong>
            <small>
              {{
                latestReason && latestReason.reason_codes
                  ? formatCodes(latestReason.reason_codes, 4)
                  : "No strategy reason published yet"
              }}
            </small>
          </div>
        </div>
      </article>

      <article class="panel">
        <div class="panel-title">Price Curve</div>
        <CandleCanvas :candles="candles" :prices="priceSeries" :height="290" />
      </article>

      <article class="panel">
        <div class="panel-title">Equity Curve</div>
        <SeriesCanvas :values="equityCurve" color="#19d7b8" :height="190" />
      </article>

      <div class="two-up-grid">
        <article class="panel">
          <div class="panel-title">Alert Panel</div>
          <div class="event-list">
            <div v-if="!alerts.length" class="event-row">
              <strong>No active alerts</strong>
              <small>The system has not raised recent market or risk alerts.</small>
            </div>
            <div
              v-for="(alert, index) in alerts"
              :key="'alert-' + index"
              class="event-row"
              :class="'event-row-' + alertTone(alert)"
            >
              <strong>{{ formatAlert(alert) }}</strong>
              <small>{{ formatRelativeTimeFromNs(alert.ts_ns) }}</small>
            </div>
          </div>
        </article>

        <article class="panel">
          <div class="panel-title">Execution Log</div>
          <div class="event-list">
            <div v-if="!reports.length" class="event-row">
              <strong>No executions yet</strong>
              <small>Recent fills will appear here with side, price, and fee.</small>
            </div>
            <div
              v-for="(report, index) in reports.slice(0, 6)"
              :key="'report-' + index"
              class="event-row"
            >
              <strong>
                {{ report.status }} · {{ report.side > 0 ? "Buy" : "Sell" }} · {{ formatNumber(report.filled_qty, 4) }}
              </strong>
              <small>
                {{ formatCurrency(report.avg_price, 2) }} · fee {{ formatCurrency(report.fee, 4) }} · {{ formatRelativeTimeFromNs(report.transaction_time_ns) }}
              </small>
            </div>
          </div>
        </article>
      </div>

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
          <div class="pane-value">{{ displayValue(item.value) }}</div>
        </div>
        <div class="pane-body" v-show="isExpanded(item.key)">
          <SeriesCanvas :values="item.series" :height="150" />
          <div class="stats-row">
            <span>min {{ seriesStats(item.series).min }}</span>
            <span>max {{ seriesStats(item.series).max }}</span>
            <span>latest {{ seriesStats(item.series).latest }}</span>
          </div>
        </div>
      </article>
    </section>
  `,
});
