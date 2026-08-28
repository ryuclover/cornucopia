import statistics
from typing import Optional, Sequence
from src.config.weight_config import WeightConfig
from src.evaluation.models import QualificationStatus, ScoreTrend, TraderEvaluationSnapshot
from src.selection.models import TraderSelectionHistory


class TraderQualityCalculator:
    """
    Calculador do Componente de Qualidade Individual (Quality Component) do Trader.
    
    Combina:
    1. SurvivorScore lifetime (âncora fundamental de sobrevivência e consistência)
    2. Taxa longitudinal de qualificação (Qualification Rate)
    3. Estabilidade temporal do score (baixa dispersão histórica)
    4. Saúde e dinâmica recente (janelas 30d, 90d, 180d e ScoreTrend), com renormalização sobre janelas disponíveis.
    """
    @classmethod
    def calculate_quality(
        cls,
        snapshot: TraderEvaluationSnapshot,
        config: WeightConfig,
        history: Optional[TraderSelectionHistory] = None,
        longitudinal_snapshots: Optional[Sequence[TraderEvaluationSnapshot]] = None
    ) -> tuple[float, dict[str, float]]:
        """
        Calcula o score de qualidade individual normalizado em [0.0, 1.0].
        Retorna (quality_score, subcomponents_dict).
        """
        # 1. Survivor Score Base [0, 1]
        survivor_score_val = max(0.0, min(100.0, snapshot.survivor_score))
        s_score = survivor_score_val / 100.0

        # 2. Qualification Rate [0, 1]
        if hasattr(snapshot, "qualification_rate") and snapshot.qualification_rate is not None:
            q_rate = max(0.0, min(1.0, snapshot.qualification_rate / 100.0 if snapshot.qualification_rate > 1.0 else snapshot.qualification_rate))
        elif longitudinal_snapshots and len(longitudinal_snapshots) > 0:
            qual_count = sum(1 for s in longitudinal_snapshots if s.qualification_status == QualificationStatus.QUALIFIED)
            q_rate = qual_count / len(longitudinal_snapshots)
        else:
            q_rate = 1.0 if snapshot.qualification_status == QualificationStatus.QUALIFIED else 0.5

        # 3. Estabilidade do Score (Longitudinal Stability) [0, 1]
        if longitudinal_snapshots and len(longitudinal_snapshots) >= 2:
            scores = [s.survivor_score for s in longitudinal_snapshots]
            mean_s = statistics.mean(scores)
            stdev_s = statistics.stdev(scores) if len(scores) > 1 else 0.0
            cv = stdev_s / max(mean_s, 1.0)
            score_stability = max(0.0, min(1.0, 1.0 - cv))
        else:
            score_stability = 1.0 # Neutro se histórico único

        # 4. Saúde Recente (Recent Health) com Renormalização sobre Janelas Válidas
        recent_components: list[tuple[float, float]] = [] # (peso, valor [0, 1])

        sc_30 = getattr(snapshot, "score_30d", None) or getattr(snapshot, "survivor_score_window_30d", None)
        sc_90 = getattr(snapshot, "score_90d", None) or getattr(snapshot, "survivor_score_window_90d", None)
        sc_180 = getattr(snapshot, "score_180d", None) or getattr(snapshot, "survivor_score_window_180d", None)

        if sc_30 is not None:
            recent_components.append((0.40, sc_30 / 100.0))
        if sc_90 is not None:
            recent_components.append((0.35, sc_90 / 100.0))
        if sc_180 is not None:
            recent_components.append((0.25, sc_180 / 100.0))

        if recent_components:
            tot_w = sum(w for w, _ in recent_components)
            raw_recent = sum(w * val for w, val in recent_components) / tot_w
        else:
            # Se nenhuma janela recente tiver amostra suficiente, usa o lifetime
            raw_recent = s_score

        # Modulação por ScoreTrend
        trend_factor = 1.0
        trend = getattr(snapshot, "score_trend", None)
        if trend == ScoreTrend.IMPROVING:
            trend_factor = 1.05
        elif trend == ScoreTrend.DETERIORATING:
            trend_factor = 0.85

        recent_health = max(0.0, min(1.0, raw_recent * trend_factor))

        # 5. Combinação Ponderada Final
        quality_score = (
            config.quality_weight_survivor_score * s_score +
            config.quality_weight_qualification_rate * q_rate +
            config.quality_weight_score_stability * score_stability +
            config.quality_weight_recent_health * recent_health
        )

        quality_clamped = round(max(0.0, min(1.0, quality_score)), 4)

        subcomponents = {
            "survivor_score_norm": round(s_score, 4),
            "qualification_rate": round(q_rate, 4),
            "score_stability": round(score_stability, 4),
            "recent_health": round(recent_health, 4),
            "trend_factor": trend_factor,
        }

        return quality_clamped, subcomponents
