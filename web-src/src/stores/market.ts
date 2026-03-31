// Pinia store ported 1:1 from storeCore.mjs + store.js

import { defineStore } from "pinia";
import { reactive, ref } from "vue";
import {
  connectIndicatorSocket,
  connectStateSocket,
  fetchIndicatorHistory,
  fetchState,
} from "../api/client";

const MAX_HISTORY_POINTS = 400;

export const useMarketStore = defineStore("market", () => {
  // ── Reactive state ──
  const symbol = ref("BTCUSDT_PERP");
  const tf = ref("4h");
  const snapshot = ref<Record<string, unknown> | null>(null);
  const latestIndicator = ref<Record<string, unknown> | null>(null);
  const indicatorHistory = ref<Record<string, unknown>[]>([]);
  const categoryMaps = reactive<Record<string, Record<string, number>>>({});
  const streamStatus = ref<"idle" | "connecting" | "live" | "reconnecting">(
    "idle",
  );
  const lastLiveUpdate = ref("");
  const lastError = ref("");

  // ── Private handles ──
  let stateSocket: WebSocket | null = null;
  let indicatorSocket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  let requestVersion = 0;

  // ── Helpers ──
  function currentSelection() {
    return { symbol: symbol.value, tf: tf.value };
  }

  function isCurrent(
    version: number,
    selection: { symbol: string; tf: string },
  ) {
    return (
      version === requestVersion &&
      selection.symbol === symbol.value &&
      selection.tf === tf.value
    );
  }

  function matchesSelection(
    point: Record<string, unknown>,
    selection: { symbol: string; tf: string },
  ) {
    const candidate =
      (point.latest_indicator as Record<string, unknown>) ?? point;
    const pointSymbol = String(
      candidate.symbol || (point as Record<string, unknown>).symbol || "",
    );
    const pointTf = String(
      candidate.tf ||
        candidate.timeframe ||
        (point as Record<string, unknown>).tf ||
        (point as Record<string, unknown>).timeframe ||
        "",
    );
    return pointSymbol === selection.symbol && pointTf === selection.tf;
  }

  function upsertIndicator(
    point: Record<string, unknown>,
    selection = currentSelection(),
  ) {
    if (!point || typeof point !== "object") return;
    if (!matchesSelection(point, selection)) return;

    const ind =
      (point.latest_indicator as Record<string, unknown>) ?? point;
    latestIndicator.value = ind;
    const cutoff = Number((ind as Record<string, unknown>).cutoff_time_ns || 0);
    if (!Number.isFinite(cutoff) || cutoff <= 0) return;

    const history = indicatorHistory.value;
    const last = history[history.length - 1];
    if (
      last &&
      Number((last as Record<string, unknown>).cutoff_time_ns || 0) === cutoff
    ) {
      history[history.length - 1] = ind;
      return;
    }
    history.push(ind);
    if (history.length > MAX_HISTORY_POINTS) {
      history.splice(0, history.length - MAX_HISTORY_POINTS);
    }
  }

  // ── Socket management ──
  function cleanupSocket() {
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
    streamStatus.value = "idle";
  }

  function reconnectSockets(
    version = requestVersion,
    selection = currentSelection(),
  ) {
    cleanupSocket();
    streamStatus.value = "connecting";

    stateSocket = connectStateSocket(selection.symbol, selection.tf, {
      onMessage(payload) {
        if (!isCurrent(version, selection)) return;
        streamStatus.value = "live";
        snapshot.value = payload;
        const runtime = (payload?.runtime_status as Record<string, unknown>) ?? {};
        const lastNs = Number(runtime.last_market_event_ns || 0);
        if (lastNs > 0) {
          lastLiveUpdate.value = new Date(lastNs / 1_000_000).toISOString();
        }
        if (payload?.latest_indicator) {
          upsertIndicator(payload, selection);
        }
      },
      onClose() {
        if (!isCurrent(version, selection)) return;
        streamStatus.value = "reconnecting";
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(() => void reload(), 1200);
      },
      onOpen() {
        if (!isCurrent(version, selection)) return;
        streamStatus.value = "live";
      },
    });

    indicatorSocket = connectIndicatorSocket(selection.symbol, selection.tf, {
      onMessage(payload) {
        if (!isCurrent(version, selection)) return;
        streamStatus.value = "live";
        lastLiveUpdate.value = String(
          payload.cutoff_time || payload.ts || new Date().toISOString(),
        );
        upsertIndicator(payload, selection);
      },
      onClose() {
        if (!isCurrent(version, selection)) return;
        streamStatus.value = "reconnecting";
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(() => void reload(), 1200);
      },
      onOpen() {
        if (!isCurrent(version, selection)) return;
        streamStatus.value = "live";
      },
    });

    heartbeatTimer = setInterval(() => {
      const OPEN = WebSocket.OPEN;
      if (stateSocket?.readyState === OPEN) stateSocket.send("ping");
      if (indicatorSocket?.readyState === OPEN) indicatorSocket.send("ping");
    }, 15000);
  }

  // ── Public actions ──
  async function reload() {
    lastError.value = "";
    const version = ++requestVersion;
    const selection = currentSelection();
    await Promise.all([
      refreshState(version, selection),
      refreshIndicatorHistory(version, selection),
    ]);
    if (!isCurrent(version, selection)) return;
    reconnectSockets(version, selection);
  }

  async function refreshState(
    version = requestVersion,
    selection = currentSelection(),
  ) {
    try {
      const payload = await fetchState(selection.symbol, selection.tf);
      if (!isCurrent(version, selection)) return;
      snapshot.value = payload;
      if (payload?.latest_indicator) {
        upsertIndicator(payload, selection);
      }
    } catch (err) {
      if (!isCurrent(version, selection)) return;
      lastError.value = err instanceof Error ? err.message : String(err);
    }
  }

  async function refreshIndicatorHistory(
    version = requestVersion,
    selection = currentSelection(),
  ) {
    try {
      const history = await fetchIndicatorHistory(
        selection.symbol,
        selection.tf,
      );
      if (!isCurrent(version, selection)) return;
      if (Array.isArray(history)) {
        indicatorHistory.value = history.slice(-MAX_HISTORY_POINTS);
        const last =
          indicatorHistory.value[indicatorHistory.value.length - 1];
        if (last) latestIndicator.value = last;
      }
    } catch (err) {
      if (!isCurrent(version, selection)) return;
      lastError.value = err instanceof Error ? err.message : String(err);
    }
  }

  function setSelection(newSymbol: string, newTf: string) {
    symbol.value = newSymbol;
    tf.value = newTf;
    snapshot.value = null;
    indicatorHistory.value = [];
    latestIndicator.value = null;
    lastLiveUpdate.value = "";
    // Reset category maps
    for (const key of Object.keys(categoryMaps)) {
      delete categoryMaps[key];
    }
    void reload();
  }

  return {
    // state
    symbol,
    tf,
    snapshot,
    latestIndicator,
    indicatorHistory,
    categoryMaps,
    streamStatus,
    lastLiveUpdate,
    lastError,
    // actions
    reload,
    refreshState,
    setSelection,
    cleanupSocket,
  };
});
