from datetime import datetime, timezone
from decimal import Decimal
from pydantic import BaseModel, Field
from src.domain.enums import TraderStatus


class Trader(BaseModel):
    """
    Entidade representativa de um trader monitorado pelo sistema.
    """
    trader_id: str = Field(..., description="Identificador único universal do trader")
    name: str = Field(..., description="Nome ou alias do trader")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp UTC de inclusão do trader no sistema"
    )
    status: TraderStatus = Field(default=TraderStatus.ACTIVE, description="Status atual de acompanhamento")
    initial_capital: Decimal = Field(
        default=Decimal("10000.00"),
        description="Capital base fictício de referência para cálculos percentuais"
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Metadados adicionais (ex: corretora, perfil operacional, tags)"
    )

    model_config = {
        "frozen": False,
        "arbitrary_types_allowed": True
    }
