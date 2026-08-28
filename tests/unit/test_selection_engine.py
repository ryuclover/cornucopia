from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.evaluation_config import EvaluationConfig, EvaluationFrequency
from src.config.selection_config import SelectionConfig
from src.config.survival_config import SurvivalCriteriaConfig
from src.domain.enums import AssetClass, OrderSide
from src.domain.execution import Execution
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


@pytest.fixture
def selection_setup():
    db = DatabaseManager(db_path=":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    petr4 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY)
    inst_repo.save(petr4)

    replay_engine = TraderReplayEngine(trader_repo, inst_repo, exec_repo, price_repo)
    eval_engine = TraderEvaluationEngine(
        replay_engine=replay_engine,
        survival_config=SurvivalCriteriaConfig(min_history_days=20, min_trade_count=10),
        evaluation_config=EvaluationConfig()
    )
    sel_config = SelectionConfig(
        min_survivor_score_candidate=65.0,
        min_survivor_score_selected=75.0,
        candidate_confirmation_periods=2
    )
    selection_engine = TraderSelectionEngine(eval_engine, config=sel_config)
    return selection_engine, trader_repo, exec_repo


def test_selection_engine_series_and_core_snapshot(selection_setup):
    selection_engine, trader_repo, exec_repo = selection_setup

    t_a = Trader(trader_id="T_STEADY", name="Steady", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), initial_capital=Decimal("10000.00"))
    trader_repo.save(t_a)

    gen = SyntheticDataGenerator(seed=111)
    execs = gen.generate_profile_steady_survivor("T_STEADY", trade_count=40, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    exec_repo.insert_batch(execs)

    # Avaliação da série de seleção mensal
    history = selection_engine.evaluate_selection_series(
        trader_id="T_STEADY",
        start=datetime(2026, 1, 30, tzinfo=timezone.utc),
        end=datetime(2026, 4, 30, tzinfo=timezone.utc),
        frequency=EvaluationFrequency.MONTHLY
    )

    assert len(history.decisions) >= 3
    # Esperado: CANDIDATE -> CANDIDATE -> SELECTED
    statuses = [d.new_status for d in history.decisions]
    assert SelectionStatus.CANDIDATE in statuses
    assert history.current_status == SelectionStatus.SELECTED

    # Obtém Core Snapshot em 30/04/2026
    core = selection_engine.get_selected_core(as_of=datetime(2026, 4, 30, tzinfo=timezone.utc))
    assert core.selected_count == 1
    assert core.selected_traders[0].trader_id == "T_STEADY"
    assert core.average_survivor_score > 70.0


def test_selection_point_in_time_future_insulation(selection_setup):
    selection_engine, trader_repo, exec_repo = selection_setup

    t_a = Trader(trader_id="T_PIT", name="Pit Trader", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), initial_capital=Decimal("10000.00"))
    trader_repo.save(t_a)

    gen = SyntheticDataGenerator(seed=222)
    execs_early = gen.generate_profile_steady_survivor("T_PIT", trade_count=35, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))

    # Evento futuro em Junho que quebra o trader
    future_crash_in = Execution(execution_id="c_in", trader_id="T_PIT", symbol="PETR4", side=OrderSide.BUY, quantity=Decimal("1000"), price=Decimal("30.00"), timestamp=datetime(2026, 6, 10, tzinfo=timezone.utc))
    future_crash_out = Execution(execution_id="c_out", trader_id="T_PIT", symbol="PETR4", side=OrderSide.SELL, quantity=Decimal("1000"), price=Decimal("10.00"), timestamp=datetime(2026, 6, 10, 15, 0, tzinfo=timezone.utc))

    exec_repo.insert_batch(execs_early + [future_crash_in, future_crash_out])

    # 1. Consulta o núcleo em 30/04/2026 (antes da quebra de Junho)
    core_t1 = selection_engine.get_selected_core(as_of=datetime(2026, 4, 30, tzinfo=timezone.utc))
    assert core_t1.selected_count == 1
    assert core_t1.selected_traders[0].new_status == SelectionStatus.SELECTED

    # 2. Consulta o núcleo em 30/06/2026 (após o crash)
    core_t2 = selection_engine.get_selected_core(as_of=datetime(2026, 6, 30, tzinfo=timezone.utc))
    assert core_t2.selected_count == 0
    dec_t2 = next(d for d in core_t2.all_trader_decisions if d.trader_id == "T_PIT")
    assert dec_t2.new_status == SelectionStatus.EXCLUDED


def test_selection_determinism(selection_setup):
    selection_engine, trader_repo, exec_repo = selection_setup

    t_a = Trader(trader_id="T_DET", name="Det Trader", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), initial_capital=Decimal("10000.00"))
    trader_repo.save(t_a)

    gen = SyntheticDataGenerator(seed=333)
    execs = gen.generate_profile_steady_survivor("T_DET", trade_count=30, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    exec_repo.insert_batch(execs)

    as_of = datetime(2026, 4, 1, tzinfo=timezone.utc)
    res_1 = selection_engine.get_selected_core(as_of=as_of)
    res_2 = selection_engine.get_selected_core(as_of=as_of)

    assert res_1 == res_2
