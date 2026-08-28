from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class QualificationStatus(str, Enum):
    QUALIFIED = "QUALIFIED"
    DISQUALIFIED = "DISQUALIFIED"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


class ScoreTrend(str, Enum):
    IMPROVING = "IMPROVING"
    STABLE = "STABLE"
    DETERIORATING = "DETERIORATING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class WindowSampleStatus(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


class WindowEvaluationResult(BaseModel):
    """
    Resultado detalhado da avaliação de uma janela temporal recente (ex: 30d, 90d, 180d).
    """
    window_days: int = Field(..., description="Duração da janela em dias")
    trade_count: int = Field(..., ge=0, description="Quantidade de trades fechados observados na janela")
    min_required_trades: int = Field(..., ge=1, description="Mínimo exigido de trades para considerar a amostra suficiente")
    start_date: datetime = Field(..., description="Início do intervalo da janela")
    end_date: datetime = Field(..., description="Fim do intervalo da janela (as_of)")
    sample_status: WindowSampleStatus = Field(..., description="Status de maturidade da amostra: SUFFICIENT ou INSUFFICIENT_SAMPLE")
    score: Optional[float] = Field(default=None, description="Survivor Score da janela (None se INSUFFICIENT_SAMPLE)")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class TraderEvaluationSnapshot(BaseModel):
    """
    Snapshot imutável e auditável da avaliação de um trader no instante 'as_of'.
    
    Consolida métricas de performance, Survivor Score e histórico ponto-no-tempo.
    """
    trader_id: str = Field(..., description="ID do trader avaliado")
    as_of: datetime = Field(..., description="Timestamp UTC limite da avaliação")
    history_start: datetime = Field(..., description="Timestamp da primeira execução ou criação do trader")
    history_days: float = Field(..., ge=0.0, description="Dias decorridos de histórico ativo até as_of")
    trade_count: int = Field(..., ge=0, description="Total de trades finalizados até as_of")
    
    # P&L e Retorno
    realized_pnl: Decimal = Field(..., description="P&L realizado líquido acumulado")
    realized_equity: Decimal = Field(..., description="Patrimônio realizado (Capital inicial + Realizado)")
    net_return_pct: float = Field(..., description="Retorno percentual sobre o capital inicial (%)")
    
    # Risco e Consistência
    max_drawdown_pct: float = Field(..., ge=0.0, description="Máximo drawdown percentual histórico até as_of (%)")
    win_rate: float = Field(..., ge=0.0, le=1.0, description="Taxa de acerto dos trades (0.0 a 1.0)")
    profit_factor: float = Field(..., ge=0.0, description="Fator de lucro (Ganhos brutos / Perdas brutas)")
    sharpe_ratio: float = Field(..., description="Índice Sharpe anualizado de referência")
    sortino_ratio: float = Field(..., description="Índice Sortino de referência")
    
    # Risco de Cauda e Concentração
    largest_loss_pct: float = Field(..., ge=0.0, description="Maior perda unitária em % do capital inicial")
    max_consecutive_losses: int = Field(..., ge=0, description="Maior sequência consecutiva de derrotas")
    top_1_trade_pnl_contribution_pct: float = Field(..., ge=0.0, le=100.0, description="% de contribuição do melhor trade")
    top_5_trades_pnl_contribution_pct: float = Field(..., ge=0.0, le=100.0, description="% de contribuição dos top 5 trades")
    top_10_percent_trades_pnl_contribution_pct: float = Field(..., ge=0.0, le=100.0, description="% de contribuição do top 10% dos trades")
    
    # Score e Qualificação
    survivor_score: float = Field(..., ge=0.0, le=100.0, description="Survivor Score final em as_of (0.0 a 100.0)")
    is_qualified: bool = Field(..., description="Indica se atende a todos os critérios rígidos de sobrevivência")
    qualification_status: QualificationStatus = Field(..., description="Status categórico: QUALIFIED, DISQUALIFIED ou INSUFFICIENT_HISTORY")
    disqualification_reasons: list[str] = Field(default_factory=list, description="Lista explicativa de desqualificação")
    valuation_status: str = Field(default="CONFIRMED", description="Status da marcação a mercado ('CONFIRMED' ou 'MISSING_MARKET_PRICE')")
    
    # Sub-componentes do Survivor Score
    drawdown_score: float = Field(default=0.0, ge=0.0, le=100.0)
    tail_risk_score: float = Field(default=0.0, ge=0.0, le=100.0)
    risk_adjusted_return_score: float = Field(default=0.0, ge=0.0, le=100.0)
    maturity_score: float = Field(default=0.0, ge=0.0, le=100.0)
    
    # Janelas Recentes vs Lifetime com auditoria de amostragem
    window_30d: Optional[WindowEvaluationResult] = Field(default=None, description="Resultado detalhado da janela de 30 dias")
    window_90d: Optional[WindowEvaluationResult] = Field(default=None, description="Resultado detalhado da janela de 90 dias")
    window_180d: Optional[WindowEvaluationResult] = Field(default=None, description="Resultado detalhado da janela de 180 dias")
    
    score_30d: Optional[float] = Field(default=None, description="Survivor Score nos últimos 30 dias (None se amostragem insuficiente)")
    score_90d: Optional[float] = Field(default=None, description="Survivor Score nos últimos 90 dias (None se amostragem insuficiente)")
    score_180d: Optional[float] = Field(default=None, description="Survivor Score nos últimos 180 dias (None se amostragem insuficiente)")
    
    trade_count_30d: int = Field(default=0, ge=0, description="Trades fechados nos últimos 30 dias")
    trade_count_90d: int = Field(default=0, ge=0, description="Trades fechados nos últimos 90 dias")
    trade_count_180d: int = Field(default=0, ge=0, description="Trades fechados nos últimos 180 dias")
    
    score_lifetime: float = Field(..., description="Survivor Score do histórico total disponível")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class TraderStabilityMetrics(BaseModel):
    """
    Métricas agregadas de estabilidade temporal e consistência de um trader ao longo de uma série.
    """
    trader_id: str = Field(..., description="ID do trader")
    period_count: int = Field(..., ge=0, description="Quantidade de avaliações temporais na série")
    
    mean_score: float = Field(..., description="Média do Survivor Score na série")
    median_score: float = Field(..., description="Mediana do Survivor Score na série")
    score_std_dev: float = Field(..., description="Desvio padrão do Survivor Score na série")
    min_score: float = Field(..., description="Score mínimo observado")
    max_score: float = Field(..., description="Score máximo observado")
    
    qualification_rate_pct: float = Field(..., ge=0.0, le=100.0, description="% das avaliações em que esteve qualificado")
    positive_period_rate_pct: float = Field(..., ge=0.0, le=100.0, description="% dos intervalos entre avaliações cujo retorno do período foi positivo")
    drawdown_breach_count: int = Field(..., ge=0, description="Número de vezes que excedeu o threshold de alerta de drawdown")
    
    score_trend: ScoreTrend = Field(..., description="Tendência recente: IMPROVING, STABLE, DETERIORATING ou INSUFFICIENT_DATA")
    score_trend_slope: float = Field(..., description="Inclinação da reta de regressão linear dos scores recentes")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
