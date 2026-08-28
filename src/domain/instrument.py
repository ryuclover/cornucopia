from decimal import Decimal
from pydantic import BaseModel, Field
from src.domain.enums import AssetClass


class MarketInstrument(BaseModel):
    """
    Especificação técnica do ativo negociado.
    
    Permite calcular corretamente o valor financeiro do P&L em pontos, ticks ou moedas.
    """
    symbol: str = Field(..., description="Código do ativo (ex: PETR4, WIN$, WDO$, EURUSD)")
    asset_class: AssetClass = Field(default=AssetClass.EQUITY, description="Classe do ativo")
    tick_size: Decimal = Field(default=Decimal("0.01"), description="Variação mínima de preço")
    tick_value: Decimal = Field(default=Decimal("0.01"), description="Valor financeiro de 1 tick para 1 contrato/ação")
    contract_multiplier: Decimal = Field(default=Decimal("1.0"), description="Multiplicador de pontos (ex: 0.20 para WIN, 10.0 para WDO)")
    currency: str = Field(default="BRL", description="Moeda base de negociação")
    description: str = Field(default="", description="Descrição detalhada do instrumento")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }

    def calculate_pnl(self, quantity: Decimal, entry_price: Decimal, exit_price: Decimal, is_long: bool) -> Decimal:
        """Calcula o P&L financeiro bruto considerando o multiplicador do instrumento."""
        price_diff = (exit_price - entry_price) if is_long else (entry_price - exit_price)
        return price_diff * quantity * self.contract_multiplier
