from datetime import datetime, timezone
from decimal import Decimal
from src.config.evaluation_config import EvaluationConfig, EvaluationFrequency
from src.config.selection_config import SelectionConfig
from src.config.survival_config import SurvivalCriteriaConfig
from src.domain.enums import AssetClass
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.evaluation.engine import TraderEvaluationEngine
from src.replay.engine import TraderReplayEngine
from src.selection.engine import TraderSelectionEngine
from src.selection.models import SelectionStatus
from src.storage.database import DatabaseManager
from src.storage.repositories.executions import SQLiteExecutionRepository
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.storage.repositories.traders import SQLiteTraderRepository
from src.synthetic.generator import SyntheticDataGenerator


def test_multi_trader_selection_longitudinal_scenario():
    db = DatabaseManager(db_path=":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    # 1. Instrumentos
    petr4 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY)
    inst_repo.save(petr4)

    # 2. Cadastro dos 6 Traders
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

    # 3. Execuções dos 6 perfis
    gen = SyntheticDataGenerator(seed=777)
    execs_steady = gen.generate_profile_steady_survivor("T_STEADY", trade_count=45, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_gambler = gen.generate_profile_high_return_gambler("T_GAMBLER", trade_count=40, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_det = gen.generate_profile_deteriorating("T_DET", total_trades=40, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_rec = gen.generate_profile_recovering("T_REC", total_trades=45, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_lucky = gen.generate_profile_lucky_outlier("T_LUCKY", trade_count=35, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    execs_newbie = gen.generate_profile_insufficient_history("T_NEWBIE", trade_count=4, start_date=datetime(2026, 5, 2, tzinfo=timezone.utc))

    exec_repo.insert_batch(execs_steady + execs_gambler + execs_det + execs_rec + execs_lucky + execs_newbie)

    # 4. Inicialização dos Motores
    replay_engine = TraderReplayEngine(trader_repo, inst_repo, exec_repo, price_repo)
    eval_engine = TraderEvaluationEngine(
        replay_engine=replay_engine,
        survival_config=SurvivalCriteriaConfig(min_history_days=30, min_trade_count=15),
        evaluation_config=EvaluationConfig()
    )
    sel_config = SelectionConfig.balanced()
    selection_engine = TraderSelectionEngine(eval_engine, config=sel_config)

    # 5. Avaliação do núcleo ao longo de 6 meses
    start_dt = datetime(2026, 1, 30, tzinfo=timezone.utc)
    end_dt = datetime(2026, 6, 30, tzinfo=timezone.utc)

    core_series, churn_series = selection_engine.get_core_series(
        start=start_dt,
        end=end_dt,
        frequency=EvaluationFrequency.MONTHLY
    )

    assert len(core_series) >= 5
    assert len(churn_series) == len(core_series) - 1

    # Validação do snapshot final em Junho
    final_core = core_series[-1]
    status_map = {d.trader_id: d.new_status for d in final_core.all_trader_decisions}

    # 1. Steady Survivor deve estar SELECTED
    assert status_map["T_STEADY"] == SelectionStatus.SELECTED
    # 2. Gambler deve estar EXCLUDED ou SUSPENDED devido a fatal breaches
    assert status_map["T_GAMBLER"] in (SelectionStatus.EXCLUDED, SelectionStatus.SUSPENDED)
    # 3. Newbie com poucos dias e trades deve estar INSUFFICIENT_DATA
    assert status_map["T_NEWBIE"] == SelectionStatus.INSUFFICIENT_DATA
    # 4. Lucky outlier com lucro concentrado não deve estar SELECTED
    assert status_map["T_LUCKY"] != SelectionStatus.SELECTED
    # 5. Deteriorating após a 2ª metade degradada deve estar WATCHLIST ou SUSPENDED
    assert status_map["T_DET"] in (SelectionStatus.WATCHLIST, SelectionStatus.SUSPENDED)

    # Verifica métricas de qualidade do núcleo
    assert final_core.selected_count >= 1
    assert final_core.average_survivor_score >= 70.0
