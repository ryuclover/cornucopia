from datetime import datetime, timezone
from src.config.survival_config import SurvivalCriteriaConfig
from src.scoring.models import TraderPerformance, TraderScore


class SurvivorScoreV1:
    """
    Motor do Survivor Score V1.
    
    Algoritmo determinístico, transparente e calibrado para priorizar:
    'Sobreviver primeiro, lucrar depois'.
    
    Pontuação final varia de 0.0 a 100.0.
    """
    def __init__(self, config: SurvivalCriteriaConfig | None = None):
        self.config = config or SurvivalCriteriaConfig()

    def evaluate(self, perf: TraderPerformance) -> TraderScore:
        """
        Avalia o TraderPerformance segundo as regras do Survivor Score V1.
        """
        disqualification_reasons: list[str] = []

        # 1. Verificação dos Critérios Rígidos de Sobrevivência (Hard Gatekeepers)
        if perf.history_days < self.config.min_history_days:
            disqualification_reasons.append(
                f"Histórico insuficiente: {perf.history_days:.1f} dias (mínimo exigido: {self.config.min_history_days})"
            )

        if perf.total_trades < self.config.min_trade_count:
            disqualification_reasons.append(
                f"Número insuficiente de trades: {perf.total_trades} (mínimo exigido: {self.config.min_trade_count})"
            )

        if perf.max_drawdown_pct > self.config.max_allowed_drawdown_pct:
            disqualification_reasons.append(
                f"Drawdown excessivo: {perf.max_drawdown_pct:.2f}% (limite máximo: {self.config.max_allowed_drawdown_pct:.2f}%)"
            )

        if perf.largest_loss_pct > self.config.max_single_trade_loss_pct:
            disqualification_reasons.append(
                f"Perda catastrófica individual: {perf.largest_loss_pct:.2f}% (limite máximo: {self.config.max_single_trade_loss_pct:.2f}%)"
            )

        if perf.total_return_pct < self.config.min_net_return_pct:
            disqualification_reasons.append(
                f"Retorno líquido negativo ou insuficiente: {perf.total_return_pct:.2f}% (mínimo exigido: {self.config.min_net_return_pct:.2f}%)"
            )

        if perf.profit_factor < self.config.min_profit_factor:
            disqualification_reasons.append(
                f"Profit Factor insuficiente: {perf.profit_factor:.2f} (mínimo exigido: {self.config.min_profit_factor:.2f})"
            )

        if perf.max_consecutive_losses > self.config.max_consecutive_losses:
            disqualification_reasons.append(
                f"Sequência de derrotas excessiva: {perf.max_consecutive_losses} (limite: {self.config.max_consecutive_losses})"
            )

        if perf.sharpe_ratio < self.config.min_sharpe_ratio:
            disqualification_reasons.append(
                f"Sharpe Ratio insuficiente: {perf.sharpe_ratio:.2f} (mínimo exigido: {self.config.min_sharpe_ratio:.2f})"
            )

        is_qualified = (len(disqualification_reasons) == 0)

        # 2. Cálculo dos 4 Sub-Scores (0 a 100 cada)

        # Sub-score 1: Preservação de Capital & Drawdown (Peso 40%)
        # Drawdown <= 5% -> 100 pts. Drawdown >= max_allowed -> 0 pts.
        dd_min_benchmark = 5.0
        dd_max_limit = self.config.max_allowed_drawdown_pct
        if perf.max_drawdown_pct <= dd_min_benchmark:
            drawdown_score = 100.0
        elif perf.max_drawdown_pct >= dd_max_limit:
            drawdown_score = 0.0
        else:
            drawdown_score = 100.0 * (dd_max_limit - perf.max_drawdown_pct) / (dd_max_limit - dd_min_benchmark)

        # Sub-score 2: Risco de Cauda, Consistência & Concentração (Peso 25%)
        # Avalia a maior perda individual, sequência de perdas e concentração dos lucros nos Top trades
        loss_ratio = perf.largest_loss_pct / self.config.max_single_trade_loss_pct if self.config.max_single_trade_loss_pct > 0 else 1.0
        single_loss_score = max(0.0, 100.0 * (1.0 - min(loss_ratio, 1.0)))

        cons_loss_ratio = perf.max_consecutive_losses / self.config.max_consecutive_losses if self.config.max_consecutive_losses > 0 else 1.0
        streak_score = max(0.0, 100.0 * (1.0 - min(cons_loss_ratio, 1.0)))

        # Concentração: se Top N trades representam > 60% dos lucros, penaliza proporcionalmente
        max_conc = self.config.max_top_trades_concentration_pct
        if perf.top_n_trades_pnl_contribution_pct <= max_conc:
            concentration_score = 100.0
        else:
            concentration_score = max(0.0, 100.0 * (100.0 - perf.top_n_trades_pnl_contribution_pct) / (100.0 - max_conc))

        tail_risk_score = 0.40 * single_loss_score + 0.30 * streak_score + 0.30 * concentration_score

        # Sub-score 3: Qualidade do Retorno Ajustado ao Risco (Peso 20%)
        # Profit factor: 1.0 -> 0 pts, 2.0+ -> 100 pts (com saturação suave)
        if perf.profit_factor <= 1.0:
            pf_score = 0.0
        else:
            pf_score = min(100.0, ((perf.profit_factor - 1.0) / 1.5) * 100.0)

        # Sortino ratio: 0.0 -> 0 pts, 2.5+ -> 100 pts
        if perf.sortino_ratio <= 0.0:
            sortino_subscore = 0.0
        else:
            sortino_subscore = min(100.0, (perf.sortino_ratio / 2.5) * 100.0)

        risk_adjusted_return_score = 0.50 * pf_score + 0.50 * sortino_subscore

        # Sub-score 4: Maturidade do Histórico & Amostragem (Peso 15%)
        # Dias: até 180 dias para 100% | Trades: até 100 trades para 100%
        days_score = min(100.0, (perf.history_days / 180.0) * 100.0)
        trades_score = min(100.0, (perf.total_trades / 100.0) * 100.0)
        maturity_score = 0.50 * days_score + 0.50 * trades_score

        # 3. Composição Ponderada (40% / 25% / 20% / 15%)
        raw_score = (
            0.40 * drawdown_score +
            0.25 * tail_risk_score +
            0.20 * risk_adjusted_return_score +
            0.15 * maturity_score
        )

        # Se o trader for desqualificado por risco catastrófico ou drawdown, zera o score de consenso
        # Se for por amostragem insuficiente, aplica penalidade de 50%
        if not is_qualified:
            has_fatal_breach = (
                perf.max_drawdown_pct > self.config.max_allowed_drawdown_pct or
                perf.largest_loss_pct > self.config.max_single_trade_loss_pct or
                perf.net_pnl < 0
            )
            if has_fatal_breach:
                final_score = 0.0
            else:
                final_score = raw_score * 0.40
        else:
            final_score = raw_score

        final_score = max(0.0, min(100.0, round(final_score, 2)))

        return TraderScore(
            trader_id=perf.trader_id,
            calculated_at=perf.as_of,
            score_total=final_score,
            is_qualified=is_qualified,
            drawdown_score=round(drawdown_score, 2),
            tail_risk_score=round(tail_risk_score, 2),
            risk_adjusted_return_score=round(risk_adjusted_return_score, 2),
            maturity_score=round(maturity_score, 2),
            disqualification_reasons=disqualification_reasons,
        )
