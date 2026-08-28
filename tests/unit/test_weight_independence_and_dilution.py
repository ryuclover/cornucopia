from datetime import datetime, timezone
from src.config.dependence_config import DependenceConfig
from src.config.weight_config import WeightConfig
from src.dependence.models import (
    CoreDependenceSnapshot,
    DependenceLevel,
    DependenceMatrix,
    RedundancyGroup,
    TraderPairDependence,
)
from src.weighting.independence import TraderIndependenceCalculator


def make_test_core_dependence() -> CoreDependenceSnapshot:
    dt = datetime(2026, 3, 30, tzinfo=timezone.utc)
    t_ids = ["A", "B", "C", "D"]
    
    # A, B, C pertencem ao Grupo 1 (Redundância 95%)
    # D é independente (Grupo 2)
    g1 = RedundancyGroup(
        group_id=1,
        member_trader_ids=["A", "B", "C"],
        lead_trader_id="A",
        average_intra_group_redundancy=95.0
    )
    g2 = RedundancyGroup(
        group_id=2,
        member_trader_ids=["D"],
        lead_trader_id="D",
        average_intra_group_redundancy=100.0
    )

    pairs = {
        ("A", "B"): 95.0, ("A", "C"): 95.0, ("B", "C"): 95.0,
        ("A", "D"): 15.0, ("B", "D"): 12.0, ("C", "D"): 18.0
    }
    pairwise_map = {}
    for (t1, t2), s in pairs.items():
        p = TraderPairDependence(
            trader_a_id=t1, trader_b_id=t2, as_of=dt, overlap_periods=30,
            sample_status="SUFFICIENT", composite_redundancy_score=s,
            dependence_level=DependenceLevel.VERY_HIGH if s >= 80 else DependenceLevel.LOW
        )
        pairwise_map[f"{t1}:{t2}"] = p
        pairwise_map[f"{t2}:{t1}"] = p

    matrix = DependenceMatrix(
        as_of=dt,
        trader_ids=t_ids,
        matrix=[[100.0 for _ in range(4)] for _ in range(4)],
        pairwise_map=pairwise_map
    )

    return CoreDependenceSnapshot(
        as_of=dt,
        selected_traders=t_ids,
        selected_trader_ids=t_ids,
        dependence_matrix=matrix,
        pairwise_dependencies=list(pairwise_map.values()),
        redundancy_groups=[g1, g2],
        effective_independent_groups_count=2
    )


def test_group_dilution_and_clone_penalty():
    cfg = WeightConfig()
    dep_snap = make_test_core_dependence()
    all_ids = ["A", "B", "C", "D"]

    # Qualidades: A=0.90, B=0.90, C=0.60 (pior clone), D=0.85 (independente)
    quality_map = {"A": 0.90, "B": 0.90, "C": 0.60, "D": 0.85}

    ind_a, diag_a, grp_a = TraderIndependenceCalculator.calculate_independence("A", all_ids, quality_map, cfg, dep_snap)
    ind_b, diag_b, grp_b = TraderIndependenceCalculator.calculate_independence("B", all_ids, quality_map, cfg, dep_snap)
    ind_c, diag_c, grp_c = TraderIndependenceCalculator.calculate_independence("C", all_ids, quality_map, cfg, dep_snap)
    ind_d, diag_d, grp_d = TraderIndependenceCalculator.calculate_independence("D", all_ids, quality_map, cfg, dep_snap)

    # 1. O trader independente D deve ter fator de independência muito superior aos clones
    assert ind_d > 0.85
    assert ind_a < 0.50
    assert ind_b < 0.50

    # 2. Dentro do grupo de clones, o clone A (qualidade 0.90) deve reter maior fatia que o clone C (qualidade 0.60)
    assert ind_a > ind_c
    assert diag_a["quality_share_in_group"] > diag_c["quality_share_in_group"]

    # 3. Diluição Coletiva: a soma dos fatores de independência do grupo {A,B,C} não deve triplicar
    sum_group_ind = ind_a + ind_b + ind_c
    assert sum_group_ind < 1.30 # Muito próximo de 1 bloco independente


def test_missing_dependence_conservative_penalty():
    cfg = WeightConfig(insufficient_dependence_penalty=0.15)
    all_ids = ["A", "B"]
    quality_map = {"A": 0.80, "B": 0.80}

    # Sem snapshot de dependência (amostra desconhecida)
    ind_a, diag_a, _ = TraderIndependenceCalculator.calculate_independence("A", all_ids, quality_map, cfg, None)
    
    # Não deve assumir 100% de independência
    assert ind_a == 0.85
    assert diag_a["missing_dependence_penalty"] == 0.15
