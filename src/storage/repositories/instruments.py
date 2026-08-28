from decimal import Decimal
from typing import Optional
from src.domain.enums import AssetClass
from src.domain.instrument import MarketInstrument
from src.storage.database import DatabaseManager


class SQLiteInstrumentRepository:
    """Implementação SQLite do InstrumentRepository."""
    def __init__(self, db: DatabaseManager):
        self.db = db

    def save(self, instrument: MarketInstrument) -> None:
        sql = """
        INSERT INTO instruments (symbol, asset_class, tick_size, tick_value, contract_multiplier, currency, description)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            asset_class = excluded.asset_class,
            tick_size = excluded.tick_size,
            tick_value = excluded.tick_value,
            contract_multiplier = excluded.contract_multiplier,
            currency = excluded.currency,
            description = excluded.description;
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    instrument.symbol,
                    instrument.asset_class.value,
                    float(instrument.tick_size),
                    float(instrument.tick_value),
                    float(instrument.contract_multiplier),
                    instrument.currency,
                    instrument.description,
                )
            )

    def _row_to_instrument(self, row) -> MarketInstrument:
        return MarketInstrument(
            symbol=row["symbol"],
            asset_class=AssetClass(row["asset_class"]),
            tick_size=Decimal(str(row["tick_size"])),
            tick_value=Decimal(str(row["tick_value"])),
            contract_multiplier=Decimal(str(row["contract_multiplier"])),
            currency=row["currency"],
            description=row["description"]
        )

    def get_by_symbol(self, symbol: str) -> Optional[MarketInstrument]:
        conn = self.db.get_connection()
        try:
            cursor = conn.execute("SELECT * FROM instruments WHERE symbol = ?;", (symbol,))
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_instrument(row)
        finally:
            if not self.db._is_memory:
                conn.close()

    def list_all(self) -> list[MarketInstrument]:
        conn = self.db.get_connection()
        try:
            cursor = conn.execute("SELECT * FROM instruments ORDER BY symbol ASC;")
            return [self._row_to_instrument(r) for r in cursor.fetchall()]
        finally:
            if not self.db._is_memory:
                conn.close()
