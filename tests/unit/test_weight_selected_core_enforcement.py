from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.selection_config import SelectionConfig
from src.config.weight_config import WeightConfig
from src.dependence.engine import TraderDependenceEngine
from src.domain.enums import AssetClass, TraderStatus
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.evaluation.engine import TraderEvaluationEngine
from src.evaluation.models import QualificationStatus, ScoreTrend
from src.replay.engine import TraderReplayEngine
from src.selection.engine import TraderSelectionEngine
from src.selection.models import (
    SelectedCoreSnapshot,
    SelectionStatus,
    TraderSelectionDecision,
)
from src.storage.database import DatabaseManager as SQLiteDatabaseManager
from src.storage.repositories import (
    SQLiteExecutionRepository,
    SQLiteInstrumentRepository,
    SQLiteMarketPriceRepository,
    SQLiteTraderRepository,
)
from src.synthetic.generator import SyntheticDataGenerator
from src.weighting.engine import TraderWeightEngine


def setup_selection_and_weight_engines():
    db = SQLiteDatabaseManager(":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    petr4 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    vale3 = MarketInstrument(symbol="VALE3", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    inst_repo.save(petr4)
    inst_repo.save(vale3)

    replay = TraderReplayEngine(trader_repo, inst_repo, exec_repo, price_repo)
    eval_engine = TraderEvaluationEngine(replay)
    dep_engine = TraderDependenceEngine(replay)
    sel_engine = TraderSelectionEngine(evaluation_engine=eval_engine, config=SelectionConfig())
    weight_engine = TraderWeightEngine(
        evaluation_engine=eval_engine,
        dependence_engine=dep_engine,
        selection_engine=sel_engine
    )
    return trader_repo, exec_repo, sel_engine, weight_engine


def test_selected_core_enforcement_and_non_selected_exclusion():
    """
    Hardening 1: Somente traders em SELECTED recebem peso operacional.
    CANDIDATE, WATCHLIST, SUSPENDED, EXCLUDED não recebem peso.
    """
    trader_repo, exec_repo, sel_engine, weight_engine = setup_selection_and_weight_engines()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    # Cria traders no repositório
    for tid in ["T_SEL1", "T_SEL2", "T_CAND", "T_WATCH", "T_SUSP", "T_EXCL"]:
        trader_repo.save(Trader(trader_id=tid, name=tid, created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    gen = SyntheticDataGenerator(seed=42)
    start_dt = datetime(2026, 1, 5, tzinfo=timezone.utc)
    for tid, sym in [("T_SEL1", "PETR4"), ("T_SEL2", "VALE3"), ("T_CAND", "PETR4"), ("T_WATCH", "VALE3"), ("T_SUSP", "PETR4"), ("T_EXCL", "VALE3")]:
        for e in gen.generate_profile_steady_survivor(tid, symbol=sym, start_date=start_dt, trade_count=30):
            exec_repo.insert(e)

    # Constrói snapshot oficial de seleção contendo múltiplos estados
    decisions = [
        TraderSelectionDecision(trader_id="T_SEL1", as_of=as_of, previous_status=SelectionStatus.CANDIDATE, new_status=SelectionStatus.SELECTED, survivor_score=90.0, qualification_status=QualificationStatus.QUALIFIED, score_trend=ScoreTrend.STABLE),
        TraderSelectionDecision(trader_id="T_SEL2", as_of=as_of, previous_status=SelectionStatus.CANDIDATE, new_status=SelectionStatus.SELECTED, survivor_score=88.0, qualification_status=QualificationStatus.QUALIFIED, score_trend=ScoreTrend.STABLE),
        TraderSelectionDecision(trader_id="T_CAND", as_of=as_of, previous_status=SelectionStatus.INSUFFICIENT_DATA, new_status=SelectionStatus.CANDIDATE, survivor_score=85.0, qualification_status=QualificationStatus.QUALIFIED, score_trend=ScoreTrend.STABLE),
        TraderSelectionDecision(trader_id="T_WATCH", as_of=as_of, previous_status=SelectionStatus.SELECTED, new_status=SelectionStatus.WATCHLIST, survivor_score=78.0, qualification_status=QualificationStatus.QUALIFIED, score_trend=ScoreTrend.DETERIORATING),
        TraderSelectionDecision(trader_id="T_SUSP", as_of=as_of, previous_status=SelectionStatus.WATCHLIST, new_status=SelectionStatus.SUSPENDED, survivor_score=50.0, qualification_status=QualificationStatus.DISQUALIFIED, score_trend=ScoreTrend.DETERIORATING),
        TraderSelectionDecision(trader_id="T_EXCL", as_of=as_of, previous_status=SelectionStatus.CANDIDATE, new_status=SelectionStatus.EXCLUDED, survivor_score=30.0, qualification_status=QualificationStatus.DISQUALIFIED, score_trend=ScoreTrend.DETERIORATING),
    ]

    selected_core_snap = SelectedCoreSnapshot(
        as_of=as_of,
        selected_traders=[d for d in decisions if d.new_status == SelectionStatus.SELECTED],
        all_trader_decisions=decisions,
        selected_count=2,
        candidate_count=1,
        watchlist_count=1,
        suspended_count=1,
        excluded_count=1,
        insufficient_data_count=0
    )

    # 1. Executa Weight Engine consumindo o SelectedCoreSnapshot
    cfg = WeightConfig(maximum_trader_weight=1.0)
    core_weights = weight_engine.calculate_core_weights(as_of=as_of, selected_core=selected_core_snap, config=cfg)

    # Apenas T_SEL1 e T_SEL2 devem estar no núcleo ponderado ativo
    assert set(core_weights.selected_traders) == {"T_SEL1", "T_SEL2"}
    assert "T_CAND" not in core_weights.weights_map
    assert "T_WATCH" not in core_weights.weights_map
    assert "T_SUSP" not in core_weights.weights_map
    assert "T_EXCL" not in core_weights.weights_map

    # Soma dos pesos elegíveis = 1.0
    assert abs(core_weights.total_normalized_weight - 1.0) < 1e-4

    # 2. Fornecer manualmente IDs não selecionados no modo operacional NÃO pode fazê-los entrar
    core_filtered = weight_engine.calculate_core_weights(
        as_of=as_of,
        selected_core=selected_core_snap,
        trader_ids=["T_SEL1", "T_CAND", "T_SUSP"],
        config=cfg
    )
    # Apenas T_SEL1 é válido dos IDs passados
    assert core_filtered.selected_traders == ["T_SEL1"]
    assert core_filtered.weights_map["T_SEL1"].normalized_weight == 1.0


def test_point_in_time_state_transition_isolation():
    """
    Trader SELECTED em T1 e SUSPENDED em T2:
    Em T1 tem weight > 0.
    Em T2 tem weight = 0.
    O evento futuro em T2 não altera o peso histórico de T1.
    """
    trader_repo, exec_repo, sel_engine, weight_engine = setup_selection_and_weight_engines()
    t1 = datetime(2026, 1, 31, tzinfo=timezone.utc)
    t2 = datetime(2026, 2, 28, tzinfo=timezone.utc)

    for tid in ["T1", "T2_CONTROL"]:
        trader_repo.save(Trader(trader_id=tid, name=tid, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    gen = SyntheticDataGenerator(seed=77)
    for e in gen.generate_profile_steady_survivor("T1", symbol="PETR4", start_date=datetime(2026, 1, 5, tzinfo=timezone.utc), trade_count=30):
        exec_repo.insert(e)
    for e in gen.generate_profile_steady_survivor("T2_CONTROL", symbol="VALE3", start_date=datetime(2026, 1, 5, tzinfo=timezone.utc), trade_count=30):
        exec_repo.insert(e)

    # Em T1: T1 e T2_CONTROL estão SELECTED
    d_t1_1 = TraderSelectionDecision(trader_id="T1", as_of=t1, previous_status=SelectionStatus.CANDIDATE, new_status=SelectionStatus.SELECTED, survivor_score=90.0, qualification_status=QualificationStatus.QUALIFIED, score_trend=ScoreTrend.STABLE)
    d_t1_2 = TraderSelectionDecision(trader_id="T2_CONTROL", as_of=t1, previous_status=SelectionStatus.CANDIDATE, new_status=SelectionStatus.SELECTED, survivor_score=88.0, qualification_status=QualificationStatus.QUALIFIED, score_trend=ScoreTrend.STABLE)
    core_t1 = SelectedCoreSnapshot(
        as_of=t1,
        selected_traders=[d_t1_1, d_t1_2],
        all_trader_decisions=[d_t1_1, d_t1_2],
        selected_count=2, candidate_count=0, watchlist_count=0, suspended_count=0, excluded_count=0, insufficient_data_count=0
    )

    # Em T2: T1 foi SUSPENSO por perda e T2_CONTROL continua SELECTED
    d_t2_2 = TraderSelectionDecision(trader_id="T2_CONTROL", as_of=t2, previous_status=SelectionStatus.SELECTED, new_status=SelectionStatus.SELECTED, survivor_score=88.0, qualification_status=QualificationStatus.QUALIFIED, score_trend=ScoreTrend.STABLE)
    d_t2_1 = TraderSelectionDecision(trader_id="T1", as_of=t2, previous_status=SelectionStatus.SELECTED, new_status=SelectionStatus.SUSPENDED, survivor_score=40.0, qualification_status=QualificationStatus.DISQUALIFIED, score_trend=ScoreTrend.DETERIORATING, reasons=["DRAWDOWN_BREACH"])
    core_t2 = SelectedCoreSnapshot(
        as_of=t2,
        selected_traders=[d_t2_2],
        all_trader_decisions=[d_t2_1, d_t2_2],
        selected_count=1, candidate_count=0, watchlist_count=0, suspended_count=1, excluded_count=0, insufficient_data_count=0
    )

    cfg = WeightConfig(maximum_trader_weight=1.0)
    w_snap_t1 = weight_engine.calculate_core_weights(as_of=t1, selected_core=core_t1, config=cfg)
    w_snap_t2 = weight_engine.calculate_core_weights(as_of=t2, selected_core=core_t2, config=cfg)

    # Em T1: T1 recebeu peso operacional
    assert "T1" in w_snap_t1.weights_map
    assert w_snap_t1.weights_map["T1"].normalized_weight > 0.30

    # Em T2: T1 NÃO recebe peso operacional
    assert "T1" not in w_snap_t2.weights_map
    assert w_snap_t2.weights_map["T2_CONTROL"].normalized_weight == 1.0

    # Reavaliação de T1 não sofre efeito de T2
    w_snap_t1_recheck = weight_engine.calculate_core_weights(as_of=t1, selected_core=core_t1, config=cfg)
    assert w_snap_t1.weights_map["T1"].normalized_weight == w_snap_t1_recheck.weights_map["T1"].normalized_weight


def test_diagnostic_weights_api_separation():
    """
    A API calculate_diagnostic_weights é explicitamente separada e permite análises diagnósticas.
    """
    trader_repo, exec_repo, _, weight_engine = setup_selection_and_weight_engines()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    for tid in ["T_DIAG1", "T_DIAG2"]:
        trader_repo.save(Trader(trader_id=tid, name=tid, created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    gen = SyntheticDataGenerator(seed=12)
    for e in gen.generate_profile_steady_survivor("T_DIAG1", symbol="PETR4", start_date=datetime(2026, 1, 5, tzinfo=timezone.utc), trade_count=20):
        exec_repo.insert(e)
    for e in gen.generate_profile_steady_survivor("T_DIAG2", symbol="VALE3", start_date=datetime(2026, 1, 5, tzinfo=timezone.utc), trade_count=20):
        exec_repo.insert(e)

    cfg = WeightConfig(maximum_trader_weight=1.0)
    diag_weights = weight_engine.calculate_diagnostic_weights(as_of=as_of, trader_ids=["T_DIAG1", "T_DIAG2"], config=cfg)

    assert set(diag_weights.selected_traders) == {"T_DIAG1", "T_DIAG2"}
    assert abs(diag_weights.total_normalized_weight - 1.0) < 1e-4
