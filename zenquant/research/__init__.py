"""Deterministic research pipeline helpers built on the main replay kernel."""

from .auto_research import Campaign, CampaignConfig, CampaignReport, SearchSpace, WalkForwardSplit
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

__all__ = [
    "BaselineMetrics",
    "Campaign",
    "CampaignConfig",
    "CampaignReport",
    "CandidateMetrics",
    "Experiment",
    "ExperimentTracker",
    "PromotionCriteria",
    "PromotionGate",
    "PromotionVerdict",
    "ResearchRunArtifact",
    "ResearchRunConfig",
    "RobustnessResult",
    "SearchSpace",
    "WalkForwardSplit",
    "run_research_pipeline",
]
