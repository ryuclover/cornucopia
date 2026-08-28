from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.domain.enums import PositionSide
from src.domain.trade import ClosedTrade
from src.metrics.calculator import PerformanceCalculator


def test_point_in_time_guarantees_no_look_ahead_bias():
    initial_capital = Decimal("10000.00")
    
    # Criamos trades em Janeiro e Fevereiro de 2026
    trades = [
        ClosedTrade(
            trade_id="t1",
            trader_id="T1",
            symbol="PETR4",
            side=PositionSide.LONG,
            quantity=Decimal("100"),
            entry_price=Decimal("30.00"),
            exit_price=Decimal("32.00"),
            entry_time=datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc),
            exit_time=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
            gross_pnl=Decimal("200.00"),
            commission=Decimal("0.0"),
            net_pnl=Decimal("200.00"),
            return_pct=Decimal("0.0667")
        ),
        ClosedTrade(
            trade_id="t2",
            trader_id="T1",
            symbol="PETR4",
            side=PositionSide.LONG,
            quantity=Decimal("100"),
            entry_price=Decimal("32.00"),
            exit_price=Decimal("34.00"),
            entry_time=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
            exit_time=datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc),
            gross_pnl=Decimal("200.00"),
            commission=Decimal("0.0"),
            net_pnl=Decimal("200.00"),
            return_pct=Decimal("0.0625")
        ),
        # Trade futuro que teve uma perda gigante em Fevereiro
        ClosedTrade(
            trade_id="t3_future_loss",
            trader_id="T1",
            symbol="PETR4",
            side=PositionSide.LONG,
            quantity=Decimal("100"),
            entry_price=Decimal("34.00"),
            exit_price=Decimal("20.00"),
            entry_time=datetime(2026, 2, 10, 10, 0, tzinfo=timezone.utc),
            exit_time=datetime(2026, 2, 10, 15, 0, tzinfo=timezone.utc),
            gross_pnl=Decimal("-1400.00"),
            commission=Decimal("0.0"),
            net_pnl=Decimal("-1400.00"),
            return_pct=Decimal("-0.4117")
        ),
    ]

    as_of_january = datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

    # 1. Calculando com o dataset completo incluindo fevereiro
    perf_with_full_dataset = PerformanceCalculator.calculate(
        trader_id="T1",
        trades=trades,
        as_of=as_of_january,
        initial_capital=initial_capital
    )

    # 2. Calculando apenas com a lista de janeiro
    perf_only_january = PerformanceCalculator.calculate(
        trader_id="T1",
        trades=trades[:2],
        as_of=as_of_january,
        initial_capital=initial_capital
    )

    # Devem ser exatamente idênticos, garantindo 0 data leakage e 0 look-ahead bias
    assert perf_with_full_dataset.total_trades == 2
    assert perf_with_full_dataset.net_pnl == Decimal("400.00")
    assert perf_with_full_dataset.win_rate == 1.0
    assert perf_with_full_dataset.max_drawdown_amount == Decimal("0.0")
    assert perf_with_full_dataset == perf_only_january

    # Se movermos o as_of para o final de Fevereiro, o trade futuro entra em vigor
    as_of_february = datetime(2026, 2, 28, 23, 59, 59, tzinfo=timezone.utc)
    perf_feb = PerformanceCalculator.calculate(
        trader_id="T1",
        trades=trades,
        as_of=as_of_february,
        initial_capital=initial_capital
    )
    assert perf_feb.total_trades == 3
    assert perf_feb.net_pnl == Decimal("-1000.00")
    assert perf_feb.max_drawdown_amount == Decimal("1400.00")


def test_point_in_time_mark_to_market_isolation():
    from src.domain.enums import AssetClass, OrderSide
    from src.domain.execution import Execution
    from src.domain.instrument import MarketInstrument
    from src.domain.position import PositionTracker

    instrument = MarketInstrument(
        symbol="PETR4",
        asset_class=AssetClass.EQUITY,
        tick_size=Decimal("0.01"),
        contract_multiplier=Decimal("1.0")
    )
    initial_cap = Decimal("10000.00")
    tracker = PositionTracker(instrument=instrument, trader_id="T1", initial_capital=initial_cap)

    t0 = datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 10, 15, 0, tzinfo=timezone.utc)

    # 1. Abre posição LONG de 100 @ 30.00 em T0
    e_open = Execution(
        execution_id="e_open",
        trader_id="T1",
        symbol="PETR4",
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        price=Decimal("30.00"),
        timestamp=t0
    )
    tracker.process_execution(e_open)

    # 2. Registra market price em T1: Preço sobe para 35.00 (+500)
    tracker.mark_market_price(current_price=Decimal("35.00"), timestamp=t1)

    # 3. Registra market price em T2: Preço despenca para 20.00 (-1000)
    tracker.mark_market_price(current_price=Decimal("20.00"), timestamp=t2)

    # 4. Consulta ponto-no-tempo com as_of = T1
    mtm_t1 = tracker.calculate_drawdown_as_of(as_of=t1)
    snaps_t1 = tracker.get_equity_snapshots_as_of(as_of=t1)

    # Comprova que T2 tem ZERO impacto nos resultados em T1
    assert len(snaps_t1) == 2  # t0 (abertura) e t1 (marcação 35.00)
    assert mtm_t1["current_unrealized_pnl"] == Decimal("500.00")
    assert mtm_t1["current_equity"] == Decimal("10500.00")
    assert mtm_t1["max_drawdown_amount"] == Decimal("0.0")
    assert mtm_t1["max_drawdown_pct"] == 0.0

    # 5. Consulta ponto-no-tempo com as_of = T2 (agora a queda é refletida)
    mtm_t2 = tracker.calculate_drawdown_as_of(as_of=t2)
    snaps_t2 = tracker.get_equity_snapshots_as_of(as_of=t2)

    assert len(snaps_t2) == 3
    assert mtm_t2["current_unrealized_pnl"] == Decimal("-1000.00")
    assert mtm_t2["current_equity"] == Decimal("9000.00")
    # Drawdown de pico a vale: de 10500 para 9000 = 1500 (14.28%)
    assert mtm_t2["max_drawdown_amount"] == Decimal("1500.00")
    assert pytest.approx(mtm_t2["max_drawdown_pct"], 0.01) == 14.2857
