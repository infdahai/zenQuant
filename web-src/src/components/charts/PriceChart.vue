<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch, type PropType } from "vue";
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  ColorType,
  CrosshairMode,
  LineStyle,
} from "lightweight-charts";
import { colors } from "../../theme/dark";

const props = defineProps({
  candles: { type: Array as PropType<Record<string, unknown>[]>, default: () => [] },
  position: { type: Object as PropType<Record<string, unknown> | null>, default: null },
  height: { type: Number, default: 320 },
});

const chartContainer = ref<HTMLDivElement>();
let chart: IChartApi | null = null;
let candleSeries: ISeriesApi<"Candlestick"> | null = null;
let entryLine: ISeriesApi<"Line"> | null = null;
let stopLine: ISeriesApi<"Line"> | null = null;

onMounted(() => {
  if (!chartContainer.value) return;
  chart = createChart(chartContainer.value, {
    width: chartContainer.value.clientWidth,
    height: props.height,
    layout: {
      background: { type: ColorType.Solid, color: "#111820" },
      textColor: "#6b7f94",
      fontSize: 11,
      fontFamily: "'JetBrains Mono', monospace",
    },
    grid: {
      vertLines: { color: "rgba(55,90,120,0.1)" },
      horzLines: { color: "rgba(55,90,120,0.1)" },
    },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { borderColor: "rgba(55,90,120,0.25)" },
    timeScale: { borderColor: "rgba(55,90,120,0.25)", timeVisible: true },
  });

  candleSeries = chart.addCandlestickSeries({
    upColor: colors.candleUp,
    downColor: colors.candleDown,
    borderUpColor: colors.candleUp,
    borderDownColor: colors.candleDown,
    wickUpColor: colors.candleUp,
    wickDownColor: colors.candleDown,
  });

  entryLine = chart.addLineSeries({
    color: colors.ok,
    lineWidth: 1,
    lineStyle: LineStyle.Dashed,
    crosshairMarkerVisible: false,
    priceLineVisible: false,
    lastValueVisible: false,
  });

  stopLine = chart.addLineSeries({
    color: colors.danger,
    lineWidth: 1,
    lineStyle: LineStyle.Dashed,
    crosshairMarkerVisible: false,
    priceLineVisible: false,
    lastValueVisible: false,
  });

  const observer = new ResizeObserver((entries) => {
    for (const entry of entries) {
      chart?.applyOptions({ width: entry.contentRect.width });
    }
  });
  observer.observe(chartContainer.value);
  onBeforeUnmount(() => {
    observer.disconnect();
    chart?.remove();
  });

  updateData();
});

function updateData() {
  if (!candleSeries || !chart) return;
  const data = props.candles
    .filter((c) => c && c.ts_ns)
    .map((c) => ({
      time: Math.floor(Number(c.ts_ns) / 1e9) as unknown as import("lightweight-charts").Time,
      open: Number(c.open),
      high: Number(c.high),
      low: Number(c.low),
      close: Number(c.close),
    }));

  if (data.length) {
    candleSeries.setData(data);
    chart.timeScale().fitContent();
  }

  // Position overlays
  const pos = props.position;
  if (pos && data.length) {
    const entryPrice = Number(pos.entry_price || 0);
    const stopPrice = Number(pos.trailing_stop || 0);
    const first = data[0];
    const last = data[data.length - 1];

    if (entryPrice > 0 && entryLine) {
      entryLine.setData([
        { time: first.time, value: entryPrice },
        { time: last.time, value: entryPrice },
      ]);
    }
    if (stopPrice > 0 && stopLine) {
      stopLine.setData([
        { time: first.time, value: stopPrice },
        { time: last.time, value: stopPrice },
      ]);
    }
  }
}

watch(() => [props.candles, props.position], updateData, { deep: true });
</script>

<template>
  <div ref="chartContainer" class="chart-container" />
</template>

<style scoped>
.chart-container {
  width: 100%;
  border-radius: 6px;
  overflow: hidden;
}
</style>
