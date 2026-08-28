from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.domain.enums import AssetClass, OrderSide, PositionSide
from src.domain.execution import Execution
from src.domain.instrument import MarketInstrument
from src.domain.position import PositionTracker


@pytest.fixture
def petr4_instrument():
    return MarketInstrument(
        symbol="PETR4",
        asset_class=AssetClass.EQUITY,
        tick_size=Decimal("0.01"),
        contract_multiplier=Decimal("1.0")
    )


def test_single_long_trade(petr4_instrument):
    tracker = PositionTracker(instrument=petr4_instrument, trader_id="T1")
    
    # 1. Compra 100 PETR4 @ 30.00
    e1 = Execution(
        execution_id="e1",
        trader_id="T1",
        symbol="PETR4",
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        price=Decimal("30.00"),
        timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc),
        commission=Decimal("2.50")
    )
    closed = tracker.process_execution(e1)
    assert len(closed) == 0
    assert tracker.position.side == PositionSide.LONG
    assert tracker.position.quantity == Decimal("100")
    assert tracker.position.average_entry_price == Decimal("30.00")

    # 2. Vende 100 PETR4 @ 33.00
    e2 = Execution(
        execution_id="e2",
        trader_id="T1",
        symbol="PETR4",
        side=OrderSide.SELL,
        quantity=Decimal("100"),
        price=Decimal("33.00"),
        timestamp=datetime(2026, 1, 10, 15, 0, tzinfo=timezone.utc),
        commission=Decimal("2.50")
    )
    closed = tracker.process_execution(e2)
    assert len(closed) == 1
    trade = closed[0]
    assert trade.quantity == Decimal("100")
    assert trade.gross_pnl == Decimal("300.00")  # (33-30)*100
    assert trade.commission == Decimal("5.00")   # 2.50 + 2.50
    assert trade.net_pnl == Decimal("295.00")
    assert tracker.position.side == PositionSide.FLAT
    assert tracker.position.quantity == Decimal("0.0")


def test_scale_in_and_fifo_scale_out(petr4_instrument):
    tracker = PositionTracker(instrument=petr4_instrument, trader_id="T1")
    
    # Compra Lote 1: 100 @ 30.00
    e1 = Execution(
        execution_id="e1",
        trader_id="T1",
        symbol="PETR4",
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        price=Decimal("30.00"),
        timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc),
        commission=Decimal("2.00")
    )
    tracker.process_execution(e1)

    # Compra Lote 2 (Scale-in): 100 @ 32.00
    e2 = Execution(
        execution_id="e2",
        trader_id="T1",
        symbol="PETR4",
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        price=Decimal("32.00"),
        timestamp=datetime(2026, 1, 10, 11, 0, tzinfo=timezone.utc),
        commission=Decimal("2.00")
    )
    tracker.process_execution(e2)

    assert tracker.position.quantity == Decimal("200")
    assert tracker.position.average_entry_price == Decimal("31.00")

    # Saída parcial 1 (Scale-out): Vende 150 @ 35.00
    # FIFO: deve fechar 100 do Lote 1 (@ 30.00) e 50 do Lote 2 (@ 32.00)
    e3 = Execution(
        execution_id="e3",
        trader_id="T1",
        symbol="PETR4",
        side=OrderSide.SELL,
        quantity=Decimal("150"),
        price=Decimal("35.00"),
        timestamp=datetime(2026, 1, 10, 14, 0, tzinfo=timezone.utc),
        commission=Decimal("3.00")  # 0.02 / unidade
    )
    closed = tracker.process_execution(e3)
    assert len(closed) == 2
    
    t1 = closed[0]
    assert t1.quantity == Decimal("100")
    assert t1.entry_price == Decimal("30.00")
    assert t1.gross_pnl == Decimal("500.00")  # (35-30)*100

    t2 = closed[1]
    assert t2.quantity == Decimal("50")
    assert t2.entry_price == Decimal("32.00")
    assert t2.gross_pnl == Decimal("150.00")  # (35-32)*50

    # Sobram 50 unidades do Lote 2 (@ 32.00)
    assert tracker.position.quantity == Decimal("50")
    assert tracker.position.average_entry_price == Decimal("32.00")
    assert tracker.position.side == PositionSide.LONG


def test_position_reversal(petr4_instrument):
    tracker = PositionTracker(instrument=petr4_instrument, trader_id="T1")

    # Compra 100 @ 30.00
    e1 = Execution(
        execution_id="e1",
        trader_id="T1",
        symbol="PETR4",
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        price=Decimal("30.00"),
        timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc),
        commission=Decimal("1.00")
    )
    tracker.process_execution(e1)

    # Vende 150 @ 28.00 (Stop e vira a mão para Short de 50)
    e2 = Execution(
        execution_id="e2",
        trader_id="T1",
        symbol="PETR4",
        side=OrderSide.SELL,
        quantity=Decimal("150"),
        price=Decimal("28.00"),
        timestamp=datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc),
        commission=Decimal("1.50")
    )
    closed = tracker.process_execution(e2)
    assert len(closed) == 1
    trade = closed[0]
    assert trade.quantity == Decimal("100")
    assert trade.gross_pnl == Decimal("-200.00")
    assert trade.side == PositionSide.LONG

    # Agora a posição deve estar SHORT de 50 @ 28.00
    assert tracker.position.side == PositionSide.SHORT
    assert tracker.position.quantity == Decimal("50")
    assert tracker.position.average_entry_price == Decimal("28.00")


def test_chronological_order_enforcement(petr4_instrument):
    tracker = PositionTracker(instrument=petr4_instrument, trader_id="T1")
    e1 = Execution(
        execution_id="e1",
        trader_id="T1",
        symbol="PETR4",
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        price=Decimal("30.00"),
        timestamp=datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
    )
    tracker.process_execution(e1)

    # Execução anterior à última deve ser rejeitada
    e2 = Execution(
        execution_id="e2",
        trader_id="T1",
        symbol="PETR4",
        side=OrderSide.SELL,
        quantity=Decimal("100"),
        price=Decimal("31.00"),
        timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc)
    )
    with pytest.raises(ValueError, match="fora de ordem temporal"):
        tracker.process_execution(e2)


def test_intratrade_mae_mfe_and_equity_snapshots(petr4_instrument):
    tracker = PositionTracker(instrument=petr4_instrument, trader_id="T1", initial_capital=Decimal("10000.00"))
    
    # Compra 100 @ 30.00
    e1 = Execution(
        execution_id="e1",
        trader_id="T1",
        symbol="PETR4",
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        price=Decimal("30.00"),
        timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc)
    )
    tracker.process_execution(e1)

    # Preço cai para 27.00 durante a operação (adverse excursion)
    snap1 = tracker.mark_market_price(Decimal("27.00"), datetime(2026, 1, 10, 11, 0, tzinfo=timezone.utc))
    assert snap1.unrealized_pnl == Decimal("-300.00")
    assert snap1.total_equity == Decimal("9700.00")

    # Preço sobe para 35.00 durante a operação (favorable excursion)
    snap2 = tracker.mark_market_price(Decimal("35.00"), datetime(2026, 1, 10, 13, 0, tzinfo=timezone.utc))
    assert snap2.unrealized_pnl == Decimal("500.00")
    assert snap2.total_equity == Decimal("10500.00")

    # Encerra a 33.00
    e2 = Execution(
        execution_id="e2",
        trader_id="T1",
        symbol="PETR4",
        side=OrderSide.SELL,
        quantity=Decimal("100"),
        price=Decimal("33.00"),
        timestamp=datetime(2026, 1, 10, 15, 0, tzinfo=timezone.utc)
    )
    closed = tracker.process_execution(e2)
    assert len(closed) == 1
    trade = closed[0]
    assert trade.net_pnl == Decimal("300.00")
    assert trade.max_adverse_excursion == Decimal("-300.00")  # (27 - 30) * 100
    assert trade.max_favorable_excursion == Decimal("500.00") # (35 - 30) * 100
