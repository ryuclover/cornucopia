from datetime import datetime, timezone
from typing import Optional, Sequence
from src.config.weight_config import WeightConfig
from src.evaluation.models import TraderEvaluationSnapshot


class TraderConfidenceCalculator:
    """
    Calculador do Componente de Confiança Estatística da Evidência (Evidence Confidence Component).
    
    Princípio:
    A confiança quantifica a solidez e profundidade amostral das evidências históricas de um trader
    (quantidade de trades, maturidade temporal em dias e regularidade longitudinal).
    
    Regra Importante:
    A confiança NÃO transforma um trader ruim em bom — ela apenas modula a certeza do peso atribuído,
    evitando que traders recém-qualificados com amostra mínima recebam alocações desproporcionais.
    """
    @classmethod
    def calculate_confidence(
        cls,
        snapshot: TraderEvaluationSnapshot,
        as_of: datetime,
        config: WeightConfig,
        created_at: Optional[datetime] = None,
        longitudinal_snapshots: Optional[Sequence[TraderEvaluationSnapshot]] = None
    ) -> tuple[float, dict[str, float]]:
        """
        Calcula o fator de confiança amostral em [minimum_confidence_factor, 1.0].
        Retorna (confidence_factor, subcomponents_dict).
        """
        # 1. Confiança de Execuções (Trades Count) [0, 1]
        trade_count = getattr(snapshot, "trade_count", 0) or getattr(snapshot, "total_closed_trades", 0)
        if trade_count == 0 and hasattr(snapshot, "metrics_summary") and isinstance(snapshot.metrics_summary, dict):
            trade_count = snapshot.metrics_summary.get("total_closed_trades", 0)

        c_trades = min(1.0, trade_count / float(config.confidence_target_trades))

        # 2. Confiança de Histórico Temporal (Days Active) [0, 1]
        if created_at:
            if as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=timezone.utc)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            history_days = max(1, (as_of - created_at).days)
        elif hasattr(snapshot, "history_days") and snapshot.history_days is not None:
            history_days = max(1.0, snapshot.history_days)
        elif hasattr(snapshot, "metrics_summary") and isinstance(snapshot.metrics_summary, dict):
            history_days = snapshot.metrics_summary.get("history_days", 90)
        else:
            history_days = 90

        c_days = min(1.0, history_days / float(config.confidence_target_days))

        # 3. Confiança de Regularidade Longitudinal [0, 1]
        if longitudinal_snapshots is not None:
            eval_count = len(longitudinal_snapshots)
            c_evals = min(1.0, eval_count / 6.0)
        else:
            eval_count = 1
            c_evals = 1.0

        # 4. Combinação Ponderada Bruta
        raw_confidence = (0.50 * c_trades) + (0.35 * c_days) + (0.15 * c_evals)

        # 5. Aplicação do Piso Mínimo (Floor) e Saturação (Clamping)
        final_confidence = max(config.minimum_confidence_factor, min(1.0, raw_confidence))
        final_clamped = round(final_confidence, 4)

        subcomponents = {
            "trade_count": float(trade_count),
            "trade_confidence": round(c_trades, 4),
            "history_days": float(history_days),
            "history_confidence": round(c_days, 4),
            "eval_count": float(eval_count),
            "eval_confidence": round(c_evals, 4),
            "raw_confidence": round(raw_confidence, 4),
            "minimum_confidence_floor": config.minimum_confidence_factor,
        }

        return final_clamped, subcomponents
