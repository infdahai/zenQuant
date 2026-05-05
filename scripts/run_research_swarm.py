#!/usr/bin/env python3
"""Run the repo-native research agent swarm."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from zenquant.research.swarm import ProfitPriority, SourceScope, SwarmLoopMode, SwarmManager, SwarmMode, SwarmRunConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ZenQuant research-layer agent swarm")
    parser.add_argument("--goal", required=True, help="Research goal or question")
    parser.add_argument("--output-dir", required=True, type=Path, help="Swarm artifact directory")
    parser.add_argument(
        "--mode", choices=(SwarmMode.DRY_RUN.value, SwarmMode.EXECUTE.value), default=SwarmMode.DRY_RUN.value
    )
    parser.add_argument("--root", default="data/raw/binance", type=Path, help="Historical data root")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading symbol")
    parser.add_argument("--profile-path", default="config/profiles/default.toml", type=Path)
    parser.add_argument("--budget", default="smoke", help="Framework campaign budget")
    parser.add_argument("--search-space-mode", default=None, help="Framework campaign search-space mode")
    parser.add_argument("--optuna-trials", type=int, default=None, help="Override framework campaign Optuna trials")
    parser.add_argument("--rl-episodes", type=int, default=None, help="Override framework campaign RL episodes")
    parser.add_argument(
        "--liquidation-dir",
        default=Path("data/raw/merged_liquidations/aggregated"),
        type=Path,
        help="Liquidation data directory passed to framework campaign",
    )
    parser.add_argument("--resume", action="store_true", help="Resume framework campaign inside the swarm output dir")
    parser.add_argument(
        "--loop-mode",
        choices=(SwarmLoopMode.CAMPAIGN.value, SwarmLoopMode.RALPH.value),
        default=SwarmLoopMode.CAMPAIGN.value,
        help="Research orchestration mode",
    )
    parser.add_argument("--max-ralph-rounds", type=int, default=3, help="Maximum Ralph loop rounds")
    parser.add_argument("--target-candidates", type=int, default=10, help="Target Ralph candidate count")
    parser.add_argument("--min-candidates", type=int, default=5, help="Minimum useful Ralph candidate count")
    parser.add_argument(
        "--source-scope",
        choices=(SourceScope.FREE.value,),
        default=SourceScope.FREE.value,
        help="Source discovery boundary",
    )
    parser.add_argument(
        "--profit-priority",
        choices=(ProfitPriority.GUARDED.value,),
        default=ProfitPriority.GUARDED.value,
        help="Ralph loop ranking mode",
    )
    parser.add_argument("--python-bin", default=sys.executable, help="Python executable used for child commands")
    parser.add_argument(
        "--allow-test-final-verdict",
        action="store_true",
        help="Allow explicit test-split final acceptance commands",
    )
    args = parser.parse_args()

    result = SwarmManager().run(
        SwarmRunConfig(
            goal=args.goal,
            output_dir=args.output_dir,
            mode=SwarmMode(args.mode),
            symbol=args.symbol,
            data_root=args.root,
            profile_path=args.profile_path,
            budget=args.budget,
            search_space_mode=args.search_space_mode,
            optuna_trials=args.optuna_trials,
            rl_episodes=args.rl_episodes,
            liquidation_dir=args.liquidation_dir,
            resume=args.resume,
            loop_mode=SwarmLoopMode(args.loop_mode),
            max_ralph_rounds=args.max_ralph_rounds,
            target_candidates=args.target_candidates,
            min_candidates=args.min_candidates,
            source_scope=SourceScope(args.source_scope),
            profit_priority=ProfitPriority(args.profit_priority),
            allow_test_final_verdict=args.allow_test_final_verdict,
            python_bin=args.python_bin,
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
