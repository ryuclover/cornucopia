from decimal import Decimal
import pytest
from src.domain.enums import AssetClass
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.ingestion.importer import ExecutionImporter
from src.storage.database import DatabaseManager
from src.storage.repositories.executions import SQLiteExecutionRepository
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.traders import SQLiteTraderRepository
from src.synthetic.generator import SyntheticDataGenerator


def test_idempotent_import_executions():
    db = DatabaseManager(db_path=":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)

    # Cria trader e instrumento
    trader_repo.save(Trader(trader_id="T_001", name="Trader Test"))
    inst_repo.save(MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY))

    importer = ExecutionImporter(
        execution_repo=exec_repo,
        trader_repo=trader_repo,
        instrument_repo=inst_repo
    )

    # Gera 100 operações sintéticas determinísticas (200 execuções: compra + venda)
    gen = SyntheticDataGenerator(seed=123)
    executions = gen.generate_executions_for_trader("T_001", symbol="PETR4", trade_count=50)
    assert len(executions) == 100

    csv_content = gen.executions_to_csv(executions)

    # 1ª Importação: todas as 100 devem ser inseridas
    report_1 = importer.import_csv(csv_content, source_name="batch.csv")
    assert report_1.rows_read == 100
    assert report_1.inserted == 100
    assert report_1.duplicates == 0
    assert report_1.rejected == 0

    all_stored_1 = exec_repo.find_by_trader("T_001")
    assert len(all_stored_1) == 100

    # 2ª Importação do MESMO arquivo: 0 inseridas, 100 duplicatas identificadas, 0 rejeições
    report_2 = importer.import_csv(csv_content, source_name="batch.csv")
    assert report_2.rows_read == 100
    assert report_2.inserted == 0
    assert report_2.duplicates == 100
    assert report_2.conflicts == 0
    assert report_2.rejected == 0
    assert report_2.is_success is True

    all_stored_2 = exec_repo.find_by_trader("T_001")
    assert len(all_stored_2) == 100


def test_integrity_conflict_different_price_and_original_preserved():
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

    # 1. Importa execução inicial
    csv_1 = "execution_id,trader_id,symbol,timestamp,side,quantity,price,commission\n" \
            "exec_100,T001,PETR4,2026-01-10T10:00:00Z,BUY,100,30.00,1.50\n"
    rep_1 = importer.import_csv(csv_1)
    assert rep_1.inserted == 1
    assert rep_1.conflicts == 0

    # 2. Tenta importar mesmo execution_id com PREÇO DIFERENTE (38.00 em vez de 30.00)
    csv_conflicting_price = "execution_id,trader_id,symbol,timestamp,side,quantity,price,commission\n" \
                            "exec_100,T001,PETR4,2026-01-10T10:00:00Z,BUY,100,38.00,1.50\n"
    rep_2 = importer.import_csv(csv_conflicting_price)
    assert rep_2.inserted == 0
    assert rep_2.duplicates == 0
    assert rep_2.conflicts == 1
    assert rep_2.is_success is False
    assert any("price" in str(err.reason) for err in rep_2.errors)

    # Comprova que o registro no banco permanece o ORIGINAL com price=30.00
    stored = exec_repo.find_by_id("exec_100")
    assert stored is not None
    assert stored.price == Decimal("30.00")


def test_integrity_conflict_different_trader():
    db = DatabaseManager(db_path=":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)

    trader_repo.save(Trader(trader_id="T001", name="Trader Alpha"))
    trader_repo.save(Trader(trader_id="T002", name="Trader Beta"))
    inst_repo.save(MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY))

    importer = ExecutionImporter(
        execution_repo=exec_repo,
        trader_repo=trader_repo,
        instrument_repo=inst_repo
    )

    # 1. Inserção para T001
    csv_1 = "execution_id,trader_id,symbol,timestamp,side,quantity,price,commission\n" \
            "exec_200,T001,PETR4,2026-01-10T10:00:00Z,BUY,100,30.00,1.50\n"
    importer.import_csv(csv_1)

    # 2. Conflito: mesmo execution_id para T002
    csv_conflicting_trader = "execution_id,trader_id,symbol,timestamp,side,quantity,price,commission\n" \
                             "exec_200,T002,PETR4,2026-01-10T10:00:00Z,BUY,100,30.00,1.50\n"
    rep = importer.import_csv(csv_conflicting_trader)
    assert rep.inserted == 0
    assert rep.conflicts == 1
    assert rep.is_success is False

    # Original no banco continua sendo de T001
    stored = exec_repo.find_by_id("exec_200")
    assert stored.trader_id == "T001"
