from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.domain.enums import AssetClass, OrderSide, PositionSide
from src.domain.execution import Execution
from src.domain.instrument import MarketInstrument
from src.domain.position import PositionTracker
from src.domain.trader import Trader
from src.replay.engine import TraderReplayEngine
from src.storage.database import DatabaseManager
from src.storage.repositories.executions import SQLiteExecutionRepository
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.storage.repositories.traders import SQLiteTraderRepository
from src.storage.repositories.base import MarketPriceRecord


@pytest.fixture
def replay_setup():
    db = DatabaseManager(db_path=":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    # Cadastra trader base e instrumentos
    trader = Trader(trader_id="T001", name="Trader Replay", initial_capital=Decimal("10000.00"))
    petr4 = MarketInstrument(
        symbol="PETR4",
        asset_class=AssetClass.EQUITY,
        tick_size=Decimal("0.01"),
        contract_multiplier=Decimal("1.0")
    )
    trader_repo.save(trader)
    inst_repo.save(petr4)

    engine = TraderReplayEngine(
        trader_repo=trader_repo,
        instrument_repo=inst_repo,
        execution_repo=exec_repo,
        market_price_repo=price_repo
    )
    return engine, exec_repo, price_repo


def test_simple_buy_sell_replay(replay_setup):
    engine, exec_repo, _ = replay_setup
    
    # 1. Compra 100 @ 30.00 às 10:00
    e1 = Execution(
        execution_id="e1",
        trader_id="T001",
        symbol="PETR4",
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        price=Decimal("30.00"),
        timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc),
        commission=Decimal("2.50")
    )
    # 2. Vende 100 @ 35.00 às 15:00
    e2 = Execution(
        execution_id="e2",
        trader_id="T001",
        symbol="PETR4",
        side=OrderSide.SELL,
        quantity=Decimal("100"),
        price=Decimal("35.00"),
        timestamp=datetime(2026, 1, 10, 15, 0, tzinfo=timezone.utc),
        commission=Decimal("2.50")
    )
    exec_repo.insert_batch([e1, e2])

    as_of = datetime(2026, 1, 10, 18, 0, tzinfo=timezone.utc)
    result = engine.replay_trader("T001", as_of=as_of)

    assert len(result.closed_trades) == 1
    trade = result.closed_trades[0]
    assert trade.gross_pnl == Decimal("500.00")
    assert trade.net_pnl == Decimal("495.00")
    assert result.total_realized_pnl == Decimal("495.00")
    assert result.total_equity == Decimal("10495.00")
    assert result.performance.total_trades == 1
    assert result.performance.win_rate == 1.0


def test_scale_in_and_scale_out_matches_position_tracker(replay_setup):
    engine, exec_repo, _ = replay_setup
    inst = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY)

    # Execuções: Compra 100 @ 30.00, Compra 100 @ 32.00, Vende 150 @ 36.00
    e1 = Execution(execution_id="e1", trader_id="T001", symbol="PETR4", side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00"), timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc), commission=Decimal("1.00"))
    e2 = Execution(execution_id="e2", trader_id="T001", symbol="PETR4", side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("32.00"), timestamp=datetime(2026, 1, 10, 11, 0, tzinfo=timezone.utc), commission=Decimal("1.00"))
    e3 = Execution(execution_id="e3", trader_id="T001", symbol="PETR4", side=OrderSide.SELL, quantity=Decimal("150"), price=Decimal("36.00"), timestamp=datetime(2026, 1, 10, 14, 0, tzinfo=timezone.utc), commission=Decimal("1.50"))
    exec_repo.insert_batch([e1, e2, e3])

    # Replay persistido
    as_of = datetime(2026, 1, 10, 18, 0, tzinfo=timezone.utc)
    replay_res = engine.replay_trader("T001", as_of=as_of)

    # Tracker direto em memória
    direct_tracker = PositionTracker(instrument=inst, trader_id="T001", initial_capital=Decimal("10000.00"))
    direct_tracker.process_execution(e1)
    direct_tracker.process_execution(e2)
    direct_tracker.process_execution(e3)

    assert replay_res.positions["PETR4"].quantity == direct_tracker.position.quantity == Decimal("50")
    assert replay_res.positions["PETR4"].average_entry_price == direct_tracker.position.average_entry_price == Decimal("32.00")
    assert replay_res.total_realized_pnl == direct_tracker.position.realized_pnl
    assert len(replay_res.closed_trades) == len(direct_tracker.closed_trades) == 2


def test_position_reversal_replay(replay_setup):
    engine, exec_repo, _ = replay_setup

    # Compra 100 @ 30.00, depois Vende 150 @ 28.00 (Stop e vira para Short de 50)
    e1 = Execution(execution_id="e1", trader_id="T001", symbol="PETR4", side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00"), timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc))
    e2 = Execution(execution_id="e2", trader_id="T001", symbol="PETR4", side=OrderSide.SELL, quantity=Decimal("150"), price=Decimal("28.00"), timestamp=datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc))
    exec_repo.insert_batch([e1, e2])

    as_of = datetime(2026, 1, 10, 18, 0, tzinfo=timezone.utc)
    res = engine.replay_trader("T001", as_of=as_of)

    pos = res.positions["PETR4"]
    assert pos.side == PositionSide.SHORT
    assert pos.quantity == Decimal("50")
    assert pos.average_entry_price == Decimal("28.00")
    assert res.total_realized_pnl == Decimal("-200.00")


def test_point_in_time_and_market_prices(replay_setup):
    engine, exec_repo, price_repo = replay_setup

    t0 = datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 10, 15, 0, tzinfo=timezone.utc)

    # Execuções: Compra em T0, Venda futura em T2
    e1 = Execution(execution_id="e1", trader_id="T001", symbol="PETR4", side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00"), timestamp=t0)
    e2_future = Execution(execution_id="e2_fut", trader_id="T001", symbol="PETR4", side=OrderSide.SELL, quantity=Decimal("100"), price=Decimal("40.00"), timestamp=t2)
    exec_repo.insert_batch([e1, e2_future])

    # Cotações de mercado: T1 = 34.00, T2 = 40.00
    price_repo.insert_batch([
        MarketPriceRecord(symbol="PETR4", timestamp=t1, price=Decimal("34.00")),
        MarketPriceRecord(symbol="PETR4", timestamp=t2, price=Decimal("40.00")),
    ])

    # Replay no instante T1:
    # Deve enxergar apenas a compra de T0 e a cotação de T1 (34.00).
    # A posição está aberta (100 ações), unrealized P&L = (34 - 30)*100 = 400.
    # A execução de T2 e o preço de T2 são estritamente invisíveis.
    res_t1 = engine.replay_trader("T001", as_of=t1)
    assert len(res_t1.closed_trades) == 0
    assert res_t1.positions["PETR4"].quantity == Decimal("100")
    assert res_t1.total_unrealized_pnl == Decimal("400.00")
    assert res_t1.total_equity == Decimal("10400.00")

    # Replay no instante T2:
    # Agora a venda futura foi executada, posição zerada e lucro realizado de 1000.
    res_t2 = engine.replay_trader("T001", as_of=t2)
    assert len(res_t2.closed_trades) == 1
    assert res_t2.positions["PETR4"].quantity == Decimal("0.0")
    assert res_t2.total_realized_pnl == Decimal("1000.00")
    assert res_t2.total_unrealized_pnl == Decimal("0.0")
    assert res_t2.total_equity == Decimal("11000.00")


def test_replay_determinism(replay_setup):
    engine, exec_repo, _ = replay_setup
    
    # Insere 10 execuções
    execs = []
    base_t = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    for i in range(5):
        t_in = datetime.fromtimestamp(base_t.timestamp() + i * 7200, tz=timezone.utc)
        t_out = datetime.fromtimestamp(base_t.timestamp() + i * 7200 + 3600, tz=timezone.utc)
        execs.append(Execution(execution_id=f"in_{i}", trader_id="T001", symbol="PETR4", side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00"), timestamp=t_in))
        execs.append(Execution(execution_id=f"out_{i}", trader_id="T001", symbol="PETR4", side=OrderSide.SELL, quantity=Decimal("100"), price=Decimal("32.00"), timestamp=t_out))
    
    exec_repo.insert_batch(execs)

    as_of = datetime(2026, 1, 5, tzinfo=timezone.utc)
    res_1 = engine.replay_trader("T001", as_of=as_of)
    res_2 = engine.replay_trader("T001", as_of=as_of)

    assert res_1.total_realized_pnl == res_2.total_realized_pnl == Decimal("1000.00")
    assert res_1.total_equity == res_2.total_equity == Decimal("11000.00")
    assert res_1.performance == res_2.performance
    assert res_1.score == res_2.score


def test_replay_missing_market_price_does_not_invent_valuation(replay_setup):
    engine, exec_repo, price_repo = replay_setup

    t0 = datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc)
    as_of = datetime(2026, 1, 10, 14, 0, tzinfo=timezone.utc)

    # 1. Abre posição de 100 @ 30.00
    e1 = Execution(
        execution_id="e_open",
        trader_id="T001",
        symbol="PETR4",
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        price=Decimal("30.00"),
        timestamp=t0
    )
    exec_repo.insert(e1)

    # NENHUMA cotação é inserida no price_repo

    # 2. Executa Replay em as_of (com posição aberta mas sem cotação de mercado)
    res = engine.replay_trader("T001", as_of=as_of)

    assert res.positions["PETR4"].quantity == Decimal("100")
    # Comprova que o sistema não inventa cotação nem calcula unrealized P&L artificial de zero
    assert res.valuation_status == "MISSING_MARKET_PRICE"
    assert res.total_unrealized_pnl is None
    assert res.total_equity is None
    assert res.realized_equity == Decimal("10000.00")
    assert res.total_realized_pnl == Decimal("0.0")
