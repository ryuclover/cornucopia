from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class WeightingPreset(str, Enum):
    """Presets predefinidos de política de ponderação."""
    BALANCED = "BALANCED"
    CONSERVATIVE = "CONSERVATIVE"
    DIVERSIFICATION_FOCUSED = "DIVERSIFICATION_FOCUSED"
    QUALITY_FOCUSED = "QUALITY_FOCUSED"


class WeightConfig(BaseModel):
    """
    Configurações centralizadas e parametrizáveis para o motor de pesos dos traders selecionados.
    """
    # 1. Pesos Macro dos 3 Pilares Principais (devem somar 1.0)
    quality_weight: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Peso do componente de Qualidade Individual no cálculo do peso bruto"
    )
    independence_weight: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="Peso do componente de Independência e Diluição de Grupo"
    )
    confidence_weight: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Peso do componente de Confiança Estatística na Evidência"
    )

    # 2. Pesos Internos do Componente de Qualidade (somam 1.0)
    quality_weight_survivor_score: float = Field(
        default=0.60,
        ge=0.0,
        le=1.0,
        description="Peso do Survivor Score lifetime no componente de Qualidade"
    )
    quality_weight_qualification_rate: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Peso da taxa de qualificação longitudinal do trader"
    )
    quality_weight_score_stability: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Peso da estabilidade temporal do score (baixa volatilidade longitudinal)"
    )
    quality_weight_recent_health: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Peso da saúde e consistência em janelas recentes (30d, 90d, 180d, trend)"
    )

    # 3. Parâmetros de Independência e Diluição de Grupo
    redundancy_penalty_strength: float = Field(
        default=1.0,
        ge=0.0,
        le=2.0,
        description="Intensidade da penalização de redundância intra-grupo (1.0 = diluição linear padrão)"
    )
    insufficient_dependence_penalty: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Penalidade conservadora aplicada ao fator de independência quando não há amostra suficiente"
    )

    # 4. Parâmetros de Confiança Amostral (Evidence Confidence)
    confidence_target_trades: int = Field(
        default=100,
        ge=10,
        description="Meta de trades fechados para saturação máxima (1.0) da confiança de execução"
    )
    confidence_target_days: int = Field(
        default=180,
        ge=15,
        description="Meta de dias de histórico ativo para saturação máxima da confiança temporal"
    )
    confidence_target_overlap_periods: int = Field(
        default=60,
        ge=5,
        description="Meta de períodos alinhados de dependência para saturação de confiança relacional"
    )
    minimum_confidence_factor: float = Field(
        default=0.20,
        ge=0.01,
        le=1.0,
        description="Piso mínimo do fator de confiança para evitar anulação total de novos traders selecionados"
    )

    # 5. Restrições e Limites (Caps e Floors)
    maximum_trader_weight: float = Field(
        default=0.25,
        ge=0.01,
        le=1.0,
        description="Teto percentual máximo permitido para qualquer trader individual (ex: 0.25 = 25%)"
    )
    minimum_trader_weight: float = Field(
        default=0.0,
        ge=0.0,
        le=0.20,
        description="Piso mínimo de peso individual (opcional)"
    )
    prune_below_minimum_weight: bool = Field(
        default=False,
        description="Se True, traders com peso abaixo do piso mínimo são zerados e o peso redistribuído"
    )
    maximum_group_weight: float = Field(
        default=0.40,
        ge=0.05,
        le=1.0,
        description="Teto percentual máximo permitido para o conjunto de membros de um mesmo Redundancy Group"
    )
    max_weight_change_per_period: Optional[float] = Field(
        default=None,
        ge=0.01,
        le=1.0,
        description="Variação máxima de peso permitida entre snapshots consecutivos (smoothing opcional)"
    )

    @classmethod
    def from_preset(cls, preset: WeightingPreset) -> "WeightConfig":
        """Gera instâncias pré-configuradas com base em presets consagrados."""
        if preset == WeightingPreset.CONSERVATIVE:
            return cls(
                quality_weight=0.60,
                independence_weight=0.25,
                confidence_weight=0.15,
                maximum_trader_weight=0.20,
                maximum_group_weight=0.35,
                minimum_confidence_factor=0.30
            )
        elif preset == WeightingPreset.DIVERSIFICATION_FOCUSED:
            return cls(
                quality_weight=0.40,
                independence_weight=0.45,
                confidence_weight=0.15,
                maximum_trader_weight=0.20,
                maximum_group_weight=0.30,
                redundancy_penalty_strength=1.2
            )
        elif preset == WeightingPreset.QUALITY_FOCUSED:
            return cls(
                quality_weight=0.70,
                independence_weight=0.20,
                confidence_weight=0.10,
                maximum_trader_weight=0.35,
                maximum_group_weight=0.50
            )
        return cls()

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
