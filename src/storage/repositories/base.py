from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional, Protocol, Sequence
from src.domain.execution import Execution
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader


@dataclass(frozen=True)
class MarketPriceRecord:
    """Registro temporal de observação de preço de mercado para marcação a mercado."""
    symbol: str
    timestamp: datetime
    price: Decimal
    source: str = "feed"
    id: Optional[int] = None


class TraderRepository(Protocol):
    """Contrato abstrato de repositório para a entidade Trader."""
    def save(self, trader: Trader) -> None: ...
    def get_by_id(self, trader_id: str) -> Optional[Trader]: ...
    def list_all(self) -> list[Trader]: ...
    def list_active(self) -> list[Trader]: ...


class InstrumentRepository(Protocol):
    """Contrato abstrato de repositório para instrumentos de mercado."""
    def save(self, instrument: MarketInstrument) -> None: ...
    def get_by_symbol(self, symbol: str) -> Optional[MarketInstrument]: ...
    def list_all(self) -> list[MarketInstrument]: ...


@dataclass(frozen=True)
class ExecutionConflict:
    """Representa um conflito de integridade onde o execution_id já existe com dados divergentes."""
    execution_id: str
    existing_execution: Execution
    conflicting_execution: Execution
    divergent_fields: list[str]


class ExecutionRepository(Protocol):
    """Contrato abstrato de repositório para execuções (fills)."""
    def insert(self, execution: Execution) -> str:
        """Insere execução. Retorna 'INSERTED', 'DUPLICATE' ou 'CONFLICT'."""
        ...
    def insert_batch(self, executions: Sequence[Execution]) -> tuple[int, int, list[ExecutionConflict]]:
        """Insere lote de execuções. Retorna tupla (inseridos, duplicados, conflitos)."""
        ...
    def find_by_id(self, execution_id: str) -> Optional[Execution]: ...
    def find_by_trader(self, trader_id: str) -> list[Execution]: ...
    def find_by_symbol(self, symbol: str) -> list[Execution]: ...
    def find_by_trader_until_as_of(self, trader_id: str, as_of: datetime) -> list[Execution]: ...
    def find_by_time_range(self, start: datetime, end: datetime) -> list[Execution]: ...


class MarketPriceRepository(Protocol):
    """Contrato abstrato de repositório para cotações e preços de mercado."""
    def insert(self, record: MarketPriceRecord) -> None: ...
    def insert_batch(self, records: Sequence[MarketPriceRecord]) -> int: ...
    def get_latest_price_until_as_of(self, symbol: str, as_of: datetime) -> Optional[Decimal]: ...
    def get_price_history_until_as_of(self, symbol: str, as_of: datetime) -> list[MarketPriceRecord]: ...
