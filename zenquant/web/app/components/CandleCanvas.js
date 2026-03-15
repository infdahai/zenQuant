// Lightweight canvas renderer for candles or fallback live tick prices.

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
    position: {
      type: Object,
      default: null,
    },
    trailingStop: {
      type: Number,
      default: null,
    },
    height: {
      type: Number,
      default: 240,
    },
  },
  setup(props) {
    const canvasRef = ref(null);

    const draw = () => {
      // Draw candles when confirmed bars are available. When the live runtime
      // has only recent ticks, fall back to a simple price line so the page
      // still shows visible market movement.
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

      const overlayPrices = [];
      if (props.position && Number.isFinite(Number(props.position.entry_price))) {
        overlayPrices.push(Number(props.position.entry_price));
      }
      if (Number.isFinite(Number(props.trailingStop)) && Number(props.trailingStop) > 0) {
        overlayPrices.push(Number(props.trailingStop));
      }

      const drawOverlay = ({ minP, maxP, top, plotH, left, right }) => {
        const span = Math.max(1e-9, maxP - minP);
        const priceToY = (price) => top + ((maxP - price) / span) * plotH;
        if (props.position && Number.isFinite(Number(props.position.entry_price))) {
          drawLevel({
            ctx,
            left,
            right,
            y: priceToY(Number(props.position.entry_price)),
            price: Number(props.position.entry_price),
            color: Number(props.position.direction) >= 0 ? "#19d7b8" : "#f5a524",
            label: Number(props.position.direction) >= 0 ? "Long entry" : "Short entry",
            align: "top",
          });
        }
        if (Number.isFinite(Number(props.trailingStop)) && Number(props.trailingStop) > 0) {
          drawLevel({
            ctx,
            left,
            right,
            y: priceToY(Number(props.trailingStop)),
            price: Number(props.trailingStop),
            color: "#b04444",
            label: "Trailing stop",
            align: "bottom",
          });
        }
      };

      const candles = props.candles.slice(-140);
      if (candles.length >= 2) {
        const lows = candles.map((item) => Number(item.low));
        const highs = candles.map((item) => Number(item.high));
        const minP = Math.min(...lows, ...overlayPrices);
        const maxP = Math.max(...highs, ...overlayPrices);
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
        drawOverlay({ minP, maxP, top, plotH, left, right });
        return;
      }

      const prices = props.prices
        .slice(-220)
        .map((item) => Number(item.price))
        .filter((item) => Number.isFinite(item));
      if (prices.length < 2) {
        return;
      }
      const minP = Math.min(...prices, ...overlayPrices);
      const maxP = Math.max(...prices, ...overlayPrices);
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
      drawOverlay({ minP, maxP, top, plotH, left, right });
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
      () => [props.candles, props.prices, props.position, props.trailingStop],
      () => nextTick(draw),
      { deep: true },
    );

    return { canvasRef };
  },
  template: `<canvas ref="canvasRef" class="canvas chart-canvas"></canvas>`,
});

function drawLevel({ ctx, left, right, y, price, color, label, align }) {
  if (!Number.isFinite(y)) {
    return;
  }
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.setLineDash([8, 4]);
  ctx.beginPath();
  ctx.moveTo(left, y);
  ctx.lineTo(right, y);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.font = "600 11px JetBrains Mono, monospace";
  const text = `${label} ${price.toFixed(2)}`;
  const textWidth = ctx.measureText(text).width;
  const boxX = Math.max(left + 6, right - textWidth - 16);
  const boxY = align === "bottom" ? y + 6 : y - 20;
  ctx.fillStyle = "rgba(10, 18, 28, 0.88)";
  ctx.fillRect(boxX - 6, boxY, textWidth + 12, 16);
  ctx.fillStyle = color;
  ctx.fillText(text, boxX, boxY + 12);
  ctx.restore();
}
