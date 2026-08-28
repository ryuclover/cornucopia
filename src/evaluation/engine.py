import math
import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Sequence
from src.config.evaluation_config import EvaluationConfig, EvaluationFrequency
from src.config.survival_config import SurvivalCriteriaConfig
from src.evaluation.models import (
    QualificationStatus,
    ScoreTrend,
    TraderEvaluationSnapshot,
    TraderStabilityMetrics,
)
from src.metrics.calculator import PerformanceCalculator
from src.replay.engine import TraderReplayEngine
from src.scoring.survivor_v1 import SurvivorScoreV1


class TraderEvaluationEngine:
    """
    Motor de Avaliação Longitudinal de Traders.
    
    Orquestra o TraderReplayEngine para gerar snapshots de avaliação e calcular métricas
    de consistência temporal, tendências e janelas comparativas de curto/médio/longo prazo.
    """
    def __init__(
        self,
        replay_engine: TraderReplayEngine,
        survival_config: Optional[SurvivalCriteriaConfig] = None,
        evaluation_config: Optional[EvaluationConfig] = None,
    ):
        self.replay_engine = replay_engine
        self.survival_config = survival_config or SurvivalCriteriaConfig()
        self.evaluation_config = evaluation_config or EvaluationConfig()
        self.scorer = SurvivorScoreV1(self.survival_config)
        # Garante alinhamento de critérios entre replay e evaluation
        self.replay_engine.scorer = self.scorer

    def _normalize_utc(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def evaluate_trader(self, trader_id: str, as_of: datetime) -> TraderEvaluationSnapshot:
        """
        Executa a avaliação pontual de um trader em 'as_of'.
        """
        as_of = self._normalize_utc(as_of)
        
        # 1. Executa o Replay ponto-no-tempo
        replay_res = self.replay_engine.replay_trader(trader_id, as_of=as_of, compute_score=True)
        perf = replay_res.performance
        score = replay_res.score or self.scorer.evaluate(perf)
        
        trader = self.replay_engine.trader_repo.get_by_id(trader_id)
        history_start = trader.created_at if trader else as_of

        # 2. Identifica o status de qualificação distinguindo inexperiência de degradação
        if score.is_qualified:
            qual_status = QualificationStatus.QUALIFIED
        else:
            is_insufficient_sample = (
                perf.history_days < self.survival_config.min_history_days or
                perf.total_trades < self.survival_config.min_trade_count
            )
            has_catastrophic_risk_breach = (
                perf.max_drawdown_pct > self.survival_config.max_allowed_drawdown_pct or
                perf.largest_loss_pct > self.survival_config.max_single_trade_loss_pct or
                perf.max_consecutive_losses > self.survival_config.max_consecutive_losses
            )
            if is_insufficient_sample and not has_catastrophic_risk_breach:
                qual_status = QualificationStatus.INSUFFICIENT_HISTORY
            else:
                qual_status = QualificationStatus.DISQUALIFIED

        # 3. Calcula auditoria detalhada de janelas recentes (30d, 90d, 180d)
        w30 = self._calculate_window_result(
            replay_res.closed_trades,
            as_of,
            self.evaluation_config.recent_window_days,
            self.evaluation_config.min_trades_30d,
            replay_res.initial_capital,
            trader_id
        )
        w90 = self._calculate_window_result(
            replay_res.closed_trades,
            as_of,
            self.evaluation_config.medium_window_days,
            self.evaluation_config.min_trades_90d,
            replay_res.initial_capital,
            trader_id
        )
        w180 = self._calculate_window_result(
            replay_res.closed_trades,
            as_of,
            self.evaluation_config.long_window_days,
            self.evaluation_config.min_trades_180d,
            replay_res.initial_capital,
            trader_id
        )

        return TraderEvaluationSnapshot(
            trader_id=trader_id,
            as_of=as_of,
            history_start=history_start,
            history_days=perf.history_days,
            trade_count=perf.total_trades,
            realized_pnl=replay_res.total_realized_pnl,
            realized_equity=replay_res.realized_equity,
            net_return_pct=perf.total_return_pct,
            max_drawdown_pct=perf.max_drawdown_pct,
            win_rate=perf.win_rate,
            profit_factor=perf.profit_factor,
            sharpe_ratio=perf.sharpe_ratio,
            sortino_ratio=perf.sortino_ratio,
            largest_loss_pct=perf.largest_loss_pct,
            max_consecutive_losses=perf.max_consecutive_losses,
            top_1_trade_pnl_contribution_pct=perf.top_1_trade_pnl_contribution_pct,
            top_5_trades_pnl_contribution_pct=perf.top_5_trades_pnl_contribution_pct,
            top_10_percent_trades_pnl_contribution_pct=perf.top_10_percent_trades_pnl_contribution_pct,
            survivor_score=score.score_total,
            is_qualified=score.is_qualified,
            qualification_status=qual_status,
            disqualification_reasons=score.disqualification_reasons,
            valuation_status=replay_res.valuation_status,
            drawdown_score=score.drawdown_score,
            tail_risk_score=score.tail_risk_score,
            risk_adjusted_return_score=score.risk_adjusted_return_score,
            maturity_score=score.maturity_score,
            window_30d=w30,
            window_90d=w90,
            window_180d=w180,
            score_30d=w30.score,
            score_90d=w90.score,
            score_180d=w180.score,
            trade_count_30d=w30.trade_count,
            trade_count_90d=w90.trade_count,
            trade_count_180d=w180.trade_count,
            score_lifetime=score.score_total
        )

    def _calculate_window_result(
        self,
        all_closed_trades: list,
        as_of: datetime,
        window_days: int,
        min_required_trades: int,
        initial_capital: Decimal,
        trader_id: str
    ):
        """Calcula WindowEvaluationResult com validação explícita de suficiência de amostragem."""
        from src.evaluation.models import WindowEvaluationResult, WindowSampleStatus

        window_start = as_of - timedelta(days=window_days)
        window_trades = [t for t in all_closed_trades if t.exit_time >= window_start and t.exit_time <= as_of]
        trade_count = len(window_trades)

        if trade_count < min_required_trades:
            return WindowEvaluationResult(
                window_days=window_days,
                trade_count=trade_count,
                min_required_trades=min_required_trades,
                start_date=window_start,
                end_date=as_of,
                sample_status=WindowSampleStatus.INSUFFICIENT_SAMPLE,
                score=None
            )

        window_perf = PerformanceCalculator.calculate(
            trader_id=trader_id,
            trades=window_trades,
            as_of=as_of,
            initial_capital=initial_capital,
            first_history_date=window_start
        )
        window_score = self.scorer.evaluate(window_perf)

        return WindowEvaluationResult(
            window_days=window_days,
            trade_count=trade_count,
            min_required_trades=min_required_trades,
            start_date=window_start,
            end_date=as_of,
            sample_status=WindowSampleStatus.SUFFICIENT,
            score=window_score.score_total
        )

    def evaluate_series(
        self,
        trader_id: str,
        start: datetime,
        end: datetime,
        frequency: Optional[EvaluationFrequency] = None
    ) -> list[TraderEvaluationSnapshot]:
        """
        Avalia o mesmo trader longitudinalmente ao longo de múltiplos pontos no tempo.
        """
        start = self._normalize_utc(start)
        end = self._normalize_utc(end)
        freq = frequency or self.evaluation_config.frequency

        timestamps = self.generate_evaluation_timestamps(start, end, freq)
        snapshots: list[TraderEvaluationSnapshot] = []

        for ts in timestamps:
            snap = self.evaluate_trader(trader_id, as_of=ts)
            snapshots.append(snap)

        return snapshots

    @staticmethod
    def generate_evaluation_timestamps(
        start: datetime,
        end: datetime,
        frequency: EvaluationFrequency
    ) -> list[datetime]:
        """Gera pontos temporais determinísticos entre start e end."""
        if start > end:
            return []

        step = timedelta(days=1)
        if frequency == EvaluationFrequency.WEEKLY:
            step = timedelta(days=7)
        elif frequency == EvaluationFrequency.MONTHLY:
            step = timedelta(days=30)

        current = start
        timestamps = []
        while current <= end:
            timestamps.append(current)
            current += step

        if not timestamps or timestamps[-1] < end:
            timestamps.append(end)

        return timestamps

    def calculate_stability_metrics(
        self,
        snapshots: Sequence[TraderEvaluationSnapshot]
    ) -> TraderStabilityMetrics:
        """
        Calcula métricas agregadas de estabilidade e consistência sobre uma série temporal de snapshots.
        """
        if not snapshots:
            raise ValueError("Lista de snapshots não pode ser vazia para cálculo de estabilidade.")

        trader_id = snapshots[0].trader_id
        scores = [s.survivor_score for s in snapshots]
        n = len(scores)

        mean_score = round(statistics.mean(scores), 2)
        median_score = round(statistics.median(scores), 2)
        score_std_dev = round(statistics.stdev(scores), 2) if n > 1 else 0.0
        min_score = min(scores)
        max_score = max(scores)

        qualified_count = sum(1 for s in snapshots if s.is_qualified)
        qualification_rate_pct = round(100.0 * qualified_count / n, 2)

        # Positive Period Rate: mede o delta de retorno do intervalo entre avaliações consecutivas
        if n >= 2:
            positive_intervals = 0
            total_intervals = n - 1
            for i in range(total_intervals):
                delta_eq = snapshots[i + 1].realized_equity - snapshots[i].realized_equity
                if delta_eq > Decimal("0.0"):
                    positive_intervals += 1
            positive_period_rate_pct = round(100.0 * positive_intervals / total_intervals, 2)
        else:
            positive_period_rate_pct = 100.0 if snapshots[0].net_return_pct > 0 else 0.0

        warn_threshold = self.evaluation_config.drawdown_warning_threshold_pct
        drawdown_breach_count = sum(1 for s in snapshots if s.max_drawdown_pct > warn_threshold)

        # Cálculo de tendência de score ignorando períodos de histórico insuficiente
        valid_snaps = [
            s for s in snapshots
            if s.qualification_status != QualificationStatus.INSUFFICIENT_HISTORY and s.survivor_score is not None
        ]
        valid_scores = [s.survivor_score for s in valid_snaps]

        trend_window = min(len(valid_scores), self.evaluation_config.trend_window_periods)
        recent_valid_scores = valid_scores[-trend_window:] if trend_window > 0 else []

        if len(recent_valid_scores) < 2:
            trend = ScoreTrend.INSUFFICIENT_DATA
            slope = 0.0
        else:
            x_vals = list(range(len(recent_valid_scores)))
            x_mean = statistics.mean(x_vals)
            y_mean = statistics.mean(recent_valid_scores)

            numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, recent_valid_scores))
            denominator = sum((x - x_mean) ** 2 for x in x_vals)

            slope = (numerator / denominator) if denominator != 0 else 0.0
            slope = round(slope, 3)

            if slope > self.evaluation_config.improving_slope_threshold:
                trend = ScoreTrend.IMPROVING
            elif slope < self.evaluation_config.deterioration_slope_threshold:
                trend = ScoreTrend.DETERIORATING
            else:
                trend = ScoreTrend.STABLE

        return TraderStabilityMetrics(
            trader_id=trader_id,
            period_count=n,
            mean_score=mean_score,
            median_score=median_score,
            score_std_dev=score_std_dev,
            min_score=min_score,
            max_score=max_score,
            qualification_rate_pct=qualification_rate_pct,
            positive_period_rate_pct=positive_period_rate_pct,
            drawdown_breach_count=drawdown_breach_count,
            score_trend=trend,
            score_trend_slope=slope
        )
