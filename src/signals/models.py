from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field
from src.domain.enums import PositionSide


class SignalState(str, Enum):
    """
    Estados discretos e auditáveis da opinião/posição de um trader em relação a um instrumento.
    """
    LONG = "LONG"              # Posição líquida comprada aberta em as_of
    SHORT = "SHORT"            # Posição líquida vendida aberta em as_of
    FLAT = "FLAT"              # Acompanha o ativo recentemente, mas está deliberadamente zerado em as_of
    NO_OPINION = "NO_OPINION"  # Nunca negociou ou não possui atividade recente suficiente no ativo
    UNKNOWN = "UNKNOWN"        # Estado não pôde ser reconstruído de forma confiável


class TraderSignal(BaseModel):
    """
    Registro individual e imutável da posição/opinião extraída de um trader para um instrumento em 'as_of'.
    """
    trader_id: str = Field(..., description="ID do trader")
    symbol: str = Field(..., description="Símbolo do instrumento financeiro")
    as_of: datetime = Field(..., description="Timestamp UTC limite de extração do sinal")
    
    signal_state: SignalState = Field(..., description="Estado discreto do sinal (LONG, SHORT, FLAT, NO_OPINION, UNKNOWN)")
    position_side: Optional[PositionSide] = Field(default=None, description="Direção da posição aberta (LONG, SHORT ou None)")
    position_quantity: Decimal = Field(default=Decimal("0.0"), description="Quantidade física líquida em aberto")
    normalized_exposure: float = Field(default=0.0, description="Direção líquida normalizada (+1.0 = LONG, -1.0 = SHORT, 0.0 = FLAT/NO_OPINION)")
    
    last_execution_at: Optional[datetime] = Field(default=None, description="Timestamp da última execução no instrumento <= as_of")
    days_since_last_execution: Optional[float] = Field(default=None, description="Dias decorridos desde a última execução até as_of")
    
    source: str = Field(default="POSITION_RECONSTRUCTION", description="Fonte/método de extração do sinal")
    weight: float = Field(default=0.0, ge=0.0, le=1.0, description="Peso normalizado do trader no Core (se associado)")
    redundancy_group_id: Optional[int] = Field(default=None, description="ID do Redundancy Group do trader")
    
    reasons: list[str] = Field(default_factory=list, description="Lista explicativa da classificação do sinal")
    diagnostics: dict[str, Any] = Field(default_factory=dict, description="Metadados internos da extração")

    model_config = {
        "frozen": True,
        "arbitrary_types_allowed": True
    }
