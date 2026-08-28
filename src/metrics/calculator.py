from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence
from src.domain.trade import ClosedTrade
from src.metrics.drawdown import compute_drawdown_series
from src.metrics.pnl import compute_pnl_summary
from src.metrics.ratios import compute_risk_adjusted_ratios
from src.scoring.models import TraderPerformance


class PerformanceCalculator:
    """
    Calculadora unificada de performance ponto-no-tempo.
    
    Garante ausência estrita de Look-Ahead Bias filtrando trades por `exit_time <= as_of`.
    """
    @staticmethod
    def calculate(
        trader_id: str,
        trades: Sequence[ClosedTrade],
        as_of: datetime,
        initial_capital: Decimal,
        first_history_date: datetime | None = None,
        risk_free_rate_annual: float = 0.10,
    ) -> TraderPerformance:
        """
        Calcula o snapshot de performance do trader até a data `as_of`.
        """
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        else:
            as_of = as_of.astimezone(timezone.utc)

        # Filtro estrito Ponto no Tempo (Point-in-Time filter)
        eligible_trades = [t for t in trades if t.exit_time <= as_of]
        # Ordenação cronológica garantida
        eligible_trades.sort(key=lambda t: t.exit_time)

        # Cálculo do tempo de histórico em dias
        if eligible_trades:
            earliest_trade_time = min(t.entry_time for t in eligible_trades)
            start_date = min(earliest_trade_time, first_history_date) if first_history_date else earliest_trade_time
            history_days = max((as_of - start_date).total_seconds() / 86400.0, 1.0)
        else:
            if first_history_date:
                history_days = max((as_of - first_history_date).total_seconds() / 86400.0, 0.0)
            else:
                history_days = 0.0

        pnl_stats = compute_pnl_summary(eligible_trades)
        dd_stats = compute_drawdown_series(eligible_trades, initial_capital)
        ratios = compute_risk_adjusted_ratios(
            trades=eligible_trades,
            initial_capital=initial_capital,
            history_days=history_days,
            risk_free_rate_annual=risk_free_rate_annual,
            max_drawdown_pct=dd_stats["max_drawdown_pct"]
        )

        net_pnl = pnl_stats["net_pnl"]
        total_return_pct = float(net_pnl / initial_capital * Decimal("100.0")) if initial_capital > 0 else 0.0
        largest_loss_pct = float(pnl_stats["largest_loss"] / initial_capital * Decimal("100.0")) if initial_capital > 0 else 0.0

        return TraderPerformance(
            trader_id=trader_id,
            as_of=as_of,
            initial_capital=initial_capital,
            total_trades=pnl_stats["total_trades"],
            winning_trades=pnl_stats["winning_trades"],
            losing_trades=pnl_stats["losing_trades"],
            scratch_trades=pnl_stats["scratch_trades"],
            history_days=history_days,
            gross_pnl=pnl_stats["gross_pnl"],
            total_commission=pnl_stats["total_commission"],
            net_pnl=net_pnl,
            total_return_pct=total_return_pct,
            win_rate=pnl_stats["win_rate"],
            avg_win=pnl_stats["avg_win"],
            avg_loss=pnl_stats["avg_loss"],
            payoff_ratio=pnl_stats["payoff_ratio"],
            profit_factor=pnl_stats["profit_factor"],
            largest_win=pnl_stats["largest_win"],
            largest_loss=pnl_stats["largest_loss"],
            largest_loss_pct=largest_loss_pct,
            top_1_trade_pnl_contribution_pct=pnl_stats["top_1_trade_pnl_contribution_pct"],
            top_n_trades_pnl_contribution_pct=pnl_stats["top_n_trades_pnl_contribution_pct"],
            top_5_trades_pnl_contribution_pct=pnl_stats["top_5_trades_pnl_contribution_pct"],
            top_10_percent_trades_pnl_contribution_pct=pnl_stats["top_10_percent_trades_pnl_contribution_pct"],
            max_consecutive_losses=pnl_stats["max_consecutive_losses"],
            max_consecutive_wins=pnl_stats["max_consecutive_wins"],
            max_drawdown_amount=dd_stats["max_drawdown_amount"],
            max_drawdown_pct=dd_stats["max_drawdown_pct"],
            return_volatility_pct=ratios["return_volatility_pct"],
            downside_volatility_pct=ratios["downside_volatility_pct"],
            sharpe_ratio=ratios["sharpe_ratio"],
            sortino_ratio=ratios["sortino_ratio"],
            calmar_ratio=ratios["calmar_ratio"],
        )
