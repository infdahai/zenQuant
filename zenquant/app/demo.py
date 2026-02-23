from __future__ import annotations

from zenquant.app.pipeline import ZenQuantPipeline


def main() -> None:
    pipeline = ZenQuantPipeline()
    result = pipeline.run(steps=320, start_price=40_000.0)

    print("ZenQuant 3.0 simulation complete")
    print(f"signals={result.total_signals}")
    print(f"execution_reports={result.total_reports}")
    print(f"filled_reports={result.filled_reports}")
    print(f"rejected_reports={result.rejected_reports}")
    print(f"factor_weights={result.total_factor_weights}")
    print(f"final_position={result.final_position:.6f}")
    print(f"kill_switch_active={result.kill_switch_active}")


if __name__ == "__main__":
    main()
