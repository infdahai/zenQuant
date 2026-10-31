<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import Curve from './Curve.svelte';
  import { latestRequest } from './request.js';
  import type { Response } from './types';
  const client = latestRequest();
  let mode = 'synthetic';
  let kind = 'C', strike = '100', quantity = '0.01', ask = '2', bid = '1.9', fee = '0.0003', terminalFee = '0.001';
  let first = '50', step = '0.1', count = 1001;
  let signalEnabled = false, hypothesis = 'Manual hypothesis: examine rising price scenarios; no probability assigned', signalKnown = '10', signalUntil = '1000';
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
  function cancel() { invalidate(); error = 'Request canceled; calculate again to resume.'; }
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

<svelte:head><title>ZenQuant · Options Scenario Lab</title></svelte:head>
<header><a class="brand" href="/">Z<span>EN</span>QUANT <small>OPTIONS LAB</small></a><span class="pill">Go calculation · Svelte interaction</span></header>
<main>
  <div class="intro"><div><p class="eyebrow">CONDITIONAL PAYOFF / V1</p><h1>How does payoff change with price?</h1><p class="muted">Linear European option · single-leg BUY · expiry settlement scenarios</p></div><span class="badge">Research demo · no trading access</span></div>
  <div class="notice"><strong>Expiry scenario analysis</strong>　This is not a historical backtest, current mark-to-market, or profit probability. Gross payoff is the expiry payout; net P&L deducts only the entered quote cost and explicit fees, and does not predict slippage or fills.</div>
  <nav aria-label="Page navigation"><a href="/risk">Volatility observation ↗</a><a href="/paper">Manual Paper Workbench ↗</a><a href="#inputs">Adjust inputs ↓</a><a href="#curve">View curve ↓</a></nav>
  <div class="workspace">
    <section class="controls card" id="inputs">
      <h2>01 / Inputs and assumptions</h2>
      <label>Input method<select aria-label="Input method" bind:value={mode} onchange={switchMode}><option value="synthetic">Synthetic mathematical example · parameter demonstration</option><option value="evidence">Full evidence JSON · exact input</option></select></label>
      {#if mode === 'synthetic'}
        <p class="hint">ETH / USDC · synthetic clock cutoff=10 ns, expiry=1000 ns. Not market data. Quantity is in base asset units; example depth explicitly equals the entered quantity.</p>
        <form onsubmit={event => { event.preventDefault(); calculate(); }}>
          <div class="fields">
            <label>Option type<select aria-label="Option type" bind:value={kind} onchange={changed}><option value="C">Call </option><option value="P">Put </option></select></label>
            <label>Strike price<input aria-label="Strike price" inputmode="decimal" bind:value={strike} oninput={changed}></label>
            <label>Quantity / ETH<input aria-label="Quantity" inputmode="decimal" bind:value={quantity} oninput={changed}></label>
            <label>Best ask / USDC<input aria-label="Best ask" inputmode="decimal" bind:value={ask} oninput={changed}></label>
            <label>Best bid / USDC<input aria-label="Best bid" inputmode="decimal" bind:value={bid} oninput={changed}></label>
            <label>Entry fee rate<input aria-label="Entry fee rate" placeholder="Leave blank = missing" bind:value={fee} oninput={changed}></label>
            <label>Total expiry fee<input aria-label="Total expiry fee" placeholder="Leave blank = missing" bind:value={terminalFee} oninput={changed}></label>
          </div>
          <p class="hint">Entry fees follow the existing rule: rate × index × quantity, capped at 12.5% of premium. The example index is fixed at 100; this is not an exchange-verified fee rate.</p>
          <h3>Expiry price grid</h3><div class="fields"><label>Starting price<input aria-label="Starting price" bind:value={first} oninput={changed}></label><label>Price step<input aria-label="Price step" bind:value={step} oninput={changed}></label><label>Points / 2–4096<input aria-label="Points" type="number" min="2" max="4096" bind:value={count} oninput={changed}></label></div>
          <label class="check"><input type="checkbox" bind:checked={signalEnabled} onchange={changed}>Add manual signal hypothesis (optional)</label>
          {#if signalEnabled}<label>Hypothesis<input aria-label="Hypothesis" bind:value={hypothesis} oninput={changed}></label><div class="fields"><label>Known at / ns<input aria-label="Signal known at" bind:value={signalKnown} oninput={changed}></label><label>Valid until / ns<input aria-label="Signal valid until" bind:value={signalUntil} oninput={changed}></label></div><p class="hint">The signal binds to this candidate and price hypothesis; it does not select contracts, assign probabilities, or affect the independent option math.</p>{/if}
          <button class="primary" type="submit">Calculate full curve</button>
        </form>
      {:else}
        <p class="hint">Submit options-payoff-curve-request.rust.v1. JSON text is sent unchanged to Go; amounts use strings and nanosecond integers are not parsed by the browser. Missing candidates or grids are reported. Source hashes identify evidence but do not authenticate it.</p>
        <label>Evidence JSON<textarea aria-label="Evidence JSON" spellcheck="false" bind:value={raw} oninput={invalidate}></textarea></label>
        <button class="primary" onclick={calculate}>Evaluate input evidence</button>
      {/if}
    </section>
    <div class="results">
      <section class="card chart-card" id="curve">
        <div class="section-head"><div><h2>02 / Conditional P&L curve</h2><p class="muted">One request · Go batch calculation · exact amounts</p></div>{#if busy}<button onclick={cancel}>Cancel calculation</button>{:else}<span class="pill">{response?.report.points.length ?? 0} scenarios</span>{/if}</div>
        <div aria-live="polite" class="state">{#if busy}Calculating; previous results cleared…{:else if error}<strong role="alert">Unable to complete: {error}</strong>{:else if response?.report.missing.length}<strong role="alert">Not evaluable: missing {response.report.missing.join(' / ')}</strong>{:else if diagnostic}<span data-testid="net-status">Net P&L {diagnostic.net_status.status} · {diagnostic.net_status.reason}</span>{:else}Enter parameters to start evaluation.{/if}</div>
        {#if response?.report.points.length}
          <div class="legend"><span class="gross">● Gross expiry payoff</span><span class="net">● Conditional net P&L</span><span>{serverMode === 'synthetic' ? 'SYNTHETIC · synthetic example' : 'input evidence · not independently verified'}</span></div>
          <Curve points={response.report.points} currency={response.report.report_currency}/>
          <p class="hint plot-note">Plot coordinates use approximate numbers; the inspector and export preserve exact Go decimals. The horizontal axis is price, not time.</p>
          <div class="inspector"><label>Inspect scenario<input aria-label="Inspect scenario" type="range" min="0" max={response.report.points.length - 1} step="1" bind:value={selected}></label>{#if point}<div class="metrics"><div><small>Expiry price</small><b data-testid="price">{point.delivery_price}</b></div><div><small>Gross payoff</small><b>{point.gross_payoff ?? 'Not evaluable'}</b></div><div><small>Net P&L</small><b class="net" data-testid="pnl">{point.conditional_model_pnl ?? 'Not evaluable'}</b></div></div>{/if}</div>
        {/if}
      </section>
      {#if response}
        <section class="card evidence"><h2>03 / Costs and evidence · {response.report.report_currency}</h2><div class="metrics"><div><small>Entry premium</small><b>{diagnostic?.cost?.premium_native ?? 'Not evaluable'}</b></div><div><small>Entry fee</small><b>{diagnostic?.cost?.fee_native ?? 'Not evaluable'}</b></div><div><small>Total entry cost</small><b>{diagnostic?.cost?.total_report_currency ?? 'Not evaluable'}</b></div></div>
          <dl><dt>Expiry fee assumption</dt><dd>{response.report.terminal_fee_assumption ?? 'Missing · net P&L not evaluable'}</dd><dt>Evaluation / expiry</dt><dd>{response.report.cutoff_ns} / {response.report.expiry_ns ?? 'Missing'} ns</dd><dt>Model support</dt><dd>{diagnostic?.support.model_support.status ?? 'NOT_EVALUABLE'} · {diagnostic?.support.model_support.reason ?? response.report.missing.join(', ')}</dd><dt>Optional signal</dt><dd data-testid="signal-status">{response.report.signal_status.status} · {response.report.signal_status.reason}</dd><dt>Account / execution</dt><dd>UNVERIFIED / UNVERIFIED · Account risk not assessed; no evidence of real fills</dd><dt>Input identity</dt><dd class="hash">{response.report.input_hash}</dd></dl>
          <div class="foot"><span>Go calculation {(response.calculation_us / 1000).toFixed(1)} ms · Request round trip {elapsed.toFixed(1)} ms</span><button onclick={download}>Export exact JSON</button></div>
        </section>
      {/if}
    </div>
  </div>
  <footer>ZenQuant MVP · Core formulas calculated by Go · no live trading connection</footer>
</main>

<style>
  :global(*){box-sizing:border-box} nav{display:flex;gap:20px;margin:0 0 18px;font-size:12px} nav a{color:#54dfb0;text-decoration:none} :global(body){margin:0;background:#0b131c;color:#e2ecf4;font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;font-size:14px;line-height:1.55} :global(button),:global(input),:global(select),:global(textarea){font:inherit} header{height:72px;border-bottom:1px solid #223240;display:flex;align-items:center;justify-content:space-between;padding:0 4vw;background:#0e1822}.brand{color:#edf7ff;letter-spacing:2px;text-decoration:none;font-size:21px;font-weight:750}.brand span{color:#54dfb0}.brand small{font-size:10px;color:#839bb0;margin-left:16px;letter-spacing:3px}.pill,.badge{font-size:11px;letter-spacing:.5px;border:1px solid #304555;border-radius:99px;padding:5px 11px;color:#aac0d3}.badge{border-color:#6c542f;color:#edc186}.intro{display:flex;justify-content:space-between;align-items:center;gap:20px;margin:28px 0 24px}.eyebrow{color:#54dfb0;font-size:10px;letter-spacing:3px;margin:0 0 8px}h1{font-size:clamp(23px,3vw,33px);line-height:1.3;letter-spacing:-.5px;margin:0 0 12px}h2{font-size:13px;letter-spacing:1px;margin:0 0 16px}h3{font-size:12px;color:#bdcfde;border-top:1px solid #283846;padding-top:18px;margin-top:20px}.muted{color:#8ba1b5;margin:0;font-size:12px}.notice{padding:13px 17px;border-left:3px solid #d7b16b;background:#201f1b;color:#c7baa4;font-size:12px;margin-bottom:24px}main{max-width:1440px;margin:auto;padding:0 4vw}.workspace{display:grid;grid-template-columns:320px minmax(0,1fr);gap:22px}.card{background:#111e29;border:1px solid #273948;border-radius:12px;padding:22px}.controls{align-self:start}.results{display:flex;flex-direction:column;gap:20px;min-width:0}.fields{display:grid;grid-template-columns:1fr 1fr;gap:12px}label{display:flex;flex-direction:column;gap:6px;font-size:11px;color:#adc0d0;margin-bottom:12px}input,select,textarea{width:100%;min-width:0;border:1px solid #345065;border-radius:6px;background:#0c1721;color:#eff8ff;padding:10px;outline:none}input:focus,select:focus,textarea:focus{border-color:#54dfb0;box-shadow:0 0 0 2px #54dfb022}textarea{height:400px;resize:vertical;font-family:monospace;font-size:12px}.hint{color:#8ba2b6;font-size:11px;line-height:1.8;margin:10px 0 18px}.check{flex-direction:row;align-items:center;font-size:12px;margin:22px 0 14px}.check input{width:16px}.primary{width:100%;background:#54dfb0;color:#09291f;border:none;font-weight:750;padding:12px;cursor:pointer;margin-top:12px}button{border:1px solid #3a5367;border-radius:6px;background:#192d3d;color:#d9e9f5;padding:7px 12px;cursor:pointer}button:hover{filter:brightness(1.12)}.section-head{display:flex;justify-content:space-between;align-items:start;gap:12px}.section-head h2{margin-bottom:7px}.state{min-height:42px;padding:14px 0 6px;color:#d6bf8f;font-size:11px;overflow-wrap:anywhere}.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:11px;color:#93a8bb;margin:12px 0 2px}.gross{color:#8896f5}.net{color:#54dfb0}.plot-note{margin:15px 0}.inspector{border-top:1px solid #2c3e4d;padding-top:12px}.inspector input{padding:0;accent-color:#54dfb0}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.metrics div{min-width:0}.metrics small{display:block;font-size:10px;color:#8ea8bd;margin-bottom:5px}.metrics b{display:block;font-size:20px;font-weight:550;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}.evidence dl{display:grid;grid-template-columns:90px minmax(0,1fr);font-size:11px;gap:12px;margin-top:24px}.evidence dt{color:#89a3b7}.evidence dd{margin:0;color:#c5d6e4;overflow-wrap:anywhere}.hash{font-family:monospace}.foot{display:flex;gap:12px;justify-content:space-between;align-items:center;color:#7f9aae;font-size:10px;margin-top:20px;padding-top:16px;border-top:1px solid #263b4b}footer{padding:26px 0 40px;color:#6f8a9f;font-size:11px;text-align:center}@media(max-width:900px){.workspace{grid-template-columns:1fr}.controls{order:0}.fields{grid-template-columns:repeat(3,1fr)}.intro .badge{display:none}.brand small{display:none}main{padding:0 16px}.card{padding:17px}.chart-card{padding:17px 10px}.section-head,.legend,.state,.inspector,.plot-note{margin-left:7px;margin-right:7px}.metrics b{font-size:17px}}@media(max-width:480px){.fields{grid-template-columns:1fr 1fr}header{height:60px;padding:0 16px}.brand{font-size:17px}header .pill{font-size:9px;padding:4px 7px}.foot{align-items:start;flex-direction:column}.metrics{gap:9px}.metrics b{font-size:16px}.evidence dl{grid-template-columns:72px minmax(0,1fr)}}
</style>
