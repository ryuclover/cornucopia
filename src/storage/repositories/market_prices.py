from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence
from src.storage.database import DatabaseManager
from src.storage.repositories.base import MarketPriceRecord


class SQLiteMarketPriceRepository:
    """Implementação SQLite para cotações e observações de preço de mercado."""
    def __init__(self, db: DatabaseManager):
        self.db = db

    def insert(self, record: MarketPriceRecord) -> None:
        ts_str = record.timestamp.astimezone(timezone.utc).isoformat()
        sql = "INSERT INTO market_prices (symbol, timestamp, price, source) VALUES (?, ?, ?, ?);"
        with self.db.transaction() as conn:
            conn.execute(sql, (record.symbol, ts_str, float(record.price), record.source))

    def insert_batch(self, records: Sequence[MarketPriceRecord]) -> int:
        if not records:
            return 0
        sql = "INSERT INTO market_prices (symbol, timestamp, price, source) VALUES (?, ?, ?, ?);"
        params = [
            (
                r.symbol,
                r.timestamp.astimezone(timezone.utc).isoformat(),
                float(r.price),
                r.source
            )
            for r in records
        ]
        with self.db.transaction() as conn:
            conn.executemany(sql, params)
        return len(records)

    def _row_to_record(self, row) -> MarketPriceRecord:
        ts = datetime.fromisoformat(row["timestamp"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return MarketPriceRecord(
            id=row["id"],
            symbol=row["symbol"],
            timestamp=ts,
            price=Decimal(str(row["price"])),
            source=row["source"]
        )

    def get_latest_price_until_as_of(self, symbol: str, as_of: datetime) -> Optional[Decimal]:
        """
        Retorna o último preço conhecido em ou antes de as_of.
        Garante que NENHUM preço futuro posterior a as_of seja retornado.
        """
        as_of_str = as_of.astimezone(timezone.utc).isoformat()
        sql = """
        SELECT price FROM market_prices 
        WHERE symbol = ? AND timestamp <= ? 
        ORDER BY timestamp DESC, id DESC 
        LIMIT 1;
        """
        conn = self.db.get_connection()
        try:
            cursor = conn.execute(sql, (symbol, as_of_str))
            row = cursor.fetchone()
            if row is None:
                return None
            return Decimal(str(row["price"]))
        finally:
            if not self.db._is_memory:
                conn.close()

    def get_latest_record_until_as_of(self, symbol: str, as_of: datetime) -> Optional[MarketPriceRecord]:
        """
        Retorna o último registro MarketPriceRecord conhecido em ou antes de as_of.
        """
        as_of_str = as_of.astimezone(timezone.utc).isoformat()
        sql = """
        SELECT * FROM market_prices 
        WHERE symbol = ? AND timestamp <= ? 
        ORDER BY timestamp DESC, id DESC 
        LIMIT 1;
        """
        conn = self.db.get_connection()
        try:
            cursor = conn.execute(sql, (symbol, as_of_str))
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_record(row)
        finally:
            if not self.db._is_memory:
                conn.close()

    def get_first_price_in_or_after_as_of(self, symbol: str, as_of: datetime) -> Optional[MarketPriceRecord]:
        """
        Retorna a primeira cotação MarketPriceRecord registrada em ou após as_of.
        """
        as_of_str = as_of.astimezone(timezone.utc).isoformat()
        sql = """
        SELECT * FROM market_prices 
        WHERE symbol = ? AND timestamp >= ? 
        ORDER BY timestamp ASC, id ASC 
        LIMIT 1;
        """
        conn = self.db.get_connection()
        try:
            cursor = conn.execute(sql, (symbol, as_of_str))
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_record(row)
        finally:
            if not self.db._is_memory:
                conn.close()

    def get_price_history_range(self, symbol: str, start: datetime, end: datetime) -> list[MarketPriceRecord]:
        """
        Retorna as cotações no intervalo [start, end].
        """
        start_str = start.astimezone(timezone.utc).isoformat()
        end_str = end.astimezone(timezone.utc).isoformat()
        sql = """
        SELECT * FROM market_prices 
        WHERE symbol = ? AND timestamp >= ? AND timestamp <= ? 
        ORDER BY timestamp ASC, id ASC;
        """
        conn = self.db.get_connection()
        try:
            cursor = conn.execute(sql, (symbol, start_str, end_str))
            return [self._row_to_record(r) for r in cursor.fetchall()]
        finally:
            if not self.db._is_memory:
                conn.close()

