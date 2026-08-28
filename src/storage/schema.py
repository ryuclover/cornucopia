"""Definição determinística do esquema SQL e índices para SQLite."""

SCHEMA_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS traders (
    trader_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    initial_capital REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS instruments (
    symbol TEXT PRIMARY KEY,
    asset_class TEXT NOT NULL,
    tick_size REAL NOT NULL,
    tick_value REAL NOT NULL,
    contract_multiplier REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'BRL',
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    trader_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    timestamp TEXT NOT NULL,
    commission REAL NOT NULL DEFAULT 0.0,
    slippage REAL NOT NULL DEFAULT 0.0,
    order_id TEXT,
    notes TEXT,
    FOREIGN KEY (trader_id) REFERENCES traders(trader_id) ON DELETE CASCADE,
    FOREIGN KEY (symbol) REFERENCES instruments(symbol) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_executions_trader_time 
ON executions (trader_id, timestamp ASC, execution_id ASC);

CREATE INDEX IF NOT EXISTS idx_executions_symbol_time 
ON executions (symbol, timestamp ASC);

CREATE INDEX IF NOT EXISTS idx_executions_timestamp 
ON executions (timestamp ASC);

CREATE TABLE IF NOT EXISTS market_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    price REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'feed',
    FOREIGN KEY (symbol) REFERENCES instruments(symbol) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_market_prices_sym_time 
ON market_prices (symbol, timestamp ASC, id ASC);
"""
