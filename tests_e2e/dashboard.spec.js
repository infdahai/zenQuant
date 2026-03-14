import { expect, test } from "@playwright/test";

test("dashboard renders decision path and key trading state", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "ZenQuant Indicators" })).toBeVisible();
  await expect(page.getByText("Decision Path")).toBeVisible();
  await expect(page.getByText("1D bias")).toBeVisible();
  await expect(page.getByText("Latest target")).toBeVisible();
  await expect(page.getByText("Last execution")).toBeVisible();
  await expect(page.getByText("Price Curve")).toBeVisible();
});

test("dashboard reaches a live websocket stream", async ({ page }) => {
  await page.goto("/");

  const status = page.getByTestId("stream-status");
  const update = page.getByTestId("stream-update");

  await expect(status).toHaveText("live");
  await expect(update).not.toHaveText("awaiting first tick");
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
