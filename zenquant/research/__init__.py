"""Deterministic research pipeline helpers built on the main replay kernel."""

from .auto_research import Campaign, CampaignConfig, CampaignReport, SearchSpace, WalkForwardSplit
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

__all__ = [
    "BaselineMetrics",
    "Campaign",
    "CampaignConfig",
    "CampaignReport",
    "CandidateMetrics",
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
    "VariantSpec",
    "WalkForwardSplit",
    "WindowSpec",
    "build_markdown_summary",
    "compare_variants",
    "default_variant_specs",
    "fixed_2023_08_window",
    "liquidation_research_variant_specs",
    "paulwei_execution_baseline",
    "paulwei_risk_baseline",
    "run_research_pipeline",
    "train_tuner",
    "windows_from_worst_periods",
]
