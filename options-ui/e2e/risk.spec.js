import {test,expect} from '@playwright/test';
import fs from 'node:fs';
const output=process.env.ZQ_RISK_EVIDENCE_DIR || '.artifacts/vix-ui';
test.beforeAll(()=>fs.mkdirSync(output,{recursive:true}));

test('separate Rust observations, fixed demo disclosure, evidence export and mobile layout',async({page})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/risk');
  await expect(page.getByTestId('futures-classification')).toContainText('BACKWARDATION');
  await expect(page.getByTestId('data-mode')).toContainText('虚构价格');
  await expect(page.getByTestId('futures-spread')).toHaveText('4');
  await expect(page.getByTestId('futures-ratio')).toHaveText('0.2');
  await expect(page.getByTestId('spot-classification')).toContainText('SHORT_HORIZON_HIGHER');
  await expect(page.getByTestId('spot-ratio')).toHaveText('1.125');
  await expect(page.getByTestId('futures-panel')).toContainText('VX/V6');
  await expect(page.getByTestId('futures-panel')).toContainText('VX/X6');
  await expect(page.getByTestId('futures-panel')).not.toContainText('VX43');
  await expect(page.getByTestId('roll-status')).toContainText('UNKNOWN');
  await page.screenshot({path:`${output}/01-desktop.png`,fullPage:true});
  const downloadPromise=page.waitForEvent('download');await page.getByText('导出完整 JSON',{exact:true}).click();
  const download=await downloadPromise;await download.saveAs(`${output}/observation.json`);
  const exported=JSON.parse(fs.readFileSync(`${output}/observation.json`,'utf8'));
  expect(exported.report.futures.metrics.spread_points).toBe('4');expect(exported.report.evidence_status).toBe('UNVERIFIED');
  expect(JSON.parse(exported.request_json).futures.calendar.contracts[1].tenor).toBe('WEEKLY');
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:`${output}/02-mobile.png`,fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();expect(errors).toEqual([]);
});

test('unavailable values, asynchronous rejection and explicit monthly roll',async({page})=>{
  await page.goto('/risk');await expect(page.getByTestId('futures-status')).toContainText('READY');
  for(const [scenario,status] of [['MISSING','MISSING'],['STALE','STALE'],['ASYNCHRONOUS','INVALID']]){
    await page.getByLabel('演示场景').selectOption(scenario);await expect(page.getByTestId('futures-status')).toContainText(status);
    await expect(page.getByTestId('futures-unavailable')).toHaveText('不可计算');await expect(page.getByTestId('futures-spread')).toHaveCount(0);
  }
  await page.screenshot({path:`${output}/03-asynchronous.png`,fullPage:true});
  await page.getByLabel('演示场景').selectOption('ROLL');
  await expect(page.getByTestId('roll-status')).toContainText('不能把换月产生的变化解释为恐慌改善');
  await expect(page.getByTestId('futures-panel')).toContainText('VX/X6');await expect(page.getByTestId('futures-panel')).toContainText('VX/Z6');
  await expect(page.getByTestId('futures-classification')).toContainText('CONTANGO');await page.screenshot({path:`${output}/04-roll.png`,fullPage:true});
});

test('strict raw input preserves independence and exact recurring ratios, rejected edits clear results',async({page})=>{
  await page.goto('/risk');await expect(page.getByTestId('futures-status')).toContainText('READY');
  await page.getByLabel('风险观察输入方式').selectOption('snapshot');
  const input=JSON.parse(await page.getByLabel('完整观察 JSON').inputValue());
  input.mode='SUPPLIED_SNAPSHOT_UNVERIFIED';input.spot=null;
  input.futures.quotes[0].price.value='22';input.futures.quotes[1].price.value='21';
  await page.getByLabel('完整观察 JSON').fill(JSON.stringify(input));await page.getByText('评估观察输入',{exact:true}).click();
  await expect(page.getByTestId('futures-ratio')).toHaveText('1 / 21（精确分数）');await expect(page.getByTestId('spot-status')).toContainText('MISSING');
  await expect(page.getByTestId('data-mode')).toContainText('输入快照');
  input.extra=true;await page.getByLabel('完整观察 JSON').fill(JSON.stringify(input));
  await expect(page.getByTestId('futures-panel')).toHaveCount(0);await page.getByText('评估观察输入',{exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('INVALID_INPUT');
  await page.getByLabel('风险观察输入方式').selectOption('demo');await expect(page.getByTestId('futures-status')).toContainText('READY');
});
