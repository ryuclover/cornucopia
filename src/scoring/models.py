from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field


class TraderPerformance(BaseModel):
    """
    Métricas puramente descritivas e objetivas da performance histórica de um trader.
    
    Calculadas rigorosamente 'ponto no tempo' (as_of) para evitar look-ahead bias.
    """
    trader_id: str = Field(..., description="ID do trader avaliado")
    as_of: datetime = Field(..., description="Data/hora UTC limite do cálculo")
    initial_capital: Decimal = Field(..., description="Capital base de referência")
    
    # Contagem e Histórico
    total_trades: int = Field(..., ge=0, description="Total de trades finalizados até as_of")
    winning_trades: int = Field(default=0, ge=0, description="Total de trades vencedores")
    losing_trades: int = Field(default=0, ge=0, description="Total de trades perdedores")
    scratch_trades: int = Field(default=0, ge=0, description="Total de trades neutros (zero P&L)")
    history_days: float = Field(..., ge=0.0, description="Duração do histórico em dias decorridos")
    
    # P&L e Retornos
    gross_pnl: Decimal = Field(..., description="P&L bruto total acumulado")
    total_commission: Decimal = Field(..., description="Total de custos pagos")
    net_pnl: Decimal = Field(..., description="P&L líquido total acumulado")
    total_return_pct: float = Field(..., description="Retorno percentual acumulado sobre capital base (%)")
    
    # Estatísticas de Trades
    win_rate: float = Field(..., ge=0.0, le=1.0, description="Taxa de acerto (0.0 a 1.0)")
    avg_win: Decimal = Field(default=Decimal("0.0"), description="Ganho financeiro médio dos trades vencedores")
    avg_loss: Decimal = Field(default=Decimal("0.0"), description="Perda financeira média dos trades perdedores (valor absoluto)")
    payoff_ratio: float = Field(default=0.0, ge=0.0, description="Relação ganho médio / perda média")
    profit_factor: float = Field(default=0.0, ge=0.0, description="Soma dos ganhos / Soma das perdas")
    
    # Risco de Cauda, Concentração e Sequências
    largest_win: Decimal = Field(default=Decimal("0.0"), description="Maior ganho individual")
    largest_loss: Decimal = Field(default=Decimal("0.0"), description="Maior perda individual (valor absoluto)")
    largest_loss_pct: float = Field(default=0.0, ge=0.0, description="Maior perda individual em % do capital")
    top_1_trade_pnl_contribution_pct: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Contribuição percentual do melhor trade sobre o lucro bruto total (%)"
    )
    top_n_trades_pnl_contribution_pct: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Concentração percentual do lucro nos Top N melhores trades"
    )
    top_5_trades_pnl_contribution_pct: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Contribuição percentual dos Top 5 melhores trades sobre o lucro bruto total (%)"
    )
    top_10_percent_trades_pnl_contribution_pct: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Contribuição percentual do top 10% dos trades vencedores sobre o lucro bruto total (%)"
    )
    max_consecutive_losses: int = Field(default=0, ge=0, description="Maior sequência consecutiva de derrotas")
    max_consecutive_wins: int = Field(default=0, ge=0, description="Maior sequência consecutiva de vitórias")
    
    # Drawdown
    max_drawdown_amount: Decimal = Field(default=Decimal("0.0"), description="Máximo drawdown financeiro")
    max_drawdown_pct: float = Field(default=0.0, ge=0.0, description="Máximo drawdown percentual de pico a vale (%)")
    
    # Índices de Eficiência e Risco
    return_volatility_pct: float = Field(default=0.0, ge=0.0, description="Desvio padrão dos retornos (%)")
    downside_volatility_pct: float = Field(default=0.0, ge=0.0, description="Desvio padrão apenas dos retornos negativos (%)")
    sharpe_ratio: float = Field(default=0.0, description="Índice Sharpe dos retornos")
    sortino_ratio: float = Field(default=0.0, description="Índice Sortino (foco em downside deviation)")
    calmar_ratio: float = Field(default=0.0, description="Índice Calmar (Retorno Anualizado / Max Drawdown)")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }


class TraderScore(BaseModel):
    """
    Avaliação qualitativa e determinística de sustentabilidade e consistência do trader.
    
    O Survivor Score varia de 0 a 100.
    """
    trader_id: str = Field(..., description="ID do trader")
    calculated_at: datetime = Field(..., description="Timestamp UTC do cálculo")
    score_total: float = Field(..., ge=0.0, le=100.0, description="Survivor Score final (0.0 a 100.0)")
    is_qualified: bool = Field(..., description="Indica se o trader superou todos os filtros rígidos de sobrevivência")
    
    # Sub-componentes do Score (0 a 100 ponderados)
    drawdown_score: float = Field(..., ge=0.0, le=100.0, description="Sub-score de preservação de capital / Drawdown")
    tail_risk_score: float = Field(..., ge=0.0, le=100.0, description="Sub-score de controle de perdas de cauda")
    risk_adjusted_return_score: float = Field(..., ge=0.0, le=100.0, description="Sub-score de retorno ajustado ao risco")
    maturity_score: float = Field(..., ge=0.0, le=100.0, description="Sub-score de maturidade e amostragem")
    
    disqualification_reasons: list[str] = Field(
        default_factory=list,
        description="Lista de motivos caso o trader tenha sido desqualificado pelos critérios rígidos"
    )

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
