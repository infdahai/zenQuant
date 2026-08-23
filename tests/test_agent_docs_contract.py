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


def test_authoritative_docs_mark_old_rerun_identity_insufficient() -> None:
    architecture = Path("docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    handoff = Path("docs/QUANT_V2_ACCEPTANCE_HANDOFF.md").read_text(encoding="utf-8")
    for content in (architecture, handoff):
        normalized = " ".join(content.split())
        assert "sha256:44fbafe8d88db40a618f2742f7a5cdd602310bf5869e1bac33c587d281c1b160" in content
        assert "sha256:182398d05f7289e25b467cb8957603bdbde5360f39fdf62d8e366a9a5c415fed" in content
        assert "requires_reinstrumented_30_run_rerun" in content
        assert "economic values are diagnostic only" in normalized
