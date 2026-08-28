from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class ConsensusDirection(str, Enum):
    """
    Direções discretas de consenso coletivo ponderado de um instrumento financeiro em 'as_of'.
    """
    LONG = "LONG"                            # Consenso robusto para exposição comprada
    SHORT = "SHORT"                          # Consenso robusto para exposição vendida
    NEUTRAL = "NEUTRAL"                      # Cobertura satisfatória com núcleo predominantemente zerado (FLAT)
    NO_CONSENSUS = "NO_CONSENSUS"            # Disputa direcional relevante sem margem ou grupos independentes suficientes
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"  # Pouca autoridade do núcleo possui opinião válida sobre o ativo
    UNKNOWN = "UNKNOWN"                      # Estado não pôde ser avaliado com consistência


class GroupDirectionalState(str, Enum):
    """
    Classificação direcional consolidada interna de um Redundancy Group em relação a um instrumento.
    """
    LONG = "LONG"              # Apoio direcional interno material e coeso para LONG
    SHORT = "SHORT"            # Apoio direcional interno material e coeso para SHORT
    NEUTRAL = "NEUTRAL"        # Grupo majoritariamente zerado (FLAT)
    CONFLICT = "CONFLICT"      # Conflito direcional interno entre membros do grupo
    NO_OPINION = "NO_OPINION"  # Grupo sem opinião/sem atividade no instrumento
    UNKNOWN = "UNKNOWN"        # Dados insuficientes ou desconhecidos no grupo


class InstrumentConsensusSnapshot(BaseModel):
    """
    Fotografia auditável e imutável do consenso coletivo para um instrumento específico em 'as_of'.
    """
    symbol: str = Field(..., description="Símbolo do instrumento financeiro")
    as_of: datetime = Field(..., description="Timestamp UTC limite de referência do consenso")
    consensus_direction: ConsensusDirection = Field(..., description="Direção de consenso apurada")
    
    # Distribuição Ponderada das Opiniões (Soma = 1.0)
    long_weight: float = Field(default=0.0, ge=0.0, le=1.0, description="Peso ponderado dos especialistas que estão LONG")
    short_weight: float = Field(default=0.0, ge=0.0, le=1.0, description="Peso ponderado dos especialistas que estão SHORT")
    flat_weight: float = Field(default=0.0, ge=0.0, le=1.0, description="Peso ponderado dos especialistas que estão FLAT")
    no_opinion_weight: float = Field(default=0.0, ge=0.0, le=1.0, description="Peso dos especialistas sem opinião no ativo (NO_OPINION)")
    unknown_weight: float = Field(default=0.0, ge=0.0, le=1.0, description="Peso dos especialistas com estado desconhecido (UNKNOWN)")
    
    # Métricas Agregadas de Participação e Margem
    coverage_weight: float = Field(default=0.0, ge=0.0, le=1.0, description="Fração de autoridade do Core com opinião (LONG+SHORT+FLAT)")
    directional_weight: float = Field(default=0.0, ge=0.0, le=1.0, description="Fração de autoridade do Core posicionada direcionalmente (LONG+SHORT)")
    directional_agreement_long: float = Field(default=0.0, ge=0.0, le=1.0, description="Concordância direcional pró-LONG: LONG / (LONG+SHORT)")
    directional_agreement_short: float = Field(default=0.0, ge=0.0, le=1.0, description="Concordância direcional pró-SHORT: SHORT / (LONG+SHORT)")
    consensus_margin: float = Field(default=0.0, description="Margem de liderança ponderada: (long_weight - short_weight)")
    
    # Auditoria de Participantes e Grupos Independentes
    long_supporting_traders: list[str] = Field(default_factory=list, description="IDs dos traders posicionados em LONG")
    short_supporting_traders: list[str] = Field(default_factory=list, description="IDs dos traders posicionados em SHORT")
    flat_traders: list[str] = Field(default_factory=list, description="IDs dos traders acompanhando mas zerados (FLAT)")
    no_opinion_traders: list[str] = Field(default_factory=list, description="IDs dos traders sem opinião no ativo")
    
    long_supporting_groups: list[int] = Field(default_factory=list, description="IDs dos Redundancy Groups independentes apoiando LONG")
    short_supporting_groups: list[int] = Field(default_factory=list, description="IDs dos Redundancy Groups independentes apoiando SHORT")
    long_supporting_group_count: int = Field(default=0, ge=0, description="Quantidade de grupos independentes apoiando LONG")
    short_supporting_group_count: int = Field(default=0, ge=0, description="Quantidade de grupos independentes apoiando SHORT")
    
    group_support_breakdown: dict[str, Any] = Field(default_factory=dict, description="Detalhamento do suporte direcional por Redundancy Group")
    group_direction_breakdown: dict[str, Any] = Field(default_factory=dict, description="Classificação direcional e métricas internas de cada Redundancy Group")
    reasons: list[str] = Field(default_factory=list, description="Explicação detalhada dos fatores que fundamentaram a decisão")
    triggered_rules: list[str] = Field(default_factory=list, description="Regras e critérios acionados na determinação do consenso")
    diagnostics: dict[str, Any] = Field(default_factory=dict, description="Metadados internos para depuração e diagnóstico")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class CoreConsensusSnapshot(BaseModel):
    """
    Fotografia agregada do consenso coletivo para todo o universo ativo de instrumentos em 'as_of'.
    """
    as_of: datetime = Field(..., description="Timestamp UTC do snapshot")
    weight_snapshot_as_of: datetime = Field(..., description="Timestamp UTC do CoreWeightSnapshot de origem")
    instruments: list[str] = Field(default_factory=list, description="Lista de todos os símbolos analisados")
    consensus_by_instrument: dict[str, InstrumentConsensusSnapshot] = Field(default_factory=dict, description="Mapeamento símbolo -> InstrumentConsensusSnapshot")
    
    long_consensus_count: int = Field(default=0, ge=0, description="Total de instrumentos com consenso LONG")
    short_consensus_count: int = Field(default=0, ge=0, description="Total de instrumentos com consenso SHORT")
    neutral_count: int = Field(default=0, ge=0, description="Total de instrumentos no estado NEUTRAL")
    no_consensus_count: int = Field(default=0, ge=0, description="Total de instrumentos no estado NO_CONSENSUS")
    insufficient_coverage_count: int = Field(default=0, ge=0, description="Total de instrumentos no estado INSUFFICIENT_COVERAGE")
    total_instruments_analyzed: int = Field(default=0, ge=0, description="Total de instrumentos avaliados no snapshot")
    
    diagnostics: dict[str, Any] = Field(default_factory=dict, description="Metadados globais da execução")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class ConsensusTurnoverMetric(BaseModel):
    """
    Registro da estabilidade e rotação direcional de consenso entre dois snapshots temporais.
    """
    from_as_of: datetime = Field(..., description="Timestamp inicial do período")
    to_as_of: datetime = Field(..., description="Timestamp final do período")
    
    direction_changes_count: int = Field(default=0, ge=0, description="Total de mudanças de estado de consenso")
    flips_count: int = Field(default=0, ge=0, description="Total de reversões diretas severas (LONG <-> SHORT)")
    changes_by_instrument: dict[str, tuple[ConsensusDirection, ConsensusDirection]] = Field(
        default_factory=dict,
        description="Mapeamento symbol -> (direção_anterior, nova_direção)"
    )

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
