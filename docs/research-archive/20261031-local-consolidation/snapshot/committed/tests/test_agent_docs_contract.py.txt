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
    normalized = " ".join(content.split())
    assert "August huge-profit claim is invalid" in content
    assert "sha256:82af23a08c68a94641e1d7769ecdf9619059dddbeb531dd3703cc4d2ff809f4a" in content
    assert "OUT_OF_SCOPE_NOT_EQUITY_INTEGRATED" in content
    assert "No promotion or parameter search is authorized" in content
    assert "runs completed; comparative/causal stage validity is **UNVERIFIED**" in normalized
    for contradictory_claim in (
        "The experiment is now complete",
        "The completed experiment",
        "These stages are causal attribution experiments",
        "the completed Quant V2 acceptance",
    ):
        assert contradictory_claim not in normalized


def test_authoritative_docs_preserve_product_gate_blockers() -> None:
    architecture = Path("docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    handoff = Path("docs/QUANT_V2_ACCEPTANCE_HANDOFF.md").read_text(encoding="utf-8")
    for content in (architecture, handoff):
        normalized = " ".join(content.split())
        assert "DCA-off Sharpe sensitivity" in normalized
        assert "45%" in normalized
        assert "40%" in normalized
        assert "26.8%" in normalized
        assert "25%" in normalized
        assert "-4.12" in normalized
        assert "full data-present `zenquant-check` is **NOT green**" in normalized
        assert "preserved product blockers, not implementation defects" in normalized
        assert "Do not weaken these thresholds" in normalized
        assert "No promotion or parameter search" in normalized


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


def test_operator_docs_publish_identity_complete_rerun_verdict() -> None:
    for path in (Path("CLAUDE.md"), Path("docs/LIVE_VALIDATION_HANDOFF_SPEC.md")):
        normalized = " ".join(path.read_text(encoding="utf-8").split())
        assert "pre-contract diagnostic only" in normalized
        assert "identity_complete_30_run_rerun_completed" in normalized
        assert "results/accounting_integrity_20260823_v2/acceptance_summary.json" in normalized
        assert "data/frozen_snapshots/accounting_integrity_20260823_v2" in normalized
        assert "3259f41a54a4ca688fd17cc59ad5c784564c49032f1c2a34721ea0ad237916d9" in normalized
        assert "+379.21" in normalized
        assert "-1,818.72" in normalized
        assert "0/5" in normalized
        assert "BLOCKED_PAUSE_PROMOTION_AND_TRAINING" in normalized
        assert "promotion or training" in normalized


def test_authoritative_docs_mark_old_rerun_identity_insufficient() -> None:
    architecture = Path("docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    handoff = Path("docs/QUANT_V2_ACCEPTANCE_HANDOFF.md").read_text(encoding="utf-8")
    for content in (architecture, handoff):
        normalized = " ".join(content.split())
        assert "sha256:a2e7a4039556aa427ec258a2f38d302c6edf5e37e8e81b9cedd4a36897b66c1a" in content
        assert "sha256:17fe13a2b55cfd8a34fb0e9b2c0c616e7af4c38a25e9eed705eb4b9cc0d6be04" in content
        assert "identity_complete_30_run_rerun_completed" in content
        assert "old pre-contract" in normalized
        assert "diagnostic only" in normalized
