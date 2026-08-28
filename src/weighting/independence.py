from typing import Optional
from src.config.weight_config import WeightConfig
from src.dependence.models import CoreDependenceSnapshot, RedundancyGroup


class TraderIndependenceCalculator:
    """
    Calculador do Componente de Independência e Diluição de Grupo (Independence Component).
    
    Princípio Central (Group Dilution):
    Clones e traders altamente redundantes agrupados em um mesmo RedundancyGroup não devem
    multiplicar o poder de voto/influência do bloco. O grupo possui um orçamento de informação
    limitado, e seus membros competem internamente por essa fatia proporcionalmente à sua qualidade relativa.
    """
    @classmethod
    def calculate_independence(
        cls,
        trader_id: str,
        all_trader_ids: list[str],
        trader_quality_map: dict[str, float],
        config: WeightConfig,
        dependence_snapshot: Optional[CoreDependenceSnapshot] = None
    ) -> tuple[float, dict[str, float], Optional[int]]:
        """
        Calcula o fator de independência individual normalizado em [0.0, 1.0].
        Retorna (independence_factor, subcomponents_dict, group_id).
        """
        if dependence_snapshot is None or not dependence_snapshot.selected_trader_ids:
            # Caso 1: Sem dados de dependência disponíveis -> penalidade conservadora
            penalty = config.insufficient_dependence_penalty
            base_ind = max(0.10, 1.0 - penalty)
            return round(base_ind, 4), {"missing_dependence_penalty": penalty, "group_dilution_factor": base_ind}, None

        # 1. Localiza o Redundancy Group do trader
        matched_group: Optional[RedundancyGroup] = None
        for grp in dependence_snapshot.redundancy_groups:
            if trader_id in grp.member_trader_ids:
                matched_group = grp
                break

        if matched_group is None:
            # Trader é independente (grupo unitário virtual)
            group_id = None
            member_count = 1
            intra_redundancy = 0.0
            group_budget = 1.0
            quality_share = 1.0
            group_dilution_factor = 1.0
        else:
            group_id = matched_group.group_id
            member_count = len(matched_group.member_trader_ids)
            intra_redundancy = (matched_group.average_intra_group_redundancy / 100.0) if member_count > 1 else 1.0

            # Orçamento de Informação do Grupo (Group Information Budget):
            # Para clones perfeitos (intra_redundancy = 1.0), group_budget = 1.0 independente de quantos clones existam.
            # Se intra_redundancy = 0.70, cada membro extra adiciona (1 - 0.70) = 0.30 de informação não-redundante.
            strength = config.redundancy_penalty_strength
            novelty_per_extra_member = max(0.0, 1.0 - intra_redundancy)
            group_budget = 1.0 + ((member_count - 1) * novelty_per_extra_member * strength)

            # Partilha proporcional à qualidade individual dos membros do grupo
            group_qualities = [
                max(0.01, trader_quality_map.get(mid, 0.5))
                for mid in matched_group.member_trader_ids
            ]
            sum_group_quality = sum(group_qualities) if sum(group_qualities) > 0 else 1.0

            my_quality = max(0.01, trader_quality_map.get(trader_id, 0.5))
            quality_share = my_quality / sum_group_quality

            # Fator de diluição individual
            group_dilution_factor = (group_budget * quality_share) / 1.0

        # 2. Ajuste por Redundância de Fundo com traders de fora do grupo (External Background Redundancy)
        external_redundancy_scores: list[float] = []
        dep_matrix = dependence_snapshot.dependence_matrix
        if dep_matrix and dep_matrix.pairwise_map:
            for other_id in all_trader_ids:
                if other_id != trader_id:
                    # Se o outro trader não faz parte do mesmo grupo
                    is_same_group = (matched_group is not None and other_id in matched_group.member_trader_ids)
                    if not is_same_group:
                        pair = dep_matrix.pairwise_map.get(f"{trader_id}:{other_id}")
                        if pair and pair.composite_redundancy_score is not None:
                            external_redundancy_scores.append(pair.composite_redundancy_score)

        if external_redundancy_scores:
            avg_ext_red = sum(external_redundancy_scores) / len(external_redundancy_scores)
            ext_ind_factor = max(0.30, 1.0 - ((avg_ext_red / 100.0) * 0.40))
        else:
            avg_ext_red = 0.0
            ext_ind_factor = 1.0

        # 3. Combinação Final do Fator de Independência
        final_independence = group_dilution_factor * ext_ind_factor
        final_clamped = round(max(0.05, min(1.0, final_independence)), 4)

        subcomponents = {
            "group_id": float(group_id) if group_id is not None else 0.0,
            "group_member_count": float(member_count),
            "intra_group_redundancy": round(intra_redundancy * 100.0, 2),
            "group_information_budget": round(group_budget, 4),
            "quality_share_in_group": round(quality_share, 4),
            "group_dilution_factor": round(group_dilution_factor, 4),
            "avg_external_redundancy": round(avg_ext_red, 2),
            "external_independence_factor": round(ext_ind_factor, 4),
        }

        return final_clamped, subcomponents, group_id
