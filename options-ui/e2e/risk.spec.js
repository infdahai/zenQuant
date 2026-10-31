import { test, expect } from '@playwright/test';

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

test('observer fits a mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/risk');
  await expect(page.locator('p.classification').filter({ hasText: 'BACKWARDATION' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
