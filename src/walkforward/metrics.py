from datetime import datetime, timezone
import math
import statistics
from typing import Any, Optional, Sequence
from src.config.evaluation_config import EvaluationFrequency
from src.config.walkforward_config import EvaluationStatus, OutcomeClassification, WalkForwardConfig
from src.consensus.models import ConsensusDirection
from src.walkforward.models import (
    ConsensusEpisode,
    EfficacyMetricSet,
    ForwardReturnOutcome,
    HorizonEfficacySummary,
    WalkForwardDecision,
    WalkForwardDecisionJournal,
)


class WalkForwardMetricsCalculator:
    """
    Calculador de Métricas de Eficácia de Sinais, Distribuição, Buckets, Subconjuntos Não-Sobrepostos e Regimes.
    
    Analisa retornos assinados, taxas de acerto (hit rate), razão de payoff, concentração
    de cauda (Top 1, Top 5, Top 10%), estratificação por buckets e isolamento de Final Holdout.
    """
    def __init__(self, config: WalkForwardConfig):
        self.config = config

    @staticmethod
    def _normalize_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def calculate_metric_set(
        self,
        outcomes: Sequence[ForwardReturnOutcome],
        decisions_map: dict[str, WalkForwardDecision]
    ) -> EfficacyMetricSet:
        """
        Calcula o conjunto padronizado EfficacyMetricSet para uma lista de outcomes.
        """
        evaluated = [o for o in outcomes if o.evaluation_status == EvaluationStatus.EVALUATED]
        directional = [
            o for o in evaluated
            if decisions_map.get(o.decision_id) is not None and
            decisions_map[o.decision_id].consensus_direction in (ConsensusDirection.LONG, ConsensusDirection.SHORT)
        ]

        tot_dir = len(directional)
        correct_count = sum(1 for o in directional if o.outcome_class == OutcomeClassification.CORRECT)
        incorrect_count = sum(1 for o in directional if o.outcome_class == OutcomeClassification.INCORRECT)
        neutral_count = sum(1 for o in directional if o.outcome_class == OutcomeClassification.NEUTRAL_OUTCOME)

        evaluated_dir_decisions = correct_count + incorrect_count
        hit_rate = round((correct_count / evaluated_dir_decisions) * 100.0, 2) if evaluated_dir_decisions > 0 else None

        # Retornos Assinados
        signed_rets = [o.signed_return_pct for o in directional if o.signed_return_pct is not None]
        avg_signed = round(statistics.mean(signed_rets), 4) if signed_rets else 0.0
        med_signed = round(statistics.median(signed_rets), 4) if signed_rets else 0.0
        std_signed = round(statistics.stdev(signed_rets), 4) if len(signed_rets) > 1 else 0.0

        pos_rets = [r for r in signed_rets if r > 0]
        neg_rets = [r for r in signed_rets if r < 0]
        avg_pos = round(statistics.mean(pos_rets), 4) if pos_rets else 0.0
        avg_neg = round(statistics.mean(neg_rets), 4) if neg_rets else 0.0
        payoff = round(abs(avg_pos / avg_neg), 4) if avg_neg != 0 else None

        best_out = max(signed_rets) if signed_rets else None
        worst_out = min(signed_rets) if signed_rets else None

        # Concentração e Percentis
        sorted_rets = sorted(signed_rets, reverse=True)
        top1_ret = sorted_rets[0] if sorted_rets else 0.0
        top5_ret = sum(sorted_rets[:5]) if len(sorted_rets) >= 5 else sum(sorted_rets)
        top10pct_n = max(1, int(len(sorted_rets) * 0.10)) if sorted_rets else 0
        top10pct_ret = sum(sorted_rets[:top10pct_n]) if sorted_rets else 0.0

        p10 = round(statistics.quantiles(signed_rets, n=10)[0], 4) if len(signed_rets) >= 10 else None
        p25 = round(statistics.quantiles(signed_rets, n=4)[0], 4) if len(signed_rets) >= 4 else None
        p75 = round(statistics.quantiles(signed_rets, n=4)[2], 4) if len(signed_rets) >= 4 else None
        p90 = round(statistics.quantiles(signed_rets, n=10)[8], 4) if len(signed_rets) >= 10 else None

        # LONG vs SHORT separadamente
        long_outs = [o for o in directional if decisions_map[o.decision_id].consensus_direction == ConsensusDirection.LONG]
        short_outs = [o for o in directional if decisions_map[o.decision_id].consensus_direction == ConsensusDirection.SHORT]

        long_corr = sum(1 for o in long_outs if o.outcome_class == OutcomeClassification.CORRECT)
        long_tot = len(long_outs)
        long_hit = round((long_corr / long_tot) * 100.0, 2) if long_tot > 0 else None

        short_corr = sum(1 for o in short_outs if o.outcome_class == OutcomeClassification.CORRECT)
        short_tot = len(short_outs)
        short_hit = round((short_corr / short_tot) * 100.0, 2) if short_tot > 0 else None

        return EfficacyMetricSet(
            observation_count=len(evaluated),
            directional_count=tot_dir,
            correct_count=correct_count,
            incorrect_count=incorrect_count,
            neutral_outcome_count=neutral_count,
            hit_rate_pct=hit_rate,
            average_signed_return_pct=avg_signed,
            median_signed_return_pct=med_signed,
            return_std_pct=std_signed,
            payoff_ratio=payoff,
            average_positive_return_pct=avg_pos,
            average_negative_return_pct=avg_neg,
            best_outcome_pct=best_out,
            worst_outcome_pct=worst_out,
            top_1_outcome_pct=round(top1_ret, 4),
            top_5_sum_pct=round(top5_ret, 4),
            top_10_percent_sum_pct=round(top10pct_ret, 4),
            percentiles={
                "p10": p10,
                "p25": p25,
                "median": med_signed,
                "p75": p75,
                "p90": p90,
            },
            by_direction={
                "LONG": {
                    "count": long_tot,
                    "correct": long_corr,
                    "hit_rate_pct": long_hit,
                    "avg_signed_pct": round(statistics.mean([o.signed_return_pct for o in long_outs if o.signed_return_pct is not None]), 4) if long_outs else 0.0
                },
                "SHORT": {
                    "count": short_tot,
                    "correct": short_corr,
                    "hit_rate_pct": short_hit,
                    "avg_signed_pct": round(statistics.mean([o.signed_return_pct for o in short_outs if o.signed_return_pct is not None]), 4) if short_outs else 0.0
                }
            }
        )

    def calculate_horizon_summary(
        self,
        horizon_days: int,
        all_outcomes: Sequence[ForwardReturnOutcome],
        non_overlapping_outcomes: Sequence[ForwardReturnOutcome],
        decisions_map: dict[str, WalkForwardDecision],
        episodes: Sequence[ConsensusEpisode]
    ) -> HorizonEfficacySummary:
        """
        Gera o sumário estruturado HorizonEfficacySummary contendo ALL_OBSERVATIONS e NON_OVERLAPPING.
        """
        all_metrics = self.calculate_metric_set(all_outcomes, decisions_map)
        non_overlap_metrics = self.calculate_metric_set(non_overlapping_outcomes, decisions_map)

        return HorizonEfficacySummary(
            horizon_days=horizon_days,
            all_observation_count=all_metrics.observation_count,
            non_overlapping_observation_count=non_overlap_metrics.observation_count,
            episode_count=len(episodes),
            all_observations=all_metrics,
            non_overlapping=non_overlap_metrics
        )

    def calculate_efficacy_for_horizon(
        self,
        outcomes: Sequence[ForwardReturnOutcome],
        decisions_map: dict[str, WalkForwardDecision]
    ) -> dict[str, Any]:
        """
        Wrapper em formato dict compatível com visualizações analíticas.
        """
        return self.calculate_metric_set(outcomes, decisions_map).model_dump()

    def calculate_bucket_analysis(
        self,
        outcomes: Sequence[ForwardReturnOutcome],
        decisions_map: dict[str, WalkForwardDecision]
    ) -> dict[str, Any]:
        """
        Analisa a relação empírica entre força do consenso (margem, grupos, cobertura) e resultado futuro.
        """
        evaluated = [o for o in outcomes if o.evaluation_status == EvaluationStatus.EVALUATED]
        directional = [
            o for o in evaluated
            if decisions_map.get(o.decision_id) is not None and
            decisions_map[o.decision_id].consensus_direction in (ConsensusDirection.LONG, ConsensusDirection.SHORT)
        ]

        margin_buckets = {
            "20-30%": [o for o in directional if 0.20 <= abs(decisions_map[o.decision_id].consensus_margin) < 0.30],
            "30-40%": [o for o in directional if 0.30 <= abs(decisions_map[o.decision_id].consensus_margin) < 0.40],
            "40-50%": [o for o in directional if 0.40 <= abs(decisions_map[o.decision_id].consensus_margin) < 0.50],
            ">50%": [o for o in directional if abs(decisions_map[o.decision_id].consensus_margin) >= 0.50],
        }

        group_buckets = {
            "2_groups": [o for o in directional if decisions_map[o.decision_id].supporting_independent_group_count == 2],
            "3_groups": [o for o in directional if decisions_map[o.decision_id].supporting_independent_group_count == 3],
            "4+_groups": [o for o in directional if decisions_map[o.decision_id].supporting_independent_group_count >= 4],
        }

        coverage_buckets = {
            "50-60%": [o for o in directional if 0.50 <= decisions_map[o.decision_id].coverage_weight < 0.60],
            "60-75%": [o for o in directional if 0.60 <= decisions_map[o.decision_id].coverage_weight < 0.75],
            "75-90%": [o for o in directional if 0.75 <= decisions_map[o.decision_id].coverage_weight < 0.90],
            ">90%": [o for o in directional if decisions_map[o.decision_id].coverage_weight >= 0.90],
        }

        def summarize_bucket(bucket_outs: list[ForwardReturnOutcome]) -> dict[str, Any]:
            if not bucket_outs:
                return {"count": 0, "hit_rate_pct": None, "avg_signed_pct": None}
            corr = sum(1 for o in bucket_outs if o.outcome_class == OutcomeClassification.CORRECT)
            rets = [o.signed_return_pct for o in bucket_outs if o.signed_return_pct is not None]
            return {
                "count": len(bucket_outs),
                "hit_rate_pct": round((corr / len(bucket_outs)) * 100.0, 2),
                "avg_signed_pct": round(statistics.mean(rets), 4) if rets else 0.0
            }

        return {
            "by_consensus_margin": {k: summarize_bucket(v) for k, v in margin_buckets.items()},
            "by_independent_groups": {k: summarize_bucket(v) for k, v in group_buckets.items()},
            "by_coverage": {k: summarize_bucket(v) for k, v in coverage_buckets.items()}
        }

    def calculate_segment_metrics(
        self,
        outcomes_by_horizon: dict[int, list[ForwardReturnOutcome]],
        decisions_map: dict[str, WalkForwardDecision]
    ) -> dict[str, dict[str, Any]]:
        """
        Calcula métricas de eficácia para cada segmento/regime temporal configurado.
        """
        if not self.config.segments:
            return {}

        results: dict[str, dict[str, Any]] = {}
        for seg_name, (seg_start, seg_end) in self.config.segments.items():
            seg_start = self._normalize_utc(seg_start)
            seg_end = self._normalize_utc(seg_end)

            seg_dict: dict[str, Any] = {}
            for h, outs in outcomes_by_horizon.items():
                seg_outs = [
                    o for o in outs
                    if seg_start <= self._normalize_utc(o.decision_as_of) <= seg_end
                ]
                seg_dict[f"horizon_{h}d"] = self.calculate_metric_set(seg_outs, decisions_map).model_dump()

            results[seg_name] = seg_dict

        return results

    def build_data_quality_summary(
        self,
        journal: WalkForwardDecisionJournal,
        outcomes_by_horizon: dict[int, list[ForwardReturnOutcome]]
    ) -> dict[str, Any]:
        """
        Consolida estatísticas de integridade e qualidade dos dados.
        """
        missing_ref_count = 0
        missing_fut_count = 0
        stale_ref_count = 0

        for outs in outcomes_by_horizon.values():
            for o in outs:
                if o.evaluation_status == EvaluationStatus.MISSING_REFERENCE_PRICE:
                    missing_ref_count += 1
                elif o.evaluation_status == EvaluationStatus.MISSING_FORWARD_PRICE:
                    missing_fut_count += 1
                elif o.evaluation_status == EvaluationStatus.STALE_REFERENCE_PRICE:
                    stale_ref_count += 1

        empty_core_count = sum(1 for d in journal.decisions if d.selected_core_count == 0)

        return {
            "total_decisions_recorded": journal.total_decisions,
            "abstention_decisions_count": journal.neutral_decisions + journal.no_consensus_decisions + journal.insufficient_coverage_decisions,
            "empty_core_decisions_count": empty_core_count,
            "missing_reference_prices_count": missing_ref_count,
            "missing_future_prices_count": missing_fut_count,
            "stale_reference_prices_count": stale_ref_count,
            "data_quality_status": "EXCELLENT" if (missing_ref_count + missing_fut_count == 0) else "GAPS_DETECTED"
        }

    def generate_statistical_warnings(
        self,
        efficacy_by_horizon: dict[int, dict[str, Any]],
        episodes: Sequence[ConsensusEpisode]
    ) -> list[str]:
        """
        Gera avisos explícitos para amostras pequenas, dados esparsos e outcomes sobrepostos.
        """
        warnings: list[str] = []

        # Determina intervalo aproximado de decisão em dias
        freq = self.config.decision_frequency
        if freq == EvaluationFrequency.DAILY:
            interval_days = 1
        elif freq == EvaluationFrequency.WEEKLY:
            interval_days = 7
        elif freq == EvaluationFrequency.MONTHLY:
            interval_days = 30
        elif freq == EvaluationFrequency.QUARTERLY:
            interval_days = 90
        elif freq == EvaluationFrequency.YEARLY:
            interval_days = 365
        else:
            interval_days = 1

        for h, metrics in efficacy_by_horizon.items():
            dir_count = metrics.get("directional_decisions_count", 0)
            if dir_count < self.config.minimum_sample_for_reporting:
                warnings.append(
                    f"LOW_SAMPLE_WARNING: Apenas {dir_count} decisões direcionais no horizonte +{h}d (< mínimo de {self.config.minimum_sample_for_reporting})"
                )

            # Warning sobreposto quando forward_horizon > decision_interval
            if h > interval_days:
                warnings.append(
                    f"OVERLAPPING_OUTCOMES_WARNING: Horizonte de +{h}d excede o intervalo de decisão ({freq.value} = ~{interval_days}d). Avalie a visão NON_OVERLAPPING para medição descorrelacionada."
                )

        if len(episodes) < 3:
            warnings.append(f"LOW_EPISODE_SAMPLE_WARNING: Apenas {len(episodes)} episódios direcionais rastreados")

        return warnings
