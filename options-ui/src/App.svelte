<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Curve from './Curve.svelte';
  import { latestRequest } from './request.js';
  import type { Response } from './types';
  const client = latestRequest();
  let mode = 'synthetic';
  let kind = 'C', strike = '100', quantity = '0.01', ask = '2', bid = '1.9', fee = '0.0003', terminalFee = '0.001';
  let first = '50', step = '0.1', count = 1001;
  let signalEnabled = false, hypothesis = '人工假设：检查上涨价格情景；未赋予概率', signalKnown = '10', signalUntil = '1000';
  let raw = '', response: Response | null = null, busy = false, error = '', elapsed = 0, selected = 500;
  let timer: ReturnType<typeof setTimeout>;
  let serverMode = 'synthetic';
  function invalidate() { client.cancel(); clearTimeout(timer); response = null; error = ''; busy = false; }
  function changed() { invalidate(); if (mode === 'synthetic') timer = setTimeout(calculate, 220); }
  function calculate() {
    clearTimeout(timer); response = null; busy = true; error = '';
    const start = performance.now(); serverMode = mode;
    const body = mode === 'evidence' ? raw : JSON.stringify({option_type:kind, strike, quantity, ask, bid, fee_rate:fee || null, terminal_fee:terminalFee || null, first, step, count:Number(count), signal_hypothesis:signalEnabled ? hypothesis : null, signal_known_ns:signalKnown, signal_valid_until_ns:signalUntil});
    client.run(mode === 'evidence' ? '/api/curve' : '/api/synthetic', body, (data: Response) => {
      response = data; raw = data.request_json; busy = false; elapsed = performance.now() - start;
      if (data.report.points.length) selected = Math.min(Math.max(0, selected), data.report.points.length - 1);
    }, (message: string) => { error = message; busy = false; });
  }
  function switchMode() { invalidate(); }
  function cancel() { invalidate(); error = '已取消请求；再次计算可恢复。'; }
  function download() {
    if (!response) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(response, null, 2)], {type:'application/json'}));
    const a = document.createElement('a'); a.href = url; a.download = 'zenquant-conditional-payoff.json'; a.click(); URL.revokeObjectURL(url);
  }
  $: diagnostic = response?.report.diagnostic;
  $: point = response?.report.points[selected];
  onMount(calculate);
  onDestroy(() => { client.cancel(); clearTimeout(timer); });
</script>

<svelte:head><title>ZenQuant · 期权情景实验台</title></svelte:head>
<header><a class="brand" href="/">Z<span>EN</span>QUANT <small>OPTIONS LAB</small></a><span class="pill">Go 计算 · Svelte 交互</span></header>
<main>
  <div class="intro"><div><p class="eyebrow">CONDITIONAL PAYOFF / V1</p><h1>价格变化，收益如何变化？</h1><p class="muted">线性欧式期权 · BUY 单腿 · 到期结算情景</p></div><span class="badge">研究演示 · 无交易权限</span></div>
  <div class="notice"><strong>到期情景分析</strong>　这不是历史回测、当前盯市或盈利概率。毛收益是到期支付；净损益仅扣除输入报价成本与明确费用，不预测滑点或成交。</div>
  <nav aria-label="页面定位"><a href="/paper">人工 Paper 工作台 ↗</a><a href="#inputs">调整输入 ↓</a><a href="#curve">查看曲线 ↓</a></nav>
  <div class="workspace">
    <section class="controls card" id="inputs">
      <h2>01 / 输入与假设</h2>
      <label>输入方式<select aria-label="输入方式" bind:value={mode} onchange={switchMode}><option value="synthetic">合成数学样例 · 调参演示</option><option value="evidence">完整证据 JSON · 精确输入</option></select></label>
      {#if mode === 'synthetic'}
        <p class="hint">ETH / USDC · 合成时钟 cutoff=10 ns，到期=1000 ns。非市场行情。数量单位为基础资产；样例深度明确等于输入数量。</p>
        <form onsubmit={event => { event.preventDefault(); calculate(); }}>
          <div class="fields">
            <label>期权类型<select aria-label="期权类型" bind:value={kind} onchange={changed}><option value="C">Call 看涨</option><option value="P">Put 看跌</option></select></label>
            <label>行权价<input aria-label="行权价" inputmode="decimal" bind:value={strike} oninput={changed}></label>
            <label>数量 / ETH<input aria-label="数量" inputmode="decimal" bind:value={quantity} oninput={changed}></label>
            <label>卖一价 / USDC<input aria-label="卖一价" inputmode="decimal" bind:value={ask} oninput={changed}></label>
            <label>买一价 / USDC<input aria-label="买一价" inputmode="decimal" bind:value={bid} oninput={changed}></label>
            <label>开仓费率<input aria-label="开仓费率" placeholder="留空 = 缺失" bind:value={fee} oninput={changed}></label>
            <label>到期总费用<input aria-label="到期总费用" placeholder="留空 = 缺失" bind:value={terminalFee} oninput={changed}></label>
          </div>
          <p class="hint">开仓费使用既有规则：费率 × 指数 × 数量，受权利金 12.5% 上限约束。样例指数固定 100，非真实交易所认证费率。</p>
          <h3>到期价格网格</h3><div class="fields"><label>起始价格<input aria-label="起始价格" bind:value={first} oninput={changed}></label><label>价格步长<input aria-label="价格步长" bind:value={step} oninput={changed}></label><label>点数 / 2–4096<input aria-label="点数" type="number" min="2" max="4096" bind:value={count} oninput={changed}></label></div>
          <label class="check"><input type="checkbox" bind:checked={signalEnabled} onchange={changed}>附加人工信号假设（可选）</label>
          {#if signalEnabled}<label>假设内容<input aria-label="假设内容" bind:value={hypothesis} oninput={changed}></label><div class="fields"><label>已知时点 / ns<input aria-label="信号已知时点" bind:value={signalKnown} oninput={changed}></label><label>有效截止 / ns<input aria-label="信号有效截止" bind:value={signalUntil} oninput={changed}></label></div><p class="hint">信号绑定本候选和价格假设；不自动选合约、不产生概率、不影响独立期权数学。</p>{/if}
          <button class="primary" type="submit">计算整条曲线</button>
        </form>
      {:else}
        <p class="hint">提交 options-payoff-curve-request.rust.v1。JSON 文本原样送至 Go，金额使用字符串，纳秒整数不经浏览器解析。缺候选或网格会返回缺项。来源 hash 仅标识证据，不认证其真实性。</p>
        <label>证据 JSON<textarea aria-label="证据 JSON" spellcheck="false" bind:value={raw} oninput={invalidate}></textarea></label>
        <button class="primary" onclick={calculate}>评估输入证据</button>
      {/if}
    </section>
    <div class="results">
      <section class="card chart-card" id="curve">
        <div class="section-head"><div><h2>02 / 条件盈亏曲线</h2><p class="muted">一次请求 · Go 批量计算 · 精确金额</p></div>{#if busy}<button onclick={cancel}>取消计算</button>{:else}<span class="pill">{response?.report.points.length ?? 0} 个情景</span>{/if}</div>
        <div aria-live="polite" class="state">{#if busy}正在计算，旧结果已清除…{:else if error}<strong role="alert">无法完成：{error}</strong>{:else if response?.report.missing.length}<strong role="alert">不可评估：缺少 {response.report.missing.join(' / ')}</strong>{:else if diagnostic}<span data-testid="net-status">净损益 {diagnostic.net_status.status} · {diagnostic.net_status.reason}</span>{:else}输入参数后开始评估。{/if}</div>
        {#if response?.report.points.length}
          <div class="legend"><span class="gross">● 到期毛收益</span><span class="net">● 条件净损益</span><span>{serverMode === 'synthetic' ? 'SYNTHETIC · 合成样例' : '输入证据 · 未独立核验'}</span></div>
          <Curve points={response.report.points} currency={response.report.report_currency}/>
          <p class="hint plot-note">绘图坐标使用近似数值；下方与导出保留 Go 精确小数。横轴是价格，不是时间。</p>
          <div class="inspector"><label>查看情景<input aria-label="查看情景" type="range" min="0" max={response.report.points.length - 1} step="1" bind:value={selected}></label>{#if point}<div class="metrics"><div><small>到期价格</small><b data-testid="price">{point.delivery_price}</b></div><div><small>毛收益</small><b>{point.gross_payoff ?? '不可评估'}</b></div><div><small>净损益</small><b class="net" data-testid="pnl">{point.conditional_model_pnl ?? '不可评估'}</b></div></div>{/if}</div>
        {/if}
      </section>
      {#if response}
        <section class="card evidence"><h2>03 / 成本与证据 · {response.report.report_currency}</h2><div class="metrics"><div><small>开仓权利金</small><b>{diagnostic?.cost?.premium_native ?? '不可评估'}</b></div><div><small>开仓手续费</small><b>{diagnostic?.cost?.fee_native ?? '不可评估'}</b></div><div><small>开仓总成本</small><b>{diagnostic?.cost?.total_report_currency ?? '不可评估'}</b></div></div>
          <dl><dt>到期费用假设</dt><dd>{response.report.terminal_fee_assumption ?? '缺失 · 净损益不可评估'}</dd><dt>评估 / 到期时点</dt><dd>{response.report.cutoff_ns} / {response.report.expiry_ns ?? '缺失'} ns</dd><dt>模型支持</dt><dd>{diagnostic?.support.model_support.status ?? 'NOT_EVALUABLE'} · {diagnostic?.support.model_support.reason ?? response.report.missing.join(', ')}</dd><dt>可选信号</dt><dd data-testid="signal-status">{response.report.signal_status.status} · {response.report.signal_status.reason}</dd><dt>账户 / 执行</dt><dd>UNVERIFIED / UNVERIFIED · 未评估账户风控，无真实成交证据</dd><dt>输入身份</dt><dd class="hash">{response.report.input_hash}</dd></dl>
          <div class="foot"><span>Go 计算 {(response.calculation_us / 1000).toFixed(1)} ms · 请求往返 {elapsed.toFixed(1)} ms</span><button onclick={download}>导出精确 JSON</button></div>
        </section>
      {/if}
    </div>
  </div>
  <footer>ZenQuant MVP · 核心公式由 Go 计算 · 无实盘接入</footer>
</main>

<style>
  :global(*){box-sizing:border-box} nav{display:flex;gap:20px;margin:0 0 18px;font-size:12px} nav a{color:#54dfb0;text-decoration:none} :global(body){margin:0;background:#0b131c;color:#e2ecf4;font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;font-size:14px;line-height:1.55} :global(button),:global(input),:global(select),:global(textarea){font:inherit} header{height:72px;border-bottom:1px solid #223240;display:flex;align-items:center;justify-content:space-between;padding:0 4vw;background:#0e1822}.brand{color:#edf7ff;letter-spacing:2px;text-decoration:none;font-size:21px;font-weight:750}.brand span{color:#54dfb0}.brand small{font-size:10px;color:#839bb0;margin-left:16px;letter-spacing:3px}.pill,.badge{font-size:11px;letter-spacing:.5px;border:1px solid #304555;border-radius:99px;padding:5px 11px;color:#aac0d3}.badge{border-color:#6c542f;color:#edc186}.intro{display:flex;justify-content:space-between;align-items:center;gap:20px;margin:28px 0 24px}.eyebrow{color:#54dfb0;font-size:10px;letter-spacing:3px;margin:0 0 8px}h1{font-size:clamp(23px,3vw,33px);line-height:1.3;letter-spacing:-.5px;margin:0 0 12px}h2{font-size:13px;letter-spacing:1px;margin:0 0 16px}h3{font-size:12px;color:#bdcfde;border-top:1px solid #283846;padding-top:18px;margin-top:20px}.muted{color:#8ba1b5;margin:0;font-size:12px}.notice{padding:13px 17px;border-left:3px solid #d7b16b;background:#201f1b;color:#c7baa4;font-size:12px;margin-bottom:24px}main{max-width:1440px;margin:auto;padding:0 4vw}.workspace{display:grid;grid-template-columns:320px minmax(0,1fr);gap:22px}.card{background:#111e29;border:1px solid #273948;border-radius:12px;padding:22px}.controls{align-self:start}.results{display:flex;flex-direction:column;gap:20px;min-width:0}.fields{display:grid;grid-template-columns:1fr 1fr;gap:12px}label{display:flex;flex-direction:column;gap:6px;font-size:11px;color:#adc0d0;margin-bottom:12px}input,select,textarea{width:100%;min-width:0;border:1px solid #345065;border-radius:6px;background:#0c1721;color:#eff8ff;padding:10px;outline:none}input:focus,select:focus,textarea:focus{border-color:#54dfb0;box-shadow:0 0 0 2px #54dfb022}textarea{height:400px;resize:vertical;font-family:monospace;font-size:12px}.hint{color:#8ba2b6;font-size:11px;line-height:1.8;margin:10px 0 18px}.check{flex-direction:row;align-items:center;font-size:12px;margin:22px 0 14px}.check input{width:16px}.primary{width:100%;background:#54dfb0;color:#09291f;border:none;font-weight:750;padding:12px;cursor:pointer;margin-top:12px}button{border:1px solid #3a5367;border-radius:6px;background:#192d3d;color:#d9e9f5;padding:7px 12px;cursor:pointer}button:hover{filter:brightness(1.12)}.section-head{display:flex;justify-content:space-between;align-items:start;gap:12px}.section-head h2{margin-bottom:7px}.state{min-height:42px;padding:14px 0 6px;color:#d6bf8f;font-size:11px;overflow-wrap:anywhere}.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:11px;color:#93a8bb;margin:12px 0 2px}.gross{color:#8896f5}.net{color:#54dfb0}.plot-note{margin:15px 0}.inspector{border-top:1px solid #2c3e4d;padding-top:12px}.inspector input{padding:0;accent-color:#54dfb0}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.metrics div{min-width:0}.metrics small{display:block;font-size:10px;color:#8ea8bd;margin-bottom:5px}.metrics b{display:block;font-size:20px;font-weight:550;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}.evidence dl{display:grid;grid-template-columns:90px minmax(0,1fr);font-size:11px;gap:12px;margin-top:24px}.evidence dt{color:#89a3b7}.evidence dd{margin:0;color:#c5d6e4;overflow-wrap:anywhere}.hash{font-family:monospace}.foot{display:flex;gap:12px;justify-content:space-between;align-items:center;color:#7f9aae;font-size:10px;margin-top:20px;padding-top:16px;border-top:1px solid #263b4b}footer{padding:26px 0 40px;color:#6f8a9f;font-size:11px;text-align:center}@media(max-width:900px){.workspace{grid-template-columns:1fr}.controls{order:0}.fields{grid-template-columns:repeat(3,1fr)}.intro .badge{display:none}.brand small{display:none}main{padding:0 16px}.card{padding:17px}.chart-card{padding:17px 10px}.section-head,.legend,.state,.inspector,.plot-note{margin-left:7px;margin-right:7px}.metrics b{font-size:17px}}@media(max-width:480px){.fields{grid-template-columns:1fr 1fr}header{height:60px;padding:0 16px}.brand{font-size:17px}header .pill{font-size:9px;padding:4px 7px}.foot{align-items:start;flex-direction:column}.metrics{gap:9px}.metrics b{font-size:16px}.evidence dl{grid-template-columns:72px minmax(0,1fr)}}
</style>
