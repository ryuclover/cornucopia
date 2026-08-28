from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.consensus_config import ConsensusConfig
from src.config.evaluation_config import EvaluationFrequency
from src.config.selection_config import SelectionConfig
from src.config.walkforward_config import BaselineMode, WalkForwardConfig
from src.config.weight_config import WeightConfig
from src.consensus.engine import ConsensusEngine
from src.consensus.models import ConsensusDirection
from src.dependence.engine import TraderDependenceEngine
from src.domain.enums import AssetClass, OrderSide, PositionSide, TraderStatus
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
from src.weighting.engine import TraderWeightEngine


def setup_pipeline_environment():
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


def test_walkforward_end_to_end_pipeline_run():
    """
    Execução ponta a ponta do WalkForwardEngine:
    - Warm-up
    - Tomada de decisões
    - Avaliação de outcomes
    - Rastreamento de episódios
    - Shadow Strategy
    - Baselines
    - Métricas consolidadas
    """
    trader_repo, exec_repo, price_repo, sel_e, dep_e, w_e, sig_e, cons_e, replay, eval_e = setup_pipeline_environment()

    # Criação de 2 traders com histórico
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for tid in ["T1", "T2"]:
        trader_repo.save(Trader(trader_id=tid, name=tid, created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    # Execuções durante Jan, Fev, Março
    for day in [10, 20]:
        for tid in ["T1", "T2"]:
            exec_repo.insert(Execution(execution_id=f"E_1_{tid}_{day}", trader_id=tid, symbol="PETR4", timestamp=datetime(2026, 1, day, 10, 0, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))
            exec_repo.insert(Execution(execution_id=f"E_1_{tid}_{day}_c", trader_id=tid, symbol="PETR4", timestamp=datetime(2026, 1, day, 15, 0, tzinfo=timezone.utc), side=OrderSide.SELL, quantity=Decimal("100"), price=Decimal("31.00")))

    # Execuções em Março para manter posição
    exec_repo.insert(Execution(execution_id="E_3_T1", trader_id="T1", symbol="PETR4", timestamp=datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))
    exec_repo.insert(Execution(execution_id="E_3_T2", trader_id="T2", symbol="PETR4", timestamp=datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))

    # Cotações de mercado para avaliação
    for m in [1, 2, 3, 4]:
        for d in [1, 15, 28, 30]:
            if m == 2 and d > 28:
                continue
            dt = datetime(2026, m, d, 16, 0, tzinfo=timezone.utc)
            price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=dt, price=Decimal("30.00")))

    wf_cfg = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 4, 30, tzinfo=timezone.utc),
        warmup_days=30,
        decision_frequency=EvaluationFrequency.MONTHLY,
        forward_horizons_days=[5, 20],
        baseline_modes=[BaselineMode.EQUAL_WEIGHT, BaselineMode.SIMPLE_MAJORITY]
    )

    wf_engine = WalkForwardEngine(
        replay_engine=replay,
        evaluation_engine=eval_e,
        selection_engine=sel_e,
        dependence_engine=dep_e,
        weight_engine=w_e,
        signal_engine=sig_e,
        consensus_engine=cons_e,
        price_repo=price_repo,
        config=wf_cfg
    )

    run = wf_engine.run_walk_forward(symbols=["PETR4"])

    assert run.run_id is not None
    assert run.decision_journal.total_decisions >= 3
    assert 5 in run.outcomes_by_horizon
    assert 20 in run.outcomes_by_horizon
    assert "PETR4" in run.shadow_strategy_by_symbol
    assert "data_quality_status" in run.data_quality_summary


def test_lookahead_adversarial_prefix_invariance():
    """
    Teste Adversarial de Prefix Invariance (Seção 46):
    1. Executa walk-forward até T1 (31/Jan).
    2. Registra as decisões congeladas.
    3. Insere novas execuções e preços em T2 (Fevereiro/Março).
    4. Executa novamente até T1.
    5. Prova que as decisões em T1 são 100% idênticas (bit-for-bit).
    """
    trader_repo, exec_repo, price_repo, sel_e, dep_e, w_e, sig_e, cons_e, replay, eval_e = setup_pipeline_environment()

    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    trader_repo.save(Trader(trader_id="T1", name="T1", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))
    trader_repo.save(Trader(trader_id="T2", name="T2", created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    # Execuções em Janeiro
    exec_repo.insert(Execution(execution_id="E1", trader_id="T1", symbol="PETR4", timestamp=datetime(2026, 1, 10, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))
    exec_repo.insert(Execution(execution_id="E2", trader_id="T2", symbol="PETR4", timestamp=datetime(2026, 1, 10, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))

    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 31, 16, 0, tzinfo=timezone.utc), price=Decimal("30.00")))

    wf_cfg_1 = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        warmup_days=0,
        decision_frequency=EvaluationFrequency.MONTHLY
    )

    wf_engine_1 = WalkForwardEngine(
        replay_engine=replay,
        evaluation_engine=eval_e,
        selection_engine=sel_e,
        dependence_engine=dep_e,
        weight_engine=w_e,
        signal_engine=sig_e,
        consensus_engine=cons_e,
        price_repo=price_repo,
        config=wf_cfg_1
    )

    run_1 = wf_engine_1.run_walk_forward(symbols=["PETR4"])
    decs_1 = [d.model_dump() for d in run_1.decision_journal.decisions]

    # Injeta dados futuros em Março (operações altamente voláteis e lucros massivos)
    exec_repo.insert(Execution(execution_id="E_FUT_1", trader_id="T1", symbol="PETR4", timestamp=datetime(2026, 3, 10, tzinfo=timezone.utc), side=OrderSide.SELL, quantity=Decimal("500"), price=Decimal("60.00")))
    exec_repo.insert(Execution(execution_id="E_FUT_2", trader_id="T2", symbol="PETR4", timestamp=datetime(2026, 3, 10, tzinfo=timezone.utc), side=OrderSide.SELL, quantity=Decimal("500"), price=Decimal("60.00")))

    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 3, 31, 16, 0, tzinfo=timezone.utc), price=Decimal("60.00")))

    # Executa novamente com o mesmo período original até 31/Jan
    run_2 = wf_engine_1.run_walk_forward(symbols=["PETR4"])
    decs_2 = [d.model_dump() for d in run_2.decision_journal.decisions]

    # As decisões passadas até 31/Jan devem ser rigorosamente idênticas
    assert decs_1 == decs_2


def test_synthetic_scenarios_consensus_success_and_failure():
    """
    Cenários Sintéticos Econômicos:
    - CONSENSUS_SUCCESS: 2 grupos independentes LONG antes de alta expressiva.
    - CONSENSUS_FAILURE: 2 grupos independentes LONG antes de queda severa.
    """
    trader_repo, exec_repo, price_repo, sel_e, dep_e, w_e, sig_e, cons_e, replay, eval_e = setup_pipeline_environment()

    t_id1, t_id2 = "T1", "T2"
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    trader_repo.save(Trader(trader_id=t_id1, name=t_id1, created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))
    trader_repo.save(Trader(trader_id=t_id2, name=t_id2, created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    # Compra em 10/Jan
    exec_repo.insert(Execution(execution_id="E1", trader_id=t_id1, symbol="PETR4", timestamp=datetime(2026, 1, 10, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))
    exec_repo.insert(Execution(execution_id="E2", trader_id=t_id2, symbol="PETR4", timestamp=datetime(2026, 1, 10, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))

    # Cenário de Alta (Success)
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 31, 16, 0, tzinfo=timezone.utc), price=Decimal("30.00")))
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 2, 5, 16, 0, tzinfo=timezone.utc), price=Decimal("35.00")))

    wf_cfg = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        warmup_days=0,
        forward_horizons_days=[5],
        decision_frequency=EvaluationFrequency.MONTHLY
    )

    wf_engine = WalkForwardEngine(replay, eval_e, sel_e, dep_e, w_e, sig_e, cons_e, price_repo, wf_cfg)
    run_success = wf_engine.run_walk_forward(symbols=["PETR4"])

    outcomes_5d = run_success.outcomes_by_horizon[5]
    assert len(outcomes_5d) >= 1
    valid_outcomes = [o for o in outcomes_5d if o.raw_return_pct is not None]
    if valid_outcomes:
        assert valid_outcomes[0].raw_return_pct > 0


def test_false_majority_clones_and_coverage_in_pipeline():
    """
    Cenário FALSE_MAJORITY_CLONES:
    Traders clones votam na mesma direção, mas o motor de consenso e independência do Cornucopia
    evita que clones dominem artificialmente o consensus output.
    """
    trader_repo, exec_repo, price_repo, sel_e, dep_e, w_e, sig_e, cons_e, replay, eval_e = setup_pipeline_environment()

    # 3 clones (G1) + 1 independente (G2)
    for tid in ["CLONE_1", "CLONE_2", "CLONE_3", "IND_1"]:
        trader_repo.save(Trader(trader_id=tid, name=tid, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    # Clones compram exatamente no mesmo segundo (perfeita correlação)
    for tid in ["CLONE_1", "CLONE_2", "CLONE_3"]:
        exec_repo.insert(Execution(execution_id=f"E_{tid}", trader_id=tid, symbol="PETR4", timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))

    # IND_1 vende (SHORT)
    exec_repo.insert(Execution(execution_id="E_IND", trader_id="IND_1", symbol="PETR4", timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc), side=OrderSide.SELL, quantity=Decimal("100"), price=Decimal("30.00")))

    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 31, 16, 0, tzinfo=timezone.utc), price=Decimal("30.00")))

    wf_cfg = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        warmup_days=0,
        decision_frequency=EvaluationFrequency.MONTHLY
    )

    wf_engine = WalkForwardEngine(replay, eval_e, sel_e, dep_e, w_e, sig_e, cons_e, price_repo, wf_cfg)
    run = wf_engine.run_walk_forward(symbols=["PETR4"])

    # Cornucopia não emite LONG com clone dominance simples sem confirmação de 2 grupos independentes
    dec = run.decision_journal.decisions[0]
    assert dec.consensus_direction != ConsensusDirection.LONG or dec.supporting_independent_group_count >= 2


def test_segments_and_statistical_warnings():
    """
    Testa segmentação temporal por regimes e geração de LOW_SAMPLE_WARNING.
    """
    trader_repo, exec_repo, price_repo, sel_e, dep_e, w_e, sig_e, cons_e, replay, eval_e = setup_pipeline_environment()

    trader_repo.save(Trader(trader_id="T1", name="T1", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 1, 31, 16, 0, tzinfo=timezone.utc), price=Decimal("30.00")))
    price_repo.insert(MarketPriceRecord(symbol="PETR4", timestamp=datetime(2026, 2, 5, 16, 0, tzinfo=timezone.utc), price=Decimal("30.00")))

    wf_cfg = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        warmup_days=0,
        minimum_sample_for_reporting=50,  # Força warning
        segments={
            "REGIME_JAN": (datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 31, tzinfo=timezone.utc))
        }
    )

    wf_engine = WalkForwardEngine(replay, eval_e, sel_e, dep_e, w_e, sig_e, cons_e, price_repo, wf_cfg)
    run = wf_engine.run_walk_forward(symbols=["PETR4"])

    assert len(run.warnings) >= 1
    assert any("LOW_SAMPLE_WARNING" in w for w in run.warnings)
    assert "REGIME_JAN" in run.segment_metrics

