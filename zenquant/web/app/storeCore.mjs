// Realtime store controller used by the Vue pages.
//
// The controller keeps two concerns separate:
// 1. fetch a clean baseline snapshot/history for the current symbol/timeframe
// 2. keep that baseline fresh with state and indicator websocket streams

const MAX_HISTORY_POINTS = 400;

/**
 * Bind fetch/reconnect logic to the shared reactive store.
 *
 * Example:
 *   const controller = createMarketStoreController(state, apiClient);
 *   controller.reload();
 */
export function createMarketStoreController(state, api) {
  let indicatorSocket = null;
  let stateSocket = null;
  let reconnectTimer = null;
  let heartbeatTimer = null;
  let requestVersion = 0;

  async function reload() {
    // A monotonically increasing request version prevents stale responses from
    // an old symbol/timeframe selection from overwriting the current UI.
    state.lastError = "";
    const version = ++requestVersion;
    const selection = { symbol: state.symbol, tf: state.tf };
    await Promise.all([
      refreshState(version, selection),
      refreshIndicatorHistory(version, selection),
    ]);
    if (!isCurrent(version, selection)) {
      return;
    }
    reconnectSockets(version, selection);
  }

  async function refreshState(version = requestVersion, selection = currentSelection()) {
    try {
      const payload = await api.fetchState(selection.symbol, selection.tf);
      if (!isCurrent(version, selection)) {
        return;
      }
      state.snapshot = payload;
      if (payload && payload.latest_indicator) {
        upsertIndicator(payload, selection);
      }
    } catch (err) {
      if (!isCurrent(version, selection)) {
        return;
      }
      state.lastError = err instanceof Error ? err.message : String(err);
    }
  }

  async function refreshIndicatorHistory(
    version = requestVersion,
    selection = currentSelection(),
  ) {
    try {
      const history = await api.fetchIndicatorHistory(selection.symbol, selection.tf);
      if (!isCurrent(version, selection)) {
        return;
      }
      if (Array.isArray(history)) {
        state.indicatorHistory = history.slice(-MAX_HISTORY_POINTS);
        const last = state.indicatorHistory[state.indicatorHistory.length - 1];
        if (last) {
          state.latestIndicator = last;
        }
      }
    } catch (err) {
      if (!isCurrent(version, selection)) {
        return;
      }
      state.lastError = err instanceof Error ? err.message : String(err);
    }
  }

  function upsertIndicator(point, selection = currentSelection()) {
    // Indicator websocket updates can race with REST history loads. Replace the
    // last point when the cutoff matches; append otherwise.
    if (!point || typeof point !== "object") {
      return;
    }
    if (!matchesSelection(point, selection)) {
      return;
    }
    const latestIndicator = point.latest_indicator ?? point;
    state.latestIndicator = latestIndicator;
    const cutoff = Number(latestIndicator.cutoff_time_ns || 0);
    if (!Number.isFinite(cutoff) || cutoff <= 0) {
      return;
    }

    const history = state.indicatorHistory;
    const last = history[history.length - 1];
    if (last && Number(last.cutoff_time_ns || 0) === cutoff) {
      history[history.length - 1] = latestIndicator;
      return;
    }

    history.push(latestIndicator);
    if (history.length > MAX_HISTORY_POINTS) {
      history.splice(0, history.length - MAX_HISTORY_POINTS);
    }
  }

  function reconnectSockets(version = requestVersion, selection = currentSelection()) {
    // State and indicator sockets carry different update frequencies. Keeping
    // both allows the dashboard to move on every state change without waiting
    // for a new indicator pack.
    cleanupSocket();
    state.streamStatus = "connecting";
    stateSocket = api.connectStateSocket(
      selection.symbol,
      selection.tf,
      (payload) => {
        if (!isCurrent(version, selection)) {
          return;
        }
        state.streamStatus = "live";
        state.snapshot = payload;
        const runtime = payload && payload.runtime_status ? payload.runtime_status : {};
        const lastNs = Number(runtime.last_market_event_ns || 0);
        if (lastNs > 0) {
          state.lastLiveUpdate = new Date(lastNs / 1_000_000).toISOString();
        }
        if (payload && payload.latest_indicator) {
          upsertIndicator(payload, selection);
        }
      },
      () => {
        if (!isCurrent(version, selection)) {
          return;
        }
        state.streamStatus = "reconnecting";
        if (reconnectTimer) {
          clearTimeout(reconnectTimer);
        }
        reconnectTimer = setTimeout(() => {
          void reload();
        }, 1200);
      },
      () => {
        if (!isCurrent(version, selection)) {
          return;
        }
        state.streamStatus = "live";
      },
    );

    indicatorSocket = api.connectIndicatorSocket(
      selection.symbol,
      selection.tf,
      (payload) => {
        if (!isCurrent(version, selection)) {
          return;
        }
        state.streamStatus = "live";
        state.lastLiveUpdate = String(
          payload.cutoff_time || payload.ts || new Date().toISOString(),
        );
        upsertIndicator(payload, selection);
      },
      () => {
        if (!isCurrent(version, selection)) {
          return;
        }
        state.streamStatus = "reconnecting";
        if (reconnectTimer) {
          clearTimeout(reconnectTimer);
        }
        reconnectTimer = setTimeout(() => {
          void reload();
        }, 1200);
      },
      () => {
        if (!isCurrent(version, selection)) {
          return;
        }
        state.streamStatus = "live";
      },
    );

    heartbeatTimer = setInterval(() => {
      const openState = globalThis.WebSocket ? globalThis.WebSocket.OPEN : 1;
      if (stateSocket && stateSocket.readyState === openState) {
        stateSocket.send("ping");
      }
      if (indicatorSocket && indicatorSocket.readyState === openState) {
        indicatorSocket.send("ping");
      }
    }, 15000);
  }

  function cleanupSocket() {
    // Centralized cleanup avoids orphaned timers and stale websocket handles
    // when the user switches symbol/timeframe quickly.
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
    if (stateSocket) {
      stateSocket.close();
      stateSocket = null;
    }
    if (indicatorSocket) {
      indicatorSocket.close();
      indicatorSocket = null;
    }
    state.streamStatus = "idle";
  }

  function setSelection(symbol, tf) {
    // Reset derived UI state so the next selection starts from a clean slate.
    state.symbol = symbol;
    state.tf = tf;
    state.snapshot = null;
    state.indicatorHistory = [];
    state.latestIndicator = null;
    state.lastLiveUpdate = "";
    state.categoryMaps = {};
    void reload();
  }

  function currentSelection() {
    return { symbol: state.symbol, tf: state.tf };
  }

  function isCurrent(version, selection) {
    return (
      version === requestVersion &&
      selection.symbol === state.symbol &&
      selection.tf === state.tf
    );
  }

  function matchesSelection(point, selection) {
    const candidate = point.latest_indicator ?? point;
    const pointSymbol = String(candidate.symbol || point.symbol || "");
    const pointTf = String(candidate.tf || candidate.timeframe || point.tf || point.timeframe || "");
    return pointSymbol === selection.symbol && pointTf === selection.tf;
  }

  return {
    reload,
    refreshState,
    setSelection,
    cleanupSocket,
  };
}
