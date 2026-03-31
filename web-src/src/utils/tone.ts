// Tone mapping and state description helpers extracted from DashboardPage.js

import {
  formatBiasLabel,
  formatNumber,
  formatPercent,
  formatRegimeLabel,
  humanizeCode,
  type MarketHealth,
  type Tone,
} from "./format";

export function toneFromState(kind: string, token: unknown): Tone {
  const value = String(token || "").toUpperCase();
  if (kind === "quality") {
    if (value === "UNSAFE" || value === "DEGRADED") return "danger";
    if (value === "NOISY") return "warning";
    return "ok";
  }
  if (kind === "volatility") {
    if (value === "DISTORTED") return "danger";
    if (value === "HIGH") return "warning";
    return "ok";
  }
  if (kind === "positioning") {
    if (
      value === "CROWDED_LONG" ||
      value === "CROWDED_SHORT" ||
      value === "UNCERTAIN"
    )
      return "warning";
    return "ok";
  }
  if (kind === "directional" && value === "TRANSITION") return "warning";
  return "ok";
}

export function describeDirectionalState(token: unknown): string {
  const value = String(token || "").toUpperCase();
  if (value === "UPTREND")
    return "Structure and momentum agree on upside continuation.";
  if (value === "DOWNTREND")
    return "Structure and momentum agree on downside continuation.";
  if (value === "TRANSITION")
    return "Trend structure is contested, so conviction should stay low.";
  return "Directional edge is weak or intentionally flat.";
}

export function describeVolatilityState(token: unknown): string {
  const value = String(token || "").toUpperCase();
  if (value === "DISTORTED")
    return "Market conditions are stressed enough to question normal execution assumptions.";
  if (value === "HIGH")
    return "Volatility is elevated, so sizing and stops deserve extra caution.";
  if (value === "LOW")
    return "Compression is building and breakout follow-through matters more than churn.";
  return "Volatility is inside normal operating bounds.";
}

export function describePositioningState(token: unknown): string {
  const value = String(token || "").toUpperCase();
  if (value === "CROWDED_LONG")
    return "Long positioning is crowded, so upside needs cleaner follow-through.";
  if (value === "CROWDED_SHORT")
    return "Short positioning is crowded, which raises squeeze risk.";
  if (value === "UNCERTAIN")
    return "Positioning signals are mixed and should not drive aggressive size.";
  return "Positioning looks balanced enough for normal playbook behavior.";
}

export function describeQualityState(token: unknown): string {
  const value = String(token || "").toUpperCase();
  if (value === "UNSAFE")
    return "Data quality is not good enough to justify new risk.";
  if (value === "DEGRADED")
    return "Inputs are degraded, so live signals deserve skepticism.";
  if (value === "NOISY")
    return "Data is usable, but the system should expect more friction and false moves.";
  return "Data quality is clean enough for normal bar-close decisions.";
}

export function describeConfidence(confidence: number | null): string {
  const value = Number(confidence);
  if (!Number.isFinite(value)) return "No confirmed confidence profile yet";
  if (value >= 0.8)
    return "Classification is stable across all four dimensions";
  if (value >= 0.65)
    return "State is usable, but one dimension is softer than the rest";
  if (value >= 0.5)
    return "Confidence is moderate, so watch for transition before adding risk";
  return "Confidence is fragile and the strategy should stay cautious";
}

export function describeTransitionRisk(risk: unknown): string {
  const value = Number(risk);
  if (!Number.isFinite(value)) return "No transition profile yet";
  if (value >= 0.6)
    return "The state is unstable and could flip on the next confirmed bar";
  if (value >= 0.35)
    return "One dimension is soft enough that the current thesis needs monitoring";
  return "The current state is behaving consistently across dimensions";
}

export function toPercentWidth(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "0%";
  return `${Math.max(0, Math.min(100, numeric * 100))}%`;
}

// ── Thesis summary builder ──

export interface ThesisChip {
  label: string;
  value: string;
  tone: Tone;
}

export interface ThesisSummary {
  tone: Tone;
  headline: string;
  detail: string;
  action: string;
  confidenceCopy: string;
  confidenceDetail: string;
  transitionCopy: string;
  transitionDetail: string;
  transitionWidth: string;
  chips: ThesisChip[];
}

export function buildThesisSummary(params: {
  state: Record<string, unknown> | null;
  playbook: Record<string, unknown> | null;
  directionalBias: Record<string, unknown> | null;
  latestTarget: Record<string, unknown> | null;
  latestRiskDecision: Record<string, unknown> | null;
  marketHealth: MarketHealth;
  stateConfidence: number | null;
}): ThesisSummary {
  const {
    state,
    playbook,
    directionalBias,
    latestTarget,
    latestRiskDecision,
    marketHealth,
    stateConfidence,
  } = params;

  if (!state) {
    return {
      tone: "warning",
      headline: "Warming the state engine",
      detail:
        "The runtime is live, but state only publishes after a confirmed bar-close classification.",
      action:
        "Let the first 4H/1D state settle before judging strategy posture or trusting target changes.",
      confidenceCopy: "-",
      confidenceDetail: "No confirmed state confidence yet",
      transitionCopy: "Transition risk unavailable",
      transitionDetail:
        "State stability appears after the first classification",
      transitionWidth: "0%",
      chips: [
        { label: "Feed", value: marketHealth.label, tone: marketHealth.tone },
        {
          label: "Playbook",
          value: playbook
            ? humanizeCode(playbook.playbook_id)
            : "Awaiting playbook",
          tone: "warning",
        },
        {
          label: "Bias",
          value: directionalBias
            ? formatBiasLabel(directionalBias.direction, true)
            : "Awaiting bias",
          tone: "warning",
        },
        {
          label: "Target",
          value: latestTarget
            ? `${formatNumber(latestTarget.target_qty, 4)} BTC`
            : "No target",
          tone: "warning",
        },
      ],
    };
  }

  const targetQty = latestTarget ? Number(latestTarget.target_qty || 0) : 0;
  const transitionRisk = Number(state.transition_risk || 0);
  const riskFlags =
    latestRiskDecision && Array.isArray(latestRiskDecision.flags)
      ? (latestRiskDecision.flags as string[]).filter(
          (flag: string) => flag !== "OK",
        )
      : [];

  let tone: Tone = "ok";
  if (
    marketHealth.tone === "danger" ||
    String(state.quality_state) === "UNSAFE" ||
    String(state.volatility_state) === "DISTORTED"
  ) {
    tone = "danger";
  } else if (
    marketHealth.tone === "warning" ||
    transitionRisk >= 0.45 ||
    String(state.positioning_state).startsWith("CROWDED") ||
    String(state.quality_state) !== "TRADABLE" ||
    riskFlags.length
  ) {
    tone = "warning";
  }

  let headline = "Strategy is waiting for a cleaner edge";
  let detail = `${formatRegimeLabel(state.regime)} regime with ${humanizeCode(state.volatility_state).toLowerCase()} volatility, ${humanizeCode(state.positioning_state).toLowerCase()} positioning, and ${humanizeCode(state.quality_state).toLowerCase()} quality.`;
  let action = "The system is flat until the state improves.";

  if (playbook) {
    detail = `${detail} Active playbook: ${humanizeCode(playbook.playbook_id).toLowerCase()} (${formatPercent(playbook.playbook_confidence, 2)} confidence).`;
  }

  if (String(state.directional_state) === "UPTREND") {
    headline = "Long thesis is active";
    action =
      Math.abs(targetQty) > 0
        ? `Current intent is ${formatNumber(targetQty, 4)} BTC on the long side.`
        : "The long thesis exists, but the strategy is not adding exposure yet.";
  } else if (String(state.directional_state) === "DOWNTREND") {
    headline = "Short thesis is active";
    action =
      Math.abs(targetQty) > 0
        ? `Current intent is ${formatNumber(targetQty, 4)} BTC on the short side.`
        : "The short thesis exists, but the strategy is waiting for execution alignment.";
  } else if (String(state.directional_state) === "TRANSITION") {
    headline = "State is rotating between regimes";
    action =
      "Keep conviction low until directional structure and momentum agree again.";
  } else if (String(state.directional_state) === "NEUTRAL") {
    headline = "Strategy is preserving capital";
    action =
      "The engine is explicitly neutral and should only re-risk after cleaner confirmation.";
  }

  if (riskFlags.length) {
    action = `Risk overlay is active: ${riskFlags.slice(0, 2).map(humanizeCode).join(" | ")}.`;
  }

  return {
    tone,
    headline,
    detail,
    action,
    confidenceCopy:
      stateConfidence === null ? "-" : formatPercent(stateConfidence, 2),
    confidenceDetail: describeConfidence(stateConfidence),
    transitionCopy: formatPercent(transitionRisk, 2),
    transitionDetail: describeTransitionRisk(transitionRisk),
    transitionWidth: toPercentWidth(transitionRisk),
    chips: [
      {
        label: "Regime",
        value: formatRegimeLabel(state.regime),
        tone: "ok",
      },
      {
        label: "Playbook",
        value: playbook
          ? humanizeCode(playbook.playbook_id)
          : "Awaiting playbook",
        tone:
          playbook && playbook.playbook_id === "CAPITAL_PRESERVATION"
            ? "warning"
            : "ok",
      },
      {
        label: "Direction",
        value: humanizeCode(state.directional_state),
        tone,
      },
      {
        label: "Quality",
        value: humanizeCode(state.quality_state),
        tone: toneFromState("quality", state.quality_state),
      },
      {
        label: "Target",
        value: latestTarget
          ? `${formatNumber(targetQty, 4)} BTC`
          : "No target",
        tone: Math.abs(targetQty) > 0 ? "ok" : "warning",
      },
    ],
  };
}

// ── State cards builder ──

export interface StateCard {
  label: string;
  value: string;
  confidenceCopy: string;
  confidenceWidth: string;
  detail: string;
  tone: Tone;
}

export function buildStateCards(
  state: Record<string, unknown> | null,
): StateCard[] {
  if (!state) return [];
  return [
    buildStateCard(
      "Directional",
      state.directional_state,
      state.directional_confidence,
      describeDirectionalState(state.directional_state),
      toneFromState("directional", state.directional_state),
    ),
    buildStateCard(
      "Volatility",
      state.volatility_state,
      state.volatility_confidence,
      describeVolatilityState(state.volatility_state),
      toneFromState("volatility", state.volatility_state),
    ),
    buildStateCard(
      "Positioning",
      state.positioning_state,
      state.positioning_confidence,
      describePositioningState(state.positioning_state),
      toneFromState("positioning", state.positioning_state),
    ),
    buildStateCard(
      "Market quality",
      state.quality_state,
      state.quality_confidence,
      describeQualityState(state.quality_state),
      toneFromState("quality", state.quality_state),
    ),
  ];
}

function buildStateCard(
  label: string,
  token: unknown,
  confidence: unknown,
  detail: string,
  tone: Tone,
): StateCard {
  return {
    label,
    value: humanizeCode(token),
    confidenceCopy: `Confidence ${formatPercent(confidence, 2)}`,
    confidenceWidth: toPercentWidth(confidence),
    detail,
    tone,
  };
}

export function averageStateConfidence(
  state: Record<string, unknown> | null,
): number | null {
  if (!state) return null;
  const scores = [
    Number(state.directional_confidence || 0),
    Number(state.volatility_confidence || 0),
    Number(state.positioning_confidence || 0),
    Number(state.quality_confidence || 0),
  ].filter((v) => Number.isFinite(v));
  if (!scores.length) return null;
  return scores.reduce((sum, v) => sum + v, 0) / scores.length;
}
