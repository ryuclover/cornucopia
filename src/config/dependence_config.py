from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from src.config.evaluation_config import EvaluationFrequency


class DependenceConfig(BaseModel):
    """
    Configurações centralizadas para análise de dependência, similaridade e correlação entre traders.
    """
    # Janela e Amostragem Temporal
    analysis_window_days: int = Field(
        default=90,
        ge=15,
        description="Janela retrospectiva em dias a partir de 'as_of' para análise de dependência"
    )
    alignment_frequency: EvaluationFrequency = Field(
        default=EvaluationFrequency.DAILY,
        description="Frequência de alinhamento temporal para sincronização dos retornos e posições"
    )
    minimum_overlap_periods: int = Field(
        default=15,
        ge=3,
        description="Quantidade mínima de períodos alinhados necessários para considerar a amostra estatisticamente válida"
    )
    minimum_overlap_trades: int = Field(
        default=5,
        ge=1,
        description="Mínimo de trades fechados observados na janela para validação amostral"
    )
    timing_tolerance_hours: float = Field(
        default=24.0,
        ge=0.1,
        description="Janela de tolerância temporal em horas para considerar execuções como sincronizadas (timing similarity)"
    )

    # Thresholds de Classificação e Agrupamento
    very_high_redundancy_threshold: float = Field(
        default=80.0,
        ge=0.0,
        le=100.0,
        description="Threshold de score acima do qual a dependência é classificada como VERY_HIGH"
    )
    high_redundancy_threshold: float = Field(
        default=65.0,
        ge=0.0,
        le=100.0,
        description="Threshold de score acima do qual a dependência é classificada como HIGH"
    )
    moderate_redundancy_threshold: float = Field(
        default=40.0,
        ge=0.0,
        le=100.0,
        description="Threshold de score acima do qual a dependência é classificada como MODERATE"
    )
    grouping_redundancy_threshold: float = Field(
        default=65.0,
        ge=0.0,
        le=100.0,
        description="Limiar de corte para conexão em grafo e formação de Redundancy Groups"
    )
    minimum_internal_group_redundancy: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Limiar mínimo de redundância exigido entre TODOS os membros de um grupo (complete-linkage). Se None, utiliza grouping_redundancy_threshold."
    )

    # Pesos do Composite Redundancy Score (Soma deve normalizar para 1.0)
    weight_return_correlation: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Peso da correlação linear de retornos diários no score composto"
    )
    weight_directional_agreement: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description="Peso da concordância direcional líquida no score composto"
    )
    weight_position_overlap: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Peso da sobreposição posicional por instrumento no score composto"
    )
    weight_instrument_overlap: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Peso do índice de Jaccard do universo de ativos negociados no score composto"
    )
    weight_timing_similarity: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Peso da proximidade de timing das ordens no score composto"
    )

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
