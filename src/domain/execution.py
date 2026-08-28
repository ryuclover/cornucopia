from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from src.domain.enums import OrderSide


class Execution(BaseModel):
    """
    Evento atômico e imutável de execução de ordem (Fill).
    
    Representa o fato incontestável de que uma quantidade foi comprada ou vendida
    a determinado preço em um determinado instante no tempo.
    """
    execution_id: str = Field(..., description="Identificador único da execução (ex: ticket da corretora ou UUID)")
    trader_id: str = Field(..., description="ID do trader autor da execução")
    symbol: str = Field(..., description="Símbolo do instrumento negociado")
    side: OrderSide = Field(..., description="Direção da operação: BUY ou SELL")
    quantity: Decimal = Field(..., gt=0, description="Quantidade executada (estritamente positiva)")
    price: Decimal = Field(..., gt=0, description="Preço unitário de execução")
    timestamp: datetime = Field(..., description="Timestamp UTC exato do momento da execução")
    commission: Decimal = Field(default=Decimal("0.0"), ge=0, description="Taxas, emolumentos e corretagens da operação")
    slippage: Decimal = Field(default=Decimal("0.0"), ge=0, description="Slippage estimado ou medido")
    order_id: Optional[str] = Field(default=None, description="ID da ordem de origem se houver")
    notes: Optional[str] = Field(default=None, description="Anotações de auditoria")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }

    @field_validator("timestamp")
    @classmethod
    def ensure_utc_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)
