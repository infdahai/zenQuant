import { test, expect } from '@playwright/test';
import fs from 'node:fs';
const output = process.env.ZQ_EVIDENCE_DIR || '.artifacts/payoff-ui';
test.beforeAll(() => fs.mkdirSync(output, {recursive:true}));

test('running UI: default curve, changed inputs, missing costs and mobile layout', async ({page}) => {
  const errors = []; page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');
  await expect(page.getByTestId('net-status')).toContainText('净损益 VALID');
  await expect(page.getByTestId('pnl')).toHaveText('-0.0213');
  await page.screenshot({path:`${output}/01-default.png`,fullPage:true});
  await page.getByLabel('行权价',{exact:true}).fill('110');
  await page.getByLabel('卖一价',{exact:true}).fill('3');
  await expect(page.getByTestId('pnl')).toHaveText('-0.0313');
  await page.screenshot({path:`${output}/02-changed.png`,fullPage:true});
  await page.getByLabel('到期总费用',{exact:true}).fill('');
  await expect(page.getByTestId('net-status')).toContainText('MISSING_TERMINAL_FEE_ASSUMPTION');
  await expect(page.getByTestId('pnl')).toHaveText('不可评估');
  await page.screenshot({path:`${output}/03-missing-fee.png`,fullPage:true});
  await page.getByLabel('附加人工信号假设（可选）').check();
  await page.getByLabel('信号有效截止',{exact:true}).fill('10');
  await page.getByLabel('信号已知时点',{exact:true}).fill('9');
  await expect(page.getByTestId('signal-status')).toContainText('SIGNAL_EXPIRED');
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:`${output}/04-mobile.png`,fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});

test('raw evidence preserves missing inputs; bad request can recover', async ({page}) => {
  await page.goto('/'); await expect(page.getByTestId('net-status')).toContainText('净损益 VALID');
  await page.getByLabel('输入方式').selectOption('evidence');
  await page.getByLabel('证据 JSON').fill('{"schema_version":"options-payoff-curve-request.rust.v1","request_id":"missing","cutoff_ns":1900000000000000001,"report_currency":"USDC","candidate":null,"grid":null,"signal":null}');
  await page.getByText('评估输入证据',{exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('CANDIDATE_AND_CONTRACT');
  await expect(page.getByLabel('证据 JSON')).toHaveValue(/1900000000000000001/);
  await page.getByLabel('证据 JSON').fill('{"bad":true}');
  await page.getByText('评估输入证据',{exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('INVALID_INPUT');
  await page.getByLabel('输入方式').selectOption('synthetic');
  await page.getByText('计算整条曲线',{exact:true}).click();
  await expect(page.getByTestId('net-status')).toContainText('净损益 VALID');
});

test('4096 point browser work is measured separately and rapid edits keep final result', async ({page}) => {
  await page.addInitScript(() => {
    window.__longTasks=[];
    new PerformanceObserver(list => window.__longTasks.push(...list.getEntries().map(e=>({start:e.startTime,duration:e.duration})))).observe({type:'longtask',buffered:true});
  });
  await page.goto('/'); await expect(page.getByTestId('net-status')).toContainText('净损益 VALID');
  await page.getByLabel('点数',{exact:true}).fill('4096');
  await expect(page.getByText('4096 个情景',{exact:true})).toBeVisible();
  await expect(page.getByTestId('net-status')).toContainText('净损益 VALID');
  const measures = await page.evaluate(async()=>{
    // Timings cover client fetch, JSON parse and canvas render separately; no JS pricing.
    const start = performance.now();
    const response=await fetch('/api/synthetic',{method:'POST',headers:{'Content-Type':'application/json','X-ZenQuant-Request':'1'},body:JSON.stringify({option_type:'C',strike:'100',quantity:'.01',ask:'2',bid:'1.9',fee_rate:'.0003',terminal_fee:'.001',first:'50',step:'.1',count:4096,signal_hypothesis:null,signal_known_ns:'10',signal_valid_until_ns:'1000'})});
    const text=await response.text();const received=performance.now();const data=JSON.parse(text);const parsed=performance.now();
    return {points:data.report.points.length,body_bytes:text.length,rust_calculation_us:data.calculation_us,request_body_ms:received-start,json_parse_ms:parsed-received,canvas:document.querySelector('[data-testid="draw-time"]').textContent,long_tasks:window.__longTasks,user_agent:navigator.userAgent,hardware_concurrency:navigator.hardwareConcurrency};
  });
  await page.evaluate(() => {
    window.__inputPaint = [];
    document.querySelector('[aria-label="卖一价"]').addEventListener('input', () => {
      const start = performance.now();
      requestAnimationFrame(() => requestAnimationFrame(() => window.__inputPaint.push(performance.now() - start)));
    });
  });
  await page.getByLabel('卖一价',{exact:true}).fill('4');
  await page.getByLabel('卖一价',{exact:true}).fill('5');
  await page.getByLabel('卖一价',{exact:true}).fill('6');
  await expect(page.getByTestId('pnl')).toHaveText('-0.0613');
  measures.input_to_second_animation_frame_ms = await page.evaluate(() => window.__inputPaint);
  measures.long_tasks = await page.evaluate(() => window.__longTasks);
  fs.writeFileSync(`${output}/browser-performance.json`,JSON.stringify(measures,null,2));
  expect(measures.points).toBe(4096);
  await page.screenshot({path:`${output}/05-large-curve.png`,fullPage:true});
});
