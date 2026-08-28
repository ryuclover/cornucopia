from enum import Enum


class OrderSide(str, Enum):
    """Lado da execução da ordem."""
    BUY = "BUY"
    SELL = "SELL"


class PositionSide(str, Enum):
    """Direção da posição consolidada."""
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class AssetClass(str, Enum):
    """Classe de ativo de mercado."""
    EQUITY = "EQUITY"
    FUTURES = "FUTURES"
    FOREX = "FOREX"
    CRYPTO = "CRYPTO"
    COMMODITIES = "COMMODITIES"


class TraderStatus(str, Enum):
    """Status de atividade/qualificação do trader."""
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DISQUALIFIED = "DISQUALIFIED"
