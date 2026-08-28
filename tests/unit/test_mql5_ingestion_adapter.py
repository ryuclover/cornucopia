from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import pytest
from src.domain.enums import AssetClass, OrderSide
from src.domain.instrument import MarketInstrument
from src.domain.position import PositionTracker
from src.ingestion.mql5_adapter import MQL5SignalsIngestionAdapter
from src.storage.database import DatabaseManager as SQLiteDatabaseManager
from src.storage.repositories.executions import SQLiteExecutionRepository
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.traders import SQLiteTraderRepository


def test_mql5_adapter_deal_conversion_and_provenance():
    """
    Testa a conversão de um arquivo fixture MQL5 em Executions atômicas com proveniência completa.
    """
    adapter = MQL5SignalsIngestionAdapter()
    trader_file = Path("data/fixtures/acquisition_poc/raw/trader_2329290.json")

    trader, executions, instruments = adapter.parse_raw_signal_file(trader_file)

    assert trader.trader_id == "MQL5_2329290"
    assert trader.name == "Precise Pair Trading Pro"
    assert len(executions) == 16  # 8 deals * 2 (OPEN e CLOSE)
    assert len(instruments) == 2  # EURUSD e GBPUSD

    # Primeiro deal: Ticket 1085201, BUY 0.10 EURUSD
    open_ex = executions[0]
    assert open_ex.execution_id == "MQL5_2329290_1085201_OPEN"
    assert open_ex.side == OrderSide.BUY
    assert open_ex.quantity == Decimal("0.10")
    assert open_ex.price == Decimal("1.08500")
    assert "MQL5_SIGNALS" in open_ex.notes
    assert "1085201" in open_ex.notes

    close_ex = executions[1]
    assert close_ex.execution_id == "MQL5_2329290_1085201_CLOSE"
    assert close_ex.side == OrderSide.SELL
    assert close_ex.quantity == Decimal("0.10")
    assert close_ex.price == Decimal("1.08900")


def test_mql5_normalized_dataset_persistence():
    """
    Valida a persistência do dataset normalizado nos repositórios SQLite do Cornucopia.
    """
    adapter = MQL5SignalsIngestionAdapter()
    trader, executions, instruments = adapter.parse_raw_signal_file("data/fixtures/acquisition_poc/raw/trader_2340140.json")

    db = SQLiteDatabaseManager(":memory:")
    t_repo = SQLiteTraderRepository(db)
    i_repo = SQLiteInstrumentRepository(db)
    e_repo = SQLiteExecutionRepository(db)

    t_repo.save(trader)
    for inst in instruments:
        i_repo.save(inst)

    inserted, dup, conf = e_repo.insert_batch(executions)
    assert inserted == len(executions)
    assert dup == 0
    assert len(conf) == 0

    persisted_execs = e_repo.find_by_trader(trader.trader_id)
    assert len(persisted_execs) == len(executions)


def test_mql5_adapter_native_csv_statement_and_pnl_reconstruction():
    """
    Testa a ingestão de um arquivo no formato nativo MT4/MT5 CSV Statement
    e valida a coerência da reconstrução de posições e P&L com o PositionTracker.
    """
    # Cria arquivo CSV simulando uma exportação nativa do MetaTrader
    sample_csv_content = """Ticket,Open Time,Type,Size,Item,Price,S / L,T / P,Close Time,Price,Commission,Taxes,Swap,Profit
101,2026.01.12 08:30:00,buy,100,PETR4,30.00,28.00,34.00,2026.01.12 16:30:00,32.00,-2.00,0.00,0.00,200.00
102,2026.01.14 09:00:00,sell,100,PETR4,32.50,34.00,30.00,2026.01.14 17:00:00,31.00,-2.00,0.00,0.00,150.00
"""
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as tmp:
        tmp.write(sample_csv_content)
        tmp_path = tmp.name

    adapter = MQL5SignalsIngestionAdapter()
    trader, executions, instruments, audit = adapter.parse_csv_statement(
        file_path=tmp_path,
        signal_id="MT4_DEMO_01",
        trader_name="Demo MT4 Signal"
    )

    assert audit["trades_count"] == 2
    assert audit["executions_count"] == 4
    assert audit["reported_profit_sum"] == 350.00

    # Valida reconstrução de P&L via PositionTracker
    tracker = PositionTracker(
        instrument=instruments[0],
        trader_id=trader.trader_id,
        initial_capital=Decimal("10000.00")
    )
    for ex in executions:
        tracker.process_execution(ex)

    # Ao final das 2 operações fechadas, a posição líquida deve ser FLAT (0)
    assert tracker.position.quantity == Decimal("0.0")
    # P&L realizado líquido descontando corretagens ($4.00): $350 - $4 = $346.00
    assert tracker.position.realized_pnl == Decimal("346.00")
    assert tracker.position.total_commission_paid == Decimal("4.00")
    # P&L realizado bruto: $350.00
    gross_pnl = tracker.position.realized_pnl + tracker.position.total_commission_paid
    assert gross_pnl == Decimal("350.00")
