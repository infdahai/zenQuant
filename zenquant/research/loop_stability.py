from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _ts() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _stale_running(loop_status: dict[str, Any] | None, campaign_status: dict[str, Any] | None) -> bool:
    if not loop_status or not campaign_status:
        return False
    return (
        loop_status.get("loop_state") in {"campaign_running", "failed"} and campaign_status.get("status") == "running"
    )


def _failure_cluster(loop_status: dict[str, Any] | None, campaign_status: dict[str, Any] | None) -> str:
    stop_reason = str((loop_status or {}).get("stop_reason") or "")
    phase = str((campaign_status or {}).get("phase") or "")
    if "bootstrap" in stop_reason or campaign_status is None:
        return "bootstrap_durability"
    if phase == "stage1_rl" or str((campaign_status or {}).get("rl_failure_driver") or ""):
        return "stage1_rl_startup_fragility"
    if phase == "stage2_full_verdict":
        return "stage2_durability"
    return "unknown_or_completed"


def _terminal_classification(
    loop_status: dict[str, Any] | None, campaign_status: dict[str, Any] | None, report_present: bool
) -> str:
    if report_present and (campaign_status or {}).get("status") == "completed":
        return "completed"
    if _stale_running(loop_status, campaign_status):
        return "stale_running_state"
    if campaign_status is None:
        return "bootstrap_failed_before_status"
    if (campaign_status or {}).get("status") == "failed":
        return "failed_terminal_state"
    if not report_present and (loop_status or {}).get("loop_state") == "failed":
        return "failed_missing_final_report"
    if (loop_status or {}).get("loop_state") == "stopped":
        return "stopped"
    return "incomplete_or_unknown"


def build_loop_stability_review(results_root: Path) -> dict[str, Any]:
    loops: list[dict[str, Any]] = []
    for loop_dir in sorted(results_root.glob("framework_optimization_loop_v*")):
        if not loop_dir.is_dir():
            continue
        loop_status = _read_json(loop_dir / "loop_status.json")
        current_run_dir = Path(str((loop_status or {}).get("current_run_dir") or "")) if loop_status else None
        if current_run_dir is None or not str(current_run_dir):
            campaign_status = None
            report_present = False
        else:
            campaign_status = _read_json(current_run_dir / "campaign_status.json")
            report_present = (current_run_dir / "framework_optimization.json").exists()
        loops.append(
            {
                "loop_id": loop_dir.name,
                "last_completed_round": max(
                    [
                        int(path.parent.name.split("_")[-1])
                        for path in loop_dir.glob("runs/round_*/framework_optimization.json")
                    ],
                    default=0,
                ),
                "terminal_classification": _terminal_classification(loop_status, campaign_status, report_present),
                "failure_cluster": _failure_cluster(loop_status, campaign_status),
                "stale_state_detected": _stale_running(loop_status, campaign_status),
                "bootstrap_status_present": campaign_status is not None,
                "stage1_rl_progress_seen": bool(
                    campaign_status and (campaign_status.get("rl_episodes_completed") or 0) > 0
                ),
                "stage2_terminal_state_written": bool(
                    campaign_status
                    and (
                        campaign_status.get("status") in {"completed", "failed"}
                        or campaign_status.get("phase") == "stage3_complete"
                    )
                ),
            }
        )
    payload = {
        "generated_at": _ts(),
        "results_root": str(results_root),
        "loop_count": len(loops),
        "loops": loops,
    }
    return payload


def write_loop_stability_review(payload: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "framework_optimization_loop_stability_review.json"
    md_path = output_dir / "framework_optimization_loop_stability_review.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Framework Optimization Loop Stability Review",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- loop_count: `{payload['loop_count']}`",
        "",
        "| loop_id | last_completed_round | terminal_classification | failure_cluster | stale_state_detected | bootstrap_status_present | stage1_rl_progress_seen | stage2_terminal_state_written |",
        "|---|---:|---|---|---|---|---|---|",
    ]
    for item in payload["loops"]:
        lines.append(
            f"| `{item['loop_id']}` | {item['last_completed_round']} | `{item['terminal_classification']}` | "
            f"`{item['failure_cluster']}` | `{item['stale_state_detected']}` | `{item['bootstrap_status_present']}` | "
            f"`{item['stage1_rl_progress_seen']}` | `{item['stage2_terminal_state_written']}` |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path)}
