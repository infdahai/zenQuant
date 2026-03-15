// Small wrapper that combines reactive UI state with the websocket controller.

import { reactive } from "/static/vendor/vue.esm-browser.prod.js";
import { connectIndicatorSocket, connectStateSocket, fetchIndicatorHistory, fetchState } from "./api.js";
import { createMarketStoreController } from "./storeCore.mjs";

export function createMarketStore(
  apiClient = { fetchState, fetchIndicatorHistory, connectIndicatorSocket, connectStateSocket },
) {
  // Keep the selection and most recent data in one plain reactive object so
  // every page/component reads the same live source of truth.
  const state = reactive({
    symbol: "BTCUSDT_PERP",
    tf: "4h",
    snapshot: null,
    latestIndicator: null,
    indicatorHistory: [],
    categoryMaps: {},
    source: "live",
    streamStatus: "idle",
    lastLiveUpdate: "",
    lastError: "",
  });

  return {
    state,
    ...createMarketStoreController(state, apiClient),
  };
}
