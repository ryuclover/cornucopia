from decimal import Decimal
from typing import Sequence
from src.domain.trade import ClosedTrade


def compute_drawdown_series(
    trades: Sequence[ClosedTrade],
    initial_capital: Decimal
) -> dict:
    """
    Calcula a curva de patrimônio líquido (equity curve), High Water Mark (HWM)
    e o Drawdown Máximo absoluto e percentual.
    """
    if initial_capital <= 0:
        raise ValueError("Capital inicial deve ser estritamente positivo.")

    if not trades:
        return {
            "equity_curve": [initial_capital],
            "high_water_mark": [initial_capital],
            "drawdown_amounts": [Decimal("0.0")],
            "drawdown_pcts": [0.0],
            "max_drawdown_amount": Decimal("0.0"),
            "max_drawdown_pct": 0.0,
        }

    current_equity = initial_capital
    current_hwm = initial_capital

    equity_curve: list[Decimal] = [current_equity]
    hwm_series: list[Decimal] = [current_hwm]
    dd_amounts: list[Decimal] = [Decimal("0.0")]
    dd_pcts: list[float] = [0.0]

    max_dd_amount = Decimal("0.0")
    max_dd_pct = 0.0

    for trade in trades:
        current_equity += trade.net_pnl
        if current_equity > current_hwm:
            current_hwm = current_equity

        dd_amount = current_hwm - current_equity
        # Drawdown percentual em relação ao topo histórico (High Water Mark)
        dd_pct = float(dd_amount / current_hwm * Decimal("100.0")) if current_hwm > 0 else 100.0

        equity_curve.append(current_equity)
        hwm_series.append(current_hwm)
        dd_amounts.append(dd_amount)
        dd_pcts.append(dd_pct)

        if dd_amount > max_dd_amount:
            max_dd_amount = dd_amount
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    return {
        "equity_curve": equity_curve,
        "high_water_mark": hwm_series,
        "drawdown_amounts": dd_amounts,
        "drawdown_pcts": dd_pcts,
        "max_drawdown_amount": max_dd_amount,
        "max_drawdown_pct": max_dd_pct,
    }
