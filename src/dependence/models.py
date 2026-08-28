from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class DependenceLevel(str, Enum):
    """
    Níveis categóricos de dependência/redundância entre pares de traders.
    """
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # Amostra sobreposta insuficiente para inferência estatística
    LOW = "LOW"                              # Baixa similaridade (potencial de diversificação elevado)
    MODERATE = "MODERATE"                    # Similaridade moderada
    HIGH = "HIGH"                            # Alta redundância (comportamento substancialmente repetitivo)
    VERY_HIGH = "VERY_HIGH"                  # Redundância extrema (praticamente a mesma estratégia/sinal)


class TraderTimeSeriesFrame(BaseModel):
    """
    Representação temporal normalizada de um trader em um bucket discreto (ex: diário).
    """
    timestamp: datetime = Field(..., description="Data/hora limite do bucket temporal normalizado")
    net_return: float = Field(default=0.0, description="Retorno percentual ou relativo gerado no período (%)")
    net_pnl: Decimal = Field(default=Decimal("0.0"), description="P&L líquido realizado no período")
    gross_exposure: Decimal = Field(default=Decimal("0.0"), description="Exposição financeira bruta média/final no período")
    position_directions: dict[str, float] = Field(
        default_factory=dict,
        description="Direção normalizada por símbolo: +1.0 (Long), -1.0 (Short), 0.0 (Flat)"
    )
    is_active: bool = Field(default=False, description="Indica se o trader teve posições abertas ou execuções no período")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class TraderPairDependence(BaseModel):
    """
    Resultado detalhado e auditável da análise de dependência e redundância entre dois traders.
    """
    trader_a_id: str = Field(..., description="ID do primeiro trader")
    trader_b_id: str = Field(..., description="ID do segundo trader")
    as_of: datetime = Field(..., description="Timestamp UTC limite da análise ponto-no-tempo")
    analysis_start: Optional[datetime] = Field(default=None, description="Timestamp UTC inicial da janela de análise")
    
    overlap_periods: int = Field(..., ge=0, description="Quantidade de períodos temporais alinhados observados")
    overlap_trades_a: int = Field(default=0, ge=0, description="Trades do trader A na janela de análise")
    overlap_trades_b: int = Field(default=0, ge=0, description="Trades do trader B na janela de análise")
    sample_status: str = Field(..., description="'SUFFICIENT' ou 'INSUFFICIENT_DATA'")
    correlation_status: str = Field(
        default="VALID",
        description="Status específico da correlação: 'VALID', 'UNDEFINED_ZERO_VARIANCE' ou 'INSUFFICIENT_DATA'"
    )
    
    # Métricas Quantitativas de Dependência
    return_correlation: Optional[float] = Field(
        default=None,
        description="Correlação linear de Pearson dos retornos diários alinhados (em [-1.0, 1.0]), None se indefinida por variância zero"
    )
    directional_agreement: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="% de períodos ativos com mesma direção líquida (excluindo períodos de flat conjunto)"
    )
    position_overlap: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="% de concordância direcional ponderada por ativo específico negociado"
    )
    instrument_overlap: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Índice de similaridade de Jaccard do universo de ativos operados (%)"
    )
    timing_similarity: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Proximidade temporal média das ordens e entradas dos traders (%)"
    )
    
    # Redundância Combinada
    composite_redundancy_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Score ponderado de redundância composto (0.0 = totalmente independente, 100.0 = idêntico)"
    )
    dependence_level: DependenceLevel = Field(..., description="Classificação categórica da dependência")

    @property
    def overlap_trades(self) -> int:
        """Menor quantidade de trades entre os dois traders na janela para critério amostral conservador."""
        return min(self.overlap_trades_a, self.overlap_trades_b)

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class DependenceMatrix(BaseModel):
    """
    Matriz simétrica N x N de redundância entre um conjunto de traders em 'as_of'.
    """
    as_of: datetime = Field(..., description="Timestamp UTC da matriz")
    trader_ids: list[str] = Field(..., description="Lista ordenada dos IDs dos traders")
    matrix: list[list[Optional[float]]] = Field(
        ...,
        description="Matriz N x N com os Composite Redundancy Scores (diagonal = 100.0, None se amostra insuficiente)"
    )
    pairwise_map: dict[str, TraderPairDependence] = Field(
        default_factory=dict,
        description="Mapa com chave 'traderA:traderB' para acesso rápido ao par"
    )

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class RedundancyGroup(BaseModel):
    """
    Bloco de traders altamente redundantes agrupados via componentes conexos.
    """
    group_id: int = Field(..., ge=1, description="Identificador numérico sequencial do grupo")
    member_trader_ids: list[str] = Field(..., description="Lista dos IDs dos traders membros do bloco")
    lead_trader_id: str = Field(..., description="Trader de referência do grupo (ex: maior survivor score ou antiguidade)")
    average_intra_group_redundancy: float = Field(default=0.0, description="Média de redundância entre os membros do grupo")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class CoreDependenceSnapshot(BaseModel):
    """
    Fotografia consolidada da dependência e diversidade de estratégias no núcleo em 'as_of'.
    """
    as_of: datetime = Field(..., description="Timestamp UTC do snapshot")
    selected_traders: list[str] = Field(default_factory=list, description="IDs dos traders analisados")
    selected_trader_ids: list[str] = Field(default_factory=list, description="IDs dos traders analisados (alias)")
    dependence_matrix: DependenceMatrix = Field(..., description="Matriz de redundância dos traders")
    pairwise_dependencies: list[TraderPairDependence] = Field(
        default_factory=list,
        description="Lista de todas as análises pairwise calculadas"
    )
    redundancy_groups: list[RedundancyGroup] = Field(default_factory=list, description="Grupos de redundância identificados")
    effective_independent_groups_count: int = Field(..., ge=0, description="Quantidade de blocos de opinião independentes")
    
    # Estatísticas de Redundância do Núcleo
    average_redundancy: float = Field(default=0.0, description="Média de redundância entre todos os pares válidos")
    median_redundancy: float = Field(default=0.0, description="Mediana de redundância entre os pares válidos")
    maximum_redundancy: float = Field(default=0.0, description="Maior redundância observada entre dois traders distintos")
    minimum_redundancy: float = Field(default=0.0, description="Menor redundância observada entre dois traders distintos")
    
    highly_redundant_pairs: list[tuple[str, str, float]] = Field(
        default_factory=list,
        description="Lista de tuplas (trader_a, trader_b, score) para pares com redundância >= threshold"
    )
    independent_pair_count: int = Field(
        default=0,
        ge=0,
        description="Quantidade de pares com dependência classificada como LOW"
    )

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
