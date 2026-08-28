from datetime import datetime, timezone
from decimal import Decimal
import pytest
from src.config.consensus_config import ConsensusConfig
from src.consensus.engine import ConsensusEngine
from src.consensus.models import ConsensusDirection
from src.domain.enums import AssetClass, OrderSide, PositionSide, TraderStatus
from src.domain.execution import Execution
from src.domain.instrument import MarketInstrument
from src.domain.trader import Trader
from src.replay.engine import TraderReplayEngine
from src.signals.engine import TraderSignalEngine
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
        survivor_score=88.0,
        redundancy_group_id=gid,
        sample_status="SUFFICIENT",
        quality_component=0.88,
        independence_component=0.80,
        confidence_component=0.90,
        raw_weight=0.63,
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


def test_core_consensus_pipeline_section_43_integrated_scenario():
    """
    Cenário Integrado Oficial (Seção 43):
    Traders: A (22%, G1), B (11%, G1), C (8%, G1), D (24%, G2), E (20%, G3), F (15%, G4)
    Posições em PETR4:
    A: LONG, B: LONG, C: LONG, D: LONG
    E: SHORT
    F: NO_OPINION (nunca operou)
    
    Verificações:
    - 4 traders apoiando LONG
    - 2 grupos independentes apoiando LONG (G1 e G2)
    - Oposição de E (SHORT 20% <= 25%)
    - Ausência de F (15% preservado no denominador)
    - Suporte real: 65%, Cobertura: 85%, Margem: 45%
    - Decisão final: LONG
    """
    db = SQLiteDatabaseManager(":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    petr4 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    vale3 = MarketInstrument(symbol="VALE3", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    inst_repo.save(petr4)
    inst_repo.save(vale3)

    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for tid in ["A", "B", "C", "D", "E", "F"]:
        trader_repo.save(Trader(trader_id=tid, name=tid, created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    # Execuções em PETR4
    # A, B, C, D abrem LONG
    exec_repo.insert(Execution(execution_id="E_A", trader_id="A", symbol="PETR4", timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))
    exec_repo.insert(Execution(execution_id="E_B", trader_id="B", symbol="PETR4", timestamp=datetime(2026, 1, 10, 10, 5, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("50"), price=Decimal("30.00")))
    exec_repo.insert(Execution(execution_id="E_C", trader_id="C", symbol="PETR4", timestamp=datetime(2026, 1, 10, 10, 10, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("30"), price=Decimal("30.00")))
    exec_repo.insert(Execution(execution_id="E_D", trader_id="D", symbol="PETR4", timestamp=datetime(2026, 1, 10, 10, 15, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("150"), price=Decimal("30.00")))

    # E abre SHORT
    exec_repo.insert(Execution(execution_id="E_E", trader_id="E", symbol="PETR4", timestamp=datetime(2026, 1, 10, 10, 20, tzinfo=timezone.utc), side=OrderSide.SELL, quantity=Decimal("100"), price=Decimal("30.00")))

    # F nunca operou PETR4 (opera VALE3)
    exec_repo.insert(Execution(execution_id="E_F", trader_id="F", symbol="VALE3", timestamp=datetime(2026, 1, 10, 10, 25, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("200"), price=Decimal("60.00")))

    as_of = datetime(2026, 1, 31, tzinfo=timezone.utc)

    # CoreWeightSnapshot da Etapa 6
    w_snap = make_core_weight_snapshot(as_of, [
        ("A", 0.22, 1),
        ("B", 0.11, 1),
        ("C", 0.08, 1),
        ("D", 0.24, 2),
        ("E", 0.20, 3),
        ("F", 0.15, 4),
    ])

    replay = TraderReplayEngine(trader_repo, inst_repo, exec_repo, price_repo)
    sig_engine = TraderSignalEngine(replay)
    cons_engine = ConsensusEngine(sig_engine)

    # Executa Consenso do Núcleo
    cfg = ConsensusConfig(
        minimum_coverage_weight=0.50,
        minimum_directional_agreement=0.70,
        minimum_supporting_independent_groups=2,
        maximum_opposition_weight=0.25
    )
    core_consensus = cons_engine.calculate_core_consensus(as_of, w_snap, config=cfg)

    # Verificações de PETR4
    petr4_cons = core_consensus.consensus_by_instrument["PETR4"]
    assert petr4_cons.consensus_direction == ConsensusDirection.LONG
    assert petr4_cons.long_weight == pytest.approx(0.65, abs=1e-3)
    assert petr4_cons.short_weight == pytest.approx(0.20, abs=1e-3)
    assert petr4_cons.no_opinion_weight == pytest.approx(0.15, abs=1e-3)
    assert petr4_cons.coverage_weight == pytest.approx(0.85, abs=1e-3)
    assert petr4_cons.consensus_margin == pytest.approx(0.45, abs=1e-3)

    # Grupos independentes
    assert len(petr4_cons.long_supporting_traders) == 4
    assert petr4_cons.long_supporting_group_count == 2
    assert set(petr4_cons.long_supporting_groups) == {1, 2}
    assert petr4_cons.short_supporting_group_count == 1
    assert set(petr4_cons.short_supporting_groups) == {3}

    # Verificações de VALE3
    vale3_cons = core_consensus.consensus_by_instrument["VALE3"]
    # Em VALE3: apenas F (15%) está LONG, os outros 85% são NO_OPINION -> INSUFFICIENT_COVERAGE
    assert vale3_cons.consensus_direction == ConsensusDirection.INSUFFICIENT_COVERAGE
    assert vale3_cons.coverage_weight == pytest.approx(0.15, abs=1e-3)

    assert core_consensus.long_consensus_count == 1
    assert core_consensus.insufficient_coverage_count == 1


def test_longitudinal_consensus_series_and_flips():
    """
    Testa série histórica longitudinal com transição e flip:
    Mês 1 (Jan): PETR4 LONG
    Mês 2 (Fev): PETR4 NO_CONSENSUS (disputa)
    Mês 3 (Mar): PETR4 SHORT (flip completo)
    """
    db = SQLiteDatabaseManager(":memory:")
    trader_repo = SQLiteTraderRepository(db)
    inst_repo = SQLiteInstrumentRepository(db)
    exec_repo = SQLiteExecutionRepository(db)
    price_repo = SQLiteMarketPriceRepository(db)

    petr4 = MarketInstrument(symbol="PETR4", asset_class=AssetClass.EQUITY, tick_size=Decimal("0.01"), tick_value=Decimal("0.01"), contract_multiplier=Decimal("1.0"), currency="BRL")
    inst_repo.save(petr4)

    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for tid in ["T1", "T2"]:
        trader_repo.save(Trader(trader_id=tid, name=tid, created_at=base_time, status=TraderStatus.ACTIVE, initial_capital=Decimal("10000.00")))

    # Jan: T1 e T2 compram PETR4
    exec_repo.insert(Execution(execution_id="E1", trader_id="T1", symbol="PETR4", timestamp=datetime(2026, 1, 10, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))
    exec_repo.insert(Execution(execution_id="E2", trader_id="T2", symbol="PETR4", timestamp=datetime(2026, 1, 10, tzinfo=timezone.utc), side=OrderSide.BUY, quantity=Decimal("100"), price=Decimal("30.00")))

    # Fev: T2 reverte para SHORT
    exec_repo.insert(Execution(execution_id="E3", trader_id="T2", symbol="PETR4", timestamp=datetime(2026, 2, 10, tzinfo=timezone.utc), side=OrderSide.SELL, quantity=Decimal("200"), price=Decimal("31.00")))

    # Mar: T1 também reverte para SHORT
    exec_repo.insert(Execution(execution_id="E4", trader_id="T1", symbol="PETR4", timestamp=datetime(2026, 3, 10, tzinfo=timezone.utc), side=OrderSide.SELL, quantity=Decimal("200"), price=Decimal("32.00")))

    t1 = datetime(2026, 1, 31, tzinfo=timezone.utc)
    t2 = datetime(2026, 2, 28, tzinfo=timezone.utc)
    t3 = datetime(2026, 3, 31, tzinfo=timezone.utc)

    w_t1 = make_core_weight_snapshot(t1, [("T1", 0.50, 1), ("T2", 0.50, 2)])
    w_t2 = make_core_weight_snapshot(t2, [("T1", 0.50, 1), ("T2", 0.50, 2)])
    w_t3 = make_core_weight_snapshot(t3, [("T1", 0.50, 1), ("T2", 0.50, 2)])

    replay = TraderReplayEngine(trader_repo, inst_repo, exec_repo, price_repo)
    sig_engine = TraderSignalEngine(replay)
    cons_engine = ConsensusEngine(sig_engine)

    snaps, turnovers = cons_engine.calculate_consensus_series(
        start=t1,
        end=t3,
        weight_series=[w_t1, w_t2, w_t3],
        symbols=["PETR4"]
    )

    assert len(snaps) == 3
    assert snaps[0].consensus_by_instrument["PETR4"].consensus_direction == ConsensusDirection.LONG
    assert snaps[1].consensus_by_instrument["PETR4"].consensus_direction == ConsensusDirection.NO_CONSENSUS
    assert snaps[2].consensus_by_instrument["PETR4"].consensus_direction == ConsensusDirection.SHORT

    assert len(turnovers) == 2
    assert turnovers[0].direction_changes_count == 1
    assert turnovers[1].direction_changes_count == 1
    # De NO_CONSENSUS para SHORT não é flip direto
    assert turnovers[1].flips_count == 0
