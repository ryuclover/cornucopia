from datetime import datetime, timezone
from decimal import Decimal
from src.config.dependence_config import DependenceConfig
from src.config.evaluation_config import EvaluationFrequency
from src.dependence.engine import TraderDependenceEngine
from src.dependence.models import DependenceLevel
from src.domain.enums import AssetClass, TraderStatus
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


def setup_test_engine():
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
    engine = TraderDependenceEngine(replay_engine=replay)
    return trader_repo, exec_repo, engine


def test_insufficient_sample_behavior():
    trader_repo, exec_repo, engine = setup_test_engine()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    t1 = Trader(trader_id="T_FEW1", name="Few Trades 1", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00"))
    t2 = Trader(trader_id="T_FEW2", name="Few Trades 2", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00"))
    trader_repo.save(t1)
    trader_repo.save(t2)

    gen = SyntheticDataGenerator(seed=77)
    # Apenas 2 trades (insuficiente conforme minimum_overlap_trades = 5)
    execs_t1 = gen.generate_executions_for_trader("T_FEW1", "PETR4", trade_count=2, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_t2 = gen.generate_executions_for_trader("T_FEW2", "PETR4", trade_count=2, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))

    for e in execs_t1 + execs_t2:
        exec_repo.insert(e)

    as_of = datetime(2026, 1, 15, tzinfo=timezone.utc)
    res = engine.analyze_pair("T_FEW1", "T_FEW2", as_of=as_of)

    # Requisito 12: Nunca converter falta de dados em redundância zero
    assert res.sample_status == "INSUFFICIENT_DATA"
    assert res.return_correlation is None
    assert res.composite_redundancy_score is None
    assert res.dependence_level == DependenceLevel.INSUFFICIENT_DATA


def test_longitudinal_pair_and_core_series():
    trader_repo, exec_repo, engine = setup_test_engine()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    t1 = Trader(trader_id="T_S1", name="Trader 1", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00"))
    t2 = Trader(trader_id="T_S2", name="Trader 2 Mirror", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00"))
    t3 = Trader(trader_id="T_S3", name="Trader 3 Independent", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00"))
    trader_repo.save(t1)
    trader_repo.save(t2)
    trader_repo.save(t3)

    gen = SyntheticDataGenerator(seed=88)
    execs_1 = gen.generate_executions_for_trader("T_S1", "PETR4", trade_count=35, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_2 = gen.generate_profile_mirror_trader(execs_1, "T_S2", time_shift_seconds=60)
    execs_3 = gen.generate_executions_for_trader("T_S3", "VALE3", trade_count=35, start_date=datetime(2026, 1, 6, tzinfo=timezone.utc))

    for e in execs_1 + execs_2 + execs_3:
        exec_repo.insert(e)

    # 1. Série longitudinal de par (mensal de Jan a Abril)
    pair_series = engine.analyze_pair_series(
        trader_a_id="T_S1",
        trader_b_id="T_S2",
        start=datetime(2026, 1, 31, tzinfo=timezone.utc),
        end=datetime(2026, 4, 30, tzinfo=timezone.utc),
        frequency=EvaluationFrequency.MONTHLY
    )
    assert len(pair_series) >= 3
    for p in pair_series:
        assert p.trader_a_id == "T_S1"
        assert p.trader_b_id == "T_S2"
        if p.sample_status == "SUFFICIENT":
            assert p.composite_redundancy_score is not None and p.composite_redundancy_score >= 80.0

    # 2. Série longitudinal do núcleo (mensal de Jan a Abril)
    core_series = engine.analyze_core_series(
        start=datetime(2026, 1, 31, tzinfo=timezone.utc),
        end=datetime(2026, 4, 30, tzinfo=timezone.utc),
        frequency=EvaluationFrequency.MONTHLY,
        trader_ids=["T_S1", "T_S2", "T_S3"]
    )
    assert len(core_series) >= 3
    for snap in core_series:
        assert len(snap.selected_trader_ids) == 3
        # T_S1 e T_S2 juntos + T_S3 separado = 2 grupos independentes quando amostra é suficiente
        if snap.average_redundancy > 0:
            assert snap.effective_independent_groups_count == 2
