from decimal import Decimal
import pytest
from src.domain.enums import AssetClass
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.ingestion.csv_parser import CsvParser
from src.ingestion.importer import ExecutionImporter
from src.ingestion.models import MissingColumnError
from src.storage.database import DatabaseManager
from src.storage.repositories.executions import SQLiteExecutionRepository
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.traders import SQLiteTraderRepository


@pytest.fixture
def setup_repos():
    db = DatabaseManager(db_path=":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)

    trader_repo.save(Trader(trader_id="T001", name="Trader Alpha"))
    inst_repo.save(MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY))

    importer = ExecutionImporter(
        execution_repo=exec_repo,
        trader_repo=trader_repo,
        instrument_repo=inst_repo
    )
    return importer, exec_repo


def test_import_valid_csv(setup_repos):
    importer, exec_repo = setup_repos
    csv_content = """execution_id,trader_id,symbol,timestamp,side,quantity,price,commission
e1,T001,PETR4,2026-01-10T10:00:00Z,BUY,100,30.50,1.50
e2,T001,PETR4,2026-01-10T15:00:00Z,SELL,100,33.00,1.50
"""
    report = importer.import_csv(csv_content, source_name="valid_test.csv")
    assert report.rows_read == 2
    assert report.inserted == 2
    assert report.duplicates == 0
    assert report.rejected == 0
    assert report.is_success is True

    stored = exec_repo.find_by_trader("T001")
    assert len(stored) == 2
    assert stored[0].price == Decimal("30.50")
    assert stored[1].price == Decimal("33.00")


def test_import_csv_missing_columns(setup_repos):
    importer, _ = setup_repos
    # Ausente a coluna 'price'
    bad_csv = """execution_id,trader_id,symbol,timestamp,side,quantity
e1,T001,PETR4,2026-01-10T10:00:00Z,BUY,100
"""
    with pytest.raises(MissingColumnError, match="Colunas obrigatórias ausentes"):
        importer.import_csv(bad_csv)


def test_import_csv_mixed_valid_and_invalid_lines(setup_repos):
    importer, exec_repo = setup_repos
    csv_content = """execution_id,trader_id,symbol,timestamp,side,quantity,price,commission
e1,T001,PETR4,2026-01-10T10:00:00Z,BUY,100,30.50,1.50
e2_bad_qty,T001,PETR4,2026-01-10T11:00:00Z,BUY,-50,30.50,1.50
e3_bad_side,T001,PETR4,2026-01-10T12:00:00Z,HOLD,100,30.50,1.50
e4_bad_ts,T001,PETR4,data_invalida,BUY,100,30.50,1.50
e5_unknown_trader,T999_UNKNOWN,PETR4,2026-01-10T14:00:00Z,BUY,100,30.50,1.50
e6_valid,T001,PETR4,2026-01-10T15:00:00Z,SELL,100,33.00,1.50
"""
    report = importer.import_csv(csv_content, source_name="mixed.csv")
    assert report.rows_read == 6
    assert report.inserted == 2  # Apenas e1 e e6
    assert report.rejected == 4
    assert report.is_success is False
    assert len(report.errors) == 4

    error_fields = {e.field for e in report.errors}
    assert "quantity" in error_fields
    assert "side" in error_fields
    assert "timestamp" in error_fields
    assert "trader_id" in error_fields

    stored = exec_repo.find_by_trader("T001")
    assert len(stored) == 2
    assert {e.execution_id for e in stored} == {"e1", "e6_valid"}
