"""Deterministic research pipeline helpers built on the main replay kernel."""

from .auto_research import (
    Campaign,
    CampaignConfig,
    CampaignReport,
    SearchSpace,
    WalkForwardSplit,
    run_dev_robustness_stage,
    run_dev_validation_stage,
    run_search_stage,
)
from .comparison import (
    VariantSpec,
    WindowSpec,
    build_markdown_summary,
    compare_variants,
    default_variant_specs,
    fixed_2023_08_window,
    liquidation_research_variant_specs,
    paulwei_execution_baseline,
    paulwei_risk_baseline,
    windows_from_worst_periods,
)
from .experiment import Experiment, ExperimentTracker
from .pipeline import ResearchRunArtifact, ResearchRunConfig, run_research_pipeline
from .promoter import (
    BaselineMetrics,
    CandidateMetrics,
    PromotionCriteria,
    PromotionGate,
    PromotionVerdict,
    RobustnessResult,
)
from .rl_tuner import ParamTunerEnv, train_tuner
from .splits import DatasetSplitSpec, SplitWindow, default_btc_split_spec, split_summary_json

__all__ = [
    "BaselineMetrics",
    "Campaign",
    "CampaignConfig",
    "CampaignReport",
    "CandidateMetrics",
    "DatasetSplitSpec",
    "Experiment",
    "ExperimentTracker",
    "ParamTunerEnv",
    "PromotionCriteria",
    "PromotionGate",
    "PromotionVerdict",
    "ResearchRunArtifact",
    "ResearchRunConfig",
    "RobustnessResult",
    "SearchSpace",
    "SplitWindow",
    "VariantSpec",
    "WalkForwardSplit",
    "WindowSpec",
    "build_markdown_summary",
    "compare_variants",
    "default_btc_split_spec",
    "default_variant_specs",
    "fixed_2023_08_window",
    "liquidation_research_variant_specs",
    "paulwei_execution_baseline",
    "paulwei_risk_baseline",
    "run_dev_robustness_stage",
    "run_dev_validation_stage",
    "run_research_pipeline",
    "run_search_stage",
    "split_summary_json",
    "train_tuner",
    "windows_from_worst_periods",
]
