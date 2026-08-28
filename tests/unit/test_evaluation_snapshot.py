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
from src.evaluation.models import QualificationStatus
from src.replay.engine import TraderReplayEngine
from src.storage.database import DatabaseManager
from src.storage.repositories.executions import SQLiteExecutionRepository
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.storage.repositories.traders import SQLiteTraderRepository
from src.synthetic.generator import SyntheticDataGenerator


@pytest.fixture
def eval_setup():
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
        evaluation_config=EvaluationConfig(recent_window_days=30, medium_window_days=60)
    )
    return eval_engine, trader_repo, exec_repo


def test_evaluation_snapshot_qualified_trader(eval_setup):
    eval_engine, trader_repo, exec_repo = eval_setup

    t1 = Trader(trader_id="T_STEADY", name="Steady Trader", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), initial_capital=Decimal("10000.00"))
    trader_repo.save(t1)

    gen = SyntheticDataGenerator(seed=42)
    execs = gen.generate_profile_steady_survivor("T_STEADY", trade_count=35, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    exec_repo.insert_batch(execs)

    as_of = datetime(2026, 4, 1, tzinfo=timezone.utc)
    snapshot = eval_engine.evaluate_trader("T_STEADY", as_of=as_of)

    assert snapshot.trader_id == "T_STEADY"
    assert snapshot.is_qualified is True
    assert snapshot.qualification_status == QualificationStatus.QUALIFIED
    assert snapshot.trade_count == 35
    assert snapshot.history_days >= 30
    assert snapshot.survivor_score > 60.0
    assert snapshot.score_lifetime == snapshot.survivor_score
    assert snapshot.realized_equity > Decimal("10000.00")
    assert snapshot.valuation_status == "CONFIRMED"


def test_insufficient_history_status(eval_setup):
    eval_engine, trader_repo, exec_repo = eval_setup

    t_new = Trader(trader_id="T_NEWBIE", name="New Trader", created_at=datetime(2026, 1, 10, tzinfo=timezone.utc), initial_capital=Decimal("10000.00"))
    trader_repo.save(t_new)

    gen = SyntheticDataGenerator(seed=10)
    # Apenas 4 trades limpos e lucrativos
    execs = gen.generate_profile_insufficient_history("T_NEWBIE", trade_count=4, start_date=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc))
    exec_repo.insert_batch(execs)

    as_of = datetime(2026, 1, 15, tzinfo=timezone.utc)
    snapshot = eval_engine.evaluate_trader("T_NEWBIE", as_of=as_of)

    # Não deve ser classificado como DISQUALIFIED (mau trader), e sim INSUFFICIENT_HISTORY
    assert snapshot.is_qualified is False
    assert snapshot.qualification_status == QualificationStatus.INSUFFICIENT_HISTORY
    assert any("insuficiente" in r.lower() for r in snapshot.disqualification_reasons)
    assert snapshot.max_drawdown_pct < 5.0


def test_disqualified_fatal_drawdown(eval_setup):
    eval_engine, trader_repo, exec_repo = eval_setup

    t_gambler = Trader(trader_id="T_GAMBLER", name="Gambler", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), initial_capital=Decimal("10000.00"))
    trader_repo.save(t_gambler)

    gen = SyntheticDataGenerator(seed=99)
    execs = gen.generate_profile_high_return_gambler("T_GAMBLER", trade_count=30, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    exec_repo.insert_batch(execs)

    as_of = datetime(2026, 4, 1, tzinfo=timezone.utc)
    snapshot = eval_engine.evaluate_trader("T_GAMBLER", as_of=as_of)

    assert snapshot.is_qualified is False
    assert snapshot.qualification_status == QualificationStatus.DISQUALIFIED
    assert snapshot.max_drawdown_pct > 25.0


def test_window_sample_sufficiency_and_insufficiency(eval_setup):
    eval_engine, trader_repo, exec_repo = eval_setup

    t_trader = Trader(trader_id="T_WINDOW", name="Window Trader", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), initial_capital=Decimal("10000.00"))
    trader_repo.save(t_trader)

    # Configuração: min_trades_30d = 5, min_trades_90d = 15, min_trades_180d = 30
    eval_engine.evaluation_config = EvaluationConfig(
        recent_window_days=30,
        min_trades_30d=5,
        medium_window_days=90,
        min_trades_90d=15,
        long_window_days=180,
        min_trades_180d=30
    )

    # Gera 8 trades em Jan/Fev (suficiente para 30d se estiverem concentrados, mas insuficiente para 90d que exige 15 trades)
    gen = SyntheticDataGenerator(seed=12)
    execs = gen.generate_executions_for_trader("T_WINDOW", trade_count=8, start_date=datetime(2026, 1, 10, tzinfo=timezone.utc))
    exec_repo.insert_batch(execs)

    as_of = datetime(2026, 2, 1, tzinfo=timezone.utc)
    snapshot = eval_engine.evaluate_trader("T_WINDOW", as_of=as_of)

    # Janela 30d: 8 trades >= 5 exigidos -> SUFFICIENT e score preenchido
    assert snapshot.window_30d.sample_status.value == "SUFFICIENT"
    assert snapshot.window_30d.trade_count == 8
    assert snapshot.window_30d.score is not None
    assert snapshot.score_30d == snapshot.window_30d.score

    # Janela 90d: 8 trades < 15 exigidos -> INSUFFICIENT_SAMPLE e score None (e não zero)
    assert snapshot.window_90d.sample_status.value == "INSUFFICIENT_SAMPLE"
    assert snapshot.window_90d.trade_count == 8
    assert snapshot.window_90d.score is None
    assert snapshot.score_90d is None

    # Janela 180d: 8 trades < 30 exigidos -> INSUFFICIENT_SAMPLE e score None
    assert snapshot.window_180d.sample_status.value == "INSUFFICIENT_SAMPLE"
    assert snapshot.window_180d.score is None
    assert snapshot.score_180d is None
