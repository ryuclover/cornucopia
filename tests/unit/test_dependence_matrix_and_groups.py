from datetime import datetime, timezone
import random
from src.config.dependence_config import DependenceConfig
from src.dependence.clustering import RedundancyClusterer
from src.dependence.models import DependenceLevel, DependenceMatrix, TraderPairDependence


def test_dependence_matrix_symmetry_and_diagonal():
    dt = datetime(2026, 3, 30, tzinfo=timezone.utc)
    t_ids = ["T1", "T2", "T3"]
    
    p12 = TraderPairDependence(
        trader_a_id="T1", trader_b_id="T2", as_of=dt, overlap_periods=30,
        overlap_trades_a=10, overlap_trades_b=10, sample_status="SUFFICIENT",
        composite_redundancy_score=85.0, dependence_level=DependenceLevel.VERY_HIGH
    )
    p13 = TraderPairDependence(
        trader_a_id="T1", trader_b_id="T3", as_of=dt, overlap_periods=30,
        overlap_trades_a=10, overlap_trades_b=10, sample_status="SUFFICIENT",
        composite_redundancy_score=20.0, dependence_level=DependenceLevel.LOW
    )
    p23 = TraderPairDependence(
        trader_a_id="T2", trader_b_id="T3", as_of=dt, overlap_periods=30,
        overlap_trades_a=10, overlap_trades_b=10, sample_status="SUFFICIENT",
        composite_redundancy_score=25.0, dependence_level=DependenceLevel.LOW
    )

    pairwise_map = {
        "T1:T2": p12, "T2:T1": p12,
        "T1:T3": p13, "T3:T1": p13,
        "T2:T3": p23, "T3:T2": p23,
    }
    matrix_data = [
        [100.0, 85.0, 20.0],
        [85.0, 100.0, 25.0],
        [20.0, 25.0, 100.0]
    ]

    mat = DependenceMatrix(
        as_of=dt,
        trader_ids=t_ids,
        matrix=matrix_data,
        pairwise_map=pairwise_map
    )

    # Verifica simetria e diagonal
    for i in range(len(t_ids)):
        assert mat.matrix[i][i] == 100.0
        for j in range(len(t_ids)):
            assert mat.matrix[i][j] == mat.matrix[j][i]


def test_anti_chaining_complete_linkage_hardening():
    """
    Hardening 1: Prevenção de chaining transitivo artificial.
    A-B = 90 (alto), B-C = 90 (alto), mas A-C = 20 (baixo).
    Esperado: NÃO formar {A, B, C}. Deve formar {A, B} e {C} ou {B, C} e {A}.
    """
    dt = datetime(2026, 3, 30, tzinfo=timezone.utc)
    t_ids = ["A", "B", "C"]
    cfg = DependenceConfig(grouping_redundancy_threshold=65.0)

    p_ab = TraderPairDependence(
        trader_a_id="A", trader_b_id="B", as_of=dt, overlap_periods=30,
        sample_status="SUFFICIENT", composite_redundancy_score=90.0,
        dependence_level=DependenceLevel.VERY_HIGH
    )
    p_bc = TraderPairDependence(
        trader_a_id="B", trader_b_id="C", as_of=dt, overlap_periods=30,
        sample_status="SUFFICIENT", composite_redundancy_score=90.0,
        dependence_level=DependenceLevel.VERY_HIGH
    )
    p_ac = TraderPairDependence(
        trader_a_id="A", trader_b_id="C", as_of=dt, overlap_periods=30,
        sample_status="SUFFICIENT", composite_redundancy_score=20.0,
        dependence_level=DependenceLevel.LOW
    )

    pairwise_map = {
        "A:B": p_ab, "B:A": p_ab,
        "B:C": p_bc, "C:B": p_bc,
        "A:C": p_ac, "C:A": p_ac,
    }

    # 1. Não deve formar grupo único de 3
    groups = RedundancyClusterer.find_redundancy_groups(
        trader_ids=t_ids,
        pairwise_map=pairwise_map,
        config=cfg
    )

    assert len(groups) == 2 # 2 blocos independentes, e NÃO 1 grupo único
    group_sets = [set(g.member_trader_ids) for g in groups]
    assert {"A", "B"} in group_sets or {"B", "C"} in group_sets
    assert {"A", "B", "C"} not in group_sets


def test_complete_linkage_all_high_forms_single_group():
    """
    Hardening 2: A-B, A-C e B-C todos altos (>= 65.0) -> forma {A, B, C}.
    """
    dt = datetime(2026, 3, 30, tzinfo=timezone.utc)
    t_ids = ["A", "B", "C"]
    cfg = DependenceConfig(grouping_redundancy_threshold=65.0)

    p_ab = TraderPairDependence(trader_a_id="A", trader_b_id="B", as_of=dt, overlap_periods=30, sample_status="SUFFICIENT", composite_redundancy_score=85.0, dependence_level=DependenceLevel.VERY_HIGH)
    p_bc = TraderPairDependence(trader_a_id="B", trader_b_id="C", as_of=dt, overlap_periods=30, sample_status="SUFFICIENT", composite_redundancy_score=80.0, dependence_level=DependenceLevel.VERY_HIGH)
    p_ac = TraderPairDependence(trader_a_id="A", trader_b_id="C", as_of=dt, overlap_periods=30, sample_status="SUFFICIENT", composite_redundancy_score=75.0, dependence_level=DependenceLevel.HIGH)

    pairwise_map = {
        "A:B": p_ab, "B:A": p_ab,
        "B:C": p_bc, "C:B": p_bc,
        "A:C": p_ac, "C:A": p_ac,
    }

    groups = RedundancyClusterer.find_redundancy_groups(
        trader_ids=t_ids,
        pairwise_map=pairwise_map,
        config=cfg,
        lead_priorities={"A": 90.0, "B": 85.0, "C": 80.0}
    )

    assert len(groups) == 1
    assert set(groups[0].member_trader_ids) == {"A", "B", "C"}
    assert groups[0].lead_trader_id == "A"
    assert groups[0].average_intra_group_redundancy == 80.0 # (85+80+75)/3


def test_independent_trader_forms_unit_group():
    """
    Hardening 3: Trader independente com score baixo com todos -> forma grupo unitário isolado.
    """
    dt = datetime(2026, 3, 30, tzinfo=timezone.utc)
    t_ids = ["A", "B", "D"]
    cfg = DependenceConfig(grouping_redundancy_threshold=65.0)

    p_ab = TraderPairDependence(trader_a_id="A", trader_b_id="B", as_of=dt, overlap_periods=30, sample_status="SUFFICIENT", composite_redundancy_score=90.0, dependence_level=DependenceLevel.VERY_HIGH)
    p_ad = TraderPairDependence(trader_a_id="A", trader_b_id="D", as_of=dt, overlap_periods=30, sample_status="SUFFICIENT", composite_redundancy_score=15.0, dependence_level=DependenceLevel.LOW)
    p_bd = TraderPairDependence(trader_a_id="B", trader_b_id="D", as_of=dt, overlap_periods=30, sample_status="SUFFICIENT", composite_redundancy_score=20.0, dependence_level=DependenceLevel.LOW)

    pairwise_map = {
        "A:B": p_ab, "B:A": p_ab,
        "A:D": p_ad, "D:A": p_ad,
        "B:D": p_bd, "D:B": p_bd,
    }

    groups = RedundancyClusterer.find_redundancy_groups(
        trader_ids=t_ids,
        pairwise_map=pairwise_map,
        config=cfg
    )

    assert len(groups) == 2
    d_group = next(g for g in groups if "D" in g.member_trader_ids)
    assert d_group.member_trader_ids == ["D"]
    assert d_group.average_intra_group_redundancy == 100.0


def test_redundancy_groups_order_independence():
    """
    Hardening 4: A partição em grupos é 100% determinística e independente da ordem da lista de entrada.
    """
    dt = datetime(2026, 3, 30, tzinfo=timezone.utc)
    t_ids = ["A", "B", "C", "D", "E"]
    cfg = DependenceConfig(grouping_redundancy_threshold=65.0)
    scores_priority = {"A": 90.0, "B": 85.0, "C": 80.0, "D": 75.0, "E": 70.0}

    # A e B são redundantes (88), C e D são redundantes (82), E é independente
    pairs = {
        ("A", "B"): 88.0, ("A", "C"): 20.0, ("A", "D"): 15.0, ("A", "E"): 10.0,
        ("B", "C"): 25.0, ("B", "D"): 18.0, ("B", "E"): 12.0,
        ("C", "D"): 82.0, ("C", "E"): 22.0,
        ("D", "E"): 19.0
    }
    pairwise_map = {}
    for (t1, t2), s in pairs.items():
        p = TraderPairDependence(
            trader_a_id=t1, trader_b_id=t2, as_of=dt, overlap_periods=30,
            sample_status="SUFFICIENT", composite_redundancy_score=s,
            dependence_level=DependenceLevel.HIGH if s >= 65.0 else DependenceLevel.LOW
        )
        pairwise_map[f"{t1}:{t2}"] = p
        pairwise_map[f"{t2}:{t1}"] = p

    # Executa com a ordem original
    res1 = RedundancyClusterer.find_redundancy_groups(
        trader_ids=t_ids, pairwise_map=pairwise_map, config=cfg, lead_priorities=scores_priority
    )

    # Executa com várias permutações embaralhadas
    rng = random.Random(42)
    for _ in range(10):
        shuffled = list(t_ids)
        rng.shuffle(shuffled)
        res_shuffled = RedundancyClusterer.find_redundancy_groups(
            trader_ids=shuffled, pairwise_map=pairwise_map, config=cfg, lead_priorities=scores_priority
        )

        assert len(res1) == len(res_shuffled)
        for g1, g2 in zip(res1, res_shuffled):
            assert g1.member_trader_ids == g2.member_trader_ids
            assert g1.lead_trader_id == g2.lead_trader_id
            assert g1.average_intra_group_redundancy == g2.average_intra_group_redundancy
