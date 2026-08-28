from typing import Optional
from src.config.dependence_config import DependenceConfig
from src.dependence.models import DependenceMatrix, RedundancyGroup, TraderPairDependence


class RedundancyClusterer:
    """
    Agrupador comportamental transparente e determinístico baseado em Complete-Linkage / Clique Partitioning.
    
    Regra de Agrupamento Estrita (Anti-Chaining):
    Um trader só pode ingressar em um determinado RedundancyGroup se a sua redundância com TODOS os membros
    já pertencentes ao grupo for maior ou igual ao threshold configurado (minimum_internal_group_redundancy).
    
    Isso impede que relações de chaining transitivo (ex: A ~ B e B ~ C com A !~ C) formem blocos agregados espúrios.
    """
    @classmethod
    def find_redundancy_groups(
        cls,
        trader_ids: list[str],
        pairwise_map: dict[str, TraderPairDependence],
        config: DependenceConfig,
        lead_priorities: Optional[dict[str, float]] = None
    ) -> list[RedundancyGroup]:
        """
        Calcula os grupos de redundância coerentes interna e determinísticamente.
        """
        if not trader_ids:
            return []

        threshold = (
            config.minimum_internal_group_redundancy
            if config.minimum_internal_group_redundancy is not None
            else config.grouping_redundancy_threshold
        )
        priorities = lead_priorities or {}

        # Chave determinística de ordenação para eleição de líderes e priorização:
        # 1. Maior prioridade (ex: SurvivorScore)
        # 2. Ordem alfabética do ID como critério de desempate
        def sort_key(tid: str) -> tuple[float, str]:
            return (-priorities.get(tid, 0.0), tid)

        unique_traders = sorted(list(set(trader_ids)), key=sort_key)
        remaining: set[str] = set(unique_traders)
        formed_groups: list[list[str]] = []

        while remaining:
            # Seleciona o trader de maior prioridade disponível como semente/líder do novo bloco
            leader = min(remaining, key=sort_key)
            group = [leader]

            # Avalia todos os outros candidatos restantes em ordem determinística
            candidates = sorted(list(remaining - {leader}), key=sort_key)
            for cand in candidates:
                # Regra de Complete-Linkage: deve ter redundância >= threshold com TODOS os membros atuais
                is_compatible_with_all = True
                for member in group:
                    pair_key_1 = f"{cand}:{member}"
                    pair_key_2 = f"{member}:{cand}"
                    pair = pairwise_map.get(pair_key_1) or pairwise_map.get(pair_key_2)

                    if (
                        pair is None or
                        pair.composite_redundancy_score is None or
                        pair.composite_redundancy_score < threshold
                    ):
                        is_compatible_with_all = False
                        break

                if is_compatible_with_all:
                    group.append(cand)

            # Remove membros alocados do conjunto restante
            for member in group:
                remaining.remove(member)

            formed_groups.append(group)

        # Ordena grupos: maior quantidade de membros primeiro; desempate pelo líder do grupo
        formed_groups.sort(key=lambda g: (-len(g), sort_key(g[0])))

        groups: list[RedundancyGroup] = []
        for idx, members in enumerate(formed_groups, start=1):
            lead_id = members[0] # Primeiro membro pela ordem de prioridade
            sorted_members = sorted(members)

            # Calcula redundância média interna do grupo
            if len(sorted_members) <= 1:
                avg_intra = 100.0
            else:
                pair_scores = []
                for i in range(len(sorted_members)):
                    for j in range(i + 1, len(sorted_members)):
                        t1, t2 = sorted_members[i], sorted_members[j]
                        pair = pairwise_map.get(f"{t1}:{t2}") or pairwise_map.get(f"{t2}:{t1}")
                        if pair and pair.composite_redundancy_score is not None:
                            pair_scores.append(pair.composite_redundancy_score)
                avg_intra = round(sum(pair_scores) / len(pair_scores), 2) if pair_scores else 100.0

            groups.append(
                RedundancyGroup(
                    group_id=idx,
                    member_trader_ids=sorted_members,
                    lead_trader_id=lead_id,
                    average_intra_group_redundancy=avg_intra
                )
            )

        return groups

    @classmethod
    def identify_redundancy_groups(
        cls,
        trader_ids: list[str],
        matrix: DependenceMatrix,
        config: Optional[DependenceConfig] = None,
        trader_score_map: Optional[dict[str, float]] = None
    ) -> list[RedundancyGroup]:
        """
        Helper method para identificar grupos a partir do objeto DependenceMatrix.
        """
        cfg = config or DependenceConfig()
        return cls.find_redundancy_groups(
            trader_ids=trader_ids,
            pairwise_map=matrix.pairwise_map,
            config=cfg,
            lead_priorities=trader_score_map
        )
