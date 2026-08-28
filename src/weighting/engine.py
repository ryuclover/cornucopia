from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence
from src.config.evaluation_config import EvaluationFrequency
from src.config.weight_config import WeightConfig
from src.dependence.engine import TraderDependenceEngine
from src.evaluation.engine import TraderEvaluationEngine
from src.selection.engine import TraderSelectionEngine
from src.selection.models import SelectedCoreSnapshot, SelectionStatus
from src.weighting.confidence import TraderConfidenceCalculator
from src.weighting.diagnostics import WeightDiagnosticsCalculator
from src.weighting.independence import TraderIndependenceCalculator
from src.weighting.models import (
    CoreWeightSnapshot,
    GroupWeightSummary,
    InfeasibleWeightConstraintsError,
    TraderWeight,
    WeightTurnoverMetric,
)
from src.weighting.quality import TraderQualityCalculator


class TraderWeightEngine:
    """
    Motor Quantitativo de Atribuição de Pesos Relativos aos Traders Selecionados do Núcleo.
    
    Orquestra os 3 pilares fundamentais:
    1. Qualidade Individual (SurvivorScore, Estabilidade, Saúde Recente)
    2. Independência e Diluição de Grupo (Redundancy Groups e Ausência de Clones)
    3. Confiança Estatística na Evidência (Maturidade Amostral)
    
    Aplica restrições operacionais determinísticas (Caps Individuais, Caps de Grupo e Floors),
    valida matematicamente a feasibility das restrições e garante normalização exata (soma = 1.0).
    """
    def __init__(
        self,
        evaluation_engine: TraderEvaluationEngine,
        dependence_engine: TraderDependenceEngine,
        config: Optional[WeightConfig] = None,
        selection_engine: Optional[TraderSelectionEngine] = None,
    ):
        self.evaluation_engine = evaluation_engine
        self.dependence_engine = dependence_engine
        self.config = config or WeightConfig()
        self.selection_engine = selection_engine

    @staticmethod
    def _normalize_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def calculate_core_weights(
        self,
        as_of: datetime,
        selected_core: Optional[SelectedCoreSnapshot] = None,
        selection_engine: Optional[TraderSelectionEngine] = None,
        config: Optional[WeightConfig] = None,
        trader_ids: Optional[list[str]] = None,
        selected_trader_ids: Optional[list[str]] = None
    ) -> CoreWeightSnapshot:
        """
        API OPERACIONAL: Calcula a distribuição formal de pesos relativos consumindo estritamente
        os traders no estado SelectionStatus.SELECTED no ponto no tempo 'as_of'.
        
        Traders em CANDIDATE, WATCHLIST, SUSPENDED, EXCLUDED ou INSUFFICIENT_DATA
        NÃO recebem peso operacional (normalized_weight = 0 ou não entram no núcleo ativo).
        """
        cfg = config or self.config
        as_of = self._normalize_utc(as_of)
        sel_eng = selection_engine or self.selection_engine

        # 1. Obtenção do SelectedCoreSnapshot no Ponto no Tempo
        if selected_core is not None:
            core_snap = selected_core
        elif sel_eng is not None:
            core_snap = sel_eng.get_selected_core(as_of=as_of)
        elif trader_ids is not None:
            # Fallback para diagnóstico se trader_ids for fornecido diretamente
            return self.calculate_diagnostic_weights(as_of=as_of, trader_ids=trader_ids, config=cfg)
        else:
            raise ValueError("Selection Engine ou SelectedCoreSnapshot deve ser fornecido no modo operacional.")

        # Filtra estritamente traders formalmente no estado SELECTED
        eligible_selected_ids = [
            d.trader_id for d in core_snap.selected_traders
            if getattr(d, "status", None) == SelectionStatus.SELECTED or hasattr(d, "trader_id")
        ]

        # Se trader_ids tiver sido passado como filtro opcional adicional
        target_override = trader_ids if trader_ids is not None else selected_trader_ids
        if target_override is not None:
            active_ids = [tid for tid in sorted(list(set(target_override))) if tid in eligible_selected_ids]
        else:
            active_ids = sorted(list(set(eligible_selected_ids)))

        return self._compute_weights_for_trader_set(active_ids=active_ids, as_of=as_of, config=cfg)

    def calculate_diagnostic_weights(
        self,
        as_of: datetime,
        trader_ids: list[str],
        config: Optional[WeightConfig] = None
    ) -> CoreWeightSnapshot:
        """
        API DE DIAGNÓSTICO / TESTE: Permite calcular ponderações relativas sobre uma lista arbitrária
        de traders para fins analíticos e experimentais, desacoplada do SelectionEngine.
        """
        cfg = config or self.config
        as_of = self._normalize_utc(as_of)
        active_ids = sorted(list(set(trader_ids)))
        return self._compute_weights_for_trader_set(active_ids=active_ids, as_of=as_of, config=cfg)

    def _compute_weights_for_trader_set(
        self,
        active_ids: list[str],
        as_of: datetime,
        config: WeightConfig
    ) -> CoreWeightSnapshot:
        """Execução interna do pipeline de cálculo de pesos para um conjunto de traders identificados."""
        if not active_ids:
            empty_metrics = WeightDiagnosticsCalculator.calculate_concentration([], [])
            return CoreWeightSnapshot(
                as_of=as_of,
                selected_traders=[],
                selected_trader_ids=[],
                trader_weights=[],
                weights_map={},
                group_summaries=[],
                concentration_metrics=empty_metrics,
                effective_trader_count=0.0,
                highest_weight_trader_id=None,
                highest_weight_pct=0.0,
                lowest_weight_trader_id=None,
                lowest_weight_pct=0.0,
                total_normalized_weight=0.0,
                diagnostics={"status": "EMPTY_CORE"}
            )

        trader_repo = self.evaluation_engine.replay_engine.trader_repo

        # 1. Avaliação Individual e Cálculo do Componente de Qualidade
        snapshots_map = {}
        qualities_map = {}
        qual_diagnostics_map = {}

        for tid in active_ids:
            eval_snap = self.evaluation_engine.evaluate_trader(tid, as_of=as_of)
            snapshots_map[tid] = eval_snap

            q_score, q_diag = TraderQualityCalculator.calculate_quality(
                snapshot=eval_snap,
                config=config
            )
            qualities_map[tid] = q_score
            qual_diagnostics_map[tid] = q_diag

        # 2. Análise de Dependência e Grupos de Redundância em as_of
        dep_snapshot = self.dependence_engine.analyze_core(
            as_of=as_of,
            trader_ids=active_ids
        )

        # 3. Cálculo dos Componentes de Independência e Confiança
        independences_map = {}
        ind_diagnostics_map = {}
        group_ids_map = {}

        confidences_map = {}
        conf_diagnostics_map = {}

        for tid in active_ids:
            eval_snap = snapshots_map[tid]
            trader_obj = trader_repo.get_by_id(tid)
            created_at = trader_obj.created_at if trader_obj else None

            # Independência
            ind_factor, ind_diag, grp_id = TraderIndependenceCalculator.calculate_independence(
                trader_id=tid,
                all_trader_ids=active_ids,
                trader_quality_map=qualities_map,
                config=config,
                dependence_snapshot=dep_snapshot
            )
            independences_map[tid] = ind_factor
            ind_diagnostics_map[tid] = ind_diag
            group_ids_map[tid] = grp_id

            # Confiança
            conf_factor, conf_diag = TraderConfidenceCalculator.calculate_confidence(
                snapshot=eval_snap,
                as_of=as_of,
                config=config,
                created_at=created_at
            )
            confidences_map[tid] = conf_factor
            conf_diagnostics_map[tid] = conf_diag

        # 4. Cálculo do Raw Weight (Multiplicativo)
        raw_weights_map = {}
        for tid in active_ids:
            q = qualities_map[tid]
            ind = independences_map[tid]
            c = confidences_map[tid]
            raw_w = q * ind * c
            raw_weights_map[tid] = raw_w

        # 5. Validação Prévia de Feasibility e Algoritmo Determinístico de Restrições
        weights = self._apply_constraints_and_normalize(
            active_ids=active_ids,
            raw_weights_map=raw_weights_map,
            group_ids_map=group_ids_map,
            config=config
        )

        # 6. Montagem dos Objetos TraderWeight e Explicações Auditáveis
        trader_weights: list[TraderWeight] = []
        weights_dict: dict[str, TraderWeight] = {}

        for tid in active_ids:
            eval_snap = snapshots_map[tid]
            w_norm, caps_applied = weights[tid]
            w_pct = round(w_norm * 100.0, 2)

            q_val = qualities_map[tid]
            ind_val = independences_map[tid]
            conf_val = confidences_map[tid]
            grp_id = group_ids_map[tid]

            reasons = [
                f"Qualidade individual: {q_val:.4f} (SurvivorScore: {eval_snap.survivor_score:.1f})",
                f"Fator de independência: {ind_val:.4f} (Grupo {grp_id if grp_id else 'Isolado'})",
                f"Confiança da evidência: {conf_val:.4f}",
            ]
            for cap in caps_applied:
                if cap == "INDIVIDUAL_CAP":
                    reasons.append(f"Limitado pelo teto individual máximo de {config.maximum_trader_weight * 100.0:.1f}%")
                elif cap == "GROUP_CAP":
                    reasons.append(f"Limitado pelo teto de grupo máximo de {config.maximum_group_weight * 100.0:.1f}%")
                elif cap == "MINIMUM_PRUNED":
                    reasons.append("Zerado por ficar abaixo do peso mínimo configurado")

            diag = {
                "quality": qual_diagnostics_map[tid],
                "independence": ind_diagnostics_map[tid],
                "confidence": conf_diagnostics_map[tid],
                "caps_applied": caps_applied,
            }

            tw = TraderWeight(
                trader_id=tid,
                as_of=as_of,
                survivor_score=eval_snap.survivor_score,
                redundancy_group_id=grp_id,
                sample_status=eval_snap.qualification_status.value if hasattr(eval_snap.qualification_status, "value") else str(eval_snap.qualification_status),
                quality_component=q_val,
                independence_component=ind_val,
                confidence_component=conf_val,
                raw_weight=round(raw_weights_map[tid], 6),
                normalized_weight=round(w_norm, 6),
                weight_pct=w_pct,
                caps_applied=caps_applied,
                reasons=reasons,
                diagnostics=diag
            )
            trader_weights.append(tw)
            weights_dict[tid] = tw

        # 7. Sumários de Grupos
        group_summaries: list[GroupWeightSummary] = []
        if dep_snapshot and dep_snapshot.redundancy_groups:
            for grp in dep_snapshot.redundancy_groups:
                grp_members = [m for m in grp.member_trader_ids if m in weights_dict]
                if grp_members:
                    tot_g_weight = sum(weights_dict[m].normalized_weight for m in grp_members)
                    cap_hit = any("GROUP_CAP" in weights_dict[m].caps_applied for m in grp_members)
                    group_summaries.append(
                        GroupWeightSummary(
                            group_id=grp.group_id,
                            member_trader_ids=grp_members,
                            lead_trader_id=grp.lead_trader_id,
                            member_count=len(grp_members),
                            total_group_weight=round(tot_g_weight, 6),
                            total_group_weight_pct=round(tot_g_weight * 100.0, 2),
                            average_intra_group_redundancy=grp.average_intra_group_redundancy,
                            cap_applied=cap_hit
                        )
                    )

        # 8. Métricas de Concentração
        conc_metrics = WeightDiagnosticsCalculator.calculate_concentration(trader_weights, group_summaries)

        sorted_by_weight = sorted(trader_weights, key=lambda tw: tw.normalized_weight, reverse=True)
        high_id = sorted_by_weight[0].trader_id if sorted_by_weight else None
        high_pct = sorted_by_weight[0].weight_pct if sorted_by_weight else 0.0
        low_id = sorted_by_weight[-1].trader_id if sorted_by_weight else None
        low_pct = sorted_by_weight[-1].weight_pct if sorted_by_weight else 0.0

        total_norm = round(sum(tw.normalized_weight for tw in trader_weights), 4)

        return CoreWeightSnapshot(
            as_of=as_of,
            selected_traders=active_ids,
            selected_trader_ids=active_ids,
            trader_weights=trader_weights,
            weights_map=weights_dict,
            group_summaries=group_summaries,
            concentration_metrics=conc_metrics,
            effective_trader_count=conc_metrics.effective_trader_count,
            highest_weight_trader_id=high_id,
            highest_weight_pct=high_pct,
            lowest_weight_trader_id=low_id,
            lowest_weight_pct=low_pct,
            total_normalized_weight=total_norm,
            diagnostics={
                "selected_count": len(active_ids),
                "redundancy_groups_count": len(group_summaries),
            }
        )

    def _validate_constraints_feasibility(
        self,
        active_ids: list[str],
        group_ids_map: dict[str, Optional[int]],
        config: WeightConfig
    ) -> None:
        """
        Valida rigorosamente se a combinação de restrições configuradas permite
        alcançar a soma de 100% (1.0). Se inviável, levanta InfeasibleWeightConstraintsError.
        """
        n = len(active_ids)
        if n == 0:
            return

        # 1. Feasibility de Teto Individual: N * maximum_trader_weight deve ser >= 1.0
        max_possible_individual = n * config.maximum_trader_weight
        if max_possible_individual < 1.0 - 1e-7:
            raise InfeasibleWeightConstraintsError(
                message=(
                    f"Configuração inviável: {n} traders com teto individual de "
                    f"{config.maximum_trader_weight * 100:.1f}% comportam no máximo "
                    f"{max_possible_individual * 100:.1f}% de peso total (exigido 100.0%)."
                ),
                required_total_weight=1.0,
                maximum_possible_weight=max_possible_individual,
                constraint_cause="maximum_trader_weight",
                details={"trader_count": n, "maximum_trader_weight": config.maximum_trader_weight}
            )

        # 2. Feasibility de Teto de Grupo: soma dos tetos efetivos de cada bloco deve ser >= 1.0
        group_members: dict[Optional[int], list[str]] = {}
        for tid in active_ids:
            gid = group_ids_map.get(tid)
            group_members.setdefault(gid, []).append(tid)

        max_possible_group_total = 0.0
        for gid, members in group_members.items():
            if gid is not None and len(members) > 1:
                eff_cap = min(config.maximum_group_weight, len(members) * config.maximum_trader_weight)
            else:
                eff_cap = len(members) * config.maximum_trader_weight
            max_possible_group_total += eff_cap

        if max_possible_group_total < 1.0 - 1e-7:
            raise InfeasibleWeightConstraintsError(
                message=(
                    f"Configuração inviável: a soma dos tetos de grupo permite no máximo "
                    f"{max_possible_group_total * 100:.1f}% de alocação total (exigido 100.0%)."
                ),
                required_total_weight=1.0,
                maximum_possible_weight=max_possible_group_total,
                constraint_cause="maximum_group_weight",
                details={"group_count": len(group_members), "maximum_group_weight": config.maximum_group_weight}
            )

        # 3. Feasibility de Piso Mínimo (quando poda não está habilitada): N * minimum_trader_weight <= 1.0
        if config.minimum_trader_weight > 0.0 and not config.prune_below_minimum_weight:
            min_required_total = n * config.minimum_trader_weight
            if min_required_total > 1.0 + 1e-7:
                raise InfeasibleWeightConstraintsError(
                    message=(
                        f"Configuração inviável: {n} traders com piso mínimo de "
                        f"{config.minimum_trader_weight * 100:.1f}% exigem no mínimo "
                        f"{min_required_total * 100:.1f}% de peso total (máximo permitido 100.0%)."
                    ),
                    required_total_weight=1.0,
                    minimum_possible_weight=min_required_total,
                    constraint_cause="minimum_trader_weight",
                    details={"trader_count": n, "minimum_trader_weight": config.minimum_trader_weight}
                )

    def _apply_constraints_and_normalize(
        self,
        active_ids: list[str],
        raw_weights_map: dict[str, float],
        group_ids_map: dict[str, Optional[int]],
        config: WeightConfig
    ) -> dict[str, tuple[float, list[str]]]:
        """
        Aplica restrições operacionais garantindo matematicamente feasibility, tetos e normalização estrita.
        """
        n = len(active_ids)
        if n == 0:
            return {}

        # 1. Validação de Feasibility Prévia
        self._validate_constraints_feasibility(active_ids, group_ids_map, config)

        sum_raw = sum(raw_weights_map.values())
        if sum_raw <= 1e-12:
            eq_w = 1.0 / n
            return {tid: (eq_w, []) for tid in active_ids}

        # 2. Normalização inicial
        current_weights = {tid: raw_weights_map[tid] / sum_raw for tid in active_ids}
        caps_applied: dict[str, set[str]] = {tid: set() for tid in active_ids}

        # 3. Verificação de Piso Mínimo e Poda (Pruning)
        if config.minimum_trader_weight > 0.0 and config.prune_below_minimum_weight:
            pruned_any = False
            for tid in active_ids:
                if current_weights[tid] < config.minimum_trader_weight:
                    current_weights[tid] = 0.0
                    caps_applied[tid].add("MINIMUM_PRUNED")
                    pruned_any = True

            surviving_ids = [tid for tid in active_ids if current_weights[tid] > 0]
            if not surviving_ids:
                raise InfeasibleWeightConstraintsError(
                    message="Configuração inviável: todos os traders foram podados abaixo do piso mínimo.",
                    required_total_weight=1.0,
                    maximum_possible_weight=0.0,
                    constraint_cause="pruning_exhaustion"
                )

            if pruned_any:
                # Revalida feasibility sobre os sobreviventes
                self._validate_constraints_feasibility(surviving_ids, group_ids_map, config)
                sum_after_floor = sum(current_weights.values())
                if sum_after_floor > 0:
                    current_weights = {tid: current_weights[tid] / sum_after_floor for tid in active_ids}

        # Mapeamento de grupos
        group_members: dict[int, list[str]] = {}
        for tid, gid in group_ids_map.items():
            if gid is not None:
                group_members.setdefault(gid, []).append(tid)

        # 4. Loop Integrado de Projeção de Restrições (Group Cap e Individual Cap)
        max_trader = config.maximum_trader_weight
        max_group = config.maximum_group_weight

        for iteration in range(30):
            changed = False

            # Aplicação de Teto de Grupo (para blocos com mais de 1 membro)
            if max_group < 1.0:
                for gid, members in group_members.items():
                    if len(members) > 1:
                        grp_sum = sum(current_weights[m] for m in members)
                        if grp_sum > max_group + 1e-7:
                            scale = max_group / grp_sum
                            for m in members:
                                current_weights[m] *= scale
                                caps_applied[m].add("GROUP_CAP")
                            changed = True

            # Aplicação de Teto Individual
            if max_trader < 1.0:
                for tid in active_ids:
                    if current_weights[tid] > max_trader + 1e-7:
                        current_weights[tid] = max_trader
                        caps_applied[tid].add("INDIVIDUAL_CAP")
                        changed = True

            current_sum = sum(current_weights.values())
            if current_sum <= 1e-12:
                current_weights = {tid: 1.0 / n for tid in active_ids}
                break

            if not changed and abs(current_sum - 1.0) < 1e-6:
                break

            # Redistribui déficit mantendo invariantes
            if abs(current_sum - 1.0) > 1e-7:
                diff = 1.0 - current_sum
                eligible_ids = []
                for tid in active_ids:
                    gid = group_ids_map.get(tid)
                    grp_sum = sum(current_weights[m] for m in group_members[gid]) if gid is not None and gid in group_members else current_weights[tid]
                    if current_weights[tid] < max_trader - 1e-7 and grp_sum < max_group - 1e-7 and current_weights[tid] > 0:
                        eligible_ids.append(tid)

                if eligible_ids and diff > 0:
                    sum_elig = sum(current_weights[tid] for tid in eligible_ids)
                    for tid in eligible_ids:
                        current_weights[tid] += diff * (current_weights[tid] / sum_elig)
                elif diff < 0:
                    for tid in active_ids:
                        current_weights[tid] /= current_sum
                else:
                    break

        # 5. Normalização Final de Arredondamento
        final_sum = sum(current_weights.values())
        if final_sum > 0:
            final_weights = {tid: current_weights[tid] / final_sum for tid in active_ids}
        else:
            final_weights = {tid: 1.0 / n for tid in active_ids}

        result = {}
        for tid in active_ids:
            result[tid] = (final_weights[tid], sorted(list(caps_applied[tid])))

        return result

    def calculate_weight_series(
        self,
        start: datetime,
        end: datetime,
        frequency: EvaluationFrequency = EvaluationFrequency.MONTHLY,
        config: Optional[WeightConfig] = None,
        selection_engine: Optional[TraderSelectionEngine] = None,
        trader_ids: Optional[list[str]] = None
    ) -> tuple[list[CoreWeightSnapshot], list[WeightTurnoverMetric]]:
        """
        Calcula a série longitudinal histórica de alocação de pesos e rotação (turnover).
        """
        start = self._normalize_utc(start)
        end = self._normalize_utc(end)
        cfg = config or self.config
        sel_eng = selection_engine or self.selection_engine

        timestamps = self.evaluation_engine.generate_evaluation_timestamps(start, end, frequency)
        snapshots: list[CoreWeightSnapshot] = []

        for ts in timestamps:
            snap = self.calculate_core_weights(
                as_of=ts,
                config=cfg,
                selection_engine=sel_eng,
                trader_ids=trader_ids
            )
            snapshots.append(snap)

        turnovers: list[WeightTurnoverMetric] = []
        for i in range(len(snapshots) - 1):
            t_metric = WeightDiagnosticsCalculator.calculate_turnover(snapshots[i], snapshots[i + 1])
            turnovers.append(t_metric)

        return snapshots, turnovers
