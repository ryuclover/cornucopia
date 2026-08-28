"""
Módulo de Análise de Dependência, Similaridade e Correlação entre Traders.
"""

from src.dependence.alignment import TimeSeriesAligner
from src.dependence.clustering import RedundancyClusterer
from src.dependence.config import DependenceConfig
from src.dependence.engine import TraderDependenceEngine
from src.dependence.metrics import (
    calculate_composite_redundancy_score,
    calculate_directional_agreement,
    calculate_instrument_overlap,
    calculate_position_overlap,
    calculate_return_correlation,
    calculate_timing_similarity,
    classify_dependence_level,
)
from src.dependence.models import (
    CoreDependenceSnapshot,
    DependenceLevel,
    DependenceMatrix,
    RedundancyGroup,
    TraderPairDependence,
    TraderTimeSeriesFrame,
)

__all__ = [
    "DependenceConfig",
    "DependenceLevel",
    "TraderTimeSeriesFrame",
    "TraderPairDependence",
    "DependenceMatrix",
    "RedundancyGroup",
    "CoreDependenceSnapshot",
    "TimeSeriesAligner",
    "RedundancyClusterer",
    "TraderDependenceEngine",
    "calculate_return_correlation",
    "calculate_directional_agreement",
    "calculate_position_overlap",
    "calculate_instrument_overlap",
    "calculate_timing_similarity",
    "calculate_composite_redundancy_score",
    "classify_dependence_level",
]
