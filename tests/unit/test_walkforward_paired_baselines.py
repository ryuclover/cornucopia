from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.walkforward_config import BaselineMode, WalkForwardConfig
from src.consensus.engine import ConsensusEngine
from src.consensus.models import ConsensusDirection
from src.domain.enums import AssetClass, OrderSide, TraderStatus
from src.domain.execution import Execution
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.replay.engine import TraderReplayEngine
from src.signals.engine import TraderSignalEngine
from src.storage.database import DatabaseManager as SQLiteDatabaseManager
from src.storage.repositories.base import MarketPriceRecord
from src.storage.repositories.executions import SQLiteExecutionRepository
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.storage.repositories.traders import SQLiteTraderRepository
from src.walkforward.baselines import BaselineEngine
from src.walkforward.models import WalkForwardDecision
from src.walkforward.simulator import ConsensusShadowStrategySimulator


def setup_baselines_env():
    db = SQLiteDatabaseManager(":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    petr4 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    inst_repo.save(petr4)

    replay = TraderReplayEngine(trader_repo, inst_repo, exec_repo, price_repo)
    sig_engine = TraderSignalEngine(replay)
    cons_engine = ConsensusEngine(sig_engine)
    cfg = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 31, tzinfo=timezone.utc)
    )
    b_engine = BaselineEngine(sig_engine, cons_engine, price_repo, cfg)

    return trader_repo, exec_repo, price_repo, b_engine, cfg, cons_engine, sig_engine


def test_three_views_and_missing_data_parity():
    """
    Testa:
    1. Native Strategy Performance preserva abstenções e características próprias de cada política;
    2. Common Opportunity Set remove oportunidades onde um dos lados não tem outcome válido (missing-data parity);
    3. Common Directional Decision isola apenas onde ambos resolveram agir direcionalmente.
    """
    trader_repo, exec_repo, price_repo, b_engine, cfg, cons_engine, sig_engine = setup_baselines_env()

    for tid in ["T1", "T2", "T3"]:
        trader_repo.save(Trader(trader_id=tid, name=tid, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    # Execuções: T1 LONG, T2 LONG, T3 SHORT
    exec_repo.insert(Execution(execution_id="E1", trader_id="T1", symbol="PETR4", timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))
    exec_repo.insert(Execution(execution_id="E2", trader_id="T2", symbol="PETR4", timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))
    exec_repo.insert(Execution(execution_id="E3", trader_id="T3", symbol="PETR4", timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc), side=OrderSide.SELL, quantity=Decimal("100"), price=Decimal("30.00")))

    # Preços válidos apenas para D1 e D1+5d, mas faltando preço futuro para D2
    d1 = datetime(2026, 1, 10, tzinfo=timezone.utc)
    d2 = datetime(2026, 2, 10, tzinfo=timezone.utc)

    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=d1, price=Decimal("30.00")))
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 15, tzinfo=timezone.utc), price=Decimal("33.00")))
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=d2, price=Decimal("33.00")))
    # Sem preço para d2 + 5d

    corn_dec_1 = WalkForwardDecision(
        decision_id="D1",
        decision_as_of=d1,
        symbol="PETR4",
        selected_trader_ids=["T1", "T2", "T3"],
        selected_core_count=3,
        consensus_direction=ConsensusDirection.LONG
    )
    corn_dec_2 = WalkForwardDecision(
        decision_id="D2",
        decision_as_of=d2,
        symbol="PETR4",
        selected_trader_ids=["T1", "T2", "T3"],
        selected_core_count=3,
        consensus_direction=ConsensusDirection.NO_CONSENSUS  # Cornucopia absteve aqui
    )

    sim = ConsensusShadowStrategySimulator(price_repo, cfg.friction)
    c_shadow = sim.simulate_shadow_strategy("PETR4", [corn_dec_1, corn_dec_2])

    comp = b_engine.compare_with_baseline(BaselineMode.SIMPLE_MAJORITY, [corn_dec_1, corn_dec_2], c_shadow, default_horizon_days=5)

    # 1. Native Strategy Performance
    assert "cumulative_net_return_pct" in comp.native_cornucopia
    assert "cumulative_net_return_pct" in comp.native_baseline

    # 2. Missing-Data Parity: D2 não tinha preço futuro, logo foi removida de ambos no Common Opportunity
    assert comp.common_opportunity_count == 1
    assert comp.missing_data_removed_count == 1

    # 3. Common Directional: em D1 ambos foram LONG; em D2 Cornucopia absteve
    assert comp.common_directional_count == 1
    assert comp.common_directional_metrics is not None
    assert comp.common_directional_metrics["cornucopia_hit_rate_pct"] == 100.0
