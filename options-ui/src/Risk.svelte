<script>
  import { onMount, onDestroy } from 'svelte';
  import { latestRequest } from './request.js';
  const client = latestRequest();
  let scenario = 'BACKWARDATION', input = '', result = null, auditedRequest = null, error = '', busy = false;
  function invalidate() {
    client.cancel(); result = null; auditedRequest = null; error = ''; busy = false;
  }
  function run(path, body) {
    invalidate(); busy = true;
    client.run(path, body, data => { result = data; auditedRequest = JSON.parse(data.request_json); if (path === '/api/vix/demo') input = data.request_json; busy = false; }, message => { error = message; busy = false; });
  }
  function rollExplanation(roll) {
    if (roll === 'CHANGED_FROM_SUPPLIED_PAIR') return 'The selected pair changed since the supplied previous pair.';
    if (roll === 'UNCHANGED_FROM_SUPPLIED_PAIR') return 'The selected pair is unchanged from the supplied previous pair.';
    return 'No previous pair was supplied, so a roll cannot be determined.';
  }
  function timestamp(value) {
    if (!Number.isSafeInteger(value) || value < 0 || value > 8640000000000000) return 'Invalid timestamp';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? 'Invalid timestamp' : `${date.toISOString()} (${value} ms)`;
  }
  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], {type:'application/json'}));
    const a = document.createElement('a'); a.href = url; a.download = 'vix-observation.json'; a.click(); URL.revokeObjectURL(url);
  }
  onMount(() => run('/api/vix/demo', JSON.stringify({scenario})));
  onDestroy(() => client.cancel());
</script>
<svelte:head><title>Volatility Observation</title></svelte:head>
<header><a href="/">ZenQuant</a><span>READ-ONLY OBSERVATION</span><a href="/paper">Manual paper</a></header>
<main>
  <p class="eyebrow">SEPARATE HORIZONS / UNVERIFIED INPUT</p><h1>Volatility observation</h1>
  <p class="notice">Demonstration prices are invented. Supplied snapshots and source rights are not independently verified. READY applies only at the input cutoff and does not mean data is fresh now. This is not a forecast, trading signal, or account risk assessment.</p>
  <div class="layout"><section><h2>Observation input</h2><label>Demonstration scenario<select bind:value={scenario} onchange={invalidate}><option>BACKWARDATION</option><option>CONTANGO</option><option>FLAT</option><option>MISSING</option><option>STALE</option><option>ASYNCHRONOUS</option><option>ROLL</option></select></label><button onclick={() => run('/api/vix/demo', JSON.stringify({scenario}))}>Load demonstration</button>
  <label>Exact request JSON<textarea bind:value={input} oninput={invalidate} spellcheck="false"></textarea></label><button onclick={() => run('/api/vix/evaluate', input)}>Evaluate supplied JSON</button><p class="muted">Input is sent unchanged. Local evidence admission is available through the command line.</p></section>
  <div class="panels"><p aria-live="polite">{busy ? 'Evaluating…' : error ? `Unable to evaluate: ${error}` : ''}</p>
  {#if result}
    <p>Cutoff: {timestamp(result.report.cutoff_ms)} · Maximum age: {result.report.max_age_ms} ms</p>
    {#each [['Monthly VX futures', result.report.futures], ['VIX9D / VIX indices', result.report.spot]] as [title, panel]}
      <section><h2>{title}</h2><p class="status">{panel.status} · {panel.reason}</p>
      {#if panel.selected_contracts.length}<p>Selected: {panel.selected_contracts.map(c => c.symbol).join(' / ')}</p><p>Roll: {panel.roll}</p><p>{rollExplanation(panel.roll)} Selection uses monthly contracts whose last trade is after the cutoff.</p>{#each panel.selected_contracts as contract}<p>{contract.symbol} last trade: {timestamp(contract.last_trade_ms)}</p>{/each}{/if}
      {#if panel.metrics}<p class="classification">{panel.metrics.classification}</p><dl><dt>First / second</dt><dd>{panel.metrics.first_value} / {panel.metrics.second_value}</dd><dt>Spread</dt><dd>{panel.metrics.spread_points}</dd><dt>Ratio</dt><dd>{panel.metrics.ratio.decimal_exact ?? `${panel.metrics.ratio.numerator} / ${panel.metrics.ratio.denominator}`}</dd><dt>Ratio − 1</dt><dd>{panel.metrics.ratio_minus_one.decimal_exact ?? `${panel.metrics.ratio_minus_one.numerator} / ${panel.metrics.ratio_minus_one.denominator}`}</dd></dl>{/if}</section>
    {/each}
    <section><h2>Quote source audit</h2><p>All supplied quotes are shown, including quotes not selected for the result.</p>
      {#each [...(auditedRequest.futures?.quotes ?? []), ...[auditedRequest.spot?.vix9d, auditedRequest.spot?.vix].filter(Boolean)] as quote}
        <h3>{quote.symbol}</h3><dl><dt>Source reference</dt><dd>{quote.source.reference}</dd><dt>Source SHA-256</dt><dd>{quote.source.sha256}</dd><dt>Source timezone</dt><dd>{quote.source.timezone}</dd><dt>Observed</dt><dd>{timestamp(quote.observed_ms)}</dd><dt>Received</dt><dd>{timestamp(quote.received_ms)}</dd><dt>Source known</dt><dd>{timestamp(quote.source.known_ms)}</dd><dt>Trading session</dt><dd>{quote.trading_session_date}</dd><dt>Declared delay</dt><dd>{quote.declared_delay_ms == null ? 'Unknown' : `${quote.declared_delay_ms} ms`}</dd><dt>Price basis</dt><dd>{quote.price.kind}</dd></dl>
      {/each}
    </section>
    <section><h2>Audit</h2><p>Evidence: {result.report.evidence_status}</p><p class="hash">Input SHA-256: {result.report.input_sha256}</p><p class="hash">Transport SHA-256: {result.transport_sha256}</p><button onclick={download}>Export observation JSON</button></section>
  {/if}</div></div>
</main>
<style>
:global(*){box-sizing:border-box}:global(body){margin:0;background:#0b131c;color:#e2ecf4;font:14px/1.6 system-ui,sans-serif}header{display:flex;gap:24px;align-items:center;padding:20px 4vw;border-bottom:1px solid #273948}header span{color:#8ba1b5;font-size:11px}a{color:#54dfb0}main{max-width:1250px;margin:auto;padding:36px 4vw}.eyebrow{font-size:11px;letter-spacing:2px;color:#54dfb0}h1{font-size:32px}h2{font-size:16px}.notice{background:#29251c;border-left:3px solid #d7b16b;padding:14px}.layout{display:grid;grid-template-columns:minmax(260px,380px) minmax(0,1fr);gap:20px;margin-top:24px}section{background:#111e29;border:1px solid #273948;border-radius:10px;padding:20px;margin-bottom:16px;min-width:0}label{display:block;color:#adc0d0;font-size:12px;margin:14px 0}select,textarea{display:block;width:100%;background:#0b1721;border:1px solid #345065;border-radius:6px;color:#eff8ff;padding:10px;margin-top:6px}textarea{height:320px;resize:vertical;font:12px monospace}button{background:#54dfb0;border:0;border-radius:5px;padding:10px 14px;color:#09291f;font-weight:bold;cursor:pointer}.status,.muted{color:#a6bac9}.classification{font-size:22px;color:#54dfb0}dl{display:grid;grid-template-columns:150px minmax(0,1fr);gap:8px}dt{color:#8ba1b5}dd{margin:0;overflow-wrap:anywhere}.hash{overflow-wrap:anywhere;font:12px monospace}@media(max-width:700px){main{padding:22px 16px}.layout{grid-template-columns:1fr}header{padding:16px}header span{display:none}dl{grid-template-columns:1fr}}
</style>
