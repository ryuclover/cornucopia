from datetime import datetime, timedelta, timezone
from decimal import Decimal
from src.dependence.engine import TraderDependenceEngine
from src.domain.enums import AssetClass, OrderSide, TraderStatus
from src.domain.execution import Execution
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.replay.engine import TraderReplayEngine
from src.storage.database import DatabaseManager as SQLiteDatabaseManager
from src.storage.repositories import (
    SQLiteExecutionRepository,
    SQLiteInstrumentRepository,
    SQLiteMarketPriceRepository,
    SQLiteTraderRepository,
)
from src.synthetic.generator import SyntheticDataGenerator


def setup_engine(db_path: str = ":memory:"):
    db = SQLiteDatabaseManager(db_path)
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)
    replay = TraderReplayEngine(trader_repo, inst_repo, exec_repo, price_repo)
    engine = TraderDependenceEngine(replay_engine=replay)
    return db, trader_repo, inst_repo, exec_repo, replay, engine


def test_dependence_point_in_time_future_insulation():
    _, trader_repo, inst_repo, exec_repo, _, engine = setup_engine()
    
    inst = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    inst_repo.save(inst)

    t1 = Trader(trader_id="T1", name="Alpha", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00"))
    t2 = Trader(trader_id="T2", name="Beta", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00"))
    trader_repo.save(t1)
    trader_repo.save(t2)

    gen = SyntheticDataGenerator(seed=42)
    cutoff = datetime(2026, 1, 31, tzinfo=timezone.utc)
    # Operações passadas (janeiro)
    execs_past_t1 = gen.generate_executions_for_trader("T1", "PETR4", trade_count=10, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_past_t2 = gen.generate_profile_mirror_trader(execs_past_t1, "T2", time_shift_seconds=60)

    for e in execs_past_t1 + execs_past_t2:
        exec_repo.insert(e)

    # Calcula dependência em cutoff (janeiro)
    dep_before = engine.analyze_pair("T1", "T2", as_of=cutoff)

    # Agora insere operações futuras (a partir de março de 2026) com comportamentos divergentes
    future_start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    execs_future_t1 = gen.generate_executions_for_trader("T1", "PETR4", trade_count=20, start_date=future_start)
    execs_future_t2 = gen.generate_profile_anti_correlated(execs_future_t1, "T2")

    for e in execs_future_t1 + execs_future_t2:
        exec_repo.insert(e)

    # Recalcula dependência com as_of = cutoff (passado)
    dep_after = engine.analyze_pair("T1", "T2", as_of=cutoff)

    # Prova que o futuro não vazou para o passado
    assert dep_before.composite_redundancy_score == dep_after.composite_redundancy_score
    assert dep_before.overlap_periods == dep_after.overlap_periods
    assert dep_before.return_correlation == dep_after.return_correlation
    assert dep_before.directional_agreement == dep_after.directional_agreement


def test_dependence_determinism():
    _, trader_repo, inst_repo, exec_repo, _, engine = setup_engine()
    
    inst = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    inst_repo.save(inst)

    t1 = Trader(trader_id="T1", name="Alpha", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00"))
    t2 = Trader(trader_id="T2", name="Beta", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00"))
    trader_repo.save(t1)
    trader_repo.save(t2)

    gen = SyntheticDataGenerator(seed=99)
    execs_t1 = gen.generate_executions_for_trader("T1", "PETR4", trade_count=30, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_t2 = gen.generate_executions_for_trader("T2", "PETR4", trade_count=30, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))

    for e in execs_t1 + execs_t2:
        exec_repo.insert(e)

    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)
    res1 = engine.analyze_pair("T1", "T2", as_of=as_of)
    res2 = engine.analyze_pair("T1", "T2", as_of=as_of)

    assert res1.composite_redundancy_score == res2.composite_redundancy_score
    assert res1.return_correlation == res2.return_correlation
    assert res1.directional_agreement == res2.directional_agreement
    assert res1.dependence_level == res2.dependence_level
