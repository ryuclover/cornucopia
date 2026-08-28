from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class InfeasibleWeightConstraintsError(ValueError):
    """
    Exceção levantada quando uma combinação de restrições de peso (caps individuais, caps de grupo,
    floors ou pruning) torna matematicamente inviável satisfazer a soma exata de 1.0 (100%).
    """
    def __init__(
        self,
        message: str,
        required_total_weight: float = 1.0,
        maximum_possible_weight: Optional[float] = None,
        minimum_possible_weight: Optional[float] = None,
        constraint_cause: Optional[str] = None,
        details: Optional[dict[str, Any]] = None
    ):
        super().__init__(message)
        self.required_total_weight = required_total_weight
        self.maximum_possible_weight = maximum_possible_weight
        self.minimum_possible_weight = minimum_possible_weight
        self.constraint_cause = constraint_cause
        self.details = details or {}


class TraderWeight(BaseModel):
    """
    Registro individual, imutável e auditável do peso relativo atribuído a um trader no instante 'as_of'.
    """
    trader_id: str = Field(..., description="ID do trader selecionado")
    as_of: datetime = Field(..., description="Timestamp UTC de referência do cálculo")
    
    # Métricas Base de Origem
    survivor_score: float = Field(..., ge=0.0, le=100.0, description="Survivor Score lifetime na data")
    redundancy_group_id: Optional[int] = Field(default=None, description="ID do Redundancy Group ao qual pertence")
    sample_status: str = Field(default="SUFFICIENT", description="'SUFFICIENT' ou 'INSUFFICIENT_DATA'")
    
    # 3 Pilares Principais (normalizados em [0.0, 1.0])
    quality_component: float = Field(..., ge=0.0, le=1.0, description="Score composto de qualidade individual (0 a 1)")
    independence_component: float = Field(..., ge=0.0, le=1.0, description="Fator de independência e diluição de grupo (0 a 1)")
    confidence_component: float = Field(..., ge=0.0, le=1.0, description="Fator de confiança estatística da evidência (0 a 1)")
    
    # Pesos Bruto e Normalizado
    raw_weight: float = Field(..., ge=0.0, description="Peso bruto preliminar (Quality x Independence x Confidence)")
    normalized_weight: float = Field(..., ge=0.0, le=1.0, description="Peso final normalizado do trader no núcleo (soma do núcleo = 1.0)")
    weight_pct: float = Field(..., ge=0.0, le=100.0, description="Peso percentual no portfólio coletivo (%)")
    
    # Auditoria e Diagnósticos
    caps_applied: list[str] = Field(default_factory=list, description="Lista de restrições acionadas (ex: INDIVIDUAL_CAP, GROUP_CAP)")
    reasons: list[str] = Field(default_factory=list, description="Explicação detalhada dos fatores que geraram o peso final")
    diagnostics: dict[str, Any] = Field(default_factory=dict, description="Métricas internas detalhadas do cálculo")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class GroupWeightSummary(BaseModel):
    """
    Sumário da alocação de peso agregada por Redundancy Group no núcleo.
    """
    group_id: int = Field(..., ge=1, description="ID do Redundancy Group")
    member_trader_ids: list[str] = Field(..., description="Lista dos IDs dos traders membros")
    lead_trader_id: str = Field(..., description="Trader de maior qualidade/líder do bloco")
    member_count: int = Field(..., ge=1, description="Total de membros no bloco redundante")
    
    total_group_weight: float = Field(..., ge=0.0, le=1.0, description="Soma dos pesos normalizados dos membros")
    total_group_weight_pct: float = Field(..., ge=0.0, le=100.0, description="Participação percentual total do grupo (%)")
    average_intra_group_redundancy: float = Field(default=100.0, description="Redundância média observada dentro do grupo (%)")
    cap_applied: bool = Field(default=False, description="Indica se o teto de grupo (maximum_group_weight) foi acionado")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class WeightConcentrationMetrics(BaseModel):
    """
    Métricas estatísticas de concentração e diversidade efetiva de pesos do núcleo.
    """
    effective_trader_count: float = Field(..., ge=0.0, description="Número efetivo de traders independentes: 1 / sum(w_i^2)")
    herfindahl_index: float = Field(..., ge=0.0, le=1.0, description="Índice de Herfindahl-Hirschman (HHI): sum(w_i^2)")
    top_1_weight_share_pct: float = Field(..., ge=0.0, le=100.0, description="Participação do trader de maior peso (%)")
    top_3_weight_share_pct: float = Field(..., ge=0.0, le=100.0, description="Soma da participação dos 3 maiores traders (%)")
    top_5_weight_share_pct: float = Field(..., ge=0.0, le=100.0, description="Soma da participação dos 5 maiores traders (%)")
    
    effective_group_count: float = Field(..., ge=0.0, description="Número efetivo de grupos independentes: 1 / sum(w_G^2)")
    group_herfindahl_index: float = Field(..., ge=0.0, le=1.0, description="HHI ponderado por grupo: sum(w_G^2)")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class CoreWeightSnapshot(BaseModel):
    """
    Fotografia consolidada e auditável da distribuição de pesos do núcleo formal em 'as_of'.
    """
    as_of: datetime = Field(..., description="Timestamp UTC de referência do snapshot")
    selected_traders: list[str] = Field(default_factory=list, description="Lista ordenada dos IDs dos traders ponderados")
    selected_trader_ids: list[str] = Field(default_factory=list, description="Alias da lista de IDs")
    
    trader_weights: list[TraderWeight] = Field(default_factory=list, description="Lista das ponderações individuais de cada trader")
    weights_map: dict[str, TraderWeight] = Field(default_factory=dict, description="Dicionário de acesso rápido trader_id -> TraderWeight")
    group_summaries: list[GroupWeightSummary] = Field(default_factory=list, description="Sumário dos pesos consolidados por grupo")
    
    concentration_metrics: WeightConcentrationMetrics = Field(..., description="Métricas de concentração e número efetivo")
    effective_trader_count: float = Field(..., ge=0.0, description="Número efetivo de participantes (1 / sum(w_i^2))")
    
    highest_weight_trader_id: Optional[str] = Field(default=None, description="ID do trader com maior peso no núcleo")
    highest_weight_pct: float = Field(default=0.0, description="Maior peso individual percentual (%)")
    lowest_weight_trader_id: Optional[str] = Field(default=None, description="ID do trader com menor peso no núcleo")
    lowest_weight_pct: float = Field(default=0.0, description="Menor peso individual percentual (%)")
    
    total_normalized_weight: float = Field(default=1.0, description="Soma estrita dos pesos normalizados (1.0)")
    diagnostics: dict[str, Any] = Field(default_factory=dict, description="Metadados globais da execução")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class WeightTurnoverMetric(BaseModel):
    """
    Métrica de rotação (turnover) da estrutura de pesos entre dois snapshots temporais consecutivos.
    """
    from_as_of: datetime = Field(..., description="Timestamp inicial do período")
    to_as_of: datetime = Field(..., description="Timestamp final do período")
    turnover_pct: float = Field(..., ge=0.0, le=100.0, description="Taxa de rotação percentual: 0.5 * sum(|w_i(t) - w_i(t-1)|) * 100")
    weight_deltas: dict[str, float] = Field(default_factory=dict, description="Variação de peso percentual por trader")
    max_weight_increase_trader: Optional[str] = Field(default=None, description="Trader com maior ganho de peso")
    max_weight_decrease_trader: Optional[str] = Field(default=None, description="Trader com maior perda de peso")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
