import {
  defineComponent,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "/static/vendor/vue.esm-browser.prod.js";

export const CandleCanvas = defineComponent({
  name: "CandleCanvas",
  props: {
    candles: {
      type: Array,
      default: () => [],
    },
    prices: {
      type: Array,
      default: () => [],
    },
    height: {
      type: Number,
      default: 240,
    },
  },
  setup(props) {
    const canvasRef = ref(null);

    const draw = () => {
      const canvas = canvasRef.value;
      if (!canvas) {
        return;
      }
      const rect = canvas.getBoundingClientRect();
      const width = Math.max(320, Math.floor(rect.width));
      const height = Math.max(120, Math.floor(props.height));
      const dpr = window.devicePixelRatio || 1;

      canvas.width = width * dpr;
      canvas.height = height * dpr;

      const ctx = canvas.getContext("2d");
      if (!ctx) {
        return;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);

      ctx.fillStyle = "rgba(10, 18, 28, 0.86)";
      ctx.fillRect(0, 0, width, height);

      const candles = props.candles.slice(-140);
      if (candles.length >= 2) {
        const lows = candles.map((item) => Number(item.low));
        const highs = candles.map((item) => Number(item.high));
        const minP = Math.min(...lows);
        const maxP = Math.max(...highs);
        const span = Math.max(1e-9, maxP - minP);

        const left = 10;
        const right = width - 8;
        const top = 8;
        const bottom = height - 18;
        const plotW = Math.max(1, right - left);
        const plotH = Math.max(1, bottom - top);

        for (let i = 1; i < 5; i += 1) {
          const y = top + (plotH * i) / 5;
          ctx.strokeStyle = "rgba(55, 90, 120, 0.30)";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(left, y);
          ctx.lineTo(right, y);
          ctx.stroke();
        }

        const slot = plotW / candles.length;
        const bodyW = Math.max(2, slot * 0.65);

        candles.forEach((item, index) => {
          const open = Number(item.open);
          const close = Number(item.close);
          const high = Number(item.high);
          const low = Number(item.low);
          if (![open, close, high, low].every(Number.isFinite)) {
            return;
          }

          const x = left + index * slot + slot * 0.5;
          const yOpen = top + ((maxP - open) / span) * plotH;
          const yClose = top + ((maxP - close) / span) * plotH;
          const yHigh = top + ((maxP - high) / span) * plotH;
          const yLow = top + ((maxP - low) / span) * plotH;
          const up = close >= open;

          ctx.strokeStyle = up ? "#14d9b4" : "#ff676f";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(x, yHigh);
          ctx.lineTo(x, yLow);
          ctx.stroke();

          const yTop = Math.min(yOpen, yClose);
          const h = Math.max(1, Math.abs(yClose - yOpen));
          ctx.fillStyle = up ? "rgba(20, 217, 180, 0.85)" : "rgba(255, 103, 111, 0.86)";
          ctx.fillRect(x - bodyW * 0.5, yTop, bodyW, h);
        });

        ctx.fillStyle = "#8fb0ca";
        ctx.font = "600 11px JetBrains Mono, monospace";
        ctx.fillText(`H ${maxP.toFixed(2)}  L ${minP.toFixed(2)}`, left, height - 6);
        return;
      }

      const prices = props.prices
        .slice(-220)
        .map((item) => Number(item.price))
        .filter((item) => Number.isFinite(item));
      if (prices.length < 2) {
        return;
      }
      const minP = Math.min(...prices);
      const maxP = Math.max(...prices);
      const span = Math.max(1e-9, maxP - minP);

      const left = 10;
      const right = width - 8;
      const top = 8;
      const bottom = height - 18;
      const plotW = Math.max(1, right - left);
      const plotH = Math.max(1, bottom - top);

      for (let i = 1; i < 5; i += 1) {
        const y = top + (plotH * i) / 5;
        ctx.strokeStyle = "rgba(55, 90, 120, 0.30)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(left, y);
        ctx.lineTo(right, y);
        ctx.stroke();
      }

      const points = prices.map((price, idx) => {
        const x = left + (idx / (prices.length - 1)) * plotW;
        const y = top + ((maxP - price) / span) * plotH;
        return { x, y };
      });

      const fill = ctx.createLinearGradient(0, top, 0, bottom);
      fill.addColorStop(0, "rgba(20, 217, 180, 0.28)");
      fill.addColorStop(1, "rgba(20, 217, 180, 0.04)");
      ctx.beginPath();
      ctx.moveTo(points[0].x, bottom);
      points.forEach((point) => ctx.lineTo(point.x, point.y));
      ctx.lineTo(points[points.length - 1].x, bottom);
      ctx.closePath();
      ctx.fillStyle = fill;
      ctx.fill();

      ctx.beginPath();
      ctx.moveTo(points[0].x, points[0].y);
      points.forEach((point) => ctx.lineTo(point.x, point.y));
      ctx.strokeStyle = "#19d7b8";
      ctx.lineWidth = 1.8;
      ctx.stroke();

      ctx.fillStyle = "#8fb0ca";
      ctx.font = "600 11px JetBrains Mono, monospace";
      ctx.fillText(`H ${maxP.toFixed(2)}  L ${minP.toFixed(2)} (live ticks)`, left, height - 6);
    };

    let ro;
    onMounted(() => {
      nextTick(draw);
      ro = new ResizeObserver(() => draw());
      if (canvasRef.value) {
        ro.observe(canvasRef.value);
      }
      window.addEventListener("resize", draw);
    });

    onBeforeUnmount(() => {
      if (ro) {
        ro.disconnect();
      }
      window.removeEventListener("resize", draw);
    });

    watch(
      () => [props.candles, props.prices],
      () => nextTick(draw),
      { deep: true },
    );

    return { canvasRef };
  },
  template: `<canvas ref="canvasRef" class="canvas chart-canvas"></canvas>`,
});
