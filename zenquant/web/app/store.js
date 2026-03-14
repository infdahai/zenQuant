import { reactive } from "/static/vendor/vue.esm-browser.prod.js";
import { connectIndicatorSocket, fetchIndicatorHistory, fetchState } from "./api.js";

const MAX_HISTORY_POINTS = 400;

export function createMarketStore() {
  const state = reactive({
    symbol: "BTCUSDT_PERP",
    tf: "4h",
    snapshot: null,
    latestIndicator: null,
    indicatorHistory: [],
    categoryMaps: {},
    source: "live",
    lastError: "",
  });

  let socket = null;
  let reconnectTimer = null;
  let heartbeatTimer = null;

  async function reload() {
    state.lastError = "";
    await Promise.all([refreshState(), refreshIndicatorHistory()]);
    reconnectSocket();
  }

  async function refreshState() {
    try {
      const payload = await fetchState(state.symbol, state.tf);
      state.snapshot = payload;
      if (payload && payload.latest_indicator) {
        upsertIndicator(payload.latest_indicator);
      }
    } catch (err) {
      state.lastError = err instanceof Error ? err.message : String(err);
    }
  }

  async function refreshIndicatorHistory() {
    try {
      const history = await fetchIndicatorHistory(state.symbol, state.tf);
      if (Array.isArray(history)) {
        state.indicatorHistory = history.slice(-MAX_HISTORY_POINTS);
        const last = state.indicatorHistory[state.indicatorHistory.length - 1];
        if (last) {
          state.latestIndicator = last;
        }
      }
    } catch (err) {
      state.lastError = err instanceof Error ? err.message : String(err);
    }
  }

  function upsertIndicator(point) {
    if (!point || typeof point !== "object") {
      return;
    }
    state.latestIndicator = point;
    const cutoff = Number(point.cutoff_time_ns || 0);
    if (!Number.isFinite(cutoff) || cutoff <= 0) {
      return;
    }

    const history = state.indicatorHistory;
    const last = history[history.length - 1];
    if (last && Number(last.cutoff_time_ns || 0) === cutoff) {
      history[history.length - 1] = point;
      return;
    }

    history.push(point);
    if (history.length > MAX_HISTORY_POINTS) {
      history.splice(0, history.length - MAX_HISTORY_POINTS);
    }
  }

  function reconnectSocket() {
    cleanupSocket();
    socket = connectIndicatorSocket(
      state.symbol,
      state.tf,
      (payload) => {
        upsertIndicator(payload);
      },
      () => {
        if (reconnectTimer) {
          clearTimeout(reconnectTimer);
        }
        reconnectTimer = setTimeout(() => {
          reconnectSocket();
        }, 1200);
      },
    );

    heartbeatTimer = setInterval(() => {
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        return;
      }
      socket.send("ping");
    }, 15000);
  }

  function cleanupSocket() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
    if (socket) {
      socket.close();
      socket = null;
    }
  }

  function setSelection(symbol, tf) {
    state.symbol = symbol;
    state.tf = tf;
    state.indicatorHistory = [];
    state.latestIndicator = null;
    state.categoryMaps = {};
    reload();
  }

  return {
    state,
    reload,
    refreshState,
    setSelection,
    cleanupSocket,
  };
}
