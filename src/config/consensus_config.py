from enum import Enum
from pydantic import BaseModel, Field


class ConsensusPreset(str, Enum):
    """Presets predefinidos de política de consenso."""
    BALANCED = "BALANCED"
    CONSERVATIVE = "CONSERVATIVE"
    HIGH_CONSENSUS = "HIGH_CONSENSUS"
    EXPLORATORY = "EXPLORATORY"


class ConsensusConfig(BaseModel):
    """
    Configurações e thresholds centralizados para o motor de consenso ponderado por instrumento.
    """
    # 1. Cobertura e Participação Mínima
    minimum_coverage_weight: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Fração mínima de autoridade ponderada do Core (LONG+SHORT+FLAT) necessária para evitar INSUFFICIENT_COVERAGE"
    )
    minimum_directional_weight: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Fração mínima de autoridade ponderada direcional (LONG+SHORT) para gerar consenso LONG ou SHORT"
    )

    # 2. Concordância Direcional e Suporte do Core
    minimum_directional_agreement: float = Field(
        default=0.70,
        ge=0.50,
        le=1.0,
        description="Concordância mínima entre participantes direcionais (LONG / (LONG+SHORT) ou vice-versa)"
    )
    minimum_core_support: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="Participação ponderada mínima do lado vencedor em relação ao total do Core (1.0)"
    )
    minimum_consensus_margin: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Diferença mínima exigida entre o peso vencedor e o peso oposto (w_venc - w_oposto)"
    )
    maximum_opposition_weight: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description="Teto máximo permitido de peso para a direção oposta (ex: para LONG, SHORT não pode exceder 25%)"
    )

    # 3. Requisitos de Independência e Contagem
    minimum_supporting_traders: int = Field(
        default=2,
        ge=1,
        description="Quantidade mínima de traders individuais apoiando a mesma direção"
    )
    minimum_supporting_independent_groups: int = Field(
        default=2,
        ge=1,
        description="Quantidade mínima de Redundancy Groups independentes apoiando a mesma direção"
    )
    minimum_independent_group_support_weight: float = Field(
        default=0.03,
        ge=0.0,
        le=1.0,
        description="Peso líquido mínimo que um grupo deve representar no Core para contar como confirmação independente"
    )
    minimum_group_directional_agreement: float = Field(
        default=0.70,
        ge=0.50,
        le=1.0,
        description="Concordância direcional interna mínima do grupo para assumir LONG ou SHORT"
    )
    minimum_group_directional_margin: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Margem líquida direcional interna mínima do grupo (long_w - short_w ou vice-versa)"
    )

    # 4. Parâmetros de Sinal
    flat_activity_lookback_days: int = Field(
        default=30,
        ge=1,
        description="Lookback em dias para inferir FLAT versus NO_OPINION"
    )

    @classmethod
    def from_preset(cls, preset: ConsensusPreset) -> "ConsensusConfig":
        """Gera instâncias pré-configuradas baseadas em presets operacionais."""
        if preset == ConsensusPreset.CONSERVATIVE:
            return cls(
                minimum_coverage_weight=0.60,
                minimum_directional_weight=0.40,
                minimum_directional_agreement=0.80,
                minimum_core_support=0.40,
                minimum_consensus_margin=0.25,
                minimum_supporting_traders=2,
                minimum_supporting_independent_groups=2,
                minimum_independent_group_support_weight=0.05,
                minimum_group_directional_agreement=0.80,
                maximum_opposition_weight=0.20
            )
        elif preset == ConsensusPreset.HIGH_CONSENSUS:
            return cls(
                minimum_coverage_weight=0.70,
                minimum_directional_weight=0.50,
                minimum_directional_agreement=0.85,
                minimum_core_support=0.45,
                minimum_consensus_margin=0.30,
                minimum_supporting_traders=3,
                minimum_supporting_independent_groups=3,
                minimum_independent_group_support_weight=0.05,
                minimum_group_directional_agreement=0.85,
                maximum_opposition_weight=0.15
            )
        elif preset == ConsensusPreset.EXPLORATORY:
            return cls(
                minimum_coverage_weight=0.35,
                minimum_directional_weight=0.20,
                minimum_directional_agreement=0.60,
                minimum_core_support=0.20,
                minimum_consensus_margin=0.10,
                minimum_supporting_traders=1,
                minimum_supporting_independent_groups=1,
                minimum_independent_group_support_weight=0.01,
                minimum_group_directional_agreement=0.60,
                maximum_opposition_weight=0.35
            )
        return cls()

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
