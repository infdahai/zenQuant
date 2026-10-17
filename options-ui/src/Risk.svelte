<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { latestRequest } from './request.js';
  // Rust owns all financial arithmetic and classification; JS formats audit timestamps only.
  type Json = any;
  const client = latestRequest();
  let mode = 'demo', scenario = 'BACKWARDATION', raw = '', response: Json = null;
  let busy = false, error = '';
  const classifications: Record<string,string> = {BACKWARDATION:'倒挂',CONTANGO:'正向期限结构',FLAT:'平坦',SHORT_HORIZON_HIGHER:'9 天高于 30 天',SHORT_HORIZON_LOWER:'9 天低于 30 天',EQUAL_HORIZON_VALUES:'两个期限数值相等'};
  const statuses: Record<string,string> = {READY:'输入时点可计算',MISSING:'证据缺失',STALE:'证据过期',INVALID:'证据不一致'};
  const reasons: Record<string,string> = {FUTURES_INPUT_REQUIRED:'缺少期货输入',SPOT_INPUT_REQUIRED:'缺少指数输入',SELECTED_MONTHLY_QUOTE_REQUIRED:'所选月度合约缺少报价',MISSING_PRICE:'缺少价格',QUOTE_EXPIRED:'报价超过输入时点的有效期',CALENDAR_EXPIRED:'合约日历已过期',UNKNOWN_SOURCE_DELAY:'来源延迟未知',ASYNCHRONOUS_OR_DIFFERENT_SESSION_QUOTES:'报价不同步或属于不同交易日',INCOMPATIBLE_PRICE_BASIS:'两个价格的口径不一致',BOTH_SPOT_INDICES_REQUIRED:'必须同时提供 VIX9D 与 VIX'};
  function invalidate() { client.cancel(); response=null; busy=false; error=''; }
  function evaluate() {
    invalidate(); busy=true;
    client.run(mode==='demo' ? '/api/vix/demo' : '/api/vix/evaluate', mode==='demo' ? JSON.stringify({scenario}) : raw,
      (data:Json)=>{ response=data; if(mode==='demo') raw=data.request_json; busy=false; },
      (message:string)=>{error=message; busy=false;});
  }
  function changeMode() { invalidate(); if(mode==='demo') evaluate(); }
  function time(ms:number, zone='UTC') { return new Intl.DateTimeFormat('sv-SE',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',fractionalSecondDigits:3,hourCycle:'h23'}).format(new Date(ms))+' '+zone; }
  function ratio(r:Json) { return r.decimal_exact ?? `${r.numerator} / ${r.denominator}（精确分数）`; }
  function download() {
    const url=URL.createObjectURL(new Blob([JSON.stringify(response,null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download='zenquant-vix-observation.json';a.click();URL.revokeObjectURL(url);
  }
  $: input = response ? JSON.parse(response.request_json) : null;
  $: quoteRows = input ? [...(input.futures?.quotes || []),input.spot?.vix9d,input.spot?.vix].filter(Boolean) : [];
  onMount(evaluate); onDestroy(()=>client.cancel());
</script>

<svelte:head><title>ZenQuant · 波动率风险观察</title></svelte:head>
<header><a class="brand" href="/risk">ZENQUANT <small>RISK OBSERVATION</small></a><span class="pill">只读 · Rust</span></header>
<main>
  <nav aria-label="工作台导航"><a href="/">期权情景实验台 ↗</a><a href="/paper">人工 Paper 工作台 ↗</a></nav>
  <p class="eyebrow">VOLATILITY / TWO DISTINCT TERM STRUCTURES</p>
  <h1>波动率期限结构观察</h1>
  <p class="subtitle">两种期限含义，分别查看。倒挂本身不预测价格方向，也不构成交易指令。</p>
  <div class="banner" data-testid="data-mode"><strong>{response?.report.mode === 'SUPPLIED_SNAPSHOT_UNVERIFIED' ? '输入快照 · 来源未认证' : '固定演示夹具 · 虚构价格 · 非实时行情'}</strong><span>当前未接入授权实时数据。所有状态仅相对于输入 cutoff 判断，不能表示现在的市场状态。</span></div>
  <section class="controls" aria-label="观察输入">
    <label>输入方式<select aria-label="风险观察输入方式" bind:value={mode} onchange={changeMode}><option value="demo">固定演示夹具</option><option value="snapshot">完整快照 JSON · 未认证</option></select></label>
    {#if mode === 'demo'}
      <label>演示场景<select aria-label="演示场景" bind:value={scenario} onchange={evaluate}>
        <option value="BACKWARDATION">月度期货倒挂</option><option value="CONTANGO">月度期货正向结构</option><option value="FLAT">月度期货平坦</option><option value="MISSING">近月报价缺失</option><option value="STALE">报价过期</option><option value="ASYNCHRONOUS">报价不同步</option><option value="ROLL">显式换月</option>
      </select></label>
    {/if}
    <button onclick={evaluate} disabled={busy}>{busy ? '校验中…' : '评估观察输入'}</button>
  </section>
  {#if mode === 'snapshot'}<label class="raw">完整观察 JSON<textarea aria-label="完整观察 JSON" rows="10" bind:value={raw} oninput={invalidate} spellcheck="false"></textarea></label>{/if}
  {#if error}<p role="alert" class="error">输入被拒绝：{error}。旧结果已清除。</p>{/if}
  {#if response}
    <div class="asof"><span>输入时点 <b>{time(response.report.cutoff_ms)}</b></span><span>最大报价年龄 {response.report.max_age_ms} ms</span><span>来源认证 <b>UNVERIFIED</b></span></div>
    <div class="panels">
      {#each ['futures','spot'] as key}
        {@const p=response.report[key]}
        <section class="card" data-testid={`${key}-panel`}>
          <div class="section-head"><p class="eyebrow">{key==='futures' ? '01 / MONTHLY VX FUTURES' : '02 / SPOT INDEX HORIZONS'}</p><span class:bad={p.status!=='READY'} class="status" data-testid={`${key}-status`}>{statuses[p.status]} · {p.status}</span></div>
          <h2>{key==='futures' ? '近月 / 次月 VX 期货' : 'VIX9D / VIX 现货期限指标'}</h2>
          <p class="explain">{key==='futures' ? '未来两个到期日的 VIX 预期。仅连续月度合约，排除周度合约。' : 'SPX 期权隐含的 9 天与 30 天波动率期限，非两份期货合约。'}</p>
          <p class="formula">{key==='futures' ? '价差 = VX1 − VX2　·　比率差 = VX1 / VX2 − 1' : '差值 = VIX9D − VIX　·　比值 = VIX9D / VIX'}</p>
          {#if p.metrics}
            <p class="classification" data-testid={`${key}-classification`}>{classifications[p.metrics.classification]}<small>{p.metrics.classification}</small></p>
            <div class="numbers"><div><span>{key==='futures' ? '期货价差 / 点' : '指数差值 / 点'}</span><b data-testid={`${key}-spread`}>{p.metrics.spread_points}</b></div><div><span>{key==='futures' ? '比率差' : '比值'} / 无量纲</span><b class="ratio" data-testid={`${key}-ratio`}>{ratio(key==='futures' ? p.metrics.ratio_minus_one : p.metrics.ratio)}</b></div></div>
            <p class="hint">输入数值：{p.metrics.first_value} / {p.metrics.second_value}。全部由 Rust 精确计算，无浮点估值。</p>
          {:else}<p class="unavailable" data-testid={`${key}-unavailable`}>不可计算</p><p class="error">{reasons[p.reason] || '输入未满足校验要求'}<br><code>{p.reason}</code></p>{/if}
          {#if key==='futures'}
            <div class="contracts">{#each p.selected_contracts as c}<div><strong>{c.symbol}</strong><span>到期日 {c.settlement_date} · MONTHLY</span><span>最后交易 {time(c.last_trade_ms,'America/Chicago')}</span></div>{/each}</div>
            <p class="roll" data-testid="roll-status">{p.roll==='CHANGED_FROM_SUPPLIED_PAIR' ? '换月：合约对已改变。不能把换月产生的变化解释为恐慌改善。' : p.roll==='UNCHANGED_FROM_SUPPLIED_PAIR' ? '相对输入的上一合约对未换月。' : '换月状态 UNKNOWN：未提供上一合约对。'}<small>{p.roll}</small></p>
          {:else}<p class="hint">独立指数比较，不使用期货的到期日和换月规则。</p>{/if}
        </section>
      {/each}
    </div>
    <section class="card audit">
      <div class="section-head"><h2>输入审计</h2><button class="secondary" onclick={download}>导出完整 JSON</button></div>
      <p class="explain">来源哈希用于标识输入，不证明行情真实。演示价格与来源时点均为固定构造；真实数据授权和完整性尚未验证。</p>
      <div class="quotes">{#each quoteRows as q}<article><strong>{q.symbol}</strong><span>{q.price.kind} · 交易日 {q.trading_session_date}</span><span>观察 {time(q.observed_ms)}</span><span>接收 {time(q.received_ms)}</span><span>已知 {time(q.source.known_ms)}</span><span>来源时区 {q.source.timezone} · 声明延迟 {q.declared_delay_ms === null ? 'UNKNOWN' : `${q.declared_delay_ms} ms`}</span><span class="source">{q.source.reference}</span><code>{q.source.sha256}</code></article>{/each}</div>
      <dl><dt>模型版本</dt><dd>{response.report.model_version}</dd><dt>换月规则</dt><dd>{input.futures?.roll_rule || '无期货输入'}</dd><dt>日历来源</dt><dd>{input.futures?.calendar.source.reference || '缺失'}</dd><dt>输入 SHA-256</dt><dd>{response.report.input_sha256}</dd><dt>Rust 代码 SHA-256</dt><dd>{response.code_sha256}</dd></dl>
      <details><summary>查看全部输入、日历有效期与精确时点</summary><pre>{response.request_json}</pre></details>
    </section>
  {/if}
  <footer>只读风险观察 · 未接入账户风控、订单或自动策略 · <a href="https://www.cboe.com/tradable-products/vix/term-structure" target="_blank" rel="noreferrer">Cboe 期限定义 ↗</a></footer>
</main>

<style>
  header{height:72px;border-bottom:1px solid #273b49;display:flex;align-items:center;justify-content:space-between;padding:0 4vw;background:#0e1923}.brand{color:#e8f3fc;text-decoration:none;letter-spacing:2px;font-weight:750;font-size:21px}.brand small{font-size:10px;letter-spacing:2px;margin-left:14px;color:#92abbd}.pill{color:#9eb8c9;border:1px solid #345064;border-radius:20px;padding:5px 12px;font-size:11px}main{max-width:1440px;margin:auto;padding:28px 4vw}nav{display:flex;gap:22px;flex-wrap:wrap;margin-bottom:32px}a{color:#70d8c2;text-decoration:none}nav a,footer{font-size:12px}.eyebrow{color:#74cabc;font-size:10px;letter-spacing:2px;margin:0 0 12px}h1{font-size:clamp(25px,3vw,38px);margin:0 0 10px;letter-spacing:-.5px}h2{font-size:19px;line-height:1.5;margin:0 0 10px}.subtitle,.explain{color:#9fb3c3;font-size:13px;line-height:1.8}.banner{display:flex;flex-direction:column;gap:6px;border:1px solid #695936;border-left:3px solid #dbbd79;border-radius:6px;padding:18px 22px;margin:26px 0;color:#e1cc9d;background:#211f19}.banner span{font-size:12px;color:#bdb19a}.controls{display:flex;align-items:end;gap:18px;padding:18px 0 24px;flex-wrap:wrap}label{display:flex;flex-direction:column;gap:8px;color:#9db4c5;font-size:12px;min-width:210px}select,textarea{color:#e1edf7;background:#101e2b;border:1px solid #385367;border-radius:6px;padding:10px;min-width:0}button{background:#69d4bb;color:#10241f;font-weight:650;border:0;border-radius:6px;padding:11px 18px;cursor:pointer}button:disabled{opacity:.5}button.secondary{border:1px solid #416175;color:#bfd5e4;background:transparent;font-size:12px}.raw{margin-bottom:20px}textarea{width:100%;font-family:monospace;font-size:12px}.asof{display:flex;flex-wrap:wrap;gap:8px 24px;color:#8da8bb;font-size:11px;margin:0 0 18px}.asof b{color:#c4d8e5;font-weight:500}.panels{display:grid;grid-template-columns:1fr 1fr;gap:22px}.card{background:#101e2a;border:1px solid #293f50;border-radius:12px;padding:24px;min-width:0}.section-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.section-head .eyebrow{margin:0 0 8px}.status{font-size:10px;color:#90bfb3;margin-bottom:8px}.status.bad{color:#d9b37e}.formula{font-family:monospace;font-size:11px;color:#9fbbd0;border-top:1px solid #29404e;padding-top:16px}.classification{font-size:25px;color:#e1eff8;margin:26px 0 18px}.classification small,.roll small{display:block;font-size:10px;letter-spacing:1px;color:#849fae;margin-top:6px}.numbers{display:grid;grid-template-columns:1fr 1fr;gap:18px}.numbers span{display:block;font-size:11px;color:#8ca7bb}.numbers b{display:block;font-size:34px;line-height:1.4;font-weight:550;overflow-wrap:anywhere}.numbers b.ratio{font-size:28px}.hint{color:#829eaf;font-size:11px;line-height:1.8}.contracts{margin-top:20px;border-top:1px solid #2a4051;padding-top:16px;display:grid;gap:12px}.contracts div{display:flex;flex-direction:column;gap:3px}.contracts strong{color:#bfd9eb;font-size:14px}.contracts span{font-size:11px;color:#8da8bb}.roll{background:#192b35;border-radius:5px;padding:12px;color:#c8c5ad;font-size:12px;line-height:1.7}.unavailable{font-size:29px;color:#c6b599;margin:30px 0 15px}.error{color:#e3bc93;font-size:12px;overflow-wrap:anywhere}.audit{margin-top:24px}.quotes{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin:24px 0}.quotes article{display:flex;flex-direction:column;gap:5px;background:#0c1822;padding:14px;border:1px solid #283e4d;border-radius:6px;font-size:11px;color:#90adbf;min-width:0}.quotes strong{font-size:14px;color:#c6dce9}.source{margin-top:6px}code,dd,.source{overflow-wrap:anywhere}code{font-size:10px;color:#7597ac}dl{display:grid;grid-template-columns:125px minmax(0,1fr);gap:10px;font-size:11px}dt{color:#819bad}dd{margin:0;color:#b0c8d9;font-family:monospace}summary{cursor:pointer;color:#91c5d2;font-size:12px;margin-top:24px}pre{max-height:420px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;padding:18px;background:#09151e}footer{padding:28px 0;color:#7596aa;line-height:1.9}@media(max-width:850px){.panels{grid-template-columns:1fr}.brand small{display:none}}@media(max-width:480px){main{padding:20px 14px}header{padding:0 14px}.brand{font-size:18px}.controls{gap:12px;align-items:stretch}.controls label{flex:1;min-width:150px}.controls button{width:100%}.card{padding:18px}.banner{padding:15px}dl{grid-template-columns:1fr;gap:6px}dd{margin-bottom:10px}.classification{font-size:23px}.numbers b{font-size:30px}.numbers b.ratio{font-size:23px}.section-head{align-items:start}}
</style>
