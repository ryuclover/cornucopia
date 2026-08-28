from datetime import datetime, timezone
from decimal import Decimal
from src.config.evaluation_config import EvaluationConfig, EvaluationFrequency
from src.config.survival_config import SurvivalCriteriaConfig
from src.domain.enums import AssetClass, TraderStatus
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.evaluation.engine import TraderEvaluationEngine
from src.evaluation.models import QualificationStatus, ScoreTrend
from src.ranking.engine import TraderRankingEngine
from src.ranking.persistence import RankPersistenceCalculator
from src.replay.engine import TraderReplayEngine
from src.storage.database import DatabaseManager
from src.storage.repositories.executions import SQLiteExecutionRepository
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.storage.repositories.traders import SQLiteTraderRepository
from src.synthetic.generator import SyntheticDataGenerator


def test_full_longitudinal_ranking_pipeline_with_all_profiles():
    db = DatabaseManager(db_path=":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    # 1. Cadastra instrumentos
    petr4 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY)
    vale3 = MarketInstrument(symbol="VALE3", asset_class=AssetClass.EQUITY)
    inst_repo.save(petr4)
    inst_repo.save(vale3)

    # 2. Cadastra os 6 Traders dos perfis comportamentais
    base_time = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    traders = [
        Trader(trader_id="T_STEADY", name="Steady Survivor", created_at=base_time, initial_capital=Decimal("10000.00")),
        Trader(trader_id="T_GAMBLER", name="Gambler", created_at=base_time, initial_capital=Decimal("10000.00")),
        Trader(trader_id="T_DET", name="Deteriorating", created_at=base_time, initial_capital=Decimal("10000.00")),
        Trader(trader_id="T_REC", name="Recovering", created_at=base_time, initial_capital=Decimal("10000.00")),
        Trader(trader_id="T_LUCKY", name="Lucky Outlier", created_at=base_time, initial_capital=Decimal("10000.00")),
        Trader(trader_id="T_NEWBIE", name="Newbie", created_at=datetime(2026, 5, 1, tzinfo=timezone.utc), initial_capital=Decimal("10000.00")),
    ]
    for t in traders:
        trader_repo.save(t)

    # 3. Gera as operações sintéticas para cada perfil
    gen = SyntheticDataGenerator(seed=999)
    execs_steady = gen.generate_profile_steady_survivor("T_STEADY", trade_count=45, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_gambler = gen.generate_profile_high_return_gambler("T_GAMBLER", trade_count=40, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_det = gen.generate_profile_deteriorating("T_DET", total_trades=40, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_rec = gen.generate_profile_recovering("T_REC", total_trades=45, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_lucky = gen.generate_profile_lucky_outlier("T_LUCKY", trade_count=35, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_newbie = gen.generate_profile_insufficient_history("T_NEWBIE", trade_count=4, start_date=datetime(2026, 5, 2, tzinfo=timezone.utc))

    all_execs = execs_steady + execs_gambler + execs_det + execs_rec + execs_lucky + execs_newbie
    exec_repo.insert_batch(all_execs)

    # 4. Configuração e Inicialização dos Motores
    replay_engine = TraderReplayEngine(trader_repo, inst_repo, exec_repo, price_repo)
    eval_engine = TraderEvaluationEngine(
        replay_engine=replay_engine,
        survival_config=SurvivalCriteriaConfig(min_history_days=30, min_trade_count=15),
        evaluation_config=EvaluationConfig()
    )
    ranking_engine = TraderRankingEngine(eval_engine, trader_repo)

    # 5. Executa ranking point-in-time em Junho de 2026
    as_of_final = datetime(2026, 6, 1, tzinfo=timezone.utc)
    final_rank = ranking_engine.rank(as_of=as_of_final)

    assert final_rank.total_traders == 6
    
    # Validação do status de cada perfil
    status_map = {item.trader_id: item.qualification_status for item in final_rank.full_ranking}
    assert status_map["T_STEADY"] == QualificationStatus.QUALIFIED
    assert status_map["T_NEWBIE"] == QualificationStatus.INSUFFICIENT_HISTORY
    assert status_map["T_GAMBLER"] == QualificationStatus.DISQUALIFIED

    # Validação da série longitudinal e persistência de ranking
    rankings_series = ranking_engine.rank_series(
        start=datetime(2026, 2, 1, tzinfo=timezone.utc),
        end=datetime(2026, 6, 1, tzinfo=timezone.utc),
        frequency=EvaluationFrequency.MONTHLY
    )
    assert len(rankings_series) >= 4

    pers_steady = RankPersistenceCalculator.calculate_trader_persistence(rankings_series, "T_STEADY")
    assert pers_steady.top_3_percentage >= 75.0
