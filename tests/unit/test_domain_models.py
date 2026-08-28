from datetime import datetime, timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError
from src.domain.enums import AssetClass, OrderSide, PositionSide, TraderStatus
from src.domain.execution import Execution
from src.domain.instrument import MarketInstrument
from src.domain.trade import ClosedTrade
from src.domain.trader import Trader


def test_market_instrument_pnl_calculation():
    # Ações normais (multiplicador 1.0)
    petr4 = MarketInstrument(
        symbol="PETR4",
        asset_class=AssetClass.EQUITY,
        tick_size=Decimal("0.01"),
        contract_multiplier=Decimal("1.0")
    )
    pnl_long = petr4.calculate_pnl(
        quantity=Decimal("100"),
        entry_price=Decimal("30.00"),
        exit_price=Decimal("32.50"),
        is_long=True
    )
    assert pnl_long == Decimal("250.00")

    # Mini Índice WIN (multiplicador 0.20 por ponto)
    win = MarketInstrument(
        symbol="WIN$",
        asset_class=AssetClass.FUTURES,
        tick_size=Decimal("5.0"),
        contract_multiplier=Decimal("0.20")
    )
    pnl_short = win.calculate_pnl(
        quantity=Decimal("2"),
        entry_price=Decimal("120000"),
        exit_price=Decimal("119500"),
        is_long=False
    )
    # 500 pontos * 2 contratos * 0.20 = R$ 200.00
    assert pnl_short == Decimal("200.00")


def test_execution_immutability_and_validation():
    exec1 = Execution(
        execution_id="exec-001",
        trader_id="trader-alpha",
        symbol="PETR4",
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        price=Decimal("35.50"),
        timestamp=datetime(2026, 1, 15, 13, 0, 0, tzinfo=timezone.utc),
        commission=Decimal("5.00")
    )
    # Imutabilidade
    with pytest.raises(ValidationError):
        exec1.price = Decimal("36.00")  # type: ignore

    # Quantidade negativa proibida
    with pytest.raises(ValidationError):
        Execution(
            execution_id="exec-002",
            trader_id="trader-alpha",
            symbol="PETR4",
            side=OrderSide.BUY,
            quantity=Decimal("-10"),
            price=Decimal("35.50"),
            timestamp=datetime(2026, 1, 15, 13, 0, 0, tzinfo=timezone.utc)
        )


def test_closed_trade_properties():
    trade = ClosedTrade(
        trade_id="tr-01",
        trader_id="trader-alpha",
        symbol="PETR4",
        side=PositionSide.LONG,
        quantity=Decimal("100"),
        entry_price=Decimal("30.00"),
        exit_price=Decimal("32.00"),
        entry_time=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc),
        exit_time=datetime(2026, 1, 10, 15, 0, tzinfo=timezone.utc),
        gross_pnl=Decimal("200.00"),
        commission=Decimal("10.00"),
        net_pnl=Decimal("190.00"),
        return_pct=Decimal("0.0633")
    )
    assert trade.is_win is True
    assert trade.is_loss is False
    assert trade.is_scratch is False
    assert trade.duration_seconds == 5 * 3600
