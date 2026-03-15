import test from "node:test";
import assert from "node:assert/strict";

import { createMarketStoreController } from "../zenquant/web/app/storeCore.mjs";

function deferred() {
  let resolve;
  const promise = new Promise((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

test("store controller ignores stale responses after selection changes", async () => {
  const state = {
    symbol: "BTCUSDT_PERP",
    tf: "4h",
    snapshot: null,
    latestIndicator: null,
    indicatorHistory: [],
    categoryMaps: {},
    source: "live",
    lastError: "",
  };

  const pending = new Map();
  const api = {
    fetchState(symbol, tf) {
      const item = deferred();
      pending.set(`state:${symbol}:${tf}`, item);
      return item.promise;
    },
    fetchIndicatorHistory(symbol, tf) {
      const item = deferred();
      pending.set(`history:${symbol}:${tf}`, item);
      return item.promise;
    },
    connectIndicatorSocket() {
      return {
        readyState: 0,
        close() {},
        send() {},
      };
    },
    connectStateSocket() {
      return {
        readyState: 0,
        close() {},
        send() {},
      };
    },
  };

  const controller = createMarketStoreController(state, api);
  const first = controller.reload();
  controller.setSelection("ETHUSDT_PERP", "1d");

  pending.get("state:ETHUSDT_PERP:1d").resolve({
    symbol: "ETHUSDT_PERP",
    tf: "1d",
    latest_indicator: { symbol: "ETHUSDT_PERP", tf: "1d", cutoff_time_ns: 2 },
  });
  pending.get("history:ETHUSDT_PERP:1d").resolve([
    { symbol: "ETHUSDT_PERP", tf: "1d", cutoff_time_ns: 2, values: {} },
  ]);

  pending.get("state:BTCUSDT_PERP:4h").resolve({
    symbol: "BTCUSDT_PERP",
    tf: "4h",
    latest_indicator: { symbol: "BTCUSDT_PERP", tf: "4h", cutoff_time_ns: 1 },
  });
  pending.get("history:BTCUSDT_PERP:4h").resolve([
    { symbol: "BTCUSDT_PERP", tf: "4h", cutoff_time_ns: 1, values: {} },
  ]);

  await Promise.all([first, new Promise((resolve) => setTimeout(resolve, 0))]);

  assert.equal(state.symbol, "ETHUSDT_PERP");
  assert.equal(state.tf, "1d");
  assert.equal(state.snapshot.symbol, "ETHUSDT_PERP");
  assert.equal(state.latestIndicator.symbol, "ETHUSDT_PERP");
  controller.cleanupSocket();
});
