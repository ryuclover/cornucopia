from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from src.domain.enums import PositionSide


class ClosedTrade(BaseModel):
    """
    Operação finalizada (Round-Trip ou lote correspondido por saída parcial).
    
    Unidade atômica para cálculo de métricas de performance (P&L, win rate, drawdown, payoff, etc.).
    """
    trade_id: str = Field(..., description="Identificador único da operação consolidada")
    trader_id: str = Field(..., description="ID do trader")
    symbol: str = Field(..., description="Símbolo do instrumento negociado")
    side: PositionSide = Field(..., description="Direção da operação: LONG ou SHORT")
    quantity: Decimal = Field(..., gt=0, description="Quantidade negociada nesta operação")
    entry_price: Decimal = Field(..., gt=0, description="Preço médio de entrada")
    exit_price: Decimal = Field(..., gt=0, description="Preço médio de saída")
    entry_time: datetime = Field(..., description="Timestamp UTC da entrada")
    exit_time: datetime = Field(..., description="Timestamp UTC da saída (encerramento)")
    gross_pnl: Decimal = Field(..., description="P&L financeiro bruto")
    commission: Decimal = Field(default=Decimal("0.0"), ge=0, description="Total de custos incorridos (entrada + saída)")
    net_pnl: Decimal = Field(..., description="P&L financeiro líquido de custos (gross_pnl - commission)")
    return_pct: Decimal = Field(..., description="Retorno percentual da operação sobre o valor financeiro alocado")
    entry_execution_ids: list[str] = Field(default_factory=list, description="IDs das execuções de entrada")
    exit_execution_ids: list[str] = Field(default_factory=list, description="IDs das execuções de saída")
    max_adverse_excursion: Optional[Decimal] = Field(
        default=None,
        description="Máxima excursão adversa intratrade (pior drawdown financeiro experimentado durante a operação)"
    )
    max_favorable_excursion: Optional[Decimal] = Field(
        default=None,
        description="Máxima excursão favorável intratrade (pico financeiro positivo experimentado durante a operação)"
    )

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }

    @field_validator("entry_time", "exit_time")
    @classmethod
    def ensure_utc_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @property
    def duration_seconds(self) -> float:
        """Tempo de permanência na operação em segundos."""
        return (self.exit_time - self.entry_time).total_seconds()

    @property
    def is_win(self) -> bool:
        """Retorna se o trade teve resultado líquido estritamente positivo."""
        return self.net_pnl > 0

    @property
    def is_loss(self) -> bool:
        """Retorna se o trade teve resultado líquido estritamente negativo."""
        return self.net_pnl < 0

    @property
    def is_scratch(self) -> bool:
        """Retorna se o trade foi no zero a zero (breakeven)."""
        return self.net_pnl == 0
