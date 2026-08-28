from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.evaluation_config import EvaluationConfig, EvaluationFrequency
from src.config.survival_config import SurvivalCriteriaConfig
from src.domain.enums import AssetClass
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.evaluation.engine import TraderEvaluationEngine
from src.ranking.engine import TraderRankingEngine
from src.ranking.persistence import RankPersistenceCalculator
from src.replay.engine import TraderReplayEngine
from src.storage.database import DatabaseManager
from src.storage.repositories.executions import SQLiteExecutionRepository
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.storage.repositories.traders import SQLiteTraderRepository
from src.synthetic.generator import SyntheticDataGenerator


def test_rank_persistence_and_turnover():
    db = DatabaseManager(db_path=":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    petr4 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY)
    inst_repo.save(petr4)

    # 3 Traders: Steady A (consistente), Gambler B, Deteriorating C
    t_a = Trader(trader_id="T_A", name="Steady", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), initial_capital=Decimal("10000.00"))
    t_b = Trader(trader_id="T_B", name="Gambler", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), initial_capital=Decimal("10000.00"))
    t_c = Trader(trader_id="T_C", name="Deteriorating", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), initial_capital=Decimal("10000.00"))
    trader_repo.save(t_a)
    trader_repo.save(t_b)
    trader_repo.save(t_c)

    gen = SyntheticDataGenerator(seed=333)
    execs_a = gen.generate_profile_steady_survivor("T_A", trade_count=40, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_b = gen.generate_profile_high_return_gambler("T_B", trade_count=40, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_c = gen.generate_profile_deteriorating("T_C", total_trades=40, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    exec_repo.insert_batch(execs_a + execs_b + execs_c)

    replay_engine = TraderReplayEngine(
        trader_repo=trader_repo,
        instrument_repo=inst_repo,
        execution_repo=exec_repo,
        market_price_repo=price_repo
    )
    eval_engine = TraderEvaluationEngine(
        replay_engine=replay_engine,
        survival_config=SurvivalCriteriaConfig(min_history_days=20, min_trade_count=10),
        evaluation_config=EvaluationConfig()
    )
    ranking_engine = TraderRankingEngine(eval_engine, trader_repo=trader_repo)

    # Gera série de rankings mensais de Jan a Maio
    series = ranking_engine.rank_series(
        start=datetime(2026, 1, 30, tzinfo=timezone.utc),
        end=datetime(2026, 5, 30, tzinfo=timezone.utc),
        frequency=EvaluationFrequency.MONTHLY
    )

    assert len(series) >= 4

    # Calcula persistência para Trader A (Steady) vs Trader C (Deteriorating)
    pers_a = RankPersistenceCalculator.calculate_trader_persistence(series, "T_A")
    pers_c = RankPersistenceCalculator.calculate_trader_persistence(series, "T_C")

    assert pers_a.top_3_percentage == 100.0
    assert pers_a.average_rank <= pers_c.average_rank

    # Calcula Turnover entre os períodos
    turnover = RankPersistenceCalculator.calculate_series_turnover(series, top_n=2)
    assert len(turnover) == len(series) - 1
    assert all(t.top_n == 2 for t in turnover)
