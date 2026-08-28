import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence
from src.domain.enums import OrderSide
from src.domain.execution import Execution
from src.storage.database import DatabaseManager


class SQLiteExecutionRepository:
    """
    Implementação SQLite do ExecutionRepository com suporte a detecção de conflitos de integridade,
    idempotência para duplicatas legítimas e ordenação temporal determinística.
    """
    def __init__(self, db: DatabaseManager):
        self.db = db

    def _compare_executions(self, existing: Execution, incoming: Execution) -> list[str]:
        """Compara todos os campos econômicos relevantes para detectar divergências."""
        divergent = []
        if existing.trader_id != incoming.trader_id:
            divergent.append("trader_id")
        if existing.symbol != incoming.symbol:
            divergent.append("symbol")
        if existing.side != incoming.side:
            divergent.append("side")
        if existing.quantity != incoming.quantity:
            divergent.append("quantity")
        if existing.price != incoming.price:
            divergent.append("price")
        if existing.timestamp != incoming.timestamp:
            divergent.append("timestamp")
        if existing.commission != incoming.commission:
            divergent.append("commission")
        if existing.slippage != incoming.slippage:
            divergent.append("slippage")
        return divergent

    def insert(self, execution: Execution) -> str:
        """
        Insere execução.
        Retorna:
        - 'INSERTED' se inserido com sucesso
        - 'DUPLICATE' se idêntico a registro existente
        - 'CONFLICT' se execution_id colidir com dados divergentes
        """
        existing = self.find_by_id(execution.execution_id)
        if existing is not None:
            divergent = self._compare_executions(existing, execution)
            if not divergent:
                return "DUPLICATE"
            return "CONFLICT"

        ts_str = execution.timestamp.astimezone(timezone.utc).isoformat()
        sql = """
        INSERT INTO executions (
            execution_id, trader_id, symbol, side, quantity, price, timestamp,
            commission, slippage, order_id, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    execution.execution_id,
                    execution.trader_id,
                    execution.symbol,
                    execution.side.value,
                    float(execution.quantity),
                    float(execution.price),
                    ts_str,
                    float(execution.commission),
                    float(execution.slippage),
                    execution.order_id,
                    execution.notes,
                )
            )
            return "INSERTED"

    def insert_batch(self, executions: Sequence[Execution]) -> tuple[int, int, list[ExecutionConflict]]:
        """
        Insere lote de execuções dentro de uma única transação atômica.
        Retorna: (total_inseridos, total_duplicados_identicos, lista_conflitos).
        """
        if not executions:
            return (0, 0, [])

        from src.storage.repositories.base import ExecutionConflict

        insert_sql = """
        INSERT INTO executions (
            execution_id, trader_id, symbol, side, quantity, price, timestamp,
            commission, slippage, order_id, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """

        inserted_count = 0
        duplicate_count = 0
        conflicts: list[ExecutionConflict] = []

        with self.db.transaction() as conn:
            for ex in executions:
                cursor = conn.execute("SELECT * FROM executions WHERE execution_id = ?;", (ex.execution_id,))
                row = cursor.fetchone()
                if row is None:
                    # Não existe: insere
                    conn.execute(
                        insert_sql,
                        (
                            ex.execution_id,
                            ex.trader_id,
                            ex.symbol,
                            ex.side.value,
                            float(ex.quantity),
                            float(ex.price),
                            ex.timestamp.astimezone(timezone.utc).isoformat(),
                            float(ex.commission),
                            float(ex.slippage),
                            ex.order_id,
                            ex.notes,
                        )
                    )
                    inserted_count += 1
                else:
                    existing = self._row_to_execution(row)
                    divergent = self._compare_executions(existing, ex)
                    if not divergent:
                        duplicate_count += 1
                    else:
                        conflicts.append(
                            ExecutionConflict(
                                execution_id=ex.execution_id,
                                existing_execution=existing,
                                conflicting_execution=ex,
                                divergent_fields=divergent
                            )
                        )

        return (inserted_count, duplicate_count, conflicts)

    def find_by_id(self, execution_id: str) -> Optional[Execution]:
        conn = self.db.get_connection()
        try:
            cursor = conn.execute("SELECT * FROM executions WHERE execution_id = ?;", (execution_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_execution(row)
        finally:
            if not self.db._is_memory:
                conn.close()

    def _row_to_execution(self, row) -> Execution:
        ts = datetime.fromisoformat(row["timestamp"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return Execution(
            execution_id=row["execution_id"],
            trader_id=row["trader_id"],
            symbol=row["symbol"],
            side=OrderSide(row["side"]),
            quantity=Decimal(str(row["quantity"])),
            price=Decimal(str(row["price"])),
            timestamp=ts,
            commission=Decimal(str(row["commission"])),
            slippage=Decimal(str(row["slippage"])),
            order_id=row["order_id"],
            notes=row["notes"]
        )

    def find_by_trader(self, trader_id: str) -> list[Execution]:
        conn = self.db.get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM executions WHERE trader_id = ? ORDER BY timestamp ASC, execution_id ASC;",
                (trader_id,)
            )
            return [self._row_to_execution(r) for r in cursor.fetchall()]
        finally:
            if not self.db._is_memory:
                conn.close()

    def find_by_symbol(self, symbol: str) -> list[Execution]:
        conn = self.db.get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM executions WHERE symbol = ? ORDER BY timestamp ASC, execution_id ASC;",
                (symbol,)
            )
            return [self._row_to_execution(r) for r in cursor.fetchall()]
        finally:
            if not self.db._is_memory:
                conn.close()

    def find_by_trader_until_as_of(self, trader_id: str, as_of: datetime) -> list[Execution]:
        """
        Busca estritamente ponto-no-tempo (timestamp <= as_of) com ordenação determinística.
        """
        as_of_str = as_of.astimezone(timezone.utc).isoformat()
        conn = self.db.get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM executions WHERE trader_id = ? AND timestamp <= ? ORDER BY timestamp ASC, execution_id ASC;",
                (trader_id, as_of_str)
            )
            return [self._row_to_execution(r) for r in cursor.fetchall()]
        finally:
            if not self.db._is_memory:
                conn.close()

    def find_by_time_range(self, start: datetime, end: datetime) -> list[Execution]:
        start_str = start.astimezone(timezone.utc).isoformat()
        end_str = end.astimezone(timezone.utc).isoformat()
        conn = self.db.get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM executions WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC, execution_id ASC;",
                (start_str, end_str)
            )
            return [self._row_to_execution(r) for r in cursor.fetchall()]
        finally:
            if not self.db._is_memory:
                conn.close()

    def find_all_until_as_of(self, as_of: datetime) -> list[Execution]:
        """
        Retorna todas as execuções de todos os traders registradas até as_of.
        """
        as_of_str = as_of.astimezone(timezone.utc).isoformat()
        conn = self.db.get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM executions WHERE timestamp <= ? ORDER BY timestamp ASC, execution_id ASC;",
                (as_of_str,)
            )
            return [self._row_to_execution(r) for r in cursor.fetchall()]
        finally:
            if not self.db._is_memory:
                conn.close()

