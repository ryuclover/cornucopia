import math
from decimal import Decimal
from typing import Sequence
from src.domain.trade import ClosedTrade


def compute_risk_adjusted_ratios(
    trades: Sequence[ClosedTrade],
    initial_capital: Decimal,
    history_days: float,
    risk_free_rate_annual: float = 0.10,
    max_drawdown_pct: float = 0.0,
) -> dict:
    """
    Calcula volatilidade dos retornos, downside deviation, Sharpe Ratio,
    Sortino Ratio e Calmar Ratio.
    """
    if not trades:
        return {
            "return_volatility_pct": 0.0,
            "downside_volatility_pct": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "calmar_ratio": 0.0,
        }

    returns_pct = [float(t.return_pct) * 100.0 for t in trades]
    n = len(returns_pct)

    mean_return = sum(returns_pct) / n

    if n > 1:
        variance = sum((r - mean_return) ** 2 for r in returns_pct) / (n - 1)
        stdev = math.sqrt(variance)
    else:
        stdev = 0.0

    # Downside deviation (apenas retornos abaixo de zero / MAR)
    downside_squared = [min(r, 0.0) ** 2 for r in returns_pct]
    if n > 1:
        downside_variance = sum(downside_squared) / (n - 1)
        downside_dev = math.sqrt(downside_variance)
    else:
        downside_dev = 0.0

    # Taxa livre de risco proporcional por trade
    # Estimando aprox 252 dias úteis / ano
    trades_per_year = (n / (history_days / 365.25)) if history_days > 0 else 252.0
    rf_per_trade = (risk_free_rate_annual * 100.0) / trades_per_year if trades_per_year > 0 else 0.0

    excess_return = mean_return - rf_per_trade

    sharpe_ratio = (excess_return / stdev) * math.sqrt(trades_per_year) if stdev > 0 else 0.0
    sortino_ratio = (excess_return / downside_dev) * math.sqrt(trades_per_year) if downside_dev > 0 else (
        (excess_return * math.sqrt(trades_per_year)) if excess_return > 0 else 0.0
    )

    # Calmar Ratio: Retorno Anualizado / Max Drawdown
    total_net_pnl = sum((t.net_pnl for t in trades), Decimal("0.0"))
    total_return_pct = float(total_net_pnl / initial_capital * Decimal("100.0")) if initial_capital > 0 else 0.0
    
    years = history_days / 365.25 if history_days > 0 else (1.0 / 365.25)
    annualized_return = (total_return_pct / years) if years > 0 else 0.0

    calmar_ratio = (annualized_return / max_drawdown_pct) if max_drawdown_pct > 0 else (
        annualized_return if annualized_return > 0 else 0.0
    )

    return {
        "return_volatility_pct": stdev,
        "downside_volatility_pct": downside_dev,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "calmar_ratio": calmar_ratio,
    }
