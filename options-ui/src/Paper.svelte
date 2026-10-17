<script lang="ts">
  import { onMount } from 'svelte';
  // Rust is the strict contract/decimal authority. These objects are rendered, never priced in JS.
  type Json = any;
  let state: Json = null, preview: Json = null, blockers: string[] = [];
  let error = '', notice = '', busy = false, ack = false, generation = 0;
  let configId = crypto.randomUUID();
  let deribit = '', deribitCap = '', deribitFx = '', binance = '', binanceCap = '', binanceFx = '', margin = '', maxLeverage = '';
  let intentId = '', requestId = crypto.randomUUID(), product = '', budget = '', quantity = '', price = '';
  let side = '', leverage = '', intentMargin = '', reduceOnly = '', symbol = '', underlying = '', expiry = '', optionType = '', strike = '';
  let evidenceText = '';
  const labels: Record<string,string> = { OverAllocated:'两账户分配合计超过 USD100000。', ConversionUnconfirmed:'请显式确认每个已分配账户的币种换算假设。', RiskSettingsMissing:'请明确保证金模式和杠杆限制，意图必须符合配置。', SingleIntentLimit:'预算申请超过该账户的单笔上限。', NotConfigured:'请先确认该账户的模拟配置。', Conflict:'原 ID 已绑定其他参数；不能覆盖，请检查并使用新 ID。', StalePreview:'配置或时点已变化，请重新预览。', InvalidPreview:'预览与参数不一致，请重新预览。', InvalidInput:'输入不完整或不符合契约，请检查参数。', Busy:'另一请求正在提交，请保留原 ID 重试。' };
  function invalidate() { generation++; preview = null; blockers = []; ack = false; error = ''; notice = ''; }
  async function api(path:string, body:Json) {
    const r = await fetch(`/api/paper/${path}`, {method:'POST', headers:{'Content-Type':'application/json','X-ZenQuant-Request':'1'}, body:JSON.stringify(body)});
    const data = await r.json(); if (!r.ok) throw new Error(data.error || 'REQUEST_FAILED'); return data;
  }
  function failure(e:unknown) { const code = e instanceof Error ? e.message : String(e); error = `${labels[code] || '未提交成功。保留原 ID 核对状态或重试；不自动创建新预算。'} (${code})`; }
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
      if (!product || (product === 'perp' && (!side || !intentMargin || !leverage || !reduceOnly)) || (product === 'option' && (!symbol || !underlying || !expiry || !optionType || !strike))) { invalidate(); error='请人工选择并填写所有合约和风险参数。'; return; }
      const instrument = product === 'perp' ? {product:'BINANCE_USDM_BTCUSDT_PERPETUAL',details:{schema_version:'manual-binance-usdm-perpetual.rust.v1',symbol:'BTCUSDT',side,quantity_btc:quantity,limit_usdt_per_btc:price,margin_mode:intentMargin,leverage,reduce_only:reduceOnly==='yes'}} : {product:'DERIBIT_LINEAR_USDC_OPTION_BUY',details:{schema_version:'manual-deribit-option.rust.v1',symbol,underlying,expiry_ms:Date.parse(`${expiry}Z`),option_type:optionType,strike_usdc:strike,quantity_contracts:quantity,limit_usdc_per_base:price}};
      return makePreview({schema_version:'manual-paper-command.rust.v1',request_id:requestId,action:{kind:'RECORD_INTENT',value:{intent_id:intentId,source:'MANUAL',requested_budget_usd:budget,instrument,evidence}}});
    } catch(e) { invalidate(); error='证据引用 JSON 无法解析；未发送或记录。'; }
  }
  async function confirm() {
    if(!preview || !ack || busy) return; const bound=preview; busy=true; error='';
    try { const data=await api('confirm',{preview:bound,acknowledged_record_only:ack}); state=data; preview=null; ack=false; notice=data.replayed ? '原 ID 已重放；没有追加记录、分配或交易。' : '已持久记录。没有下单、成交或资金预留。'; }
    catch(e) { failure(e); } finally { busy=false; }
  }
  function replay(r:Json) { return makePreview({schema_version:'manual-paper-command.rust.v1',request_id:r.record_request_id,action:{kind:'RECORD_INTENT',value:r.intent}}); }
  function withdraw(r:Json) { return makePreview({schema_version:'manual-paper-command.rust.v1',request_id:crypto.randomUUID(),action:{kind:'WITHDRAW_INTENT',value:{intent_id:r.intent.intent_id,intent_sha256:r.intent_sha256}}}); }
  onMount(refresh);
</script>

<svelte:head><title>ZenQuant · 人工 Paper 工作台</title></svelte:head>
<header><a class="brand" href="/paper">ZENQUANT <small>MANUAL PAPER</small></a><a href="/">期权情景实验台 ↗</a><a href="/risk">风险观察 ↗</a></header>
<main>
  <div class="intro"><p class="eyebrow">DERIBIT OPTIONS / BINANCE BTCUSDT PERPETUAL</p><h1>先记录决策，再验证交易。</h1><p>人工模拟工作台 · 总资本声明 USD100000 · 无实盘连接</p></div>
  <div class="banner">当前仅保存模拟配置和人工意图。<strong>没有订单、成交、盈利、可用保证金或强平价。</strong> 实时行情、费用与账户保证金模型未接入，交易执行保持阻断。</div>
  {#if state?.storage_scope === 'ENGINEERING_TEST_ONLY'}<p class="test-scope" role="status">ENGINEERING_TEST_ONLY · 独立工程测试存储，不是用户模拟账户</p>{/if}
  <section class="summary" aria-label="模拟预算摘要"><div><small>两条线合计 / USD</small><b>100000</b></div><div><small>Deribit 声明分配 / USD</small><b>{state?.view.settings?.deribit_usdc?.allocation_usd ?? '未配置'}</b></div><div><small>Binance 声明分配 / USD</small><b>{state?.view.settings?.binance_usdt?.allocation.allocation_usd ?? '未配置'}</b></div><div><small>未分配预算 / USD</small><b data-testid="idle-budget">{state?.view.unallocated_usd ?? '未知'}</b></div></section>
  <nav><a href="#settings">01 账户设置</a><a href="#intent">02 人工意图</a><a href="#review">03 确认记录</a><a href="#records">记录历史</a></nav>
  <div class="workspace">
    <section class="card" id="settings"><h2>01 / 模拟设置</h2><p class="muted">各账户独立分配。留空表示未配置，合计不得超额；未分配部分闲置。此声明不会给旧现金预留系统注资，不代表交易所抵押金。</p>
      <fieldset disabled={busy} oninput={invalidate} onchange={invalidate}>
        <label>配置请求 ID<input aria-label="配置请求 ID" bind:value={configId}></label><button class="minor" onclick={()=>{configId=crypto.randomUUID();invalidate();}}>新配置请求 ID</button>
        <h3>Deribit · 线性 USDC 期权</h3><div class="fields"><label>Deribit 分配 / USD<input aria-label="Deribit 分配" inputmode="decimal" placeholder="未配置" bind:value={deribit}></label><label>Deribit 单笔预算上限 / USD<input aria-label="Deribit 单笔上限" inputmode="decimal" bind:value={deribitCap}></label></div>
        <label>Deribit 币种假设<select aria-label="Deribit 币种假设" bind:value={deribitFx}><option value="">未确认 · 不配置换算</option><option value="parity">确认实验假设：1 USD = 1 USDC · v1</option></select></label>
        <h3>Binance · BTCUSDT 永续</h3><div class="fields"><label>Binance 分配 / USD<input aria-label="Binance 分配" inputmode="decimal" placeholder="未配置" bind:value={binance}></label><label>Binance 单笔预算上限 / USD<input aria-label="Binance 单笔上限" inputmode="decimal" bind:value={binanceCap}></label></div>
        <label>Binance 币种假设<select aria-label="Binance 币种假设" bind:value={binanceFx}><option value="">未确认 · 不配置换算</option><option value="parity">确认实验假设：1 USD = 1 USDT · v1</option></select></label>
        <div class="fields"><label>账户保证金模式<select aria-label="账户保证金模式" bind:value={margin}><option value="">请选择</option><option value="ISOLATED">逐仓 / ISOLATED</option><option value="CROSS">全仓 / CROSS</option></select></label><label>杠杆上限声明<input aria-label="杠杆上限" inputmode="decimal" placeholder="无默认值" bind:value={maxLeverage}></label></div>
        <p class="hint">1:1 是已确认的实验换算假设，不是汇率证据。两交易所不共享实时抵押金。保证金模式和杠杆仅记录选择，未经过 Binance 风控验证。</p>
        <button class="primary" onclick={configure}>预览配置</button>
      </fieldset>
    </section>
    <section class="card" id="intent"><h2>02 / 人工意图</h2><p class="muted">主动填写具体产品、方向、数量和限价。不会从信号推荐合约或自动产生意图。</p>
      <fieldset disabled={busy} oninput={invalidate} onchange={invalidate}>
        <div class="fields"><label>意图 ID<input aria-label="意图 ID" bind:value={intentId}></label><label>记录请求 ID<input aria-label="记录请求 ID" bind:value={requestId}></label></div>
        <button class="minor" onclick={()=>{requestId=crypto.randomUUID();invalidate();}}>新记录请求 ID</button>
        <label>产品<select aria-label="产品" bind:value={product} onchange={changedProduct}><option value="">人工选择产品</option><option value="option">Deribit 线性 USDC 期权 · BUY 单腿</option><option value="perp">Binance USD-M BTCUSDT 永续</option></select></label>
        {#if product==='option'}
          <p class="hint">仅记录线性 USDC BUY 单腿。合约身份、乘数、到期时点和经期货结算规则尚未验证；不支持反向期权、卖方或多腿。</p>
          <label>完整期权合约名<input aria-label="期权合约名" placeholder="请手工选择实际合约；无默认样例" bind:value={symbol}></label>
          <div class="fields"><label>期权标的<select aria-label="期权标的" bind:value={underlying}><option value="">请选择</option><option>BTC</option><option>ETH</option></select></label><label>Call / Put<select aria-label="Call Put" bind:value={optionType}><option value="">请选择</option><option value="C">Call</option><option value="P">Put</option></select></label><label>到期时点 / UTC<input aria-label="到期 UTC" type="datetime-local" bind:value={expiry}></label><label>行权价 / USDC<input aria-label="期权行权价" inputmode="decimal" bind:value={strike}></label></div>
        {:else if product==='perp'}
          <p class="hint">BTCUSDT · BTC 数量、USDT/BTC 价格。不是 COIN-M 张数；尚未验证交易规格与最小步长。</p>
          <div class="fields"><label>买卖方向<select aria-label="买卖方向" bind:value={side}><option value="">请选择</option><option value="BUY">BUY</option><option value="SELL">SELL</option></select></label><label>意图保证金模式<select aria-label="意图保证金模式" bind:value={intentMargin}><option value="">请选择</option><option value="ISOLATED">逐仓</option><option value="CROSS">全仓</option></select></label><label>意图杠杆<input aria-label="意图杠杆" inputmode="decimal" bind:value={leverage}></label><label>是否仅减仓<select aria-label="是否仅减仓" bind:value={reduceOnly}><option value="">请显式选择</option><option value="yes">是 / reduce-only</option><option value="no">否</option></select></label></div>
        {/if}
        <div class="fields"><label>数量 / {product==='perp'?'BTC':'合约张数'}<input aria-label="意图数量" inputmode="decimal" bind:value={quantity}></label><label>限价 / {product==='perp'?'USDT/BTC':'USDC/基础资产'}<input aria-label="意图限价" inputmode="decimal" bind:value={price}></label></div>
        <label>本笔预算申请 / USD<input aria-label="本笔预算申请" inputmode="decimal" bind:value={budget}></label><p class="hint">预算申请是人工声明，不是计算出的保证金、权利金或预留金额；记录多笔意图不会占用或释放资金。</p>
        <details><summary>证据引用（可选）</summary><p class="hint">仅接受版本化字段 specification / quote / fees / mark / funding / margin 的引用 JSON。哈希不认证来源；所有引用仍为未验证。没有文件读取或网络抓取。</p><label>证据引用 JSON<textarea aria-label="证据引用 JSON" spellcheck="false" bind:value={evidenceText} placeholder="留空 = 全部缺失"></textarea></label></details>
        <button class="primary" onclick={record}>预览人工意图</button>
      </fieldset>
    </section>
  </div>
  <section class="card review" id="review"><h2>03 / 预览与确认</h2>
    <div aria-live="polite">{#if busy}<p>正在核对或持久化…</p>{/if}{#if error}<p class="error" role="alert">{error}</p>{/if}{#if notice}<p class="success" role="status">{notice}</p>{/if}</div>
    {#if preview}
      <p>操作：<strong data-testid="preview-kind">{preview.command.action.kind}</strong> · 原请求 ID：{preview.command.request_id}</p><p class="hash">参数、证据与配置绑定：{preview.sha256}</p><p class="hint">新提交预览 5 分钟有效；原 ID 重放返回已有结果。修改参数、证据或已确认配置后，请重新预览。</p>
      {@const value = preview.command.action.value}
      {#if preview.command.action.kind === 'CONFIGURE'}
        <div class="banner">两条线合计预算 USD100000。<br>Deribit：分配 USD {value.deribit_usdc?.allocation_usd ?? '0（未配置）'}，单笔申请上限 USD {value.deribit_usdc?.single_intent_cap_usd ?? '未设置'}。<br>Binance：分配 USD {value.binance_usdt?.allocation.allocation_usd ?? '0（未配置）'}，单笔申请上限 USD {value.binance_usdt?.allocation.single_intent_cap_usd ?? '未设置'}；模式 {value.binance_usdt?.margin_mode ?? '未设置'}，杠杆上限 {value.binance_usdt?.max_leverage ?? '未设置'}。<br>已分配账户均使用显式实验假设 1 USD = 1 USDC/USDT（v1），不代表真实汇率或共享抵押金。</div>
      {:else if preview.command.action.kind === 'RECORD_INTENT'}
        <div class="banner">人工意图 {value.intent_id}：{value.instrument.details.symbol}。<br>{value.instrument.product === 'BINANCE_USDM_BTCUSDT_PERPETUAL' ? `${value.instrument.details.side} ${value.instrument.details.quantity_btc} BTC，限价 ${value.instrument.details.limit_usdt_per_btc} USDT/BTC；${value.instrument.details.margin_mode}，杠杆 ${value.instrument.details.leverage}，仅减仓：${value.instrument.details.reduce_only ? '是' : '否'}` : `BUY ${value.instrument.details.quantity_contracts} 张 ${value.instrument.details.option_type}，行权价 ${value.instrument.details.strike_usdc} USDC，限价 ${value.instrument.details.limit_usdc_per_base} USDC/基础资产；声明到期 ${new Date(value.instrument.details.expiry_ms).toISOString()}`}。<br>本笔预算申请 USD {value.requested_budget_usd}，只记录，未占款或通过交易风控。</div>
      {/if}
      {#if preview.command.action.kind === 'WITHDRAW_INTENT'}<p class="banner">撤回的是未评估意图记录，不是撤单，也不释放任何交易预留。</p>{/if}
      {#if blockers.length}<p class="blocked" data-testid="execution-blocked">执行阻断 · {blockers.join(' / ')}</p>{/if}
      <details open><summary>完整确认内容（金额保持精确字符串）</summary><pre data-testid="preview-content">{JSON.stringify(preview.command,null,2)}</pre></details>
      <label class="check"><input type="checkbox" aria-label="确认仅记录" bind:checked={ack} disabled={busy}>我确认以上参数和假设；此次仅记录模拟配置或未评估意图，不授权任何交易。</label>
      <div class="actions"><button class="primary" onclick={confirm} disabled={!ack || busy}>确认记录（不交易）</button><button onclick={invalidate} disabled={busy}>放弃预览</button></div>
    {:else}<p class="muted">先预览配置或人工意图。预览不会修改状态。</p>{/if}
    <button disabled class="execution">交易执行不可用 · 缺少费用、保证金与成交模型</button>
  </section>
  <section class="card" id="records"><div class="section-head"><h2>记录历史</h2><button onclick={()=>{invalidate();refresh();}} disabled={busy}>重新读取持久记录</button></div><p class="hint">配置修订 {state?.view.revision ?? '未知'} · 意图与资金预留分离 · 原 ID 重放不会复活已撤回记录</p>
    {#if !state?.view.intents.length}<p class="muted">尚无已确认意图。</p>{/if}
    {#each state?.view.intents ?? [] as r}
      <article class="record" data-testid="paper-record"><div class="section-head"><strong>{r.intent.intent_id}</strong><span class="status">{r.status}</span></div><p>{r.intent.instrument.details.symbol} · {r.intent.instrument.product} · 人工申请 USD {r.intent.requested_budget_usd}</p><p class="hash">意图 hash {r.intent_sha256}</p><p class="muted">账户与执行：UNVERIFIED · 无成交、盈利、可用保证金或强平价</p><div class="actions"><button onclick={()=>replay(r)} disabled={busy}>按原 ID 重放</button><button onclick={()=>withdraw(r)} disabled={busy || r.withdrawn_ms !== null}>撤回意图记录</button></div></article>
    {/each}
  </section>
  <footer>Rust 精确契约与原子持久化 · Svelte 人工流程 · 没有自动策略或实盘连接</footer>
</main>
<style>
:global(body){margin:0;background:#09131d;color:#e4edf5;font-family:Inter,system-ui,sans-serif}:global(*){box-sizing:border-box}header{max-width:1360px;margin:auto;height:78px;display:flex;align-items:center;justify-content:space-between;padding:0 28px;gap:16px}a{color:#86dec3;text-decoration:none;font-size:13px}.brand{color:#eef6ff;letter-spacing:2px;font-size:22px;font-weight:750}.brand small{font-size:10px;color:#81a1b8;letter-spacing:1px;margin-left:10px}main{max-width:1304px;margin:auto;padding:0 28px}.intro{padding:24px 0 15px}h1{font-size:34px;letter-spacing:-1px;margin:12px 0}.intro p,.muted{color:#92a9bc;font-size:13px;line-height:1.8}.eyebrow{font-size:10px!important;color:#65d8b5!important;letter-spacing:2px}.banner{padding:17px 20px;background:#1f2b2d;border:1px solid #465a51;border-radius:8px;color:#d5dcb9;font-size:13px;line-height:1.8}.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;padding:25px 0}.summary small{display:block;color:#8faabd;font-size:11px;margin-bottom:7px}.summary b{font-size:24px;font-weight:500;font-variant-numeric:tabular-nums}nav{display:flex;gap:24px;padding:0 0 22px;flex-wrap:wrap}.workspace{display:grid;grid-template-columns:1fr 1.15fr;gap:22px}.card{background:#101e2a;border:1px solid #263e50;border-radius:12px;padding:24px;margin-bottom:22px;min-width:0}h2{font-size:16px;margin:0 0 16px}h3{font-size:13px;margin-top:24px;color:#bfdae9}fieldset{border:0;padding:0;margin:0;min-width:0}label{display:flex;flex-direction:column;gap:7px;color:#a9bed0;font-size:12px;margin-bottom:14px}input,select,textarea{width:100%;min-width:0;padding:11px;color:#e4f1fb;background:#0a1721;border:1px solid #355267;border-radius:6px;font:inherit}input:focus,select:focus,textarea:focus{outline:2px solid #52c7aa}textarea{height:145px;resize:vertical;font-family:monospace}.fields{display:grid;grid-template-columns:1fr 1fr;gap:12px}.hint{color:#88a2b6;font-size:11px;line-height:1.9}.primary{background:#69dbb6;color:#08271c;border:0;font-weight:700;padding:12px 18px}button{background:#1c3345;color:#dbeaf5;border:1px solid #3b5769;padding:9px 14px;border-radius:6px;cursor:pointer;font-size:12px}button:disabled{opacity:.45;cursor:not-allowed}.minor{font-size:10px;padding:6px 10px}.check{flex-direction:row;align-items:flex-start;line-height:1.8;margin-top:20px}.check input{width:18px;flex:none;margin-top:3px}.hash{font-family:monospace;font-size:11px;overflow-wrap:anywhere;color:#829db2}.actions{display:flex;gap:12px;flex-wrap:wrap}.error,.blocked{color:#f1ba91;font-size:12px;overflow-wrap:anywhere;line-height:1.8}.success{color:#6dddb6}.execution{display:block;margin-top:22px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;line-height:1.7;background:#091720;border-radius:6px;padding:16px;max-height:340px;overflow:auto}summary{font-size:12px;color:#a5c5d7;cursor:pointer;margin:14px 0}.record{padding:18px;border:1px solid #304b5e;border-radius:8px;margin-top:15px}.record p{font-size:12px;overflow-wrap:anywhere}.section-head{display:flex;justify-content:space-between;gap:14px;align-items:center;flex-wrap:wrap}.section-head h2{margin:0}.status{font-size:10px;color:#d8c092}.test-scope{color:#f2ba94;font-size:12px}footer{text-align:center;padding:12px 0 36px;color:#7291a7;font-size:11px}@media(max-width:900px){.workspace{grid-template-columns:1fr}.summary{grid-template-columns:1fr 1fr}.brand small{display:none}h1{font-size:28px}}@media(max-width:480px){main{padding:0 14px}header{padding:0 14px;height:65px}.brand{font-size:18px}header a{font-size:11px}.card{padding:18px}.summary{gap:20px}.summary b{font-size:22px}.fields{gap:10px}.actions{align-items:stretch}.actions button{flex:1}nav{gap:16px}.banner{padding:14px}.status{overflow-wrap:anywhere}}
</style>
