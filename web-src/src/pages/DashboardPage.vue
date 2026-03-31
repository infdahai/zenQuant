<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { NTag, NButton, NSpace, NGrid, NGridItem, NCollapse, NCollapseItem } from "naive-ui";
import { useMarketStore } from "../stores/market";
import Panel from "../components/panels/Panel.vue";
import MetricCell from "../components/panels/MetricCell.vue";
import PriceChart from "../components/charts/PriceChart.vue";
import EquityChart from "../components/charts/EquityChart.vue";
import IndicatorChart from "../components/charts/IndicatorChart.vue";
import {
  collectIndicatorKeys,
  formatBiasLabel,
  formatCodes,
  formatCurrency,
  formatDisplayValue,
  formatNumber,
  formatPercent,
  formatRegimeLabel,
  formatRelativeTimeFromNs,
  formatSignedCurrency,
  formatSignedPercent,
  getMarketHealth,
  humanizeCode,
  normalizeIndicatorSeries,
  type Tone,
} from "../utils/format";
import {
  averageStateConfidence,
  buildStateCards,
  buildThesisSummary,
  toPercentWidth,
} from "../utils/tone";

const store = useMarketStore();
const nowMs = ref(Date.now());
const expandMode = ref(loadExpandMode());
const expanded = reactive<Record<string, boolean>>({});

let timer: ReturnType<typeof setInterval>;
onMounted(() => {
  timer = setInterval(() => (nowMs.value = Date.now()), 1000);
});
onBeforeUnmount(() => clearInterval(timer));

// ── Computed projections ──
const snapshot = computed(() => (store.snapshot as Record<string, unknown>) || {});
const indicator = computed(() => store.latestIndicator || (snapshot.value.latest_indicator as Record<string, unknown>) || null);
const directionalBias = computed(() => (snapshot.value.directional_bias as Record<string, unknown>) || null);
const directionalBiasMeta = computed(() => (snapshot.value.directional_bias_meta as Record<string, unknown>) || null);
const marketState = computed(() => (snapshot.value.market_state as Record<string, unknown>) || null);
const activePlaybook = computed(() => (snapshot.value.active_playbook as Record<string, unknown>) || null);
const latestSizing = computed(() => (snapshot.value.latest_sizing as Record<string, unknown>) || null);
const latestExit = computed(() => (snapshot.value.latest_exit as Record<string, unknown>) || null);
const deribitRisk = computed(() => (snapshot.value.deribit_risk as Record<string, unknown>) || null);
const deribitHistory = computed(() => ((snapshot.value.deribit_history as Record<string, unknown>[]) || []).slice(-96));
const positionState = computed(() => (snapshot.value.position_state as Record<string, unknown>) || null);
const latestTarget = computed(() => (snapshot.value.latest_target as Record<string, unknown>) || null);
const latestReason = computed(() => {
  const reasons = (snapshot.value.reasons as Record<string, unknown>[]) || [];
  return reasons[0] || null;
});
const reports = computed(() => (snapshot.value.reports as Record<string, unknown>[]) || []);
const latestReport = computed(() => reports.value[0] || null);
const riskSnapshot = computed(() => (snapshot.value.risk_snapshot as Record<string, unknown>) || null);
const latestRiskDecision = computed(() => (snapshot.value.risk_decision as Record<string, unknown>) || null);
const runtimeStatus = computed(() => (snapshot.value.runtime_status as Record<string, unknown>) || {});
const marketHealth = computed(() => getMarketHealth(runtimeStatus.value, nowMs.value));
const stateConfidence = computed(() => averageStateConfidence(marketState.value));
const candles = computed(() => ((snapshot.value.candles as Record<string, unknown>[]) || []).slice(-180));
const priceSeries = computed(() => ((snapshot.value.price_series as Record<string, unknown>[]) || []).slice(-300));
const equityCurve = computed(() => ((snapshot.value.equity_curve as Record<string, unknown>[]) || []).map((item) => Number((item as Record<string, unknown>).equity)));
const pnlSummary = computed(() => (snapshot.value.pnl_summary as Record<string, unknown>) || null);
const alerts = computed(() => ((snapshot.value.alerts as Record<string, unknown>[]) || []).slice(0, 6));
const proxyLatest = computed(() => (snapshot.value.proxy_indicator_latest as Record<string, unknown>) || {});
const proxyHistory = computed(() => (snapshot.value.proxy_indicator_history as Record<string, Record<string, unknown>[]>) || {});

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

const primaryRiskTone = computed<Tone>(() => {
  const flags = latestRiskDecision.value?.flags;
  if (!Array.isArray(flags)) return "ok";
  if (flags.some((f: unknown) => ["KILL_SWITCH", "DD_STEP_3", "SEVERE_CROWDING", "VOL_SHOCK"].includes(String(f)))) return "danger";
  if (flags.some((f: unknown) => String(f) !== "OK")) return "warning";
  return "ok";
});

// ── Position summary ──
const positionSummary = computed(() => {
  const position = positionState.value;
  const lastPrice = Number(snapshot.value.last_price || 0);
  if (!position) return { hasPosition: false, headline: "Flat", detail: "No open position", pnl: "", tone: "ok" as Tone, ageCopy: "Awaiting entry", stopDistanceCopy: "No trailing stop", entryCopy: "-", qtyCopy: "-" };

  const direction = Number(position.direction) > 0 ? "Long" : "Short";
  const entryPrice = Number(position.entry_price || 0);
  const unrealizedPnlPct = entryPrice > 0 && lastPrice > 0 ? ((lastPrice - entryPrice) / entryPrice) * Number(position.direction) : null;
  let stopDetail = "Trailing stop not set";
  let tone: Tone = "ok";
  let stopDistanceCopy = "Trailing stop not set";

  if (Number(position.trailing_stop) > 0 && lastPrice > 0) {
    const distance = Math.abs((lastPrice - Number(position.trailing_stop)) / lastPrice);
    const relation = Number(position.direction) > 0 ? "below last" : "above last";
    if (distance < 0.02) tone = "danger";
    else if (distance < 0.05) tone = "warning";
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

// ── Sizing summary ──
const sizingSummary = computed(() => {
  const sizing = latestSizing.value;
  if (!sizing) return { headline: "Awaiting sizing", formula: "-", reasons: "No sizing reason codes yet" };
  return {
    headline: `${formatNumber(sizing.final_leverage, 2)}x leverage`,
    formula: `${formatNumber(sizing.base_size, 2)} x ${formatNumber(sizing.confidence_multiplier, 2)} x ${formatNumber(sizing.stress_discount, 2)} x ${formatNumber(sizing.execution_haircut, 2)} = ${formatNumber(sizing.final_leverage, 2)}`,
    reasons: formatCodes(sizing.reason_codes as string[], 4),
  };
});

// ── Exit summary ──
const exitSummary = computed(() => {
  const exit = latestExit.value;
  if (!exit || exit.exit_type === "NONE") return { headline: "No active exit", detail: "Position remains valid", tone: "ok" as Tone, reductionCopy: "0%", stopCopy: "-", reasonCopy: "No active exit reasons" };
  const detail = [];
  if (Number.isFinite(Number(exit.stop_price)) && Number(exit.stop_price) > 0) detail.push(`Stop ${formatCurrency(exit.stop_price, 2)}`);
  detail.push(humanizeCode(exit.reason_code));
  return {
    headline: `${humanizeCode(exit.exit_type)} · ${humanizeCode(exit.exit_urgency)}`,
    detail: detail.join(" | "),
    tone: (exit.exit_urgency === "IMMEDIATE" ? "danger" : "warning") as Tone,
    reductionCopy: formatPercent(exit.target_reduction_pct, 0),
    stopCopy: Number.isFinite(Number(exit.stop_price)) && Number(exit.stop_price) > 0 ? formatCurrency(exit.stop_price, 2) : "-",
    reasonCopy: formatCodes(exit.reason_codes as string[], 5),
  };
});

// ── Bias summary ──
const biasSummary = computed(() => {
  const bias = directionalBias.value;
  const meta = directionalBiasMeta.value;
  if (!bias) return { headline: "NO SIGNAL", detail: marketHealth.value.tone === "ok" ? "Awaiting 1D bias" : marketHealth.value.detail };
  const parts = [];
  if (meta?.is_stale) parts.push(`Stale (${formatNumber(meta.age_4h_bars, 1)} bars old)`);
  else parts.push(`Confidence ${formatNumber(bias.confidence, 2)}`);
  if (meta?.quality_flags && (meta.quality_flags as string[]).length) parts.push((meta.quality_flags as string[]).map(humanizeCode).join(", "));
  return { headline: formatBiasLabel(bias.direction, true), detail: parts.join(" | ") };
});

// ── Database status ──
const databaseStatus = computed(() => {
  const database = (runtimeStatus.value.database as Record<string, unknown>) || {};
  if (!database.enabled) return { label: "Not configured", detail: "PostgreSQL audit store is optional", tone: "warning" as Tone };
  if (!database.connected) return { label: "Offline", detail: (database.last_error as string) || "Audit writes paused", tone: "danger" as Tone };
  return { label: "Online", detail: `${formatNumber(database.writes || 0, 0)} writes · queue ${formatNumber(database.queue_depth || 0, 0)}`, tone: "ok" as Tone };
});

// ── Indicator cards ──
const indicatorKeys = computed(() => collectIndicatorKeys(indicator.value, proxyLatest.value));

const indicatorCards = computed(() => {
  const history = store.indicatorHistory;
  return indicatorKeys.value.map((key) => ({
    key,
    value: indicator.value && (indicator.value as Record<string, Record<string, unknown>>).values?.[key] !== undefined
      ? (indicator.value as Record<string, Record<string, unknown>>).values[key]
      : (proxyLatest.value as Record<string, unknown>)[key],
    series: selectSeries(key, history, proxyHistory.value, priceSeries.value),
  }));
});

function selectSeries(key: string, indicatorHistory: Record<string, unknown>[], proxySeriesMap: Record<string, Record<string, unknown>[]>, pricePoints: Record<string, unknown>[]) {
  if (indicatorHistory?.length > 0) {
    const series = normalizeIndicatorSeries(key, indicatorHistory, store.categoryMaps);
    if (series.some((item) => Number.isFinite(item) && item !== 0)) return series;
  }
  if (key === "last_price" && pricePoints?.length > 0) return pricePoints.map((item) => Number((item as Record<string, unknown>).price));
  const proxySeries = proxySeriesMap[key];
  if (Array.isArray(proxySeries) && proxySeries.length > 0) return proxySeries.map((item) => Number((item as Record<string, unknown>).value));
  return [];
}

function isExpanded(key: string) {
  if (expanded[key] !== undefined) return expanded[key];
  if (expandMode.value === "all") return true;
  if (expandMode.value === "none") return false;
  return ["last_price", "realized_vol", "cvd_slope", "zen_pulse", "structure_state"].includes(key);
}

function toggleExpand(key: string) { expanded[key] = !isExpanded(key); }

function setExpandMode(mode: string) {
  expandMode.value = mode;
  for (const key of Object.keys(expanded)) delete expanded[key];
  persistExpandMode(mode);
}

function loadExpandMode() {
  return localStorage.getItem("zenquant.expandMode") || "key";
}
function persistExpandMode(mode: string) {
  localStorage.setItem("zenquant.expandMode", mode);
}

function seriesStats(values: number[]) {
  const numeric = values.filter(Number.isFinite);
  if (!numeric.length) return { min: "-", max: "-", latest: "-" };
  return { min: formatNumber(Math.min(...numeric), 4), max: formatNumber(Math.max(...numeric), 4), latest: formatNumber(numeric[numeric.length - 1], 4) };
}

function toneColor(tone: string) {
  if (tone === "danger") return "#ff4757";
  if (tone === "warning") return "#f5a524";
  return "#14d9b4";
}

const deribitIvSeries = computed(() => deribitHistory.value.map((item) => Number((item as Record<string, unknown>).atm_iv_30d || 0)));
</script>

<template>
  <div class="dashboard">
    <!-- Thesis Panel -->
    <Panel title="Current Thesis">
      <div class="thesis-block" :class="`tone-${thesisSummary.tone}`">
        <div class="thesis-main">
          <span class="thesis-kicker">Operator read</span>
          <h2>{{ thesisSummary.headline }}</h2>
          <p>{{ thesisSummary.detail }}</p>
          <p class="thesis-action">{{ thesisSummary.action }}</p>
          <div class="chip-row">
            <NTag
              v-for="chip in thesisSummary.chips"
              :key="chip.label"
              :bordered="false"
              size="small"
              :color="{ color: toneColor(chip.tone) + '18', textColor: toneColor(chip.tone) }"
            >
              {{ chip.label }}: {{ chip.value }}
            </NTag>
          </div>
        </div>
        <div class="thesis-side">
          <div class="thesis-stat">
            <span>Average confidence</span>
            <strong>{{ thesisSummary.confidenceCopy }}</strong>
            <small>{{ thesisSummary.confidenceDetail }}</small>
          </div>
          <div class="thesis-stat">
            <span>Transition risk</span>
            <strong>{{ thesisSummary.transitionCopy }}</strong>
            <div class="bar-track">
              <div class="bar-fill" :class="`fill-${thesisSummary.tone}`" :style="{ width: thesisSummary.transitionWidth }" />
            </div>
            <small>{{ thesisSummary.transitionDetail }}</small>
          </div>
        </div>
      </div>
    </Panel>

    <!-- Live Overview -->
    <Panel title="Live Overview">
      <div class="grid-4">
        <MetricCell label="Data Stream" :value="marketHealth.label" :detail="marketHealth.detail" :tone="marketHealth.tone" test-id="stream-status" />
        <MetricCell label="Position" :value="positionSummary.headline" :detail="positionSummary.detail" :tone="positionSummary.tone" test-id="position-status">
          <small v-if="positionSummary.pnl" class="pnl-sub" data-testid="position-pnl">{{ positionSummary.pnl }}</small>
        </MetricCell>
        <MetricCell label="Total PnL" :value="pnlSummary ? formatSignedCurrency(pnlSummary.pnl_abs, 2) : '-'" :detail="pnlSummary ? formatPercent(pnlSummary.pnl_pct, 2) + ' from start' : 'Waiting for equity baseline'" :tone="primaryRiskTone" />
        <MetricCell label="Equity" :value="riskSnapshot ? formatCurrency(riskSnapshot.equity, 2) : '-'" :detail="riskSnapshot ? 'Drawdown ' + formatPercent(riskSnapshot.drawdown, 2) : 'No risk snapshot yet'" />
      </div>
    </Panel>

    <!-- Decision Path -->
    <Panel title="Decision Path">
      <div class="grid-decision">
        <MetricCell label="1D bias" :value="biasSummary.headline" :detail="biasSummary.detail" />
        <MetricCell label="Bias regime" :value="directionalBias ? formatRegimeLabel(directionalBias.regime) : 'Awaiting regime'" :detail="directionalBias ? 'Updated ' + formatRelativeTimeFromNs(directionalBias.cutoff_time_ns) : 'No 1D regime yet'" />
        <MetricCell label="Data stream" :value="marketHealth.label" :detail="marketHealth.detail" :tone="marketHealth.tone" test-id="stream-update" />
        <MetricCell label="Database" :value="databaseStatus.label" :detail="databaseStatus.detail" :tone="databaseStatus.tone" />
        <MetricCell label="Active playbook" :value="activePlaybook ? humanizeCode(activePlaybook.playbook_id) : 'Awaiting'" :detail="activePlaybook ? 'Confidence ' + formatPercent(activePlaybook.playbook_confidence, 2) : 'Playbook selection publishes after state'" />
        <MetricCell label="Latest target" :value="latestTarget ? formatNumber(latestTarget.target_qty, 4) : '-'" :detail="latestTarget ? formatCodes(latestTarget.reason_codes as string[]) : 'No target yet'" />
        <MetricCell label="Sizing engine" :value="sizingSummary.headline" :detail="sizingSummary.reasons" />
        <MetricCell label="Exit monitor" :value="exitSummary.headline" :detail="exitSummary.detail" :tone="exitSummary.tone" test-id="exit-decision" />
        <MetricCell label="Risk flags" :value="latestRiskDecision?.flags ? formatCodes(latestRiskDecision.flags as string[], 2) : 'Normal'" :detail="latestRiskDecision ? 'Leverage cap ' + formatNumber(latestRiskDecision.lev_cap, 2) : 'No risk decision'" :tone="primaryRiskTone" />
        <MetricCell label="Last execution" :value="latestReport ? String(latestReport.status) : 'No fills yet'" :detail="latestReport ? formatNumber(latestReport.filled_qty, 4) + ' @ ' + formatCurrency(latestReport.avg_price, 2) : 'Execution log empty'" />
        <MetricCell label="Latest reason" :value="latestReason ? String(latestReason.summary) : 'Waiting for strategy reason'" :detail="latestReason?.reason_codes ? formatCodes(latestReason.reason_codes as string[], 4) : 'No reason codes'" wide />
      </div>
    </Panel>

    <!-- Charts row -->
    <div class="grid-2">
      <Panel title="Price Curve">
        <PriceChart :candles="candles" :position="positionState" :height="290" />
        <div class="grid-2 mt-8" data-testid="price-overlay-legend">
          <MetricCell
            v-if="positionState && Number(positionState.entry_price) > 0"
            :label="Number(positionState.direction) >= 0 ? 'Long entry' : 'Short entry'"
            :value="formatCurrency(positionState.entry_price, 2)"
            :detail="'Position ' + formatNumber(positionState.current_qty || 0, 4) + ' BTC'"
          />
          <MetricCell
            v-if="positionState && Number(positionState.trailing_stop) > 0"
            label="Trailing stop"
            :value="formatCurrency(positionState.trailing_stop, 2)"
            detail="Current invalidation level"
            test-id="trailing-stop"
          />
          <MetricCell
            v-if="!positionState"
            label="No active overlays"
            value="-"
            detail="Entry and trailing stop lines appear here with a position"
          />
        </div>
      </Panel>
      <Panel title="Equity Curve">
        <EquityChart :values="equityCurve" color="#14d9b4" :height="290" />
      </Panel>
    </div>

    <!-- Exit & Position + Sizing -->
    <div class="grid-2">
      <Panel title="Exit & Position">
        <div class="grid-decision-sm">
          <MetricCell label="Position status" :value="positionSummary.headline" :detail="positionSummary.detail" :tone="positionSummary.tone" wide />
          <MetricCell label="Unrealized PnL" :value="positionSummary.pnl || '-'" :detail="positionSummary.ageCopy" :tone="positionSummary.tone" />
          <MetricCell label="Entry price" :value="positionSummary.entryCopy" :detail="positionSummary.hasPosition ? 'Qty ' + positionSummary.qtyCopy + ' BTC' : 'No filled position'" />
          <MetricCell label="Exit decision" :value="exitSummary.headline" :detail="exitSummary.detail" :tone="exitSummary.tone" />
          <MetricCell label="Target reduction" :value="exitSummary.reductionCopy" :detail="exitSummary.reasonCopy" test-id="target-reduction" />
        </div>
      </Panel>
      <Panel title="Sizing Breakdown">
        <div class="grid-decision-sm">
          <MetricCell label="Final leverage" :value="latestSizing ? formatNumber(latestSizing.final_leverage, 2) + 'x' : '-'" :detail="sizingSummary.reasons" />
          <MetricCell label="Base size" :value="latestSizing ? formatNumber(latestSizing.base_size, 3) : '-'" detail="Vol-targeted budget" />
          <MetricCell label="Confidence" :value="latestSizing ? formatNumber(latestSizing.confidence_multiplier, 3) : '-'" detail="State/playbook blend" />
          <MetricCell label="Stress discount" :value="latestSizing ? formatNumber(latestSizing.stress_discount, 3) : '-'" detail="Positioning + vol + quality" />
          <MetricCell label="Execution haircut" :value="latestSizing ? formatNumber(latestSizing.execution_haircut, 3) : '-'" detail="Tradability filter" />
          <MetricCell label="Formula" :value="sizingSummary.formula" wide />
        </div>
      </Panel>
    </div>

    <!-- 4D State -->
    <Panel title="4D State">
      <div v-if="marketState" class="grid-4">
        <MetricCell
          v-for="card in stateCards"
          :key="card.label"
          :label="card.label"
          :value="card.value"
          :tone="card.tone"
        >
          <div class="bar-track">
            <div class="bar-fill" :class="`fill-${card.tone}`" :style="{ width: card.confidenceWidth }" />
          </div>
          <small class="metric-detail">{{ card.confidenceCopy }}</small>
          <small class="metric-detail">{{ card.detail }}</small>
        </MetricCell>
      </div>
      <div v-else class="empty-state">
        State engine is still warming. The dashboard publishes 4D state after a confirmed bar-close.
      </div>
    </Panel>

    <!-- Alerts + Execution -->
    <div class="grid-2">
      <Panel title="Alert Panel">
        <div class="event-list">
          <div v-if="!alerts.length" class="event-row">No active alerts</div>
          <div v-for="(alert, i) in alerts" :key="'a' + i" class="event-row" :class="'event-' + (Array.isArray((alert as Record<string,unknown>).flags) && ((alert as Record<string,unknown>).flags as string[]).some(f => ['KILL_SWITCH','DD_STEP_3'].includes(f)) ? 'danger' : 'warning')">
            <strong>{{ humanizeCode((alert as Record<string,unknown>).type) }}</strong>
            <small>{{ formatRelativeTimeFromNs((alert as Record<string,unknown>).ts_ns) }}</small>
          </div>
        </div>
      </Panel>
      <Panel title="Execution Log">
        <div class="event-list">
          <div v-if="!reports.length" class="event-row">No executions yet</div>
          <div v-for="(report, i) in reports.slice(0, 6)" :key="'r' + i" class="event-row">
            <strong>{{ (report as Record<string,unknown>).status }} · {{ Number((report as Record<string,unknown>).side) > 0 ? 'Buy' : 'Sell' }} · {{ formatNumber((report as Record<string,unknown>).filled_qty, 4) }}</strong>
            <small>{{ formatCurrency((report as Record<string,unknown>).avg_price, 2) }} · fee {{ formatCurrency((report as Record<string,unknown>).fee, 4) }}</small>
          </div>
        </div>
      </Panel>
    </div>

    <!-- Indicator Drilldown -->
    <Panel title="Indicator Drilldown">
      <NSpace :size="4" style="margin-bottom: 10px">
        <NButton size="tiny" :type="expandMode === 'all' ? 'primary' : 'default'" @click="setExpandMode('all')">Expand All</NButton>
        <NButton size="tiny" :type="expandMode === 'key' ? 'primary' : 'default'" @click="setExpandMode('key')">Key</NButton>
        <NButton size="tiny" :type="expandMode === 'none' ? 'primary' : 'default'" @click="setExpandMode('none')">Collapse</NButton>
      </NSpace>
    </Panel>

    <Panel v-for="item in indicatorCards" :key="item.key">
      <div class="indicator-head" @click="toggleExpand(item.key)">
        <span class="indicator-toggle">{{ isExpanded(item.key) ? '▾' : '▸' }}</span>
        <router-link class="indicator-name" :to="'/indicator/' + encodeURIComponent(item.key)">{{ item.key }}</router-link>
        <span class="indicator-value">{{ formatDisplayValue(item.value) }}</span>
      </div>
      <div v-show="isExpanded(item.key)" class="indicator-body">
        <IndicatorChart :values="item.series" :height="140" />
        <div class="stats-row">
          <span>min {{ seriesStats(item.series).min }}</span>
          <span>max {{ seriesStats(item.series).max }}</span>
          <span>latest {{ seriesStats(item.series).latest }}</span>
        </div>
      </div>
    </Panel>
  </div>
</template>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.grid-4 {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}

.grid-decision {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 6px;
}

.grid-decision-sm {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
}

@media (max-width: 1200px) {
  .grid-4 { grid-template-columns: repeat(2, 1fr); }
  .grid-decision { grid-template-columns: repeat(3, 1fr); }
}

@media (max-width: 800px) {
  .grid-2 { grid-template-columns: 1fr; }
  .grid-4 { grid-template-columns: 1fr; }
  .grid-decision { grid-template-columns: repeat(2, 1fr); }
  .grid-decision-sm { grid-template-columns: repeat(2, 1fr); }
}

.mt-8 { margin-top: 8px; }

/* Thesis block */
.thesis-block {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 16px;
  padding: 12px;
  border-radius: 6px;
  background: rgba(16, 22, 32, 0.6);
  border: 1px solid rgba(55, 90, 120, 0.12);
}

.thesis-block.tone-danger { border-color: rgba(255, 71, 87, 0.25); }
.thesis-block.tone-warning { border-color: rgba(245, 165, 36, 0.25); }
.thesis-block.tone-ok { border-color: rgba(20, 217, 180, 0.25); }

.thesis-kicker {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #6b7f94;
}

.thesis-main h2 {
  font-size: 16px;
  font-weight: 600;
  margin: 4px 0;
}

.thesis-main p {
  font-size: 12px;
  color: #a0b4c8;
  line-height: 1.4;
}

.thesis-action {
  margin-top: 4px;
  font-weight: 500;
  color: #e0e6ed !important;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.thesis-side {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.thesis-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.thesis-stat span {
  font-size: 10px;
  text-transform: uppercase;
  color: #6b7f94;
}

.thesis-stat strong {
  font-family: "JetBrains Mono", monospace;
  font-size: 14px;
}

.thesis-stat small {
  font-size: 11px;
  color: #6b7f94;
}

/* Confidence / progress bars */
.bar-track {
  width: 100%;
  height: 4px;
  background: rgba(55, 90, 120, 0.15);
  border-radius: 2px;
  margin: 4px 0;
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.3s ease;
}

.fill-ok { background: #14d9b4; }
.fill-warning { background: #f5a524; }
.fill-danger { background: #ff4757; }

/* Event list */
.event-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.event-row {
  padding: 6px 8px;
  border-radius: 4px;
  background: rgba(16, 22, 32, 0.4);
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.event-row strong {
  color: #e0e6ed;
  font-size: 12px;
}

.event-row small {
  color: #6b7f94;
  font-size: 11px;
}

.event-danger { border-left: 2px solid #ff4757; }
.event-warning { border-left: 2px solid #f5a524; }

.empty-state {
  text-align: center;
  padding: 20px;
  color: #6b7f94;
  font-size: 12px;
}

/* Indicator items */
.indicator-head {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
}

.indicator-toggle {
  font-size: 12px;
  color: #6b7f94;
  width: 14px;
}

.indicator-name {
  color: #a0b4c8;
  text-decoration: none;
  font-family: "JetBrains Mono", monospace;
  font-size: 12px;
  flex: 1;
}

.indicator-name:hover {
  color: #14d9b4;
}

.indicator-value {
  font-family: "JetBrains Mono", monospace;
  font-size: 13px;
  color: #e0e6ed;
}

.indicator-body {
  margin-top: 8px;
}

.stats-row {
  display: flex;
  gap: 16px;
  font-size: 11px;
  color: #6b7f94;
  font-family: "JetBrains Mono", monospace;
  margin-top: 4px;
}

.pnl-sub {
  font-size: 11px;
  color: #a0b4c8;
}
</style>
