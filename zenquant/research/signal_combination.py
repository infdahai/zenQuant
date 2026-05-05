"""Research-only signal independence and combination audit."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zenquant.domain import Timeframe
from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig
from zenquant.plugins.profile import build_from_profile
from zenquant.plugins.signal_roles import infer_signal_role
from zenquant.state.engine import StateEngine


@dataclass(slots=True, frozen=True)
class SignalAuditEntry:
    name: str
    role: str
    baseline_weight: float
    observations: int
    ic_mean: float
    rank_ic_mean: float
    ic_stability: float
    hit_rate: float
    avg_abs_correlation: float
    max_abs_correlation: float
    redundant_with: list[str]
    ic_stability_multiplier: float
    correlation_penalty_multiplier: float
    normalized_final_research_weight: float
    recommended_weight_adjustment: str
    allowed_in_direction_search: bool
    allowed_in_state_layer: bool
    allowed_in_execution_layer: bool
    search_bound_policy: str


@dataclass(slots=True, frozen=True)
class SignalCombinationAudit:
    generated_at: str
    symbol: str
    profile_path: str
    active_signals: list[str]
    role_groups: dict[str, list[str]]
    pairwise_correlation_matrix: dict[str, dict[str, float]]
    redundant_pairs: list[dict[str, Any]]
    same_role_redundant_pairs: list[dict[str, Any]]
    cross_role_overlap_pairs: list[dict[str, Any]]
    signals: list[SignalAuditEntry]
    summary: dict[str, Any]


@dataclass(slots=True, frozen=True)
class SignalCandidateEntry:
    name: str
    role: str
    enabled_in_default: bool
    has_data_dependency: bool
    dependency_status: str
    audit_status: str
    blocked_by: str | None
    dependency_detail: str
    recommendation: str


@dataclass(slots=True, frozen=True)
class SignalCandidateInventory:
    generated_at: str
    symbol: str
    profile_path: str
    candidates: list[SignalCandidateEntry]
    summary: dict[str, Any]


class _NullBus:
    def subscribe(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def publish(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _pearson_corr(x: list[float], y: list[float]) -> float:
    n = min(len(x), len(y))
    if n < 5:
        return 0.0
    xa = x[:n]
    ya = y[:n]
    mx = sum(xa) / n
    my = sum(ya) / n
    cov = sum((xa[i] - mx) * (ya[i] - my) for i in range(n))
    sx = sum((v - mx) ** 2 for v in xa) ** 0.5
    sy = sum((v - my) ** 2 for v in ya) ** 0.5
    if sx < 1e-12 or sy < 1e-12:
        return 0.0
    return cov / (sx * sy)


def _rank_corr(x: list[float], y: list[float]) -> float:
    n = min(len(x), len(y))
    if n < 5:
        return 0.0

    def _ranks(vals: list[float]) -> list[float]:
        indexed = sorted(enumerate(vals[:n]), key=lambda item: item[1])
        ranks = [0.0] * n
        for rank, (idx, _) in enumerate(indexed):
            ranks[idx] = float(rank)
        return ranks

    return _pearson_corr(_ranks(x), _ranks(y))


def _rolling_metric(
    signal_values: list[float],
    forward_returns: list[float],
    *,
    window: int,
    fn: Any,
) -> list[float]:
    aligned = min(len(signal_values), len(forward_returns))
    if aligned <= window:
        return []
    values = signal_values[:aligned]
    returns = forward_returns[:aligned]
    metrics: list[float] = []
    for idx in range(window, aligned + 1):
        metrics.append(fn(values[idx - window : idx], returns[idx - window : idx]))
    return metrics


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _signal_data_dependency(
    signal_name: str, *, macro_root: Path, data_root: Path
) -> tuple[bool, str, str | None, str]:
    if signal_name == "etf_flow_state_signal":
        exists = (macro_root / "btc_etf_netflow" / "data.parquet").exists()
        return True, "ready" if exists else "missing", None if exists else "btc_etf_netflow_missing", "btc_etf_netflow"
    if signal_name == "cme_positioning_state_signal":
        exists = (data_root / "cftc" / "cot_btc.parquet").exists() or (
            Path("data/raw/cftc") / "cot_btc.parquet"
        ).exists()
        return True, "ready" if exists else "missing", None if exists else "cme_positioning_missing", "cme_cot"
    if signal_name == "options_vol_signal":
        exists = (data_root / "deribit_options" / "_collector_status.json").exists() or (
            Path("data/raw/deribit_options") / "_collector_status.json"
        ).exists()
        return True, "ready" if exists else "missing", None if exists else "deribit_options_missing", "deribit_options"
    if signal_name == "macro_regime_signal":
        macro_names = ("vix", "dxy", "tlt")
        ready = all((macro_root / name / "data.parquet").exists() for name in macro_names)
        return (
            True,
            "ready" if ready else "partial",
            None if ready else "macro_regime_inputs_incomplete",
            ",".join(macro_names),
        )
    if signal_name == "cross_asset_flow_signal":
        return True, "missing", "eth_flow_dependency_missing", "eth_to_btc_transfer_entropy"
    if signal_name == "vacuum_signal":
        return True, "partial", "live_only_execution_dependency", "live_execution_conditions"
    return False, "native", None, "btc_primary_truth"


def _state_dict(state: Any) -> dict[str, Any]:
    return {
        "regime": state.regime,
        "directional_state": state.directional_state,
        "volatility_state": state.volatility_state,
        "positioning_state": state.positioning_state,
        "quality_state": state.quality_state,
        "transition_risk": state.transition_risk,
        "overall_confidence": state.overall_confidence(),
        "direction_signal": state.direction_signal,
        "quality_flags": list(state.quality_flags),
    }


def build_signal_combination_audit(
    *,
    symbol: str,
    profile_path: Path,
    signal_series: dict[str, list[float]],
    baseline_weights: dict[str, float],
    forward_returns: list[float],
    corr_threshold: float = 0.75,
    rolling_window: int = 60,
) -> SignalCombinationAudit:
    active_signals = list(signal_series.keys())
    role_groups = {"direction": [], "state": [], "execution_modifier": []}
    for name in active_signals:
        role_groups.setdefault(infer_signal_role(name), []).append(name)

    pairwise: dict[str, dict[str, float]] = {name: {} for name in active_signals}
    redundant_pairs: list[dict[str, Any]] = []
    same_role_redundant_pairs: list[dict[str, Any]] = []
    cross_role_overlap_pairs: list[dict[str, Any]] = []
    for left in active_signals:
        for right in active_signals:
            corr = _pearson_corr(signal_series[left], signal_series[right]) if left != right else 1.0
            pairwise[left][right] = round(corr, 4)
            if left < right and abs(corr) >= corr_threshold:
                pair = {
                    "left": left,
                    "right": right,
                    "correlation": round(corr, 4),
                    "left_role": infer_signal_role(left),
                    "right_role": infer_signal_role(right),
                    "shared_role": infer_signal_role(left) == infer_signal_role(right),
                }
                redundant_pairs.append(pair)
                if pair["shared_role"]:
                    same_role_redundant_pairs.append(pair)
                else:
                    cross_role_overlap_pairs.append(pair)
    redundant_pairs.sort(key=lambda item: abs(item["correlation"]), reverse=True)
    same_role_redundant_pairs.sort(key=lambda item: abs(item["correlation"]), reverse=True)
    cross_role_overlap_pairs.sort(key=lambda item: abs(item["correlation"]), reverse=True)

    direction_raw_weights: dict[str, float] = {}
    entries: list[SignalAuditEntry] = []
    for name in active_signals:
        series = signal_series[name]
        aligned = min(len(series), len(forward_returns))
        rank_ics = _rolling_metric(series, forward_returns, window=rolling_window, fn=_rank_corr)
        ics = _rolling_metric(series, forward_returns, window=rolling_window, fn=_pearson_corr)
        role = infer_signal_role(name)
        avg_abs_corr = 0.0
        max_abs_corr = 0.0
        redundant_with: list[str] = []
        peers = [other for other in active_signals if other != name and infer_signal_role(other) == role]
        if peers:
            corr_values = [abs(pairwise[name][other]) for other in peers]
            avg_abs_corr = sum(corr_values) / len(corr_values)
            max_abs_corr = max(corr_values)
            redundant_with = [other for other in peers if abs(pairwise[name][other]) >= corr_threshold]

        ic_mean = sum(ics) / len(ics) if ics else _pearson_corr(series, forward_returns)
        rank_ic_mean = sum(rank_ics) / len(rank_ics) if rank_ics else _rank_corr(series, forward_returns)
        rank_ic_std = (
            (sum((value - rank_ic_mean) ** 2 for value in rank_ics) / len(rank_ics)) ** 0.5 if rank_ics else 0.0
        )
        ic_stability = abs(rank_ic_mean) / rank_ic_std if rank_ic_std > 1e-9 else abs(rank_ic_mean)
        hit_rate = (
            sum(1 for value in rank_ics if (value >= 0 and rank_ic_mean >= 0) or (value < 0 and rank_ic_mean < 0))
            / len(rank_ics)
            if rank_ics
            else 0.5
        )

        ic_multiplier = _clamp(0.25 + max(rank_ic_mean, 0.0) * 10.0 + min(ic_stability, 1.5) * 0.2, 0.25, 1.5)
        corr_penalty = 1.0
        if role == "direction":
            corr_penalty = _clamp(1.0 - max(0.0, avg_abs_corr - 0.35), 0.4, 1.0)
        elif role == "state":
            corr_penalty = 0.6
        else:
            corr_penalty = 0.5

        baseline_weight = float(baseline_weights.get(name, 0.0))
        raw_weight = baseline_weight * ic_multiplier * corr_penalty if role == "direction" else 0.0
        if role == "direction":
            direction_raw_weights[name] = raw_weight

        if role != "direction":
            action = "research_only"
        elif baseline_weight >= 0.30 and rank_ic_mean <= 0.0 and ic_mean <= 0.0:
            action = "downweight"
        elif rank_ic_mean <= 0.0 and ic_mean <= 0.0:
            action = "disable"
        elif avg_abs_corr >= corr_threshold or corr_penalty < 0.75 or ic_multiplier < 0.75:
            action = "downweight"
        else:
            action = "keep"

        entries.append(
            SignalAuditEntry(
                name=name,
                role=role,
                baseline_weight=round(baseline_weight, 4),
                observations=aligned,
                ic_mean=round(ic_mean, 4),
                rank_ic_mean=round(rank_ic_mean, 4),
                ic_stability=round(ic_stability, 4),
                hit_rate=round(hit_rate, 4),
                avg_abs_correlation=round(avg_abs_corr, 4),
                max_abs_correlation=round(max_abs_corr, 4),
                redundant_with=sorted(redundant_with),
                ic_stability_multiplier=round(ic_multiplier, 4),
                correlation_penalty_multiplier=round(corr_penalty, 4),
                normalized_final_research_weight=0.0,
                recommended_weight_adjustment=action,
                allowed_in_direction_search=role == "direction" and action != "disable",
                allowed_in_state_layer=role == "state",
                allowed_in_execution_layer=role == "execution_modifier",
                search_bound_policy=(
                    "frozen"
                    if action == "disable"
                    else "narrow"
                    if role == "direction" and action == "downweight"
                    else "fixed_state"
                    if role == "state"
                    else "fixed_execution"
                    if role == "execution_modifier"
                    else "default"
                ),
            )
        )

    total_direction_weight = sum(direction_raw_weights.values())
    finalized: list[SignalAuditEntry] = []
    for entry in entries:
        normalized = (
            direction_raw_weights.get(entry.name, 0.0) / total_direction_weight
            if total_direction_weight > 1e-12
            else 0.0
        )
        finalized.append(
            SignalAuditEntry(
                **{
                    **asdict(entry),
                    "normalized_final_research_weight": round(normalized, 4),
                }
            )
        )

    finalized.sort(key=lambda item: (item.role != "direction", -item.normalized_final_research_weight, item.name))
    summary = {
        "active_signal_count": len(active_signals),
        "direction_signal_count": len(role_groups["direction"]),
        "state_signal_count": len(role_groups["state"]),
        "execution_modifier_count": len(role_groups["execution_modifier"]),
        "redundant_pair_count": len(redundant_pairs),
        "same_role_redundant_pair_count": len(same_role_redundant_pairs),
        "cross_role_overlap_pair_count": len(cross_role_overlap_pairs),
        "direction_layer_clean": not any(
            pair["left_role"] == "direction" and pair["right_role"] == "direction" for pair in same_role_redundant_pairs
        ),
        "state_layer_clean": not any(
            pair["left_role"] == "state" and pair["right_role"] == "state" for pair in same_role_redundant_pairs
        ),
        "blocked_by": [
            "direction_same_role_redundancy"
            for pair in same_role_redundant_pairs
            if pair["left_role"] == "direction" and pair["right_role"] == "direction"
        ]
        + [
            "state_same_role_redundancy"
            for pair in same_role_redundant_pairs
            if pair["left_role"] == "state" and pair["right_role"] == "state"
        ],
        "recommended_actions": {
            action: sum(1 for item in finalized if item.recommended_weight_adjustment == action)
            for action in ("keep", "downweight", "research_only", "disable")
        },
        "top_direction_weights": [
            {
                "name": item.name,
                "weight": item.normalized_final_research_weight,
                "baseline_weight": item.baseline_weight,
            }
            for item in finalized
            if item.role == "direction"
        ][:5],
        "high_correlation_pairs": redundant_pairs[:5],
        "same_role_redundant_pairs": same_role_redundant_pairs[:5],
        "cross_role_overlap_pairs": cross_role_overlap_pairs[:5],
    }
    return SignalCombinationAudit(
        generated_at=datetime.now(UTC).isoformat(),
        symbol=symbol,
        profile_path=str(profile_path),
        active_signals=active_signals,
        role_groups={role: sorted(names) for role, names in role_groups.items()},
        pairwise_correlation_matrix=pairwise,
        redundant_pairs=redundant_pairs,
        same_role_redundant_pairs=same_role_redundant_pairs,
        cross_role_overlap_pairs=cross_role_overlap_pairs,
        signals=finalized,
        summary=summary,
    )


def build_markdown_audit(audit: SignalCombinationAudit) -> str:
    lines = [
        "# Signal Independence Audit",
        "",
        f"- generated_at: `{audit.generated_at}`",
        f"- symbol: `{audit.symbol}`",
        f"- profile_path: `{audit.profile_path}`",
        f"- active_signal_count: `{audit.summary['active_signal_count']}`",
        f"- redundant_pair_count: `{audit.summary['redundant_pair_count']}`",
        f"- same_role_redundant_pair_count: `{audit.summary['same_role_redundant_pair_count']}`",
        f"- cross_role_overlap_pair_count: `{audit.summary['cross_role_overlap_pair_count']}`",
        f"- direction_layer_clean: `{audit.summary['direction_layer_clean']}`",
        f"- state_layer_clean: `{audit.summary['state_layer_clean']}`",
        "",
        "## Role Groups",
        "",
    ]
    for role, names in audit.role_groups.items():
        lines.append(f"- {role}: `{names}`")
    lines.extend(["", "## Signals", ""])
    for entry in audit.signals:
        lines.extend(
            [
                f"### {entry.name}",
                "",
                f"- role: `{entry.role}`",
                f"- baseline_weight: `{entry.baseline_weight}`",
                f"- rank_ic_mean: `{entry.rank_ic_mean}`",
                f"- ic_stability: `{entry.ic_stability}`",
                f"- avg_abs_correlation: `{entry.avg_abs_correlation}`",
                f"- final_research_weight: `{entry.normalized_final_research_weight}`",
                f"- recommended_weight_adjustment: `{entry.recommended_weight_adjustment}`",
                f"- allowed_in_direction_search: `{entry.allowed_in_direction_search}`",
                f"- allowed_in_state_layer: `{entry.allowed_in_state_layer}`",
                f"- allowed_in_execution_layer: `{entry.allowed_in_execution_layer}`",
                f"- search_bound_policy: `{entry.search_bound_policy}`",
                f"- redundant_with: `{entry.redundant_with}`",
                "",
            ]
        )
    return "\n".join(lines)


def build_signal_candidate_inventory(
    *,
    symbol: str,
    profile_path: Path,
    plugin_root: Path,
    macro_root: Path = Path("data/raw/macro"),
    data_root: Path = Path("data/raw"),
) -> SignalCandidateInventory:
    _, registry = build_from_profile(profile_path, plugin_root=plugin_root)
    enabled_in_default = set(registry.signal_weights)
    candidates: list[SignalCandidateEntry] = []
    for path in sorted((plugin_root / "signals").glob("*.py")):
        if path.name == "__init__.py":
            continue
        signal_name = path.stem
        role = infer_signal_role(signal_name)
        has_dependency, dependency_status, blocked_by, dependency_detail = _signal_data_dependency(
            signal_name,
            macro_root=macro_root,
            data_root=data_root,
        )
        enabled = signal_name in enabled_in_default
        audit_status = "active_audited" if enabled else "research_candidate"
        recommendation = "active_stack" if enabled else ("observation_only" if blocked_by else "research_candidate")
        candidates.append(
            SignalCandidateEntry(
                name=signal_name,
                role=role,
                enabled_in_default=enabled,
                has_data_dependency=has_dependency,
                dependency_status=dependency_status,
                audit_status=audit_status,
                blocked_by=blocked_by,
                dependency_detail=dependency_detail,
                recommendation=recommendation,
            )
        )
    summary = {
        "candidate_count": len(candidates),
        "enabled_in_default_count": sum(1 for item in candidates if item.enabled_in_default),
        "blocked_candidate_count": sum(1 for item in candidates if item.blocked_by is not None),
        "research_candidate_count": sum(1 for item in candidates if item.recommendation == "research_candidate"),
        "observation_only_count": sum(1 for item in candidates if item.recommendation == "observation_only"),
    }
    return SignalCandidateInventory(
        generated_at=datetime.now(UTC).isoformat(),
        symbol=symbol,
        profile_path=str(profile_path),
        candidates=candidates,
        summary=summary,
    )


def analyze_signal_independence(
    *,
    data_root: Path,
    symbol: str,
    profile_path: Path = Path("config/profiles/default.toml"),
    plugin_root: Path = Path("zenquant/plugins"),
) -> SignalCombinationAudit:
    _, registry = build_from_profile(profile_path, plugin_root=plugin_root)
    config = HistoricalBacktestConfig(
        root_dir=data_root,
        symbol=symbol,
        portfolio_symbol=f"{symbol}_PERP",
        profile_path=profile_path,
    )
    bt = HistoricalBacktest(config)
    bt.run()

    packs = [pack for pack in bt._indicators if pack.timeframe == Timeframe.TF_4H]
    if len(packs) < 100:
        raise ValueError(f"Need at least 100 4H packs for signal audit, got {len(packs)}")

    state_engine = StateEngine(_NullBus())
    signal_series: dict[str, list[float]] = {plugin.meta.name: [] for plugin in registry.signals}
    closes: list[float] = []
    for pack in packs:
        state = state_engine.classify(pack)
        indicators = dict(pack.values)
        state_dict = _state_dict(state)
        for plugin in registry.signals:
            result = plugin.compute(indicators, state_dict, plugin.config)
            signal_series[plugin.meta.name].append(float(result.value) * float(result.confidence))
        close = float(indicators.get("close", indicators.get("last_price", 0.0)) or 0.0)
        closes.append(close)

    forward_returns: list[float] = []
    for idx in range(len(closes) - 1):
        current = closes[idx]
        nxt = closes[idx + 1]
        forward_returns.append((nxt - current) / current if current > 0 else 0.0)

    aligned_signal_series = {name: values[:-1] for name, values in signal_series.items()}
    return build_signal_combination_audit(
        symbol=symbol,
        profile_path=profile_path,
        signal_series=aligned_signal_series,
        baseline_weights=registry.signal_weights,
        forward_returns=forward_returns,
    )


def generate_signal_combination_audit(
    *,
    data_root: Path,
    symbol: str,
    output_dir: Path = Path("results/signal_combination"),
    profile_path: Path = Path("config/profiles/default.toml"),
    plugin_root: Path = Path("zenquant/plugins"),
) -> dict[str, Any]:
    audit = analyze_signal_independence(
        data_root=data_root,
        symbol=symbol,
        profile_path=profile_path,
        plugin_root=plugin_root,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "signal_independence.json"
    md_path = output_dir / "signal_independence.md"
    active_json_path = output_dir / "active_signal_independence.json"
    active_md_path = output_dir / "active_signal_independence.md"
    json_path.write_text(json.dumps(asdict(audit), indent=2), encoding="utf-8")
    md_path.write_text(build_markdown_audit(audit), encoding="utf-8")
    active_json_path.write_text(json.dumps(asdict(audit), indent=2), encoding="utf-8")
    active_md_path.write_text(build_markdown_audit(audit), encoding="utf-8")
    inventory = build_signal_candidate_inventory(
        symbol=symbol,
        profile_path=profile_path,
        plugin_root=plugin_root,
    )
    candidate_json_path = output_dir / "signal_candidate_inventory.json"
    candidate_md_path = output_dir / "signal_candidate_inventory.md"
    candidate_json_path.write_text(json.dumps(asdict(inventory), indent=2), encoding="utf-8")
    candidate_md_path.write_text(
        "\n".join(
            [
                "# Signal Candidate Inventory",
                "",
                f"- generated_at: `{inventory.generated_at}`",
                f"- candidate_count: `{inventory.summary['candidate_count']}`",
                f"- enabled_in_default_count: `{inventory.summary['enabled_in_default_count']}`",
                f"- blocked_candidate_count: `{inventory.summary['blocked_candidate_count']}`",
                "",
                "## Candidates",
                "",
                *[
                    f"- {item.name}: role=`{item.role}` enabled_in_default=`{item.enabled_in_default}` "
                    f"audit_status=`{item.audit_status}` blocked_by=`{item.blocked_by}` "
                    f"recommendation=`{item.recommendation}` dependency=`{item.dependency_detail}`"
                    for item in inventory.candidates
                ],
            ]
        ),
        encoding="utf-8",
    )
    return {
        **audit.summary,
        "generated_at": audit.generated_at,
        "json_path": str(json_path),
        "md_path": str(md_path),
        "active_stack_json_path": str(active_json_path),
        "active_stack_md_path": str(active_md_path),
        "candidate_inventory_json_path": str(candidate_json_path),
        "candidate_inventory_md_path": str(candidate_md_path),
    }
