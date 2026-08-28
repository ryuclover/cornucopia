from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.evaluation_config import EvaluationFrequency
from src.domain.enums import AssetClass, OrderSide
from src.domain.execution import Execution
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.portfolio.service import TraderVirtualPortfolioService
from src.replay.engine import TraderReplayEngine
from src.storage.database import DatabaseManager
from src.storage.repositories.executions import SQLiteExecutionRepository
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.storage.repositories.traders import SQLiteTraderRepository


@pytest.fixture
def portfolio_setup():
    db = DatabaseManager(db_path=":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    petr4 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY)
    inst_repo.save(petr4)

    trader = Trader(trader_id="T001", name="Portfolio Trader", initial_capital=Decimal("10000.00"))
    trader_repo.save(trader)

    replay_engine = TraderReplayEngine(
        trader_repo=trader_repo,
        instrument_repo=inst_repo,
        execution_repo=exec_repo,
        market_price_repo=price_repo
    )
    service = TraderVirtualPortfolioService(replay_engine)
    return service, exec_repo


def test_virtual_portfolio_and_peak_equity(portfolio_setup):
    service, exec_repo = portfolio_setup

    # Trade 1: Ganho de 500 (Equity vai para 10500)
    e1 = Execution(execution_id="e1", trader_id="T001", symbol="PETR4", side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00"), timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc))
    e2 = Execution(execution_id="e2", trader_id="T001", symbol="PETR4", side=OrderSide.SELL, quantity=Decimal("100"), price=Decimal("35.00"), timestamp=datetime(2026, 1, 10, 15, 0, tzinfo=timezone.utc))
    # Trade 2: Perda de 200 (Equity vai para 10300)
    e3 = Execution(execution_id="e3", trader_id="T001", symbol="PETR4", side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("35.00"), timestamp=datetime(2026, 1, 12, 10, 0, tzinfo=timezone.utc))
    e4 = Execution(execution_id="e4", trader_id="T001", symbol="PETR4", side=OrderSide.SELL, quantity=Decimal("100"), price=Decimal("33.00"), timestamp=datetime(2026, 1, 12, 15, 0, tzinfo=timezone.utc))

    exec_repo.insert_batch([e1, e2, e3, e4])

    as_of = datetime(2026, 1, 15, tzinfo=timezone.utc)
    port = service.get_portfolio("T001", as_of=as_of)

    assert port.trader_id == "T001"
    assert port.realized_equity == Decimal("10300.00")
    assert port.realized_pnl == Decimal("300.00")
    assert port.peak_equity == Decimal("10500.00")
    assert port.drawdown_pct > 0.0  # (10500 - 10300) / 10500 ~= 1.9%
    assert len(port.closed_trades) == 2


def test_virtual_portfolio_equity_series(portfolio_setup):
    service, exec_repo = portfolio_setup

    e1 = Execution(execution_id="e1", trader_id="T001", symbol="PETR4", side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00"), timestamp=datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc))
    e2 = Execution(execution_id="e2", trader_id="T001", symbol="PETR4", side=OrderSide.SELL, quantity=Decimal("100"), price=Decimal("32.00"), timestamp=datetime(2026, 1, 8, 15, 0, tzinfo=timezone.utc))
    exec_repo.insert_batch([e1, e2])

    series = service.generate_equity_series(
        trader_id="T001",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        frequency=EvaluationFrequency.DAILY
    )

    assert len(series) >= 10
    # Antes do trade (Jan 1 a Jan 4), equity = 10000
    assert series[0].realized_equity == Decimal("10000.00")
    # Após o trade (Jan 9), equity = 10200
    assert series[-1].realized_equity == Decimal("10200.00")
