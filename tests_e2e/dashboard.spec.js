import { expect, test } from "@playwright/test";

test("dashboard renders decision path and key trading state", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "ZenQuant State Console" })).toBeVisible();
  await expect(page.getByText("Current Thesis")).toBeVisible();
  await expect(page.getByText("Live Overview")).toBeVisible();
  await expect(page.locator(".hero-card span", { hasText: "Data Stream" }).first()).toBeVisible();
  await expect(page.getByText("Total PnL")).toBeVisible();
  await expect(page.getByText("Decision Path")).toBeVisible();
  await expect(page.getByText("Exit & Position")).toBeVisible();
  await expect(page.getByText("4D State")).toBeVisible();
  await expect(page.getByText("Options Thermometer")).toBeVisible();
  await expect(page.getByText("1D bias")).toBeVisible();
  await expect(page.getByText("Latest target")).toBeVisible();
  await expect(page.getByText("Last execution")).toBeVisible();
  await expect(page.getByText("Position status")).toBeVisible();
  await expect(page.getByText("Exit decision")).toBeVisible();
  await expect(page.getByText("Price Curve")).toBeVisible();
  await expect(page.getByText("Alert Panel")).toBeVisible();
  await expect(page.getByText("Execution Log")).toBeVisible();
  await expect(page.getByRole("button", { name: "Expand All" })).toBeVisible();
});

test("exit and position panel exposes operator-facing values", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByTestId("position-status")).toHaveText(/Flat|Long|Short/);
  await expect(page.getByTestId("position-pnl")).not.toHaveText("");
  await expect(page.getByTestId("exit-decision")).not.toHaveText("");
  await expect(page.getByTestId("trailing-stop")).not.toHaveText("");
  await expect(page.getByTestId("target-reduction")).toHaveText(/%/);
});

test("price curve explains active overlays to the operator", async ({ page }) => {
  await page.goto("/");

  const legend = page.getByTestId("price-overlay-legend");
  await expect(legend).toBeVisible();
  await expect(legend).toContainText(/No active overlays|Long entry|Short entry/);
});

test("dashboard reaches a live websocket stream", async ({ page }) => {
  await page.goto("/");

  const status = page.getByTestId("stream-status");
  const update = page.getByTestId("stream-update");

  await expect(status).toHaveText("LIVE");
  await expect(update).toHaveText(/Simulated feed active|Updated/);
});

test("switching symbol and timeframe keeps the selected context", async ({ page }) => {
  await page.goto("/");

  const selects = page.locator("select");
  await selects.nth(0).selectOption("ETHUSDT_PERP");
  await selects.nth(1).selectOption("1d");

  await expect(selects.nth(0)).toHaveValue("ETHUSDT_PERP");
  await expect(selects.nth(1)).toHaveValue("1d");
  await expect(page.getByText("Decision Path")).toBeVisible();
});
