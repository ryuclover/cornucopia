"""Módulo de cálculo de métricas de performance e risco."""

from src.metrics.calculator import PerformanceCalculator
from src.metrics.drawdown import compute_drawdown_series
from src.metrics.pnl import (
    calculate_top_n_pnl_contribution,
    calculate_top_pct_pnl_contribution,
    compute_pnl_summary,
)
from src.metrics.ratios import compute_risk_adjusted_ratios

__all__ = [
    "PerformanceCalculator",
    "compute_pnl_summary",
    "compute_drawdown_series",
    "compute_risk_adjusted_ratios",
    "calculate_top_n_pnl_contribution",
    "calculate_top_pct_pnl_contribution",
]
