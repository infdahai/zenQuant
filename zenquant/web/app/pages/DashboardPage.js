// Main operator-facing dashboard page.

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
  collectIndicatorKeys,
  formatBiasLabel,
  formatCurrency,
  formatDisplayValue,
  formatNumber,
  formatPercent,
  formatRelativeTimeFromNs,
  formatRegimeLabel,
  formatSignedCurrency,
  formatSignedPercent,
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
    const expandMode = ref(loadExpandMode());
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

    const snapshot = computed(() => store.state.snapshot || {});
    const indicator = computed(() => store.state.latestIndicator || snapshot.value.latest_indicator || null);
    const directionalBias = computed(() => snapshot.value.directional_bias || null);
    const directionalBiasMeta = computed(() => snapshot.value.directional_bias_meta || null);
    const marketState = computed(() => snapshot.value.market_state || null);
    const activePlaybook = computed(() => snapshot.value.active_playbook || null);
    const latestSizing = computed(() => snapshot.value.latest_sizing || null);
    const latestExit = computed(() => snapshot.value.latest_exit || null);
    const deribitRisk = computed(() => snapshot.value.deribit_risk || null);
    const deribitHistory = computed(() => (snapshot.value.deribit_history || []).slice(-96));
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
    const deribitStatus = computed(() =>
      buildDeribitStatus({
        deribitRisk: deribitRisk.value,
        runtimeStatus: runtimeStatus.value,
        nowMs: nowMs.value,
      }),
    );
    const stateConfidence = computed(() => averageStateConfidence(marketState.value));
    const databaseStatus = computed(() => {
      const database = runtimeStatus.value.database || {};
      if (!database.enabled) {
        return {
          label: "Not configured",
          detail: "PostgreSQL audit store is optional in local mode",
          tone: "warning",
        };
      }
      if (!database.connected) {
        return {
          label: "Offline",
          detail: database.last_error || "Audit writes are paused until the database reconnects",
          tone: "danger",
        };
      }
      return {
        label: "Online",
        detail: `${formatNumber(database.writes || 0, 0)} writes · queue ${formatNumber(database.queue_depth || 0, 0)}`,
        tone: "ok",
      };
    });
    const candles = computed(() => (snapshot.value.candles || []).slice(-180));
    const priceSeries = computed(() => (snapshot.value.price_series || []).slice(-300));
    const equityCurve = computed(() =>
      (snapshot.value.equity_curve || []).map((item) => Number(item.equity)),
    );
    const pnlSummary = computed(() => snapshot.value.pnl_summary || null);
    const alerts = computed(() => (snapshot.value.alerts || []).slice(0, 6));
    const proxyHistory = computed(() => snapshot.value.proxy_indicator_history || {});
    const proxyLatest = computed(() => snapshot.value.proxy_indicator_latest || {});
    const thesisSummary = computed(() =>
      buildThesisSummary({
        state: marketState.value,
        playbook: activePlaybook.value,
        directionalBias: directionalBias.value,
        latestTarget: latestTarget.value,
        latestRiskDecision: latestRiskDecision.value,
        marketHealth: marketHealth.value,
        stateConfidence: stateConfidence.value,
      }),
    );
    const stateCards = computed(() => buildStateCards(marketState.value));
    const sizingSummary = computed(() => {
      const sizing = latestSizing.value;
      if (!sizing) {
        return {
          headline: "Awaiting sizing",
          detail: "Sizing publishes with each target calculation",
          formula: "-",
          reasons: "No sizing reason codes yet",
        };
      }
      return {
        headline: `${formatNumber(sizing.final_leverage, 2)}x leverage`,
        detail: `${formatNumber(sizing.base_size, 2)} × ${formatNumber(sizing.confidence_multiplier, 2)} × ${formatNumber(sizing.stress_discount, 2)} × ${formatNumber(sizing.execution_haircut, 2)}`,
        formula: `${formatNumber(sizing.base_size, 2)} × ${formatNumber(sizing.confidence_multiplier, 2)} × ${formatNumber(sizing.stress_discount, 2)} × ${formatNumber(sizing.execution_haircut, 2)} = ${formatNumber(sizing.final_leverage, 2)}`,
        reasons: formatCodes(sizing.reason_codes, 4),
      };
    });
    const exitSummary = computed(() => {
      const exit = latestExit.value;
      if (!exit || exit.exit_type === "NONE") {
        return {
          headline: "No active exit",
          detail: "Position remains valid under the current exit rules",
          tone: "ok",
          reductionCopy: "0%",
          stopCopy: "-",
          reasonCopy: "No active exit reasons",
        };
      }
      const detail = [];
      if (Number.isFinite(Number(exit.stop_price)) && Number(exit.stop_price) > 0) {
        detail.push(`Stop ${formatCurrency(exit.stop_price, 2)}`);
      }
      detail.push(humanizeCode(exit.reason_code));
      return {
        headline: `${humanizeCode(exit.exit_type)} · ${humanizeCode(exit.exit_urgency)}`,
        detail: detail.join(" | "),
        tone: exit.exit_urgency === "IMMEDIATE" ? "danger" : "warning",
        reductionCopy: formatPercent(exit.target_reduction_pct, 0),
        stopCopy:
          Number.isFinite(Number(exit.stop_price)) && Number(exit.stop_price) > 0
            ? formatCurrency(exit.stop_price, 2)
            : "-",
        reasonCopy: formatCodes(exit.reason_codes, 5),
      };
    });
    const deribitSummary = computed(() => buildDeribitSummary(deribitRisk.value));
    const deribitIvSeries = computed(() =>
      deribitHistory.value.map((item) => Number(item.atm_iv_30d || 0)),
    );

    const indicatorKeys = computed(() => {
      return collectIndicatorKeys(indicator.value, proxyLatest.value);
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
          hasPosition: false,
          headline: "Flat",
          detail: "No open position",
          pnl: "",
          tone: "ok",
          ageCopy: "Awaiting entry",
          stopDistanceCopy: "No trailing stop",
          entryCopy: "-",
          qtyCopy: "-",
        };
      }
      const direction = position.direction > 0 ? "Long" : "Short";
      const entryPrice = Number(position.entry_price || 0);
      const unrealizedPnlPct =
        entryPrice > 0 && lastPrice > 0
          ? ((lastPrice - entryPrice) / entryPrice) * position.direction
          : null;
      let stopDetail = "Trailing stop not set";
      let tone = "ok";
      let stopDistanceCopy = "Trailing stop not set";
      if (Number(position.trailing_stop) > 0 && lastPrice > 0) {
        const distance = Math.abs((lastPrice - position.trailing_stop) / lastPrice);
        const relation = position.direction > 0 ? "below last" : "above last";
        if (distance < 0.02) {
          tone = "danger";
        } else if (distance < 0.05) {
          tone = "warning";
        }
        stopDetail = `Trailing stop ${formatCurrency(position.trailing_stop)} (${formatPercent(distance, 2)} ${relation})`;
        stopDistanceCopy = `${formatPercent(distance, 2)} ${relation}`;
      }
      return {
        hasPosition: true,
        headline: `${direction} ${formatNumber(position.current_qty, 4)} BTC`,
        detail: stopDetail,
        pnl: unrealizedPnlPct === null ? "" : `Unrealized ${formatSignedPercent(unrealizedPnlPct, 2)}`,
        tone,
        ageCopy: `${formatNumber(position.bars_held || 0, 0)} bars held`,
        stopDistanceCopy,
        entryCopy: formatCurrency(entryPrice, 2),
        qtyCopy: formatNumber(position.current_qty, 4),
      };
    });
    const priceOverlayItems = computed(() => {
      const items = [];
      const position = positionState.value;
      if (position && Number.isFinite(Number(position.entry_price)) && Number(position.entry_price) > 0) {
        items.push({
          label: Number(position.direction) >= 0 ? "Long entry" : "Short entry",
          value: formatCurrency(position.entry_price, 2),
          detail: `Position overlay for ${formatNumber(position.current_qty || 0, 4)} BTC`,
        });
      }
      if (position && Number.isFinite(Number(position.trailing_stop)) && Number(position.trailing_stop) > 0) {
        items.push({
          label: "Trailing stop",
          value: formatCurrency(position.trailing_stop, 2),
          detail: "Red dashed line marks the current invalidation level",
        });
      }
      if (items.length > 0) {
        return items;
      }
      return [
        {
          label: "No active overlays",
          value: "-",
          detail: "Entry and trailing stop lines appear here once the strategy is carrying a position",
        },
      ];
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
      expanded[key] = !isExpanded(key);
    }

    function isExpanded(key) {
      if (expanded[key] !== undefined) {
        return expanded[key] === true;
      }
      if (expandMode.value === "all") {
        return true;
      }
      if (expandMode.value === "none") {
        return false;
      }
      return ["last_price", "realized_vol", "cvd_slope", "zen_pulse", "structure_state"].includes(key);
    }

    function setExpandMode(mode) {
      expandMode.value = mode;
      for (const key of Object.keys(expanded)) {
        delete expanded[key];
      }
      persistExpandMode(mode);
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
      alertTone,
      biasSummary,
      candles,
      databaseStatus,
      directionalBias,
      deribitHistory,
      deribitIvSeries,
      deribitRisk,
      deribitStatus,
      deribitSummary,
      formatDisplayValue,
      equityCurve,
      formatAlert,
      formatCodes,
      formatCurrency,
      formatNumber,
      formatPercent,
      formatRegimeLabel,
      formatRelativeTimeFromNs,
      formatSignedCurrency,
      humanizeCode,
      indicator,
      indicatorCards,
      indicatorRoute,
      isExpanded,
      latestReason,
      latestReport,
      latestRiskDecision,
      latestSizing,
      latestExit,
      latestTarget,
      activePlaybook,
      marketHealth,
      marketState,
      pnlSummary,
      positionState,
      positionSummary,
      priceOverlayItems,
      priceSeries,
      primaryRiskTone,
      reports,
      riskSnapshot,
      runtimeStatus,
      seriesStats,
      setExpandMode,
      snapshot,
      stateCards,
      stateConfidence,
      store,
      thesisSummary,
      toggleExpand,
      expandMode,
      sizingSummary,
      exitSummary,
    };
  },
  template: `
    <section class="indicator-stack">
      <article class="panel">
        <div class="panel-title">Current Thesis</div>
        <div class="thesis-grid">
          <div class="thesis-main" :class="'thesis-main-' + thesisSummary.tone">
            <span class="thesis-kicker">Operator read</span>
            <h2>{{ thesisSummary.headline }}</h2>
            <p>{{ thesisSummary.detail }}</p>
            <p class="thesis-action">{{ thesisSummary.action }}</p>
            <div class="thesis-chip-row">
              <div
                v-for="chip in thesisSummary.chips"
                :key="chip.label"
                class="thesis-chip"
                :class="'thesis-chip-' + chip.tone"
              >
                <span>{{ chip.label }}</span>
                <strong>{{ chip.value }}</strong>
              </div>
            </div>
          </div>
          <div class="thesis-side">
            <div class="thesis-stat">
              <span>Average state confidence</span>
              <strong>{{ thesisSummary.confidenceCopy }}</strong>
              <small>{{ thesisSummary.confidenceDetail }}</small>
            </div>
            <div class="thesis-stat" :class="'thesis-stat-' + thesisSummary.tone">
              <span>Transition risk</span>
              <strong>{{ thesisSummary.transitionCopy }}</strong>
              <div class="confidence-meter">
                <span
                  class="confidence-fill"
                  :class="'confidence-fill-' + thesisSummary.tone"
                  :style="{ width: thesisSummary.transitionWidth }"
                ></span>
              </div>
              <small>{{ thesisSummary.transitionDetail }}</small>
            </div>
          </div>
        </div>
      </article>

      <article class="panel">
        <div class="panel-title">Live Overview</div>
        <div class="hero-grid">
          <div class="hero-card" :class="'hero-card-' + marketHealth.tone">
            <span>Data Stream</span>
            <strong :class="'status-' + marketHealth.tone">{{ marketHealth.label }}</strong>
            <small>{{ marketHealth.detail }}</small>
          </div>
          <div class="hero-card" :class="'hero-card-' + positionSummary.tone">
            <span>Position</span>
            <strong>{{ positionSummary.headline }}</strong>
            <small>{{ positionSummary.detail }}</small>
            <small v-if="positionSummary.pnl">{{ positionSummary.pnl }}</small>
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
          <div class="decision-item" :class="'decision-item-' + marketHealth.tone">
            <span>Data stream</span>
            <strong data-testid="stream-status">{{ marketHealth.label }}</strong>
            <small data-testid="stream-update">{{ marketHealth.detail }}</small>
          </div>
          <div class="decision-item" :class="'decision-item-' + databaseStatus.tone">
            <span>Database</span>
            <strong>{{ databaseStatus.label }}</strong>
            <small>{{ databaseStatus.detail }}</small>
          </div>
          <div class="decision-item">
            <span>Active playbook</span>
            <strong>{{ activePlaybook ? humanizeCode(activePlaybook.playbook_id) : "Awaiting playbook" }}</strong>
            <small>
              {{
                activePlaybook
                  ? ("Confidence " + formatPercent(activePlaybook.playbook_confidence, 2) + " | " + activePlaybook.selection_reason)
                  : "Playbook selection publishes after state classification"
              }}
            </small>
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
          <div class="decision-item">
            <span>Sizing engine</span>
            <strong>{{ sizingSummary.headline }}</strong>
            <small>{{ sizingSummary.reasons }}</small>
          </div>
          <div class="decision-item" :class="'decision-item-' + exitSummary.tone">
            <span>Exit monitor</span>
            <strong>{{ exitSummary.headline }}</strong>
            <small>{{ exitSummary.detail }}</small>
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
        <div class="panel-title">Sizing Breakdown</div>
        <div class="decision-grid">
          <div class="decision-item">
            <span>Final leverage</span>
            <strong>{{ latestSizing ? formatNumber(latestSizing.final_leverage, 2) + "x" : "-" }}</strong>
            <small>{{ sizingSummary.reasons }}</small>
          </div>
          <div class="decision-item">
            <span>Base size</span>
            <strong>{{ latestSizing ? formatNumber(latestSizing.base_size, 3) : "-" }}</strong>
            <small>Vol-targeted leverage budget before filters</small>
          </div>
          <div class="decision-item">
            <span>Confidence multiplier</span>
            <strong>{{ latestSizing ? formatNumber(latestSizing.confidence_multiplier, 3) : "-" }}</strong>
            <small>Average of state and playbook confidence</small>
          </div>
          <div class="decision-item">
            <span>Stress discount</span>
            <strong>{{ latestSizing ? formatNumber(latestSizing.stress_discount, 3) : "-" }}</strong>
            <small>Positioning, volatility, and quality stress filter</small>
          </div>
          <div class="decision-item">
            <span>Execution haircut</span>
            <strong>{{ latestSizing ? formatNumber(latestSizing.execution_haircut, 3) : "-" }}</strong>
            <small>Tradability haircut from quality state</small>
          </div>
          <div class="decision-item decision-item-wide">
            <span>Formula</span>
            <strong>{{ sizingSummary.formula }}</strong>
            <small>{{ sizingSummary.detail }}</small>
          </div>
        </div>
      </article>

      <article class="panel">
        <div class="panel-title">Exit & Position</div>
        <div class="decision-grid">
          <div class="decision-item decision-item-wide" :class="'decision-item-' + positionSummary.tone">
            <span>Position status</span>
            <strong data-testid="position-status">{{ positionSummary.headline }}</strong>
            <small>{{ positionSummary.detail }}</small>
          </div>
          <div class="decision-item" :class="'decision-item-' + positionSummary.tone">
            <span>Unrealized PnL</span>
            <strong data-testid="position-pnl">{{ positionSummary.pnl || "-" }}</strong>
            <small>{{ positionSummary.ageCopy }}</small>
          </div>
          <div class="decision-item">
            <span>Entry price</span>
            <strong>{{ positionSummary.entryCopy }}</strong>
            <small>{{ positionSummary.hasPosition ? ("Qty " + positionSummary.qtyCopy + " BTC") : "No filled position" }}</small>
          </div>
          <div class="decision-item" :class="'decision-item-' + exitSummary.tone">
            <span>Exit decision</span>
            <strong data-testid="exit-decision">{{ exitSummary.headline }}</strong>
            <small>{{ exitSummary.detail }}</small>
          </div>
          <div class="decision-item">
            <span>Trailing stop</span>
            <strong data-testid="trailing-stop">{{ exitSummary.stopCopy !== "-" ? exitSummary.stopCopy : (positionState && positionState.trailing_stop > 0 ? formatCurrency(positionState.trailing_stop, 2) : "-") }}</strong>
            <small>{{ positionSummary.stopDistanceCopy }}</small>
          </div>
          <div class="decision-item">
            <span>Target reduction</span>
            <strong data-testid="target-reduction">{{ exitSummary.reductionCopy }}</strong>
            <small>{{ exitSummary.reasonCopy }}</small>
          </div>
        </div>
      </article>

      <article class="panel">
        <div class="panel-title">4D State</div>
        <div v-if="marketState" class="state-grid">
          <div
            v-for="card in stateCards"
            :key="card.label"
            class="state-card"
            :class="'state-card-' + card.tone"
          >
            <span>{{ card.label }}</span>
            <strong>{{ card.value }}</strong>
            <div class="confidence-meter">
              <span
                class="confidence-fill"
                :class="'confidence-fill-' + card.tone"
                :style="{ width: card.confidenceWidth }"
              ></span>
            </div>
            <small>{{ card.confidenceCopy }}</small>
            <small>{{ card.detail }}</small>
          </div>
          <div class="state-card state-card-wide" :class="'state-card-' + thesisSummary.tone">
            <span>State stability</span>
            <strong>{{ thesisSummary.transitionCopy }}</strong>
            <div class="confidence-meter">
              <span
                class="confidence-fill"
                :class="'confidence-fill-' + thesisSummary.tone"
                :style="{ width: thesisSummary.transitionWidth }"
              ></span>
            </div>
            <small>{{ thesisSummary.transitionDetail }}</small>
            <small>{{ formatCodes(marketState.reason_codes, 5) }}</small>
          </div>
        </div>
        <div v-else class="event-list">
          <div class="event-row">
            <strong>State engine is still warming</strong>
            <small>The dashboard only publishes 4D state after a confirmed bar-close classification.</small>
          </div>
        </div>
      </article>

      <article class="panel">
        <div class="panel-title">Options Thermometer</div>
        <div class="decision-grid">
          <div class="decision-item" :class="'decision-item-' + deribitStatus.tone">
            <span>Deribit feed</span>
            <strong>{{ deribitStatus.label }}</strong>
            <small>{{ deribitStatus.detail }}</small>
          </div>
          <div class="decision-item">
            <span>30D ATM IV</span>
            <strong>{{ deribitRisk ? formatNumber(deribitRisk.atm_iv_30d, 2) + "%" : "-" }}</strong>
            <small>{{ deribitSummary.atmDetail }}</small>
          </div>
          <div class="decision-item" :class="'decision-item-' + deribitSummary.termTone">
            <span>Term structure</span>
            <strong>{{ deribitRisk ? formatSignedPercent(deribitRisk.term_structure_slope, 2) : "-" }}</strong>
            <small>{{ deribitSummary.termDetail }}</small>
          </div>
          <div class="decision-item" :class="'decision-item-' + deribitSummary.skewTone">
            <span>Put/Call skew</span>
            <strong>{{ deribitRisk ? formatNumber(deribitRisk.put_call_skew_25d, 2) : "-" }}</strong>
            <small>{{ deribitSummary.skewDetail }}</small>
          </div>
          <div class="decision-item">
            <span>OI concentration</span>
            <strong>{{ deribitRisk ? formatPercent(deribitRisk.oi_concentration, 2) : "-" }}</strong>
            <small>{{ deribitSummary.oiDetail }}</small>
          </div>
          <div class="decision-item" :class="'decision-item-' + deribitSummary.flowTone">
            <span>Flow proxy</span>
            <strong>{{ deribitRisk ? formatSignedPercent(deribitRisk.block_flow_imbalance, 2) : "-" }}</strong>
            <small>{{ deribitSummary.flowDetail }}</small>
          </div>
          <div class="decision-item decision-item-wide">
            <span>Quality flags</span>
            <strong>{{ deribitRisk ? formatCodes(deribitRisk.quality_flags, 4) : "Awaiting Deribit pack" }}</strong>
            <small>{{ deribitSummary.qualityDetail }}</small>
          </div>
        </div>
        <div class="two-up-grid">
          <div class="panel panel-nested">
            <div class="panel-title panel-title-sub">ATM IV Session Trend</div>
            <SeriesCanvas :values="deribitIvSeries" color="#f5a524" :height="140" />
          </div>
          <div class="panel panel-nested">
            <div class="panel-title panel-title-sub">Observation Notes</div>
            <div class="event-list">
              <div class="event-row">
                <strong>Observation only</strong>
                <small>Deribit currently informs operators only. Strategy, risk, and execution remain unchanged.</small>
              </div>
              <div class="event-row">
                <strong>Flow is a proxy</strong>
                <small>Volume imbalance is used as a public proxy for block flow until a deeper feed is justified.</small>
              </div>
              <div class="event-row">
                <strong>Update cadence</strong>
                <small>{{ deribitStatus.schedule }}</small>
              </div>
            </div>
          </div>
        </div>
      </article>

      <div class="two-up-grid">
        <article class="panel">
          <div class="panel-title">Price Curve</div>
          <CandleCanvas
            :candles="candles"
            :prices="priceSeries"
            :position="positionState"
            :trailing-stop="positionState && positionState.trailing_stop"
            :height="290"
          />
          <div class="decision-grid price-overlay-grid" data-testid="price-overlay-legend">
            <div
              v-for="item in priceOverlayItems"
              :key="item.label"
              class="decision-item"
            >
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
              <small>{{ item.detail }}</small>
            </div>
          </div>
        </article>

        <article class="panel">
          <div class="panel-title">Equity Curve</div>
          <SeriesCanvas :values="equityCurve" color="#19d7b8" :height="190" />
        </article>
      </div>

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

      <article class="panel">
        <div class="panel-title">Indicator Drilldown</div>
        <div class="indicator-controls">
          <button class="indicator-control" :class="{ active: expandMode === 'all' }" @click="setExpandMode('all')">
            Expand All
          </button>
          <button class="indicator-control" :class="{ active: expandMode === 'key' }" @click="setExpandMode('key')">
            Key Indicators
          </button>
          <button class="indicator-control" :class="{ active: expandMode === 'none' }" @click="setExpandMode('none')">
            Collapse All
          </button>
        </div>
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
          <div class="pane-value">{{ formatDisplayValue(item.value) }}</div>
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

function loadExpandMode() {
  if (typeof window === "undefined" || !window.localStorage) {
    return "key";
  }
  return window.localStorage.getItem("zenquant.expandMode") || "key";
}

function persistExpandMode(mode) {
  if (typeof window === "undefined" || !window.localStorage) {
    return;
  }
  window.localStorage.setItem("zenquant.expandMode", mode);
}

function averageStateConfidence(state) {
  if (!state) {
    return null;
  }
  const scores = [
    Number(state.directional_confidence || 0),
    Number(state.volatility_confidence || 0),
    Number(state.positioning_confidence || 0),
    Number(state.quality_confidence || 0),
  ].filter((value) => Number.isFinite(value));
  if (!scores.length) {
    return null;
  }
  return scores.reduce((sum, value) => sum + value, 0) / scores.length;
}

function buildThesisSummary({
  state,
  playbook,
  directionalBias,
  latestTarget,
  latestRiskDecision,
  marketHealth,
  stateConfidence,
}) {
  if (!state) {
    return {
      tone: "warning",
      headline: "Warming the state engine",
      detail: "The runtime is live, but Phase 3 state only publishes after a confirmed bar-close classification.",
      action: "Let the first 4H/1D state settle before judging strategy posture or trusting target changes.",
      confidenceCopy: "-",
      confidenceDetail: "No confirmed state confidence yet",
      transitionCopy: "Transition risk unavailable",
      transitionDetail: "State stability appears after the first classification",
      transitionWidth: "0%",
      chips: [
        { label: "Feed", value: marketHealth.label, tone: marketHealth.tone },
        { label: "Playbook", value: playbook ? humanizeCode(playbook.playbook_id) : "Awaiting playbook", tone: "warning" },
        { label: "Bias", value: directionalBias ? formatBiasLabel(directionalBias.direction, true) : "Awaiting bias", tone: "warning" },
        { label: "Target", value: latestTarget ? `${formatNumber(latestTarget.target_qty, 4)} BTC` : "No target", tone: "warning" },
      ],
    };
  }

  const targetQty = latestTarget ? Number(latestTarget.target_qty || 0) : 0;
  const transitionRisk = Number(state.transition_risk || 0);
  const riskFlags =
    latestRiskDecision && Array.isArray(latestRiskDecision.flags)
      ? latestRiskDecision.flags.filter((flag) => flag !== "OK")
      : [];

  let tone = "ok";
  if (
    marketHealth.tone === "danger"
    || String(state.quality_state) === "UNSAFE"
    || String(state.volatility_state) === "DISTORTED"
  ) {
    tone = "danger";
  } else if (
    marketHealth.tone === "warning"
    || transitionRisk >= 0.45
    || String(state.positioning_state).startsWith("CROWDED")
    || String(state.quality_state) !== "TRADABLE"
    || riskFlags.length
  ) {
    tone = "warning";
  }

  let headline = "Strategy is waiting for a cleaner edge";
  let detail = `${formatRegimeLabel(state.regime)} regime with ${humanizeCode(state.volatility_state).toLowerCase()} volatility, ${humanizeCode(state.positioning_state).toLowerCase()} positioning, and ${humanizeCode(state.quality_state).toLowerCase()} quality.`;
  let action = "The system is flat until the state improves.";
  if (playbook) {
    detail = `${detail} Active playbook: ${humanizeCode(playbook.playbook_id).toLowerCase()} (${formatPercent(playbook.playbook_confidence, 2)} confidence).`;
  }

  if (String(state.directional_state) === "UPTREND") {
    headline = "Long thesis is active";
    action = Math.abs(targetQty) > 0 ? `Current intent is ${formatNumber(targetQty, 4)} BTC on the long side.` : "The long thesis exists, but the strategy is not adding exposure yet.";
  } else if (String(state.directional_state) === "DOWNTREND") {
    headline = "Short thesis is active";
    action = Math.abs(targetQty) > 0 ? `Current intent is ${formatNumber(targetQty, 4)} BTC on the short side.` : "The short thesis exists, but the strategy is waiting for execution alignment.";
  } else if (String(state.directional_state) === "TRANSITION") {
    headline = "State is rotating between regimes";
    action = "Keep conviction low until directional structure and momentum agree again.";
  } else if (String(state.directional_state) === "NEUTRAL") {
    headline = "Strategy is preserving capital";
    action = "The engine is explicitly neutral and should only re-risk after cleaner confirmation.";
  }

  if (riskFlags.length) {
    action = `Risk overlay is active: ${riskFlags.slice(0, 2).map(humanizeCode).join(" | ")}.`;
  }

  return {
    tone,
    headline,
    detail,
    action,
    confidenceCopy: stateConfidence === null ? "-" : formatPercent(stateConfidence, 2),
    confidenceDetail: describeConfidence(stateConfidence),
    transitionCopy: formatPercent(transitionRisk, 2),
    transitionDetail: describeTransitionRisk(transitionRisk),
    transitionWidth: toPercentWidth(transitionRisk),
    chips: [
      { label: "Regime", value: formatRegimeLabel(state.regime), tone: "ok" },
      { label: "Playbook", value: playbook ? humanizeCode(playbook.playbook_id) : "Awaiting playbook", tone: playbook && playbook.playbook_id === "CAPITAL_PRESERVATION" ? "warning" : "ok" },
      { label: "Direction", value: humanizeCode(state.directional_state), tone: tone },
      { label: "Quality", value: humanizeCode(state.quality_state), tone: toneFromState("quality", state.quality_state) },
      { label: "Target", value: latestTarget ? `${formatNumber(targetQty, 4)} BTC` : "No target", tone: Math.abs(targetQty) > 0 ? "ok" : "warning" },
    ],
  };
}

function buildStateCards(state) {
  if (!state) {
    return [];
  }
  return [
    buildStateCard(
      "Directional",
      state.directional_state,
      state.directional_confidence,
      describeDirectionalState(state.directional_state),
      toneFromState("directional", state.directional_state),
    ),
    buildStateCard(
      "Volatility",
      state.volatility_state,
      state.volatility_confidence,
      describeVolatilityState(state.volatility_state),
      toneFromState("volatility", state.volatility_state),
    ),
    buildStateCard(
      "Positioning",
      state.positioning_state,
      state.positioning_confidence,
      describePositioningState(state.positioning_state),
      toneFromState("positioning", state.positioning_state),
    ),
    buildStateCard(
      "Market quality",
      state.quality_state,
      state.quality_confidence,
      describeQualityState(state.quality_state),
      toneFromState("quality", state.quality_state),
    ),
  ];
}

function buildStateCard(label, token, confidence, detail, tone) {
  return {
    label,
    value: humanizeCode(token),
    confidenceCopy: `Confidence ${formatPercent(confidence, 2)}`,
    confidenceWidth: toPercentWidth(confidence),
    detail,
    tone,
  };
}

function buildDeribitStatus({ deribitRisk, runtimeStatus, nowMs }) {
  const status = runtimeStatus && runtimeStatus.deribit ? runtimeStatus.deribit : {};
  if (!status.enabled) {
    return {
      label: "Disabled",
      tone: "warning",
      detail: "Set ZENQUANT_DERIBIT_ENABLED=1 to enable the observation layer",
      schedule: "Deribit polling is disabled for this runtime",
    };
  }
  if (!deribitRisk) {
    return {
      label: "Starting",
      tone: "warning",
      detail: status.last_error || "Waiting for the first Deribit observation pack",
      schedule: "Polls every 15 minutes without affecting strategy logic",
    };
  }
  const ageMs = Math.max(0, nowMs - Number(deribitRisk.ts_ns || 0) / 1_000_000);
  if (ageMs <= 30 * 60 * 1000) {
    return {
      label: "Live",
      tone: "ok",
      detail: `Updated ${formatRelativeTimeFromNs(deribitRisk.ts_ns, nowMs)}`,
      schedule: "Polls every 15 minutes from Deribit public endpoints",
    };
  }
  return {
    label: "Stale",
    tone: "warning",
    detail: status.last_error || `Last update ${formatRelativeTimeFromNs(deribitRisk.ts_ns, nowMs)}`,
    schedule: "Polls every 15 minutes from Deribit public endpoints",
  };
}

function buildDeribitSummary(pack) {
  if (!pack) {
    return {
      atmDetail: "30D ATM IV will appear after the first options snapshot",
      termDetail: "Waiting for near/far expiry pairs",
      termTone: "warning",
      skewDetail: "Waiting for skew proxy",
      skewTone: "warning",
      oiDetail: "Open-interest concentration unavailable",
      flowDetail: "Flow proxy unavailable",
      flowTone: "warning",
      qualityDetail: "Observation layer has not published yet",
    };
  }
  const term = Number(pack.term_structure_slope || 0);
  const skew = Number(pack.put_call_skew_25d || 0);
  const concentration = Number(pack.oi_concentration || 0);
  const flow = Number(pack.block_flow_imbalance || 0);
  return {
    atmDetail: term < 0 ? "Front IV is richer than far IV, which usually signals stress." : "Front-to-back IV is inside a normal contango shape.",
    termDetail: term < 0 ? "Inversion: near-dated IV is richer than far-dated IV." : "Positive slope: longer-dated IV remains richer than front expiry.",
    termTone: term < 0 ? "danger" : term < 0.05 ? "warning" : "ok",
    skewDetail: skew > 0 ? "Puts are richer than calls, so tail-risk demand is elevated." : "Calls are as rich as or richer than puts.",
    skewTone: skew > 5 ? "danger" : skew > 0 ? "warning" : "ok",
    oiDetail: concentration >= 0.45 ? "Open interest is concentrated in a small number of strikes." : "Open interest is reasonably distributed across strikes.",
    flowDetail: flow > 0 ? "Call-side volume proxy dominates put volume." : flow < 0 ? "Put-side volume proxy dominates call volume." : "Call and put volume proxies are balanced.",
    flowTone: Math.abs(flow) > 0.35 ? "warning" : "ok",
    qualityDetail: Array.isArray(pack.quality_flags) && pack.quality_flags.length
      ? "Quality flags describe aggregation caveats, not strategy vetoes."
      : "No Deribit quality flags reported.",
  };
}

function toPercentWidth(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "0%";
  }
  return `${Math.max(0, Math.min(100, numeric * 100))}%`;
}

function toneFromState(kind, token) {
  const value = String(token || "").toUpperCase();
  if (kind === "quality") {
    if (value === "UNSAFE" || value === "DEGRADED") {
      return "danger";
    }
    if (value === "NOISY") {
      return "warning";
    }
    return "ok";
  }
  if (kind === "volatility") {
    if (value === "DISTORTED") {
      return "danger";
    }
    if (value === "HIGH") {
      return "warning";
    }
    return "ok";
  }
  if (kind === "positioning") {
    if (value === "CROWDED_LONG" || value === "CROWDED_SHORT" || value === "UNCERTAIN") {
      return "warning";
    }
    return "ok";
  }
  if (kind === "directional" && value === "TRANSITION") {
    return "warning";
  }
  return "ok";
}

function describeDirectionalState(token) {
  const value = String(token || "").toUpperCase();
  if (value === "UPTREND") {
    return "Structure and momentum agree on upside continuation.";
  }
  if (value === "DOWNTREND") {
    return "Structure and momentum agree on downside continuation.";
  }
  if (value === "TRANSITION") {
    return "Trend structure is contested, so conviction should stay low.";
  }
  return "Directional edge is weak or intentionally flat.";
}

function describeVolatilityState(token) {
  const value = String(token || "").toUpperCase();
  if (value === "DISTORTED") {
    return "Market conditions are stressed enough to question normal execution assumptions.";
  }
  if (value === "HIGH") {
    return "Volatility is elevated, so sizing and stops deserve extra caution.";
  }
  if (value === "LOW") {
    return "Compression is building and breakout follow-through matters more than churn.";
  }
  return "Volatility is inside normal operating bounds.";
}

function describePositioningState(token) {
  const value = String(token || "").toUpperCase();
  if (value === "CROWDED_LONG") {
    return "Long positioning is crowded, so upside needs cleaner follow-through.";
  }
  if (value === "CROWDED_SHORT") {
    return "Short positioning is crowded, which raises squeeze risk.";
  }
  if (value === "UNCERTAIN") {
    return "Positioning signals are mixed and should not drive aggressive size.";
  }
  return "Positioning looks balanced enough for normal playbook behavior.";
}

function describeQualityState(token) {
  const value = String(token || "").toUpperCase();
  if (value === "UNSAFE") {
    return "Data quality is not good enough to justify new risk.";
  }
  if (value === "DEGRADED") {
    return "Inputs are degraded, so live signals deserve skepticism.";
  }
  if (value === "NOISY") {
    return "Data is usable, but the system should expect more friction and false moves.";
  }
  return "Data quality is clean enough for normal bar-close decisions.";
}

function describeConfidence(confidence) {
  const value = Number(confidence);
  if (!Number.isFinite(value)) {
    return "No confirmed confidence profile yet";
  }
  if (value >= 0.8) {
    return "Classification is stable across all four dimensions";
  }
  if (value >= 0.65) {
    return "State is usable, but one dimension is softer than the rest";
  }
  if (value >= 0.5) {
    return "Confidence is moderate, so watch for transition before adding risk";
  }
  return "Confidence is fragile and the strategy should stay cautious";
}

function describeTransitionRisk(risk) {
  const value = Number(risk);
  if (!Number.isFinite(value)) {
    return "No transition profile yet";
  }
  if (value >= 0.6) {
    return "The state is unstable and could flip on the next confirmed bar";
  }
  if (value >= 0.35) {
    return "One dimension is soft enough that the current thesis needs monitoring";
  }
  return "The current state is behaving consistently across dimensions";
}
