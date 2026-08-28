import statistics
from typing import Sequence
from src.ranking.models import (
    RankingTurnoverMetric,
    TraderRankingSnapshot,
    TraderRankPersistence,
)


class RankPersistenceCalculator:
    """
    Calculadora de métricas de persistência de posição e turnover entre rankings históricos.
    """
    @staticmethod
    def calculate_trader_persistence(
        rankings: Sequence[TraderRankingSnapshot],
        trader_id: str,
        use_qualified_only: bool = False
    ) -> TraderRankPersistence:
        """
        Calcula as métricas de persistência de um trader específico ao longo de uma série de rankings.
        """
        ranks_observed: list[int] = []

        for r_snap in rankings:
            ranking_list = r_snap.qualified_ranking if use_qualified_only else r_snap.full_ranking
            for item in ranking_list:
                if item.trader_id == trader_id:
                    ranks_observed.append(item.rank)
                    break

        eval_count = len(ranks_observed)
        if eval_count == 0:
            return TraderRankPersistence(
                trader_id=trader_id,
                evaluation_count=0,
                top_3_percentage=0.0,
                top_5_percentage=0.0,
                top_10_percentage=0.0,
                average_rank=999.0,
                best_rank=999,
                worst_rank=999
            )

        top_3 = sum(1 for r in ranks_observed if r <= 3)
        top_5 = sum(1 for r in ranks_observed if r <= 5)
        top_10 = sum(1 for r in ranks_observed if r <= 10)

        return TraderRankPersistence(
            trader_id=trader_id,
            evaluation_count=eval_count,
            top_3_percentage=round(100.0 * top_3 / eval_count, 2),
            top_5_percentage=round(100.0 * top_5 / eval_count, 2),
            top_10_percentage=round(100.0 * top_10 / eval_count, 2),
            average_rank=round(statistics.mean(ranks_observed), 2),
            best_rank=min(ranks_observed),
            worst_rank=max(ranks_observed)
        )

    @staticmethod
    def calculate_all_persistence(
        rankings: Sequence[TraderRankingSnapshot],
        use_qualified_only: bool = False
    ) -> dict[str, TraderRankPersistence]:
        """Calcula a persistência de todos os traders observados na série de rankings."""
        all_traders: set[str] = set()
        for r_snap in rankings:
            for item in r_snap.full_ranking:
                all_traders.add(item.trader_id)

        return {
            t_id: RankPersistenceCalculator.calculate_trader_persistence(rankings, t_id, use_qualified_only)
            for t_id in all_traders
        }

    @staticmethod
    def calculate_series_turnover(
        rankings: Sequence[TraderRankingSnapshot],
        top_n: int = 5,
        use_qualified_only: bool = False
    ) -> list[RankingTurnoverMetric]:
        """
        Calcula a rotatividade (turnover) do Top N entre pares consecutivos de snapshots de ranking.
        """
        if len(rankings) < 2:
            return []

        turnover_metrics: list[RankingTurnoverMetric] = []

        for i in range(len(rankings) - 1):
            r1 = rankings[i]
            r2 = rankings[i + 1]

            list_1 = r1.qualified_ranking if use_qualified_only else r1.full_ranking
            list_2 = r2.qualified_ranking if use_qualified_only else r2.full_ranking

            top_1 = {item.trader_id for item in list_1[:top_n]}
            top_2 = {item.trader_id for item in list_2[:top_n]}

            if not top_1 and not top_2:
                turnover_pct = 0.0
            elif not top_1 or not top_2:
                turnover_pct = 100.0
            else:
                # Quantidade de membros do top_2 que NÃO estavam em top_1
                new_members = len(top_2 - top_1)
                turnover_pct = round(100.0 * new_members / max(len(top_1), len(top_2)), 2)

            turnover_metrics.append(
                RankingTurnoverMetric(
                    from_as_of=r1.as_of,
                    to_as_of=r2.as_of,
                    top_n=top_n,
                    turnover_pct=turnover_pct
                )
            )

        return turnover_metrics
