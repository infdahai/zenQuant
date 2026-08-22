from pathlib import Path


def test_agents_references_claude_as_supplemental_context() -> None:
    content = Path("AGENTS.md").read_text(encoding="utf-8")
    assert "Additional Agent Context" in content
    assert "`CLAUDE.md`" in content
    assert "If `CLAUDE.md` conflicts with `AGENTS.md`, `AGENTS.md` wins." in content


def test_claude_points_to_authoritative_runtime_artifacts() -> None:
    content = Path("CLAUDE.md").read_text(encoding="utf-8")
    assert "docs/LIVE_VALIDATION_HANDOFF_SPEC.md" in content
    assert "results/data_readiness/data_readiness.json" in content
    assert "results/framework_optimization/campaign_status.json" in content
    assert "results/framework_optimization/framework_optimization.json" in content


def test_claude_keeps_product_philosophy_in_agents() -> None:
    content = Path("CLAUDE.md").read_text(encoding="utf-8")
    assert (
        "If `AGENTS.md` conflicts with `CLAUDE.md`, `AGENTS.md` wins." in content
        or "如果与 `AGENTS.md` 冲突，以 `AGENTS.md` 为准。" in content
    )
    assert "Role" not in content


def test_handoff_spec_mentions_hyperliquid_as_supplemental() -> None:
    content = Path("docs/LIVE_VALIDATION_HANDOFF_SPEC.md").read_text(encoding="utf-8")
    assert "Hyperliquid public proxy = Tier 2 supplemental" in content
    assert "Binance liquidation = Tier 1 primary truth" in content
    assert "hyperliquid_admission_review.json" in content


def test_architecture_records_repaired_accounting_boundary() -> None:
    content = Path("docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "August huge-profit claim is invalid" in content
    assert "sha256:82af23a08c68a94641e1d7769ecdf9619059dddbeb531dd3703cc4d2ff809f4a" in content
    assert "OUT_OF_SCOPE_NOT_EQUITY_INTEGRATED" in content
    assert "No promotion or parameter search is authorized" in content


def test_quant_v2_handoff_records_current_blocked_evidence() -> None:
    content = Path("docs/QUANT_V2_ACCEPTANCE_HANDOFF.md").read_text(encoding="utf-8")
    assert "30/30" in content
    assert "210/210" in content
    assert "UNVERIFIED_LEGACY" in content
    assert "legacy_and_repaired_stage_provenance_incomplete" in content
    assert "-3,068.23" in content
    assert "-624.15" in content
    assert "Robustness is `0/5`" in content
    assert "BLOCKED_PAUSE_PROMOTION_AND_TRAINING" in content
