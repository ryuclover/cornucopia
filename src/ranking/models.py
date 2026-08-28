from datetime import datetime
from pydantic import BaseModel, Field
from src.evaluation.models import QualificationStatus


class TraderRankingItem(BaseModel):
    """
    Entrada individual de um trader no ranking em um instante 'as_of'.
    """
    rank: int = Field(..., ge=1, description="Posição ordinal no ranking (1 = melhor)")
    trader_id: str = Field(..., description="ID do trader")
    score: float = Field(..., ge=0.0, le=100.0, description="Survivor Score V1 no instante")
    is_qualified: bool = Field(..., description="Indica se atende a todos os critérios de sobrevivência")
    qualification_status: QualificationStatus = Field(..., description="QUALIFIED, DISQUALIFIED ou INSUFFICIENT_HISTORY")
    
    history_days: float = Field(..., ge=0.0, description="Dias de histórico até as_of")
    trade_count: int = Field(..., ge=0, description="Trades finalizados até as_of")
    max_drawdown_pct: float = Field(..., ge=0.0, description="Máximo drawdown histórico (%)")
    net_return_pct: float = Field(..., description="Retorno percentual acumulado (%)")
    profit_factor: float = Field(..., ge=0.0, description="Profit factor até as_of")
    disqualification_reasons: list[str] = Field(default_factory=list, description="Motivos de desqualificação")
    valuation_status: str = Field(default="CONFIRMED", description="Status de marcação a mercado")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class TraderRankingSnapshot(BaseModel):
    """
    Fotografia completa do ranking de traders no instante 'as_of'.
    """
    as_of: datetime = Field(..., description="Data/hora UTC limite do ranking")
    full_ranking: list[TraderRankingItem] = Field(default_factory=list, description="Todos os traders avaliados")
    qualified_ranking: list[TraderRankingItem] = Field(default_factory=list, description="Apenas traders qualificados")
    total_traders: int = Field(..., ge=0)
    qualified_traders: int = Field(..., ge=0)

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class TraderRankPersistence(BaseModel):
    """
    Métricas de permanência e consistência de um trader nas primeiras posições ao longo de uma série temporal.
    """
    trader_id: str = Field(..., description="ID do trader")
    evaluation_count: int = Field(..., ge=0, description="Total de avaliações em que o trader esteve presente")
    top_3_percentage: float = Field(..., ge=0.0, le=100.0, description="% das avaliações em que esteve no Top 3")
    top_5_percentage: float = Field(..., ge=0.0, le=100.0, description="% das avaliações em que esteve no Top 5")
    top_10_percentage: float = Field(..., ge=0.0, le=100.0, description="% das avaliações em que esteve no Top 10")
    average_rank: float = Field(..., ge=1.0, description="Posição média no ranking ao longo da série")
    best_rank: int = Field(..., ge=1, description="Melhor posição alcançada")
    worst_rank: int = Field(..., ge=1, description="Pior posição alcançada")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class RankingTurnoverMetric(BaseModel):
    """
    Métrica de rotatividade do ranking entre dois períodos consecutivos.
    """
    from_as_of: datetime = Field(..., description="Início do intervalo")
    to_as_of: datetime = Field(..., description="Fim do intervalo")
    top_n: int = Field(..., ge=1, description="Tamanho do grupo comparado (ex: Top 5, Top 10)")
    turnover_pct: float = Field(..., ge=0.0, le=100.0, description="% de novos traders que entraram no Top N")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
