"""
Módulo de Ponderação e Alocação de Pesos aos Traders Selecionados do Núcleo.
"""

from src.weighting.confidence import TraderConfidenceCalculator
from src.weighting.config import WeightConfig, WeightingPreset
from src.weighting.diagnostics import WeightDiagnosticsCalculator
from src.weighting.engine import TraderWeightEngine
from src.weighting.independence import TraderIndependenceCalculator
from src.weighting.models import (
    CoreWeightSnapshot,
    GroupWeightSummary,
    InfeasibleWeightConstraintsError,
    TraderWeight,
    WeightConcentrationMetrics,
    WeightTurnoverMetric,
)
from src.weighting.quality import TraderQualityCalculator

__all__ = [
    "WeightConfig",
    "WeightingPreset",
    "InfeasibleWeightConstraintsError",
    "TraderWeight",
    "GroupWeightSummary",
    "WeightConcentrationMetrics",
    "CoreWeightSnapshot",
    "WeightTurnoverMetric",
    "TraderQualityCalculator",
    "TraderIndependenceCalculator",
    "TraderConfidenceCalculator",
    "WeightDiagnosticsCalculator",
    "TraderWeightEngine",
]
