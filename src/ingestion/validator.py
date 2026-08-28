from decimal import Decimal
from typing import Optional
from src.domain.enums import OrderSide
from src.domain.execution import Execution
from src.ingestion.models import CanonicalExecutionInput, RowError
from src.storage.repositories.base import InstrumentRepository, TraderRepository


class IngestionValidator:
    """
    Validador de integridade referencial e conversão de entradas canônicas
    em entidades de domínio Execution.
    """
    def __init__(
        self,
        trader_repo: Optional[TraderRepository] = None,
        instrument_repo: Optional[InstrumentRepository] = None,
    ):
        self.trader_repo = trader_repo
        self.instrument_repo = instrument_repo

    def validate_and_convert(
        self,
        inputs: list[CanonicalExecutionInput]
    ) -> tuple[list[Execution], list[RowError]]:
        valid_executions: list[Execution] = []
        errors: list[RowError] = []

        # Cache local de verificação para performance em lote
        known_traders: set[str] = set()
        known_instruments: set[str] = set()

        if self.trader_repo:
            known_traders = {t.trader_id for t in self.trader_repo.list_all()}
        if self.instrument_repo:
            known_instruments = {i.symbol for i in self.instrument_repo.list_all()}

        for idx, item in enumerate(inputs, start=1):
            if self.trader_repo and item.trader_id not in known_traders:
                errors.append(
                    RowError(
                        row_number=idx,
                        field="trader_id",
                        reason=f"Trader '{item.trader_id}' não cadastrado no sistema.",
                        raw_data={"execution_id": item.execution_id, "trader_id": item.trader_id}
                    )
                )
                continue

            if self.instrument_repo and item.symbol not in known_instruments:
                errors.append(
                    RowError(
                        row_number=idx,
                        field="symbol",
                        reason=f"Instrumento '{item.symbol}' não cadastrado no sistema.",
                        raw_data={"execution_id": item.execution_id, "symbol": item.symbol}
                    )
                )
                continue

            try:
                execution = Execution(
                    execution_id=item.execution_id,
                    trader_id=item.trader_id,
                    symbol=item.symbol,
                    side=OrderSide(item.side),
                    quantity=item.quantity,
                    price=item.price,
                    timestamp=item.timestamp,
                    commission=item.commission,
                    slippage=item.slippage,
                    order_id=item.order_id,
                    notes=item.notes
                )
                valid_executions.append(execution)
            except Exception as e:
                errors.append(
                    RowError(
                        row_number=idx,
                        field="domain_conversion",
                        reason=str(e),
                        raw_data={"execution_id": item.execution_id}
                    )
                )

        return valid_executions, errors
