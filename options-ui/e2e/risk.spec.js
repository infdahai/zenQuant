import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';

const hash = value => createHash('sha256').update(value).digest('hex');
const futuresPanel = page => page.locator('section').filter({ has: page.getByRole('heading', { name: 'Monthly VX futures', exact: true }) });
const spotPanel = page => page.locator('section').filter({ has: page.getByRole('heading', { name: 'VIX9D / VIX indices', exact: true }) });

async function exportObservation(page) {
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export observation JSON' }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe('vix-observation.json');
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

async function evaluate(page, request) {
  const body = JSON.stringify(request);
  await page.getByLabel('Exact request JSON').fill(body);
  await expect(page.getByTestId('report-mode')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Export observation JSON' })).toHaveCount(0);
  const pending = page.waitForResponse('**/api/vix/evaluate');
  await page.getByRole('button', { name: 'Evaluate supplied JSON' }).click();
  const response = await pending;
  expect(response.status()).toBe(200);
  const result = await response.json();
  expect(result.transport_sha256).toBe(hash(body));
  await expect(page.getByTestId('report-mode')).toHaveText(`Report mode: ${request.mode}`);
  return result;
}

test('observer reports independent panels, roll and invalid input', async ({ page }) => {
  await page.goto('/risk');
  await expect(page.locator('p.classification').filter({ hasText: 'BACKWARDATION' })).toBeVisible();
  await expect(page.getByText('SHORT_HORIZON_HIGHER', { exact: true })).toBeVisible();
  await expect(page.getByText('READY applies only at the input cutoff', { exact: false })).toBeVisible();
  await page.getByLabel('Demonstration scenario').selectOption('ROLL');
  await page.getByRole('button', { name: 'Load demonstration' }).click();
  await expect(page.getByText('Selected: VX/X6 / VX/Z6')).toBeVisible();
  await expect(page.getByText('Roll: CHANGED_FROM_SUPPLIED_PAIR')).toBeVisible();
  await expect(page.getByText('The selected pair changed since the supplied previous pair.', { exact: false })).toBeVisible();
  await expect(futuresPanel(page).getByText('Roll warning: A change caused by rolling contracts does not show that market fear has eased.', { exact: true })).toBeVisible();
  const rolled = await exportObservation(page);
  expect(rolled.report.futures.roll).toBe('CHANGED_FROM_SUPPLIED_PAIR');
  expect(rolled.report.futures.selected_contracts.map(c => c.symbol)).toEqual(['VX/X6', 'VX/Z6']);
  expect(rolled.report.mode).toBe('DEMONSTRATION');
  await page.getByLabel('Exact request JSON').fill('{"bad":true}');
  await page.getByRole('button', { name: 'Evaluate supplied JSON' }).click();
  await expect(page.getByText('Unable to evaluate:', { exact: false })).toBeVisible();
});

test('edits invalidate results and protect unsent input from delayed responses', async ({ page }) => {
  await page.goto('/risk');
  const input = page.getByLabel('Exact request JSON');
  await expect(page.getByText('Quote source audit')).toBeVisible();
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let arrived;
  const intercepted = new Promise(resolve => { arrived = resolve; });
  await page.route('**/api/vix/demo', async route => {
    const response = await route.fetch();
    arrived();
    await gate;
    await route.fulfill({ response }).catch(() => {});
  });
  await page.getByRole('button', { name: 'Load demonstration' }).click();
  await intercepted;
  await input.fill('unsent edits');
  await expect(page.getByText('Quote source audit')).toHaveCount(0);
  await expect(page.getByText('Evaluating…')).toHaveCount(0);
  release();
  await expect(input).toHaveValue('unsent edits');
  await page.getByLabel('Demonstration scenario').selectOption('ROLL');
  await expect(input).toHaveValue('unsent edits');
});

test('audit displays source details and handles invalid quote timestamps', async ({ page }) => {
  await page.goto('/risk');
  await expect(page.getByText('Quote source audit')).toBeVisible();
  await expect(page.getByText('DEMONSTRATION: VX/V6; invented prices, no licensed feed')).toBeVisible();
  await expect(page.getByText('No previous pair was supplied, so a roll cannot be determined.')).toBeVisible();
  const input = page.getByLabel('Exact request JSON');
  const request = JSON.parse(await input.inputValue());
  request.futures.quotes[0].observed_ms = 8640000000000001;
  await input.fill(JSON.stringify(request));
  await page.getByRole('button', { name: 'Evaluate supplied JSON' }).click();
  await expect(page.getByText('INVALID_QUOTE_TIME')).toBeVisible();
  await expect(page.getByText('Invalid timestamp', { exact: true })).toBeVisible();
});

test('exact metrics, returned mode and full export survive real HTTP roundtrip', async ({ page }) => {
  const pending = page.waitForResponse('**/api/vix/demo');
  await page.goto('/risk');
  const response = await pending;
  expect(response.status()).toBe(200);
  const baseline = await response.json();
  await expect(page.getByTestId('report-mode')).toHaveText('Report mode: DEMONSTRATION');
  await expect(futuresPanel(page).locator('dd')).toHaveText(['24 / 20', '4', '1.2', '0.2']);
  await expect(spotPanel(page).locator('dd')).toHaveText(['27 / 24', '3', '1.125', '0.125']);
  await expect(page.getByText('Roll warning:', { exact: false })).toHaveCount(0);
  expect(baseline.report.input_sha256).toBe('372edb85ecadf97d4b2b0b8bd64e0ac86414890687dcb3a5e8b1f61b05dd73d3');
  expect(baseline.transport_sha256).toBe(hash('{"scenario":"BACKWARDATION"}'));
  expect(baseline.implementation).toBe('vix-observation-native.go.v1');
  expect(baseline.cargo_lock_sha256).toBeNull();
  expect(baseline.code_sha256).toMatch(/^[a-f0-9]{64}$/);
  expect(await exportObservation(page)).toEqual(baseline);

  const again = await evaluate(page, JSON.parse(baseline.request_json));
  expect(again.report).toEqual(baseline.report);
  expect(again.code_sha256).toBe(baseline.code_sha256);
  expect(again.transport_sha256).not.toBe(baseline.transport_sha256);
  expect(await exportObservation(page)).toEqual(again);

  const supplied = JSON.parse(baseline.request_json);
  supplied.mode = 'SUPPLIED_SNAPSHOT_UNVERIFIED';
  const result = await evaluate(page, supplied);
  await expect(page.getByTestId('report-mode')).toHaveText('Report mode: SUPPLIED_SNAPSHOT_UNVERIFIED');
  expect(result.report.evidence_status).toBe('UNVERIFIED');
  expect(result.report.input_sha256).not.toBe(baseline.report.input_sha256);
  expect(await exportObservation(page)).toEqual(result);
});

test('recurring fractions remain exact in both panels and the export', async ({ page }) => {
  await page.goto('/risk');
  await expect(page.getByTestId('report-mode')).toBeVisible();
  const request = JSON.parse(await page.getByLabel('Exact request JSON').inputValue());
  request.futures.quotes[0].price.value = '1';
  request.futures.quotes[1].price.value = '21';
  request.spot.vix9d.price.value = '1';
  request.spot.vix.price.value = '3';
  const result = await evaluate(page, request);
  await expect(futuresPanel(page).locator('dd')).toHaveText(['1 / 21', '-20', '1 / 21', '-20 / 21']);
  await expect(spotPanel(page).locator('dd')).toHaveText(['1 / 3', '-2', '1 / 3', '-2 / 3']);
  expect(result.report.futures.metrics).toEqual({
    first_value: '1', second_value: '21', spread_points: '-20', classification: 'CONTANGO',
    ratio: { numerator: '1', denominator: '21', decimal_exact: null },
    ratio_minus_one: { numerator: '-20', denominator: '21', decimal_exact: null },
  });
  expect(result.report.spot.metrics).toEqual({
    first_value: '1', second_value: '3', spread_points: '-2', classification: 'SHORT_HORIZON_LOWER',
    ratio: { numerator: '1', denominator: '3', decimal_exact: null },
    ratio_minus_one: { numerator: '-2', denominator: '3', decimal_exact: null },
  });
  expect(await exportObservation(page)).toEqual(result);
});

for (const scenario of ['MISSING', 'STALE', 'ASYNCHRONOUS']) {
  test(`${scenario} suppresses metrics in rendered panels and export`, async ({ page }) => {
    await page.goto('/risk');
    await expect(page.getByTestId('report-mode')).toBeVisible();
    await page.getByLabel('Demonstration scenario').selectOption(scenario);
    await expect(page.locator('.classification')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Export observation JSON' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Load demonstration' }).click();
    const status = scenario === 'ASYNCHRONOUS' ? 'INVALID' : scenario;
    await expect(futuresPanel(page).locator('.status')).toContainText(status);
    if (scenario === 'ASYNCHRONOUS') {
      await expect(futuresPanel(page).locator('.status')).toHaveText('INVALID · ASYNCHRONOUS_OR_DIFFERENT_SESSION_QUOTES');
    }
    await expect(futuresPanel(page).locator('dd, .classification')).toHaveCount(0);
    const result = await exportObservation(page);
    expect(result.report.futures.status).toBe(status);
    expect(result.report.futures.metrics).toBeNull();
    if (scenario !== 'STALE') {
      await expect(spotPanel(page).locator('dd')).toHaveText(['27 / 24', '3', '1.125', '0.125']);
      expect(result.report.spot.status).toBe('READY');
      expect(result.report.spot.metrics.spread_points).toBe('3');
    } else {
      await expect(spotPanel(page).locator('dd, .classification')).toHaveCount(0);
      expect(result.report.spot.status).toBe('STALE');
      expect(result.report.spot.metrics).toBeNull();
    }
  });
}

test('missing spot leaves futures metrics independently ready', async ({ page }) => {
  await page.goto('/risk');
  await expect(page.getByTestId('report-mode')).toBeVisible();
  const request = JSON.parse(await page.getByLabel('Exact request JSON').inputValue());
  request.spot = null;
  const result = await evaluate(page, request);
  await expect(spotPanel(page).locator('.status')).toHaveText('MISSING · SPOT_INPUT_REQUIRED');
  await expect(spotPanel(page).locator('dd, .classification')).toHaveCount(0);
  await expect(futuresPanel(page).locator('dd')).toHaveText(['24 / 20', '4', '1.2', '0.2']);
  expect(result.report.futures.status).toBe('READY');
  expect(result.report.spot.metrics).toBeNull();
  expect(await exportObservation(page)).toEqual(result);
});

test('observer fits a mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/risk');
  await expect(page.locator('p.classification').filter({ hasText: 'BACKWARDATION' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
