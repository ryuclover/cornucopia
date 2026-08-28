from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from pydantic import BaseModel, Field
from src.domain.position import EquitySnapshot, Position
from src.domain.trade import ClosedTrade
from src.scoring.models import TraderPerformance, TraderScore


class TraderReplayResult(BaseModel):
    """
    Estado completo e auditável reconstruído de um trader no instante 'as_of'.
    """
    trader_id: str = Field(..., description="ID do trader")
    as_of: datetime = Field(..., description="Timestamp UTC limite da reconstrução")
    initial_capital: Decimal = Field(..., description="Capital base inicial")
    
    positions: dict[str, Any] = Field(
        default_factory=dict,
        description="Dicionário símbolo -> estado consolidado da posição"
    )
    closed_trades: list[ClosedTrade] = Field(
        default_factory=list,
        description="Lista de todos os trades finalizados até as_of"
    )
    
    total_realized_pnl: Decimal = Field(default=Decimal("0.0"), description="P&L realizado acumulado")
    realized_equity: Decimal = Field(..., description="Patrimônio realizado (Capital inicial + P&L realizado)")
    total_unrealized_pnl: Optional[Decimal] = Field(
        default=None,
        description="P&L não realizado das posições abertas (None caso falte cotação de mercado em ou antes de as_of)"
    )
    total_commission: Decimal = Field(default=Decimal("0.0"), description="Total de comissões/taxas pagas")
    total_equity: Optional[Decimal] = Field(
        default=None,
        description="Patrimônio líquido MTM total (None caso valuation_status == 'MISSING_MARKET_PRICE')"
    )
    valuation_status: str = Field(
        default="CONFIRMED",
        description="Status da avaliação mark-to-market: 'CONFIRMED' ou 'MISSING_MARKET_PRICE'"
    )
    
    equity_snapshots: list[EquitySnapshot] = Field(
        default_factory=list,
        description="Histórico temporal mark-to-market reconstruído"
    )
    
    performance: TraderPerformance = Field(..., description="Métricas de performance calculadas até as_of")
    score: Optional[TraderScore] = Field(default=None, description="Survivor Score calculado")

    model_config = {
        "arbitrary_types_allowed": True
    }
