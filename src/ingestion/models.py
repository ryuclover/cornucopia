from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


class StructuralIngestionError(Exception):
    """Exceção levantada quando a estrutura do arquivo (CSV/JSON) é inválida."""
    pass


class MissingColumnError(StructuralIngestionError):
    """Exceção levantada quando colunas obrigatórias estão ausentes."""
    pass


class MalformedFileError(StructuralIngestionError):
    """Exceção levantada quando o arquivo não pode ser lido/parseado."""
    pass


class CanonicalExecutionInput(BaseModel):
    """
    Modelo de entrada canônico intermediário para linhas recebidas de CSV/JSON.
    
    Normaliza tipos primitivos antes da conversão para a entidade de domínio Execution.
    """
    execution_id: str = Field(..., description="ID da execução")
    trader_id: str = Field(..., description="ID do trader")
    symbol: str = Field(..., description="Símbolo do instrumento")
    side: str = Field(..., description="BUY ou SELL")
    quantity: Decimal = Field(..., gt=0, description="Quantidade")
    price: Decimal = Field(..., gt=0, description="Preço unitário")
    timestamp: datetime = Field(..., description="Timestamp ISO-8601")
    commission: Decimal = Field(default=Decimal("0.0"), ge=0, description="Comissão")
    slippage: Decimal = Field(default=Decimal("0.0"), ge=0, description="Slippage")
    order_id: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None)

    @field_validator("side")
    @classmethod
    def validate_side(cls, v: str) -> str:
        upper = v.strip().upper()
        if upper not in ("BUY", "SELL"):
            raise ValueError(f"Side inválido: '{v}'. Deve ser 'BUY' ou 'SELL'.")
        return upper

    @field_validator("timestamp")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


@dataclass
class RowError:
    """Representação estruturada de um erro em uma linha específica."""
    row_number: int
    field: str
    reason: str
    raw_data: Optional[dict[str, Any]] = None


@dataclass
class ImportReport:
    """Relatório estruturado do resultado da importação de um lote de operações."""
    source: str
    rows_read: int = 0
    inserted: int = 0
    duplicates: int = 0
    conflicts: int = 0
    rejected: int = 0
    errors: list[RowError] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        """Retorna True se nenhuma linha foi rejeitada e não houve conflitos de integridade."""
        return self.rejected == 0 and self.conflicts == 0

    def summary(self) -> str:
        """Gera um resumo textual legível do relatório."""
        return (
            f"ImportReport(source='{self.source}', rows_read={self.rows_read}, "
            f"inserted={self.inserted}, duplicates={self.duplicates}, "
            f"conflicts={self.conflicts}, rejected={self.rejected}, errors_count={len(self.errors)})"
        )
