import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from src.storage.schema import SCHEMA_DDL


class DatabaseManager:
    """
    Gerenciador de conexões SQLite e controle transacional.
    
    Garante integridade referencial, suporte a transações atômicas e caminhos configuráveis.
    """
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._is_memory = (db_path == ":memory:" or "file:" in db_path)
        self._shared_memory_conn: sqlite3.Connection | None = None

        if self._is_memory and db_path == ":memory:":
            # Para testes em memória, mantemos uma conexão viva compartilhada
            self._shared_memory_conn = sqlite3.connect(
                ":memory:",
                check_same_thread=False,
                detect_types=sqlite3.PARSE_DECLTYPES
            )
            self._shared_memory_conn.row_factory = sqlite3.Row
            self._init_connection(self._shared_memory_conn)
            self._init_schema(self._shared_memory_conn)
        elif not self._is_memory:
            path = Path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with self.get_connection() as conn:
                self._init_schema(conn)

    def _init_connection(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA foreign_keys = ON;")
        if not self._is_memory:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA_DDL)
        conn.commit()

    def get_connection(self) -> sqlite3.Connection:
        """Retorna uma conexão SQLite configurada com suporte a row factory."""
        if self._shared_memory_conn is not None:
            return self._shared_memory_conn

        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        conn.row_factory = sqlite3.Row
        self._init_connection(conn)
        return conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager para transações atômicas.
        Executa commit automático no encerramento ou rollback em caso de exceção.
        """
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if self._shared_memory_conn is None:
                conn.close()

    def close(self) -> None:
        """Fecha conexão de memória se houver."""
        if self._shared_memory_conn is not None:
            self._shared_memory_conn.close()
            self._shared_memory_conn = None
