import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from src.domain.enums import TraderStatus
from src.domain.trader import Trader
from src.storage.database import DatabaseManager


class SQLiteTraderRepository:
    """Implementação SQLite do TraderRepository."""
    def __init__(self, db: DatabaseManager):
        self.db = db

    def save(self, trader: Trader) -> None:
        created_str = trader.created_at.astimezone(timezone.utc).isoformat()
        meta_str = json.dumps(trader.metadata)
        sql = """
        INSERT INTO traders (trader_id, name, created_at, status, initial_capital, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(trader_id) DO UPDATE SET
            name = excluded.name,
            status = excluded.status,
            initial_capital = excluded.initial_capital,
            metadata_json = excluded.metadata_json;
        """
        with self.db.transaction() as conn:
            conn.execute(
                sql,
                (
                    trader.trader_id,
                    trader.name,
                    created_str,
                    trader.status.value,
                    float(trader.initial_capital),
                    meta_str,
                )
            )

    def _row_to_trader(self, row) -> Trader:
        created_dt = datetime.fromisoformat(row["created_at"])
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        return Trader(
            trader_id=row["trader_id"],
            name=row["name"],
            created_at=created_dt,
            status=TraderStatus(row["status"]),
            initial_capital=Decimal(str(row["initial_capital"])),
            metadata=json.loads(row["metadata_json"] or "{}")
        )

    def get_by_id(self, trader_id: str) -> Optional[Trader]:
        conn = self.db.get_connection()
        try:
            cursor = conn.execute("SELECT * FROM traders WHERE trader_id = ?;", (trader_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_trader(row)
        finally:
            if not self.db._is_memory:
                conn.close()

    def list_all(self) -> list[Trader]:
        conn = self.db.get_connection()
        try:
            cursor = conn.execute("SELECT * FROM traders ORDER BY created_at ASC;")
            return [self._row_to_trader(r) for r in cursor.fetchall()]
        finally:
            if not self.db._is_memory:
                conn.close()

    def list_active(self) -> list[Trader]:
        conn = self.db.get_connection()
        try:
            cursor = conn.execute("SELECT * FROM traders WHERE status = ? ORDER BY created_at ASC;", (TraderStatus.ACTIVE.value,))
            return [self._row_to_trader(r) for r in cursor.fetchall()]
        finally:
            if not self.db._is_memory:
                conn.close()
