from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.weight_config import WeightConfig
from src.dependence.engine import TraderDependenceEngine
from src.domain.enums import AssetClass, TraderStatus
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.evaluation.engine import TraderEvaluationEngine
from src.replay.engine import TraderReplayEngine
from src.storage.database import DatabaseManager as SQLiteDatabaseManager
from src.storage.repositories import (
    SQLiteExecutionRepository,
    SQLiteInstrumentRepository,
    SQLiteMarketPriceRepository,
    SQLiteTraderRepository,
)
from src.synthetic.generator import SyntheticDataGenerator
from src.weighting.engine import TraderWeightEngine
from src.weighting.models import InfeasibleWeightConstraintsError


def setup_weight_engine():
    db = SQLiteDatabaseManager(":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    petr4 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    vale3 = MarketInstrument(symbol="VALE3", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    inst_repo.save(petr4)
    inst_repo.save(vale3)

    replay = TraderReplayEngine(trader_repo, inst_repo, exec_repo, price_repo)
    eval_engine = TraderEvaluationEngine(replay)
    dep_engine = TraderDependenceEngine(replay)
    weight_engine = TraderWeightEngine(
        evaluation_engine=eval_engine,
        dependence_engine=dep_engine
    )
    return trader_repo, exec_repo, weight_engine


def test_two_traders_with_30_percent_cap_raises_infeasible_error():
    """
    Hardening 2 (Caso 1): 2 traders com cap de 30% -> capacidade máxima = 60% < 100%.
    Não pode violar silenciosamente o cap; deve levantar InfeasibleWeightConstraintsError.
    """
    trader_repo, exec_repo, weight_engine = setup_weight_engine()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    for tid in ["T1", "T2"]:
        trader_repo.save(Trader(trader_id=tid, name=tid, created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    gen = SyntheticDataGenerator(seed=42)
    start_dt = datetime(2026, 1, 5, tzinfo=timezone.utc)
    for e in gen.generate_profile_steady_survivor("T1", symbol="PETR4", start_date=start_dt, trade_count=30):
        exec_repo.insert(e)
    for e in gen.generate_profile_steady_survivor("T2", symbol="VALE3", start_date=start_dt, trade_count=30):
        exec_repo.insert(e)

    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)
    cfg = WeightConfig(maximum_trader_weight=0.30)

    with pytest.raises(InfeasibleWeightConstraintsError) as exc_info:
        weight_engine.calculate_diagnostic_weights(as_of=as_of, trader_ids=["T1", "T2"], config=cfg)

    err = exc_info.value
    assert err.constraint_cause == "maximum_trader_weight"
    assert err.maximum_possible_weight == pytest.approx(0.60, abs=1e-4)
    assert err.required_total_weight == 1.0


def test_four_traders_with_30_percent_cap_is_feasible():
    """
    Hardening 2 (Caso 2): 4 traders com cap de 30% -> capacidade máxima = 120% >= 100%.
    Configuração viável que produz pesos com soma 1.0 e nenhum trader > 30%.
    """
    trader_repo, exec_repo, weight_engine = setup_weight_engine()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t_ids = ["T1", "T2", "T3", "T4"]

    for tid in t_ids:
        trader_repo.save(Trader(trader_id=tid, name=tid, created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    gen = SyntheticDataGenerator(seed=42)
    start_dt = datetime(2026, 1, 5, tzinfo=timezone.utc)
    for tid in t_ids:
        for e in gen.generate_profile_steady_survivor(tid, symbol="PETR4", start_date=start_dt, trade_count=25):
            exec_repo.insert(e)

    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)
    cfg = WeightConfig(maximum_trader_weight=0.30)

    snap = weight_engine.calculate_diagnostic_weights(as_of=as_of, trader_ids=t_ids, config=cfg)

    assert abs(snap.total_normalized_weight - 1.0) < 1e-4
    for tw in snap.trader_weights:
        assert tw.normalized_weight <= 0.3001


def test_floors_exceeding_100_percent_raises_infeasible_error():
    """
    Hardening 2 (Caso 3): 10 traders com floor de 15% (sem poda) -> soma mínima = 150% > 100%.
    Deve levantar InfeasibleWeightConstraintsError.
    """
    trader_repo, exec_repo, weight_engine = setup_weight_engine()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t_ids = [f"T_{i}" for i in range(10)]

    for tid in t_ids:
        trader_repo.save(Trader(trader_id=tid, name=tid, created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    gen = SyntheticDataGenerator(seed=42)
    start_dt = datetime(2026, 1, 5, tzinfo=timezone.utc)
    for tid in t_ids:
        for e in gen.generate_profile_steady_survivor(tid, symbol="PETR4", start_date=start_dt, trade_count=15):
            exec_repo.insert(e)

    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)
    cfg = WeightConfig(
        minimum_trader_weight=0.15,
        prune_below_minimum_weight=False,
        maximum_trader_weight=1.0
    )

    with pytest.raises(InfeasibleWeightConstraintsError) as exc_info:
        weight_engine.calculate_diagnostic_weights(as_of=as_of, trader_ids=t_ids, config=cfg)

    err = exc_info.value
    assert err.constraint_cause == "minimum_trader_weight"
    assert err.minimum_possible_weight == pytest.approx(1.50, abs=1e-4)


def test_group_caps_preventing_100_percent_raises_infeasible_error():
    """
    Hardening 2 (Caso 4): 3 traders no mesmo grupo com teto de grupo de 40% e nenhum trader fora do grupo.
    Capacidade máxima = 40% < 100%. Deve levantar InfeasibleWeightConstraintsError.
    """
    trader_repo, exec_repo, weight_engine = setup_weight_engine()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t_ids = ["T1", "T2", "T3"]

    for tid in t_ids:
        trader_repo.save(Trader(trader_id=tid, name=tid, created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    gen = SyntheticDataGenerator(seed=42)
    start_dt = datetime(2026, 1, 5, tzinfo=timezone.utc)
    execs_base = gen.generate_profile_steady_survivor("T1", symbol="PETR4", start_date=start_dt, trade_count=30)
    execs_t2 = gen.generate_profile_mirror_trader(execs_base, "T2", time_shift_seconds=60)
    execs_t3 = gen.generate_profile_mirror_trader(execs_base, "T3", time_shift_seconds=120)

    for e in execs_base + execs_t2 + execs_t3:
        exec_repo.insert(e)

    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)
    # Grupo de 3 clones com teto de grupo de 40% e sem traders externos
    cfg = WeightConfig(
        maximum_group_weight=0.40,
        maximum_trader_weight=0.50
    )

    with pytest.raises(InfeasibleWeightConstraintsError) as exc_info:
        weight_engine.calculate_diagnostic_weights(as_of=as_of, trader_ids=t_ids, config=cfg)

    err = exc_info.value
    assert err.constraint_cause == "maximum_group_weight"
    assert err.maximum_possible_weight <= 0.4001
