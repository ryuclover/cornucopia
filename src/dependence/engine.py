from datetime import datetime, timedelta, timezone
from decimal import Decimal
import statistics
from typing import Optional, Sequence
from src.config.dependence_config import DependenceConfig
from src.config.evaluation_config import EvaluationFrequency
from src.dependence.alignment import TimeSeriesAligner
from src.dependence.clustering import RedundancyClusterer
from src.dependence.metrics import (
    calculate_composite_redundancy_score,
    calculate_directional_agreement,
    calculate_instrument_overlap,
    calculate_position_overlap,
    calculate_return_correlation,
    calculate_timing_similarity,
    classify_dependence_level,
)
from src.dependence.models import (
    CoreDependenceSnapshot,
    DependenceLevel,
    DependenceMatrix,
    RedundancyGroup,
    TraderPairDependence,
)
from src.replay.engine import TraderReplayEngine
from src.selection.engine import TraderSelectionEngine


class TraderDependenceEngine:
    """
    Motor Quantitativo de Análise de Dependência, Similaridade e Correlação entre Traders.
    
    Executa análises pairwise, gera matrizes N x N de redundância, identifica grupos comportamentais
    conexos e rastreia a evolução longitudinal da diversidade do núcleo de especialistas.
    """
    def __init__(
        self,
        replay_engine: TraderReplayEngine,
        config: Optional[DependenceConfig] = None,
        selection_engine: Optional[TraderSelectionEngine] = None,
    ):
        self.replay_engine = replay_engine
        self.config = config or DependenceConfig()
        self.selection_engine = selection_engine

    @staticmethod
    def _normalize_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def analyze_pair(
        self,
        trader_a_id: str,
        trader_b_id: str,
        as_of: datetime,
        config: Optional[DependenceConfig] = None
    ) -> TraderPairDependence:
        """
        Analisa a relação de dependência, similaridade e correlação entre Trader A e Trader B em 'as_of'.
        """
        cfg = config or self.config
        as_of = self._normalize_utc(as_of)
        window_start = as_of - timedelta(days=cfg.analysis_window_days)

        # 1. Replay estritamente ponto-no-tempo até as_of
        replay_a = self.replay_engine.replay_trader(trader_a_id, as_of=as_of, compute_score=False)
        replay_b = self.replay_engine.replay_trader(trader_b_id, as_of=as_of, compute_score=False)

        trader_repo = self.replay_engine.trader_repo
        trader_a = trader_repo.get_by_id(trader_a_id)
        trader_b = trader_repo.get_by_id(trader_b_id)
        cap_a = trader_a.initial_capital if trader_a else Decimal("10000.00")
        cap_b = trader_b.initial_capital if trader_b else Decimal("10000.00")

        # 2. Filtra execuções e trades na janela retrospectiva [as_of - window_days, as_of]
        exec_repo = self.replay_engine.execution_repo
        all_execs_a = exec_repo.find_by_trader_until_as_of(trader_a_id, as_of)
        all_execs_b = exec_repo.find_by_trader_until_as_of(trader_b_id, as_of)

        window_execs_a = [e for e in all_execs_a if window_start <= e.timestamp <= as_of]
        window_execs_b = [e for e in all_execs_b if window_start <= e.timestamp <= as_of]

        window_trades_a = [t for t in replay_a.closed_trades if window_start <= t.exit_time <= as_of]
        window_trades_b = [t for t in replay_b.closed_trades if window_start <= t.exit_time <= as_of]

        overlap_trades_a = len(window_trades_a)
        overlap_trades_b = len(window_trades_b)

        # 3. Constrói séries temporais alinhadas
        series_a = TimeSeriesAligner.build_trader_time_series(
            trader_id=trader_a_id,
            trades=window_trades_a,
            executions=window_execs_a,
            as_of=as_of,
            window_days=cfg.analysis_window_days,
            initial_capital=cap_a,
            frequency=cfg.alignment_frequency
        )
        series_b = TimeSeriesAligner.build_trader_time_series(
            trader_id=trader_b_id,
            trades=window_trades_b,
            executions=window_execs_b,
            as_of=as_of,
            window_days=cfg.analysis_window_days,
            initial_capital=cap_b,
            frequency=cfg.alignment_frequency
        )

        aligned_a, aligned_b = TimeSeriesAligner.align_pair_series(series_a, series_b)
        overlap_periods = len(aligned_a)

        # 4. Avaliação de Suficiência Amostral
        is_sample_sufficient = (
            overlap_periods >= cfg.minimum_overlap_periods and
            min(overlap_trades_a, overlap_trades_b) >= cfg.minimum_overlap_trades
        )

        if not is_sample_sufficient:
            return TraderPairDependence(
                trader_a_id=trader_a_id,
                trader_b_id=trader_b_id,
                as_of=as_of,
                analysis_start=window_start,
                overlap_periods=overlap_periods,
                overlap_trades_a=overlap_trades_a,
                overlap_trades_b=overlap_trades_b,
                sample_status="INSUFFICIENT_DATA",
                correlation_status="INSUFFICIENT_DATA",
                return_correlation=None,
                directional_agreement=None,
                position_overlap=None,
                instrument_overlap=None,
                timing_similarity=None,
                composite_redundancy_score=None,
                dependence_level=DependenceLevel.INSUFFICIENT_DATA
            )

        # 5. Cálculo das métricas quantitativas de dependência
        returns_a = [f.net_return for f in aligned_a]
        returns_b = [f.net_return for f in aligned_b]

        return_corr = calculate_return_correlation(returns_a, returns_b)
        corr_status = "VALID" if return_corr is not None else "UNDEFINED_ZERO_VARIANCE"

        dir_agree = calculate_directional_agreement(aligned_a, aligned_b)
        pos_overlap = calculate_position_overlap(aligned_a, aligned_b)

        symbols_a = {e.symbol for e in window_execs_a}
        symbols_b = {e.symbol for e in window_execs_b}
        inst_overlap = calculate_instrument_overlap(symbols_a, symbols_b)

        timing_sim = calculate_timing_similarity(
            items_a=window_execs_a,
            items_b=window_execs_b,
            tolerance_hours=cfg.timing_tolerance_hours
        )

        composite_score = calculate_composite_redundancy_score(
            config=cfg,
            return_correlation=return_corr,
            directional_agreement=dir_agree,
            position_overlap=pos_overlap,
            instrument_overlap=inst_overlap,
            timing_similarity=timing_sim
        )

        dep_level = classify_dependence_level(composite_score, "SUFFICIENT", cfg)

        return TraderPairDependence(
            trader_a_id=trader_a_id,
            trader_b_id=trader_b_id,
            as_of=as_of,
            analysis_start=window_start,
            overlap_periods=overlap_periods,
            overlap_trades_a=overlap_trades_a,
            overlap_trades_b=overlap_trades_b,
            sample_status="SUFFICIENT",
            correlation_status=corr_status,
            return_correlation=return_corr,
            directional_agreement=dir_agree,
            position_overlap=pos_overlap,
            instrument_overlap=inst_overlap,
            timing_similarity=timing_sim,
            composite_redundancy_score=composite_score,
            dependence_level=dep_level
        )

    def compute_dependence_matrix(
        self,
        trader_ids: list[str],
        as_of: datetime,
        config: Optional[DependenceConfig] = None
    ) -> DependenceMatrix:
        """
        Calcula a matriz simétrica N x N de dependência entre um conjunto de traders em 'as_of'.
        """
        cfg = config or self.config
        as_of = self._normalize_utc(as_of)
        sorted_ids = sorted(list(set(trader_ids)))
        n = len(sorted_ids)

        matrix: list[list[Optional[float]]] = [[None for _ in range(n)] for _ in range(n)]
        pairwise_map: dict[str, TraderPairDependence] = {}

        for i in range(n):
            matrix[i][i] = 100.0  # Autocorrelação / redundância máxima consigo mesmo
            t_i = sorted_ids[i]

            for j in range(i + 1, n):
                t_j = sorted_ids[j]
                pair_result = self.analyze_pair(t_i, t_j, as_of=as_of, config=cfg)
                
                score = pair_result.composite_redundancy_score
                matrix[i][j] = score
                matrix[j][i] = score

                pairwise_map[f"{t_i}:{t_j}"] = pair_result
                pairwise_map[f"{t_j}:{t_i}"] = pair_result

        return DependenceMatrix(
            as_of=as_of,
            trader_ids=sorted_ids,
            matrix=matrix,
            pairwise_map=pairwise_map
        )

    def analyze_core(
        self,
        as_of: datetime,
        config: Optional[DependenceConfig] = None,
        selection_engine: Optional[TraderSelectionEngine] = None,
        trader_ids: Optional[list[str]] = None,
        selected_trader_ids: Optional[list[str]] = None
    ) -> CoreDependenceSnapshot:
        """
        Gera o snapshot consolidado de dependência e redundância do núcleo formal em 'as_of'.
        """
        cfg = config or self.config
        as_of = self._normalize_utc(as_of)
        sel_eng = selection_engine or self.selection_engine

        target_ids = trader_ids if trader_ids is not None else selected_trader_ids
        lead_priorities: dict[str, float] = {}

        if target_ids is None:
            if sel_eng is None:
                raise ValueError("selection_engine deve ser fornecido se trader_ids não for explicitado.")
            core_snap = sel_eng.get_selected_core(as_of=as_of)
            active_ids = [d.trader_id for d in core_snap.selected_traders]
            lead_priorities = {d.trader_id: d.survivor_score for d in core_snap.selected_traders}
        else:
            active_ids = sorted(list(set(target_ids)))

        # Se não houver traders suficientes para formar pares
        if not active_ids:
            empty_matrix = DependenceMatrix(
                as_of=as_of,
                trader_ids=[],
                matrix=[],
                pairwise_map={}
            )
            return CoreDependenceSnapshot(
                as_of=as_of,
                selected_traders=[],
                selected_trader_ids=[],
                dependence_matrix=empty_matrix,
                pairwise_dependencies=[],
                redundancy_groups=[],
                effective_independent_groups_count=0,
                average_redundancy=0.0,
                median_redundancy=0.0,
                maximum_redundancy=0.0,
                minimum_redundancy=0.0,
                highly_redundant_pairs=[],
                independent_pair_count=0
            )

        # 1. Matriz de Dependência
        dep_matrix = self.compute_dependence_matrix(active_ids, as_of=as_of, config=cfg)

        # 2. Agrupamento em Redundancy Groups
        groups = RedundancyClusterer.find_redundancy_groups(
            trader_ids=active_ids,
            pairwise_map=dep_matrix.pairwise_map,
            config=cfg,
            lead_priorities=lead_priorities
        )

        # 3. Estatísticas agregadas do núcleo
        pairwise_list: list[TraderPairDependence] = []
        valid_scores: list[float] = []
        highly_redundant: list[tuple[str, str, float]] = []
        independent_count = 0

        for i in range(len(active_ids)):
            for j in range(i + 1, len(active_ids)):
                t1, t2 = active_ids[i], active_ids[j]
                pair = dep_matrix.pairwise_map.get(f"{t1}:{t2}")
                if pair:
                    pairwise_list.append(pair)
                    if pair.composite_redundancy_score is not None:
                        score = pair.composite_redundancy_score
                        valid_scores.append(score)
                        if score >= cfg.high_redundancy_threshold:
                            highly_redundant.append((t1, t2, score))
                    if pair.dependence_level == DependenceLevel.LOW:
                        independent_count += 1

        avg_red = round(statistics.mean(valid_scores), 2) if valid_scores else 0.0
        med_red = round(statistics.median(valid_scores), 2) if valid_scores else 0.0
        max_red = round(max(valid_scores), 2) if valid_scores else 0.0
        min_red = round(min(valid_scores), 2) if valid_scores else 0.0

        return CoreDependenceSnapshot(
            as_of=as_of,
            selected_traders=active_ids,
            selected_trader_ids=active_ids,
            dependence_matrix=dep_matrix,
            pairwise_dependencies=pairwise_list,
            redundancy_groups=groups,
            effective_independent_groups_count=len(groups),
            average_redundancy=avg_red,
            median_redundancy=med_red,
            maximum_redundancy=max_red,
            minimum_redundancy=min_red,
            highly_redundant_pairs=highly_redundant,
            independent_pair_count=independent_count
        )

    def analyze_pair_series(
        self,
        trader_a_id: str,
        trader_b_id: str,
        start: datetime,
        end: datetime,
        frequency: EvaluationFrequency = EvaluationFrequency.MONTHLY,
        config: Optional[DependenceConfig] = None
    ) -> list[TraderPairDependence]:
        """
        Gera uma série temporal histórica da dependência entre dois traders para avaliar estabilidade temporal.
        """
        start = self._normalize_utc(start)
        end = self._normalize_utc(end)
        cfg = config or self.config

        timestamps = TimeSeriesAligner.generate_calendar_buckets(start, end, frequency)
        results: list[TraderPairDependence] = []

        for ts in timestamps:
            res = self.analyze_pair(trader_a_id, trader_b_id, as_of=ts, config=cfg)
            results.append(res)

        return results

    def analyze_core_series(
        self,
        start: datetime,
        end: datetime,
        frequency: EvaluationFrequency = EvaluationFrequency.MONTHLY,
        config: Optional[DependenceConfig] = None,
        selection_engine: Optional[TraderSelectionEngine] = None,
        trader_ids: Optional[list[str]] = None
    ) -> list[CoreDependenceSnapshot]:
        """
        Gera uma série temporal histórica da evolução da redundância e grupos independentes do núcleo.
        """
        start = self._normalize_utc(start)
        end = self._normalize_utc(end)
        cfg = config or self.config

        timestamps = TimeSeriesAligner.generate_calendar_buckets(start, end, frequency)
        snapshots: list[CoreDependenceSnapshot] = []

        for ts in timestamps:
            snap = self.analyze_core(
                as_of=ts,
                config=cfg,
                selection_engine=selection_engine,
                trader_ids=trader_ids
            )
            snapshots.append(snap)

        return snapshots
