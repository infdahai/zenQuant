// Presentation helpers shared by the dashboard and indicator pages.

export function formatNumber(value, digits = 4) {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return "-";
  }
  return num.toFixed(digits);
}

export function formatCurrency(value, digits = 2) {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return "-";
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(num);
}

export function formatSignedCurrency(value, digits = 2) {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return "-";
  }
  const amount = formatCurrency(Math.abs(num), digits);
  return num > 0 ? `+${amount}` : num < 0 ? `-${amount}` : amount;
}

export function formatPercent(value, digits = 2) {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return "-";
  }
  return `${(num * 100).toFixed(digits)}%`;
}

export function formatCompact(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return "-";
  }
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(num);
}

export function normalizeIndicatorSeries(key, history, categoryMaps) {
  // Some indicator values are categorical. Map them to stable numeric buckets
  // so the shared chart canvas can still render them.
  return history.map((point) => {
    const raw = point && point.values ? point.values[key] : null;
    return toNumeric(key, raw, categoryMaps);
  });
}

export function formatRelativeTimeFromNs(tsNs, nowMs = Date.now()) {
  const ts = Number(tsNs);
  if (!Number.isFinite(ts) || ts <= 0) {
    return "awaiting live market data";
  }
  const ageMs = Math.max(0, nowMs - ts / 1_000_000);
  if (ageMs < 1_000) {
    return "just now";
  }
  if (ageMs < 60_000) {
    return `${Math.round(ageMs / 1_000)}s ago`;
  }
  if (ageMs < 3_600_000) {
    return `${Math.round(ageMs / 60_000)}m ago`;
  }
  return `${Math.round(ageMs / 3_600_000)}h ago`;
}

export function getMarketHealth(runtimeStatus, nowMs = Date.now()) {
  // Treat feed freshness, not socket-open state alone, as the source of truth
  // for whether the market is actually live.
  const status = runtimeStatus || {};
  const lastNs = Number(status.last_market_event_ns || 0);
  if (String(status.mode || "").toLowerCase() === "demo" && status.market_connected) {
    return {
      label: "LIVE",
      tone: "ok",
      detail: "Simulated feed active",
      ageMs: 0,
    };
  }
  if (!status.market_connected || lastNs <= 0) {
    return {
      label: "DISCONNECTED",
      tone: "danger",
      detail: "No verified live market feed yet",
      ageMs: Infinity,
    };
  }
  const ageMs = Math.max(0, nowMs - lastNs / 1_000_000);
  if (ageMs <= 5_000) {
    return {
      label: "LIVE",
      tone: "ok",
      detail: `Updated ${formatRelativeTimeFromNs(lastNs, nowMs)}`,
      ageMs,
    };
  }
  if (ageMs <= 20_000) {
    return {
      label: "DELAYED",
      tone: "warning",
      detail: `Feed lag ${formatRelativeTimeFromNs(lastNs, nowMs)}`,
      ageMs,
    };
  }
  return {
    label: "STALE",
    tone: "danger",
    detail: `Feed lag ${formatRelativeTimeFromNs(lastNs, nowMs)}`,
    ageMs,
  };
}

export function formatBiasLabel(direction, hasSignal = true) {
  const num = Number(direction);
  if (!hasSignal || !Number.isFinite(num)) {
    return "NO SIGNAL";
  }
  if (num > 0) {
    return "LONG (1)";
  }
  if (num < 0) {
    return "SHORT (-1)";
  }
  return "FLAT (0)";
}

export function formatRegimeLabel(regime) {
  const token = String(regime || "").toUpperCase();
  if (!token) {
    return "Awaiting regime";
  }
  if (token === "TREND") {
    return "Trend";
  }
  if (token === "RANGE") {
    return "Range";
  }
  if (token === "STRESS") {
    return "Stress";
  }
  return token
    .split("_")
    .map((part) => part.charAt(0) + part.slice(1).toLowerCase())
    .join(" ");
}

export function humanizeCode(token) {
  // Keep UI copy readable without leaking internal reason-code names verbatim.
  const lookup = {
    BIAS_DIRECTION_BLOCK: "Blocked by 1D bias",
    BIAS_QUALITY_DEGRADED: "Bias quality degraded",
    BIAS_STALE: "Bias stale",
    CROWDING_DISCOUNT: "Crowding discount",
    CROWDING_EXTREME: "Crowding extreme",
    DAILY_LOSS_LIMIT: "Daily loss limit",
    DD_STEP_1: "Drawdown throttle L1",
    DD_STEP_2: "Drawdown throttle L2",
    DD_STEP_3: "Drawdown throttle L3",
    EXIT_TRAILING_STOP: "Trailing stop exit",
    KILL_SWITCH: "Kill switch",
    LIQUIDITY_STRESS: "Liquidity stress",
    NO_DIRECTION: "No direction",
    QUALITY_DEGRADED: "Quality degraded",
    REDUCE_ONLY_BLOCK_ADD: "Reduce-only blocked new risk",
    REGIME_RANGE: "Range regime",
    REGIME_STRESS: "Stress regime",
    REGIME_TREND: "Trend regime",
    SEVERE_CROWDING: "Severe crowding",
    TREND_LONG: "Trend long",
    TREND_SHORT: "Trend short",
    VOL_SHOCK: "Volatility shock",
  };
  const key = String(token || "").toUpperCase();
  if (lookup[key]) {
    return lookup[key];
  }
  return key
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0) + part.slice(1).toLowerCase())
    .join(" ");
}

export function toNumeric(key, value, categoryMaps) {
  // Category maps are stored by key so labels remain consistent across updates.
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "boolean") {
    return value ? 1 : 0;
  }
  if (value === null || value === undefined) {
    return 0;
  }

  const asNumber = Number(value);
  if (Number.isFinite(asNumber)) {
    return asNumber;
  }

  const token = String(value);
  if (!categoryMaps[key]) {
    categoryMaps[key] = {};
  }
  const map = categoryMaps[key];
  if (map[token] === undefined) {
    map[token] = Object.keys(map).length;
  }
  return map[token];
}
