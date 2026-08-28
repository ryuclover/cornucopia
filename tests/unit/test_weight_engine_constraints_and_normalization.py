from datetime import datetime, timezone
from decimal import Decimal
from src.config.dependence_config import DependenceConfig
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
from src.weighting.diagnostics import WeightDiagnosticsCalculator
from src.weighting.engine import TraderWeightEngine


def setup_weight_engine():
    db = SQLiteDatabaseManager(":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    petr4 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    vale3 = MarketInstrument(symbol="VALE3", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    win = MarketInstrument(symbol="WIN$", asset_class=AssetClass.FUTURES, tick_size=Decimal("5.0"), tick_value=Decimal("1.0"), contract_multiplier=Decimal("0.2"), currency="BRL")
    inst_repo.save(petr4)
    inst_repo.save(vale3)
    inst_repo.save(win)

    replay = TraderReplayEngine(trader_repo, inst_repo, exec_repo, price_repo)
    eval_engine = TraderEvaluationEngine(replay)
    dep_engine = TraderDependenceEngine(replay)
    weight_engine = TraderWeightEngine(
        evaluation_engine=eval_engine,
        dependence_engine=dep_engine
    )
    return trader_repo, exec_repo, weight_engine


def test_individual_and_group_caps_enforcement():
    trader_repo, exec_repo, weight_engine = setup_weight_engine()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # 5 Traders: 3 no mesmo grupo de clones (T1, T2, T3) e 2 independentes (T4, T5)
    t_ids = ["T1", "T2", "T3", "T4", "T5"]
    for tid in t_ids:
        trader_repo.save(Trader(trader_id=tid, name=tid, created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    gen = SyntheticDataGenerator(seed=42)
    start_dt = datetime(2026, 1, 5, tzinfo=timezone.utc)
    execs_base = gen.generate_profile_steady_survivor("T1", symbol="PETR4", start_date=start_dt, trade_count=30)
    execs_t2 = gen.generate_profile_mirror_trader(execs_base, "T2", time_shift_seconds=60)
    execs_t3 = gen.generate_profile_mirror_trader(execs_base, "T3", time_shift_seconds=120)
    execs_t4 = gen.generate_profile_independent_trader("T4", symbol="VALE3", start_date=start_dt, trade_count=30)
    execs_t5 = gen.generate_profile_independent_trader("T5", symbol="WIN$", start_date=start_dt, trade_count=30)

    for e in execs_base + execs_t2 + execs_t3 + execs_t4 + execs_t5:
        exec_repo.insert(e)

    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    # Config com teto individual de 30% e teto de grupo de 45%
    cfg = WeightConfig(
        maximum_trader_weight=0.30,
        maximum_group_weight=0.45
    )

    core_weight = weight_engine.calculate_core_weights(as_of=as_of, config=cfg, trader_ids=t_ids)

    # 1. Normalização estrita: soma de todos os pesos = 1.0 (tolerância 1e-4)
    total_w = sum(tw.normalized_weight for tw in core_weight.trader_weights)
    assert abs(total_w - 1.0) < 1e-4

    # 2. Nenhum trader individual excede o teto de 30%
    for tw in core_weight.trader_weights:
        assert tw.normalized_weight <= 0.3001

    # 3. Nenhum grupo excede o teto de grupo de 45%
    for grp in core_weight.group_summaries:
        assert grp.total_group_weight <= 0.4501

    # 4. Número efetivo de traders está bem calculado
    assert core_weight.effective_trader_count >= 3.0


def test_low_quality_independent_does_not_dominate():
    """
    Independência não pode compensar qualidade ruim.
    Trader A: SurvivorScore 92, redundância moderada.
    Trader B: SurvivorScore 55 (fraco), 100% independente.
    Trader A deve receber peso significativamente maior que Trader B.
    """
    trader_repo, exec_repo, weight_engine = setup_weight_engine()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    trader_repo.save(Trader(trader_id="T_GOOD", name="Good Trader", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))
    trader_repo.save(Trader(trader_id="T_WEAK", name="Weak Independent", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    gen = SyntheticDataGenerator(seed=10)
    start_dt = datetime(2026, 1, 5, tzinfo=timezone.utc)
    # T_GOOD: Steady Survivor de alta qualidade
    execs_good = gen.generate_profile_steady_survivor("T_GOOD", symbol="PETR4", start_date=start_dt, trade_count=35)
    # T_WEAK: Gambler com drawdown elevado
    execs_weak = gen.generate_profile_high_return_gambler("T_WEAK", symbol="VALE3", start_date=start_dt, trade_count=35)

    for e in execs_good + execs_weak:
        exec_repo.insert(e)

    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)
    cfg = WeightConfig(maximum_trader_weight=1.0, maximum_group_weight=1.0)
    core_weight = weight_engine.calculate_core_weights(as_of=as_of, config=cfg, trader_ids=["T_GOOD", "T_WEAK"])

    w_good = core_weight.weights_map["T_GOOD"].normalized_weight
    w_weak = core_weight.weights_map["T_WEAK"].normalized_weight

    assert w_good > w_weak * 1.5 # Trader bom tem peso substancialmente maior


def test_weight_turnover_between_snapshots():
    snap1_weights = {"A": 0.50, "B": 0.30, "C": 0.20}
    snap2_weights = {"A": 0.40, "B": 0.40, "C": 0.20}

    # Turnover teórico: 0.5 * (|0.4-0.5| + |0.4-0.3| + |0.2-0.2|) = 0.5 * (0.1 + 0.1 + 0) = 0.10 (10.0%)
    from src.weighting.models import CoreWeightSnapshot, TraderWeight, WeightConcentrationMetrics
    dt1 = datetime(2026, 1, 31, tzinfo=timezone.utc)
    dt2 = datetime(2026, 2, 28, tzinfo=timezone.utc)

    conc_dummy = WeightConcentrationMetrics(
        effective_trader_count=3.0, herfindahl_index=0.33,
        top_1_weight_share_pct=50.0, top_3_weight_share_pct=100.0,
        top_5_weight_share_pct=100.0, effective_group_count=3.0, group_herfindahl_index=0.33
    )

    s1 = CoreWeightSnapshot(
        as_of=dt1, selected_traders=["A", "B", "C"],
        trader_weights=[
            TraderWeight(trader_id=t, as_of=dt1, survivor_score=80.0, quality_component=0.8, independence_component=0.8, confidence_component=0.8, raw_weight=0.5, normalized_weight=w, weight_pct=w*100)
            for t, w in snap1_weights.items()
        ],
        weights_map={
            t: TraderWeight(trader_id=t, as_of=dt1, survivor_score=80.0, quality_component=0.8, independence_component=0.8, confidence_component=0.8, raw_weight=0.5, normalized_weight=w, weight_pct=w*100)
            for t, w in snap1_weights.items()
        },
        concentration_metrics=conc_dummy, effective_trader_count=3.0
    )

    s2 = CoreWeightSnapshot(
        as_of=dt2, selected_traders=["A", "B", "C"],
        trader_weights=[
            TraderWeight(trader_id=t, as_of=dt2, survivor_score=80.0, quality_component=0.8, independence_component=0.8, confidence_component=0.8, raw_weight=0.5, normalized_weight=w, weight_pct=w*100)
            for t, w in snap2_weights.items()
        ],
        weights_map={
            t: TraderWeight(trader_id=t, as_of=dt2, survivor_score=80.0, quality_component=0.8, independence_component=0.8, confidence_component=0.8, raw_weight=0.5, normalized_weight=w, weight_pct=w*100)
            for t, w in snap2_weights.items()
        },
        concentration_metrics=conc_dummy, effective_trader_count=3.0
    )

    turnover = WeightDiagnosticsCalculator.calculate_turnover(s1, s2)
    assert turnover.turnover_pct == 10.0
    assert turnover.max_weight_increase_trader == "B"
    assert turnover.max_weight_decrease_trader == "A"
