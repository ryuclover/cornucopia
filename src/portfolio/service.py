from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from src.config.evaluation_config import EvaluationFrequency
from src.evaluation.engine import TraderEvaluationEngine
from src.portfolio.models import TraderVirtualPortfolio
from src.replay.engine import TraderReplayEngine


class TraderVirtualPortfolioService:
    """
    Serviço para consulta e geração de séries de portfólios virtuais individuais.
    """
    def __init__(self, replay_engine: TraderReplayEngine):
        self.replay_engine = replay_engine

    def _normalize_utc(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def get_portfolio(self, trader_id: str, as_of: datetime) -> TraderVirtualPortfolio:
        """
        Reconstrói o portfólio virtual de um trader em 'as_of'.
        """
        as_of = self._normalize_utc(as_of)
        replay_res = self.replay_engine.replay_trader(trader_id, as_of=as_of, compute_score=False)
        
        # Calcula o peak equity histórico a partir dos snapshots e trades realizados
        equity_values = [replay_res.initial_capital]
        current_eq = replay_res.initial_capital
        for t in replay_res.closed_trades:
            current_eq += t.net_pnl
            equity_values.append(current_eq)

        if replay_res.total_equity is not None:
            equity_values.append(replay_res.total_equity)

        peak_equity = max(equity_values)
        
        # Drawdown atual
        ref_equity = replay_res.total_equity if replay_res.total_equity is not None else replay_res.realized_equity
        if peak_equity > 0:
            dd_amount = peak_equity - ref_equity
            dd_pct = float(dd_amount / peak_equity) * 100.0 if dd_amount > 0 else 0.0
        else:
            dd_pct = 0.0

        return TraderVirtualPortfolio(
            trader_id=trader_id,
            as_of=as_of,
            initial_capital=replay_res.initial_capital,
            realized_equity=replay_res.realized_equity,
            mark_to_market_equity=replay_res.total_equity,
            realized_pnl=replay_res.total_realized_pnl,
            unrealized_pnl=replay_res.total_unrealized_pnl,
            positions=replay_res.positions,
            closed_trades=replay_res.closed_trades,
            drawdown_pct=round(dd_pct, 2),
            peak_equity=peak_equity,
            valuation_status=replay_res.valuation_status
        )

    def generate_equity_series(
        self,
        trader_id: str,
        start: datetime,
        end: datetime,
        frequency: EvaluationFrequency = EvaluationFrequency.DAILY
    ) -> list[TraderVirtualPortfolio]:
        """
        Gera uma série temporal de portfólios virtuais e curvas de patrimônio.
        """
        start = self._normalize_utc(start)
        end = self._normalize_utc(end)

        timestamps = TraderEvaluationEngine.generate_evaluation_timestamps(start, end, frequency)
        portfolios = []

        for ts in timestamps:
            port = self.get_portfolio(trader_id, as_of=ts)
            portfolios.append(port)

        return portfolios
