// Presentation helpers ported from the legacy utils.js

export const INDICATOR_DEFAULT_KEYS = [
  "last_price",
  "funding_oi_weighted",
  "funding_oi_z",
  "cvd_slope",
  "ob_liq_z",
  "overall_ob_liq_z",
  "realized_vol",
  "atr",
  "momentum_roc",
  "momentum_roc_z",
  "zen_pulse",
  "structure_state",
];

export function collectIndicatorKeys(
  indicator: Record<string, unknown> | null,
  proxy: Record<string, unknown> = {},
): string[] {
  const merged = new Set(INDICATOR_DEFAULT_KEYS);
  if (indicator && typeof indicator === "object" && "values" in indicator) {
    const values = indicator.values as Record<string, unknown>;
    for (const key of Object.keys(values)) {
      merged.add(key);
    }
  }
  for (const key of Object.keys(proxy || {})) {
    merged.add(key);
  }
  return [...merged].sort();
}

export function formatNumber(value: unknown, digits = 4): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  return num.toFixed(digits);
}

export function formatCurrency(value: unknown, digits = 2): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(num);
}

export function formatSignedCurrency(value: unknown, digits = 2): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  const amount = formatCurrency(Math.abs(num), digits);
  return num > 0 ? `+${amount}` : num < 0 ? `-${amount}` : amount;
}

export function formatPercent(value: unknown, digits = 2): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  return `${(num * 100).toFixed(digits)}%`;
}

export function formatSignedPercent(value: unknown, digits = 2): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  const pct = formatPercent(Math.abs(num), digits);
  return num > 0 ? `+${pct}` : num < 0 ? `-${pct}` : pct;
}

export function formatDisplayValue(value: unknown, digits = 4): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return formatNumber(value, digits);
  }
  if (value === null || value === undefined) return "-";
  return `${value}`;
}

export function formatCompact(value: unknown): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(num);
}

export function formatRelativeTimeFromNs(
  tsNs: unknown,
  nowMs = Date.now(),
): string {
  const ts = Number(tsNs);
  if (!Number.isFinite(ts) || ts <= 0) return "awaiting live market data";
  const ageMs = Math.max(0, nowMs - ts / 1_000_000);
  if (ageMs < 1_000) return "just now";
  if (ageMs < 60_000) return `${Math.round(ageMs / 1_000)}s ago`;
  if (ageMs < 3_600_000) return `${Math.round(ageMs / 60_000)}m ago`;
  return `${Math.round(ageMs / 3_600_000)}h ago`;
}

export function formatBiasLabel(
  direction: unknown,
  hasSignal = true,
): string {
  const num = Number(direction);
  if (!hasSignal || !Number.isFinite(num)) return "NO SIGNAL";
  if (num > 0) return "LONG (1)";
  if (num < 0) return "SHORT (-1)";
  return "FLAT (0)";
}

export function formatRegimeLabel(regime: unknown): string {
  const token = String(regime || "").toUpperCase();
  if (!token) return "Awaiting regime";
  if (token === "TREND") return "Trend";
  if (token === "RANGE") return "Range";
  if (token === "STRESS") return "Stress";
  return token
    .split("_")
    .map((part) => part.charAt(0) + part.slice(1).toLowerCase())
    .join(" ");
}

export function formatCodes(
  values: unknown[] | undefined,
  limit = 3,
): string {
  if (!Array.isArray(values) || !values.length) return "None";
  return values
    .slice(0, limit)
    .map((v) => humanizeCode(v))
    .join(" | ");
}

const HUMANIZE_LOOKUP: Record<string, string> = {
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
  EXIT_INVALIDATION: "Playbook invalidation exit",
  EXIT_REGIME_CHANGE: "Regime-change exit",
  EXIT_TIME_DECAY: "Time-decay exit",
  EXIT_PROFIT_LOCK: "Profit lock exit",
  FLOW_PROXY_VOLUME: "Flow uses volume proxy",
  KILL_SWITCH: "Kill switch",
  LIQUIDITY_STRESS: "Liquidity stress",
  NO_DIRECTION: "No direction",
  PLAYBOOK_ALLOW_BOTH: "Playbook allows both directions",
  PLAYBOOK_ALLOW_LONG: "Playbook allows long only",
  PLAYBOOK_ALLOW_SHORT: "Playbook allows short only",
  PLAYBOOK_BEAR_DEFENSE: "Bear defense playbook",
  PLAYBOOK_BULL_CONTINUATION: "Bull continuation playbook",
  PLAYBOOK_CAPITAL_PRESERVATION: "Capital preservation playbook",
  PLAYBOOK_DIRECTION_BLOCK: "Blocked by active playbook",
  PLAYBOOK_PYRAMID_BLOCK: "Pyramid blocked by playbook",
  PLAYBOOK_RANGE_LIGHT: "Range-light playbook",
  PLAYBOOK_REDUCE_ONLY: "Playbook reduce-only mode",
  QUALITY_DEGRADED: "Quality degraded",
  QUALITY_UNSAFE: "Quality unsafe",
  REDUCE_ONLY_BLOCK_ADD: "Reduce-only blocked new risk",
  REGIME_RANGE: "Range regime",
  REGIME_STRESS: "Stress regime",
  REGIME_TREND: "Trend regime",
  SIZE_BASE_VOL_TARGET: "Vol-target base size",
  SIZE_CONFIDENCE_BLEND: "State/playbook confidence blend",
  SIZE_EXECUTION_HAIRCUT: "Execution haircut applied",
  SIZE_FACTOR_CAPPED: "Sizer factor capped",
  SIZE_MAX_LEVERAGE_CAP: "Capped by playbook max leverage",
  SIZE_VOLATILITY_STRESS: "Volatility stress discount",
  CONVICTION_BOOST: "Conviction boost",
  SKEW_PARTIAL: "Skew surface partial",
  TERM_STRUCTURE_PARTIAL: "Term structure partial",
  TRANSITION_RISK: "Transition-risk de-risking",
  UNPROFITABLE: "Position still losing",
  SEVERE_CROWDING: "Severe crowding",
  TREND_LONG: "Trend long",
  TREND_SHORT: "Trend short",
  VOL_SHOCK: "Volatility shock",
};

export function humanizeCode(token: unknown): string {
  const key = String(token || "").toUpperCase();
  if (HUMANIZE_LOOKUP[key]) return HUMANIZE_LOOKUP[key];
  return key
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0) + part.slice(1).toLowerCase())
    .join(" ");
}

export type Tone = "ok" | "warning" | "danger";

export interface MarketHealth {
  label: string;
  tone: Tone;
  detail: string;
  ageMs: number;
}

export function getMarketHealth(
  runtimeStatus: Record<string, unknown>,
  nowMs = Date.now(),
): MarketHealth {
  const status = runtimeStatus || {};
  const bootstrapPhase = String(
    (status as Record<string, unknown>).bootstrap_phase || "",
  ).toLowerCase();

  if (bootstrapPhase === "bootstrapping") {
    return {
      label: "BOOTSTRAPPING",
      tone: "warning",
      detail:
        (status.bootstrap_detail as string) ||
        "Warming live context before connecting feed",
      ageMs: Infinity,
    };
  }

  const lastNs = Number(status.last_market_event_ns || 0);

  if (
    bootstrapPhase === "failed" &&
    (!status.market_connected || lastNs <= 0)
  ) {
    return {
      label: "STARTING",
      tone: "warning",
      detail:
        (status.bootstrap_detail as string) ||
        "Bootstrap failed; waiting for live feed",
      ageMs: Infinity,
    };
  }

  if (
    String(status.mode || "").toLowerCase() === "demo" &&
    status.market_connected
  ) {
    return { label: "LIVE", tone: "ok", detail: "Simulated feed active", ageMs: 0 };
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

// Category mapping for indicator chart normalization
export function toNumeric(
  key: string,
  value: unknown,
  categoryMaps: Record<string, Record<string, number>>,
): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "boolean") return value ? 1 : 0;
  if (value === null || value === undefined) return 0;
  const asNumber = Number(value);
  if (Number.isFinite(asNumber)) return asNumber;
  const token = String(value);
  if (!categoryMaps[key]) categoryMaps[key] = {};
  const map = categoryMaps[key];
  if (map[token] === undefined) map[token] = Object.keys(map).length;
  return map[token];
}

export function normalizeIndicatorSeries(
  key: string,
  history: Record<string, unknown>[],
  categoryMaps: Record<string, Record<string, number>>,
): number[] {
  return history.map((point) => {
    const raw =
      point && (point as Record<string, Record<string, unknown>>).values
        ? (point as Record<string, Record<string, unknown>>).values[key]
        : null;
    return toNumeric(key, raw, categoryMaps);
  });
}
