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
  color: { type: String, default: "#14d9b4" },
  height: { type: Number, default: 180 },
});

const container = ref<HTMLDivElement>();
let chart: IChartApi | null = null;
let series: ISeriesApi<"Area"> | null = null;

onMounted(() => {
  if (!container.value) return;
  chart = createChart(container.value, {
    width: container.value.clientWidth,
    height: props.height,
    layout: {
      background: { type: ColorType.Solid, color: "#111820" },
      textColor: "#6b7f94",
      fontSize: 11,
      fontFamily: "'JetBrains Mono', monospace",
    },
    grid: {
      vertLines: { color: "rgba(55,90,120,0.08)" },
      horzLines: { color: "rgba(55,90,120,0.08)" },
    },
    rightPriceScale: { borderColor: "rgba(55,90,120,0.25)" },
    timeScale: { visible: false },
  });

  series = chart.addAreaSeries({
    lineColor: props.color,
    topColor: props.color + "40",
    bottomColor: props.color + "08",
    lineWidth: 2,
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
  const data = props.values.map((v, i) => ({
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
  <div ref="container" class="chart-container" />
</template>

<style scoped>
.chart-container {
  width: 100%;
  border-radius: 6px;
  overflow: hidden;
}
</style>
