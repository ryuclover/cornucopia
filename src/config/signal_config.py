from pydantic import BaseModel, Field


class SignalConfig(BaseModel):
    """
    Configurações centralizadas para extração de sinais e opiniões point-in-time dos traders.
    """
    flat_activity_lookback_days: int = Field(
        default=30,
        ge=1,
        description="Janela temporal em dias para classificar uma posição zerada como FLAT (recente) em vez de NO_OPINION (inativo)"
    )

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
