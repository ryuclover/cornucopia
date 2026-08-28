"""
Módulo de Walk-Forward, Backtest Out-of-Sample e Validação do Sistema Coletivo (Etapa 8).
"""

from src.config.walkforward_config import (
    BacktestFrictionConfig,
    BaselineMode,
    EvaluationStatus,
    OutcomeClassification,
    RunPurpose,
    WalkForwardConfig,
)
from src.walkforward.baselines import BaselineEngine
from src.walkforward.decision import WalkForwardDecisionEngine
from src.walkforward.engine import WalkForwardEngine
from src.walkforward.episodes import ConsensusEpisodeTracker
from src.walkforward.metrics import WalkForwardMetricsCalculator
from src.walkforward.models import (
    BaselineComparisonResult,
    ConsensusEpisode,
    EfficacyMetricSet,
    ForwardReturnOutcome,
    HorizonEfficacySummary,
    ShadowEquityPoint,
    ShadowStrategyResult,
    WalkForwardDecision,
    WalkForwardDecisionJournal,
    WalkForwardRun,
)
from src.walkforward.outcomes import ForwardOutcomeEvaluator
from src.walkforward.simulator import ConsensusShadowStrategySimulator

__all__ = [
    "WalkForwardConfig",
    "BacktestFrictionConfig",
    "BaselineMode",
    "EvaluationStatus",
    "OutcomeClassification",
    "RunPurpose",
    "WalkForwardDecision",
    "WalkForwardDecisionJournal",
    "ForwardReturnOutcome",
    "ConsensusEpisode",
    "ShadowEquityPoint",
    "ShadowStrategyResult",
    "BaselineComparisonResult",
    "EfficacyMetricSet",
    "HorizonEfficacySummary",
    "WalkForwardRun",
    "WalkForwardDecisionEngine",
    "ForwardOutcomeEvaluator",
    "ConsensusEpisodeTracker",
    "ConsensusShadowStrategySimulator",
    "BaselineEngine",
    "WalkForwardMetricsCalculator",
    "WalkForwardEngine",
]
