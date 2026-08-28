from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.evaluation_config import EvaluationConfig
from src.config.survival_config import SurvivalCriteriaConfig
from src.domain.enums import AssetClass, OrderSide
from src.domain.execution import Execution
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.evaluation.engine import TraderEvaluationEngine
from src.ranking.engine import TraderRankingEngine
from src.replay.engine import TraderReplayEngine
from src.storage.database import DatabaseManager
from src.storage.repositories.executions import SQLiteExecutionRepository
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.storage.repositories.traders import SQLiteTraderRepository
from src.synthetic.generator import SyntheticDataGenerator


@pytest.fixture
def ranking_setup():
    db = DatabaseManager(db_path=":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    petr4 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY)
    inst_repo.save(petr4)

    replay_engine = TraderReplayEngine(
        trader_repo=trader_repo,
        instrument_repo=inst_repo,
        execution_repo=exec_repo,
        market_price_repo=price_repo
    )
    eval_engine = TraderEvaluationEngine(
        replay_engine=replay_engine,
        survival_config=SurvivalCriteriaConfig(min_history_days=30, min_trade_count=15),
        evaluation_config=EvaluationConfig()
    )
    ranking_engine = TraderRankingEngine(eval_engine, trader_repo=trader_repo)
    return ranking_engine, trader_repo, exec_repo


def test_ranking_point_in_time_and_future_insulation(ranking_setup):
    ranking_engine, trader_repo, exec_repo = ranking_setup
    gen = SyntheticDataGenerator(seed=123)

    # Cria 3 traders
    t_a = Trader(trader_id="T_A", name="Trader A", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), initial_capital=Decimal("10000.00"))
    t_b = Trader(trader_id="T_B", name="Trader B", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), initial_capital=Decimal("10000.00"))
    trader_repo.save(t_a)
    trader_repo.save(t_b)

    # Trader A: Steady Survivor de Jan a Março
    execs_a = gen.generate_profile_steady_survivor("T_A", trade_count=35, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    # Trader B: Gambler desqualificado
    execs_b = gen.generate_profile_high_return_gambler("T_B", trade_count=35, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))

    # Evento futuro em Maio que destrói Trader A
    future_crash = Execution(
        execution_id="exec_T_A_future_crash_in",
        trader_id="T_A",
        symbol="PETR4",
        side=OrderSide.BUY,
        quantity=Decimal("500"),
        price=Decimal("30.00"),
        timestamp=datetime(2026, 5, 10, 10, 0, tzinfo=timezone.utc)
    )
    future_crash_out = Execution(
        execution_id="exec_T_A_future_crash_out",
        trader_id="T_A",
        symbol="PETR4",
        side=OrderSide.SELL,
        quantity=Decimal("500"),
        price=Decimal("15.00"), # -50% loss
        timestamp=datetime(2026, 5, 10, 15, 0, tzinfo=timezone.utc)
    )

    exec_repo.insert_batch(execs_a + execs_b + [future_crash, future_crash_out])

    # 1. Ranking em 01/04/2026 (antes da quebra de Maio)
    as_of_t1 = datetime(2026, 4, 1, tzinfo=timezone.utc)
    rank_t1 = ranking_engine.rank(as_of=as_of_t1)

    assert rank_t1.total_traders == 2
    assert rank_t1.full_ranking[0].trader_id == "T_A"
    assert rank_t1.full_ranking[0].rank == 1
    assert rank_t1.full_ranking[0].is_qualified is True

    assert len(rank_t1.qualified_ranking) == 1
    assert rank_t1.qualified_ranking[0].trader_id == "T_A"

    # 2. Ranking em 01/06/2026 (após a quebra de Maio)
    as_of_t2 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    rank_t2 = ranking_engine.rank(as_of=as_of_t2)

    # Agora Trader A foi desqualificado pelo crash de Maio
    item_a_t2 = next(item for item in rank_t2.full_ranking if item.trader_id == "T_A")
    assert item_a_t2.is_qualified is False
    assert len(rank_t2.qualified_ranking) == 0


def test_ranking_determinism(ranking_setup):
    ranking_engine, trader_repo, exec_repo = ranking_setup
    t_a = Trader(trader_id="T_A", name="Trader A", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), initial_capital=Decimal("10000.00"))
    trader_repo.save(t_a)
    
    gen = SyntheticDataGenerator(seed=77)
    execs = gen.generate_profile_steady_survivor("T_A", trade_count=20, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    exec_repo.insert_batch(execs)

    as_of = datetime(2026, 3, 1, tzinfo=timezone.utc)
    res_1 = ranking_engine.rank(as_of=as_of)
    res_2 = ranking_engine.rank(as_of=as_of)

    assert res_1 == res_2
