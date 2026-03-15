// Shared tiny chart component for indicator and equity series.

import {
  defineComponent,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "/static/vendor/vue.esm-browser.prod.js";

export const SeriesCanvas = defineComponent({
  name: "SeriesCanvas",
  props: {
    values: {
      type: Array,
      default: () => [],
    },
    color: {
      type: String,
      default: "#f6a400",
    },
    fill: {
      type: Boolean,
      default: true,
    },
    showZeroLine: {
      type: Boolean,
      default: true,
    },
    height: {
      type: Number,
      default: 180,
    },
  },
  setup(props) {
    const canvasRef = ref(null);

    const draw = () => {
      // The chart intentionally avoids axes/legends to stay cheap to redraw on
      // frequent websocket updates.
      const canvas = canvasRef.value;
      if (!canvas) {
        return;
      }
      const rect = canvas.getBoundingClientRect();
      const width = Math.max(280, Math.floor(rect.width));
      const height = Math.max(80, Math.floor(props.height));
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

      const values = props.values
        .slice(-240)
        .map((item) => Number(item))
        .filter((item) => Number.isFinite(item));
      if (values.length < 2) {
        return;
      }

      const minV = Math.min(...values);
      const maxV = Math.max(...values);
      const span = Math.max(1e-9, maxV - minV);

      const left = 10;
      const right = width - 8;
      const top = 8;
      const bottom = height - 14;
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

      if (props.showZeroLine && minV <= 0 && maxV >= 0) {
        const zeroY = top + ((maxV - 0) / span) * plotH;
        ctx.strokeStyle = "rgba(135, 153, 170, 0.40)";
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(left, zeroY);
        ctx.lineTo(right, zeroY);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      const points = values.map((value, idx) => {
        const x = left + (idx / (values.length - 1)) * plotW;
        const y = top + ((maxV - value) / span) * plotH;
        return { x, y };
      });

      if (props.fill) {
        const grad = ctx.createLinearGradient(0, top, 0, bottom);
        grad.addColorStop(0, `${props.color}55`);
        grad.addColorStop(1, `${props.color}08`);
        ctx.beginPath();
        ctx.moveTo(points[0].x, bottom);
        points.forEach((point) => ctx.lineTo(point.x, point.y));
        ctx.lineTo(points[points.length - 1].x, bottom);
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();
      }

      ctx.beginPath();
      ctx.moveTo(points[0].x, points[0].y);
      points.forEach((point) => ctx.lineTo(point.x, point.y));
      ctx.strokeStyle = props.color;
      ctx.lineWidth = 1.5;
      ctx.stroke();

      ctx.fillStyle = "#8fb0ca";
      ctx.font = "600 11px JetBrains Mono, monospace";
      ctx.fillText(`L ${minV.toFixed(3)} H ${maxV.toFixed(3)}`, left, height - 4);
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
      () => props.values,
      () => nextTick(draw),
      { deep: true },
    );

    return { canvasRef };
  },
  template: `<canvas ref="canvasRef" class="canvas series-canvas"></canvas>`,
});
