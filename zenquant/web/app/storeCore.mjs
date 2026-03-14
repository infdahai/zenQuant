const MAX_HISTORY_POINTS = 400;

export function createMarketStoreController(state, api) {
  let socket = null;
  let reconnectTimer = null;
  let heartbeatTimer = null;
  let requestVersion = 0;

  async function reload() {
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
    reconnectSocket(version, selection);
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

  function reconnectSocket(version = requestVersion, selection = currentSelection()) {
    cleanupSocket();
    socket = api.connectIndicatorSocket(
      selection.symbol,
      selection.tf,
      (payload) => {
        if (!isCurrent(version, selection)) {
          return;
        }
        upsertIndicator(payload, selection);
      },
      () => {
        if (!isCurrent(version, selection)) {
          return;
        }
        if (reconnectTimer) {
          clearTimeout(reconnectTimer);
        }
        reconnectTimer = setTimeout(() => {
          void reload();
        }, 1200);
      },
    );

    heartbeatTimer = setInterval(() => {
      const openState = globalThis.WebSocket ? globalThis.WebSocket.OPEN : 1;
      if (!socket || socket.readyState !== openState) {
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
    state.snapshot = null;
    state.indicatorHistory = [];
    state.latestIndicator = null;
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
