from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field
from src.evaluation.models import QualificationStatus, ScoreTrend


class SelectionStatus(str, Enum):
    """
    Estados formais do ciclo de vida de um trader na política de seleção do núcleo.
    """
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # Amostra ou histórico insuficiente para avaliação estatística
    CANDIDATE = "CANDIDATE"                  # Dados suficientes e qualificado, em período probatório de confirmação
    SELECTED = "SELECTED"                    # Membro ativo e formal do núcleo de especialistas
    WATCHLIST = "WATCHLIST"                  # Selecionado sob observação preventiva por declínio recente
    SUSPENDED = "SUSPENDED"                  # Suspenso temporariamente do núcleo por perda de consistência
    EXCLUDED = "EXCLUDED"                    # Excluído permanentemente por violação grave/destrutiva


class TraderSelectionDecision(BaseModel):
    """
    Registro imutável e auditável da decisão de seleção tomada para um trader em 'as_of'.
    """
    trader_id: str = Field(..., description="ID do trader avaliado")
    as_of: datetime = Field(..., description="Timestamp UTC da decisão")
    previous_status: SelectionStatus = Field(..., description="Estado de seleção anterior")
    new_status: SelectionStatus = Field(..., description="Novo estado de seleção atribuído")
    
    survivor_score: float = Field(..., ge=0.0, le=100.0, description="Survivor Score lifetime na data")
    qualification_status: QualificationStatus = Field(..., description="Status do gatekeeper de sobrevivência")
    score_trend: ScoreTrend = Field(..., description="Tendência de score recente")
    
    consecutive_qualified_periods: int = Field(default=0, ge=0, description="Períodos consecutivos qualificado")
    consecutive_watchlist_periods: int = Field(default=0, ge=0, description="Períodos consecutivos em watchlist")
    consecutive_recovery_periods: int = Field(default=0, ge=0, description="Períodos consecutivos saudáveis durante recuperação (em WATCHLIST ou SUSPENDED)")
    
    reasons: list[str] = Field(default_factory=list, description="Lista detalhada dos motivos que fundamentaram a decisão")
    triggered_rules: list[str] = Field(default_factory=list, description="Identificadores das regras acionadas na transição")
    metrics_summary: dict[str, Any] = Field(default_factory=dict, description="Sumário das principais métricas utilizadas")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class TraderSelectionHistory(BaseModel):
    """
    Histórico longitudinal ordenado de todas as decisões de seleção de um trader.
    """
    trader_id: str = Field(..., description="ID do trader")
    decisions: list[TraderSelectionDecision] = Field(default_factory=list, description="Série temporal de decisões")
    current_status: SelectionStatus = Field(default=SelectionStatus.INSUFFICIENT_DATA, description="Estado mais recente")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class SelectedCoreSnapshot(BaseModel):
    """
    Fotografia agregada do núcleo de traders selecionados no instante 'as_of'.
    """
    as_of: datetime = Field(..., description="Timestamp UTC do snapshot do núcleo")
    selected_traders: list[TraderSelectionDecision] = Field(default_factory=list, description="Decisões dos traders no estado SELECTED")
    all_trader_decisions: list[TraderSelectionDecision] = Field(default_factory=list, description="Decisões de todos os traders avaliados")
    
    selected_count: int = Field(..., ge=0, description="Total de membros no estado SELECTED")
    candidate_count: int = Field(..., ge=0, description="Total no estado CANDIDATE")
    watchlist_count: int = Field(..., ge=0, description="Total no estado WATCHLIST")
    suspended_count: int = Field(..., ge=0, description="Total no estado SUSPENDED")
    excluded_count: int = Field(..., ge=0, description="Total no estado EXCLUDED")
    insufficient_data_count: int = Field(..., ge=0, description="Total no estado INSUFFICIENT_DATA")
    
    average_survivor_score: float = Field(default=0.0, description="Média do Survivor Score dos membros SELECTED")
    minimum_survivor_score: float = Field(default=0.0, description="Score mínimo entre os membros SELECTED")
    average_qualification_rate: float = Field(default=0.0, description="Média da taxa de qualificação dos membros SELECTED")
    average_drawdown_pct: float = Field(default=0.0, description="Média do max drawdown dos membros SELECTED")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class SelectionChurnMetric(BaseModel):
    """
    Métrica de renovação (churn) do núcleo entre dois snapshots consecutivos.
    """
    from_as_of: datetime = Field(..., description="Timestamp inicial do período")
    to_as_of: datetime = Field(..., description="Timestamp final do período")
    
    promoted_to_selected: list[str] = Field(default_factory=list, description="Traders que entraram em SELECTED")
    demoted_from_selected: list[str] = Field(default_factory=list, description="Traders que saíram de SELECTED")
    
    total_selected_before: int = Field(..., ge=0)
    total_selected_after: int = Field(..., ge=0)
    churn_count: int = Field(..., ge=0, description="Total de alterações (entradas + saídas)")
    churn_rate_pct: float = Field(..., ge=0.0, description="Taxa percentual de rotatividade do núcleo")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
