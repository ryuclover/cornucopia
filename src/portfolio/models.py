from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from pydantic import BaseModel, Field
from src.domain.position import Position
from src.domain.trade import ClosedTrade


class TraderVirtualPortfolio(BaseModel):
    """
    Representação da evolução patrimonial e alocação de um trader individual em 'as_of'.
    
    Reutiliza a contabilidade auditada do ReplayEngine sem executar decisões discricionárias.
    """
    trader_id: str = Field(..., description="ID do trader")
    as_of: datetime = Field(..., description="Timestamp UTC limite do portfólio")
    initial_capital: Decimal = Field(..., description="Capital base inicial alocado")
    
    realized_equity: Decimal = Field(..., description="Patrimônio realizado (Capital inicial + Realizado)")
    mark_to_market_equity: Optional[Decimal] = Field(default=None, description="Patrimônio MTM total (None se cotação ausente)")
    
    realized_pnl: Decimal = Field(default=Decimal("0.0"), description="Lucro/prejuízo realizado acumulado")
    unrealized_pnl: Optional[Decimal] = Field(default=None, description="Lucro/prejuízo não realizado das posições em aberto")
    
    positions: dict[str, Any] = Field(default_factory=dict, description="Posições consolidadas por símbolo")
    closed_trades: list[ClosedTrade] = Field(default_factory=list, description="Histórico de operações finalizadas até as_of")
    
    drawdown_pct: float = Field(default=0.0, ge=0.0, description="Drawdown atual em relação ao topo histórico (%)")
    peak_equity: Decimal = Field(..., description="Maior patrimônio histórico alcançado até as_of")
    
    valuation_status: str = Field(default="CONFIRMED", description="Status da marcação a mercado: 'CONFIRMED' ou 'MISSING_MARKET_PRICE'")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
