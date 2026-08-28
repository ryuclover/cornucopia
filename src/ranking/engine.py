from datetime import datetime, timezone
from typing import Optional, Sequence
from src.config.evaluation_config import EvaluationFrequency
from src.evaluation.engine import TraderEvaluationEngine
from src.ranking.models import TraderRankingItem, TraderRankingSnapshot
from src.storage.repositories.base import TraderRepository


class TraderRankingEngine:
    """
    Motor de Ranking Histórico e Longitudinal de Traders.
    
    Constrói rankings rigorosamente ponto-no-tempo (point-in-time), evitando
    tanto o viés de antecipação (look-ahead bias) quanto o viés de sobrevivência (survivorship bias).
    """
    def __init__(
        self,
        evaluation_engine: TraderEvaluationEngine,
        trader_repo: Optional[TraderRepository] = None
    ):
        self.evaluation_engine = evaluation_engine
        self.trader_repo = trader_repo or evaluation_engine.replay_engine.trader_repo

    def _normalize_utc(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def rank(
        self,
        trader_ids: Optional[Sequence[str]] = None,
        as_of: Optional[datetime] = None
    ) -> TraderRankingSnapshot:
        """
        Gera o ranking estritamente ponto-no-tempo em 'as_of'.
        """
        as_of_dt = self._normalize_utc(as_of or datetime.now(timezone.utc))

        # 1. Identifica o universo de traders elegíveis naquele instante
        if trader_ids is None:
            all_traders = self.trader_repo.list_all()
            # Anti-survivorship bias: inclui qualquer trader criado em ou antes de as_of,
            # independentemente de ter sido desativado posteriormente
            target_ids = [t.trader_id for t in all_traders if t.created_at <= as_of_dt]
        else:
            target_ids = list(trader_ids)

        # 2. Avalia cada trader individualmente até as_of
        snapshots = []
        for t_id in target_ids:
            snap = self.evaluation_engine.evaluate_trader(t_id, as_of=as_of_dt)
            snapshots.append(snap)

        # 3. Ordenação determinística do Full Ranking
        # Chave de ordenação: (survivor_score DESC, net_return_pct DESC, max_drawdown_pct ASC, trader_id ASC)
        sorted_snaps = sorted(
            snapshots,
            key=lambda s: (-s.survivor_score, -s.net_return_pct, s.max_drawdown_pct, s.trader_id)
        )

        full_ranking: list[TraderRankingItem] = []
        for rank_idx, snap in enumerate(sorted_snaps, start=1):
            full_ranking.append(
                TraderRankingItem(
                    rank=rank_idx,
                    trader_id=snap.trader_id,
                    score=snap.survivor_score,
                    is_qualified=snap.is_qualified,
                    qualification_status=snap.qualification_status,
                    history_days=snap.history_days,
                    trade_count=snap.trade_count,
                    max_drawdown_pct=snap.max_drawdown_pct,
                    net_return_pct=snap.net_return_pct,
                    profit_factor=snap.profit_factor,
                    disqualification_reasons=snap.disqualification_reasons,
                    valuation_status=snap.valuation_status
                )
            )

        # 4. Qualified Ranking (somente traders que superaram todos os critérios de sobrevivência)
        qualified_snaps = [s for s in sorted_snaps if s.is_qualified]
        qualified_ranking: list[TraderRankingItem] = []
        for rank_idx, snap in enumerate(qualified_snaps, start=1):
            qualified_ranking.append(
                TraderRankingItem(
                    rank=rank_idx,
                    trader_id=snap.trader_id,
                    score=snap.survivor_score,
                    is_qualified=snap.is_qualified,
                    qualification_status=snap.qualification_status,
                    history_days=snap.history_days,
                    trade_count=snap.trade_count,
                    max_drawdown_pct=snap.max_drawdown_pct,
                    net_return_pct=snap.net_return_pct,
                    profit_factor=snap.profit_factor,
                    disqualification_reasons=snap.disqualification_reasons,
                    valuation_status=snap.valuation_status
                )
            )

        return TraderRankingSnapshot(
            as_of=as_of_dt,
            full_ranking=full_ranking,
            qualified_ranking=qualified_ranking,
            total_traders=len(full_ranking),
            qualified_traders=len(qualified_ranking)
        )

    def rank_series(
        self,
        start: datetime,
        end: datetime,
        trader_ids: Optional[Sequence[str]] = None,
        frequency: EvaluationFrequency = EvaluationFrequency.MONTHLY
    ) -> list[TraderRankingSnapshot]:
        """
        Gera uma série temporal contínua de rankings históricos.
        """
        start_dt = self._normalize_utc(start)
        end_dt = self._normalize_utc(end)

        timestamps = TraderEvaluationEngine.generate_evaluation_timestamps(start_dt, end_dt, frequency)
        series: list[TraderRankingSnapshot] = []

        for ts in timestamps:
            r_snap = self.rank(trader_ids=trader_ids, as_of=ts)
            series.append(r_snap)

        return series
