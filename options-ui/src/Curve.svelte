<script lang="ts">
  import { onMount } from 'svelte';
  import type { Point } from './types';
  export let currency = '';
  export let points: Point[] = [];
  let canvas: HTMLCanvasElement;
  let width = 700;
  let problem = '';
  let drawMs = 0;
  const height = 330;
  function draw() {
    if (!canvas) return;
    const start = performance.now();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio; canvas.height = height * ratio;
    const ctx = canvas.getContext('2d'); if (!ctx) return;
    ctx.scale(ratio, ratio); ctx.clearRect(0, 0, width, height);
    const rows = points.map(p => ({ x: Number(p.delivery_price), gross: p.gross_payoff === null ? null : Number(p.gross_payoff), net: p.conditional_model_pnl === null ? null : Number(p.conditional_model_pnl) }));
    if (!rows.length) return;
    if (rows.some(p => !Number.isFinite(p.x) || (p.gross !== null && !Number.isFinite(p.gross)) || (p.net !== null && !Number.isFinite(p.net)))) { problem = '数值超出浏览器绘图范围；请查看精确 JSON。'; return; }
    problem = '';
    let low = 0, high = 0;
    for (const p of rows) for (const v of [p.gross, p.net]) if (v !== null) { low = Math.min(low, v); high = Math.max(high, v); }
    if (high === low) high = low + 1;
    const pad = (high - low) * .1; low -= pad; high += pad;
    const left = 64, right = width - 18, top = 24, bottom = height - 44;
    const minX = rows[0].x, maxX = rows[rows.length - 1].x;
    if (minX === maxX) { problem = '价格差小于浏览器绘图精度；精确结果仍可查看。'; return; }
    const x = (v: number) => left + (v - minX) / (maxX - minX) * (right - left);
    const y = (v: number) => bottom - (v - low) / (high - low) * (bottom - top);
    ctx.font = '11px system-ui';
    for (let i = 0; i <= 4; i++) {
      const value = low + (high - low) * i / 4;
      ctx.strokeStyle = '#233341'; ctx.beginPath(); ctx.moveTo(left, y(value)); ctx.lineTo(right, y(value)); ctx.stroke();
      ctx.fillStyle = '#8da4b8'; ctx.textAlign = 'right'; ctx.fillText(value.toPrecision(3), left - 8, y(value) + 4);
      const price = minX + (maxX - minX) * i / 4;
      ctx.textAlign = 'center'; ctx.fillText(price.toPrecision(4), x(price), bottom + 22);
    }
    ctx.strokeStyle = '#617386'; ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(left, y(0)); ctx.lineTo(right, y(0)); ctx.stroke(); ctx.setLineDash([]);
    for (const [key, color] of [['gross', '#8896f5'], ['net', '#54dfb0']] as const) {
      ctx.strokeStyle = color; ctx.lineWidth = 2.5; ctx.beginPath(); let begun = false;
      for (const p of rows) if (p[key] !== null) { if (!begun) ctx.moveTo(x(p.x), y(p[key])); else ctx.lineTo(x(p.x), y(p[key])); begun = true; }
      ctx.stroke();
    }
    drawMs = performance.now() - start;
  }
  $: if (points && width) draw();
  onMount(() => { const ro = new ResizeObserver(entries => { width = Math.floor(entries[0].contentRect.width); draw(); }); ro.observe(canvas); draw(); return () => ro.disconnect(); });
</script>
<canvas bind:this={canvas} aria-label="到期标的价格与条件毛收益及净损益曲线">曲线的精确值见下方情景查看器。</canvas>
{#if problem}<p role="alert">{problem}</p>{/if}
<div class="caption"><span>横轴：到期标的价格 · 纵轴：{currency}</span><span data-testid="draw-time">绘图 {drawMs.toFixed(1)} ms</span></div>
<style>canvas { display:block;width:100%;height:330px } .caption {display:flex;justify-content:space-between;gap:12px;color:#8da4b8;font-size:11px;padding:0 16px} p {color:#ffc68a}</style>
