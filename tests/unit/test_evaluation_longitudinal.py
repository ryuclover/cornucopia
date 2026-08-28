from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.evaluation_config import EvaluationConfig, EvaluationFrequency
from src.config.survival_config import SurvivalCriteriaConfig
from src.domain.enums import AssetClass
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.evaluation.engine import TraderEvaluationEngine
from src.evaluation.models import ScoreTrend
from src.replay.engine import TraderReplayEngine
from src.storage.database import DatabaseManager
from src.storage.repositories.executions import SQLiteExecutionRepository
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.storage.repositories.traders import SQLiteTraderRepository
from src.synthetic.generator import SyntheticDataGenerator


@pytest.fixture
def eval_longitudinal_setup():
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
        survival_config=SurvivalCriteriaConfig(min_history_days=20, min_trade_count=10),
        evaluation_config=EvaluationConfig(recent_window_days=30, trend_window_periods=4)
    )
    return eval_engine, trader_repo, exec_repo


def test_deteriorating_trader_trend_and_score_decay(eval_longitudinal_setup):
    eval_engine, trader_repo, exec_repo = eval_longitudinal_setup

    t_det = Trader(trader_id="T_DET", name="Deteriorating Trader", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), initial_capital=Decimal("10000.00"))
    trader_repo.save(t_det)

    gen = SyntheticDataGenerator(seed=88)
    execs = gen.generate_profile_deteriorating("T_DET", total_trades=40, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    exec_repo.insert_batch(execs)

    # Avaliação longitudinal mensal em Jan, Fev, Mar, Abr
    start_dt = datetime(2026, 1, 30, tzinfo=timezone.utc)
    end_dt = datetime(2026, 4, 30, tzinfo=timezone.utc)

    series = eval_engine.evaluate_series("T_DET", start=start_dt, end=end_dt, frequency=EvaluationFrequency.MONTHLY)
    assert len(series) >= 3

    # O primeiro snapshot (fase boa) deve ter score superior ao último (fase deteriorada)
    first_snap = series[0]
    last_snap = series[-1]
    assert first_snap.survivor_score > last_snap.survivor_score

    # Métricas de estabilidade devem detectar tendência deteriorante
    stability = eval_engine.calculate_stability_metrics(series)
    assert stability.score_trend == ScoreTrend.DETERIORATING
    assert stability.score_trend_slope < 0.0


def test_recovering_trader_series(eval_longitudinal_setup):
    eval_engine, trader_repo, exec_repo = eval_longitudinal_setup

    t_rec = Trader(trader_id="T_REC", name="Recovering Trader", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), initial_capital=Decimal("10000.00"))
    trader_repo.save(t_rec)

    gen = SyntheticDataGenerator(seed=55)
    execs = gen.generate_profile_recovering("T_REC", total_trades=45, start_date=datetime(2026, 1, 5, tzinfo=timezone.utc))
    exec_repo.insert_batch(execs)

    start_dt = datetime(2026, 1, 30, tzinfo=timezone.utc)
    end_dt = datetime(2026, 5, 30, tzinfo=timezone.utc)

    series = eval_engine.evaluate_series("T_REC", start=start_dt, end=end_dt, frequency=EvaluationFrequency.MONTHLY)
    assert len(series) >= 4

    # Verifica que o patrimônio final recupera em relação ao ponto de vale
    valleys = min(s.realized_equity for s in series)
    final_equity = series[-1].realized_equity
    assert final_equity > valleys


def test_positive_period_rate_based_on_interval_deltas(eval_longitudinal_setup):
    eval_engine, trader_repo, exec_repo = eval_longitudinal_setup

    # Simula snapshots com patrimônios: 10000 -> 11000 (+1000) -> 10500 (-500) -> 10800 (+300)
    # Todos os patrimônios são superiores ao capital inicial de 10000,
    # porém dos 3 intervalos (Jan->Fev, Fev->Mar, Mar->Abr), apenas 2 são positivos (2/3 = 66.67%).
    from src.evaluation.models import QualificationStatus, TraderEvaluationSnapshot

    base_snap = TraderEvaluationSnapshot(
        trader_id="T_TEST",
        as_of=datetime(2026, 1, 30, tzinfo=timezone.utc),
        history_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        history_days=30.0,
        trade_count=15,
        realized_pnl=Decimal("0.0"),
        realized_equity=Decimal("10000.00"),
        net_return_pct=0.0,
        max_drawdown_pct=0.0,
        win_rate=0.6,
        profit_factor=1.5,
        sharpe_ratio=1.2,
        sortino_ratio=1.5,
        largest_loss_pct=1.0,
        max_consecutive_losses=2,
        top_1_trade_pnl_contribution_pct=20.0,
        top_5_trades_pnl_contribution_pct=50.0,
        top_10_percent_trades_pnl_contribution_pct=30.0,
        survivor_score=75.0,
        is_qualified=True,
        qualification_status=QualificationStatus.QUALIFIED,
        score_lifetime=75.0
    )

    s1 = base_snap.model_copy(update={"as_of": datetime(2026, 1, 30, tzinfo=timezone.utc), "realized_equity": Decimal("10000.00")})
    s2 = base_snap.model_copy(update={"as_of": datetime(2026, 2, 28, tzinfo=timezone.utc), "realized_equity": Decimal("11000.00")}) # +1000 (+)
    s3 = base_snap.model_copy(update={"as_of": datetime(2026, 3, 30, tzinfo=timezone.utc), "realized_equity": Decimal("10500.00")}) # -500  (-)
    s4 = base_snap.model_copy(update={"as_of": datetime(2026, 4, 30, tzinfo=timezone.utc), "realized_equity": Decimal("10800.00")}) # +300  (+)

    stability = eval_engine.calculate_stability_metrics([s1, s2, s3, s4])

    # Deve ser exatamente 2 / 3 = 66.67%
    assert stability.positive_period_rate_pct == 66.67


def test_score_trend_ignores_insufficient_history_and_detects_insufficient_data(eval_longitudinal_setup):
    eval_engine, trader_repo, exec_repo = eval_longitudinal_setup
    from src.evaluation.models import QualificationStatus, TraderEvaluationSnapshot

    # Caso 1: Apenas 1 snapshot válido e 2 insuficientes -> Deve retornar INSUFFICIENT_DATA
    s_insuf_1 = TraderEvaluationSnapshot(
        trader_id="T_TEST",
        as_of=datetime(2026, 1, 10, tzinfo=timezone.utc),
        history_start=datetime(2026, 1, 5, tzinfo=timezone.utc),
        history_days=5.0,
        trade_count=2,
        realized_pnl=Decimal("10.0"),
        realized_equity=Decimal("10010.00"),
        net_return_pct=0.1,
        max_drawdown_pct=0.0,
        win_rate=0.5,
        profit_factor=1.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        largest_loss_pct=0.0,
        max_consecutive_losses=0,
        top_1_trade_pnl_contribution_pct=100.0,
        top_5_trades_pnl_contribution_pct=100.0,
        top_10_percent_trades_pnl_contribution_pct=100.0,
        survivor_score=0.0,
        is_qualified=False,
        qualification_status=QualificationStatus.INSUFFICIENT_HISTORY,
        score_lifetime=0.0
    )
    s_insuf_2 = s_insuf_1.model_copy(update={"as_of": datetime(2026, 1, 15, tzinfo=timezone.utc)})
    s_valid_1 = s_insuf_1.model_copy(update={
        "as_of": datetime(2026, 2, 28, tzinfo=timezone.utc),
        "history_days": 50.0,
        "trade_count": 25,
        "is_qualified": True,
        "qualification_status": QualificationStatus.QUALIFIED,
        "survivor_score": 80.0
    })

    # Apenas 1 snapshot maduro -> INSUFFICIENT_DATA (sem inventar score zero)
    metrics_single = eval_engine.calculate_stability_metrics([s_insuf_1, s_insuf_2, s_valid_1])
    assert metrics_single.score_trend == ScoreTrend.INSUFFICIENT_DATA
    assert metrics_single.score_trend_slope == 0.0

    # Caso 2: Dois ou mais snapshots válidos com scores crescentes (80 -> 85 -> 90)
    s_valid_2 = s_valid_1.model_copy(update={"as_of": datetime(2026, 3, 30, tzinfo=timezone.utc), "survivor_score": 85.0})
    s_valid_3 = s_valid_1.model_copy(update={"as_of": datetime(2026, 4, 30, tzinfo=timezone.utc), "survivor_score": 90.0})

    metrics_improving = eval_engine.calculate_stability_metrics([s_insuf_1, s_insuf_2, s_valid_1, s_valid_2, s_valid_3])
    # Ignorou os dois primeiros insuficientes e calculou a tendência apenas sobre [80, 85, 90]
    assert metrics_improving.score_trend == ScoreTrend.IMPROVING
    assert metrics_improving.score_trend_slope == 5.0
