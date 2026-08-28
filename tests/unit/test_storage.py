from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.domain.enums import AssetClass, OrderSide, TraderStatus
from src.domain.execution import Execution
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.storage.database import DatabaseManager
from src.storage.repositories.executions import SQLiteExecutionRepository
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.storage.repositories.traders import SQLiteTraderRepository
from src.storage.repositories.base import MarketPriceRecord


@pytest.fixture
def db():
    manager = DatabaseManager(db_path=":memory:")
    yield manager
    manager.close()


def test_trader_repository_round_trip(db):
    repo = SQLiteTraderRepository(db)
    t1 = Trader(
        trader_id="T001",
        name="Trader Alpha",
        status=TraderStatus.ACTIVE,
        initial_capital=Decimal("15000.00"),
        metadata={"desk": "prop"}
    )
    repo.save(t1)

    fetched = repo.get_by_id("T001")
    assert fetched is not None
    assert fetched.trader_id == "T001"
    assert fetched.name == "Trader Alpha"
    assert fetched.status == TraderStatus.ACTIVE
    assert fetched.initial_capital == Decimal("15000.00")
    assert fetched.metadata == {"desk": "prop"}

    # Atualização via upsert
    t1_updated = Trader(
        trader_id="T001",
        name="Trader Alpha Modificado",
        status=TraderStatus.INACTIVE,
        initial_capital=Decimal("20000.00"),
        metadata={"desk": "prop", "updated": "true"}
    )
    repo.save(t1_updated)
    fetched_updated = repo.get_by_id("T001")
    assert fetched_updated.name == "Trader Alpha Modificado"
    assert fetched_updated.status == TraderStatus.INACTIVE
    assert fetched_updated.initial_capital == Decimal("20000.00")

    all_traders = repo.list_all()
    assert len(all_traders) == 1
    assert len(repo.list_active()) == 0


def test_instrument_repository_round_trip(db):
    repo = SQLiteInstrumentRepository(db)
    inst = MarketInstrument(
        symbol="WIN$",
        asset_class=AssetClass.FUTURES,
        tick_size=Decimal("5.0"),
        tick_value=Decimal("1.0"),
        contract_multiplier=Decimal("0.20"),
        currency="BRL",
        description="Mini Índice"
    )
    repo.save(inst)

    fetched = repo.get_by_symbol("WIN$")
    assert fetched is not None
    assert fetched.symbol == "WIN$"
    assert fetched.tick_size == Decimal("5.0")
    assert fetched.contract_multiplier == Decimal("0.20")
    assert len(repo.list_all()) == 1


def test_execution_repository_round_trip_and_uniqueness(db):
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)

    # Cadastra dependências foreign key
    trader_repo.save(Trader(trader_id="T001", name="Trader Alpha"))
    inst_repo.save(MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY))

    e1 = Execution(
        execution_id="ex_001",
        trader_id="T001",
        symbol="PETR4",
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        price=Decimal("35.00"),
        timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc),
        commission=Decimal("2.50")
    )
    status_1 = exec_repo.insert(e1)
    assert status_1 == "INSERTED"

    # Inserção duplicada idêntica deve retornar DUPLICATE
    status_dup = exec_repo.insert(e1)
    assert status_dup == "DUPLICATE"

    # Inserção conflitante (mesmo ID, preço diferente)
    e1_conflict = Execution(
        execution_id="ex_001",
        trader_id="T001",
        symbol="PETR4",
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        price=Decimal("99.00"),
        timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc),
        commission=Decimal("2.50")
    )
    status_conf = exec_repo.insert(e1_conflict)
    assert status_conf == "CONFLICT"
    # O registro original permanece com 35.00 intacto
    assert exec_repo.find_by_id("ex_001").price == Decimal("35.00")

    # Inserção em lote com 1 novo e 1 duplicado
    e2 = Execution(
        execution_id="ex_002",
        trader_id="T001",
        symbol="PETR4",
        side=OrderSide.SELL,
        quantity=Decimal("100"),
        price=Decimal("37.00"),
        timestamp=datetime(2026, 1, 10, 15, 0, tzinfo=timezone.utc),
        commission=Decimal("2.50")
    )
    ins_count, dup_count, conflicts = exec_repo.insert_batch([e1, e2])
    assert ins_count == 1
    assert dup_count == 1
    assert len(conflicts) == 0

    by_trader = exec_repo.find_by_trader("T001")
    assert len(by_trader) == 2
    assert by_trader[0].execution_id == "ex_001"
    assert by_trader[1].execution_id == "ex_002"

    # Teste de consulta ponto-no-tempo
    as_of = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
    until_as_of = exec_repo.find_by_trader_until_as_of("T001", as_of)
    assert len(until_as_of) == 1
    assert until_as_of[0].execution_id == "ex_001"


def test_market_price_repository_round_trip(db):
    inst_repo = SQLiteInstrumentRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)
    inst_repo.save(MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY))

    records = [
        MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc), price=Decimal("30.00")),
        MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc), price=Decimal("32.50")),
        MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 10, 15, 0, tzinfo=timezone.utc), price=Decimal("35.00")),
    ]
    price_repo.insert_batch(records)

    # Consulta no tempo T = 12:00 deve retornar 32.50
    p1 = price_repo.get_latest_price_until_as_of("PETR4", datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc))
    assert p1 == Decimal("32.50")

    # Consulta no tempo T = 13:00 deve retornar o último preço conhecido (32.50) e NUNCA o de 15:00
    p2 = price_repo.get_latest_price_until_as_of("PETR4", datetime(2026, 1, 10, 13, 0, tzinfo=timezone.utc))
    assert p2 == Decimal("32.50")

    # Consulta anterior ao primeiro registro retorna None
    p_early = price_repo.get_latest_price_until_as_of("PETR4", datetime(2026, 1, 9, 23, 59, tzinfo=timezone.utc))
    assert p_early is None
