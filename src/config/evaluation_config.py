from enum import Enum
from pydantic import BaseModel, Field


class EvaluationFrequency(str, Enum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class EvaluationConfig(BaseModel):
    """
    Configurações centralizadas para avaliação longitudinal e ranking histórico.
    """
    frequency: EvaluationFrequency = Field(
        default=EvaluationFrequency.MONTHLY,
        description="Frequência padrão de amostragem na avaliação de séries temporais"
    )
    recent_window_days: int = Field(
        default=30,
        ge=5,
        description="Janela de curto prazo para avaliação recente de momentum/degradação (dias)"
    )
    min_trades_30d: int = Field(
        default=5,
        ge=1,
        description="Número mínimo de trades fechados na janela de 30 dias para considerar a amostra suficiente"
    )
    medium_window_days: int = Field(
        default=90,
        ge=15,
        description="Janela de médio prazo (dias)"
    )
    min_trades_90d: int = Field(
        default=15,
        ge=1,
        description="Número mínimo de trades fechados na janela de 90 dias para considerar a amostra suficiente"
    )
    long_window_days: int = Field(
        default=180,
        ge=30,
        description="Janela de longo prazo (dias)"
    )
    min_trades_180d: int = Field(
        default=30,
        ge=1,
        description="Número mínimo de trades fechados na janela de 180 dias para considerar a amostra suficiente"
    )
    trend_window_periods: int = Field(
        default=5,
        ge=3,
        description="Número de períodos recentes utilizados para cálculo de tendência de score"
    )
    drawdown_warning_threshold_pct: float = Field(
        default=15.0,
        ge=1.0,
        le=100.0,
        description="Threshold percentual de drawdown que gera alerta de violação temporária"
    )
    deterioration_slope_threshold: float = Field(
        default=-0.5,
        description="Inclinação da reta de regressão do score abaixo da qual é classificado como 'DETERIORATING'"
    )
    improving_slope_threshold: float = Field(
        default=0.5,
        description="Inclinação da reta de regressão do score acima da qual é classificado como 'IMPROVING'"
    )

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
