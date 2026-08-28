from typing import Sequence
from src.weighting.models import (
    CoreWeightSnapshot,
    GroupWeightSummary,
    TraderWeight,
    WeightConcentrationMetrics,
    WeightTurnoverMetric,
)


class WeightDiagnosticsCalculator:
    """
    Calculador de Métricas de Concentração, Diversidade Efetiva e Turnover da Estrutura de Pesos.
    """
    @classmethod
    def calculate_concentration(
        cls,
        trader_weights: Sequence[TraderWeight],
        group_summaries: Sequence[GroupWeightSummary]
    ) -> WeightConcentrationMetrics:
        """
        Calcula as métricas de concentração de Herfindahl e Número Efetivo de Participantes.
        """
        if not trader_weights:
            return WeightConcentrationMetrics(
                effective_trader_count=0.0,
                herfindahl_index=0.0,
                top_1_weight_share_pct=0.0,
                top_3_weight_share_pct=0.0,
                top_5_weight_share_pct=0.0,
                effective_group_count=0.0,
                group_herfindahl_index=0.0
            )

        weights = [tw.normalized_weight for tw in trader_weights]
        sum_sq = sum(w * w for w in weights)
        eff_traders = (1.0 / sum_sq) if sum_sq > 1e-9 else 0.0

        sorted_w = sorted(weights, reverse=True)
        top_1 = (sorted_w[0] * 100.0) if len(sorted_w) >= 1 else 0.0
        top_3 = (sum(sorted_w[:3]) * 100.0) if len(sorted_w) >= 3 else sum(sorted_w) * 100.0
        top_5 = (sum(sorted_w[:5]) * 100.0) if len(sorted_w) >= 5 else sum(sorted_w) * 100.0

        # Concentração por Grupo
        if group_summaries:
            group_weights = [g.total_group_weight for g in group_summaries]
            sum_g_sq = sum(gw * gw for gw in group_weights)
            eff_groups = (1.0 / sum_g_sq) if sum_g_sq > 1e-9 else 0.0
        else:
            sum_g_sq = sum_sq
            eff_groups = eff_traders

        return WeightConcentrationMetrics(
            effective_trader_count=round(eff_traders, 2),
            herfindahl_index=round(sum_sq, 4),
            top_1_weight_share_pct=round(top_1, 2),
            top_3_weight_share_pct=round(top_3, 2),
            top_5_weight_share_pct=round(top_5, 2),
            effective_group_count=round(eff_groups, 2),
            group_herfindahl_index=round(sum_g_sq, 4)
        )

    @classmethod
    def calculate_turnover(
        cls,
        snap_before: CoreWeightSnapshot,
        snap_after: CoreWeightSnapshot
    ) -> WeightTurnoverMetric:
        """
        Calcula o turnover percentual entre duas alocações de pesos:
        turnover = 0.5 * sum(|w_i(t) - w_i(t-1)|) * 100%
        """
        all_ids = sorted(list(set(snap_before.selected_traders) | set(snap_after.selected_traders)))
        
        map_before = snap_before.weights_map
        map_after = snap_after.weights_map

        total_abs_diff = 0.0
        deltas: dict[str, float] = {}

        for tid in all_ids:
            w_prev = map_before[tid].normalized_weight if tid in map_before else 0.0
            w_curr = map_after[tid].normalized_weight if tid in map_after else 0.0
            diff = w_curr - w_prev
            deltas[tid] = round(diff * 100.0, 2)
            total_abs_diff += abs(diff)

        turnover_pct = round(0.5 * total_abs_diff * 100.0, 2)

        max_inc = max(deltas.items(), key=lambda x: x[1])[0] if deltas else None
        max_dec = min(deltas.items(), key=lambda x: x[1])[0] if deltas else None

        return WeightTurnoverMetric(
            from_as_of=snap_before.as_of,
            to_as_of=snap_after.as_of,
            turnover_pct=turnover_pct,
            weight_deltas=deltas,
            max_weight_increase_trader=max_inc,
            max_weight_decrease_trader=max_dec
        )
