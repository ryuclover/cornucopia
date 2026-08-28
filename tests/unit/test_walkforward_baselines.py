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


def setup_baselines_environment():
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
        end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        baseline_modes=[BaselineMode.EQUAL_WEIGHT, BaselineMode.SIMPLE_MAJORITY, BaselineMode.QUALITY_ONLY]
    )
    b_engine = BaselineEngine(sig_engine, cons_engine, price_repo, cfg)

    return trader_repo, exec_repo, price_repo, b_engine, cfg, cons_engine, sig_engine


def test_baseline_generation_and_comparisons():
    """
    Testa geração e comparação de baselines sobre as mesmas decisões congeladas.
    """
    trader_repo, exec_repo, price_repo, b_engine, cfg, cons_engine, sig_engine = setup_baselines_environment()
    as_of = datetime(2026, 1, 31, tzinfo=timezone.utc)

    # 4 Traders selecionados
    for tid in ["T1", "T2", "T3", "T4"]:
        trader_repo.save(Trader(trader_id=tid, name=tid, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    # T1, T2 abrem LONG em PETR4
    exec_repo.insert(Execution(execution_id="E1", trader_id="T1", symbol="PETR4", timestamp=datetime(2026, 1, 10, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))
    exec_repo.insert(Execution(execution_id="E2", trader_id="T2", symbol="PETR4", timestamp=datetime(2026, 1, 10, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))
    # T3 abre SHORT
    exec_repo.insert(Execution(execution_id="E3", trader_id="T3", symbol="PETR4", timestamp=datetime(2026, 1, 10, tzinfo=timezone.utc), side=OrderSide.SELL, quantity=Decimal("100"), price=Decimal("30.00")))
    # T4 FLAT

    # Preços para simulação
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=as_of, price=Decimal("30.00")))
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 2, 28, tzinfo=timezone.utc), price=Decimal("33.00")))

    cornucopia_dec = WalkForwardDecision(
        decision_id="D1",
        decision_as_of=as_of,
        symbol="PETR4",
        selected_trader_ids=["T1", "T2", "T3", "T4"],
        selected_core_count=4,
        trader_weights={"T1": 0.25, "T2": 0.25, "T3": 0.25, "T4": 0.25},
        consensus_direction=ConsensusDirection.NO_CONSENSUS
    )

    as_of_2 = datetime(2026, 2, 28, tzinfo=timezone.utc)
    cornucopia_dec_2 = WalkForwardDecision(
        decision_id="D2",
        decision_as_of=as_of_2,
        symbol="PETR4",
        selected_trader_ids=["T1", "T2", "T3", "T4"],
        selected_core_count=4,
        trader_weights={"T1": 0.25, "T2": 0.25, "T3": 0.25, "T4": 0.25},
        consensus_direction=ConsensusDirection.NO_CONSENSUS
    )

    sim = ConsensusShadowStrategySimulator(price_repo, cfg.friction)
    c_shadow = sim.simulate_shadow_strategy("PETR4", [cornucopia_dec, cornucopia_dec_2])

    # 1. Equal Weight Comparison
    comp_eq = b_engine.compare_with_baseline(BaselineMode.EQUAL_WEIGHT, [cornucopia_dec, cornucopia_dec_2], c_shadow)
    assert comp_eq.baseline_mode == BaselineMode.EQUAL_WEIGHT
    assert comp_eq.decision_count == 2

    # 2. Simple Majority Comparison (2 LONG vs 1 SHORT -> LONG)
    comp_maj = b_engine.compare_with_baseline(BaselineMode.SIMPLE_MAJORITY, [cornucopia_dec, cornucopia_dec_2], c_shadow)
    assert comp_maj.baseline_mode == BaselineMode.SIMPLE_MAJORITY
    assert comp_maj.decision_count == 2
    # Simple Majority comprou (+10%), Cornucopia ficou em NO_CONSENSUS (0%)
    assert comp_maj.baseline_net_return > comp_maj.cornucopia_net_return
