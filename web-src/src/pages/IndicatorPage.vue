<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useMarketStore } from "../stores/market";
import Panel from "../components/panels/Panel.vue";
import PriceChart from "../components/charts/PriceChart.vue";
import IndicatorChart from "../components/charts/IndicatorChart.vue";
import {
  formatDisplayValue,
  formatNumber,
  normalizeIndicatorSeries,
} from "../utils/format";

const route = useRoute();
const store = useMarketStore();

const key = computed(() => String(route.params.key || ""));
const snapshot = computed(
  () => (store.snapshot as Record<string, unknown>) || {},
);
const indicator = computed(
  () =>
    store.latestIndicator ||
    (snapshot.value.latest_indicator as Record<string, unknown>) ||
    null,
);
const candles = computed(
  () =>
    (
      (snapshot.value.candles as Record<string, unknown>[]) || []
    ).slice(-180),
);

const currentValue = computed(() => {
  if (!indicator.value) return null;
  const values = (indicator.value as Record<string, Record<string, unknown>>)
    .values;
  return values ? values[key.value] : null;
});

const series = computed(() => {
  const history = store.indicatorHistory;
  if (!history.length) return [];
  return normalizeIndicatorSeries(key.value, history, store.categoryMaps);
});

function seriesStats(values: number[]) {
  const numeric = values.filter(Number.isFinite);
  if (!numeric.length) return { min: "-", max: "-", latest: "-" };
  return {
    min: formatNumber(Math.min(...numeric), 4),
    max: formatNumber(Math.max(...numeric), 4),
    latest: formatNumber(numeric[numeric.length - 1], 4),
  };
}
</script>

<template>
  <div class="indicator-page">
    <Panel :title="key">
      <div class="indicator-header">
        <span class="indicator-key">{{ key }}</span>
        <span class="indicator-current">{{
          formatDisplayValue(currentValue)
        }}</span>
      </div>
    </Panel>

    <Panel title="Price Context">
      <PriceChart :candles="candles" :height="240" />
    </Panel>

    <Panel title="Indicator History">
      <IndicatorChart :values="series" :height="200" color="#4a90d9" />
      <div class="stats-row">
        <span>min {{ seriesStats(series).min }}</span>
        <span>max {{ seriesStats(series).max }}</span>
        <span>latest {{ seriesStats(series).latest }}</span>
      </div>
    </Panel>
  </div>
</template>

<style scoped>
.indicator-page {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.indicator-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.indicator-key {
  font-family: "JetBrains Mono", monospace;
  font-size: 16px;
  color: #a0b4c8;
}

.indicator-current {
  font-family: "JetBrains Mono", monospace;
  font-size: 20px;
  font-weight: 600;
  color: #e0e6ed;
}

.stats-row {
  display: flex;
  gap: 16px;
  font-size: 11px;
  color: #6b7f94;
  font-family: "JetBrains Mono", monospace;
  margin-top: 8px;
}
</style>
