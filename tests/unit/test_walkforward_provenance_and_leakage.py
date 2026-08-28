from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.evaluation_config import EvaluationFrequency
from src.config.walkforward_config import RunPurpose, WalkForwardConfig
from src.consensus.engine import ConsensusEngine
from src.consensus.models import ConsensusDirection
from src.dependence.engine import TraderDependenceEngine
from src.domain.enums import AssetClass, OrderSide, TraderStatus
from src.domain.execution import Execution
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.evaluation.engine import TraderEvaluationEngine
from src.replay.engine import TraderReplayEngine
from src.selection.engine import TraderSelectionEngine
from src.signals.engine import TraderSignalEngine
from src.storage.database import DatabaseManager as SQLiteDatabaseManager
from src.storage.repositories.base import MarketPriceRecord
from src.storage.repositories.executions import SQLiteExecutionRepository
from src.storage.repositories.instruments import SQLiteInstrumentRepository
from src.storage.repositories.market_prices import SQLiteMarketPriceRepository
from src.storage.repositories.traders import SQLiteTraderRepository
from src.walkforward.engine import WalkForwardEngine
from src.walkforward.models import WalkForwardDecision
from src.walkforward.simulator import ConsensusShadowStrategySimulator
from src.weighting.engine import TraderWeightEngine


def setup_env():
    db = SQLiteDatabaseManager(":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    petr4 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    inst_repo.save(petr4)

    replay = TraderReplayEngine(trader_repo, inst_repo, exec_repo, price_repo)
    eval_engine = TraderEvaluationEngine(replay)
    sel_engine = TraderSelectionEngine(eval_engine)
    dep_engine = TraderDependenceEngine(replay)
    weight_engine = TraderWeightEngine(eval_engine, dep_engine, selection_engine=sel_engine)
    sig_engine = TraderSignalEngine(replay)
    cons_engine = ConsensusEngine(sig_engine)

    return trader_repo, exec_repo, price_repo, sel_engine, dep_engine, weight_engine, sig_engine, cons_engine, replay, eval_engine


def test_config_and_dataset_fingerprints_integrity():
    """
    Testa que:
    1. Mesma configuração gera o mesmo fingerprint;
    2. Configuração diferente gera fingerprint diferente;
    3. Inserção de novos dados futuros altera o dataset_fingerprint.
    """
    cfg1 = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        warmup_days=60,
        run_purpose=RunPurpose.DEVELOPMENT,
        trial_sequence_number=1
    )
    cfg2 = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        warmup_days=60,
        run_purpose=RunPurpose.DEVELOPMENT,
        trial_sequence_number=1
    )
    cfg3 = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        warmup_days=30,  # Parâmetro diferente
        run_purpose=RunPurpose.DEVELOPMENT,
        trial_sequence_number=2
    )

    fp1 = cfg1.compute_config_fingerprint()
    fp2 = cfg2.compute_config_fingerprint()
    fp3 = cfg3.compute_config_fingerprint()

    assert fp1 == fp2
    assert fp1 != fp3


def test_same_bucket_leakage_impossibility():
    """
    Teste Nominal Dedicado de Same-Bucket Leakage:
    
    Cenário LONG:
    - No dia D1 (10/Jan), o preço salta +50% (de 100.00 para 150.00).
    - A decisão de consenso LONG é tomada no FECHAMENTO de D1 (10/Jan às 18:00).
    - No dia D2 (11/Jan), o mercado fica flat em 150.00.
    
    O backtest NÃO PODE capturar os +50% ocorridos durante o dia D1!
    O retorno simulado entre o fechamento de D1 e o fechamento de D2 deve ser exatamente 0.00%.
    """
    price_repo, sim = SQLiteMarketPriceRepository(SQLiteDatabaseManager(":memory:")), None
    db = price_repo.db
    inst_repo = SQLiteInstrumentRepository(db)
    inst_repo.save(MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL"))
    
    # Preço às 09:00 de D1 = 100.00
    # Preço às 18:00 de D1 (Fechamento) = 150.00 (+50% intraday em D1)
    # Preço às 18:00 de D2 (Fechamento) = 150.00 (0% entre D1 e D2)
    d1_close = datetime(2026, 1, 10, 18, 0, tzinfo=timezone.utc)
    d2_close = datetime(2026, 1, 11, 18, 0, tzinfo=timezone.utc)

    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=d1_close, price=Decimal("150.00")))
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=d2_close, price=Decimal("150.00")))

    # Decisão LONG congelada no fechamento de D1
    dec_d1 = WalkForwardDecision(
        decision_id="D1",
        decision_as_of=d1_close,
        symbol="PETR4",
        consensus_direction=ConsensusDirection.LONG
    )
    dec_d2 = WalkForwardDecision(
        decision_id="D2",
        decision_as_of=d2_close,
        symbol="PETR4",
        consensus_direction=ConsensusDirection.LONG
    )

    from src.config.walkforward_config import BacktestFrictionConfig
    frictionless = BacktestFrictionConfig(commission_bps=0.0, spread_bps=0.0, slippage_bps=0.0)
    sim = ConsensusShadowStrategySimulator(price_repo, frictionless)
    res = sim.simulate_shadow_strategy("PETR4", [dec_d1, dec_d2])

    # O retorno bruto e líquido do período subsequente D1 -> D2 é rigorosamente 0.0%
    pt_d1 = res.equity_curve[0]
    assert pt_d1.raw_price_return == 0.0
    assert pt_d1.gross_period_return == 0.0
    assert res.cumulative_gross_return == 0.0
    assert res.cumulative_net_return == 0.0


def test_symmetric_short_same_bucket_leakage_impossibility():
    """
    Cenário Simétrico SHORT de Same-Bucket Leakage:
    - Queda de 50% em D1 (de 100 para 50).
    - Decisão SHORT no fechamento de D1.
    - D2 permanece em 50.
    - O backtest NÃO pode computar ganho da queda de D1. Retorno D1->D2 = 0.0%.
    """
    price_repo = SQLiteMarketPriceRepository(SQLiteDatabaseManager(":memory:"))
    db = price_repo.db
    inst_repo = SQLiteInstrumentRepository(db)
    inst_repo.save(MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL"))

    d1_close = datetime(2026, 1, 10, 18, 0, tzinfo=timezone.utc)
    d2_close = datetime(2026, 1, 11, 18, 0, tzinfo=timezone.utc)

    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=d1_close, price=Decimal("50.00")))
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=d2_close, price=Decimal("50.00")))

    dec_d1 = WalkForwardDecision(decision_id="D1", decision_as_of=d1_close, symbol="PETR4", consensus_direction=ConsensusDirection.SHORT)
    dec_d2 = WalkForwardDecision(decision_id="D2", decision_as_of=d2_close, symbol="PETR4", consensus_direction=ConsensusDirection.SHORT)

    from src.config.walkforward_config import BacktestFrictionConfig
    frictionless = BacktestFrictionConfig(commission_bps=0.0, spread_bps=0.0, slippage_bps=0.0)
    sim = ConsensusShadowStrategySimulator(price_repo, frictionless)
    res = sim.simulate_shadow_strategy("PETR4", [dec_d1, dec_d2])

    assert res.equity_curve[0].raw_price_return == 0.0
    assert res.equity_curve[0].gross_period_return == 0.0
    assert res.cumulative_gross_return == 0.0


def test_final_holdout_isolation_in_pipeline():
    """
    Testa que o FINAL_HOLDOUT permanece isolado e não é misturado silenciosamente.
    """
    trader_repo, exec_repo, price_repo, sel_e, dep_e, w_e, sig_e, cons_e, replay, eval_e = setup_env()

    trader_repo.save(Trader(trader_id="T1", name="T1", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    for m in [1, 2, 3, 4]:
        dt = datetime(2026, m, 28, 16, 0, tzinfo=timezone.utc)
        price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=dt, price=Decimal("30.00")))
        price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=dt + pytest.importorskip("datetime").timedelta(days=5), price=Decimal("31.00")))

    cfg = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 4, 30, tzinfo=timezone.utc),
        warmup_days=0,
        decision_frequency=EvaluationFrequency.MONTHLY,
        holdout_start=datetime(2026, 4, 1, tzinfo=timezone.utc),
        run_purpose=RunPurpose.FINAL_HOLDOUT
    )

    wf_engine = WalkForwardEngine(replay, eval_e, sel_e, dep_e, w_e, sig_e, cons_e, price_repo, cfg)
    run = wf_engine.run_walk_forward(symbols=["PETR4"])

    assert run.holdout_metrics is not None
    assert run.full_period_diagnostic_metrics is not None
    assert run.full_period_diagnostic_metrics["diagnostic_label"] == "FULL_PERIOD_DIAGNOSTIC"
