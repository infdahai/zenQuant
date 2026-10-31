<script lang="ts">
  import { onMount } from 'svelte';
  // Go owns the contract and decimal calculations. These objects are rendered, never priced in JS.
  type Json = any;
  let state: Json = null, preview: Json = null, blockers: string[] = [];
  let error = '', notice = '', busy = false, ack = false, generation = 0;
  let configId = crypto.randomUUID();
  let deribit = '', deribitCap = '', deribitFx = '', binance = '', binanceCap = '', binanceFx = '', margin = '', maxLeverage = '';
  let intentId = '', requestId = crypto.randomUUID(), product = '', budget = '', quantity = '', price = '';
  let side = '', leverage = '', intentMargin = '', reduceOnly = '', symbol = '', underlying = '', expiry = '', optionType = '', strike = '';
  let evidenceText = '';
  const labels: Record<string,string> = { OverAllocated:'The combined account allocations exceed the paper budget.', ConversionUnconfirmed:'Explicitly confirm the currency conversion assumption for each allocated account.', RiskSettingsMissing:'Specify the margin mode and leverage limit; intents must match the configuration.', SingleIntentLimit:'The requested budget exceeds the account limit per intent.', NotConfigured:'Confirm the paper configuration for this account first.', Conflict:'This ID is bound to different parameters; check them and use a new ID.', StalePreview:'The configuration or time has changed; preview again.', InvalidPreview:'The preview does not match the parameters; preview again.', InvalidInput:'Input is incomplete or invalid; check the parameters.', Busy:'Another request is being submitted; retain the original ID and retry.' };
  function invalidate() { generation++; preview = null; blockers = []; ack = false; error = ''; notice = ''; }
  async function api(path:string, body:Json) {
    const r = await fetch(`/api/paper/${path}`, {method:'POST', headers:{'Content-Type':'application/json','X-ZenQuant-Request':'1'}, body:JSON.stringify(body)});
    const data = await r.json(); if (!r.ok) throw new Error(data.error || 'REQUEST_FAILED'); return data;
  }
  function failure(e:unknown) { const code = e instanceof Error ? e.message : String(e); error = `${labels[code] || 'Submission failed. Retain the original ID to check status or retry; no new budget is created automatically.'} (${code})`; }
  async function refresh() {
    busy = true;
    try { state = await api('state', {}); const s = state.view.settings;
      if(s) { deribit=s.deribit_usdc?.allocation_usd || ''; deribitCap=s.deribit_usdc?.single_intent_cap_usd || ''; deribitFx=s.deribit_usdc?.conversion ? 'parity' : ''; binance=s.binance_usdt?.allocation.allocation_usd || ''; binanceCap=s.binance_usdt?.allocation.single_intent_cap_usd || ''; binanceFx=s.binance_usdt?.allocation.conversion ? 'parity' : ''; margin=s.binance_usdt?.margin_mode || ''; maxLeverage=s.binance_usdt?.max_leverage || ''; }
    } catch(e) { failure(e); } finally { busy=false; }
  }
  function conversion(selected:string) { return selected === 'parity' ? {schema_version:'paper-fx-assumption.rust.v1',assumption:'EXPERIMENTAL_USD_STABLECOIN_1_TO_1',confirmed:true} : null; }
  function changedProduct() { quantity=''; price=''; budget=''; side=''; leverage=''; intentMargin=''; reduceOnly=''; symbol=''; underlying=''; expiry=''; optionType=''; strike=''; evidenceText=''; invalidate(); }
  async function makePreview(command:Json) {
    invalidate(); const current = generation; busy = true;
    try { const data = await api('preview', command); if(current !== generation) return; state=data; preview=data.preview; blockers=data.blockers; }
    catch(e) { if(current===generation) failure(e); } finally { busy=false; }
  }
  function configure() {
    return makePreview({schema_version:'manual-paper-command.rust.v1',request_id:configId,action:{kind:'CONFIGURE',value:{schema_version:'manual-paper-settings.rust.v1',total_budget_usd:'100000',deribit_usdc:deribit.trim() ? {allocation_usd:deribit,single_intent_cap_usd:deribitCap,conversion:conversion(deribitFx)} : null,binance_usdt:binance.trim() ? {allocation:{allocation_usd:binance,single_intent_cap_usd:binanceCap,conversion:conversion(binanceFx)},margin_mode:margin || null,max_leverage:maxLeverage || null} : null}}});
  }
  function record() {
    try {
      const evidence = evidenceText.trim() ? JSON.parse(evidenceText) : {specification:null,quote:null,fees:null,mark:null,funding:null,margin:null};
      if (!product || (product === 'perp' && (!side || !intentMargin || !leverage || !reduceOnly)) || (product === 'option' && (!symbol || !underlying || !expiry || !optionType || !strike))) { invalidate(); error='Manually select and fill in all contract and risk parameters.'; return; }
      const instrument = product === 'perp' ? {product:'BINANCE_USDM_BTCUSDT_PERPETUAL',details:{schema_version:'manual-binance-usdm-perpetual.rust.v1',symbol:'BTCUSDT',side,quantity_btc:quantity,limit_usdt_per_btc:price,margin_mode:intentMargin,leverage,reduce_only:reduceOnly==='yes'}} : {product:'DERIBIT_LINEAR_USDC_OPTION_BUY',details:{schema_version:'manual-deribit-option.rust.v1',symbol,underlying,expiry_ms:Date.parse(`${expiry}Z`),option_type:optionType,strike_usdc:strike,quantity_contracts:quantity,limit_usdc_per_base:price}};
      return makePreview({schema_version:'manual-paper-command.rust.v1',request_id:requestId,action:{kind:'RECORD_INTENT',value:{intent_id:intentId,source:'MANUAL',requested_budget_usd:budget,instrument,evidence}}});
    } catch(e) { invalidate(); error='Evidence reference JSON could not be parsed; nothing was sent or recorded.'; }
  }
  async function confirm() {
    if(!preview || !ack || busy) return; const bound=preview; busy=true; error='';
    try { const data=await api('confirm',{preview:bound,acknowledged_record_only:ack}); state=data; preview=null; ack=false; notice=data.replayed ? 'Original ID replayed; no records, allocations, or trades were added.' : 'Recorded persistently. No orders, fills, or funds reserved.'; }
    catch(e) { failure(e); } finally { busy=false; }
  }
  function replay(r:Json) { return makePreview({schema_version:'manual-paper-command.rust.v1',request_id:r.record_request_id,action:{kind:'RECORD_INTENT',value:r.intent}}); }
  function withdraw(r:Json) { return makePreview({schema_version:'manual-paper-command.rust.v1',request_id:crypto.randomUUID(),action:{kind:'WITHDRAW_INTENT',value:{intent_id:r.intent.intent_id,intent_sha256:r.intent_sha256}}}); }
  onMount(refresh);
</script>

<svelte:head><title>ZenQuant · Manual Paper Workbench</title></svelte:head>
<header><a class="brand" href="/paper">ZENQUANT <small>MANUAL PAPER</small></a><a href="/">Options Scenario Lab ↗</a></header>
<main>
  <div class="intro"><p class="eyebrow">DERIBIT OPTIONS / BINANCE BTCUSDT PERPETUAL</p><h1>Record decisions first, then validate trades.</h1><p>Manual paper workbench · Declared total capital USD {state?.view.total_budget_usd ?? 'Unknown'} · no live trading connection</p></div>
  <div class="banner">Only paper configurations and manual intents are saved.<strong>There are no orders, fills, profits, available margin, or liquidation prices.</strong> Live market data, fees, and account margin models are not connected; trade execution remains blocked.</div>
  {#if state?.storage_scope === 'ENGINEERING_TEST_ONLY'}<p class="test-scope" role="status">ENGINEERING_TEST_ONLY · Isolated engineering test storage, not a user paper account</p>{/if}
  <section class="summary" aria-label="Paper budget summary"><div><small>Combined budget / USD</small><b>{state?.view.total_budget_usd ?? 'Unknown'}</b></div><div><small>Deribit declared allocation / USD</small><b>{state?.view.settings?.deribit_usdc?.allocation_usd ?? 'Not configured'}</b></div><div><small>Binance declared allocation / USD</small><b>{state?.view.settings?.binance_usdt?.allocation.allocation_usd ?? 'Not configured'}</b></div><div><small>Unallocated budget / USD</small><b data-testid="idle-budget">{state?.view.unallocated_usd ?? 'Unknown'}</b></div></section>
  <nav><a href="#settings">01 Account settings</a><a href="#intent">02 Manual intent</a><a href="#review">03 Confirm record</a><a href="#records">Record history</a></nav>
  <div class="workspace">
    <section class="card" id="settings"><h2>01 / Paper settings</h2><p class="muted">Allocate each account separately. Leave blank to omit an account; allocations must not exceed the total, and unallocated funds remain idle. This declaration does not fund the legacy cash reservation system or represent exchange collateral.</p>
      <fieldset disabled={busy} oninput={invalidate} onchange={invalidate}>
        <label>Configuration request ID<input aria-label="Configuration request ID" bind:value={configId}></label><button class="minor" onclick={()=>{configId=crypto.randomUUID();invalidate();}}>New configuration request ID</button>
        <h3>Deribit · Linear USDC option</h3><div class="fields"><label>Deribit allocation / USD<input aria-label="Deribit allocation" inputmode="decimal" placeholder="Not configured" bind:value={deribit}></label><label>Deribit limit per intent / USD<input aria-label="Deribit limit per intent" inputmode="decimal" bind:value={deribitCap}></label></div>
        <label>Deribit currency assumption<select aria-label="Deribit currency assumption" bind:value={deribitFx}><option value="">Unconfirmed · no conversion configured</option><option value="parity">Confirm experimental assumption: 1 USD = 1 USDC · v1</option></select></label>
        <h3>Binance · BTCUSDT perpetual</h3><div class="fields"><label>Binance allocation / USD<input aria-label="Binance allocation" inputmode="decimal" placeholder="Not configured" bind:value={binance}></label><label>Binance limit per intent / USD<input aria-label="Binance limit per intent" inputmode="decimal" bind:value={binanceCap}></label></div>
        <label>Binance currency assumption<select aria-label="Binance currency assumption" bind:value={binanceFx}><option value="">Unconfirmed · no conversion configured</option><option value="parity">Confirm experimental assumption: 1 USD = 1 USDT · v1</option></select></label>
        <div class="fields"><label>Account margin mode<select aria-label="Account margin mode" bind:value={margin}><option value="">Select</option><option value="ISOLATED">ISOLATED</option><option value="CROSS">CROSS</option></select></label><label>Declared leverage limit<input aria-label="Leverage limit" inputmode="decimal" placeholder="No default" bind:value={maxLeverage}></label></div>
        <p class="hint">1:1 is a confirmed experimental conversion assumption, not exchange rate evidence. The exchanges do not share live collateral. Margin mode and leverage are recorded only and have not passed Binance risk validation.</p>
        <button class="primary" onclick={configure}>Preview configuration</button>
      </fieldset>
    </section>
    <section class="card" id="intent"><h2>02 / Manual intent</h2><p class="muted">Enter the product, side, quantity, and limit price manually. Signals do not recommend contracts or create intents.</p>
      <fieldset disabled={busy} oninput={invalidate} onchange={invalidate}>
        <div class="fields"><label>Intent ID<input aria-label="Intent ID" bind:value={intentId}></label><label>Record request ID<input aria-label="Record request ID" bind:value={requestId}></label></div>
        <button class="minor" onclick={()=>{requestId=crypto.randomUUID();invalidate();}}>New record request ID</button>
        <label>Product<select aria-label="Product" bind:value={product} onchange={changedProduct}><option value="">Select a product manually</option><option value="option">Deribit Linear USDC option · single-leg BUY</option><option value="perp">Binance USD-M BTCUSDT perpetual</option></select></label>
        {#if product==='option'}
          <p class="hint">Records only single-leg linear USDC BUY options. Contract identity, multiplier, expiry, and futures settlement rules are unverified; inverse options, selling, and multi-leg positions are unsupported.</p>
          <label>Full option contract name<input aria-label="Option contract name" placeholder="Manually select an actual contract; no default example" bind:value={symbol}></label>
          <div class="fields"><label>Option underlying<select aria-label="Option underlying" bind:value={underlying}><option value="">Select</option><option>BTC</option><option>ETH</option></select></label><label>Call / Put<select aria-label="Call Put" bind:value={optionType}><option value="">Select</option><option value="C">Call</option><option value="P">Put</option></select></label><label>Expiry / UTC<input aria-label="Expiry UTC" type="datetime-local" bind:value={expiry}></label><label>Strike / USDC<input aria-label="Option strike" inputmode="decimal" bind:value={strike}></label></div>
        {:else if product==='perp'}
          <p class="hint">BTCUSDT · quantity in BTC, price in USDT/BTC. Not COIN-M contracts; trading specifications and minimum increments are unverified.</p>
          <div class="fields"><label>Side<select aria-label="Side" bind:value={side}><option value="">Select</option><option value="BUY">BUY</option><option value="SELL">SELL</option></select></label><label>Intent margin mode<select aria-label="Intent margin mode" bind:value={intentMargin}><option value="">Select</option><option value="ISOLATED">Isolated</option><option value="CROSS">Cross</option></select></label><label>Intent leverage<input aria-label="Intent leverage" inputmode="decimal" bind:value={leverage}></label><label>Reduce only<select aria-label="Reduce only" bind:value={reduceOnly}><option value="">Select explicitly</option><option value="yes">Yes / reduce-only</option><option value="no">No</option></select></label></div>
        {/if}
        <div class="fields"><label>Quantity / {product==='perp'?'BTC':'contracts'}<input aria-label="Intent quantity" inputmode="decimal" bind:value={quantity}></label><label>Limit price / {product==='perp'?'USDT/BTC':'USDC/base asset'}<input aria-label="Intent limit price" inputmode="decimal" bind:value={price}></label></div>
        <label>Requested budget / USD<input aria-label="Requested budget" inputmode="decimal" bind:value={budget}></label><p class="hint">The requested budget is a manual declaration, not calculated margin, premium, or reserved funds. Recording intents does not reserve or release funds.</p>
        <details><summary>Evidence references (optional)</summary><p class="hint">Only reference JSON with versioned specification / quote / fees / mark / funding / margin fields is accepted. Hashes do not authenticate sources; all references remain unverified. No files or network resources are fetched.</p><label>Evidence reference JSON<textarea aria-label="Evidence reference JSON" spellcheck="false" bind:value={evidenceText} placeholder="Leave blank = all missing"></textarea></label></details>
        <button class="primary" onclick={record}>Preview manual intent</button>
      </fieldset>
    </section>
  </div>
  <section class="card review" id="review"><h2>03 / Preview and confirm</h2>
    <div aria-live="polite">{#if busy}<p>Checking or saving…</p>{/if}{#if error}<p class="error" role="alert">{error}</p>{/if}{#if notice}<p class="success" role="status">{notice}</p>{/if}</div>
    {#if preview}
      <p>Action: <strong data-testid="preview-kind">{preview.command.action.kind}</strong> · Original request ID: {preview.command.request_id}</p><p class="hash">Parameters, evidence, and configuration binding: {preview.sha256}</p><p class="hint">Original preview expires: {new Date(preview.expires_ms).toISOString()}; Replay with the original ID can still return the existing result. Preview again after changing parameters, evidence, or confirmed configuration.</p>
      {@const value = preview.command.action.value}
      {#if preview.command.action.kind === 'CONFIGURE'}
        <div class="banner">Combined budget USD {value.total_budget_usd}. <br>Deribit: allocation USD {value.deribit_usdc?.allocation_usd ?? '0 (not configured)'}, limit per intent USD {value.deribit_usdc?.single_intent_cap_usd ?? 'Not set'}. <br>Binance: allocation USD {value.binance_usdt?.allocation.allocation_usd ?? '0 (not configured)'}, limit per intent USD {value.binance_usdt?.allocation.single_intent_cap_usd ?? 'Not set'}; mode {value.binance_usdt?.margin_mode ?? 'Not set'}; leverage limit {value.binance_usdt?.max_leverage ?? 'Not set'}. <br>Allocated accounts use the explicit experimental assumption 1 USD = 1 USDC/USDT (v1); this does not represent a real exchange rate or shared collateral.</div>
      {:else if preview.command.action.kind === 'RECORD_INTENT'}
        <div class="banner">Manual intent {value.intent_id}: {value.instrument.details.symbol}. <br>{value.instrument.product === 'BINANCE_USDM_BTCUSDT_PERPETUAL' ? `${value.instrument.details.side} ${value.instrument.details.quantity_btc} BTC, limit ${value.instrument.details.limit_usdt_per_btc} USDT/BTC; ${value.instrument.details.margin_mode}, leverage ${value.instrument.details.leverage}, reduce only: ${value.instrument.details.reduce_only ? 'Yes' : 'No'}` : `BUY ${value.instrument.details.quantity_contracts} contracts ${value.instrument.details.option_type}, strike ${value.instrument.details.strike_usdc} USDC, limit ${value.instrument.details.limit_usdc_per_base} USDC/base asset; declared expiry ${new Date(value.instrument.details.expiry_ms).toISOString()}`}. <br>Requested budget USD {value.requested_budget_usd}, record only; no funds reserved or trading risk checks passed.</div>
      {/if}
      {#if preview.command.action.kind === 'WITHDRAW_INTENT'}<p class="banner">This withdraws an unassessed intent record; it does not cancel an order or release trading reserves.</p>{/if}
      {#if blockers.length}<p class="blocked" data-testid="execution-blocked">Execution blocked · {blockers.join(' / ')}</p>{/if}
      <details open><summary>Full confirmation content (amounts remain exact strings)</summary><pre data-testid="preview-content">{JSON.stringify(preview.command,null,2)}</pre></details>
      <label class="check"><input type="checkbox" aria-label="Confirm record only" bind:checked={ack} disabled={busy}>I confirm the parameters and assumptions above. This records only paper configuration or an unassessed intent and does not authorize trading.</label>
      <div class="actions"><button class="primary" onclick={confirm} disabled={!ack || busy}>Confirm record (no trading)</button><button onclick={invalidate} disabled={busy}>Discard preview</button></div>
    {:else}<p class="muted">Preview a configuration or manual intent first. Preview does not change state.</p>{/if}
    <button disabled class="execution">Trade execution unavailable · missing fee, margin, and fill models</button>
  </section>
  <section class="card" id="records"><div class="section-head"><h2>Record history</h2><button onclick={()=>{invalidate();refresh();}} disabled={busy}>Reload persisted records</button></div><p class="hint">Configuration revision {state?.view.revision ?? 'Unknown'} · Intents are separate from reserved funds · Replaying the original ID does not restore withdrawn records</p>
    {#if !state?.view.intents.length}<p class="muted">No confirmed intents yet.</p>{/if}
    {#each state?.view.intents ?? [] as r}
      <article class="record" data-testid="paper-record"><div class="section-head"><strong>{r.intent.intent_id}</strong><span class="status">{r.status}</span></div><p>{r.intent.instrument.details.symbol} · {r.intent.instrument.product} · Manual request USD {r.intent.requested_budget_usd}</p><p class="hash">Intent hash {r.intent_sha256}</p><p class="muted">Account and execution: UNVERIFIED · No fills, profits, available margin, or liquidation prices</p><div class="actions"><button onclick={()=>replay(r)} disabled={busy}>Replay original ID</button><button onclick={()=>withdraw(r)} disabled={busy || r.withdrawn_ms !== null}>Withdraw intent record</button></div></article>
    {/each}
  </section>
  <footer>Go exact contract and atomic persistence · Svelte manual workflow · no automated strategy or live connection</footer>
</main>
<style>
:global(body){margin:0;background:#09131d;color:#e4edf5;font-family:Inter,system-ui,sans-serif}:global(*){box-sizing:border-box}header{max-width:1360px;margin:auto;height:78px;display:flex;align-items:center;justify-content:space-between;padding:0 28px;gap:16px}a{color:#86dec3;text-decoration:none;font-size:13px}.brand{color:#eef6ff;letter-spacing:2px;font-size:22px;font-weight:750}.brand small{font-size:10px;color:#81a1b8;letter-spacing:1px;margin-left:10px}main{max-width:1304px;margin:auto;padding:0 28px}.intro{padding:24px 0 15px}h1{font-size:34px;letter-spacing:-1px;margin:12px 0}.intro p,.muted{color:#92a9bc;font-size:13px;line-height:1.8}.eyebrow{font-size:10px!important;color:#65d8b5!important;letter-spacing:2px}.banner{padding:17px 20px;background:#1f2b2d;border:1px solid #465a51;border-radius:8px;color:#d5dcb9;font-size:13px;line-height:1.8}.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;padding:25px 0}.summary small{display:block;color:#8faabd;font-size:11px;margin-bottom:7px}.summary b{font-size:24px;font-weight:500;font-variant-numeric:tabular-nums}nav{display:flex;gap:24px;padding:0 0 22px;flex-wrap:wrap}.workspace{display:grid;grid-template-columns:1fr 1.15fr;gap:22px}.card{background:#101e2a;border:1px solid #263e50;border-radius:12px;padding:24px;margin-bottom:22px;min-width:0}h2{font-size:16px;margin:0 0 16px}h3{font-size:13px;margin-top:24px;color:#bfdae9}fieldset{border:0;padding:0;margin:0;min-width:0}label{display:flex;flex-direction:column;gap:7px;color:#a9bed0;font-size:12px;margin-bottom:14px}input,select,textarea{width:100%;min-width:0;padding:11px;color:#e4f1fb;background:#0a1721;border:1px solid #355267;border-radius:6px;font:inherit}input:focus,select:focus,textarea:focus{outline:2px solid #52c7aa}textarea{height:145px;resize:vertical;font-family:monospace}.fields{display:grid;grid-template-columns:1fr 1fr;gap:12px}.hint{color:#88a2b6;font-size:11px;line-height:1.9}.primary{background:#69dbb6;color:#08271c;border:0;font-weight:700;padding:12px 18px}button{background:#1c3345;color:#dbeaf5;border:1px solid #3b5769;padding:9px 14px;border-radius:6px;cursor:pointer;font-size:12px}button:disabled{opacity:.45;cursor:not-allowed}.minor{font-size:10px;padding:6px 10px}.check{flex-direction:row;align-items:flex-start;line-height:1.8;margin-top:20px}.check input{width:18px;flex:none;margin-top:3px}.hash{font-family:monospace;font-size:11px;overflow-wrap:anywhere;color:#829db2}.actions{display:flex;gap:12px;flex-wrap:wrap}.error,.blocked{color:#f1ba91;font-size:12px;overflow-wrap:anywhere;line-height:1.8}.success{color:#6dddb6}.execution{display:block;margin-top:22px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;line-height:1.7;background:#091720;border-radius:6px;padding:16px;max-height:340px;overflow:auto}summary{font-size:12px;color:#a5c5d7;cursor:pointer;margin:14px 0}.record{padding:18px;border:1px solid #304b5e;border-radius:8px;margin-top:15px}.record p{font-size:12px;overflow-wrap:anywhere}.section-head{display:flex;justify-content:space-between;gap:14px;align-items:center;flex-wrap:wrap}.section-head h2{margin:0}.status{font-size:10px;color:#d8c092}.test-scope{color:#f2ba94;font-size:12px}footer{text-align:center;padding:12px 0 36px;color:#7291a7;font-size:11px}@media(max-width:900px){.workspace{grid-template-columns:1fr}.summary{grid-template-columns:1fr 1fr}.brand small{display:none}h1{font-size:28px}}@media(max-width:480px){main{padding:0 14px}header{padding:0 14px;height:65px}.brand{font-size:18px}header a{font-size:11px}.card{padding:18px}.summary{gap:20px}.summary b{font-size:22px}.fields{gap:10px}.actions{align-items:stretch}.actions button{flex:1}nav{gap:16px}.banner{padding:14px}.status{overflow-wrap:anywhere}}
</style>
