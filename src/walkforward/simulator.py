from datetime import datetime, timezone
from decimal import Decimal
import math
from typing import Optional, Sequence
from src.config.walkforward_config import BacktestFrictionConfig, WalkForwardConfig
from src.consensus.models import ConsensusDirection
from src.storage.repositories import SQLiteMarketPriceRepository
from src.walkforward.models import ShadowEquityPoint, ShadowStrategyResult, WalkForwardDecision


class ConsensusShadowStrategySimulator:
    """
    Simulador da Shadow Strategy Normalizada de Consenso.
    
    Representa a evolução de um índice fictício unitário (+1.0 LONG, -1.0 SHORT, 0.0 Neutro)
    seguindo estritamente a direção congelada pelo consenso no fechamento de cada período T,
    aplicando a exposição no período subsequente (T -> T+1), deduzindo custos de fricção
    sobre o turnover e gerando curvas de equity bruta e líquida.
    """
    def __init__(
        self,
        price_repo: SQLiteMarketPriceRepository,
        friction_config: Optional[BacktestFrictionConfig] = None
    ):
        self.price_repo = price_repo
        self.friction = friction_config or BacktestFrictionConfig()

    @staticmethod
    def _normalize_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _get_price_at(self, symbol: str, dt: datetime) -> Optional[float]:
        p = self.price_repo.get_latest_price_until_as_of(symbol, dt)
        return float(p) if p is not None else None

    def simulate_shadow_strategy(
        self,
        symbol: str,
        decisions: Sequence[WalkForwardDecision]
    ) -> ShadowStrategyResult:
        """
        Simula a Shadow Strategy para as decisões cronológicas de um símbolo.
        """
        if not decisions:
            return ShadowStrategyResult(symbol=symbol)

        sorted_decs = sorted(decisions, key=lambda d: self._normalize_utc(d.decision_as_of))
        equity_curve: list[ShadowEquityPoint] = []

        gross_equity = 1.0
        net_equity = 1.0
        peak_net_equity = 1.0
        prev_exposure = 0.0

        net_returns: list[float] = []
        downside_returns: list[float] = []
        total_turnover = 0.0
        total_costs = 0.0

        long_periods = 0
        short_periods = 0
        flat_periods = 0

        for i in range(len(sorted_decs)):
            curr_dec = sorted_decs[i]
            t_curr = self._normalize_utc(curr_dec.decision_as_of)

            # 1. Determina Exposição Unitária Normalizada
            if curr_dec.consensus_direction == ConsensusDirection.LONG:
                target_exp = 1.0
                long_periods += 1
            elif curr_dec.consensus_direction == ConsensusDirection.SHORT:
                target_exp = -1.0
                short_periods += 1
            else:
                target_exp = 0.0
                flat_periods += 1

            # 2. Calcula Turnover e Fricção de Transição
            turnover = abs(target_exp - prev_exposure)
            friction_cost = turnover * self.friction.total_friction_rate
            total_turnover += turnover
            total_costs += friction_cost

            # 3. Mede retorno do ativo até o próximo ponto de decisão
            if i < len(sorted_decs) - 1:
                t_next = self._normalize_utc(sorted_decs[i + 1].decision_as_of)
                p_curr = self._get_price_at(symbol, t_curr)
                p_next = self._get_price_at(symbol, t_next)

                if p_curr is not None and p_next is not None and p_curr > 0:
                    raw_price_ret = (p_next - p_curr) / p_curr
                else:
                    raw_price_ret = 0.0
            else:
                raw_price_ret = 0.0

            # 4. Retornos Bruto e Líquido do Período
            gross_period_ret = target_exp * raw_price_ret
            net_period_ret = gross_period_ret - friction_cost

            # Atualiza Cota de Equity
            gross_equity *= (1.0 + gross_period_ret)
            net_equity *= (1.0 + net_period_ret)
            if net_equity > peak_net_equity:
                peak_net_equity = net_equity

            drawdown = max(0.0, (peak_net_equity - net_equity) / peak_net_equity) if peak_net_equity > 0 else 0.0

            net_returns.append(net_period_ret)
            if net_period_ret < 0:
                downside_returns.append(net_period_ret)

            pt = ShadowEquityPoint(
                as_of=t_curr,
                symbol=symbol,
                consensus_direction=curr_dec.consensus_direction,
                target_exposure=target_exp,
                raw_price_return=round(raw_price_ret * 100.0, 4),
                gross_period_return=round(gross_period_ret * 100.0, 4),
                turnover=round(turnover, 2),
                friction_cost=round(friction_cost * 100.0, 4),
                net_period_return=round(net_period_ret * 100.0, 4),
                gross_equity=round(gross_equity, 6),
                net_equity=round(net_equity, 6),
                drawdown=round(drawdown, 4)
            )
            equity_curve.append(pt)
            prev_exposure = target_exp

        # 5. Métricas Estatísticas Agregadas
        n_obs = len(equity_curve)
        cum_gross = (gross_equity - 1.0) * 100.0
        cum_net = (net_equity - 1.0) * 100.0
        max_dd = max((pt.drawdown for pt in equity_curve), default=0.0)

        pos_count = sum(1 for r in net_returns if r > 0)
        pos_rate = (pos_count / n_obs) * 100.0 if n_obs > 0 else 0.0

        time_in_mkt = ((long_periods + short_periods) / n_obs) * 100.0 if n_obs > 0 else 0.0
        long_rate = (long_periods / n_obs) * 100.0 if n_obs > 0 else 0.0
        short_rate = (short_periods / n_obs) * 100.0 if n_obs > 0 else 0.0
        flat_rate = (flat_periods / n_obs) * 100.0 if n_obs > 0 else 0.0

        # Métricas de Risco (Sharpe, Sortino, Volatilidade)
        mean_ret = sum(net_returns) / n_obs if n_obs > 0 else 0.0
        var_ret = sum((r - mean_ret) ** 2 for r in net_returns) / (n_obs - 1) if n_obs > 1 else 0.0
        vol = math.sqrt(var_ret) if var_ret > 0 else 0.0

        sharpe: Optional[float] = None
        if vol > 1e-8:
            sharpe = round(mean_ret / vol, 4)

        sortino: Optional[float] = None
        if downside_returns:
            downside_var = sum(r ** 2 for r in downside_returns) / len(downside_returns)
            downside_vol = math.sqrt(downside_var)
            if downside_vol > 1e-8:
                sortino = round(mean_ret / downside_vol, 4)

        calmar: Optional[float] = None
        if max_dd > 1e-4:
            calmar = round((cum_net / 100.0) / max_dd, 4)

        return ShadowStrategyResult(
            symbol=symbol,
            equity_curve=equity_curve,
            cumulative_gross_return=round(cum_gross, 4),
            cumulative_net_return=round(cum_net, 4),
            volatility=round(vol * 100.0, 4) if vol > 0 else None,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown=round(max_dd, 4),
            calmar_ratio=calmar,
            positive_period_rate=round(pos_rate, 2),
            total_turnover=round(total_turnover, 2),
            total_simulated_costs=round(total_costs * 100.0, 4),
            time_in_market_pct=round(time_in_mkt, 2),
            long_exposure_rate_pct=round(long_rate, 2),
            short_exposure_rate_pct=round(short_rate, 2),
            flat_exposure_rate_pct=round(flat_rate, 2)
        )
