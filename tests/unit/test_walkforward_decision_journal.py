from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.consensus_config import ConsensusConfig
from src.config.evaluation_config import EvaluationFrequency
from src.config.selection_config import SelectionConfig
from src.config.signal_config import SignalConfig
from src.config.walkforward_config import WalkForwardConfig
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
from src.selection.policy import TraderSelectionPolicy
from src.signals.engine import TraderSignalEngine
from src.storage.database import DatabaseManager as SQLiteDatabaseManager
from src.storage.repositories import (
    SQLiteExecutionRepository,
    SQLiteInstrumentRepository,
    SQLiteMarketPriceRepository,
    SQLiteTraderRepository,
)
from src.walkforward.decision import WalkForwardDecisionEngine
from src.weighting.engine import TraderWeightEngine


def setup_walkforward_environment():
    db = SQLiteDatabaseManager(":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    petr4 = MarketInstrument(
        symbol="PETR4",
        asset_class=AssetClass.EQUITY,
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        contract_multiplier=Decimal("1.0"),
        currency="BRL"
    )
    inst_repo.save(petr4)

    replay = TraderReplayEngine(trader_repo, inst_repo, exec_repo, price_repo)
    eval_engine = TraderEvaluationEngine(replay)
    sel_engine = TraderSelectionEngine(eval_engine)
    dep_engine = TraderDependenceEngine(replay)
    weight_engine = TraderWeightEngine(eval_engine, dep_engine, selection_engine=sel_engine)
    sig_engine = TraderSignalEngine(replay)
    cons_engine = ConsensusEngine(sig_engine)

    return trader_repo, exec_repo, price_repo, sel_engine, dep_engine, weight_engine, sig_engine, cons_engine


def test_chronological_decisions_and_warmup():
    """
    Verifica se as decisões respeitam o período de warm-up e são ordenadas cronologicamente.
    """
    trader_repo, exec_repo, price_repo, sel_e, dep_e, w_e, sig_e, cons_e = setup_walkforward_environment()
    
    t1 = Trader(trader_id="T1", name="T1", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00"))
    trader_repo.save(t1)

    wf_cfg = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 4, 30, tzinfo=timezone.utc),
        warmup_days=60,
        decision_frequency=EvaluationFrequency.MONTHLY
    )

    dec_engine = WalkForwardDecisionEngine(sel_e, dep_e, w_e, sig_e, cons_e, wf_cfg)
    timestamps = dec_engine.generate_decision_timestamps()

    # Com 60 dias de warm-up a partir de 01/Jan, a primeira decisão deve ser >= 02/Março
    assert len(timestamps) >= 2
    assert all(ts >= datetime(2026, 3, 1, tzinfo=timezone.utc) for ts in timestamps)


def test_empty_core_records_insufficient_coverage_without_inventing_traders():
    """
    Se nenhum trader for qualificado/selecionado em 'as_of', o motor deve registrar
    INSUFFICIENT_COVERAGE com selected_core_count = 0.
    """
    trader_repo, exec_repo, price_repo, sel_e, dep_e, w_e, sig_e, cons_e = setup_walkforward_environment()

    wf_cfg = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        warmup_days=0,
        decision_frequency=EvaluationFrequency.MONTHLY
    )

    dec_engine = WalkForwardDecisionEngine(sel_e, dep_e, w_e, sig_e, cons_e, wf_cfg)
    journal = dec_engine.build_decision_journal(symbols=["PETR4"])

    assert journal.total_decisions >= 1
    for dec in journal.decisions:
        assert dec.selected_core_count == 0
        assert dec.consensus_direction == ConsensusDirection.INSUFFICIENT_COVERAGE


def test_decision_determinism_and_immutability():
    """
    Múltiplas execuções com a mesma base e configuração produzem exatamente as mesmas decisões.
    """
    trader_repo, exec_repo, price_repo, sel_e, dep_e, w_e, sig_e, cons_e = setup_walkforward_environment()

    wf_cfg = WalkForwardConfig(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        warmup_days=0,
        decision_frequency=EvaluationFrequency.MONTHLY
    )

    dec_engine = WalkForwardDecisionEngine(sel_e, dep_e, w_e, sig_e, cons_e, wf_cfg)
    j1 = dec_engine.build_decision_journal(symbols=["PETR4"])
    j2 = dec_engine.build_decision_journal(symbols=["PETR4"])

    assert j1.total_decisions == j2.total_decisions
    for d1, d2 in zip(j1.decisions, j2.decisions):
        assert d1.model_dump() == d2.model_dump()
