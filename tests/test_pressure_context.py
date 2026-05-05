import pandas as pd

from zenquant.research.pressure_context import build_pressure_context_attribution


def test_pressure_context_explains_reduce_flip_without_strategy_wiring() -> None:
    payload = build_pressure_context_attribution(
        attribution_payload=_attribution_payload(),
        admission_payload=_admission_payload(),
        independence_payload=_independence_payload(),
        features=_features(),
        min_context_rows=4,
        min_high_pressure_event_count=2,
    )

    assert payload["test_evaluated"] is False
    assert payload["strategy_mutation_allowed"] is False
    assert payload["direction_vote_allowed"] is False
    assert payload["intent_aggregation_allowed"] is False
    assert payload["risk_overlay_wiring_allowed"] is False
    assert payload["replay_explains_reduce_flip_cadence"] is True
    assert payload["next_action"] == "review_pressure_context_for_explanation_only_do_not_wire"
    top = payload["aggregate"]["top_matches"][0]
    assert top["factor"] == "pressure_z"
    assert top["target_reduce_flip_high_lift"] > 1.0


def test_pressure_context_blocks_when_feature_overlap_is_missing() -> None:
    payload = build_pressure_context_attribution(
        attribution_payload=_attribution_payload(),
        admission_payload=_admission_payload(),
        independence_payload=_independence_payload(),
        features=pd.DataFrame({"bar_close_ts": []}),
        min_context_rows=4,
    )

    assert payload["replay_explains_reduce_flip_cadence"] is False
    assert "pressure_feature_frame_missing" in payload["blocked_by"]
    assert payload["next_action"] == "repair_pressure_context_inputs_before_replay_review"


def _admission_payload() -> dict:
    return {
        "source_name": "deribit_large_option_flow",
        "research_ready": True,
        "use_for_research_only": True,
        "features_path": "features.parquet",
        "top_feature_candidates": [
            {
                "name": "pressure_z",
                "target": "forward_abs_return_4h",
                "signal_sign": 1.0,
                "rolling_rank_ic_mean": 0.2,
                "ablation_delta": 0.1,
            }
        ],
    }


def _independence_payload() -> dict:
    return {
        "blocked_by": None,
        "allowed_in_pressure_context": True,
        "allowed_in_state_features": False,
    }


def _features() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "bar_close_ts": pd.date_range("2023-08-01", periods=8, freq="4h", tz="UTC"),
            "pressure_z": [-1.0, -0.8, -0.5, 0.0, 0.2, 1.5, 1.7, 2.0],
        }
    )


def _attribution_payload() -> dict:
    rows = []
    actions = ["hold", "hold", "hold", "add", "hold", "reduce", "flip", "reduce"]
    for idx, action in enumerate(actions):
        ts = pd.Timestamp("2023-08-01T00:00:00+00:00") + pd.Timedelta(hours=4 * idx)
        rows.append(
            {
                "cutoff_time_ns": int(ts.value),
                "target_action": action,
                "execution_actions": [action] if action in {"reduce", "flip"} else [],
            }
        )
    return {
        "windows": [
            {
                "window": "2023-08",
                "variants": [
                    {
                        "variant": "candidate",
                        "metrics": {"sharpe": 1.0, "net_pnl": 10.0},
                        "decision_attribution_ledger": {"decision_rows": rows},
                    }
                ],
            }
        ]
    }
