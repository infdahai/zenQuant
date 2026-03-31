<script setup lang="ts">
import { NTag, NSpace, NSelect } from "naive-ui";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useMarketStore } from "../../stores/market";
import {
  formatCurrency,
  formatNumber,
  formatPercent,
  formatRegimeLabel,
  getMarketHealth,
  humanizeCode,
} from "../../utils/format";

const store = useMarketStore();
const nowMs = ref(Date.now());

let timer: ReturnType<typeof setInterval>;
onMounted(() => {
  timer = setInterval(() => (nowMs.value = Date.now()), 1000);
});
onBeforeUnmount(() => clearInterval(timer));

const snapshot = computed(
  () => (store.snapshot as Record<string, unknown>) || {},
);
const runtimeStatus = computed(
  () => (snapshot.value.runtime_status as Record<string, unknown>) || {},
);
const health = computed(() => getMarketHealth(runtimeStatus.value, nowMs.value));
const positionState = computed(
  () => (snapshot.value.position_state as Record<string, unknown>) || null,
);
const riskSnapshot = computed(
  () => (snapshot.value.risk_snapshot as Record<string, unknown>) || null,
);
const marketState = computed(
  () => (snapshot.value.market_state as Record<string, unknown>) || null,
);
const riskDecision = computed(
  () => (snapshot.value.risk_decision as Record<string, unknown>) || null,
);

const lastPrice = computed(() => {
  const p = Number(snapshot.value.last_price || 0);
  return p > 0 ? formatCurrency(p, 2) : "-";
});

const regimeLabel = computed(() =>
  marketState.value
    ? formatRegimeLabel(marketState.value.regime)
    : "Warming",
);

const positionLabel = computed(() => {
  if (!positionState.value) return "Flat";
  const dir =
    Number(positionState.value.direction) > 0 ? "Long" : "Short";
  return `${dir} ${formatNumber(positionState.value.current_qty, 4)}`;
});

const equityLabel = computed(() =>
  riskSnapshot.value
    ? formatCurrency(riskSnapshot.value.equity, 2)
    : "-",
);

const drawdownLabel = computed(() =>
  riskSnapshot.value
    ? formatPercent(riskSnapshot.value.drawdown, 2)
    : "-",
);

const levCapLabel = computed(() =>
  riskDecision.value
    ? `${formatNumber(riskDecision.value.lev_cap, 1)}x`
    : "-",
);

const riskTone = computed(() => {
  const flags = riskDecision.value?.flags;
  if (!Array.isArray(flags)) return "ok";
  if (
    flags.some((f: unknown) =>
      ["KILL_SWITCH", "DD_STEP_3", "SEVERE_CROWDING", "VOL_SHOCK"].includes(
        String(f),
      ),
    )
  )
    return "danger";
  if (flags.some((f: unknown) => String(f) !== "OK")) return "warning";
  return "ok";
});

const riskLabel = computed(() => {
  const flags = riskDecision.value?.flags;
  if (!Array.isArray(flags)) return "OK";
  const active = flags.filter((f: unknown) => String(f) !== "OK");
  if (!active.length) return "OK";
  return active
    .slice(0, 2)
    .map((f: unknown) => humanizeCode(f))
    .join(" | ");
});

const symbolOptions = [
  { label: "BTCUSDT_PERP", value: "BTCUSDT_PERP" },
  { label: "ETHUSDT_PERP", value: "ETHUSDT_PERP" },
];

const tfOptions = [
  { label: "4H", value: "4h" },
  { label: "1D", value: "1d" },
];

function onSymbolChange(val: string) {
  store.setSelection(val, store.tf);
}
function onTfChange(val: string) {
  store.setSelection(store.symbol, val);
}

function toneColor(tone: string) {
  if (tone === "danger") return "#ff4757";
  if (tone === "warning") return "#f5a524";
  return "#14d9b4";
}
</script>

<template>
  <header class="status-bar">
    <div class="status-bar-inner">
      <div class="status-left">
        <NTag
          :bordered="false"
          size="small"
          :color="{
            color: toneColor(health.tone) + '18',
            textColor: toneColor(health.tone),
          }"
        >
          {{ store.lastError ? "ERROR" : health.label }}
        </NTag>
        <span class="status-price">BTC {{ lastPrice }}</span>
        <span class="status-divider">|</span>
        <span class="status-item">Regime: <strong>{{ regimeLabel }}</strong></span>
        <span class="status-divider">|</span>
        <span class="status-item">{{ positionLabel }}</span>
        <span class="status-divider">|</span>
        <span class="status-item">Equity: <strong>{{ equityLabel }}</strong></span>
        <span class="status-divider">|</span>
        <span class="status-item">DD: <strong>{{ drawdownLabel }}</strong></span>
        <span class="status-divider">|</span>
        <span class="status-item">Lev Cap: <strong>{{ levCapLabel }}</strong></span>
        <span class="status-divider">|</span>
        <span class="status-item" :style="{ color: toneColor(riskTone) }">
          Risk: <strong>{{ riskLabel }}</strong>
        </span>
      </div>
      <div class="status-right">
        <NSelect
          :value="store.symbol"
          :options="symbolOptions"
          size="tiny"
          :style="{ width: '140px' }"
          @update:value="onSymbolChange"
        />
        <NSelect
          :value="store.tf"
          :options="tfOptions"
          size="tiny"
          :style="{ width: '70px' }"
          @update:value="onTfChange"
        />
      </div>
    </div>
  </header>
</template>

<style scoped>
.status-bar {
  position: sticky;
  top: 0;
  z-index: 100;
  background: rgba(10, 14, 20, 0.95);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid rgba(55, 90, 120, 0.2);
}

.status-bar-inner {
  max-width: 1600px;
  margin: 0 auto;
  padding: 6px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.status-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #a0b4c8;
}

.status-price {
  font-family: "JetBrains Mono", "Fira Code", monospace;
  font-weight: 600;
  color: #e0e6ed;
  font-size: 13px;
}

.status-divider {
  color: rgba(55, 90, 120, 0.4);
}

.status-item strong {
  color: #e0e6ed;
}

.status-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
</style>
