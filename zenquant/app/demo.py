from __future__ import annotations

from zenquant.app.pipeline import ZenQuantPipeline


def main() -> None:
    pipeline = ZenQuantPipeline(seed=11)
    result = pipeline.run(steps=420)

    print("ZenQuant 4.0 Phase 1 simulation complete")
    print(f"indicator_packs={result.total_indicator_packs}")
    print(f"target_positions={result.total_targets}")
    print(f"execution_reports={result.total_reports}")
    print(f"filled_reports={result.filled_reports}")
    print(f"latest_zen_pulse={result.latest_zen_pulse:.2f}")
    print(f"latest_regime={result.latest_regime}")
    print(f"final_position_btc={result.final_position_btc:.6f}")


if __name__ == "__main__":
    main()
