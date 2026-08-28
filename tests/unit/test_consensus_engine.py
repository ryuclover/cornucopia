from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.consensus_config import ConsensusConfig
from src.consensus.engine import ConsensusEngine
from src.consensus.models import ConsensusDirection
from src.domain.enums import AssetClass, TraderStatus
from src.domain.instrument import MarketInstrument
from src.replay.engine import TraderReplayEngine
from src.signals.engine import TraderSignalEngine
from src.signals.models import SignalState, TraderSignal
from src.storage.database import DatabaseManager as SQLiteDatabaseManager
from src.storage.repositories import (
    SQLiteExecutionRepository,
    SQLiteInstrumentRepository,
    SQLiteMarketPriceRepository,
    SQLiteTraderRepository,
)
from src.weighting.diagnostics import WeightDiagnosticsCalculator
from src.weighting.models import CoreWeightSnapshot, TraderWeight


def make_trader_weight(tid: str, as_of: datetime, weight: float, gid: int | None = None) -> TraderWeight:
    return TraderWeight(
        trader_id=tid,
        as_of=as_of,
        survivor_score=85.0,
        redundancy_group_id=gid,
        sample_status="SUFFICIENT",
        quality_component=0.85,
        independence_component=0.80,
        confidence_component=0.90,
        raw_weight=0.612,
        normalized_weight=weight,
        weight_pct=round(weight * 100.0, 2),
        caps_applied=[],
        reasons=[]
    )


def make_core_weight_snapshot(as_of: datetime, weights_spec: list[tuple[str, float, int | None]]) -> CoreWeightSnapshot:
    tw_list = [make_trader_weight(tid, as_of, w, gid) for tid, w, gid in weights_spec]
    weights_map = {tw.trader_id: tw for tw in tw_list}
    tot_w = round(sum(w for _, w, _ in weights_spec), 4)
    conc = WeightDiagnosticsCalculator.calculate_concentration(tw_list, [])
    return CoreWeightSnapshot(
        as_of=as_of,
        selected_traders=[tw.trader_id for tw in tw_list],
        selected_trader_ids=[tw.trader_id for tw in tw_list],
        trader_weights=tw_list,
        weights_map=weights_map,
        group_summaries=[],
        concentration_metrics=conc,
        effective_trader_count=conc.effective_trader_count,
        highest_weight_trader_id=tw_list[0].trader_id if tw_list else None,
        highest_weight_pct=tw_list[0].weight_pct if tw_list else 0.0,
        lowest_weight_trader_id=tw_list[-1].trader_id if tw_list else None,
        lowest_weight_pct=tw_list[-1].weight_pct if tw_list else 0.0,
        total_normalized_weight=tot_w,
        diagnostics={}
    )


def setup_consensus_engine():
    db = SQLiteDatabaseManager(":memory:")
    replay = TraderReplayEngine(
        SQLiteTraderRepository(db),
        SQLiteInstrumentRepository(db),
        SQLiteExecutionRepository(db),
        SQLiteMarketPriceRepository(db)
    )
    sig_engine = TraderSignalEngine(replay)
    cons_engine = ConsensusEngine(sig_engine)
    return cons_engine


def test_strong_long_and_short_consensus():
    """
    Consenso robusto LONG e SHORT com múltiplos grupos independentes e alta concordância.
    """
    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    # Core com 4 traders independentes de 25% cada
    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.25, 1),
        ("T2", 0.25, 2),
        ("T3", 0.25, 3),
        ("T4", 0.25, None)
    ])

    # T1, T2, T3 = LONG (75%), T4 = FLAT (25%) -> LONG
    signals_long = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T3", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T4", symbol="PETR4", as_of=as_of, signal_state=SignalState.FLAT, normalized_exposure=0.0),
    ]

    res_long = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals_long)
    assert res_long.consensus_direction == ConsensusDirection.LONG
    assert res_long.long_weight == 0.75
    assert res_long.short_weight == 0.0
    assert res_long.directional_agreement_long == 1.0
    assert res_long.long_supporting_group_count == 3

    # T1, T2, T3 = SHORT (75%), T4 = NO_OPINION (25%) -> SHORT
    signals_short = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.SHORT, normalized_exposure=-1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.SHORT, normalized_exposure=-1.0),
        TraderSignal(trader_id="T3", symbol="PETR4", as_of=as_of, signal_state=SignalState.SHORT, normalized_exposure=-1.0),
        TraderSignal(trader_id="T4", symbol="PETR4", as_of=as_of, signal_state=SignalState.NO_OPINION, normalized_exposure=0.0),
    ]

    res_short = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals_short)
    assert res_short.consensus_direction == ConsensusDirection.SHORT
    assert res_short.short_weight == 0.75
    assert res_short.directional_agreement_short == 1.0


def test_directional_conflict_produces_no_consensus():
    """
    Disputa direcional acirrada (LONG 40% vs SHORT 40%) -> NO_CONSENSUS.
    """
    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.40, 1),
        ("T2", 0.40, 2),
        ("T3", 0.20, 3)
    ])

    signals = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.SHORT, normalized_exposure=-1.0),
        TraderSignal(trader_id="T3", symbol="PETR4", as_of=as_of, signal_state=SignalState.FLAT, normalized_exposure=0.0),
    ]

    res = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals)
    assert res.consensus_direction == ConsensusDirection.NO_CONSENSUS
    assert res.consensus_margin == 0.0


def test_low_coverage_produces_insufficient_coverage():
    """
    Apenas 1 trader com 20% de peso tem opinião LONG e 80% é NO_OPINION.
    Mesmo com 100% de concordância direcional, coverage = 20% < 50% -> INSUFFICIENT_COVERAGE.
    """
    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.20, 1),
        ("T2", 0.40, 2),
        ("T3", 0.40, 3)
    ])

    signals = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.NO_OPINION, normalized_exposure=0.0),
        TraderSignal(trader_id="T3", symbol="PETR4", as_of=as_of, signal_state=SignalState.NO_OPINION, normalized_exposure=0.0),
    ]

    res = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals)
    assert res.consensus_direction == ConsensusDirection.INSUFFICIENT_COVERAGE
    assert res.coverage_weight == 0.20


def test_majority_flat_produces_neutral():
    """
    Alta cobertura (80%), mas 70% está FLAT e apenas 10% direcional -> NEUTRAL.
    """
    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.70, 1),
        ("T2", 0.10, 2),
        ("T3", 0.20, 3)
    ])

    signals = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.FLAT, normalized_exposure=0.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T3", symbol="PETR4", as_of=as_of, signal_state=SignalState.NO_OPINION, normalized_exposure=0.0),
    ]

    res = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals)
    assert res.consensus_direction == ConsensusDirection.NEUTRAL
    assert res.flat_weight == 0.70


def test_single_redundancy_group_blocked_for_independence():
    """
    3 traders com 70% de peso apoiam LONG, mas TODOS pertencem ao mesmo Redundancy Group (Group 1).
    Como minimum_supporting_independent_groups = 2, deve ser barrado em NO_CONSENSUS.
    """
    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.25, 1),
        ("T2", 0.25, 1),
        ("T3", 0.20, 1),
        ("T4", 0.30, 2)
    ])

    signals = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T3", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T4", symbol="PETR4", as_of=as_of, signal_state=SignalState.NO_OPINION, normalized_exposure=0.0),
    ]

    cfg = ConsensusConfig(minimum_supporting_independent_groups=2)
    res = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals, config=cfg)

    assert res.consensus_direction == ConsensusDirection.NO_CONSENSUS
    assert res.long_weight == 0.70
    assert len(res.long_supporting_traders) == 3
    assert res.long_supporting_group_count == 1
    assert "INSUFFICIENT_INDEPENDENT_GROUPS" in res.triggered_rules


def test_timestamp_mismatch_and_non_core_injection():
    """
    Validação de segurança:
    - Timestamp mismatch levanta ValueError.
    - Sinais de traders fora do Core são descartados e não influenciam pesos.
    """
    engine = setup_consensus_engine()
    t1 = datetime(2026, 1, 31, tzinfo=timezone.utc)
    t2 = datetime(2026, 2, 28, tzinfo=timezone.utc)

    w_snap = make_core_weight_snapshot(t1, [
        ("T1", 0.50, 1),
        ("T2", 0.50, 2)
    ])

    # 1. Timestamp mismatch
    with pytest.raises(ValueError, match="Inconsistência temporal"):
        engine.calculate_instrument_consensus("PETR4", t2, w_snap, [])

    # 2. Injeção de trader fora do core (T_INTRUDER)
    signals = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=t1, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=t1, signal_state=SignalState.FLAT, normalized_exposure=0.0),
        TraderSignal(trader_id="T_INTRUDER", symbol="PETR4", as_of=t1, signal_state=SignalState.SHORT, normalized_exposure=-1.0),
    ]

    res = engine.calculate_instrument_consensus("PETR4", t1, w_snap, signals)
    assert "T_INTRUDER" not in res.short_supporting_traders
    assert res.short_weight == 0.0
    assert res.long_weight == 0.50


def test_consensus_presets_and_diagnostics():
    """
    Testa carregamento de presets de consenso e cálculo de estabilidade longitudinal.
    """
    from src.config.consensus_config import ConsensusPreset
    from src.consensus.diagnostics import ConsensusDiagnosticsCalculator
    from src.consensus.models import CoreConsensusSnapshot

    cfg_cons = ConsensusConfig.from_preset(ConsensusPreset.CONSERVATIVE)
    assert cfg_cons.minimum_coverage_weight == 0.60
    assert cfg_cons.minimum_directional_agreement == 0.80

    cfg_high = ConsensusConfig.from_preset(ConsensusPreset.HIGH_CONSENSUS)
    assert cfg_high.minimum_supporting_independent_groups == 3

    cfg_exp = ConsensusConfig.from_preset(ConsensusPreset.EXPLORATORY)
    assert cfg_exp.minimum_coverage_weight == 0.35

    # Diagnósticos de estabilidade
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)
    snap1 = CoreConsensusSnapshot(
        as_of=as_of,
        weight_snapshot_as_of=as_of,
        instruments=["PETR4", "VALE3"],
        consensus_by_instrument={},
        long_consensus_count=1,
        short_consensus_count=0,
        neutral_count=0,
        no_consensus_count=1,
        insufficient_coverage_count=0,
        total_instruments_analyzed=2
    )

    metrics = ConsensusDiagnosticsCalculator.calculate_longitudinal_stability([snap1])
    assert metrics["total_snapshots"] == 1
    assert metrics["long_consensus_rate_pct"] == 50.0
    assert metrics["no_consensus_rate_pct"] == 50.0


def test_unknown_weight_not_redistributed():
    """
    Sinais com UNKNOWN não são redistribuídos silenciosamente nem inflacionam consenso.
    """
    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    # Core: T1 (40%), T2 (30%), T3 (30%)
    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.40, 1),
        ("T2", 0.30, 2),
        ("T3", 0.30, 3)
    ])

    # T1 = LONG, T2 = UNKNOWN, T3 = NO_OPINION
    signals = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.UNKNOWN, normalized_exposure=0.0),
        TraderSignal(trader_id="T3", symbol="PETR4", as_of=as_of, signal_state=SignalState.NO_OPINION, normalized_exposure=0.0),
    ]

    res = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals)
    assert res.unknown_weight == 0.30
    assert res.long_weight == 0.40
    assert res.coverage_weight == 0.40  # UNKNOWN e NO_OPINION não contam como cobertura
    # Cobertura 40% < 50% -> INSUFFICIENT_COVERAGE
    assert res.consensus_direction == ConsensusDirection.INSUFFICIENT_COVERAGE


def test_invalid_weight_sum_fails_explicitly():
    """
    CoreWeightSnapshot com soma dos pesos != 1.0 deve falhar com ValueError.
    """
    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    # Soma = 0.70 != 1.0
    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.40, 1),
        ("T2", 0.30, 2)
    ])

    with pytest.raises(ValueError, match="CoreWeightSnapshot inválido"):
        engine.calculate_instrument_consensus("PETR4", as_of, w_snap, [])


def test_independent_group_confirmation_success():
    """
    Dois grupos independentes apoiando LONG satisfazem minimum_supporting_independent_groups=2.
    """
    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.35, 1),
        ("T2", 0.35, 2),
        ("T3", 0.30, 3)
    ])

    signals = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T3", symbol="PETR4", as_of=as_of, signal_state=SignalState.FLAT, normalized_exposure=0.0),
    ]

    cfg = ConsensusConfig(minimum_supporting_independent_groups=2)
    res = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals, config=cfg)

    assert res.consensus_direction == ConsensusDirection.LONG
    assert res.long_supporting_group_count == 2
    assert set(res.long_supporting_groups) == {1, 2}


def test_no_opinion_inflation_prevented():
    """
    Traders com NO_OPINION não podem inflacionar o suporte percentual sobre o Core total.
    """
    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.20, 1),
        ("T2", 0.80, 2)
    ])

    # T1 = LONG (20%), T2 = NO_OPINION (80%)
    signals = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.NO_OPINION, normalized_exposure=0.0),
    ]

    res = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals)
    assert res.long_weight == 0.20
    assert res.no_opinion_weight == 0.80
    assert res.directional_agreement_long == 1.0  # 100% entre direcionais
    # Mas como coverage é 20% (<50%) e core support é 20% (<35%), é INSUFFICIENT_COVERAGE
    assert res.consensus_direction == ConsensusDirection.INSUFFICIENT_COVERAGE


def test_consensus_determinism():
    """
    Múltiplas execuções com os mesmos dados produzem exatamente os mesmos resultados.
    """
    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.50, 1),
        ("T2", 0.50, 2)
    ])

    signals = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
    ]

    res1 = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals)
    res2 = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals)

    assert res1.model_dump() == res2.model_dump()


def test_tiny_second_group_rejected_for_independent_confirmation():
    """
    G1 LONG = 45%, G2 LONG = 1%.
    Com minimum_independent_group_support_weight = 5% (0.05):
    G2 não conta como segunda confirmação independente -> long_supporting_group_count = 1 (< 2) -> NO_CONSENSUS.
    """
    from src.consensus.models import GroupDirectionalState

    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.45, 1),
        ("T2", 0.01, 2),
        ("T3", 0.54, 3)
    ])

    signals = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T3", symbol="PETR4", as_of=as_of, signal_state=SignalState.FLAT, normalized_exposure=0.0),
    ]

    cfg = ConsensusConfig(
        minimum_supporting_independent_groups=2,
        minimum_independent_group_support_weight=0.05
    )
    res = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals, config=cfg)

    assert res.consensus_direction == ConsensusDirection.NO_CONSENSUS
    assert res.long_supporting_group_count == 1
    assert res.long_supporting_groups == [1]
    assert res.group_direction_breakdown["Group_2"]["direction"] == GroupDirectionalState.NO_OPINION.value
    assert res.group_direction_breakdown["Group_2"]["is_independent_support_long"] is False


def test_meaningful_second_group_accepted():
    """
    G1 LONG = 40%, G2 LONG = 10%.
    Com minimum_independent_group_support_weight = 5% (0.05):
    Ambos os grupos satisfazem o suporte material e pureza -> long_supporting_group_count = 2 -> LONG.
    """
    from src.consensus.models import GroupDirectionalState

    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.40, 1),
        ("T2", 0.10, 2),
        ("T3", 0.50, 3)
    ])

    signals = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T3", symbol="PETR4", as_of=as_of, signal_state=SignalState.FLAT, normalized_exposure=0.0),
    ]

    cfg = ConsensusConfig(
        minimum_supporting_independent_groups=2,
        minimum_independent_group_support_weight=0.05
    )
    res = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals, config=cfg)

    assert res.consensus_direction == ConsensusDirection.LONG
    assert res.long_supporting_group_count == 2
    assert set(res.long_supporting_groups) == {1, 2}
    assert res.group_direction_breakdown["Group_1"]["direction"] == GroupDirectionalState.LONG.value
    assert res.group_direction_breakdown["Group_2"]["direction"] == GroupDirectionalState.LONG.value


def test_internally_conflicted_group_classified_as_conflict():
    """
    G1 possui 2 traders: T1 (LONG 12%) e T2 (SHORT 11%).
    O grupo G1 deve ser classificado como CONFLICT e não contar nem para LONG nem para SHORT.
    """
    from src.consensus.models import GroupDirectionalState

    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.12, 1),
        ("T2", 0.11, 1),
        ("T3", 0.40, 2),
        ("T4", 0.37, 3)
    ])

    signals = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.SHORT, normalized_exposure=-1.0),
        TraderSignal(trader_id="T3", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T4", symbol="PETR4", as_of=as_of, signal_state=SignalState.FLAT, normalized_exposure=0.0),
    ]

    cfg = ConsensusConfig(
        minimum_supporting_independent_groups=2,
        minimum_group_directional_agreement=0.70
    )
    res = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals, config=cfg)

    g1_info = res.group_direction_breakdown["Group_1"]
    assert g1_info["direction"] == GroupDirectionalState.CONFLICT.value
    assert g1_info["is_independent_support_long"] is False
    assert g1_info["is_independent_support_short"] is False
    # Apenas Group 2 confirma LONG, Group 1 está em CONFLICT
    assert res.long_supporting_group_count == 1
    assert res.long_supporting_groups == [2]


def test_strong_internal_long_group_classified_as_long():
    """
    G1: T1 (LONG 18%), T2 (SHORT 2%).
    Concordância interna = 18/20 = 90% >= 70%.
    G1 é classificado como LONG.
    """
    from src.consensus.models import GroupDirectionalState

    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.18, 1),
        ("T2", 0.02, 1),
        ("T3", 0.40, 2),
        ("T4", 0.40, 3)
    ])

    signals = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.SHORT, normalized_exposure=-1.0),
        TraderSignal(trader_id="T3", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T4", symbol="PETR4", as_of=as_of, signal_state=SignalState.FLAT, normalized_exposure=0.0),
    ]

    cfg = ConsensusConfig(
        minimum_supporting_independent_groups=2,
        minimum_group_directional_agreement=0.70
    )
    res = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals, config=cfg)

    g1_info = res.group_direction_breakdown["Group_1"]
    assert g1_info["direction"] == GroupDirectionalState.LONG.value
    assert g1_info["is_independent_support_long"] is True
    assert res.long_supporting_group_count == 2
    assert set(res.long_supporting_groups) == {1, 2}


def test_same_group_cannot_confirm_both_sides_simultaneously():
    """
    Garante explicitamente que nenhum redundancy group apareça simultaneamente em
    long_supporting_groups e short_supporting_groups.
    """
    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.25, 1),
        ("T2", 0.25, 1),
        ("T3", 0.25, 2),
        ("T4", 0.25, 2)
    ])

    signals = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.SHORT, normalized_exposure=-1.0),
        TraderSignal(trader_id="T3", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T4", symbol="PETR4", as_of=as_of, signal_state=SignalState.SHORT, normalized_exposure=-1.0),
    ]

    res = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals)
    assert set(res.long_supporting_groups) & set(res.short_supporting_groups) == set()
    assert res.long_supporting_group_count == 0
    assert res.short_supporting_group_count == 0


def test_multiple_traders_same_group_single_confirmation():
    """
    4 traders no mesmo Grupo 1 todos LONG -> contam como apenas 1 grupo apoiando LONG.
    """
    engine = setup_consensus_engine()
    as_of = datetime(2026, 3, 30, tzinfo=timezone.utc)

    w_snap = make_core_weight_snapshot(as_of, [
        ("T1", 0.20, 1),
        ("T2", 0.20, 1),
        ("T3", 0.20, 1),
        ("T4", 0.20, 1),
        ("T5", 0.20, 2)
    ])

    signals = [
        TraderSignal(trader_id="T1", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T2", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T3", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T4", symbol="PETR4", as_of=as_of, signal_state=SignalState.LONG, normalized_exposure=1.0),
        TraderSignal(trader_id="T5", symbol="PETR4", as_of=as_of, signal_state=SignalState.FLAT, normalized_exposure=0.0),
    ]

    res = engine.calculate_instrument_consensus("PETR4", as_of, w_snap, signals)
    assert len(res.long_supporting_traders) == 4
    assert res.long_supporting_group_count == 1
    assert res.long_supporting_groups == [1]


