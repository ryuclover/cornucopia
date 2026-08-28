from decimal import Decimal
import pytest
from src.domain.enums import AssetClass
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.ingestion.importer import ExecutionImporter
from src.ingestion.models import MalformedFileError
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


def test_import_valid_json_array(setup_repos):
    importer, exec_repo = setup_repos
    json_text = """[
        {
            "execution_id": "j1",
            "trader_id": "T001",
            "symbol": "PETR4",
            "timestamp": "2026-01-10T10:00:00Z",
            "side": "BUY",
            "quantity": 100,
            "price": 30.50,
            "commission": 1.50
        },
        {
            "execution_id": "j2",
            "trader_id": "T001",
            "symbol": "PETR4",
            "timestamp": "2026-01-10T15:00:00Z",
            "side": "SELL",
            "quantity": 100,
            "price": 33.00,
            "commission": 1.50
        }
    ]"""
    report = importer.import_json(json_text, source_name="valid.json")
    assert report.rows_read == 2
    assert report.inserted == 2
    assert report.is_success is True

    stored = exec_repo.find_by_trader("T001")
    assert len(stored) == 2


def test_import_valid_json_lines(setup_repos):
    importer, exec_repo = setup_repos
    jsonl_text = """{"execution_id": "jl1", "trader_id": "T001", "symbol": "PETR4", "timestamp": "2026-01-10T10:00:00Z", "side": "BUY", "quantity": 100, "price": 30.50}
{"execution_id": "jl2", "trader_id": "T001", "symbol": "PETR4", "timestamp": "2026-01-10T15:00:00Z", "side": "SELL", "quantity": 100, "price": 33.00}
"""
    report = importer.import_json(jsonl_text, source_name="valid.jsonl")
    assert report.rows_read == 2
    assert report.inserted == 2
    assert report.is_success is True


def test_import_malformed_json(setup_repos):
    importer, _ = setup_repos
    bad_json = """[ {"execution_id": "j1", "trader_id": "T001" INVALID_SYNTAX """
    with pytest.raises(MalformedFileError, match="JSON"):
        importer.import_json(bad_json)


def test_import_json_with_invalid_fields(setup_repos):
    importer, exec_repo = setup_repos
    json_text = """[
        {
            "execution_id": "j1",
            "trader_id": "T001",
            "symbol": "PETR4",
            "timestamp": "2026-01-10T10:00:00Z",
            "side": "BUY",
            "quantity": 100,
            "price": 30.50
        },
        {
            "execution_id": "j2_missing_price",
            "trader_id": "T001",
            "symbol": "PETR4",
            "timestamp": "2026-01-10T15:00:00Z",
            "side": "SELL",
            "quantity": 100
        }
    ]"""
    report = importer.import_json(json_text, source_name="missing_fields.json")
    assert report.rows_read == 2
    assert report.inserted == 1
    assert report.rejected == 1
    assert report.errors[0].field == "price"
