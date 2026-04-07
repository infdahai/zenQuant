import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from zenquant.app.paper_live_scorecard import (
    PaperLiveScorecardConfig,
    build_scorecard_from_validation,
    collect_scorecard,
    load_scorecard_history_payloads,
    persist_review,
    persist_scorecard,
)


def _validation_summary(*, ok: bool = True, checked_at: datetime | None = None) -> dict:
    when = checked_at or datetime(2026, 4, 8, 0, 0, tzinfo=UTC)
    return {
        "ok": ok,
        "failures": [] if ok else ["database_not_connected"],
        "watch_seconds": 0.0,
        "poll_interval_s": 30.0,
        "symbol": "BTCUSDT_PERP",
        "latest": {
            "ts_ns": int(when.timestamp() * 1_000_000_000),
            "health_status": "ok" if ok else "degraded",
            "mode": "live",
            "bootstrap_phase": "ready",
            "market_connected": True,
            "last_market_event_ns": int(when.timestamp() * 1_000_000_000),
            "database_enabled": True,
            "database_connected": ok,
            "database_writes": 12 if ok else 0,
            "database_dropped": 0,
            "indicator_4h": True,
            "state_4h": True,
            "playbook_4h": True,
            "sizing_4h": True,
            "target_4h": True,
            "indicator_1d": True,
            "state_1d": True,
            "bias_1d": True,
            "bar_close_count_4h": 8,
            "bar_close_count_1d": 2,
        },
        "market_age_s": 1.0,
        "bar_close_counts": {"4h": 8, "1d": 2},
        "observation_count": 1,
    }


class PaperLiveScorecardTests(unittest.TestCase):
    def test_build_scorecard_marks_complete_only_after_review_export_and_streak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_root = root / "live_training_runs"
            export_root.mkdir(parents=True)
            (export_root / "index.json").write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": "20260408T000000Z_btcusdt_perp_weekly-review",
                                "created_at": "2026-04-08T00:00:00+00:00",
                                "label": "weekly-review",
                                "symbol": "BTCUSDT_PERP",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            start = datetime(2026, 3, 25, 0, 0, tzinfo=UTC)
            for day in range(14):
                checked_at = start + timedelta(days=day)
                scorecard = build_scorecard_from_validation(
                    _validation_summary(ok=True, checked_at=checked_at),
                    output_root=root / "paper_live_scorecards",
                    now=checked_at,
                )
                persist_scorecard(scorecard, output_root=root / "paper_live_scorecards")

            persist_review(
                output_root=root / "paper_live_scorecards",
                verdict="PASS",
                summary="Weekly parity / execution / risk review passed",
                artifact_path="results/live_training_runs/20260408T000000Z_btcusdt_perp_weekly-review/report.md",
            )

            final_time = start + timedelta(days=14, hours=1)
            with patch("zenquant.app.paper_live_scorecard.LIVE_EXPORT_INDEX_PATH", export_root / "index.json"):
                final = build_scorecard_from_validation(
                    _validation_summary(ok=True, checked_at=final_time),
                    output_root=root / "paper_live_scorecards",
                    now=final_time,
                )

            self.assertTrue(final["paper_trading_complete"])
            self.assertEqual(final["status_label"], "paper trading complete")
            self.assertIsNotNone(final["latest_export"])
            self.assertIsNotNone(final["latest_review"])

    def test_collect_scorecard_persists_latest_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "paper_live_scorecards"
            with patch(
                "zenquant.app.paper_live_scorecard.run_validation",
                return_value=_validation_summary(ok=False),
            ):
                scorecard = collect_scorecard(
                    PaperLiveScorecardConfig(
                        output_root=root,
                        review_summary="runtime degraded",
                    )
                )

            self.assertFalse(scorecard["healthy"])
            self.assertTrue((root / "latest.json").exists())
            history = load_scorecard_history_payloads(root)
            self.assertEqual(len(history), 1)


if __name__ == "__main__":
    unittest.main()
