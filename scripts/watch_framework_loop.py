#!/usr/bin/env python3
"""Watch or print the current state of the framework research loop."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _tail_lines(path: Path, count: int = 20) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()[-count:]


def _current_round_path(loop_dir: Path) -> Path | None:
    pointer = loop_dir / "current_round"
    if not pointer.exists() and not pointer.is_symlink():
        return None
    if pointer.is_symlink():
        return pointer.resolve()
    try:
        return Path(pointer.read_text(encoding="utf-8").strip())
    except Exception:
        return pointer


def _snapshot(loop_dir: Path) -> dict[str, Any]:
    loop_status = _read_json(loop_dir / "loop_status.json") or {}
    round_dir = _current_round_path(loop_dir)
    campaign_status = _read_json(round_dir / "campaign_status.json") if round_dir is not None else None
    loop_lock = _read_json(loop_dir / "loop.lock")
    campaign_lock = _read_json(round_dir / "campaign.lock") if round_dir is not None else None
    return {
        "loop_status": loop_status,
        "loop_lock": loop_lock,
        "current_round_dir": str(round_dir) if round_dir is not None else None,
        "campaign_status": campaign_status,
        "campaign_lock": campaign_lock,
        "loop_log_tail": _tail_lines(loop_dir / "loop.log"),
        "campaign_log_tail": _tail_lines(round_dir / "campaign.log") if round_dir is not None else [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch framework research loop status")
    parser.add_argument("--output-dir", default="results/framework_optimization_loop")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()

    loop_dir = Path(args.output_dir)
    while True:
        print(json.dumps(_snapshot(loop_dir), indent=2))
        if not args.follow:
            return
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    main()
