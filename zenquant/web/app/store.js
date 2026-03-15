import { reactive } from "/static/vendor/vue.esm-browser.prod.js";
import { connectIndicatorSocket, connectStateSocket, fetchIndicatorHistory, fetchState } from "./api.js";
import { createMarketStoreController } from "./storeCore.mjs";

export function createMarketStore(
  apiClient = { fetchState, fetchIndicatorHistory, connectIndicatorSocket, connectStateSocket },
) {
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
