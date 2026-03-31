<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch, type PropType } from "vue";
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  ColorType,
} from "lightweight-charts";

const props = defineProps({
  values: { type: Array as PropType<number[]>, default: () => [] },
  color: { type: String, default: "#4a90d9" },
  height: { type: Number, default: 140 },
});

const container = ref<HTMLDivElement>();
let chart: IChartApi | null = null;
let series: ISeriesApi<"Line"> | null = null;

onMounted(() => {
  if (!container.value) return;
  chart = createChart(container.value, {
    width: container.value.clientWidth,
    height: props.height,
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: "#6b7f94",
      fontSize: 10,
      fontFamily: "'JetBrains Mono', monospace",
    },
    grid: {
      vertLines: { visible: false },
      horzLines: { color: "rgba(55,90,120,0.08)" },
    },
    rightPriceScale: { borderVisible: false },
    timeScale: { visible: false },
    crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
  });

  series = chart.addLineSeries({
    color: props.color,
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
  });

  const observer = new ResizeObserver((entries) => {
    for (const entry of entries) {
      chart?.applyOptions({ width: entry.contentRect.width });
    }
  });
  observer.observe(container.value);
  onBeforeUnmount(() => {
    observer.disconnect();
    chart?.remove();
  });

  updateData();
});

function updateData() {
  if (!series || !chart) return;
  const data = props.values
    .filter((v) => Number.isFinite(v))
    .map((v, i) => ({
      time: (i + 1) as unknown as import("lightweight-charts").Time,
      value: v,
    }));
  if (data.length) {
    series.setData(data);
    chart.timeScale().fitContent();
  }
}

watch(() => props.values, updateData, { deep: true });
</script>

<template>
  <div ref="container" class="mini-chart" />
</template>

<style scoped>
.mini-chart {
  width: 100%;
  border-radius: 4px;
  overflow: hidden;
}
</style>
