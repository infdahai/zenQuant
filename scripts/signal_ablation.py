#!/usr/bin/env python3
"""Signal ablation study — load-bearing component analysis.

Anthropic harness principle: "Every component encodes an assumption about
what the model can't do. Those assumptions are worth stress testing."

Disables each signal plugin one at a time and measures Sharpe impact.
Identifies dead-weight signals that can be removed or deprioritized.

Usage:
    uv run python scripts/signal_ablation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.historical import HistoricalBacktest, HistoricalBacktestConfig


def run_ablation() -> None:
    print("Signal Ablation Study — Load-Bearing Analysis")
    print("=" * 70)

    # Baseline run
    print("\n[1/N] Running baseline...")
    baseline = HistoricalBacktest(HistoricalBacktestConfig()).run()
    baseline_sharpe = baseline.metrics.sharpe
    baseline_pnl = baseline.metrics.net_pnl
    baseline_dd = baseline.metrics.max_drawdown
    baseline_trades = baseline.metrics.trade_count

    print(f"  Baseline: Sharpe={baseline_sharpe:.2f}, PnL=${baseline_pnl:,.0f}, DD={baseline_dd:.1%}")

    # Get signal plugin names from profile
    profile_path = Path("config/profiles/default.toml")
    if not profile_path.exists():
        print("ERROR: default.toml not found")
        return

    import tomllib

    with profile_path.open("rb") as f:
        profile = tomllib.load(f)

    signals = profile.get("signals", {})
    signal_names = [name for name, cfg in signals.items() if cfg.get("enabled", True)]

    print(f"\nFound {len(signal_names)} active signals: {signal_names}")
    print()

    # Ablation: disable each signal one at a time
    results: list[dict] = []
    for i, signal_name in enumerate(signal_names):
        print(f"[{i + 2}/{len(signal_names) + 1}] Ablating: {signal_name}...")

        # Create modified profile: copy original and disable target signal
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".toml", delete=False, mode="w") as tmp:
            # Read original and insert enabled=false for target signal
            original_text = profile_path.read_text()
            # Replace the signal's enabled state
            section_header = f"[signals.{signal_name}]"
            if section_header in original_text:
                lines = original_text.split("\n")
                in_target = False
                for j, line in enumerate(lines):
                    if line.strip() == section_header:
                        in_target = True
                        continue
                    if in_target and line.strip().startswith("enabled"):
                        lines[j] = "enabled = false"
                        in_target = False
                        break
                    if in_target and line.strip().startswith("["):
                        # No enabled key found, insert one
                        lines.insert(j, "enabled = false")
                        in_target = False
                        break
                tmp.write("\n".join(lines))
            else:
                tmp.write(original_text)
            tmp_path = Path(tmp.name)

        try:
            config = HistoricalBacktestConfig(profile_path=tmp_path)
            result = HistoricalBacktest(config).run()

            sharpe = result.metrics.sharpe
            pnl = result.metrics.net_pnl
            dd = result.metrics.max_drawdown
            trades = result.metrics.trade_count

            sharpe_delta = sharpe - baseline_sharpe
            pnl_delta = pnl - baseline_pnl

            verdict = "DEAD WEIGHT" if sharpe_delta >= 0 else "LOAD-BEARING"
            results.append({
                "signal": signal_name,
                "sharpe": sharpe,
                "sharpe_delta": sharpe_delta,
                "pnl": pnl,
                "pnl_delta": pnl_delta,
                "dd": dd,
                "trades": trades,
                "verdict": verdict,
            })

            print(f"  Sharpe={sharpe:.2f} (Δ={sharpe_delta:+.2f}), PnL=${pnl:,.0f} (Δ=${pnl_delta:+,.0f}) → {verdict}")

        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"signal": signal_name, "verdict": "ERROR", "sharpe_delta": 0})
        finally:
            tmp_path.unlink(missing_ok=True)

    # Summary
    print("\n" + "=" * 70)
    print(f"{'Signal':<30} {'Sharpe':>8} {'Δ Sharpe':>10} {'Δ PnL':>12} {'Verdict':>15}")
    print("-" * 70)
    print(f"{'BASELINE':<30} {baseline_sharpe:>8.2f} {'---':>10} {'---':>12} {'---':>15}")

    for r in sorted(results, key=lambda x: x.get("sharpe_delta", 0)):
        if r["verdict"] == "ERROR":
            print(f"{r['signal']:<30} {'ERROR':>8}")
            continue
        print(
            f"{r['signal']:<30} {r['sharpe']:>8.2f} {r['sharpe_delta']:>+10.2f} "
            f"{r['pnl_delta']:>+12,.0f} {r['verdict']:>15}"
        )

    # Recommendations
    dead_weight = [r for r in results if r["verdict"] == "DEAD WEIGHT"]
    load_bearing = [r for r in results if r["verdict"] == "LOAD-BEARING"]

    print(f"\n{'=' * 70}")
    print(f"LOAD-BEARING signals ({len(load_bearing)}): removing hurts Sharpe")
    for r in load_bearing:
        print(f"  ✓ {r['signal']} (Δ={r['sharpe_delta']:+.2f})")

    if dead_weight:
        print(f"\nDEAD WEIGHT signals ({len(dead_weight)}): removing helps or neutral")
        for r in dead_weight:
            print(f"  ✗ {r['signal']} (Δ={r['sharpe_delta']:+.2f})")
        print("\nAction: Consider disabling dead-weight signals to reduce complexity.")
    else:
        print("\nAll signals are load-bearing. No dead weight detected.")


if __name__ == "__main__":
    run_ablation()
